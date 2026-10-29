from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from manimagent.schemas import FINAL_STATUSES, Artifact, GenerationRequest, RunManifest, now


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def job_dir(self, job_id: str) -> Path:
        from manimagent.schemas import is_job_id

        if not is_job_id(job_id):
            raise FileNotFoundError("任务不存在")
        return self.root / job_id

    @staticmethod
    def write_json(path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def create(self, request: GenerationRequest) -> RunManifest:
        with self._lock:
            manifest = RunManifest(job_id=uuid.uuid4().hex)
            directory = self.job_dir(manifest.job_id)
            self.write_json(directory / "request.json", request.model_dump())
            self.save(manifest)
            return manifest

    def save(self, manifest: RunManifest) -> None:
        with self._lock:
            manifest.updated_at = now()
            self.write_json(self.job_dir(manifest.job_id) / "manifest.json", manifest.model_dump())

    def load(self, job_id: str) -> RunManifest:
        with self._lock:
            return RunManifest.model_validate_json(
                (self.job_dir(job_id) / "manifest.json").read_text(encoding="utf-8")
            )

    def register(self, manifest: RunManifest, path: Path, kind: str, label: str) -> str:
        root = self.job_dir(manifest.job_id).resolve()
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("产物必须位于当前任务目录")
        artifact_id = "a_" + uuid.uuid4().hex
        manifest.artifacts[artifact_id] = Artifact(
            artifact_id=artifact_id,
            path=resolved.relative_to(root).as_posix(),
            kind=kind,
            label=label,
        )
        return artifact_id

    def resolve(self, manifest: RunManifest, artifact_id: str) -> Path:
        artifact = manifest.artifacts.get(artifact_id)
        if artifact is None:
            raise FileNotFoundError("产物不存在")
        root = self.job_dir(manifest.job_id).resolve()
        path = (root / artifact.path).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError("产物不存在")
        return path

    def recover(self) -> int:
        recovered = 0
        for path in self.root.glob("*/manifest.json"):
            try:
                manifest = self.load(path.parent.name)
                if manifest.status not in FINAL_STATUSES:
                    manifest.status = "interrupted"
                    manifest.message = "上次运行中断，请重新提交；已有产物已保留"
                    self.save(manifest)
                    recovered += 1
            except (OSError, ValueError):
                continue
        return recovered

    def public(self, manifest: RunManifest) -> dict:
        return {
            "job_id": manifest.job_id,
            "status": manifest.status,
            "stage": manifest.stage,
            "title": manifest.title,
            "message": manifest.message,
            "created_at": manifest.created_at,
            "updated_at": manifest.updated_at,
            "completed_scenes": sum(s.status == "completed" for s in manifest.scene_results),
            "total_scenes": manifest.total_scenes,
            "final_artifact_id": manifest.final_artifact_id,
            "usage": manifest.usage.model_dump(),
            "artifacts": [
                {
                    "artifact_id": a.artifact_id,
                    "kind": a.kind,
                    "label": a.label,
                    "url": f"/api/jobs/{manifest.job_id}/artifacts/{a.artifact_id}",
                }
                for a in manifest.artifacts.values()
            ],
        }

"""Local geometry checks and contact sheets of actual native-renderer evidence.

No model calls or substitute images are made here. ``validate_layout`` computes
real geometry but emits no PNGs; only encoded-video frames count as visual
evidence. A passing report still requires a person to inspect the contact sheets
for mathematical meaning and defects not represented by the renderer's bounds.
"""

from __future__ import annotations

import argparse
import json
import math
from itertools import combinations
from pathlib import Path

from PIL import Image, ImageDraw

from evals.support import save


def _overlap(a: dict, b: dict) -> bool:
    return (
        min(a["right"], b["right"]) - max(a["left"], b["left"]) > 1e-6
        and min(a["top"], b["top"]) - max(a["bottom"], b["bottom"]) > 1e-6
    )


def check_events(events: list[dict]) -> list[dict]:
    """Check the bounds the renderer actually records, without inferring others."""
    issues = []
    for event in events:
        beat = event["beat_id"]
        boxes = [
            ("content", event["content_bounds"]),
            ("caption", event["caption_bounds"]),
            *[(f"formula_{i}", b) for i, b in enumerate(event["formula_bounds"])],
        ]
        for name, bounds in boxes:
            if not all(math.isfinite(value) for value in bounds.values()):
                issues.append({"beat_id": beat, "code": "nonfinite_bounds", "object": name})
            elif (
                bounds["left"] < -6.7 - 1e-6
                or bounds["right"] > 6.7 + 1e-6
                or bounds["bottom"] < -3.53 - 1e-6
                or bounds["top"] > 2.25 + 1e-6
            ):
                issues.append({"beat_id": beat, "code": "unsafe_bounds", "object": name})
        if _overlap(event["content_bounds"], event["caption_bounds"]):
            issues.append({"beat_id": beat, "code": "content_caption_collision"})
        formula_em = event["formula_em"]
        if formula_em is not None and formula_em < 0.468 - 1e-6:
            issues.append({"beat_id": beat, "code": "unreadable_formula", "em": formula_em})
        for i, j in combinations(range(len(event["formula_bounds"])), 2):
            if _overlap(event["formula_bounds"][i], event["formula_bounds"][j]):
                issues.append({"beat_id": beat, "code": "formula_collision", "indices": [i, j]})
        for name in ("matrix_cells", "inline_matrix_cells"):
            for i, j in combinations(range(len(event[name])), 2):
                if _overlap(event[name][i]["bounds"], event[name][j]["bounds"]):
                    issues.append(
                        {
                            "beat_id": beat,
                            "code": "matrix_cell_collision",
                            "kind": name,
                            "indices": [i, j],
                        }
                    )
    return issues


def _contact_sheets(frames: list[dict], output: Path) -> list[str]:
    """Keep full-resolution originals; make six-frame preview pages for inspection."""
    paths = []
    for page, start in enumerate(range(0, len(frames), 6), 1):
        selected = frames[start : start + 6]
        sheet = Image.new("RGB", (1280, math.ceil(len(selected) / 2) * 390), "#0c1728")
        draw = ImageDraw.Draw(sheet)
        for index, record in enumerate(selected):
            x, y = index % 2 * 640, index // 2 * 390
            with Image.open(record["path"]) as source:
                preview = source.convert("RGB")
                preview.thumbnail((640, 360), Image.Resampling.LANCZOS)
                sheet.paste(preview, (x, y))
            # IDs come from LocalID / generated filenames and are ASCII.
            label = f"{record['scene_id']} / {record['beat_id']} @ {record['seconds']:.2f}s"
            draw.text((x + 8, y + 366), label, fill="#e9f0ff")
        path = output / f"contact_sheet_{page:02d}.png"
        sheet.save(path)
        paths.append(str(path.resolve()))
    return paths


def inspect_render_tree(root: Path, *, output: Path | None = None) -> dict:
    """Inspect an existing ``runtime/renders/<job_id>`` without rerendering it.

    Failed workers remain failed and their original error/log paths are included.
    Missing result files or missing keyframes become explicit issues. Layout-only
    results can establish geometry checks, never successful visual verification.
    """
    root = Path(root).resolve()
    output = Path(output).resolve() if output is not None else root / "visual_review"
    output.mkdir(parents=True, exist_ok=True)
    artifacts, issues, frames = [], [], []
    for input_path in sorted(root.rglob("input.json")):
        data = json.loads(input_path.read_text(encoding="utf-8"))
        if "scene" not in data and "videos" not in data:
            continue
        directory = input_path.parent
        result_path = directory / "result.json"
        scene_id = data["scene"]["scene_id"] if "scene" in data else "final"
        record = {
            "scene_id": scene_id,
            "input": str(input_path),
            "result": str(result_path),
            "worker_log": str(directory / "worker.log"),
            "error_file": str(directory / "error.txt")
            if (directory / "error.txt").is_file()
            else None,
        }
        artifacts.append(record)
        if not result_path.is_file():
            record["status"] = "missing_result"
            issues.append({"scene_id": scene_id, "code": "missing_result", "path": str(directory)})
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        record.update(status=result["status"], layout_only=result.get("layout_only", False))
        if result["status"] != "success":
            issues.append(
                {
                    "scene_id": scene_id,
                    "code": "renderer_error",
                    "message": result.get("message"),
                    "path": str(directory),
                }
            )
            continue
        events = result.get("events", [])
        record["events"] = events
        record["media"] = result.get("media")
        record["frame_count"] = len(result.get("frames", []))
        if "video_path" in result:
            video = directory / result["video_path"]
            record["video"] = str(video)
            if not video.is_file():
                issues.append({"scene_id": scene_id, "code": "missing_video", "path": str(video)})
        issues.extend(
            {"scene_id": scene_id, "path": str(directory), **item} for item in check_events(events)
        )
        if "scene" in data:
            expected = [beat["beat_id"] for beat in data["scene"]["beats"]]
            if [event["beat_id"] for event in events] != expected:
                issues.append({"scene_id": scene_id, "code": "missing_beat_evidence"})
            if not record["layout_only"] and len(result.get("frames", [])) != len(expected):
                issues.append({"scene_id": scene_id, "code": "missing_beat_frames"})
        for index, frame in enumerate(result.get("frames", [])):
            path = (directory / frame["path"]).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError(f"Renderer frame escaped its evidence directory: {path}")
            if not path.is_file():
                issues.append({"scene_id": scene_id, "code": "missing_frame", "path": str(path)})
                continue
            with Image.open(path) as image:
                image.load()
                if image.size != (1280, 720):
                    issues.append(
                        {
                            "scene_id": scene_id,
                            "code": "unexpected_frame_size",
                            "path": str(path),
                            "size": list(image.size),
                        }
                    )
            frames.append(
                {
                    "path": str(path),
                    "scene_id": scene_id,
                    "beat_id": events[index]["beat_id"]
                    if index < len(events)
                    else f"boundary_{index}",
                    "seconds": frame["actual_seconds"],
                }
            )
    report = {
        "root": str(root),
        "status": "fail"
        if issues
        else "geometry_pass_frames_available"
        if frames
        else "unverified_frames",
        "counts": {
            "artifacts": len(artifacts),
            "layout_checks": sum(item.get("layout_only", False) for item in artifacts),
            "rendered_scenes": sum(
                item.get("video") is not None and item["scene_id"] != "final" for item in artifacts
            ),
            "frames": len(frames),
            "issues": len(issues),
        },
        "issues": issues,
        "artifacts": artifacts,
        "frames": frames,
        "contact_sheets": _contact_sheets(frames, output),
        "limits": [
            "Layout checks emit geometry only; all PNGs here come from decoded actual videos.",
            "No semantic or complete visual pass is claimed without viewing the frames.",
            "Object-label collisions and in-animation transients lack exported per-object bounds.",
            "Formula, caption, content and matrix-cell boxes are checked where recorded.",
        ],
    }
    save(output / "report.json", report)
    return report


def inspect_plan(plan, renderer, root: Path, budget, *, assemble: bool = False) -> dict:
    """Render a real plan and save visual evidence; optionally assemble a full movie.

    This is intentionally a real render, with the original timings and all beats.
    A layout dry run cannot supply keyframes. Exceptions are recorded and then
    re-raised so a caller cannot silently turn a failed render into success.
    """
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        renderer.validate_runtime(root / "runtime_check", budget)
        videos = []
        for index, scene in enumerate(plan.scenes):
            renderer.validate_layout(
                scene,
                root / "layout" / scene.scene_id,
                plan.outline,
                len(plan.scenes),
                budget,
                index,
            )
            directory = root / scene.scene_id
            result = renderer.render(scene, directory, plan, budget, index)
            videos.append(directory / result["video_path"])
        if assemble:
            renderer.assemble(videos, root / "final", plan, budget)
    except Exception as exc:
        save(root / "inspection_error.json", {"type": type(exc).__name__, "message": str(exc)})
        inspect_render_tree(root)
        raise
    return inspect_render_tree(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Existing native-render evidence directory")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_render_tree(args.root, output=args.output)
    print(
        json.dumps(
            {key: report[key] for key in ("status", "counts", "contact_sheets")},
            ensure_ascii=False,
            indent=2,
        )
    )
    return int(report["status"] == "fail")


if __name__ == "__main__":
    raise SystemExit(main())

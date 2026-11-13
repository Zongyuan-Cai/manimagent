"""One authorized frozen-payload scene comparison; no automatic model retries."""
from __future__ import annotations

import hashlib
import json
import traceback
from pathlib import Path
from types import SimpleNamespace

from evals.narrated import isolated_settings
from evals.support import save, source_hash
from evals.visual_review import inspect_render_tree
from manimagent.budget import Budget
from manimagent.llm.client import ChatProvider
from manimagent.schemas import Usage
from manimagent.video.models import BeatDraft, PlannedScene, SceneDraft, SceneOutline, VideoOutline
from manimagent.video.prompts import SCENE_PROMPT
from manimagent.video.renderer.process import NativeRenderer
from manimagent.video.timeline import resolve_scene


REPO = Path(__file__).resolve().parents[3]
ROOT = Path(__file__).resolve().parent
ROUND = ROOT.parent / "round-11"
record = json.loads((ROUND / "record.json").read_text(encoding="utf-8"))
matches = [
    (i, c) for i, c in enumerate(record["calls"])
    if c.get("event") == "request" and c["role"] == "scene_generator"
    and json.loads(c["request"]["messages"][1]["content"]).get("scene", {}).get("scene_id") == "s5"
]
index, original_request = matches[0]
source_text = original_request["request"]["messages"][1]["content"]
payload = json.loads(source_text)
assert payload["feedback"] is None and payload["previous_scene"] is None
save(ROOT / "source_payload.txt", source_text)
save(ROOT / "source_payload.json", payload)
save(ROOT / "source_request.json", original_request["request"])
save(ROOT / "source_request_with_schema_repair.json", matches[-1][1]["request"])
save(ROOT / "original_initial_response.txt", next(
    c["raw_text"] for c in record["calls"][index + 1:] if "raw_text" in c
))
save(ROOT / "candidate_system_prompt.txt", SCENE_PROMPT)
original_artifact = next(
    a for a in json.loads((ROUND / "visual/report.json").read_text(encoding="utf-8"))["artifacts"]
    if a["scene_id"] == "s5"
)
original_input = json.loads(Path(original_artifact["input"]).read_text(encoding="utf-8"))
save(ROOT / "original_final_scene.json", original_input["scene"])
outline = VideoOutline.model_validate(payload["global_outline"])
spec = SceneOutline.model_validate(payload["scene"])
original_scene = PlannedScene.model_validate(original_input["scene"])
original_draft = SceneDraft(scene_id=spec.scene_id, beats=[
    {key: value for key, value in beat.items() if key in BeatDraft.model_fields}
    for beat in original_input["scene"]["beats"]
])
original_resolved = resolve_scene(spec, original_draft)
assert original_resolved.model_dump() == original_scene.model_dump()
result = {
    "source_round": 11, "scene_id": "s5", "new_model_calls_requested": 1,
    "source_payload_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
    "candidate_prompt_sha256": hashlib.sha256(SCENE_PROMPT.encode()).hexdigest(),
    "source_hash_at_start": source_hash(REPO),
    "baseline_note": "R11 final scene includes one schema-repair response; original user payload unchanged.",
}
save(ROOT / "started.json", result)
calls = []
candidate = None
with isolated_settings(None, ROOT) as settings:
    settings.tts.enabled = False
    settings.max_model_calls = 1
    settings.task_timeout = 900
    settings.model_timeout = 90
    profile = settings.model_for("scene_generator")
    profile.thinking = "disabled"
    profile.max_output_tokens = 16000
    assert profile.model == original_request["request"]["model"]
    usage = Usage()
    budget = Budget(settings, usage)
    budget.retry_allowance = 0
    try:
        draft = ChatProvider(settings, trace=calls.append).complete(
            "scene_generator", SCENE_PROMPT, payload, SceneDraft, budget
        )
        save(ROOT / "candidate_draft.json", draft.model_dump())
        candidate = resolve_scene(spec, draft)
        save(ROOT / "candidate_scene.json", candidate.model_dump())
    except Exception as exc:
        result["candidate_generation_error"] = {
            "type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()
        }
    finally:
        save(ROOT / "candidate_calls.json", calls)
        save(ROOT / "candidate_usage.json", usage.model_dump())
        requests = [c for c in calls if c.get("event") == "request"]
        assert len(requests) == 1
        candidate_text = requests[0]["request"]["messages"][1]["content"]
        result["candidate_payload_sha256"] = hashlib.sha256(candidate_text.encode()).hexdigest()
        result["payload_exactly_equal"] = candidate_text == source_text
        assert result["payload_exactly_equal"]
        result["new_model_calls_actual"] = usage.model_calls
        result["usage"] = usage.model_dump()
        save(ROOT / "result.json", result)
    for name, scene in (("original", original_resolved), ("candidate", candidate)):
        if scene is None:
            continue
        directory = ROOT / name
        local_budget = Budget(settings, Usage())
        plan = SimpleNamespace(outline=outline, scenes=outline.scenes)
        renderer = NativeRenderer()
        try:
            renderer.validate_layout(scene, directory / "layout" / scene.scene_id,
                                     outline, len(outline.scenes), local_budget, 4)
            rendered = renderer.render(scene, directory / scene.scene_id, plan, local_budget, 4)
            result[name] = {"status": "rendered", "wall_seconds": rendered["wall_seconds"]}
        except Exception as exc:
            result[name] = {"status": "failed", "type": type(exc).__name__,
                            "message": str(exc), "traceback": traceback.format_exc()}
        result[name]["visual"] = inspect_render_tree(directory)
        save(ROOT / "result.json", result)
print(json.dumps({k: result[k] for k in ("payload_exactly_equal", "new_model_calls_actual")}, indent=2))

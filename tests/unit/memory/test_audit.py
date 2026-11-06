import json

from manimagent.memory.audit import memory_context, publish_memory_context, render_memory_context


def request(role, payload, attempt=1):
    return {
        "event": "request",
        "role": role,
        "attempt": attempt,
        "request": {
            "messages": [
                {"role": "system", "content": "private system text"},
                {"role": "user", "content": json.dumps(payload)},
            ]
        },
    }


def record():
    return {
        "job_id": "job_" + "a" * 32,
        "memory_snapshot": {
            "captured_at": "2026-11-06T01:00:00+00:00",
            "entries": [
                {
                    "memory_id": "memory_" + "b" * 32,
                    "field": "visual.palette",
                    "value": "蓝色",
                    "scope": "global",
                    "layer": "deep",
                }
            ],
        },
    }


def test_audit_uses_actual_requests_instead_of_inventing_projected_calls():
    data = record()
    visual = {"field": "visual.palette", "value": "蓝色", "scope": "global"}
    data["calls"] = [
        request(
            "answer_writer",
            {
                "user_memory": {"deep": []},
                "recent_user_memory": [],
                "instruction": "private question",
            },
        ),
        {"event": "call_summary", "http_status": 200},
        request(
            "scene_generator",
            {
                "user_memory": {"deep": [visual]},
                "recent_user_memory": [],
                "materials": "private material",
            },
        ),
        {"event": "call_summary", "http_status": 503},
        request(
            "scene_generator", {"user_memory": {"deep": []}, "recent_user_memory": []}, attempt=2
        ),
    ]
    context = memory_context(data, status="running")
    assert [call["http_status"] for call in context["calls"]] == [200, 503, None]
    assert [call["entries"][0]["included"] for call in context["calls"]] == [False, True, False]
    assert "视觉偏好" in context["calls"][0]["entries"][0]["reason"]
    markdown = render_memory_context(context)
    assert "private" not in markdown
    assert "2026-11-06" in markdown and "蓝色" in markdown
    assert "不证明" in markdown


def test_unstarted_disabled_and_historical_missing_memory_are_distinct():
    waiting = memory_context(record(), status="queued")
    assert waiting["calls"] == [] and "尚无" in render_memory_context(waiting)
    old = {"job_id": record()["job_id"]}
    assert memory_context(old, status="completed")["snapshot_state"] == "unknown"
    old["memory_snapshot"] = None
    assert memory_context(old, status="completed")["snapshot_state"] == "disabled"
    old["memory_snapshot_known"] = False
    assert memory_context(old, status="completed")["snapshot_state"] == "unknown"


def test_audit_export_failure_keeps_record_and_is_recoverable(tmp_path, monkeypatch):
    from manimagent.memory import audit

    data = record()
    with monkeypatch.context() as fault:
        fault.setattr(
            audit, "write_text", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
        )
        publish_memory_context(tmp_path, data, status="completed")
    assert data["memory_context_export_error"]["code"] == "memory_context_export_failed"
    assert data["memory_snapshot"] == record()["memory_snapshot"]
    publish_memory_context(tmp_path, data, status="completed")
    assert "memory_context_export_error" not in data
    assert (tmp_path / "memory/snapshots" / (data["job_id"] + ".md")).is_file()


def test_memory_fences_cannot_break_the_audit_json_block():
    data = record()
    data["memory_snapshot"]["entries"][0]["value"] = "```example```"
    markdown = render_memory_context(memory_context(data, status="queued"))
    assert "````json\n" in markdown

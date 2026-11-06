"""The provider, video context reservation and shared budget use the same snapshot."""

import time
from types import SimpleNamespace

from manimagent.budget import Budget
from manimagent.config import ModelConfig, Settings
from manimagent.jobs import execution
from manimagent.schemas import Usage


def test_video_execution_passes_overridden_snapshot_to_service_and_budget(monkeypatch):
    settings = Settings(
        models={
            "text": ModelConfig(
                base_url="https://controlled.invalid/v1",
                model="controlled",
                auth_mode="none",
                max_output_tokens=6000,
                context_window=128000,
            )
        },
        video_max_output_tokens=10000,
        max_model_calls=30,
        model_timeout=90,
    )
    # Isolate the stage wiring from persistence and network behavior, covered elsewhere.
    task = object.__new__(execution.JobExecution)
    task.settings, task.usage = settings, Usage(model_calls=2)
    task.budget = Budget(settings, task.usage)
    task.overall_deadline = time.monotonic() + 900
    task.record = {}
    task.store = SimpleNamespace(
        load=lambda *args: SimpleNamespace(
            memory_snapshot=None, model_fields_set={"memory_snapshot"}
        )
    )
    task.transport = task.credentials = None
    task.renderer = SimpleNamespace(preflight=lambda: [])
    task.stage = lambda name: None
    captured = {}

    def service(store, provider, renderer, snapshot):
        captured.update(store=store, provider=provider, renderer=renderer, snapshot=snapshot)

        def generate(request, budget, record, progress):
            captured.update(request=request, budget=budget, record=record)
            return "controlled-result"

        return SimpleNamespace(generate=generate)

    monkeypatch.setattr(execution, "VideoService", service)
    assert task.video("answer_" + "a" * 32) == "controlled-result"
    snapshot = captured["snapshot"]
    assert snapshot is captured["provider"].settings is captured["budget"].settings
    assert captured["budget"] is task.budget
    assert snapshot is not settings
    for role in ("video_planner", "scene_generator", "plan_checker"):
        assert snapshot.model_for(role).max_output_tokens == 10000
        assert settings.model_for(role).max_output_tokens == 6000
    assert snapshot.model_timeout == 60 and settings.model_timeout == 90
    assert snapshot.max_model_calls == 26
    assert captured["budget"].retry_allowance == 1

import pytest

from manimagent.budget import BudgetExceeded
from manimagent.materials.process import parse_isolated


def test_budget_failure_immediately_after_spawn_still_reaps_child(tmp_path, monkeypatch):
    events = []

    class Process:
        def poll(self):
            return None

        def kill(self):
            events.append("kill")

        def wait(self):
            events.append("wait")

    class Budget:
        def remaining(self):
            raise BudgetExceeded("expired")

    monkeypatch.setattr("manimagent.materials.process.subprocess.Popen", lambda *a, **k: Process())
    with pytest.raises(BudgetExceeded, match="expired"):
        parse_isolated({}, tmp_path, 10, Budget())
    assert events == ["kill", "wait"]
    assert list(tmp_path.iterdir()) == []

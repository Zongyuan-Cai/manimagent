import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import Settings
from manimagent.memory.store import MemoryStore
from tests.support.memory import Clock, observe


@pytest.mark.parametrize("temporary", [False, True])
def test_patch_preserves_layer_until_explicit_long_term_confirmation(tmp_path, temporary):
    app = create_app(Settings(runs_dir=tmp_path))
    manager = app.state.job_manager
    clock = Clock()
    manager.memory = MemoryStore(manager.memory.path, clock=clock)
    original = observe(manager.memory, temporary=temporary)
    clock.advance(2)
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v2/memory/{original.memory_id}", json={"value": "先解释图例"}
        )
        assert response.status_code == 200, response.text
        corrected = response.json()
        assert corrected["layer"] == "short_term" and corrected["temporary"] is temporary
        assert corrected["expires_at"] == original.expires_at
        assert corrected["confirmed_at"] is None
        promoted = client.patch(
            f"/api/v2/memory/{corrected['memory_id']}",
            json={"value": "以后先解释图例", "confirm_long_term": True},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["layer"] == "deep" and not promoted.json()["temporary"]
        assert promoted.json()["expires_at"] is None and promoted.json()["confirmed_at"]


def test_patch_cannot_resurrect_expired_memory_even_with_confirmation(tmp_path):
    app = create_app(Settings(runs_dir=tmp_path))
    manager = app.state.job_manager
    clock = Clock()
    manager.memory = MemoryStore(manager.memory.path, clock=clock)
    original = observe(manager.memory)
    clock.advance(14)
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v2/memory/{original.memory_id}",
            json={"value": "新值", "confirm_long_term": True},
        )
        # Background expiry cleanup may already have removed this record.
        assert response.status_code in {404, 422}
        assert response.json()["error"]["code"] in {"invalid_memory", "memory_not_found"}
        assert all(entry.value == original.value for entry in manager.memory.list_entries())

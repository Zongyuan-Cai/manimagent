import pytest
from fastapi.testclient import TestClient

from manimagent.api import create_app
from manimagent.config import Settings


@pytest.mark.parametrize(
    "asset",
    [
        "main.js",
        "api.js",
        "dom.js",
        "workspace.js",
        "results.js",
        "settings.js",
        "app.css",
        "package.json",
    ],
)
def test_updated_application_assets_are_not_hidden_by_browser_conditional_cache(tmp_path, asset):
    with TestClient(create_app(Settings(runs_dir=tmp_path))) as client:
        initial = client.get(f"/static/{asset}")
        response = client.get(
            f"/static/{asset}",
            headers={
                "If-None-Match": initial.headers["etag"],
                "If-Modified-Since": initial.headers["last-modified"],
            },
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/").headers["cache-control"] == "no-store"

import pytest

from manimagent.config import ModelConfig, Settings
from manimagent.llm.settings import LLMSettingsStore


@pytest.mark.parametrize(
    "names,roles,expected",
    [
        (["writer"], {}, {"answer_writer": "writer", "plan_checker": "writer"}),
        (["writer", "text"], {}, {"answer_writer": "text", "scene_generator": "text"}),
        (
            ["text", "writer", "check"],
            {"answer_writer": "writer", "answer_checker": "check"},
            {"answer_writer": "writer", "scene_generator": "writer", "plan_checker": "check"},
        ),
    ],
)
def test_runtime_and_configuration_view_resolve_the_same_models(tmp_path, names, roles, expected):
    settings = Settings(models={name: ModelConfig(model=name) for name in names}, roles=roles)
    store = LLMSettingsStore(settings, tmp_path)
    view = store.view(settings)
    for role, identifier in expected.items():
        assert settings.model_id_for(role) == identifier
        assert settings.model_for(role).model == identifier
        assert view["roles"][role] == identifier

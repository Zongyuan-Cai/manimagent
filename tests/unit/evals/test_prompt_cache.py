import json

import httpx
import pytest

from evals.prompt_cache import payload_for, run, solution_matches, workload
from manimagent.config import ModelConfig, Settings


def configured():
    return Settings(
        models={
            "text": ModelConfig(
                base_url="https://model.example/v1", model="cache-test", auth_mode="none"
            )
        }
    )


@pytest.mark.parametrize(
    ("answer", "expected", "matches"),
    [
        ("4x=-8，再得x=-2", -2, True),
        ("8x=16，再得 x = 2", 2, True),
        ("4x=-8", -2, None),
        ("x=-3", -2, False),
    ],
)
def test_solution_check_does_not_read_coefficient_terms_as_the_solution(answer, expected, matches):
    assert solution_matches(answer, expected) is matches


def test_workload_preserves_material_refs_and_preferences_across_conditions():
    data = workload()
    blocks = data["shared"]["materials"][0]["blocks"]
    assert len(blocks) == len({block["equation"] for block in blocks}) == 50
    assert len(data["shared"]["allowed_source_refs"]) == 50
    assert len({round_["instruction"] for round_ in data["rounds"]}) == 3
    snapshots = [round_["snapshot"] for round_ in data["rounds"]]
    assert snapshots[0] == snapshots[1] == snapshots[2]
    current = []
    for item in data["rounds"]:
        old = payload_for("legacy", data["shared"], item)
        new = payload_for("stable_prefix", data["shared"], item)
        assert next(iter(old)) == "instruction" and list(old)[-1] == "user_memory"
        assert next(iter(new)) == "user_memory" and list(new)[-1] == "instruction"
        assert old["materials"] == new["materials"]
        assert old["allowed_source_refs"] == new["allowed_source_refs"]
        assert old["instruction"] == new["instruction"]
        current.append(json.dumps(new, ensure_ascii=False).split(', "instruction":')[0])
    assert current[0] == current[1] == current[2]


@pytest.mark.parametrize("failed", [False, True])
def test_six_calls_are_interleaved_bounded_and_never_retried(tmp_path, failed):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if failed:
            return httpx.Response(503, text="private-upstream-error")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "x = -2"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 5,
                    "prompt_tokens_details": {"cached_tokens": 50},
                },
            },
        )

    output = tmp_path / "fresh"
    settings = configured()
    result = run(settings, lambda _: None, output, transport=httpx.MockTransport(handler))
    assert len(requests) == result["usage"]["model_calls"] == 6
    assert all(
        body["max_tokens"] == 384 and body["thinking"] == {"type": "disabled"} for body in requests
    )
    assert len({body["messages"][0]["content"] for body in requests}) == 1
    assert [(call["variant"], call["round"]) for call in result["calls"]] == [
        (variant, round_) for round_ in range(1, 4) for variant in ("legacy", "stable_prefix")
    ]
    assert all(call["attempts"] == 1 and call["answered"] is not failed for call in result["calls"])
    assert result["usage"]["cached_input_tokens"] == (None if failed else 300)
    assert settings.model_for("answer_writer").max_output_tokens == 6000
    assert "private-upstream-error" not in (output / "summary.json").read_text(encoding="utf-8")
    assert (output / "报告.md").is_file()
    before = (output / "calls.json").read_bytes()
    with pytest.raises(FileExistsError):
        run(settings, lambda _: None, output, transport=httpx.MockTransport(handler))
    assert (output / "calls.json").read_bytes() == before and len(requests) == 6

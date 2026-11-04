from __future__ import annotations

import pytest
from pydantic import ValidationError

from manimagent.video.models import SceneOutline, VideoOutline


def scene(identifier):
    return dict(
        scene_id=identifier, title="测试", objective="测试目标", unit_ids=["u1"], beat_count=3
    )


@pytest.mark.parametrize("identifier", ["CON", "aux", "Nul", "PRN", "com1", "LPT9"])
def test_scene_directory_rejects_windows_devices_everywhere(identifier):
    with pytest.raises(ValidationError):
        SceneOutline(**scene(identifier))


@pytest.mark.parametrize("ids", [["s1", "S1", "s3"], ["s1", "s1", "s3"]])
def test_outline_rejects_shared_scene_paths_before_rendering(ids):
    with pytest.raises(ValidationError):
        VideoOutline(
            status="ready",
            message="就绪",
            title="测试",
            audience="学生",
            difficulty="basic",
            difficulty_reason="基础",
            duration_reason="完整",
            scenes=[scene(i) for i in ids],
        )

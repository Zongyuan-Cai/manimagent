"""Opt-in real Manim/FFmpeg tests: RUN_NATIVE_TESTS=1, with the animation extra."""

import math
import os
import subprocess
import wave
from array import array
from itertools import combinations
from types import SimpleNamespace

import pytest

from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.errors import ContentError
from manimagent.schemas import Usage
from manimagent.video.models import SceneDraft, SceneOutline
from manimagent.video.renderer.media import probe
from manimagent.video.renderer.process import NativeRenderer
from manimagent.video.timeline import resolve_scene

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NATIVE_TESTS") != "1", reason="opt-in native renderer"
)


def planned(identifier, diagrams):
    outline = SceneOutline(
        scene_id=identifier,
        title="完整演示与结果核对",
        objective="解释计算与图形",
        unit_ids=["u1"],
        beat_count=3,
    )
    draft = SceneDraft(
        scene_id=identifier,
        beats=[
            dict(
                beat_id=f"b{i}",
                unit_ids=["u1"],
                headline="核对图形和数值",
                screen_text="先核对题目给定条件，再逐项代入计算。每个矩阵元素保留原有精度，观察图形与公式的对应关系。",
                spoken_text="先核对题目给定条件，再逐项代入计算，观察图形与公式的对应关系。",
                cue_text="先核对",
                reading_reason="读完条件与计算依据",
                formulas=[
                    r"\sigma^2=\frac{1}{5}\sum_{i=1}^{5}(x_i-3)^2"
                    if identifier == "formulas" and i == 0
                    else r"x=\frac{1}{2}"
                ],
                diagram=diagram,
            )
            for i, diagram in enumerate(diagrams)
        ],
    )
    return resolve_scene(outline, draft)


def test_fractional_beat_waits_preserve_exact_final_frame_count(tmp_path):
    scene = planned("fractional", [{"kind": "none"}] * 3)
    clock = 0.0
    for beat, frames in zip(scene.beats, [416, 310, 329], strict=True):
        beat.start_seconds = clock
        beat.stable_seconds = clock + 1
        beat.cue_seconds = clock + 1.7
        beat.narration_end_seconds = clock + 8
        clock += frames / 30
        beat.end_seconds = clock
    scene.duration_seconds = clock
    plan = SimpleNamespace(outline=SimpleNamespace(title="语音帧数回归"), scenes=[scene])
    result = NativeRenderer().render(scene, tmp_path, plan, Budget(Settings(), Usage()), 0)
    info = probe(tmp_path / result["video_path"], decode=True)
    assert info["decoded_frames"] == 1055
    assert abs(info["duration_seconds"] - clock) < 0.0001
    assert all(
        abs(event["end_seconds"] - beat.end_seconds) < 0.001
        for event, beat in zip(result["events"], scene.beats, strict=True)
    )


def test_real_render_precision_geometry_and_decoded_complete_movie(tmp_path):
    renderer = NativeRenderer()
    budget = Budget(Settings(runs_dir=tmp_path), Usage())
    near = [[1, 1.0000001], [1, 1.0000002]]
    scenes = [
        planned("matrix", [{"kind": "matrix", "matrix": near}] * 3),
        planned(
            "geometry",
            [
                {
                    "kind": "plane",
                    "objects": [
                        {
                            "object_id": "a",
                            "kind": "vector",
                            "points": [[0, 0], [3, 2]],
                            "label": "a",
                        },
                        {"object_id": "c", "kind": "circle", "points": [[1, 1]], "radius": 0.5},
                    ],
                }
            ]
            * 3,
        ),
        planned("formulas", [{"kind": "none"}] * 3),
        planned("cases", [{"kind": "none"}] * 3),
    ]
    scenes[1].beats[0].formulas = [r"A=(0,0)\quad B=(4,0)\quad C=(1,3)"]
    scenes[2].beats[1].formulas = [r"A^{-1}=\frac{1}{ad-bc}\begin{pmatrix}d&-b\\-c&a\end{pmatrix}"]
    scenes[2].beats[2].formulas = [r"\begin{bmatrix}2&1\\1&1\end{bmatrix}^{-1}"]
    scenes[3].beats[0].formulas = [r"\begin{cases}2x+y=7\\x-y=2\end{cases}"]
    scenes[3].beats[1].formulas = [r"f(x)=\begin{cases}x&x\geq0\\-x&x<0\end{cases}"]
    scenes[3].beats[2].formulas = [r"\begin{pmatrix}2&1\\1&1\end{pmatrix}^T"]
    scenes[3].beats[
        2
    ].screen_text = (
        "因为 b ≠ 0，所以 b·b = ||b||² > 0，可除以 b·b，得到 c = (a·b)/(b·b)，于是 p = c b。"
    )
    plan = SimpleNamespace(
        outline=SimpleNamespace(title="真实渲染回归"),
        scenes=scenes,
        duration_seconds=sum(s.duration_seconds for s in scenes),
    )
    renderer.validate_runtime(tmp_path / "check", budget)
    paths = []
    for index, scene in enumerate(scenes):
        directory = tmp_path / scene.scene_id
        if index == 0 and os.name == "nt":
            # The former 64-character formula SVG path exceeded MAX_PATH here.
            directory = directory / ("nested_" + "x" * max(1, 215 - len(str(directory))))
        layout = renderer.validate_layout(
            scene, directory / "layout", plan.outline, len(scenes), budget, index
        )
        result = renderer.render(scene, directory, plan, budget, index)
        assert len(result["frames"]) == 3
        assert all(e["formula_em"] >= 0.468 for e in result["events"])
        if scene.scene_id == "formulas":
            # Stacked limits need more physical height, not smaller body glyphs.
            sizes = [e["formula_bounds"][0] for e in result["events"]]
            assert sizes[0]["top"] - sizes[0]["bottom"] > 1.4
            assert [c["text"] for c in result["events"][1]["inline_matrix_cells"]] == [
                "d",
                "-b",
                "-c",
                "a",
            ]
            assert [c["text"] for c in result["events"][2]["inline_matrix_cells"]] == [
                "2",
                "1",
                "1",
                "1",
            ]
            for event in result["events"][1:]:
                for a, b in combinations([c["bounds"] for c in event["inline_matrix_cells"]], 2):
                    assert (
                        a["right"] < b["left"]
                        or b["right"] < a["left"]
                        or a["top"] < b["bottom"]
                        or b["top"] < a["bottom"]
                    )
        if scene.scene_id == "geometry":
            assert result["events"][0]["formula_lines"] == ["A=(0,0)", "B=(4,0)", "C=(1,3)"]
        assert [e["matrix_cells"] for e in layout["events"]] == [
            e["matrix_cells"] for e in result["events"]
        ]
        if scene.scene_id == "cases":
            assert [c["text"] for c in result["events"][0]["inline_matrix_cells"]] == [
                "2x+y=7",
                "x-y=2",
            ]
            assert [c["text"] for c in result["events"][1]["inline_matrix_cells"]] == [
                "x",
                r"x\geq0",
                "-x",
                "x<0",
            ]
        if index == 0:
            for event in result["events"]:
                cells = event["matrix_cells"]
                assert [float(c["text"]) for c in cells] == [v for row in near for v in row]
                for a, b in combinations([c["bounds"] for c in cells], 2):
                    assert (
                        a["right"] < b["left"]
                        or b["right"] < a["left"]
                        or a["top"] < b["bottom"]
                        or b["top"] < a["bottom"]
                    )
        paths.append(directory / result["video_path"])
    result = renderer.assemble(paths, tmp_path / "full", plan, budget)
    media = probe(tmp_path / "full" / result["video_path"], decode=True)
    assert 120 <= media["duration_seconds"] <= 360
    assert media["decoded_frames"] > 3600
    assert media["audio_streams"] == 0


def test_unreadable_matrix_is_rejected_before_movie_render(tmp_path):
    scene = planned("dense", [{"kind": "matrix", "matrix": [[123456.789123456] * 4] * 4}] * 3)
    with pytest.raises(ContentError, match="可读性"):
        NativeRenderer().validate_layout(
            scene,
            tmp_path,
            SimpleNamespace(title="密集矩阵"),
            1,
            Budget(Settings(runs_dir=tmp_path), Usage()),
            0,
        )
    assert not (tmp_path / "scene.mp4").exists()


def test_real_audio_mux_decodes_both_streams_and_rejects_missing_or_short_audio(tmp_path):
    """Synthetic PCM tone, not MiMo speech: exercise the real mux worker and AAC decoder."""
    import av
    import imageio_ffmpeg

    directory = tmp_path / "final"
    directory.mkdir()
    silent = directory / "complete.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=1280x720:r=30:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(silent),
        ],
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    with pytest.raises(ValueError, match="流数量"):
        probe(silent, decode=True, expect_audio=True)

    rate = 24000
    audio = tmp_path / "synthetic_fixture.wav"
    samples = array(
        "h",
        [
            0 if index < rate // 4 else round(6000 * math.sin(2 * math.pi * 440 * index / rate))
            for index in range(2 * rate)
        ],
    )
    if os.sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(samples.tobytes())
    renderer = NativeRenderer()
    budget = Budget(Settings(runs_dir=tmp_path), Usage())
    result = renderer.mux_audio(directory, audio, SimpleNamespace(duration_seconds=2.0), budget)
    output = directory / result["video_path"]
    metadata = probe(output, decode=True, expect_audio=True)
    assert metadata["decoded_frames"] == 60
    assert metadata["audio_streams"] == 1
    assert metadata["audio_codec"] == "aac"
    assert abs(metadata["duration_seconds"] - metadata["audio_duration_seconds"]) < 0.05
    assert abs(metadata["decoded_audio_samples"] / metadata["audio_sample_rate"] - 2) < 0.05
    with av.open(str(output)) as container:
        assert any(abs(frame.to_ndarray()).max() > 0.01 for frame in container.decode(audio=0))
    with pytest.raises(ValueError, match="流数量"):
        probe(output, expect_audio=False)

    # A missing tail must fail validation rather than publish clipped narration.
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(samples[:rate].tobytes())
    with pytest.raises(ContentError, match="时长"):
        renderer.mux_audio(directory, audio, SimpleNamespace(duration_seconds=2.0), budget)

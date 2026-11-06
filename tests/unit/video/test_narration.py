from __future__ import annotations

import wave

import pytest

from manimagent.budget import Budget
from manimagent.config import Settings
from manimagent.errors import ContentError
from manimagent.schemas import Usage
from manimagent.video.narration import compose_pcm


def test_pcm_placement_preserves_leading_gap_speech_and_tail(tmp_path):
    source = tmp_path / "clip.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 8000)
    output = tmp_path / "out.wav"
    budget = Budget(Settings(), Usage())
    compose_pcm([(source, 1), (source, 3)], output, 5, budget)
    with wave.open(str(output), "rb") as wav:
        assert wav.getnframes() == 40000
        assert wav.readframes(8000) == bytes(16000)
        assert wav.readframes(8000) == b"\x01\x00" * 8000
        assert wav.readframes(8000) == bytes(16000)
        assert wav.readframes(8000) == b"\x01\x00" * 8000
        assert wav.readframes(8000) == bytes(16000)
    with pytest.raises(ContentError, match="重叠"):
        compose_pcm([(source, 0), (source, 0.5)], output, 3, budget)
    with pytest.raises(ContentError, match="终点"):
        compose_pcm([(source, 1)], output, 1.5, budget)


def test_unsigned_eight_bit_stereo_gaps_use_midpoint_silence(tmp_path):
    source = tmp_path / "unsigned-stereo.wav"
    rate = 8000
    # Alternating left/right values must survive placement, with 0x80 in both gap channels.
    pcm = b"\x90\x70" * rate
    with wave.open(str(source), "wb") as wav:
        wav.setparams((2, 1, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    output = tmp_path / "out.wav"
    compose_pcm([(source, 1)], output, 3, Budget(Settings(), Usage()))
    with wave.open(str(output), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getnframes()) == (2, 1, 3 * rate)
        assert wav.readframes(rate) == b"\x80\x80" * rate
        assert wav.readframes(rate) == pcm
        assert wav.readframes(rate) == b"\x80\x80" * rate

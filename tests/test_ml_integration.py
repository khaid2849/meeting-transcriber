"""Runs the real models on 60 s of the meeting recording. `uv run pytest -m slow`."""
import subprocess

import numpy as np
import pytest

from app import config

WAV = config.ROOT / "cuoc_hop_20261102_143735.wav"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not WAV.exists() or not config.VAD_MODEL.exists(),
                       reason="needs the meeting recording and ./setup_models.sh"),
]


@pytest.fixture(scope="module")
def samples():
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "300", "-t", "60", "-i", str(WAV),
           "-f", "f32le", "-ac", "1", "-ar", "16000", "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, np.float32)


def test_transcribe_excerpt(samples):
    from app.asr import transcribe
    progress = []
    segs = transcribe(samples, config.WHISPER_MODEL, "Odoo, AI", progress.append)
    assert len(segs) >= 3
    assert all(0 <= s["start"] <= s["end"] <= 60.5 for s in segs)
    assert all(s["text"].strip() for s in segs)
    assert progress and progress[-1] == pytest.approx(1.0)



def test_release_model_frees_gpu_memory(samples):
    import mlx.core as mx
    from app.asr import release_model, transcribe
    transcribe(samples[:16000 * 10], config.WHISPER_MODEL, "", lambda p: None)
    assert mx.get_active_memory() > 1e9
    release_model()
    assert mx.get_active_memory() < 3e8

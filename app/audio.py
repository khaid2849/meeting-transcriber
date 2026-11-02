"""ffmpeg/ffprobe wrappers."""
import json
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


class AudioError(Exception):
    """ffmpeg could not read or convert the file; message is the tail of its stderr."""


def _run(cmd: list[str]) -> bytes:
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()[-500:]
        raise AudioError(err or f"{cmd[0]} lỗi (mã {proc.returncode})")
    return proc.stdout


def probe(path: Path) -> dict:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)])
    info = json.loads(out)
    streams = info.get("streams", [])
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise AudioError("File không có luồng âm thanh")
    has_video = any(s.get("codec_type") == "video"
                    and not s.get("disposition", {}).get("attached_pic") for s in streams)
    return {"duration": float(info.get("format", {}).get("duration") or 0.0),
            "has_video": has_video}


def extract_audio(src: Path, dst: Path) -> None:
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
          "-vn", "-ac", "1", "-c:a", "aac", "-b:a", "64k", str(dst)])


def load_samples(path: Path, sr: int = SAMPLE_RATE) -> np.ndarray:
    out = _run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
                "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"])
    return np.frombuffer(out, dtype=np.float32).copy()

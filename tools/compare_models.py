"""Transcribe one excerpt with several Whisper models to compare accuracy and speed.

Usage: uv run python tools/compare_models.py --input X.wav --start 600 --duration 600 --out tmp/compare
PhoWhisper runs only when the optional `compare` dependency group is installed.
"""
import argparse
import subprocess
import time
from pathlib import Path

import numpy as np

MLX_MODELS = ["mlx-community/whisper-large-v3-mlx", "mlx-community/whisper-large-v3-turbo"]
PHO_MODEL = "vinai/PhoWhisper-large"


def load_excerpt(path: Path, start: float, duration: float) -> np.ndarray:
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", str(start), "-t", str(duration),
           "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, np.float32)


def fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def run_mlx(model: str, samples: np.ndarray) -> list[tuple[float, str]]:
    import mlx_whisper
    res = mlx_whisper.transcribe(samples, path_or_hf_repo=model, language="vi",
                                 condition_on_previous_text=False, verbose=None)
    return [(s["start"], s["text"].strip()) for s in res["segments"]]


def run_pho(samples: np.ndarray) -> list[tuple[float, str]]:
    from transformers import pipeline
    asr = pipeline("automatic-speech-recognition", model=PHO_MODEL, device="mps",
                   chunk_length_s=30, return_timestamps=True)
    out = asr({"raw": samples, "sampling_rate": 16000})
    return [((c["timestamp"][0] or 0.0), c["text"].strip()) for c in out["chunks"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--start", type=float, default=600)
    ap.add_argument("--duration", type=float, default=600)
    ap.add_argument("--out", type=Path, default=Path("tmp/compare"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    samples = load_excerpt(args.input, args.start, args.duration)

    runners = [(m, lambda m=m: run_mlx(m, samples)) for m in MLX_MODELS]
    try:
        import transformers  # noqa: F401
        runners.append((PHO_MODEL, lambda: run_pho(samples)))
    except ImportError:
        print("bỏ qua PhoWhisper (chưa cài nhóm 'compare')")

    for name, run in runners:
        t0 = time.time()
        lines = run()
        took = time.time() - t0
        dst = args.out / (name.split("/")[-1] + ".txt")
        body = "\n".join(f"[{fmt(t)}] {txt}" for t, txt in lines)
        dst.write_text(f"# {name} — {took:.1f}s cho {args.duration:.0f}s audio\n{body}\n", encoding="utf-8")
        print(f"{name}: {took:.1f}s, {len(lines)} đoạn → {dst}")


if __name__ == "__main__":
    main()

"""Speech-to-text: silero VAD picks speech, mlx-whisper transcribes each chunk."""
from collections.abc import Callable

import numpy as np
import sherpa_onnx

from app.config import CHUNK_MAX_LEN, LANGUAGE, VAD_MODEL
from app.filters import clean_segments, speech_chunks

SR = 16000


def detect_speech(samples: np.ndarray) -> list[tuple[float, float]]:
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(VAD_MODEL)
    config.silero_vad.min_silence_duration = 0.5
    config.silero_vad.min_speech_duration = 0.25
    config.sample_rate = SR
    window = config.silero_vad.window_size
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)

    regions: list[tuple[float, float]] = []

    def drain() -> None:
        while not vad.empty():
            seg = vad.front
            regions.append((seg.start / SR, (seg.start + len(seg.samples)) / SR))
            vad.pop()

    for i in range(0, len(samples), window):
        vad.accept_waveform(samples[i:i + window])
        drain()
    vad.flush()
    drain()
    return regions


def transcribe(samples: np.ndarray, model: str, keywords: str,
               on_progress: Callable[[float], None]) -> list[dict]:
    """Return cleaned Whisper segments (with word timings) in absolute seconds."""
    import mlx_whisper

    chunks = speech_chunks(detect_speech(samples), CHUNK_MAX_LEN, total=len(samples) / SR)
    segments: list[dict] = []
    for i, (start, end) in enumerate(chunks):
        piece = samples[int(start * SR):int(end * SR)]
        result = mlx_whisper.transcribe(
            piece, path_or_hf_repo=model, language=LANGUAGE,
            initial_prompt=keywords.strip() or None, condition_on_previous_text=False,
            word_timestamps=True, verbose=None)
        for seg in result["segments"]:
            segments.append({
                "start": round(start + seg["start"], 2),
                "end": round(min(end, start + seg["end"]), 2),
                "text": seg["text"],
                "words": [{"start": round(start + w["start"], 2), "end": round(start + w["end"], 2),
                           "word": w["word"]} for w in seg.get("words", [])],
                "avg_logprob": seg.get("avg_logprob"),
                "no_speech_prob": seg.get("no_speech_prob"),
            })
        on_progress((i + 1) / len(chunks))
    if not chunks:
        on_progress(1.0)
    return clean_segments(segments)

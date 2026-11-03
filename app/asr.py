"""Speech-to-text: silero VAD picks speech, mlx-whisper transcribes each chunk."""
from collections.abc import Callable

import numpy as np
import sherpa_onnx

from app.config import CHUNK_MAX_GAP, CHUNK_MAX_LEN, VAD_MODEL
from app.filters import needs_retry, pick_richer, speech_chunks

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


def transcribe(samples: np.ndarray, model: str, keywords: str, on_progress: Callable[[float], None],
               language: str = "vi") -> tuple[list[dict], str | None]:
    """Return (raw Whisper segments in absolute seconds, language used). Cleaning happens later.

    language "auto" detects the language on the first speech chunk and keeps it for the rest:
    per-chunk detection on short chunks flips between languages.
    """
    import mlx_whisper

    regions = detect_speech(samples)
    chunks = speech_chunks(regions, CHUNK_MAX_LEN, total=len(samples) / SR, max_gap=CHUNK_MAX_GAP)
    prompt = keywords.strip() or None
    lang = None if language == "auto" else language

    def run(piece: np.ndarray, initial_prompt: str | None) -> list[dict]:
        nonlocal lang
        result = mlx_whisper.transcribe(
            piece, path_or_hf_repo=model, language=lang, initial_prompt=initial_prompt,
            condition_on_previous_text=False,
            # Word timings are unused and cost ~3x per call on short chunks.
            word_timestamps=False, verbose=None)
        lang = lang or result.get("language")
        return result["segments"]

    segments: list[dict] = []
    for i, (start, end) in enumerate(chunks):
        piece = samples[int(start * SR):int(end * SR)]
        result = run(piece, prompt)
        speech = sum(max(0.0, min(end, e) - max(start, s)) for s, e in regions)
        # The keyword prompt sometimes derails Whisper into an outro hallucination for a whole chunk.
        if prompt and needs_retry(result, speech):
            result = pick_richer(result, run(piece, None))
        for seg in result:
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
    return segments, lang


def release_model() -> None:
    """Drop the cached Whisper model so an idle server holds ~50 MB instead of ~4 GB."""
    import gc

    import mlx.core as mx
    from mlx_whisper.transcribe import ModelHolder

    ModelHolder.model = None
    ModelHolder.model_path = None
    gc.collect()
    mx.clear_cache()

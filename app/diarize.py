"""Speaker diarization with sherpa-onnx (pyannote segmentation + speaker embeddings)."""
from collections.abc import Callable

import numpy as np
import sherpa_onnx

from app.assemble import normalize_turns
from app.config import CLUSTER_THRESHOLD, DIARIZE_THREADS, EMB_MODEL, SEG_MODEL, SPEAKER_MIN_SECONDS


def diarize(samples: np.ndarray, num_speakers: int | None,
            on_progress: Callable[[float], None]) -> list[tuple[float, float, int]]:
    """Return (start, end, speaker_index) turns sorted by start time."""
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_MODEL)),
            num_threads=DIARIZE_THREADS),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=DIARIZE_THREADS),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers or -1, threshold=CLUSTER_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Cấu hình tách người nói không hợp lệ (thiếu model? chạy ./setup_models.sh)")
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)

    def callback(done: int, total: int) -> int:
        on_progress(done / total if total else 1.0)
        return 0

    result = sd.process(samples, callback=callback).sort_by_start_time()
    on_progress(1.0)
    turns = [(round(r.start, 2), round(r.end, 2), int(r.speaker)) for r in result]
    # A fixed speaker count is the user's call, so only auto-detected stray clusters are folded.
    return turns if num_speakers else normalize_turns(turns, SPEAKER_MIN_SECONDS)

"""Paths, model ids and tuning values."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEETINGS_DIR = ROOT / "meetings"
MODELS_DIR = ROOT / "models"
PORT = 8765

WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
LANGUAGE = "vi"

VAD_MODEL = MODELS_DIR / "silero_vad.onnx"
SEG_MODEL = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_MODEL = MODELS_DIR / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

CLUSTER_THRESHOLD = 0.5
DIARIZE_THREADS = 4
# Speakers with less total talk are folded into the nearest main speaker (stray clusters).
SPEAKER_MIN_SECONDS = 15.0
PARAGRAPH_MAX_GAP = 2.0
PARAGRAPH_MAX_LEN = 60.0
CHUNK_MAX_LEN = 28.0
CHUNK_MAX_GAP = 4.0

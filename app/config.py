"""Paths, model ids and tuning values."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEETINGS_DIR = ROOT / "meetings"
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"  # AI settings (with API keys) and saved chats
PORT = 8765

WHISPER_MODEL = "mlx-community/whisper-large-v3-mlx"
LANGUAGE = "vi"  # default spoken language
LANGUAGES = ("vi", "en", "auto")  # "auto": Whisper detects it once, on the first speech

VAD_MODEL = MODELS_DIR / "silero_vad.onnx"

PARAGRAPH_MAX_GAP = 2.0
PARAGRAPH_MAX_LEN = 60.0
CHUNK_MAX_LEN = 28.0
CHUNK_MAX_GAP = 4.0

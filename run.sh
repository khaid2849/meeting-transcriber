#!/usr/bin/env bash
# Start the web app at http://localhost:8765 and open it in the browser.
set -euo pipefail
cd "$(dirname "$0")"
unset VIRTUAL_ENV

command -v ffmpeg >/dev/null || { echo "Thiếu ffmpeg: brew install ffmpeg"; exit 1; }
command -v uv >/dev/null || { echo "Thiếu uv: brew install uv"; exit 1; }
if [ ! -f models/silero_vad.onnx ] || [ ! -d models/sherpa-onnx-pyannote-segmentation-3-0 ]; then
  echo "Chưa có model. Chạy ./setup_models.sh trước."; exit 1
fi

URL=http://localhost:8765
(sleep 3; open "$URL") &
export HF_HUB_OFFLINE=1  # models are local after setup; no network at runtime
exec uv run uvicorn --factory app.main:build_app --host 127.0.0.1 --port 8765

#!/usr/bin/env bash
# Download the on-device models once. Safe to re-run: existing files are skipped.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p models
cd models

fetch() {  # fetch URL [OUTPUT]
  local url=$1 out=${2:-$(basename "$1")}
  if [ -e "$out" ]; then echo "đã có: $out"; return; fi
  echo "tải: $out"
  curl -fL --retry 3 -o "$out.part" "$url" && mv "$out.part" "$out"
}

base=https://github.com/k2-fsa/sherpa-onnx/releases/download
fetch "$base/asr-models/silero_vad.onnx"
# "recongition" is the upstream release tag's spelling.
fetch "$base/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
if [ ! -d sherpa-onnx-pyannote-segmentation-3-0 ]; then
  fetch "$base/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
  tar xjf sherpa-onnx-pyannote-segmentation-3-0.tar.bz2 && rm sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
fi
cd ..

echo "tải model Whisper (lần đầu mất vài phút)…"
uv run python -c "from huggingface_hub import snapshot_download; from app.config import WHISPER_MODEL; print(snapshot_download(WHISPER_MODEL))"

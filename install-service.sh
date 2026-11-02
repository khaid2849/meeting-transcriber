#!/usr/bin/env bash
# Install the app as a macOS LaunchAgent: it starts at login and restarts if it crashes.
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$(pwd)
LABEL=local.meeting-transcriber
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=8765
DOMAIN="gui/$(id -u)"

[ -x .venv/bin/python ] || { echo "Chưa có môi trường Python: chạy 'uv sync' trước."; exit 1; }
[ -f models/silero_vad.onnx ] || { echo "Chưa có model: chạy ./setup_models.sh trước."; exit 1; }
FFMPEG=$(command -v ffmpeg) || { echo "Thiếu ffmpeg: brew install ffmpeg"; exit 1; }

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Cổng $PORT đang bị chiếm (có thể ./run.sh đang chạy). Tắt nó rồi chạy lại script này."; exit 1
fi

mkdir -p logs "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/python</string>
    <string>-m</string><string>uvicorn</string>
    <string>--factory</string><string>app.main:build_app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$PORT</string>
    <string>--no-access-log</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$FFMPEG"):/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HF_HUB_OFFLINE</key><string>1</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/service.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/service.log</string>
</dict>
</plist>
EOF
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"

for _ in $(seq 1 30); do
  if curl -fs -o /dev/null "http://localhost:$PORT/"; then
    echo "Đã cài service. App chạy tại http://localhost:$PORT (tự bật khi đăng nhập)."
    echo "Log: $ROOT/logs/service.log · Gỡ: ./uninstall-service.sh"
    exit 0
  fi
  sleep 1
done
echo "Service đã cài nhưng app chưa trả lời sau 30 giây. Xem log: $ROOT/logs/service.log"; exit 1

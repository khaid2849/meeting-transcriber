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

# The Tailscale name lets `tailscale serve` (see ./enable-tailscale.sh) reach the app.
TS_HOST=""
if command -v tailscale >/dev/null; then
  TS_HOST=$(tailscale status --json 2>/dev/null \
    | python3 -c 'import sys, json; print(json.load(sys.stdin).get("Self", {}).get("DNSName", "").rstrip("."))' \
    2>/dev/null || true)
fi

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
for _ in $(seq 1 10); do  # bootout returns before launchd has fully removed the service
  launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done
for _ in $(seq 1 10); do  # ...and before the old process has released the port
  lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 1
done
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
    <key>MT_ALLOWED_HOSTS</key><string>$TS_HOST</string>
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
for attempt in 1 2 3 4 5; do  # "Bootstrap failed: 5" is transient right after a bootout
  launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null && break
  [ "$attempt" = 5 ] && { echo "launchctl bootstrap thất bại. Thử lại: ./install-service.sh"; exit 1; }
  sleep 2
done

for _ in $(seq 1 30); do
  if curl -fs -o /dev/null "http://localhost:$PORT/"; then
    echo "Đã cài service. App chạy tại http://localhost:$PORT (tự bật khi đăng nhập)."
    echo "Log: $ROOT/logs/service.log · Gỡ: ./uninstall-service.sh"
    exit 0
  fi
  sleep 1
done
echo "Service đã cài nhưng app chưa trả lời sau 30 giây. Xem log: $ROOT/logs/service.log"; exit 1

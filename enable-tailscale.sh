#!/usr/bin/env bash
# Share the app with your own tailnet at https://<this-mac>.<tailnet>.ts.net (not the public internet).
set -euo pipefail
cd "$(dirname "$0")"
command -v tailscale >/dev/null || { echo "Chưa cài Tailscale: https://tailscale.com/download"; exit 1; }
HOST=$(tailscale status --json | python3 -c 'import sys, json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
[ -n "$HOST" ] || { echo "Tailscale chưa đăng nhập."; exit 1; }

# Re-install the service so the app accepts requests for $HOST.
./install-service.sh >/dev/null || { echo "Cài lại service thất bại:"; ./install-service.sh; exit 1; }
tailscale serve --bg 8765 >/dev/null

if curl -fs -o /dev/null --max-time 15 "https://$HOST/"; then
  echo "Đã bật. Mở trên mọi thiết bị trong tailnet: https://$HOST"
else
  echo "Đã cấu hình https://$HOST nhưng chưa truy cập thử được. Xem: tailscale serve status"
fi

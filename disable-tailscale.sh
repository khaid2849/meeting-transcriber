#!/usr/bin/env bash
# Stop sharing the app over Tailscale. The app keeps running locally.
set -euo pipefail
tailscale serve --https=443 off
echo "Đã tắt chia sẻ qua Tailscale. App vẫn chạy tại http://localhost:8765"

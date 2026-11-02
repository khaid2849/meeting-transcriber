#!/usr/bin/env bash
# Stop the LaunchAgent and remove it from login items. Meetings and models are kept.
set -euo pipefail
LABEL=local.meeting-transcriber
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Đã gỡ service. Dữ liệu trong meetings/ vẫn còn nguyên."

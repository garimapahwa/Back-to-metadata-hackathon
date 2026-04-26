#!/usr/bin/env bash
set -euo pipefail

pkill -f "uvicorn server:app" 2>/dev/null || true
pkill -f "python -m slack_bot.bot" 2>/dev/null || true

echo "Stopped backend and Slack bot processes."

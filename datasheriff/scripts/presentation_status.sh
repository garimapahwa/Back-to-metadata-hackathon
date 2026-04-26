#!/usr/bin/env bash
set -euo pipefail

echo "=== DataSheriff Presentation Status ==="
echo "Backend process count: $(ps aux | grep "uvicorn server:app" | grep -v grep | wc -l | tr -d ' ')"
echo "Bot process count: $(ps aux | grep "python -m slack_bot.bot" | grep -v grep | wc -l | tr -d ' ')"

echo
echo "Backend health:"
curl -sS http://127.0.0.1:8000/health || echo "Backend health check failed"

echo
echo "--- Last backend logs ---"
tail -n 20 /tmp/datasheriff-backend.log 2>/dev/null || echo "No backend log yet"

echo
echo "--- Last bot logs ---"
tail -n 20 /tmp/datasheriff-bot.log 2>/dev/null || echo "No bot log yet"

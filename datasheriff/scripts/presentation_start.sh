#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d .venv ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate

echo "[1/4] Stopping old processes"
pkill -f "uvicorn server:app" 2>/dev/null || true
pkill -f "python -m slack_bot.bot" 2>/dev/null || true

BACKEND_LOG="/tmp/datasheriff-backend.log"
BOT_LOG="/tmp/datasheriff-bot.log"

echo "[2/4] Starting backend"
nohup uvicorn server:app --host 0.0.0.0 --port 8000 </dev/null > "$BACKEND_LOG" 2>&1 &

# Wait briefly for backend health.
for _ in {1..15}; do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "Backend failed to become healthy. Tail:"
  tail -n 40 "$BACKEND_LOG" || true
  exit 1
fi

echo "[3/4] Starting Slack bot"
nohup python -u -m slack_bot.bot </dev/null > "$BOT_LOG" 2>&1 &
sleep 2

echo "[4/4] Status"
BACKEND_COUNT="$(pgrep -fc "uvicorn server:app" || true)"
BOT_COUNT="$(pgrep -fc "python -m slack_bot.bot" || true)"

echo "Backend processes: $BACKEND_COUNT"
echo "Bot processes: $BOT_COUNT"
echo "Backend log: $BACKEND_LOG"
echo "Bot log: $BOT_LOG"

if [[ "$BOT_COUNT" -lt 1 ]]; then
  echo "Slack bot failed to stay up. Last bot logs:"
  tail -n 40 "$BOT_LOG" || true
  exit 1
fi

echo "Presentation stack is ready."

#!/bin/bash
# Claude Code Web Manager - Startup Script
#
# Usage:
#   ./start.sh              # Start on default port 5100
#   ./start.sh 8080         # Start on custom port
#   ./start.sh --dev        # Start with hot reload
#
# Listens on 127.0.0.1 only. To open it from a phone / another machine:
#   JARVIS_HOST=0.0.0.0 ./start.sh
# Other devices then need the access token (data/access_token, or the header's
# phone button on this machine). Requests from this machine need none.

set -e

# Agents started from here (claude / codex / agy) need the same proxy and PATH as
# your terminal; launched from launchd or an app they would otherwise get 403s.
if [ -f "$HOME/.shell_env" ]; then
  set +e
  . "$HOME/.shell_env"
  set -e
fi

PORT=${1:-5100}
HOST=${JARVIS_HOST:-127.0.0.1}
DEV_MODE=false

if [ "$1" = "--dev" ]; then
  DEV_MODE=true
  PORT=${2:-5100}
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure data directories exist
mkdir -p data worktrees logs

# Initialize dev-tasks.json if missing
if [ ! -f data/dev-tasks.json ]; then
  echo '{"next_id": 1, "tasks": []}' > data/dev-tasks.json
fi

# Check Python dependencies
if ! python3 -c "import fastapi" 2>/dev/null; then
  echo "Installing dependencies..."
  pip3 install -r requirements.txt
fi

echo ""
echo "  ╔═══════════════════════════════════════════╗"
echo "  ║   Jarvis                                  ║"
echo "  ║   http://localhost:$PORT                   ║"
echo "  ╚═══════════════════════════════════════════╝"
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  echo "  Listening on $HOST: other devices need the token in data/access_token"
fi
echo ""

if [ "$DEV_MODE" = true ]; then
  # Only watch static/ for reload — Python file changes (by worker tasks
  # or manual edits) won't restart the server and kill running workers
  # --loop asyncio: required because uvloop breaks subprocess stdin pipes
  # and loop.call_soon_threadsafe used by worker's reader thread
  python3 -m uvicorn server:app --host "$HOST" --port "$PORT" --reload --reload-dir static --reload-exclude "$(pwd)/.claude" --loop asyncio
else
  python3 -m uvicorn server:app --host "$HOST" --port "$PORT" --loop asyncio
fi

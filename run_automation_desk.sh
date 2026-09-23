#!/usr/bin/env bash
# CLAUDE> start the Automation desk: packages, GUI build when its source changed, the app, then the browser.
# Usage: ./run_automation_desk.sh [--no-browser]     Ctrl+C stops the app.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
PORT=$(grep -E '^port *=' automation_desk.toml | grep -oE '[0-9]+')
URL="http://127.0.0.1:${PORT}"
LOG=data/server.log
OPEN_BROWSER=1
[[ "${1:-}" == "--no-browser" ]] && OPEN_BROWSER=0

open_browser() {
    [[ $OPEN_BROWSER == 1 ]] && xdg-open "$URL" >/dev/null 2>&1 &
    return 0
}

if curl -sf "$URL/api/version" >/dev/null 2>&1; then
    echo "The Automation desk is already running at $URL"
    open_browser
    exit 0
fi

for tool in uv npm curl; do
    command -v "$tool" >/dev/null || { echo "Missing '$tool': install it first." >&2; exit 1; }
done

echo 'Checking Python packages...'
uv sync --quiet

INDEX=src/automation_desk/static/index.html
if [[ ! -d frontend/node_modules ]]; then
    echo 'Installing GUI packages (first run)...'
    (cd frontend && npm install --silent)
fi
if [[ ! -f $INDEX ]] || [[ -n $(find frontend/src frontend/index.html frontend/vite.config.ts -newer "$INDEX" -print -quit) ]]; then
    echo 'Building the GUI...'
    (cd frontend && npm run build --silent >/dev/null)
fi

mkdir -p data
echo "Starting the app (log: $LOG)..."
uv run automation-desk >>"$LOG" 2>&1 &
APP=$!
trap 'echo; echo "Stopping the Automation desk."; kill $APP 2>/dev/null; wait $APP 2>/dev/null; exit 0' INT TERM

for _ in $(seq 1 40); do
    if curl -sf "$URL/api/version" >/dev/null 2>&1; then
        echo "The Automation desk is running at $URL  (Ctrl+C to stop)"
        open_browser
        wait $APP
        exit $?
    fi
    if ! kill -0 $APP 2>/dev/null; then
        echo "The app stopped while starting. Last lines of $LOG:" >&2
        tail -n 20 "$LOG" >&2
        exit 1
    fi
    sleep 0.5
done
echo "The app did not answer within 20 seconds. Last lines of $LOG:" >&2
tail -n 20 "$LOG" >&2
kill $APP 2>/dev/null
exit 1

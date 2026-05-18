#!/usr/bin/env bash
# Start the heatmap server in the background.
#
# - Binds to 127.0.0.1:8000 only (no public exposure).
# - Writes PID to .server.pid and logs to logs/server.log.
# - Detaches from the terminal so you can `exit` from SSH and the server
#   keeps running. Stop it later with `deploy/stop.sh`.
# - Refuses to start if a previous instance is already running (checks the
#   PID file).
#
# Usage:
#   deploy/start.sh             # uses default port 8000
#   deploy/start.sh 8080        # uses port 8080

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PORT="${1:-8000}"
HOST="127.0.0.1"
PID_FILE="${PROJECT_ROOT}/.server.pid"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/server.log"
VENV_PY="${PROJECT_ROOT}/.venv/bin/python"

# --- preflight ---------------------------------------------------------------

if [[ ! -x "${VENV_PY}" ]]; then
    echo "[start] virtual environment not found at ${VENV_PY}"
    echo "[start] run deploy/install.sh first."
    exit 1
fi

if [[ -f "${PID_FILE}" ]]; then
    EXISTING_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
        echo "[start] server already running (pid ${EXISTING_PID})."
        echo "[start] stop it first with deploy/stop.sh, or use deploy/status.sh to inspect."
        exit 1
    fi
    # Stale PID file (process died) — clean it up before continuing.
    rm -f "${PID_FILE}"
fi

mkdir -p "${LOG_DIR}"

# --- launch ------------------------------------------------------------------

echo "[start] launching uvicorn on ${HOST}:${PORT}"
echo "[start] logs -> ${LOG_FILE}"

# `nohup` keeps the process alive after the SSH session ends; `&` puts it in
# the background; `setsid` gives it its own session so it does not receive
# SIGHUP. stdout/stderr go to the log file.
setsid nohup "${VENV_PY}" -m uvicorn app.main:app \
    --host "${HOST}" --port "${PORT}" \
    >>"${LOG_FILE}" 2>&1 < /dev/null &
SERVER_PID=$!

# Give uvicorn a moment to bind; if it dies, surface that immediately.
sleep 1
if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
    echo "[start] uvicorn failed to start. Last lines from ${LOG_FILE}:"
    echo "----------------------------------------------------------------"
    tail -n 20 "${LOG_FILE}" || true
    echo "----------------------------------------------------------------"
    exit 1
fi

echo "${SERVER_PID}" > "${PID_FILE}"

echo "[start] server is running (pid ${SERVER_PID})"
echo
echo "[start] To open the UI from your laptop, run this on your LAPTOP"
echo "[start] (NOT on the server), keeping the SSH session alive:"
echo
echo "    ssh -N -L 8000:127.0.0.1:${PORT} <user>@<your-vps>"
echo
echo "[start] Then open http://localhost:8000 in your browser."
echo "[start] Stop the server with: deploy/stop.sh"
echo "[start] Tail logs with:       tail -f ${LOG_FILE}"

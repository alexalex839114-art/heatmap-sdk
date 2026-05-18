#!/usr/bin/env bash
# Stop the heatmap server started by deploy/start.sh.
#
# Sends SIGTERM (graceful), waits up to ~5 seconds, then SIGKILL if still alive.
# Removes the PID file. Safe to run when nothing is running.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"
PID_FILE="${PROJECT_ROOT}/.server.pid"

if [[ ! -f "${PID_FILE}" ]]; then
    echo "[stop] no PID file at ${PID_FILE} — server is not running."
    exit 0
fi

PID="$(cat "${PID_FILE}" 2>/dev/null || true)"

if [[ -z "${PID}" ]] || ! kill -0 "${PID}" 2>/dev/null; then
    echo "[stop] PID ${PID:-<empty>} is not running. Removing stale PID file."
    rm -f "${PID_FILE}"
    exit 0
fi

echo "[stop] sending SIGTERM to pid ${PID}"
kill "${PID}"

# Wait up to 5 seconds for graceful shutdown.
for _ in $(seq 1 10); do
    if ! kill -0 "${PID}" 2>/dev/null; then
        break
    fi
    sleep 0.5
done

if kill -0 "${PID}" 2>/dev/null; then
    echo "[stop] process still alive after 5s, sending SIGKILL"
    kill -9 "${PID}" || true
    sleep 0.5
fi

if kill -0 "${PID}" 2>/dev/null; then
    echo "[stop] FAILED to kill pid ${PID}. Inspect manually with 'ps -p ${PID}'."
    exit 1
fi

rm -f "${PID_FILE}"
echo "[stop] server stopped."

#!/usr/bin/env bash
# Show whether the server is running and on which port.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"
PID_FILE="${PROJECT_ROOT}/.server.pid"
LOG_FILE="${PROJECT_ROOT}/logs/server.log"

if [[ ! -f "${PID_FILE}" ]]; then
    echo "[status] not running (no PID file)"
    exit 0
fi

PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
if [[ -z "${PID}" ]] || ! kill -0 "${PID}" 2>/dev/null; then
    echo "[status] not running (stale PID file: ${PID:-<empty>})"
    exit 0
fi

echo "[status] running (pid ${PID})"

# Try to surface the port from `ss` / `netstat` (best-effort, ok to fail).
if command -v ss >/dev/null 2>&1; then
    LISTEN_LINE="$(ss -ltnp 2>/dev/null | grep -E "pid=${PID}(\b|,)" || true)"
    if [[ -n "${LISTEN_LINE}" ]]; then
        echo "[status] listening: ${LISTEN_LINE}"
    fi
fi

if [[ -f "${LOG_FILE}" ]]; then
    echo "[status] log: ${LOG_FILE}"
    echo "[status] last 5 log lines:"
    tail -n 5 "${LOG_FILE}" | sed 's/^/    /'
fi

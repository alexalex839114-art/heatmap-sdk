#!/usr/bin/env bash
# One-shot install for Ubuntu 24.04 LTS.
#
# Idempotent: safe to re-run.
# - Installs Python 3.12 + venv tooling via apt (if missing).
# - Creates .venv/ in the project root.
# - Installs Python deps from requirements.txt.
# - Copies .env.example to .env if .env does not exist yet.
#
# Run from anywhere — it always operates on the project that contains this
# script.

set -euo pipefail

# Always work from the project root (parent of deploy/).
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${DEPLOY_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "[install] project root: ${PROJECT_ROOT}"

# --- 1. System packages -------------------------------------------------------

# Use sudo when not running as root; many VPS images run as root with no sudo
# installed at all, so this avoids a hard dependency on sudo.
if [[ ${EUID} -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

NEEDED_APT_PACKAGES=(python3 python3-venv python3-pip curl ca-certificates)
MISSING_PACKAGES=()
for pkg in "${NEEDED_APT_PACKAGES[@]}"; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
        MISSING_PACKAGES+=("${pkg}")
    fi
done

if [[ ${#MISSING_PACKAGES[@]} -gt 0 ]]; then
    echo "[install] installing missing apt packages: ${MISSING_PACKAGES[*]}"
    ${SUDO} apt-get update
    ${SUDO} apt-get install -y "${MISSING_PACKAGES[@]}"
else
    echo "[install] all required apt packages are already present"
fi

# --- 2. Python venv -----------------------------------------------------------

VENV_DIR="${PROJECT_ROOT}/.venv"
VENV_PY="${VENV_DIR}/bin/python"

if [[ ! -x "${VENV_PY}" ]]; then
    echo "[install] creating virtual environment at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
fi

echo "[install] upgrading pip"
"${VENV_PY}" -m pip install --upgrade pip >/dev/null

echo "[install] installing requirements.txt"
"${VENV_PY}" -m pip install -r "${PROJECT_ROOT}/requirements.txt"

# --- 3. .env scaffold ---------------------------------------------------------

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
    if [[ -f "${PROJECT_ROOT}/.env.example" ]]; then
        cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
        echo "[install] copied .env.example -> .env"
        echo "[install] EDIT .env to set BINANCE_API_KEY / BINANCE_API_SECRET before starting"
    else
        echo "[install] WARNING: no .env.example found, skipping .env scaffold"
    fi
else
    echo "[install] .env already exists, leaving it untouched"
fi

echo
echo "[install] done."
echo "[install] next steps:"
echo "[install]   1. nano ${PROJECT_ROOT}/.env      # fill in BINANCE_API_KEY / BINANCE_API_SECRET"
echo "[install]   2. ${DEPLOY_DIR}/start.sh        # start the server in background"

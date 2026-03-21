#!/usr/bin/env bash
# AirBridge — Wireless File Transfer launcher

set -e

echo "============================================"
echo "  AirBridge — Wireless File Transfer"
echo "============================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Detect python command
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[ERROR] Python not found. Please install Python 3.10+"
    exit 1
fi

PYVER=$($PYTHON --version 2>&1)
echo "[INFO] $PYVER detected"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "[INFO] Creating virtual environment..."
    $PYTHON -m venv venv
    echo "[OK] Virtual environment created"
fi

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
echo "[INFO] Installing dependencies..."
pip install -r requirements.txt -q
echo "[OK] Dependencies ready"

echo ""
echo "[START] Launching AirBridge..."
echo "[INFO] Press Ctrl+C to stop"
echo ""

python -m airbridge

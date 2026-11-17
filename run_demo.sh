#!/usr/bin/env bash
# Start Pécule with offline demo data (mock prices, no network needed for the data).
# Works on macOS and Linux:   ./run_demo.sh      (PORT=8001 ./run_demo.sh to change the port)
#
# It creates .venv on the first run, installs the requirements, creates the demo
# database data/demo.db if missing (your real data/portfolio.db is never touched),
# then starts the server on 127.0.0.1.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# The code needs Python 3.11 or newer. macOS ships an older python3, so look for a newer one.
find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" > /dev/null 2>&1 \
      && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if [ ! -d .venv ]; then
  PYTHON="$(find_python)" || {
    echo "Python 3.11 or newer is required. On macOS: brew install python@3.12" >&2
    exit 1
  }
  echo "Creating .venv with $PYTHON ($("$PYTHON" --version))"
  "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet -r requirements.txt

export DATABASE_URL="sqlite:///./data/demo.db"
export PRICE_PROVIDER="mock"

if [ ! -f data/demo.db ]; then
  python -m app.seed_demo
fi

echo
echo "Pécule is starting: open http://127.0.0.1:${PORT}   (Ctrl+C to stop)"
echo
exec uvicorn app.main:app --host 127.0.0.1 --port "$PORT"

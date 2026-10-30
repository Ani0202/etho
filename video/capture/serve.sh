#!/bin/bash
# Local read-only Etho app for screen capture. No model calls: read-only mode refuses all new work.
# Usage: PYTHON=/path/to/python SHOWCASE=/path/to/data/showcase video/capture/serve.sh
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export ETHO_APP_READ_ONLY=1
export ETHO_APP_DATA_DIR="$ROOT/data/video/tmp/appdata"
export ETHO_APP_SHOWCASE_DIR="${SHOWCASE:-$ROOT/data/showcase}"
mkdir -p "$ETHO_APP_DATA_DIR"
cd "$ROOT"
exec "${PYTHON:-.venv/bin/python}" -m etho.app.server --host 127.0.0.1 --port 8795

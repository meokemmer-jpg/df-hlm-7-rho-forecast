#!/usr/bin/env bash
set -euo pipefail

LOCK_DIR="${DF_HLM_7_LOCK_DIR:-/tmp/df-hlm-7.lock}"
BASE_DIR="${DF_HLM_7_BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STOP_FLAG="${DF_HLM_7_STOP_FLAG:-/tmp/df-hlm-7.stop}"

if [[ -f "$STOP_FLAG" ]]; then
  echo "DF-HLM-7 stopped by $STOP_FLAG" >&2
  exit 2
fi

if pgrep -f "src/rho_forecast.py" >/dev/null 2>&1; then
  echo "DF-HLM-7 already running (pgrep guard)" >&2
  exit 3
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "DF-HLM-7 already running (mutex $LOCK_DIR)" >&2
  exit 4
fi

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$BASE_DIR"
export PYTHONPATH="$BASE_DIR:${PYTHONPATH:-}"
python3 src/rho_forecast.py --base-dir "$BASE_DIR" "$@"

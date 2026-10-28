#!/bin/sh
# Usage: ./play.sh        run all 9 stages
#        ./play.sh 7      run a single stage (1-9)
set -e
cd "$(dirname "$0")"
command -v brew >/dev/null && { brew list libusb >/dev/null 2>&1 || brew install libusb; }
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
export DYLD_LIBRARY_PATH="$(brew --prefix 2>/dev/null || echo /opt/homebrew)/lib"
exec .venv/bin/python game.py "$@"

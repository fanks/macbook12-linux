#!/bin/sh
# Build as your normal user. See --help and docs/audio.md.
set -eu
exec python3 "$(dirname -- "$0")/lib/kernel.py" audio build "$@"

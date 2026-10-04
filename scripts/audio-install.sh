#!/bin/sh
# Install only on disk; never hot-swap the audio codec.
set -eu
exec python3 "$(dirname -- "$0")/lib/kernel.py" audio install "$@"

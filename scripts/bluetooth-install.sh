#!/bin/sh
# Install for a later boot, without replacing a running driver.
set -eu
exec python3 "$(dirname -- "$0")/lib/kernel.py" bluetooth install "$@"

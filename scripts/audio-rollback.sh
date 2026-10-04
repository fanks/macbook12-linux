#!/bin/sh
# Restore the next-boot module selection; retain software volume.
set -eu
exec python3 "$(dirname -- "$0")/lib/kernel.py" audio rollback "$@"

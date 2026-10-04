#!/bin/sh
# Restore the original driver selection for a later boot.
set -eu
exec python3 "$(dirname -- "$0")/lib/kernel.py" bluetooth rollback "$@"

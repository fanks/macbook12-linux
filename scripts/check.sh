#!/bin/bash
# Software checks only; no installation, hardware access or desktop changes.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
python3 -m compileall -q scripts trackpad tests
python3 -m unittest discover -s trackpad -p 'test_*.py' -q
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 tests/check_repository.py
mkdir -p build
cc -shared -fPIC -O2 -Wall -Wextra -Werror -Wl,-z,relro,-z,now \
    -o build/libmacbook-trackpad.so trackpad/curve.c -ldl -pthread
python3 tests/check_curve_math.py
while IFS= read -r -d '' script; do
    bash -n "$script"
    shellcheck "$script"
done < <(find scripts -name '*.sh' -print0)
shellcheck trackpad/launch-gnome-shell
while IFS= read -r -d '' extension; do
    node --input-type=module --check < "$extension"
done < <(find keyboard -name '*.js' -print0)
while IFS= read -r -d '' schema; do
    glib-compile-schemas --strict --dry-run "$(dirname -- "$schema")"
done < <(find keyboard -name '*.gschema.xml' -print0)
mkdir -p build/ui
GSK_RENDERER=cairo dbus-run-session -- xvfb-run -a python3 trackpad/qa.py build/ui
echo 'All software checks passed. Hardware validation is separate.'

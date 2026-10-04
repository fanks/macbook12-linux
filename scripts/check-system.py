#!/usr/bin/env python3
"""Print a short, read-only compatibility summary without serial numbers."""
from pathlib import Path
import platform
import subprocess


def package(name):
    result = subprocess.run(['dpkg-query', '-W', '-f=${Version}', name], text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else 'not installed'


def main():
    if platform.system() != 'Linux' or not Path('/etc/debian_version').exists():
        raise SystemExit('Run this on the MacBook after installing Debian.')
    model_path = Path('/sys/class/dmi/id/product_name')
    model = model_path.read_text().strip() if model_path.exists() else 'unknown'
    print('Model:     ', model)
    print('Debian:    ', Path('/etc/debian_version').read_text().strip())
    print('Kernel:    ', platform.release())
    print('GNOME:     ', package('gnome-shell'))
    print('libinput:  ', package('libinput10'))
    print('keyd:      ', package('keyd'))
    print('WirePlumber:', package('wireplumber'))
    print('\nSupported hardware: MacBook10,1 (2017). Each installer checks its own prerequisites.')
    if model != 'MacBook10,1':
        print('This model is not yet supported by these installers.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

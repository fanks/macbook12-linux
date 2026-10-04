#!/usr/bin/env python3
"""Install, update or remove Trackpad Curve for the current GNOME user."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'trackpad'
DATA = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
BASE = DATA / 'macbook-trackpad'
DROPIN = CONFIG / 'systemd/user/org.gnome.Shell@wayland.service.d/80-macbook-trackpad.conf'
COMMAND = Path.home() / '.local/bin/trackpad-curve'
DESKTOP = DATA / 'applications/local.macbook.TrackpadCurve.desktop'
SCHEMA = 'org.gnome.desktop.peripherals.touchpad'


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.trackpad-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_session():
    if os.getuid() == 0 or os.environ.get('SUDO_USER'):
        raise RuntimeError('Run this from your GNOME Terminal without sudo.')
    for path in (DATA, CONFIG, BASE, COMMAND, DESKTOP):
        # ld.so --preload cannot quote spaces or colons in a library pathname.
        if not re.fullmatch(r'/[A-Za-z0-9_./@+\-]+', str(path)):
            raise RuntimeError('Home/XDG paths must be absolute and contain no spaces or shell characters.')
    if os.environ.get('XDG_SESSION_TYPE') != 'wayland' or not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        raise RuntimeError('Open Terminal in your GNOME Wayland desktop and run again.')
    run('gsettings', 'get', SCHEMA, 'accel-profile')


def check_platform():
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'debian' or release.get('VERSION_ID') != '13' or platform.machine() != 'x86_64':
        raise RuntimeError('This version supports Debian 13 amd64 only.')
    if Path('/sys/class/dmi/id/product_name').read_text().strip() != 'MacBook10,1':
        raise RuntimeError('Only the 2017 12-inch MacBook (MacBook10,1) has been verified.')
    if not run('dpkg-query', '-W', '-f=${Version}', 'gnome-shell').startswith('48.'):
        raise RuntimeError('GNOME Shell 48 is required. Do not bypass this version check.')
    if not run('dpkg-query', '-W', '-f=${Version}', 'libinput10:amd64').startswith('1.28.1-'):
        raise RuntimeError('This curve support was verified with libinput 1.28.1 only.')
    if not any(p.read_text().strip() == 'Apple SPI Touchpad' for p in Path('/sys/class/input').glob('event*/device/name')):
        raise RuntimeError('The built-in Apple SPI Touchpad was not found.')
    subprocess.run([sys.executable, '-c', "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_foreign('cairo'); from gi.repository import Gtk, Adw"], check=True)
    if not Path('/usr/include/libinput.h').exists() or not shutil.which('cc'):
        raise RuntimeError('Install the build and GTK packages listed in docs/trackpad.md first.')


def read_record():
    path = BASE / 'install.json'
    return json.loads(path.read_text()) if path.exists() else None


def verify_owned(record, allow_missing=()):
    # Do not clobber manual edits during an update or uninstall.
    for name, expected in record['files'].items():
        path = Path(name)
        if path in allow_missing and not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise RuntimeError(f'Installed file changed or missing; inspect it first: {path}')


def install():
    check_platform()
    old = read_record()
    if (BASE / 'disabled').exists() and old and not old.get('uninstalled'):
        raise RuntimeError('Custom support was disabled. Run uninstall to finish removal before reinstalling.')
    if old and old.get('phase') == 'removing':
        raise RuntimeError('Removal was interrupted. Run uninstall again to finish it first.')
    if old and old.get('uninstalled'):
        raise RuntimeError(f'Previous settings were kept in {BASE}. Move that folder aside before a fresh install.')
    if old:
        verify_owned(old)
    elif BASE.exists() or any(p.exists() or p.is_symlink() for p in (DROPIN, COMMAND, DESKTOP)):
        raise RuntimeError('A trackpad setup already exists. Back it up and remove it with its original uninstaller first.')
    dropins = run('systemctl', '--user', 'show', 'org.gnome.Shell@wayland.service', '-p', 'DropInPaths', '--value').split()
    if any(path != str(DROPIN) for path in dropins):
        raise RuntimeError('GNOME already has another service override; review it before installing this one.')
    original = {key: run('gsettings', 'get', SCHEMA, key) for key in ('accel-profile', 'speed')}
    with tempfile.TemporaryDirectory(prefix='macbook-trackpad-build-') as temporary:
        library = Path(temporary) / 'libmacbook-trackpad.so'
        subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-Wl,-z,relro,-z,now', '-o', str(library), str(SOURCE / 'curve.c'), '-ldl', '-pthread'], check=True)
        files = {
            BASE / 'libmacbook-trackpad.so': (library.read_bytes(), 0o644),
            BASE / 'curve.c': ((SOURCE / 'curve.c').read_bytes(), 0o644),
            BASE / 'launch-gnome-shell': ((SOURCE / 'launch-gnome-shell').read_bytes(), 0o700),
            BASE / 'support.json': ((json.dumps({'curve_version': 2, 'sha256': digest(library.read_bytes())})+'\n').encode(), 0o600),
            DROPIN: (f'[Service]\nExecStart=\nExecStart={BASE}/launch-gnome-shell\n'.encode(), 0o644),
            COMMAND: (f'#!/bin/sh\nexport MACBOOK_TRACKPAD_DIR="{BASE}"\nexec /usr/bin/python3 "{BASE}/tuner/app.py" "$@"\n'.encode(), 0o755),
            DESKTOP: (f'[Desktop Entry]\nType=Application\nName=Trackpad Curve\nComment=Fine-tune trackpad acceleration\nExec={COMMAND}\nIcon=input-touchpad-symbolic\nTerminal=false\nCategories=Settings;HardwareSettings;\nStartupNotify=true\n'.encode(), 0o644),
        }
        for name in ('app.py', 'backend.py'):
            files[BASE / 'tuner' / name] = ((SOURCE / name).read_bytes(), 0o644)
        BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
        sys.path.insert(0, str(SOURCE))
        from backend import Backend, Curve
        backend = Backend(BASE)
        with backend._lock():
            if old:
                verify_owned(old)
            prior = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None for path in files}
            backup = None
            created = []
            try:
                backup = Path(tempfile.mkdtemp(prefix='upgrade-' if old else 'install-', dir=BASE))
                atomic(backup / 'before.json', (json.dumps({str(p): None if v is None else {'hex': v[0].hex(), 'mode': v[1]} for p, v in prior.items()}, indent=2)+'\n').encode())
                if not old:
                    for name, data in [('curve.conf', b'0.5 1.92\n'), ('medium.conf', b'1.25\n'),
                                       ('tuner-baseline.json', b'{"version":1,"slow":0.5,"medium":1.25,"fast":1.92}\n')]:
                        atomic(BASE / name, data)
                        created.append(BASE / name)
                else:
                    backend.read().curve.validate()
                for path, (data, mode) in files.items():
                    atomic(path, data, mode)  # New inode: never truncate a loaded .so.
                subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
                if not old:
                    subprocess.run(['gsettings', 'set', SCHEMA, 'accel-profile', 'adaptive'], check=True)
                record = {'version': 1, 'files': {str(p): digest(data) for p, (data, _) in files.items()},
                          'original_settings': old['original_settings'] if old else original,
                          'backup': str(backup), 'uninstalled': False}
                atomic(BASE / 'install.json', (json.dumps(record, indent=2)+'\n').encode())
            except BaseException:
                for path, previous in prior.items():
                    if previous is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic(path, *previous)
                for path in created:
                    path.unlink(missing_ok=True)
                if not old:
                    subprocess.run(['gsettings', 'set', SCHEMA, 'accel-profile', original['accel-profile']], check=False)
                subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
                if not old:
                    # Remove only this failed attempt's known files/empty dirs.
                    if backup is not None:
                        (backup / 'before.json').unlink(missing_ok=True)
                        backup.rmdir()
                    if (BASE / 'tuner').exists():
                        (BASE / 'tuner').rmdir()
                    (BASE / 'tuner.lock').unlink()
                    BASE.rmdir()
                raise
    print('Installed. Save your work, sign out and back in once, then run:')
    print(f'  {COMMAND}')
    print('Future Apply changes take effect immediately. Re-running install updates the app and keeps your curve.')


def uninstall():
    record = read_record()
    if record is None or record.get('uninstalled'):
        print('Trackpad Curve is not installed by this installer.')
        return
    sys.path.insert(0, str(SOURCE))
    from backend import Backend
    with Backend(BASE)._lock():
        record = read_record()
        retry = record.get('phase') == 'removing'
        recovery = (DROPIN,) if (BASE / 'disabled').exists() else ()
        verify_owned(record, (DROPIN, COMMAND, DESKTOP) if retry else recovery)
        curve = BASE / 'curve.conf'
        saved = BASE / 'curve.conf.uninstalled'
        if saved.exists() and not retry:
            raise RuntimeError(f'A previous curve backup exists: {saved}')
        if curve.exists() and saved.exists():
            raise RuntimeError('A new curve appeared during removal. Preserve it before retrying.')
        if not retry:
            record['phase'] = 'removing'
            record['profile_at_removal'] = run('gsettings', 'get', SCHEMA, 'accel-profile')
            atomic(BASE / 'install.json', (json.dumps(record, indent=2)+'\n').encode())
        atomic(BASE / 'disabled', b'Uninstalled; settings retained for reference.\n')
        if curve.exists():
            curve.rename(saved)
        for path in (DROPIN, COMMAND, DESKTOP):
            path.unlink(missing_ok=True)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        current = run('gsettings', 'get', SCHEMA, 'accel-profile')
        restore = record['original_settings']['accel-profile']
        if record['profile_at_removal'] == "'adaptive'" and current in ("'adaptive'", "'flat'", restore):
            for value in ('flat', restore):
                subprocess.run(['gsettings', 'set', SCHEMA, 'accel-profile', value], check=True)
        record['uninstalled'] = True
        record['phase'] = 'removed'
        atomic(BASE / 'install.json', (json.dumps(record, indent=2)+'\n').encode())
    print(f'Removed the launcher and GNOME override. Saved settings remain in {BASE}.')
    print('Sign out and back in to finish unloading the custom support.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install', 'uninstall'))
    args = parser.parse_args()
    try:
        check_session()
        if args.action == 'check':
            check_platform()
            print('Supported MacBook, desktop and dependencies found. No settings changed.')
        elif args.action == 'install':
            install()
        else:
            uninstall()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'Trackpad setup: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

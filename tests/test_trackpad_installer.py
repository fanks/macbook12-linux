"""Installer lifecycle tests; no real desktop, compiler or home directory."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('trackpad_installer', ROOT / 'scripts/trackpad.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        paths = {
            'BASE': self.root / 'data/macbook-trackpad',
            'DROPIN': self.root / 'config/systemd/user/org.gnome.Shell@wayland.service.d/80-macbook-trackpad.conf',
            'COMMAND': self.root / 'bin/trackpad-curve',
            'DESKTOP': self.root / 'data/applications/local.macbook.TrackpadCurve.desktop',
        }
        self.paths = paths
        self.profile = "'default'"
        self.speed = '0.42'
        self.commands = []
        self.fail_write = False
        for name, path in paths.items():
            context = patch.object(installer, name, path)
            context.start()
            self.addCleanup(context.stop)
        for context in (patch.object(installer, 'check_platform'),
                        patch.object(installer, 'run', side_effect=self.fake_run),
                        patch.object(installer.subprocess, 'run', side_effect=self.command)):
            context.start()
            self.addCleanup(context.stop)

    def fake_run(self, *args):
        if args[:2] == ('gsettings', 'get'):
            return self.profile if args[-1] == 'accel-profile' else self.speed
        if args[0] == 'systemctl':
            return str(self.paths['DROPIN']) if self.paths['DROPIN'].exists() else ''
        raise AssertionError(args)

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args[0] == 'cc':
            Path(args[args.index('-o') + 1]).write_bytes(b'isolated fake compiled library')
        elif args[:2] == ['gsettings', 'set']:
            self.profile = "'" + args[-1].strip("'") + "'"
        return None

    def test_install_update_uninstall_preserves_curve_and_user_speed(self):
        installer.install()
        base = self.paths['BASE']
        self.assertEqual(self.profile, "'adaptive'")
        self.assertEqual(self.speed, '0.42')
        data = b'0.57 3.1\n'
        (base / 'curve.conf').write_bytes(data)
        (base / 'tuner-undo.json').write_bytes(b'preserve this undo')
        protected = {name: ((base / name).read_bytes(), (base / name).stat())
                     for name in ('curve.conf', 'medium.conf', 'tuner-baseline.json', 'tuner-undo.json')}
        # Represent an owned v2 installation, including its recorded hash.
        support = base / 'support.json'
        previous_support = json.loads(support.read_text())
        previous_support['curve_version'] = 2
        support.write_text(json.dumps(previous_support))
        previous_record = installer.read_record()
        previous_record['files'][str(support)] = installer.digest(support.read_bytes())
        (base / 'install.json').write_text(json.dumps(previous_record))
        previous_inode = (base / 'libmacbook-trackpad.so').stat().st_ino
        self.commands.clear()
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            installer.install()
        self.assertIn('sign out and back in before applying any curve changes', output.getvalue())
        self.assertNotEqual(previous_inode, (base / 'libmacbook-trackpad.so').stat().st_ino)
        for name, (contents, stat) in protected.items():
            with self.subTest(file=name):
                self.assertEqual((base / name).read_bytes(), contents)
                current = (base / name).stat()
                self.assertEqual((current.st_ino, current.st_mtime_ns, current.st_ctime_ns),
                                 (stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns))
        self.assertFalse(any(command[:2] == ['gsettings', 'set'] for command in self.commands))
        self.assertFalse(any('logout' in command or 'reboot' in command for command in self.commands))
        record = json.loads((base / 'support.json').read_text())
        self.assertEqual(record['curve_version'], 3)
        self.assertEqual(record['sha256'], installer.digest((base / 'libmacbook-trackpad.so').read_bytes()))
        installer.verify_owned(installer.read_record())
        self.assertEqual(installer.read_record()['original_settings'], previous_record['original_settings'])
        installer.uninstall()
        self.assertEqual(self.profile, "'default'")
        self.assertEqual(self.speed, '0.42')
        self.assertEqual((base / 'curve.conf.uninstalled').read_bytes(), data)
        self.assertFalse(self.paths['DROPIN'].exists())
        self.assertFalse(self.paths['COMMAND'].exists())
        installer.uninstall()  # Repeating removal is harmless.

    def test_external_profile_and_installed_file_changes_are_preserved(self):
        installer.install()
        source = self.paths['BASE'] / 'tuner/app.py'
        original = source.read_bytes()
        source.write_bytes(b'manual changes')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            installer.install()
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            installer.uninstall()
        self.assertEqual(source.read_bytes(), b'manual changes')
        source.write_bytes(original)
        self.profile = "'flat'"
        installer.uninstall()
        self.assertEqual(self.profile, "'flat'")

    def test_refuse_unmanaged_existing_installation(self):
        self.paths['BASE'].mkdir(parents=True)
        (self.paths['BASE'] / 'curve.conf').write_text('do not replace')
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            installer.install()
        self.assertEqual((self.paths['BASE'] / 'curve.conf').read_text(), 'do not replace')
        self.assertEqual(self.commands, [])

    def test_console_recovery_can_be_followed_by_uninstall(self):
        installer.install()
        (self.paths['BASE'] / 'disabled').touch()
        self.paths['DROPIN'].unlink()
        installer.uninstall()
        self.assertTrue(installer.read_record()['uninstalled'])
        self.assertFalse(self.paths['COMMAND'].exists())

    def test_failed_update_restores_previous_files(self):
        installer.install()
        app = self.paths['BASE'] / 'tuner/app.py'
        before = app.read_bytes()
        real = installer.atomic
        failed = False
        def write(path, data, mode=0o600):
            nonlocal failed
            if path == app and not failed:
                failed = True
                raise OSError('disk full')
            real(path, data, mode)
        with patch.object(installer, 'atomic', side_effect=write):
            with self.assertRaises(OSError):
                installer.install()
        self.assertEqual(app.read_bytes(), before)
        installer.verify_owned(installer.read_record())

    def test_failed_first_install_can_be_retried(self):
        real = installer.atomic
        failed = False
        def write(path, data, mode=0o600):
            nonlocal failed
            if path == self.paths['BASE'] / 'tuner/app.py' and not failed:
                failed = True
                raise OSError('disk full')
            real(path, data, mode)
        with patch.object(installer, 'atomic', side_effect=write):
            with self.assertRaises(OSError):
                installer.install()
        self.assertFalse(self.paths['BASE'].exists())
        installer.install()
        installer.verify_owned(installer.read_record())

    def test_interrupted_uninstall_is_retryable(self):
        installer.install()
        failed = False
        def command(args, **kwargs):
            nonlocal failed
            if args == ['systemctl', '--user', 'daemon-reload'] and not failed:
                failed = True
                raise OSError('temporary bus failure')
            return self.command(args, **kwargs)
        with patch.object(installer.subprocess, 'run', side_effect=command):
            with self.assertRaises(OSError):
                installer.uninstall()
        self.assertEqual(installer.read_record()['phase'], 'removing')
        installer.uninstall()
        self.assertTrue(installer.read_record()['uninstalled'])
        self.assertEqual(self.profile, "'default'")

    def test_failed_first_backup_write_can_be_retried(self):
        real = installer.atomic
        failed = False

        def write(path, data, mode=0o600):
            nonlocal failed
            if path.name == 'before.json' and not failed:
                failed = True
                raise OSError('disk full before first backup')
            real(path, data, mode)

        with patch.object(installer, 'atomic', side_effect=write):
            with self.assertRaises(OSError):
                installer.install()
        self.assertEqual(self.profile, "'default'")
        self.assertFalse(self.paths['BASE'].exists())
        installer.install()
        installer.verify_owned(installer.read_record())

    def test_failed_initial_profile_change_restores_original_and_allows_retry(self):
        failed = False

        def command(args, **kwargs):
            nonlocal failed
            result = self.command(args, **kwargs)
            if args[:2] == ['gsettings', 'set'] and args[-1] == 'adaptive' and not failed:
                failed = True
                # Exercise failure after the desktop accepted the change too.
                raise installer.subprocess.CalledProcessError(1, args)
            return result

        with patch.object(installer.subprocess, 'run', side_effect=command):
            with self.assertRaises(installer.subprocess.CalledProcessError):
                installer.install()
        self.assertEqual(self.profile, "'default'")
        self.assertEqual(self.speed, '0.42')
        self.assertFalse(self.paths['BASE'].exists())
        self.assertFalse(self.paths['DROPIN'].exists())
        self.assertFalse(self.paths['COMMAND'].exists())
        installer.install()
        installer.verify_owned(installer.read_record())

    def test_uninstall_profile_restore_failure_is_retryable(self):
        installer.install()
        original_curve = (self.paths['BASE'] / 'curve.conf').read_bytes()
        failed = False

        def command(args, **kwargs):
            nonlocal failed
            if args[:2] == ['gsettings', 'set'] and args[-1] == "'default'" and not failed:
                failed = True
                raise installer.subprocess.CalledProcessError(1, args)
            return self.command(args, **kwargs)

        with patch.object(installer.subprocess, 'run', side_effect=command):
            with self.assertRaises(installer.subprocess.CalledProcessError):
                installer.uninstall()
        self.assertEqual(self.profile, "'flat'")
        self.assertEqual(installer.read_record()['phase'], 'removing')
        self.assertFalse((self.paths['BASE'] / 'curve.conf').exists())
        self.assertEqual((self.paths['BASE'] / 'curve.conf.uninstalled').read_bytes(), original_curve)
        installer.uninstall()
        self.assertEqual(self.profile, "'default'")
        self.assertEqual(self.speed, '0.42')
        self.assertTrue(installer.read_record()['uninstalled'])


if __name__ == '__main__':
    unittest.main()

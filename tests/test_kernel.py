#!/usr/bin/env python3
"""Isolated regression tests. No root, network, devices or real boot files."""
import argparse
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("kernel_helper", Path(__file__).resolve().parents[1] / "scripts/lib/kernel.py")
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)


class UtilityTests(unittest.TestCase):
    def test_initramfs_names_cover_hyphens_underscores_and_compression(self):
        for component, names in (
            ("audio", ("snd-hda-codec-cirrus.ko", "snd_hda_codec_cirrus.ko.xz", "snd-hda-codec-cirrus.ko.zst")),
            ("bluetooth", ("hci_uart.ko", "hci-uart.ko.xz")),
        ):
            inventory = "\n".join("usr/lib/modules/test/" + n for n in names)
            inventory += "\nother.ko\nhci_uart.ko.backup\nsnd-hda-codec-cirrus.ko/wrong"
            with patch.object(k, "run", return_value=inventory):
                self.assertEqual(len(k.image_members("test", component)), len(names))

    def test_atomic_copy_preserves_source_and_sets_permissions(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "source", Path(folder) / "target"
            source.write_bytes(b"module bytes\x00")
            target.write_bytes(b"previous")
            old_inode = target.stat().st_ino
            k.atomic_copy(source, target, 0o600)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertNotEqual(target.stat().st_ino, old_inode)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["source", "target"])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.release = "6.12.111+deb13-amd64"
        self.filename = "hci_uart.ko"
        self.source = self.root / "build"
        self.source.mkdir()
        self.candidate = self.source / self.filename
        self.candidate.write_bytes(b"candidate")
        self.original = self.root / "stock/hci_uart.ko.xz"
        self.original.parent.mkdir()
        self.original.write_bytes(b"stock")
        self.destination = self.root / "lib/modules" / self.release / "updates/macbook12-linux" / self.filename
        self.destination.parent.mkdir(parents=True)
        self.state = self.root / "var/lib/macbook12-linux/kernel" / self.release / "bluetooth"
        self.state.mkdir(parents=True)
        self.boot = self.root / "boot" / ("initrd.img-" + self.release)
        self.boot.parent.mkdir()
        self.boot.write_bytes(b"previous boot image")
        self.manifest = {"format": 1, "component": "bluetooth", "kernel": self.release,
                         "candidate_sha256": k.digest(self.candidate), "stock_sha256": k.digest(self.original)}
        for folder in (self.source, self.state):
            (folder / "manifest.json").write_text(json.dumps(self.manifest))
        (self.state / self.filename).write_bytes(self.candidate.read_bytes())
        self.args = argparse.Namespace(component="bluetooth", directory=self.source, kernel=self.release,
                                       update_initramfs=True, dry_run=False)
        real_path = Path

        def rooted(value):
            value = real_path(value)
            if value.is_absolute() and not value.is_relative_to(self.root):
                return self.root / str(value).lstrip("/")
            return value

        for name, value in (
            ("Path", rooted), ("platform", lambda *_: None), ("secure_boot", lambda: None),
            ("validate_secure_dir", lambda *_: None),
            ("stock_module", lambda *_: self.original),
            ("selected", lambda *_: self.destination.resolve() if self.destination.exists() else self.original.resolve()),
            ("module_info", lambda p, field: "hci_uart" if field == "name" else "same-vermagic"),
            ("image_members", lambda *_: ["hci_uart.ko"]),
        ):
            self.stack.enter_context(patch.object(k, name, value))
        self.stack.enter_context(patch.object(k.os, "uname", return_value=argparse.Namespace(release=self.release)))
        self.commands = self.stack.enter_context(patch.object(k, "run", return_value=""))
        self.verify = self.stack.enter_context(patch.object(k, "verify_image"))

    def test_repeat_install_requires_complete_record(self):
        self.destination.write_bytes(self.candidate.read_bytes())
        with self.assertRaises(RuntimeError):
            k.install(self.args)
        self.commands.assert_not_called()

    def test_repeat_install_verifies_boot_image(self):
        self.destination.write_bytes(self.candidate.read_bytes())
        (self.state / "COMPLETE").write_text("complete")
        k.install(self.args)
        self.verify.assert_called_once_with(self.release, "bluetooth", self.candidate)
        self.commands.assert_not_called()

    def test_repeat_install_rejects_stale_boot_image(self):
        self.destination.write_bytes(self.candidate.read_bytes())
        (self.state / "COMPLETE").write_text("complete")
        self.verify.side_effect = RuntimeError("unexpected boot module")
        with self.assertRaisesRegex(RuntimeError, "unexpected boot module"):
            k.install(self.args)
        self.assertEqual(self.destination.read_bytes(), b"candidate")

    def test_missing_override_does_not_skip_required_initramfs_permission(self):
        self.args.update_initramfs = False
        with self.assertRaisesRegex(RuntimeError, "requires --update-initramfs"):
            k.rollback(self.args)
        self.commands.assert_not_called()

    def test_missing_override_recovers_original_boot_image(self):
        k.rollback(self.args)
        self.verify.assert_called_once_with(self.release, "bluetooth", self.original)
        self.assertIn(("update-initramfs", "-u", "-k", self.release), [call.args for call in self.commands.call_args_list])
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.state.exists())
        self.assertEqual(len(list(self.state.parent.glob("bluetooth.rolled-back-*"))), 1)

    def test_failed_rebuild_restores_previous_image_and_override(self):
        self.destination.write_bytes(b"candidate")

        def command(*argv, **kwargs):
            if argv[0] == "update-initramfs":
                self.boot.write_bytes(b"partly rebuilt image")
                raise subprocess.CalledProcessError(1, argv)
            return ""

        self.commands.side_effect = command
        with self.assertRaises(subprocess.CalledProcessError):
            k.rollback(self.args)
        self.assertEqual(self.boot.read_bytes(), b"previous boot image")
        self.assertEqual(self.destination.read_bytes(), b"candidate")
        self.assertTrue(self.state.exists())

    def test_absent_state_still_verifies_existing_boot_module(self):
        for path in self.state.iterdir():
            path.unlink()
        self.state.rmdir()
        self.verify.side_effect = RuntimeError("unexpected boot module")
        with self.assertRaisesRegex(RuntimeError, "unexpected boot module"):
            k.rollback(self.args)
        self.commands.assert_not_called()


if __name__ == "__main__":
    unittest.main()

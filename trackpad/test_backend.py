"""Isolated tests: all files are temporary and all desktop commands are fake."""
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from backend import Backend, BackendError, ConflictError, Curve


ORIGINAL = Curve(.5, 1.25, 1.92)
CHANGED = Curve(.6, 1.4, 1.8)


class Desktop:
    def __init__(self, base, proc):
        self.base = base
        self.proc = proc
        self.settings = {"accel-profile": "'adaptive'", "speed": "0.2"}
        self.calls = []
        self.logs = []
        self.fail_adaptive = False
        self.on_set = None
        self.no_log = False
        self.pid = "1234"
        (base / "libmacbook-trackpad.so").write_bytes(b"test library")
        (base / "support.json").write_text(json.dumps({
            "curve_version": 3, "sha256": hashlib.sha256(b"test library").hexdigest()}))
        (proc / self.pid).mkdir(parents=True)
        self.mapping()

    def mapping(self, *, deleted=False):
        library = self.base / "libmacbook-trackpad.so"
        device = library.stat().st_dev
        (self.proc / self.pid / "maps").write_text(
            f"1000-2000 r--p 00000000 {os.major(device):02x}:{os.minor(device):02x} {library.stat().st_ino} {library}" +
            (" (deleted)" if deleted else "") + "\n")

    def log_curve(self):
        if self.no_log or not (self.base / "curve.conf").exists():
            return
        slow, fast = map(float, (self.base / "curve.conf").read_text().split())
        medium = float((self.base / "medium.conf").read_text()) if (self.base / "medium.conf").exists() else 1.
        self.logs.append({"_PID": self.pid, "__REALTIME_TIMESTAMP": str(time.time_ns() // 1000),
            "MESSAGE": f"macbook-trackpad: custom curve applied (slow={slow:.2f}, fast={fast:.2f}, medium={medium:.2f})"})

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[0] == "gsettings":
            key = argv[3]
            if argv[1] == "get":
                return self.settings[key]
            value = argv[4]
            if self.on_set:
                self.on_set(key, value)
            if self.fail_adaptive and key == "accel-profile" and value == "'adaptive'":
                self.fail_adaptive = False
                raise RuntimeError("simulated desktop failure")
            self.settings[key] = value
            if key == "accel-profile" and value == "'adaptive'":
                self.log_curve()
            return ""
        if argv[0] == "systemctl":
            return self.pid
        if argv[0] == "journalctl":
            return "\n".join(json.dumps(line) for line in self.logs)
        raise AssertionError(argv)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / "config"
        self.base.mkdir()
        self.proc = Path(self.temp.name) / "proc"
        (self.base / "curve.conf").write_text("0.5 1.92\n")
        (self.base / "medium.conf").write_text("1.25\n")
        self.desktop = Desktop(self.base, self.proc)
        self.backend = Backend(self.base, runner=self.desktop, proc_root=self.proc, sleep=lambda _: None)

    def test_read_and_optional_medium(self):
        self.assertEqual(self.backend.read().curve, ORIGINAL)
        (self.base / "medium.conf").unlink()
        self.assertEqual(self.backend.read().curve, Curve(.5, 1, 1.92))
        (self.base / "curve.conf").unlink()
        self.assertIsNone(self.backend.read().curve)

    def test_reject_invalid_and_unsafe_files(self):
        for text in ("nan 1.2", "0.1 1.2", "0.5 1.2 extra", ""):
            with self.subTest(text=text):
                (self.base / "curve.conf").write_text(text)
                with self.assertRaises(BackendError):
                    self.backend.read()
        (self.base / "curve.conf").unlink()
        (self.base / "curve.conf").symlink_to(self.base / "medium.conf")
        with self.assertRaises(BackendError):
            self.backend.read()

    def test_validation_and_preview_boundaries(self):
        for c in (Curve(float("nan"), 1.25, 1.92), Curve(.5, 1.81, 1.92), Curve(.5, 1.25, float("inf")), Curve(.5, 1.8, 4.01)):
            with self.assertRaises(BackendError):
                c.validate()
        for slow, medium, fast in itertools.product((.3, .5, .9), (1, 1.25, 1.5, 1.8), (1, 1.92, 2, 4)):
            with self.subTest(slow=slow, medium=medium, fast=fast):
                points = Backend.propose(Curve(slow, medium, fast))
                self.assertEqual(tuple(x for x, _ in points), tuple(range(0, 640, 10)))
                self.assertEqual(points[0][1], 0.)
                self.assertTrue(all(math.isfinite(y) for _, y in points))
                self.assertTrue(all(a[1] < b[1] for a, b in zip(points, points[1:])))
                gains = [y / x for x, y in points[1:]]
                self.assertTrue(all(a <= b or math.isclose(a, b, rel_tol=2e-15)
                                    for a, b in zip(gains, gains[1:])))
                # Independent response requirements at each draggable point.
                for speed, factor in ((10, slow), (100, .9 * medium),
                                      (400, (387 / 130) * fast), (520, 4.8 * fast)):
                    self.assertAlmostEqual(points[speed // 10][1],
                                           speed * .2968 / 25.4 * factor, places=12)

    def test_apply_and_persistent_undo_preserve_exact_files_and_speed(self):
        before = self.backend.read()
        result = self.backend.apply(CHANGED, expected=before)
        self.assertEqual(result.status, "active")
        journal = next(call for call in self.desktop.calls if call[0] == "journalctl")
        self.assertIn("--boot=0", journal)
        self.assertIn("--grep=^macbook-trackpad:", journal)
        self.assertEqual(result.snapshot.curve, CHANGED)
        self.assertEqual(self.desktop.settings["speed"], "0.2")
        self.assertFalse(any(c[:2] == ["gsettings", "set"] and c[3] == "speed" for c in self.desktop.calls))
        second = Backend(self.base, runner=self.desktop, proc_root=self.proc, sleep=lambda _: None)
        self.assertTrue(second.can_undo())
        undone = second.undo(expected=second.read())
        self.assertEqual(undone.snapshot.curve, ORIGINAL)
        self.assertEqual((self.base / "curve.conf").read_bytes(), b"0.5 1.92\n")
        self.assertEqual((self.base / "medium.conf").read_bytes(), b"1.25\n")
        self.assertFalse(second.can_undo())

    def test_partial_write_failure_restores_first_file(self):
        before = self.backend.read()
        real = self.backend._atomic_write
        failed = False
        def write(name, data, mode=0o600):
            nonlocal failed
            if name == "medium.conf" and not failed:
                failed = True
                raise OSError("disk full")
            return real(name, data, mode)
        self.backend._atomic_write = write
        with self.assertRaises(BackendError):
            self.backend.apply(CHANGED, expected=before)
        self.assertEqual(self.backend.read().curve, ORIGINAL)
        self.assertEqual((self.base / "curve.conf").read_bytes(), before._files[0].data)
        self.assertFalse(any(c[:2] == ["gsettings", "set"] for c in self.desktop.calls))

    def test_gsettings_failure_restores_files_and_profile(self):
        before = self.backend.read()
        self.desktop.fail_adaptive = True
        with self.assertRaises(BackendError):
            self.backend.apply(CHANGED, expected=before)
        self.assertEqual(self.backend.read().curve, ORIGINAL)
        self.assertEqual(self.desktop.settings, {"accel-profile": "'adaptive'", "speed": "0.2"})
        self.assertIn("slow=0.50", self.desktop.logs[-1]["MESSAGE"])

    def test_external_change_before_apply_is_not_overwritten(self):
        before = self.backend.read()
        (self.base / "medium.conf").write_text("1.1\n")
        with self.assertRaises(ConflictError):
            self.backend.apply(CHANGED, expected=before)
        self.assertEqual((self.base / "medium.conf").read_text(), "1.1\n")

    def test_external_change_during_commit_is_preserved(self):
        before = self.backend.read()
        real = self.backend._atomic_write
        def write(name, data, mode=0o600):
            real(name, data, mode)
            if name == "curve.conf" and data != before._files[0].data:
                (self.base / "medium.conf").write_text("1.1\n")
        self.backend._atomic_write = write
        with self.assertRaises(ConflictError):
            self.backend.apply(CHANGED, expected=before)
        self.assertEqual((self.base / "curve.conf").read_bytes(), before._files[0].data)
        self.assertEqual((self.base / "medium.conf").read_text(), "1.1\n")

    def test_external_speed_change_is_preserved(self):
        def on_set(key, value):
            if value == "'adaptive'":
                self.desktop.settings["speed"] = "0.4"
        self.desktop.on_set = on_set
        with self.assertRaises(ConflictError):
            self.backend.apply(CHANGED, expected=self.backend.read())
        self.assertEqual(self.desktop.settings["speed"], "0.4")
        self.assertEqual(self.backend.read().curve, ORIGINAL)

    def test_undo_refuses_external_change(self):
        self.backend.apply(CHANGED, expected=self.backend.read())
        (self.base / "medium.conf").write_text("1.1\n")
        self.assertFalse(self.backend.can_undo())
        with self.assertRaises(ConflictError):
            self.backend.undo(expected=self.backend.read())
        self.assertEqual((self.base / "medium.conf").read_text(), "1.1\n")

    def test_undo_preserves_external_desktop_speed_and_profile(self):
        result = self.backend.apply(CHANGED, expected=self.backend.read())
        original_settings = dict(self.desktop.settings)
        for key, value in (("speed", "0.4"), ("accel-profile", "'flat'"), ("accel-profile", "'default'")):
            with self.subTest(key=key, value=value):
                self.desktop.settings = {**original_settings, key: value}
                before_calls = len(self.desktop.calls)
                before_files = self.backend.read()
                with self.assertRaisesRegex(ConflictError, "changed after Apply"):
                    self.backend.undo(expected=result.snapshot)
                self.assertEqual(self.desktop.settings, {**original_settings, key: value})
                self.assertEqual(self.backend.read(), before_files)
                self.assertFalse(any(call[:2] == ["gsettings", "set"] for call in self.desktop.calls[before_calls:]))
                self.assertTrue((self.base / "tuner-undo.json").exists())
        # The button's inexpensive eligibility check performs no desktop calls.
        before_calls = len(self.desktop.calls)
        self.assertTrue(self.backend.can_undo(result.snapshot))
        self.assertEqual(len(self.desktop.calls), before_calls)

    def test_undo_rejects_legacy_backup_without_desktop_baseline(self):
        result = self.backend.apply(CHANGED, expected=self.backend.read())
        path = self.base / "tuner-undo.json"
        record = json.loads(path.read_text())
        del record["after_settings"]
        path.write_text(json.dumps(record))
        before = self.backend.read()
        self.assertFalse(self.backend.can_undo(before))
        with self.assertRaises(BackendError):
            self.backend.undo(expected=result.snapshot)
        self.assertEqual(self.backend.read(), before)

    def test_lock_excludes_second_instance(self):
        with (self.base / "tuner.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ConflictError):
                self.backend.apply(CHANGED, expected=self.backend.read())

    def test_baseline_is_write_once(self):
        self.backend.initialize_baseline(ORIGINAL)
        old = (self.base / "tuner-baseline.json").read_bytes()
        self.assertEqual(self.backend.initialize_baseline(CHANGED), ORIGINAL)
        self.assertEqual((self.base / "tuner-baseline.json").read_bytes(), old)

    def test_support_hash_comes_from_local_build_manifest(self):
        path = self.base / 'support.json'
        path.unlink()
        self.assertIsNone(self.backend._support_hash())
        digest = hashlib.sha256(b'locally built binary').hexdigest()
        path.write_text(json.dumps({'curve_version': 3, 'sha256': digest}))
        self.assertEqual(self.backend._support_hash(), digest)
        for value in ({'curve_version': 1, 'sha256': digest}, {'curve_version': 2, 'sha256': digest},
                      {'curve_version': 3, 'sha256': 'bad'}, {}, None, []):
            path.write_text(json.dumps(value))
            self.assertIsNone(self.backend._support_hash())

    def test_disabled_installation_cannot_be_reactivated_by_open_window(self):
        self.backend.apply(CHANGED, expected=self.backend.read())
        before = self.backend.read()
        undo = (self.base / 'tuner-undo.json').read_bytes()
        (self.base / 'disabled').write_text('uninstalled')
        self.desktop.calls.clear()
        for action in (lambda: self.backend.apply(ORIGINAL, expected=before),
                       lambda: self.backend.undo(expected=before)):
            with self.assertRaisesRegex(BackendError, 'disabled'):
                action()
        self.assertEqual(self.backend.read(), before)
        self.assertEqual((self.base / 'tuner-undo.json').read_bytes(), undo)
        self.assertEqual(self.desktop.calls, [])

    def test_saved_without_loaded_library_requires_sign_out(self):
        self.desktop.mapping(deleted=True)
        before = self.backend.read()
        with self.assertRaisesRegex(BackendError, "Sign out and back in once"):
            self.backend.apply(CHANGED, expected=before)
        self.assertEqual(self.backend.read(), before)
        self.assertEqual(self.backend.status().status, "sign_out_required")
        self.assertFalse(any("logout" in " ".join(c) for c in self.desktop.calls))

    def test_all_apply_values_reject_unsupported_mapping_without_mutations(self):
        self.backend.apply(CHANGED, expected=self.backend.read())
        before = self.backend.read()
        undo = (self.base / "tuner-undo.json").read_bytes()
        settings = dict(self.desktop.settings)
        library = self.base / "libmacbook-trackpad.so"
        maps = self.proc / self.desktop.pid / "maps"
        digest = hashlib.sha256(b"test library").hexdigest()
        for curve in (ORIGINAL, CHANGED, Curve(.5, 1.8, 2), Curve(.5, 1.5, 4)):
            for variant in ("deleted", "inode", "device", "path", "maps missing", "library missing", "hash", "pin missing", "old version"):
                with self.subTest(curve=curve, variant=variant):
                    library.write_bytes(b"test library")
                    self.desktop.mapping()
                    parts = maps.read_text().split(None, 5)
                    support = {"curve_version": 3, "sha256": digest}
                    if variant == "deleted":
                        self.desktop.mapping(deleted=True)
                    elif variant == "inode":
                        parts[4] = str(library.stat().st_ino + 1)
                        maps.write_text(" ".join(parts))
                    elif variant == "device":
                        parts[3] = "ff:ff"
                        maps.write_text(" ".join(parts))
                    elif variant == "path":
                        parts[5] = str(library) + ".other\n"
                        maps.write_text(" ".join(parts))
                    elif variant == "maps missing":
                        maps.unlink()
                    elif variant == "library missing":
                        library.unlink()
                    elif variant == "hash":
                        # Even a correctly mapped old binary is insufficient.
                        support["sha256"] = "0" * 64
                    elif variant == "pin missing":
                        support = {}
                    elif variant == "old version":
                        support["curve_version"] = 2
                    (self.base / "support.json").write_text(json.dumps(support))
                    calls = len(self.desktop.calls)
                    with self.assertRaisesRegex(BackendError, "Sign out and back in once"):
                        self.backend.apply(curve, expected=before)
                    self.assertEqual(self.backend.read(), before)
                    self.assertEqual((self.base / "tuner-undo.json").read_bytes(), undo)
                    self.assertEqual(self.desktop.settings, settings)
                    self.assertFalse(any(c[:2] == ["gsettings", "set"] for c in self.desktop.calls[calls:]))

    def test_expanded_apply_accepts_pinned_current_library(self):
        digest = hashlib.sha256((self.base / "libmacbook-trackpad.so").read_bytes()).hexdigest()
        expanded = Curve(.5, 1.8, 4)
        with patch.object(self.backend, "_support_hash", return_value=digest):
            result = self.backend.apply(expanded, expected=self.backend.read())
        self.assertEqual(result.snapshot.curve, expanded)
        self.assertEqual(result.status, "active")

    def test_read_and_preview_remain_available_while_new_library_is_pending(self):
        self.desktop.mapping(deleted=True)
        with patch.object(self.backend, "_support_hash", return_value=None):
            self.assertEqual(self.backend.read().curve, ORIGINAL)
            self.assertEqual(len(self.backend.propose(Curve(.5, 1.5, 2))), 64)
            self.assertEqual(self.backend.status().status, "sign_out_required")
        self.assertFalse(any(c[:2] == ["gsettings", "set"] for c in self.desktop.calls))

    def test_all_undo_values_reject_pending_library_and_retain_backup(self):
        for restored in (ORIGINAL, Curve(.5, 1.8, 4)):
            with self.subTest(curve=restored):
                self.backend.apply(restored, expected=self.backend.read())
                current = self.backend.apply(CHANGED, expected=self.backend.read()).snapshot
                undo = self.backend._file("tuner-undo.json")
                self.desktop.mapping(deleted=True)
                calls = len(self.desktop.calls)
                with self.assertRaisesRegex(BackendError, "Sign out and back in once"):
                    self.backend.undo(expected=current)
                self.assertEqual(self.backend.read(), current)
                self.assertEqual(self.backend._file("tuner-undo.json"), undo)
                self.assertFalse(any(c[:2] == ["gsettings", "set"] for c in self.desktop.calls[calls:]))
                self.desktop.mapping()
                result = self.backend.undo(expected=current)
                self.assertEqual(result.snapshot.curve, restored)
                self.assertEqual(result.status, "active")

    def test_status_rejects_old_formula_or_wrong_hash_despite_matching_log(self):
        self.desktop.log_curve()
        self.assertEqual(self.backend.status().status, "active")
        path = self.base / "support.json"
        digest = hashlib.sha256((self.base / "libmacbook-trackpad.so").read_bytes()).hexdigest()
        for record in ({"curve_version": 2, "sha256": digest},
                       {"curve_version": 3, "sha256": "0" * 64}):
            with self.subTest(record=record):
                path.write_text(json.dumps(record))
                self.assertEqual(self.backend.status().status, "sign_out_required")

    def test_expanded_apply_rejects_changing_desktop_process(self):
        digest = hashlib.sha256((self.base / "libmacbook-trackpad.so").read_bytes()).hexdigest()
        calls = 0
        def runner(argv):
            nonlocal calls
            if argv[0] == "systemctl":
                calls += 1
                return "1234" if calls == 1 else "9999"
            return self.desktop(argv)
        self.backend.runner = runner
        before = self.backend.read()
        with patch.object(self.backend, "_support_hash", return_value=digest):
            with self.assertRaises(BackendError):
                self.backend.apply(Curve(.5, 1.8, 4), expected=before)
        self.assertEqual(self.backend.read(), before)

    def test_expanded_apply_rejects_library_replacement_during_probe(self):
        library = self.base / "libmacbook-trackpad.so"
        digest = hashlib.sha256(library.read_bytes()).hexdigest()
        maps = self.proc / self.desktop.pid / "maps"
        read_text = Path.read_text
        def changing_read(path, *args, **kwargs):
            result = read_text(path, *args, **kwargs)
            if path == maps:
                replacement = self.base / "replacement.so"
                replacement.write_bytes(b"test library")
                replacement.replace(library)
            return result
        before = self.backend.read()
        with patch.object(self.backend, "_support_hash", return_value=digest), patch.object(Path, "read_text", changing_read):
            with self.assertRaises(BackendError):
                self.backend.apply(Curve(.5, 1.8, 4), expected=before)
        self.assertEqual(self.backend.read(), before)

    def test_old_log_and_wrong_pid_never_confirm_new_apply(self):
        self.desktop.log_curve()
        self.desktop.no_log = True
        result = self.backend.apply(ORIGINAL, expected=self.backend.read())
        self.assertEqual(result.status, "unverified")
        self.desktop.logs[-1]["__REALTIME_TIMESTAMP"] = str(time.time_ns() // 1000 + 10000)
        self.desktop.logs[-1]["_PID"] = "999"
        self.assertEqual(self.backend.status(ORIGINAL).status, "unverified")

    def test_undo_backup_failure_restores_active_old_curve(self):
        real = self.backend._atomic_write
        def write(name, data, mode=0o600):
            if name == "tuner-undo.json":
                raise OSError("backup disk full")
            return real(name, data, mode)
        self.backend._atomic_write = write
        with self.assertRaises(BackendError):
            self.backend.apply(CHANGED, expected=self.backend.read())
        self.assertEqual(self.backend.read().curve, ORIGINAL)
        self.assertIn("slow=0.50", self.desktop.logs[-1]["MESSAGE"])


if __name__ == "__main__":
    unittest.main()

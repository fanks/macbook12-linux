"""User-only trackpad curve storage, preview, reload and confirmation.

No GTK dependency, root operations, event access, or automatic logout. Each
configuration file is atomically replaced. A lock serializes cooperating apps;
optimistic comparisons also detect external edits before writes and rollback.
An unrelated writer which ignores the lock cannot be made fully transactional
across two files, so rollback never overwrites a detected external edit.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from typing import Callable

SCHEMA = "org.gnome.desktop.peripherals.touchpad"
SERVICE = "org.gnome.Shell@wayland.service"
DEFAULT_BASE = Path(os.environ.get("MACBOOK_TRACKPAD_DIR") or
                    Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "macbook-trackpad")
NAMES = ("curve.conf", "medium.conf")
DPI = 2413.0
SLOW_MIN, SLOW_MAX = .3, .9
MEDIUM_MIN, MEDIUM_MAX = 1., 1.8
FAST_MIN, FAST_MAX = 1., 4.


class BackendError(Exception):
    """A user-readable failure. Settings may be re-read after any failure."""


class ConflictError(BackendError):
    pass


@dataclass(frozen=True)
class Curve:
    slow: float
    medium: float
    fast: float

    def validate(self) -> Curve:
        for name, value, low, high in (
            ("Precision", self.slow, SLOW_MIN, SLOW_MAX),
            ("Medium speed", self.medium, MEDIUM_MIN, MEDIUM_MAX),
            ("Fast movement", self.fast, FAST_MIN, FAST_MAX),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise BackendError(f"{name} must be a finite number between {low:g} and {high:g}.")
        _points(self)
        return self


def _points(c: Curve) -> tuple[tuple[float, float], ...]:
    """Exactly the 64 C samples; x=mm/s, y=output device counts/ms.

    Connect adjacent samples with straight lines, including in the preview.
    The custom API's x-step is 10 * DPI / 25400 counts/ms.
    """
    points = []
    base = .2968 * 1000. / DPI
    for i in range(64):
        v = i * 10.
        if v <= 10:
            factor = c.slow
        elif v < 30:
            factor = c.slow + (.9 - c.slow) * (v - 10) / 20
        else:
            capped = min(v, 520.)
            factor = .9 if capped < 130 else .0025 * (capped / 130) * (capped - 130) + .9
            t = max(0., min(1., (v - 100) / 160))
            factor *= 1 + (c.fast - 1) * t * t * (3 - 2 * t)
        weight = 0.
        if 30 < v < 60:
            t = (v - 30) / 30
            weight = t * t * (3 - 2 * t)
        elif 60 <= v <= 160:
            weight = 1.
        elif 160 < v < 260:
            t = (v - 160) / 100
            weight = 1 - t * t * (3 - 2 * t)
        factor *= 1 + (c.medium - 1) * weight
        output = (v * DPI / 25400.) * base * factor
        if not math.isfinite(output) or not 0 <= output <= 10000 or (points and output < points[-1][1]):
            raise BackendError("The curve must increase without backward steps.")
        points.append((v, output))
    return tuple(points)


@dataclass(frozen=True)
class _File:
    data: bytes | None
    mode: int = 0o600
    identity: tuple[int, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    curve: Curve | None
    revision: str
    _files: tuple[_File, _File] = field(repr=False)


@dataclass(frozen=True)
class Status:
    status: str
    message: str


@dataclass(frozen=True)
class Result:
    snapshot: Snapshot
    status: str
    message: str


def _run(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, check=True, text=True, capture_output=True,
                                timeout=5, env={**os.environ, "LC_ALL": "C"})
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackendError(f"Could not read or change the desktop setting ({argv[0]}).") from exc


class Backend:
    def __init__(self, base: Path = DEFAULT_BASE, *, runner: Callable[[list[str]], str] = _run,
                 proc_root: Path = Path("/proc"), sleep: Callable[[float], None] = time.sleep):
        self.base = Path(base)
        self.runner = runner
        self.proc_root = Path(proc_root)
        self.sleep = sleep

    @staticmethod
    def propose(curve: Curve) -> tuple[tuple[float, float], ...]:
        curve.validate()
        return _points(curve)

    def _loaded_library(self, expected_sha256: str | None = None) -> tuple[str, bool]:
        """Identify the running GNOME process and its exact installed library.

        Do not accept an old, deleted mapping merely because the filename is
        the same. Re-check the service PID and installed inode after reading
        maps, so a concurrent login/library replacement cannot confirm support.
        """
        command = ["systemctl", "--user", "show", SERVICE, "--property=MainPID", "--value"]
        pid = self.runner(command).strip()
        if not pid.isdigit() or int(pid) <= 0:
            raise BackendError("The active desktop process could not be confirmed.")
        library = self.base / "libmacbook-trackpad.so"
        before = library.stat()
        if expected_sha256 is not None:
            image = self._file("libmacbook-trackpad.so", limit=2 * 1024 * 1024)
            identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            if image.data is None or image.identity[:4] != identity or hashlib.sha256(image.data).hexdigest() != expected_sha256:
                return pid, False
        maps = (self.proc_root / pid / "maps").read_text()
        loaded = False
        for line in maps.splitlines():
            parts = line.split(None, 5)
            device = f"{os.major(before.st_dev):02x}:{os.minor(before.st_dev):02x}"
            if len(parts) == 6 and parts[5] == str(library) and parts[3] == device and parts[4].isdigit() and int(parts[4]) == before.st_ino:
                loaded = True
        after = library.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return pid, False
        if self.runner(command).strip() != pid:
            return pid, False
        return pid, loaded

    def _require_curve_support(self, curve: Curve) -> None:
        # Existing values remain usable while the new library awaits login.
        if curve.medium <= 1.5 and curve.fast <= 2.:
            return
        try:
            expected_hash = self._support_hash()
            if expected_hash is None:
                raise BackendError("The expanded library build is not configured.")
            _, loaded = self._loaded_library(expected_hash)
        except Exception:
            loaded = False
        if not loaded:
            raise BackendError("Sign out and back in once to enable the higher medium and fast limits. Nothing was changed.")

    def _support_hash(self) -> str | None:
        """The installer records each user's locally compiled library identity."""
        try:
            record = json.loads(self._file("support.json").data or b"{}")
            value = record["sha256"]
            if record["curve_version"] == 2 and isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value):
                return value
        except (BackendError, KeyError, ValueError, TypeError):
            pass
        return None

    def _file(self, name: str, limit: int = 16384) -> _File:
        path = self.base / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW)
        except FileNotFoundError:
            return _File(None)
        except OSError as exc:
            raise BackendError(f"Cannot read {name} safely.") from exc
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_size > limit:
                raise BackendError(f"{name} must be a small regular file owned by you.")
            data = os.read(fd, limit + 1)
            after = os.fstat(fd)
            identity = (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if len(data) > limit or identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ConflictError(f"{name} changed while being read. Reload the settings.")
            return _File(data, stat.S_IMODE(s.st_mode), identity)
        finally:
            os.close(fd)

    @contextmanager
    def _lock(self):
        if not self.base.is_dir() or self.base.is_symlink() or self.base.stat().st_uid != os.getuid():
            raise BackendError("The settings folder is missing or is not owned by you.")
        fd = None
        try:
            fd = os.open(self.base / "tuner.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid():
                raise BackendError("The lock file cannot be used safely.")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConflictError("Another instance is saving. Try again.") from exc
            yield
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _parse_curve(files: tuple[_File, _File]) -> Curve | None:
        curve = None
        if files[0].data is not None:
            try:
                pair = files[0].data.decode("ascii").split()
                mid = ["1"] if files[1].data is None else files[1].data.decode("ascii").split()
                if len(pair) != 2 or len(mid) != 1:
                    raise ValueError("tokens")
                curve = Curve(float(pair[0]), float(mid[0]), float(pair[1])).validate()
            except (UnicodeError, ValueError) as exc:
                raise BackendError("The curve files have an invalid format. No changes were made.") from exc
        return curve

    def read(self) -> Snapshot:
        files = tuple(self._file(name, 1024) for name in NAMES)
        if files != tuple(self._file(name, 1024) for name in NAMES):
            raise ConflictError("The settings changed concurrently. Reload them.")
        curve = self._parse_curve(files)
        h = hashlib.sha256()
        for f in files:
            h.update(repr((f.data, f.mode, f.identity)).encode())
        return Snapshot(curve, h.hexdigest(), files)

    def _atomic_write(self, name: str, data: bytes, mode: int = 0o600) -> None:
        fd, temp = tempfile.mkstemp(prefix=".tuner-", dir=self.base)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temp, mode)
            os.replace(temp, self.base / name)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _replace(self, name: str, previous: _File, desired: _File) -> _File:
        if self._file(name) != previous:
            raise ConflictError(f"{name} changed outside the app. That change was preserved.")
        if desired.data is None:
            if previous.data is not None:
                (self.base / name).unlink()
        else:
            self._atomic_write(name, desired.data, desired.mode)
        return self._file(name)

    def _settings(self) -> dict[str, str]:
        values = {key: self.runner(["gsettings", "get", SCHEMA, key]).strip()
                  for key in ("accel-profile", "speed")}
        try:
            speed = float(values["speed"])
            valid = math.isfinite(speed) and -1 <= speed <= 1
        except ValueError:
            valid = False
        if not valid or values["accel-profile"] not in ("'default'", "'adaptive'", "'flat'"):
            raise BackendError("The current desktop setting could not be read safely.")
        return values

    def _set(self, key: str, value: str, expected: str, attempted: dict[str, set[str]]) -> None:
        if self.runner(["gsettings", "get", SCHEMA, key]).strip() != expected:
            raise ConflictError("The desktop setting changed outside the app. That change was preserved.")
        attempted.setdefault(key, set()).add(value)
        self.runner(["gsettings", "set", SCHEMA, key, value])
        if self.runner(["gsettings", "get", SCHEMA, key]).strip() != value:
            raise BackendError("The desktop did not confirm the setting.")

    def _restore_settings(self, before: dict[str, str], attempted: dict[str, set[str]]) -> list[str]:
        errors = []
        for key in ("accel-profile", "speed"):
            try:
                current = self.runner(["gsettings", "get", SCHEMA, key]).strip()
                # Re-read the restored files even when both old and new curves
                # use 'adaptive': restoring the same GSettings value emits no
                # change notification and would leave the new curve active.
                if key == "accel-profile" and key in attempted and current in ({before[key]} | attempted[key]):
                    rollback_attempts: dict[str, set[str]] = {}
                    self._set(key, "'flat'", current, rollback_attempts)
                    self.sleep(.08)
                    self._set(key, before[key], "'flat'", rollback_attempts)
                    continue
                if current == before[key]:
                    continue
                if key not in attempted or current not in attempted[key]:
                    errors.append("an external desktop change was preserved")
                    continue
                self.runner(["gsettings", "set", SCHEMA, key, before[key]])
            except Exception:
                errors.append(f"{key} could not be restored")
        return errors

    def _transaction(self, before: Snapshot, desired: tuple[_File, _File], settings: dict[str, str],
                     target_settings: dict[str, str]) -> Snapshot:
        written: dict[str, _File] = {}
        attempted: dict[str, set[str]] = {}
        try:
            for name, old, new in zip(NAMES, before._files, desired):
                written[name] = self._replace(name, old, new)
            for name in NAMES:
                if self._file(name) != written[name]:
                    raise ConflictError("The curve changed concurrently. The external change was preserved.")
            self._set("accel-profile", "'flat'", settings["accel-profile"], attempted)
            self.sleep(.08)
            self._set("accel-profile", target_settings["accel-profile"], "'flat'", attempted)
            if self.runner(["gsettings", "get", SCHEMA, "speed"]).strip() != settings["speed"]:
                raise ConflictError("Speed changed outside the app. That change was preserved.")
            if target_settings["speed"] != settings["speed"]:
                self._set("speed", target_settings["speed"], settings["speed"], attempted)
            after = self.read()
            if after._files != tuple(written[name] for name in NAMES):
                raise ConflictError("The curve changed concurrently. Reload it.")
            return after
        except Exception as exc:
            errors = []
            for name, original in zip(NAMES, before._files):
                if name in written:
                    try:
                        self._replace(name, written[name], original)
                    except Exception:
                        errors.append(f"{name} changed externally or could not be restored")
            errors.extend(self._restore_settings(settings, attempted))
            detail = " Previous settings were restored." if not errors else " Check the settings: " + "; ".join(errors) + "."
            kind = ConflictError if isinstance(exc, ConflictError) else BackendError
            raise kind(str(exc) + detail) from exc

    @staticmethod
    def _encoded_files(files: tuple[_File, _File]) -> list[dict]:
        return [{"data": None if f.data is None else base64.b64encode(f.data).decode("ascii"), "mode": f.mode} for f in files]

    def _undo_record(self) -> dict:
        raw = self._file("tuner-undo.json").data
        if raw is None:
            raise BackendError("There is no saved change to undo.")
        try:
            record = json.loads(raw)
            if record["version"] != 1 or len(record["files"]) != 2:
                raise ValueError("format")
            for f in record["files"]:
                if f["data"] is not None:
                    if len(base64.b64decode(f["data"], validate=True)) > 1024:
                        raise ValueError("size")
                if not isinstance(f["mode"], int) or f["mode"] & ~0o777:
                    raise ValueError("mode")
            # Older backups without after_settings cannot safely distinguish
            # the app's state from a later external desktop settings change.
            for key in ("settings", "after_settings"):
                if record[key]["accel-profile"] not in ("'default'", "'adaptive'", "'flat'"):
                    raise ValueError("profile")
                speed = float(record[key]["speed"])
                if not math.isfinite(speed) or not -1 <= speed <= 1:
                    raise ValueError("speed")
            return record
        except (ValueError, TypeError, KeyError) as exc:
            raise BackendError("The undo backup could not be read safely.") from exc

    def can_undo(self, snapshot: Snapshot | None = None) -> bool:
        try:
            return self._undo_record()["after_revision"] == (snapshot or self.read()).revision
        except BackendError:
            return False

    def apply(self, curve: Curve, *, expected: Snapshot) -> Result:
        curve.validate()
        desired = (_File(f"{curve.slow:.17g} {curve.fast:.17g}\n".encode()),
                   _File(f"{curve.medium:.17g}\n".encode()))
        with self._lock():
            if (self.base / "disabled").exists():
                raise BackendError("Custom support is disabled or uninstalled. Close this window.")
            before = self.read()
            if before.revision != expected.revision:
                raise ConflictError("The curve changed outside the app. Reload it before saving.")
            self._require_curve_support(curve)
            settings = self._settings()
            start = time.time_ns() // 1000
            after_settings = {**settings, "accel-profile": "'adaptive'"}
            after = self._transaction(before, desired, settings, after_settings)
            record = {"version": 1, "files": self._encoded_files(before._files), "settings": settings,
                      "after_settings": after_settings, "after_revision": after.revision}
            try:
                self._atomic_write("tuner-undo.json", (json.dumps(record) + "\n").encode())
            except Exception as exc:
                # A successful change must not silently lose its Undo backup.
                self._transaction(after, before._files, self._settings(), settings)
                raise BackendError("The undo backup could not be saved. The change was restored.") from exc
        verification = self.status(curve, since_us=start)
        return Result(after, verification.status, verification.message)

    def undo(self, *, expected: Snapshot) -> Result:
        with self._lock():
            if (self.base / "disabled").exists():
                raise BackendError("Custom support is disabled or uninstalled. Close this window.")
            before = self.read()
            record = self._undo_record()
            if before.revision != expected.revision or before.revision != record["after_revision"]:
                raise ConflictError("The curve changed after the last app update. The external change was preserved.")
            settings = self._settings()
            if settings != record["after_settings"]:
                raise ConflictError("Desktop speed or profile changed after Apply. Your external change was preserved; Undo was not applied.")
            desired = tuple(_File(None if f["data"] is None else base64.b64decode(f["data"]), f["mode"]) for f in record["files"])
            restored_curve = self._parse_curve(desired)
            if restored_curve is not None:
                self._require_curve_support(restored_curve)
            start = time.time_ns() // 1000
            after = self._transaction(before, desired, settings, record["settings"])
            (self.base / "tuner-undo.json").unlink()
        verification = self.status(after.curve, since_us=start)
        return Result(after, verification.status, "Previous settings restored. " + verification.message)

    def initialize_baseline(self, curve: Curve) -> Curve:
        curve.validate()
        with self._lock():
            if self._file("tuner-baseline.json").data is None:
                data = (json.dumps({"version": 1, "slow": curve.slow, "medium": curve.medium, "fast": curve.fast}) + "\n").encode()
                # O_EXCL prevents a second installer from replacing this baseline.
                fd = os.open(self.base / "tuner-baseline.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
                with os.fdopen(fd, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
        return self.baseline()

    def baseline(self) -> Curve:
        try:
            data = json.loads(self._file("tuner-baseline.json").data or b"{}")
            if data["version"] != 1:
                raise ValueError("version")
            return Curve(data["slow"], data["medium"], data["fast"]).validate()
        except (ValueError, TypeError, KeyError) as exc:
            raise BackendError("The installation baseline is missing or unreadable.") from exc

    def status(self, curve: Curve | None = None, *, since_us: int = 0) -> Status:
        """Require current process + current library inode + a matching log.

        A loaded library alone cannot prove that this curve is active. For
        apply(), only log messages after the transaction began are accepted.
        """
        try:
            curve = curve or self.read().curve
            if curve is None:
                return Status("unverified", "The custom curve is disabled.")
            pid_text, loaded = self._loaded_library()
            if not loaded:
                return Status("sign_out_required", "Saved. Sign out and back in to activate curve support.")
            if self._settings()["accel-profile"] != "'adaptive'":
                return Status("unverified", "Saved, but the desktop is not using the custom profile.")
            expected = f"macbook-trackpad: custom curve applied (slow={curve.slow:.2f}, fast={curve.fast:.2f}, medium={curve.medium:.2f})"
            for attempt in range(3 if since_us else 1):
                output = self.runner(["journalctl", "--user", "--boot=0", f"--unit={SERVICE}", "--grep=^macbook-trackpad:", "--case-sensitive=yes", "--output=json", "--no-pager", "-n", "80"])
                events = []
                for line in output.splitlines():
                    try:
                        event = json.loads(line)
                        message = event.get("MESSAGE", "")
                        stamp = int(event.get("__REALTIME_TIMESTAMP", "0"))
                        if str(event.get("_PID")) == pid_text and isinstance(message, str) and message.startswith("macbook-trackpad:") and stamp >= since_us:
                            events.append((stamp, message))
                    except (ValueError, TypeError):
                        continue
                if events and max(events, key=lambda item: item[0])[1] == expected:
                    return Status("active", "Saved and confirmed by the desktop.")
                if attempt < 2 and since_us:
                    self.sleep(.1)
            return Status("unverified", "Saved, but the desktop has not confirmed this curve yet.")
        except Exception:
            return Status("unverified", "Saved. Could not verify that the curve is active.")

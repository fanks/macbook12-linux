#!/usr/bin/env python3
"""Install/uninstall MacBook10,1 shortcuts from the logged-in GNOME user.

Only narrow package, group, keyd configuration and service commands use sudo.
No input events are read, and no logout or reboot is performed.
"""
from __future__ import annotations

import argparse
import ast
import grp
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import tempfile
import time

UUID = "macbook12-shortcuts@macbook12-linux"
CONFIG = Path("/etc/keyd/80-macbook12-shortcuts.conf")
KEYD = "/usr/bin/keyd.rvaiya"
SERVICE = "keyd.service"
REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "keyboard" / UUID
KEYS = (
    ("org.gnome.mutter", "overlay-key"),
    ("org.gnome.shell.keybindings", "toggle-overview"),
    ("org.gnome.desktop.wm.keybindings", "switch-input-source"),
    ("org.gnome.desktop.wm.keybindings", "switch-input-source-backward"),
    ("org.gnome.shell.keybindings", "toggle-message-tray"),
    ("org.gnome.shell.keybindings", "focus-active-notification"),
)


class Error(Exception):
    pass


def run(args, *, check=True):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "LC_ALL": "C"})
    if check and result.returncode:
        raise Error(f"{' '.join(args[:3])}: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def sudo(*args):
    # Keep the terminal connected: sudo's password prompt and apt's review
    # must be visible to the person running the installer.
    command = ["sudo", "--", *map(str, args)]
    if subprocess.run(command).returncode:
        raise Error(f"Privileged command failed: {' '.join(command[:4])}")


def get(schema, key):
    return run(["gsettings", "get", schema, key])


def put(schema, key, value):
    run(["gsettings", "set", schema, key, value])
    return get(schema, key)


def array(value):
    try:
        result = ast.literal_eval(value.removeprefix("@as "))
    except (ValueError, SyntaxError) as exc:
        raise Error("Cannot safely parse a GNOME shortcut list.") from exc
    if not isinstance(result, list) or any(not isinstance(v, str) for v in result):
        raise Error("Expected a GNOME string list.")
    return result


def bitmap(text):
    """Linux x86-64 sysfs prints highest unsigned-long word first."""
    words = text.split()
    if not words or any(not re.fullmatch(r"[0-9a-fA-F]{1,16}", w) for w in words):
        raise Error("Invalid input capability bitmap.")
    return sum(int(word, 16) << (64 * i) for i, word in enumerate(reversed(words)))


def keyd_id(vendor, product, name, keys, absolute, relative):
    """keyd v2.5.0 device.c: count first 288 key bits, then its djb2 UID.

    Source: https://github.com/rvaiya/keyd/blob/v2.5.0/src/device.c
    Upstream copyright/license is retained in keyboard/KEYD-LICENSE.
    """
    required = sum(1 << key for key in (*range(2, 12), *range(16, 22)))
    if keys & required != required:
        raise Error("The detected device lacks the normal keyboard capabilities.")
    count = (keys & ((1 << 288) - 1)).bit_count()
    value = 5183
    for byte in count.to_bytes(4, "big") + bytes((absolute & 255, relative & 255)) + name.encode("ascii"):
        value = (value * 33 + byte) & 0xffffffff
    return f"k:{vendor:04x}:{product:04x}:{value:08x}"


def detect_keyboard(root=Path("/sys/class/input")):
    candidates = []
    for event in sorted(root.glob("event*")):
        device = event / "device"
        try:
            name = (device / "name").read_text().strip()
        except FileNotFoundError:
            continue
        if name != "Apple SPI Keyboard":
            continue
        if not any(part.startswith("spi") for part in device.resolve().parts):
            raise Error("Apple SPI Keyboard was found outside an SPI device path; inspect it manually.")
        capabilities = device / "capabilities"
        vendor = int((device / "id/vendor").read_text().strip(), 16)
        product = int((device / "id/product").read_text().strip(), 16)
        if not 0 <= vendor <= 65535 or not 0 <= product <= 65535:
            raise Error("Invalid input vendor/product ID.")
        identifier = keyd_id(vendor, product, name,
                             bitmap((capabilities / "key").read_text()),
                             bitmap((capabilities / "abs").read_text()),
                             bitmap((capabilities / "rel").read_text()))
        candidates.append(identifier)
    if len(candidates) != 1:
        raise Error(f"Expected one built-in Apple SPI Keyboard; found {len(candidates)}. Nothing was remapped.")
    return candidates[0]


def config_text(identifier):
    if not re.fullmatch(r"k:[0-9a-f]{4}:[0-9a-f]{4}:[0-9a-f]{8}", identifier):
        raise Error("Invalid keyd device identifier.")
    return f"# MacBook12 Linux: this keyboard only; GNOME supplies the bindings.\n[ids]\n{identifier}\n\n[main]\n"


def settings_plan(before):
    """Preserve unrelated bindings, including hardware layout-switch keys."""
    values = {(entry["schema"], entry["key"]): entry["before"] for entry in before}
    output = []
    for schema, key in KEYS:
        value = values[(schema, key)]
        if key == "overlay-key":
            desired = "''"
        else:
            bindings = array(value)
            if key == "toggle-overview":
                bindings = list(dict.fromkeys(bindings + ["<Super>space"]))
            elif key == "switch-input-source":
                bindings = ["<Control><Alt>space" if b == "<Super>space" else b for b in bindings]
            elif key == "switch-input-source-backward":
                bindings = ["<Shift><Control><Alt>space" if b == "<Shift><Super>space" else b for b in bindings]
            elif key == "toggle-message-tray":
                bindings = [b for b in bindings if b != "<Super>v"]
            elif key == "focus-active-notification":
                bindings = [b for b in bindings if b != "<Super>n"]
            desired = repr(bindings)
        output.append({"schema": schema, "key": key, "before": value, "desired": desired})
    return output


def restore_settings(entries, getter=get, setter=put):
    kept = []
    for entry in entries:
        if "after" not in entry:
            continue
        schema, key = entry["schema"], entry["key"]
        current = getter(schema, key)
        matches = current == entry["after"]
        if not matches:
            try:
                matches = array(current) == array(entry["after"])
            except Error:
                pass
        if matches:
            setter(schema, key, entry["before"])
        else:
            kept.append(f"{schema} {key}")
    return kept


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".keyboard-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def paths():
    account = pwd.getpwuid(os.getuid())
    home = Path(account.pw_dir)
    return account, home / ".local/share/gnome-shell/extensions" / UUID, home / ".local/state/macbook12-linux/keyboard.json"


def check_session():
    if os.geteuid() == 0:
        raise Error("Run this command as your normal desktop user, without sudo.")
    if os.environ.get("XDG_SESSION_TYPE") != "wayland" or "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        raise Error("Run install from a Terminal in your GNOME Wayland session.")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if release.get("ID", "").strip('"') != "debian" or release.get("VERSION_ID", "").strip('"') != "13":
        raise Error("This installer supports Debian 13 only.")
    if platform.machine() != "x86_64" or Path("/sys/class/dmi/id/product_name").read_text().strip() != "MacBook10,1":
        raise Error("Only MacBook10,1 is supported. Other 12-inch models are untested.")
    if not re.search(r"\b48(?:\.|\s|$)", run(["gnome-shell", "--version"])):
        raise Error("This extension supports GNOME Shell 48 only.")
    if get("org.gnome.shell", "disable-user-extensions") != "false":
        raise Error("Enable user extensions in GNOME before installing.")
    if UUID in array(get("org.gnome.shell", "disabled-extensions")):
        raise Error("This extension is explicitly disabled. Remove that override before installing.")


def keyd_version():
    value = run(["dpkg-query", "-W", "-f=${Status}\n${Version}", "keyd"], check=False).splitlines()
    return value[-1] if len(value) == 2 and value[0] == "install ok installed" else None


def check_keyd_version(value):
    if not value or not re.fullmatch(r"2\.5\.0-[A-Za-z0-9.+~]+", value):
        raise Error(f"Packaged keyd 2.5.0 is required; found {value!r}. No remapping was installed.")


def other_configs():
    return [path for path in CONFIG.parent.glob("*.conf") if path != CONFIG]


def install():
    check_session()
    account, extension, state = paths()
    if any(path.exists() or path.is_symlink() for path in (state, extension, CONFIG)) or other_configs():
        raise Error("An installation, extension, or keyd configuration already exists. Resolve it before installing; nothing will be overwritten.")
    identifier = detect_keyboard()
    version = keyd_version()
    if version:
        check_keyd_version(version)
    for tool in ("sudo", "gsettings", "glib-compile-schemas"):
        if not shutil.which(tool):
            raise Error(f"Required command is missing: {tool}")
    before = [{"schema": s, "key": k, "before": get(s, k)} for s, k in KEYS]
    enabled = array(get("org.gnome.shell", "enabled-extensions"))
    if UUID in enabled:
        raise Error("An existing extension registration was found. Remove it before installing.")
    service_before = {
        "enabled": run(["systemctl", "is-enabled", SERVICE], check=False) == "enabled",
        "active": run(["systemctl", "is-active", SERVICE], check=False) == "active",
    }
    if version is None:
        # Install from the configured Debian repositories, never a fetched binary.
        sudo("apt-get", "update")
        sudo("apt-get", "install", "--no-install-recommends", "--no-upgrade", "keyd")
        check_keyd_version(keyd_version())
    if not Path(KEYD).is_file():
        raise Error("Debian's /usr/bin/keyd.rvaiya binary is missing.")
    keyd_group = grp.getgrnam("keyd")
    group_added = account.pw_name not in keyd_group.gr_mem and account.pw_gid != keyd_group.gr_gid
    text = config_text(identifier)
    manifest = {"version": 1, "uid": account.pw_uid, "device_id": identifier,
                "settings": settings_plan(before), "files": {}, "group_added": group_added,
                "service_before": service_before, "config_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "status": "installing"}
    atomic_json(state, manifest)
    try:
        extension.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".macbook12-", dir=extension.parent) as temp:
            stage = Path(temp) / UUID
            shutil.copytree(SOURCE, stage)
            metadata = json.loads((stage / "metadata.json").read_text())
            metadata["keyd-config"] = text
            (stage / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
            run(["glib-compile-schemas", "--strict", str(stage / "schemas")])
            files = {str(p.relative_to(stage)): digest(p) for p in stage.rglob("*") if p.is_file()}
            if extension.exists() or extension.is_symlink():
                raise Error("The extension appeared during installation; it was not overwritten.")
            os.replace(stage, extension)
            manifest["files"] = files
            atomic_json(state, manifest)
        if CONFIG.exists() or CONFIG.is_symlink() or other_configs():
            raise Error("A keyd configuration appeared during installation; it was not overwritten.")
        with tempfile.TemporaryDirectory(prefix="macbook12-keyd-") as temp:
            candidate = Path(temp) / "keyboard.conf"
            candidate.write_text(text)
            if not CONFIG.parent.exists():
                sudo("install", "-d", "-m", "0755", CONFIG.parent)
            sudo("install", "-m", "0644", "--", candidate, CONFIG)
        if digest(CONFIG) != manifest["config_sha256"]:
            raise Error("The installed keyd configuration did not verify.")
        if group_added:
            sudo("usermod", "-aG", "keyd", account.pw_name)
        sudo("systemctl", "enable", "--now", SERVICE)
        sudo(KEYD, "reload")
        for entry in manifest["settings"]:
            if get(entry["schema"], entry["key"]) != entry["before"]:
                raise Error("A shortcut changed during installation. That change was preserved.")
            # Record the expected value before mutation, so even failure of
            # the read-back can be conservatively undone (including @as []).
            entry["after"] = entry["desired"]
            atomic_json(state, manifest)
            entry["after"] = put(entry["schema"], entry["key"], entry["desired"])
            atomic_json(state, manifest)
        current = array(get("org.gnome.shell", "enabled-extensions"))
        put("org.gnome.shell", "enabled-extensions", repr(current + [UUID]))
        manifest["status"] = "installed"
        atomic_json(state, manifest)
    except Exception:
        print("Installation did not finish. Attempting a conservative uninstall; packages are retained.")
        try:
            uninstall()
        except Exception as cleanup:
            print(f"Cleanup needs review: {cleanup}. Run this script's uninstall command after review.")
        raise
    print(f"Installed for {account.pw_name}; detected keyboard ID: {identifier}")
    print("Save work, then log out of GNOME and all other sessions for this account and sign in again.")
    print("No logout/reboot was performed. See docs/keyboard.md if the old user manager retains its groups.")


def remove_files(extension, files):
    kept = []
    for relative, expected in files.items():
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts:
            raise Error("Invalid path in keyboard installation metadata.")
        path = extension / rel
        symlink = extension.is_symlink() or any((extension / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts) + 1))
        if symlink or (path.exists() and (not path.is_file() or digest(path) != expected)):
            kept.append(str(path))
        else:
            path.unlink(missing_ok=True)
    if extension.exists() and not extension.is_symlink():
        for path in sorted((p for p in extension.rglob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: len(p.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass
        try:
            extension.rmdir()
        except OSError:
            pass
    return kept


def uninstall():
    if os.geteuid() == 0:
        raise Error("Run uninstall as the desktop user, without sudo.")
    account, extension, state = paths()
    if not state.exists():
        raise Error("No keyboard installation record was found for this user.")
    manifest = json.loads(state.read_text())
    if manifest.get("version") != 1 or manifest.get("uid") != account.pw_uid:
        raise Error("The installation record does not match this user.")
    config_present = CONFIG.exists() or CONFIG.is_symlink()
    owned_config = CONFIG.is_file() and not CONFIG.is_symlink() and digest(CONFIG) == manifest["config_sha256"]
    shared = bool(other_configs()) or (config_present and not owned_config)
    service_now = {
        "enabled": run(["systemctl", "is-enabled", SERVICE], check=False) == "enabled",
        "active": run(["systemctl", "is-active", SERVICE], check=False) == "active",
    }
    # Stop only our exclusive daemon. This also prevents a late extension
    # callback from reinstating a binding while the extension is disabled.
    if not shared:
        sudo("systemctl", "stop", SERVICE)
    enabled = array(get("org.gnome.shell", "enabled-extensions"))
    put("org.gnome.shell", "enabled-extensions", repr([v for v in enabled if v != UUID]))
    kept = restore_settings(manifest["settings"])
    kept.extend(remove_files(extension, manifest["files"]))
    if owned_config:
        # Recheck immediately before the narrow privileged removal.
        if digest(CONFIG) != manifest["config_sha256"]:
            raise Error("The keyd configuration changed during uninstall; inspect it before removal.")
        sudo("rm", "--", CONFIG)
    elif config_present:
        kept.append(str(CONFIG))
    if not shared:
        if service_now["enabled"]:
            sudo("systemctl", "enable" if manifest["service_before"]["enabled"] else "disable", SERVICE)
        elif manifest["service_before"]["enabled"]:
            kept.append("keyd service disabled after installation")
        if service_now["active"] and manifest["service_before"]["active"]:
            sudo("systemctl", "start", SERVICE)
        group = grp.getgrnam("keyd")
        if manifest["group_added"] and account.pw_name in group.gr_mem:
            sudo("gpasswd", "-d", account.pw_name, "keyd")
    else:
        kept.append("keyd service/group: later keyd configuration exists; review it and reload keyd manually")
    manifest["status"] = "uninstalled"
    manifest["preserved"] = kept
    atomic_json(state, manifest)
    archive = state.with_name(f"keyboard-uninstalled-{time.time_ns()}.json")
    state.rename(archive)
    print("Keyboard integration removed; keyd package retained. A fresh login drops any removed group membership.")
    for item in kept:
        print(f"Preserved later change: {item}")


def status():
    account, extension, state = paths()
    print(f"Installation record: {'present' if state.exists() else 'absent'}")
    print(f"Extension files: {'present' if extension.exists() else 'absent'}")
    print(f"Packaged keyd: {keyd_version() or 'not installed'}")
    try:
        group = grp.getgrnam("keyd")
        print(f"This process has keyd group: {group.gr_gid in os.getgroups() or account.pw_gid == group.gr_gid}")
    except KeyError:
        print("keyd group: absent")
    if shutil.which("gnome-extensions"):
        print(run(["gnome-extensions", "info", UUID], check=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    args = parser.parse_args()
    try:
        {"install": install, "uninstall": uninstall, "status": status}[args.action]()
    except (Error, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()

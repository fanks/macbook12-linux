#!/usr/bin/env python3
"""Install the MacBook10,1 SSD suspend workaround; never suspend or reboot."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

MODEL = "MacBook10,1"
PARAMETERS = ("mem_sleep_default=s2idle", "i915.enable_psr=0", "nvme.noacpi=1")
RULE_PATH = "/etc/udev/rules.d/80-macbook12-linux-nvme.rules"
GRUB_PATH = "/etc/default/grub.d/80-macbook12-linux-suspend.cfg"
LID_PATH = "/etc/systemd/logind.conf.d/80-macbook12-linux-lid.conf"
STATE_PATH = "/var/lib/macbook12-linux/suspend/state.json"
RULE = '''# MacBook10,1 Apple S3X NVMe: keep the SSD out of D3cold.
ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x106b", ATTR{device}=="0x2003", ATTR{[dmi/id]product_name}=="MacBook10,1", TEST=="d3cold_allowed", ATTR{d3cold_allowed}="0"
'''
GRUB = '''# Managed by macbook12-linux. Other boot options are preserved.
for _macbook12_parameter in mem_sleep_default=s2idle i915.enable_psr=0 nvme.noacpi=1; do
    case " ${GRUB_CMDLINE_LINUX:-} ${GRUB_CMDLINE_LINUX_DEFAULT:-} " in
        *" $_macbook12_parameter "*) ;;
        *) GRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT:+$GRUB_CMDLINE_LINUX_DEFAULT }$_macbook12_parameter" ;;
    esac
done
unset _macbook12_parameter
'''
LID = '''# Managed by macbook12-linux.
[Login]
HandleLidSwitch=suspend
HandleLidSwitchExternalPower=suspend
HandleLidSwitchDocked=ignore
'''
PAYLOADS = {RULE_PATH: RULE, GRUB_PATH: GRUB, LID_PATH: LID}


class Error(Exception):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Error(f"Refusing a non-regular file: {path}")
    return path.read_bytes() if path.exists() else None


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_parameter_conflicts(command_line):
    wanted = dict(parameter.split("=", 1) for parameter in PARAMETERS)
    for token in shlex.split(command_line):
        key, _, value = token.partition("=")
        if key in wanted and value != wanted[key]:
            raise Error(f"Conflicting GRUB option: {token}. Remove or update it first.")


class Installer:
    # The alternative root and runner are for isolated tests, not CLI options.
    def __init__(self, root=Path("/"), runner=subprocess.run):
        self.root = Path(root)
        self.runner = runner
        self.state_path = self.path(STATE_PATH)

    def path(self, absolute):
        return self.root / absolute.lstrip("/")

    def run(self, args):
        result = self.runner(args, check=True, text=True, capture_output=True)
        return result.stdout

    def hardware(self, installing=False):
        if self.path("/sys/class/dmi/id/product_name").read_text().strip() != MODEL:
            raise Error("This workaround supports only the 2017 MacBook12 (MacBook10,1).")
        if installing:
            release = {}
            for line in self.path("/etc/os-release").read_text().splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    release[key] = value.strip('"')
            if release.get("ID") != "debian" or release.get("VERSION_ID", "").split(".")[0] != "13":
                raise Error("Installation is supported on Debian 13 only.")
        matches = []
        for device in self.path("/sys/bus/pci/devices").iterdir():
            try:
                if (device / "vendor").read_text().strip() == "0x106b" and (device / "device").read_text().strip() == "0x2003":
                    matches.append(device)
            except FileNotFoundError:
                continue
        if len(matches) != 1:
            raise Error("Expected exactly one Apple 106b:2003 SSD; no changes made.")
        attribute = matches[0] / "d3cold_allowed"
        if not attribute.exists() or attribute.read_text().strip() not in ("0", "1"):
            raise Error("The SSD does not expose a supported d3cold_allowed setting.")
        return attribute

    def read_state(self):
        data = read_regular(self.state_path)
        if data is None:
            return None
        state = json.loads(data)
        if state.get("version") != 1 or state.get("original_d3cold") not in ("0", "1"):
            raise Error("Unrecognized suspend installation state; no changes made.")
        if not isinstance(state.get("files"), dict) or not set(state["files"]).issubset(PAYLOADS):
            raise Error("Unrecognized saved file list; no changes made.")
        if RULE_PATH not in state["files"]:
            raise Error("Incomplete suspend installation state; no changes made.")
        return state

    def save_state(self, state):
        self.state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        atomic_write(self.state_path, (json.dumps(state, indent=2) + "\n").encode(), 0o600)

    def check_files(self, state, allow_missing=False):
        for name, digest in state["files"].items():
            data = read_regular(self.path(name))
            if data is None and allow_missing:
                continue
            if data is None or sha256(data) != digest:
                raise Error(f"{name} changed since installation. Preserve your edits before uninstalling.")

    def grub_preflight(self):
        if not self.path("/etc/default/grub").is_file() or not self.path("/usr/sbin/update-grub").is_file():
            raise Error("--boot-parameters requires Debian's GRUB installation.")
        if "s2idle" not in self.path("/sys/power/mem_sleep").read_text():
            raise Error("This kernel does not offer s2idle.")
        # GRUB configuration is shell code, sourced in the same order as update-grub.
        # Preserve its contents rather than trying to rewrite shell assignments.
        main = shlex.quote(str(self.path("/etc/default/grub")))
        directory = shlex.quote(str(self.path("/etc/default/grub.d")))
        script = f'. {main}; for f in {directory}/*.cfg; do [ ! -f "$f" ] || . "$f"; done; '
        script += 'printf "%s\\n%s\\n" "${GRUB_CMDLINE_LINUX:-}" "${GRUB_CMDLINE_LINUX_DEFAULT:-}"'
        check_parameter_conflicts(self.run(["/bin/sh", "-c", script]))

    def install(self, boot_parameters=False, lid_suspend=False):
        attribute = self.hardware(installing=True)
        names = [RULE_PATH]
        if boot_parameters:
            names.append(GRUB_PATH)
        if lid_suspend:
            names.append(LID_PATH)
        state = self.read_state()
        if state:
            self.check_files(state)
            if set(state["files"]) != set(names) or state.get("phase") != "installed":
                raise Error("An installation already exists. Uninstall it before changing options.")
            print("Already installed. Run status to check the current boot.")
            return
        for name in names:
            if read_regular(self.path(name)) is not None:
                raise Error(f"Refusing to overwrite an existing unmanaged file: {name}")
        if boot_parameters:
            self.grub_preflight()
        state = {"version": 1, "phase": "installing", "original_d3cold": attribute.read_text().strip(),
                 "files": {name: sha256(PAYLOADS[name].encode()) for name in names}}
        # Save recovery information before any system configuration changes.
        self.save_state(state)
        try:
            for name in names:
                atomic_write(self.path(name), PAYLOADS[name].encode())
            self.run(["/usr/bin/udevadm", "control", "--reload-rules"])
            attribute.write_text("0\n")
            if attribute.read_text().strip() != "0":
                raise Error("The SSD did not accept d3cold_allowed=0.")
            if boot_parameters:
                self.run(["/usr/sbin/update-grub"])
            state["phase"] = "installed"
            self.save_state(state)
        except Exception:
            print("Installation stopped. Recovery data is saved; run this script with uninstall to undo partial changes.", file=sys.stderr)
            raise
        print("SSD suspend workaround installed. The PCIe root port is unchanged.")
        if boot_parameters or lid_suspend:
            print("Save your work and reboot when ready to activate the boot/lid settings.")
        print("No suspend, reboot or desktop restart was performed.")

    def uninstall(self):
        attribute = self.hardware()
        state = self.read_state()
        if state is None:
            print("No saved installation to remove.")
            return
        self.check_files(state, allow_missing=True)
        if attribute.read_text().strip() not in ("0", state["original_d3cold"]):
            raise Error("The SSD setting was changed separately. No changes made.")
        state["phase"] = "removing"
        self.save_state(state)
        for name in state["files"]:
            self.path(name).unlink(missing_ok=True)
        self.run(["/usr/bin/udevadm", "control", "--reload-rules"])
        if GRUB_PATH in state["files"]:
            self.run(["/usr/sbin/update-grub"])
        attribute.write_text(state["original_d3cold"] + "\n")
        if attribute.read_text().strip() != state["original_d3cold"]:
            raise Error("Could not restore the SSD setting. Recovery data was retained.")
        self.state_path.unlink()
        print("Removed the installed files and restored the prior SSD setting.")
        print("Reboot when ready. Boot/lid settings in this session remain in effect until then.")
        print("The previous failure to wake may return after removing this workaround.")

    def status(self):
        attribute = self.hardware()
        state = self.read_state()
        print(f"Model: {MODEL}; Apple SSD: {attribute.parent.name}")
        print(f"SSD d3cold_allowed: {attribute.read_text().strip()} (workaround: 0)")
        print(f"Current sleep mode: {self.path('/sys/power/mem_sleep').read_text().strip()}")
        if state:
            self.check_files(state)
            print(f"Saved installation: {state['phase']}; installed files match.")
        else:
            print("Saved installation: none.")
        tokens = shlex.split(self.path("/proc/cmdline").read_text())
        missing = [value for value in PARAMETERS if value not in tokens]
        if missing:
            print("Tested boot settings not active: " + " ".join(missing))
            if state and GRUB_PATH in state["files"]:
                print("Reboot to activate the installed boot settings.")
        else:
            print("All three tested boot settings are active.")


@contextlib.contextmanager
def lock():
    fd = os.open("/run/lock/macbook12-linux-suspend.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    install = sub.add_parser("install")
    install.add_argument("--boot-parameters", action="store_true", help="also configure the three tested GRUB boot options")
    install.add_argument("--lid-suspend", action="store_true", help="also configure lid suspend on battery and mains power")
    sub.add_parser("uninstall")
    sub.add_parser("status")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo, including status (installation state is private).")
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    os.environ["LC_ALL"] = "C"
    try:
        with lock():
            installer = Installer()
            if args.action == "install":
                installer.install(args.boot_parameters, args.lid_suspend)
            else:
                getattr(installer, args.action)()
    except (Error, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.strip(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

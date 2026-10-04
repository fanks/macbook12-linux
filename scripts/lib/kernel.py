#!/usr/bin/env python3
"""Build and install narrowly scoped Debian 6.12 module overrides.

Original helper code: MIT. Downloaded kernel/driver code retains its own license.
No module loading, device unbinding, service restart, or reboot is performed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

os.environ.update(PATH="/usr/sbin:/usr/bin:/sbin:/bin", LC_ALL="C")
AUDIO_COMMIT = "4cdfcdbac2db3f300cc45d9679cfd21df6590a8a"
AUDIO_FILES = {
    "patch_cirrus.c": "86acea4fe83d1ae2e2711e14dda994c218e6e05070d946bdff84a4e8e5ff4a7e",
    "patch_cirrus_a1534_setup.h": "cc77fb60e44cef0bbe689deb52dbd18fb571c02a8e70599612f388907e359544",
    "patch_cirrus_a1534_pcm.h": "3f21e6876250085af0eeef0c8c6173e52fea42b73d166d7959927cadc5334f31",
}
SOFTVOL = Path("/etc/wireplumber/wireplumber.conf.d/51-macbook12-linux-softvol.conf")
SOFTVOL_TEXT = """monitor.alsa.rules = [
  {
    matches = [ { device.name = "alsa_card.pci-0000_00_1f.3" } ]
    actions = { update-props = { api.alsa.soft-mixer = true } }
  }
]
"""
COMPONENTS = {
    "audio": ("snd_hda_codec_cirrus", "snd-hda-codec-cirrus.ko", "sound/pci/hda"),
    "bluetooth": ("hci_uart", "hci_uart.ko", "drivers/bluetooth"),
}


def fail(message):
    raise RuntimeError(message)


def run(*args, cwd=None, capture=True):
    result = subprocess.run([str(a) for a in args], cwd=cwd, check=True,
                            text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def regular(path):
    if not path.is_file() or path.is_symlink():
        fail(f"Expected a regular non-symlink file: {path}")


def platform(kernel):
    if Path("/sys/class/dmi/id/product_name").read_text().strip() != "MacBook10,1":
        fail("Only the tested 2017 MacBook10,1 is supported.")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if release.get("ID", "").strip('"') != "debian" or release.get("VERSION_ID", "").strip('"') != "13":
        fail("These scripts require Debian 13.")
    if not re.fullmatch(r"6\.12\.[0-9]+[+A-Za-z0-9.~-]*-amd64", kernel):
        fail("Only Debian 6.12 amd64 kernels are supported; review later kernels separately.")
    if run("dpkg", "--print-architecture") != "amd64":
        fail("Expected Debian amd64.")


def package(name, field):
    if run("dpkg-query", "-W", "-f=${db:Status-Abbrev}", name) != "ii":
        fail(f"Required package is not installed: {name}")
    return run("dpkg-query", "-W", "-f=${" + field + "}", name)


def stock_module(kernel, component):
    _, filename, subdir = COMPONENTS[component]
    base = Path("/lib/modules") / kernel / "kernel" / subdir
    found = [p for p in base.glob(filename + "*") if p.name in (filename, filename + ".xz", filename + ".zst", filename + ".gz")]
    if len(found) != 1:
        fail(f"Expected exactly one original {filename} under {base}")
    regular(found[0])
    return found[0]


def module_info(path, field):
    return run("modinfo", "-F", field, path)


def selected(kernel, component):
    return Path(run("modinfo", "-k", kernel, "-n", COMPONENTS[component][0])).resolve()


def image_members(kernel, component):
    filename = COMPONENTS[component][1]
    pattern = "".join("[-_]" if c in "-_" else re.escape(c) for c in filename)
    expression = re.compile(r"(^|/)" + pattern + r"(?:\.(?:xz|zst|gz))?$")
    return [line for line in run("lsinitramfs", Path("/boot") / ("initrd.img-" + kernel)).splitlines() if expression.search(line)]


def verify_image(kernel, component, expected):
    if len(image_members(kernel, component)) != 1:
        fail("Rebuilt initramfs must contain exactly one matching module.")
    with tempfile.TemporaryDirectory(prefix="verify-initramfs-", dir="/var/tmp") as temporary:
        root = Path(temporary)
        root.chmod(0o700)  # An initramfs can contain disk-unlock material.
        run("unmkinitramfs", Path("/boot") / ("initrd.img-" + kernel), root, capture=False)
        matches = [p for p in root.rglob("*.ko*") if p.name == expected.name]
        if len(matches) != 1 or digest(matches[0]) != digest(expected):
            fail("The rebuilt initramfs does not contain the expected exact module.")


def atomic_copy(source, destination, mode=0o644):
    fd, temporary = tempfile.mkstemp(prefix=".macbook12-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def secure_dir(path, mode=0o700):
    missing = []
    current = path
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=mode)
        directory.chmod(mode)
    validate_secure_dir(path)


def validate_secure_dir(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        fail(f"Unsafe root-owned directory: {path}")


def secure_boot():
    output = run("mokutil", "--sb-state")
    if "SecureBoot enabled" in output:
        fail("Secure Boot is enabled. This project does not enroll keys or sign modules.")
    lockdown = Path("/sys/kernel/security/lockdown")
    if lockdown.exists() and "[none]" not in lockdown.read_text():
        fail("Kernel lockdown is enabled; unsigned modules are not supported here.")
    enforce = Path("/sys/module/module/parameters/sig_enforce")
    if enforce.exists() and enforce.read_text().strip().lower() in ("y", "1"):
        fail("The kernel requires signed modules.")


def build(args):
    if os.geteuid() == 0:
        fail("Build as your normal user, without sudo.")
    kernel = os.uname().release
    platform(kernel)
    for command in ("gcc-14", "make", "curl", "apt-get", "dpkg-deb", "tar", "modinfo"):
        if not shutil.which(command):
            fail(f"Missing prerequisite: {command}; see docs/{args.component}.md")
    headers = "linux-headers-" + kernel
    image = "linux-image-" + kernel
    if package(headers, "source:Package") != "linux":
        fail("Expected headers from Debian's linux source package.")
    version = package(headers, "source:Version")
    if package(headers, "Version") != package(image, "Version"):
        fail("Image and header package versions differ. Install matching versions and boot that kernel first.")
    config = Path("/boot/config-" + kernel).read_text()
    if not re.search(r"^CONFIG_GCC_VERSION=14[0-9]{4}$", config, re.M):
        fail("Expected a kernel built with GCC 14; review compiler compatibility first.")
    destination = args.directory.expanduser().absolute()
    destination.mkdir(mode=0o700)  # Deliberately refuses an existing folder.
    if args.source_deb:
        archive = args.source_deb.expanduser().resolve()
    else:
        run("apt-get", "download", "linux-source-6.12=" + version, cwd=destination, capture=False)
        packages = list(destination.glob("linux-source-6.12_*.deb"))
        if len(packages) != 1:
            fail("Expected one downloaded Debian source package.")
        archive = packages[0]
    regular(archive)
    if run("dpkg-deb", "-f", archive, "Package") != "linux-source-6.12" or run("dpkg-deb", "-f", archive, "Version") != version:
        fail("Source package must match the installed headers' exact source version: " + version)
    run("dpkg-deb", "-x", archive, destination / "package", capture=False)
    _, filename, subdir = COMPONENTS[args.component]
    run("tar", "-xJf", destination / "package/usr/src/linux-source-6.12.tar.xz", "-C", destination,
        "linux-source-6.12/" + subdir, capture=False)
    source = destination / "linux-source-6.12" / subdir
    if args.component == "audio":
        for name, checksum in AUDIO_FILES.items():
            output = source / name
            run("curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2", "--silent", "--show-error",
                "https://raw.githubusercontent.com/leifliddy/macbook12-audio-driver/" + AUDIO_COMMIT + "/patch_cirrus/" + name,
                "--output", output, capture=False)
            if digest(output) != checksum:
                fail("Pinned upstream file checksum mismatch: " + name)
        patch_file = source / "patch_cirrus.c"
        text = patch_file.read_text()
        text = re.sub(r"\bsnd_pci_quirk\b", "hda_quirk", text)
        text = re.sub(r"\bSND_PCI_QUIRK\b", "HDA_CODEC_QUIRK", text)
        patch_file.write_text(text)
        (source / "Kbuild").write_text("obj-m := snd-hda-codec-cirrus.o\nsnd-hda-codec-cirrus-objs := patch_cirrus.o\n")
    else:
        path = source / "hci_bcm.c"
        text = path.read_text()
        # Exact Linux 6.12 blocks from leifliddy's pinned patch, no broad deletion.
        for block in ("\terr = dev->set_device_wakeup(dev, powered);\n\tif (err)\n\t\tgoto err_revert_shutdown;\n\n",
                      "err_revert_shutdown:\n\tdev->set_shutdown(dev, !powered);\n"):
            if text.count(block) != 1:
                fail("Bluetooth source layout changed; refusing to guess a patch.")
            text = text.replace(block, "", 1)
        path.write_text(text)
        lines = [line for line in (source / "Makefile").read_text().splitlines() if line.startswith("hci_uart-")]
        if not any("hci_bcm.o" in line for line in lines) or any(line.endswith("\\") for line in lines):
            fail("Bluetooth Kbuild layout changed; review required.")
        (source / "Kbuild").write_text("obj-m := hci_uart.o\n" + "\n".join(lines) + "\n")
    run("make", "-C", "/lib/modules/" + kernel + "/build", "M=" + str(source), "CC=gcc-14", "modules", capture=False)
    candidate = destination / filename
    shutil.copyfile(source / filename, candidate)
    original = stock_module(kernel, args.component)
    if module_info(candidate, "vermagic") != module_info(original, "vermagic"):
        fail("Built module vermagic does not match the original.")
    manifest = {"format": 1, "component": args.component, "kernel": kernel, "source_version": version,
                "candidate_sha256": digest(candidate), "stock_sha256": digest(original),
                "source_deb_sha256": digest(archive)}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Built only; no driver was loaded or installed.\nBuild directory: {destination}\nSHA256: {digest(candidate)}")


def install(args):
    kernel = os.uname().release
    platform(kernel)
    secure_boot()
    source = args.directory.expanduser().resolve()
    regular(source / "manifest.json")
    manifest = json.loads((source / "manifest.json").read_text())
    if (manifest.get("format"), manifest.get("component"), manifest.get("kernel")) != (1, args.component, kernel):
        fail("Build manifest does not match this component and running kernel.")
    module, filename, _ = COMPONENTS[args.component]
    candidate = source / filename
    regular(candidate)
    original = stock_module(kernel, args.component)
    if digest(candidate) != manifest["candidate_sha256"] or digest(original) != manifest["stock_sha256"]:
        fail("Candidate or original module differs from the build manifest.")
    if module_info(candidate, "name") != module or module_info(candidate, "vermagic") != module_info(original, "vermagic"):
        fail("Candidate name or vermagic mismatch.")
    destination = Path("/lib/modules") / kernel / "updates/macbook12-linux" / filename
    state = Path("/var/lib/macbook12-linux/kernel") / kernel / args.component
    contains = bool(image_members(kernel, args.component))
    if contains and not args.update_initramfs:
        fail("The normal initramfs already includes this module. Re-run with --update-initramfs after reading the guide.")
    if args.component == "audio":
        if not package("wireplumber", "Version").startswith("0.5."):
            fail("The audio configuration requires WirePlumber 0.5.")
        codecs = [p.read_text() for p in Path("/proc/asound").glob("card*/codec#*")]
        if not any("Vendor Id: 0x10134208" in t and "Subsystem Id: 0x106b6600" in t for t in codecs):
            fail("Expected the tested CS4208 codec and subsystem 106b6600. Resolve SOF/blacklist experiments first.")
        if SOFTVOL.exists() or SOFTVOL.is_symlink():
            regular(SOFTVOL)
            if SOFTVOL.read_text() != SOFTVOL_TEXT:
                fail("Existing soft-volume configuration differs; it will not be overwritten.")
    if destination.exists() or destination.is_symlink():
        regular(destination)
        if args.component == "audio" and not SOFTVOL.exists():
            fail("The installed audio override is missing its required soft-volume file; restore it before use.")
        if digest(destination) == manifest["candidate_sha256"] and selected(kernel, args.component) == destination.resolve() and state.is_dir():
            validate_secure_dir(state)
            regular(state / "manifest.json")
            regular(state / "COMPLETE")
            if json.loads((state / "manifest.json").read_text()) != manifest:
                fail("Existing installation metadata differs; inspect or roll back first.")
            if contains:
                verify_image(kernel, args.component, candidate)
            print("This exact override is already installed. No changes made.")
            return
        fail("An override already exists; roll it back or inspect it first.")
    if selected(kernel, args.component) != original.resolve():
        fail("A different override is selected; it will not be replaced.")
    if state.exists() or state.is_symlink():
        fail(f"Existing installation metadata requires review: {state}")
    if args.dry_run:
        print(f"Would install {candidate} as {destination}; initramfs rebuild: {contains}. No changes made.")
        return
    secure_dir(state.parent)
    secure_dir(state)
    shutil.copyfile(candidate, state / filename)
    (state / filename).chmod(0o600)
    if digest(state / filename) != manifest["candidate_sha256"]:
        fail("Candidate changed while making the protected copy.")
    boot = Path("/boot/initrd.img-" + kernel)
    if contains:
        shutil.copyfile(boot, state / "initrd.before")
        (state / "initrd.before").chmod(0o600)
    (state / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    changed_image = False
    try:
        if args.component == "audio" and not SOFTVOL.exists():
            secure_dir(SOFTVOL.parent, 0o755)
            with SOFTVOL.open("x") as output:
                output.write(SOFTVOL_TEXT)
            SOFTVOL.chmod(0o644)
        secure_dir(destination.parent, 0o755)
        atomic_copy(state / filename, destination)
        run("depmod", "-a", kernel, capture=False)
        if selected(kernel, args.component) != destination.resolve():
            fail("depmod did not select the new override.")
        if contains:
            changed_image = True
            run("update-initramfs", "-u", "-k", kernel, capture=False)
            verify_image(kernel, args.component, destination)
        if digest(original) != manifest["stock_sha256"]:
            fail("Original module unexpectedly changed.")
        (state / "COMPLETE").write_text("Installed; activation requires a new boot.\n")
    except BaseException:
        try:
            if destination.is_file() and not destination.is_symlink() and digest(destination) == manifest["candidate_sha256"]:
                destination.unlink()
                run("depmod", "-a", kernel, capture=False)
        finally:
            if changed_image:
                atomic_copy(state / "initrd.before", boot, 0o600)
        state.rename(state.with_name(state.name + ".failed-" + str(time.time_ns())))
        raise
    print(f"Installed for {kernel} only. Original modules remain intact.\nMetadata: {state}\nNo module was reloaded and no reboot was requested. Read the first-boot checks in the guide.")


def rollback(args):
    kernel = args.kernel or os.uname().release
    platform(kernel)
    _, filename, _ = COMPONENTS[args.component]
    destination = Path("/lib/modules") / kernel / "updates/macbook12-linux" / filename
    original = stock_module(kernel, args.component)
    state = Path("/var/lib/macbook12-linux/kernel") / kernel / args.component
    had_override = destination.exists() or destination.is_symlink()
    contains = bool(image_members(kernel, args.component))
    if not had_override:
        if selected(kernel, args.component) != original.resolve():
            fail("Override absent, but a different module is selected; inspect depmod configuration.")
        if not state.exists() and not state.is_symlink():
            if contains:
                # Never report recovery based only on the module index.
                verify_image(kernel, args.component, original)
            print("Override absent; original module selected and boot image checked. No changes made.")
            return
    else:
        regular(destination)
    validate_secure_dir(state)
    regular(state / "manifest.json")
    manifest = json.loads((state / "manifest.json").read_text())
    if (manifest.get("format"), manifest.get("component"), manifest.get("kernel")) != (1, args.component, kernel):
        fail("Protected rollback metadata does not match this component and kernel.")
    if (had_override and digest(destination) != manifest["candidate_sha256"]) or digest(original) != manifest["stock_sha256"]:
        fail("Installed or original module changed; refusing to remove an unrelated file.")
    if had_override and selected(kernel, args.component) != destination.resolve():
        fail("A different override is selected; inspect before rollback.")
    if contains and not args.update_initramfs:
        fail("The initramfs contains the module; rollback requires --update-initramfs.")
    if args.dry_run:
        print(f"Would remove {destination}; initramfs rebuild: {contains}. No changes made.")
        return
    regular(state / filename)
    if digest(state / filename) != manifest["candidate_sha256"]:
        fail("Protected candidate backup changed.")
    boot = Path("/boot/initrd.img-" + kernel)
    if contains:
        shutil.copyfile(boot, state / "initrd.before-rollback")
        (state / "initrd.before-rollback").chmod(0o600)
    changed_image = False
    try:
        if had_override:
            destination.unlink()
        run("depmod", "-a", kernel, capture=False)
        if selected(kernel, args.component) != original.resolve():
            fail("depmod did not select the original module.")
        if contains:
            changed_image = True
            run("update-initramfs", "-u", "-k", kernel, capture=False)
            verify_image(kernel, args.component, original)
    except BaseException:
        try:
            if had_override:
                atomic_copy(state / filename, destination)
            run("depmod", "-a", kernel, capture=False)
        finally:
            if changed_image:
                atomic_copy(state / "initrd.before-rollback", boot, 0o600)
        raise
    state.rename(state.with_name(state.name + ".rolled-back-" + str(time.time_ns())))
    print(f"Original module selected for {kernel}. The running module is unchanged until a new boot.\nSoftware-volume configuration is retained; no reboot was requested.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=COMPONENTS)
    commands = parser.add_subparsers(dest="action", required=True)
    p = commands.add_parser("build", help="Build unprivileged into a NEW directory; never install or load")
    p.add_argument("directory", type=Path)
    p.add_argument("--source-deb", type=Path, help="Already downloaded exact Debian linux-source-6.12 .deb (otherwise apt downloads it)")
    p = commands.add_parser("install", help="Install an override for the running kernel; never reload/reboot")
    p.add_argument("directory", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--update-initramfs", action="store_true", help="Allow a backed-up rebuild only when initramfs already includes the module")
    p = commands.add_parser("rollback", help="Remove only this project's hash-checked override; never reload/reboot")
    p.add_argument("--kernel", help="Installed 6.12 kernel to restore (default: running kernel)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--update-initramfs", action="store_true")
    args = parser.parse_args()
    if args.action == "build":
        build(args)
    else:
        if os.geteuid() != 0:
            fail("Install and rollback require sudo (including their read-only --dry-run checks).")
        os.umask(0o077)
        # Shared lock also serializes audio/BT updates of the same initramfs.
        fd = os.open("/run/lock/macbook12-linux-kernel.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "r+") as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                fail("Unsafe pre-existing kernel-operation lock file.")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (install if args.action == "install" else rollback)(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)

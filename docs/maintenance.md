# Updates and troubleshooting

## Kernel updates

Keep installing Debian security updates. Audio and Bluetooth overrides are built **for one kernel at a time**, with the original Debian modules left in place. A new kernel will use its original modules until you rebuild the fixes for it.

After booting the new kernel, repeat the build/install steps in [Audio](audio.md) and [Bluetooth](bluetooth.md), then shut down fully and power on again. These scripts support the Debian 6.12 series and require matching image, headers and source packages. If a compatibility check fails, use the original drivers until the repository supports that version. Do not hold back security updates to keep a custom module working.

## GNOME or libinput updates

The keyboard extension supports GNOME 48. The trackpad wrapper additionally requires libinput 1.28.1 and Wayland. Do not edit version checks to force a newer version: the desktop hooks need review and testing first. The trackpad wrapper starts ordinary GNOME when its checks fail.

Update this repository with:

```sh
git pull --ff-only
```

Then use the relevant guide's install/update command. Keep local edits on a separate branch if you customize the scripts.

## Quick checks

```sh
python3 scripts/check-system.py
systemctl --user is-active pipewire wireplumber
bluetoothctl show
cat /sys/power/mem_sleep
~/.local/bin/trackpad-curve --status
```

For a feature that failed, follow its guide's check and removal steps. `modinfo -n` shows which driver will load **next boot**; it does not prove which module is currently running. Shut down fully and power on after installing or removing an audio/Bluetooth override. Never switch the Cirrus codec with `rmmod`, `insmod` or a live unbind.

## Sleep problems

Save your work before testing suspend, and stay beside the computer. A dark screen after wake is not always a failed resume: first try a key or the trackpad. See [Suspend](suspend.md) for the tested baseline and the SSD power-setting check.

If the machine stops responding completely, a forced power-off may be needed. After restarting and unlocking the disk, inspect the previous boot's kernel journal locally:

```sh
sudo journalctl -b -1 -k
```

Only share the relevant error lines in an issue. Full logs can contain device addresses and other identifying data.

## Removal

Use each feature's removal instructions. Each installer tracks its own files and settings; do not delete all of `/etc/keyd`, GNOME extensions or systemd overrides. Removal scripts preserve changed files instead of silently overwriting later customizations.

For a trackpad-related login problem, use the [text-console recovery steps](trackpad.md#recovery-if-gnome-will-not-start).

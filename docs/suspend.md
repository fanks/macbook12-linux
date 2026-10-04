# Suspend and wake

For **Debian 13 on the 2017 12-inch MacBook (`MacBook10,1`) with Apple SSD `106b:2003`**. Other models are untested and the installer refuses them.

## Install

From the cloned repository, run:

```sh
sudo python3 scripts/suspend.py install --boot-parameters --lid-suspend
```

Save your work, then reboot normally. The installer does not reboot or put the machine to sleep.

After signing in, check the setup:

```sh
sudo python3 scripts/suspend.py status
```

Look for SSD `d3cold_allowed: 0`, `[s2idle]`, and “All three tested boot settings are active.” Then save your work, close the lid for about 15 seconds and open it. If necessary, press a key to wake the screen. Check sound and Bluetooth too. Stay at the computer for this first test; a failure to wake can require a forced shutdown.

An encrypted Debian installation works with this form of suspend. You do not need to change swap, disk encryption or `resume=` settings. This guide configures suspend, not hibernation.

## What it changes

- Keeps the matching Apple SSD out of D3cold, a state in which it can fail to return after suspend. The SSD is found by its hardware IDs, so its PCI address may differ between machines. The parent PCIe port is left alone.
- Adds `mem_sleep_default=s2idle i915.enable_psr=0 nvme.noacpi=1` through a separate GRUB configuration file. Existing unrelated boot options are kept. A conflicting value for one of these options stops installation before any changes.
- Makes closing the lid suspend on battery and mains power. With an external display or dock, the lid is ignored. Debian normally already uses this behavior; `--lid-suspend` makes it explicit.

**Tested combination:** Debian 13, Linux 6.12, these three boot options and the SSD setting. Resume succeeded in two real suspend tests, including a lid-close test with screen, sound and Bluetooth checked afterwards. The three boot options were already in use before the SSD fix was tested; the SSD fix alone on fresh Debian defaults has not been verified. Battery impact has not been measured.

The reusable installer has isolated filesystem tests; installing it on a fresh machine still needs hardware verification.

If you already manage your own boot and lid settings, omit either option:

```sh
# Only install the SSD rule; leave GRUB and lid settings alone.
sudo python3 scripts/suspend.py install
```

Choose the options on the first install. To change them later, uninstall and install again. Re-running the same installation is safe.

## Undo

```sh
sudo python3 scripts/suspend.py uninstall
```

Reboot when ready. The previous wake problem may return after removing the workaround.

The script saves the previous SSD setting in `/var/lib/macbook12-linux/suspend/state.json`. It refuses to overwrite existing unmanaged files, and uninstall checks each installed file's hash before removing anything. If you edited one, preserve the edit and restore the installed version before retrying. Recovery data is retained if installation or removal fails, so you can retry `uninstall` after resolving the reported error.

Installed configuration files:

```text
/etc/udev/rules.d/80-macbook12-linux-nvme.rules
/etc/default/grub.d/80-macbook12-linux-suspend.cfg       # --boot-parameters
/etc/systemd/logind.conf.d/80-macbook12-linux-lid.conf   # --lid-suspend
```

## If it still fails

After recovering, check the setup and the previous boot's kernel messages:

```sh
sudo python3 scripts/suspend.py status
sudo journalctl -b -1 -k --no-pager | grep -Ei 'nvme|timeout|suspend|resume|I/O error'
```

Previous-boot logs may be unavailable if persistent logging is disabled. If the lid does nothing, inspect effective settings and inhibitors:

```sh
systemd-analyze cat-config systemd/logind.conf
systemd-inhibit --list
```

GNOME or another application can inhibit lid handling. The installer leaves running desktop sessions alone; reboot after changing lid settings.

## References

- [Linux sleep states](https://docs.kernel.org/admin-guide/pm/sleep-states.html): suspend keeps the session in memory; hibernation uses storage.
- [Linux 6.12 boot parameters](https://docs.kernel.org/6.12/admin-guide/kernel-parameters.html): `mem_sleep_default` selects the suspend mode.
- [Linux 6.12 display parameters](https://github.com/torvalds/linux/blob/v6.12/drivers/gpu/drm/i915/display/intel_display_params.c): `i915.enable_psr=0` disables panel self refresh.
- [Linux 6.12 NVMe driver](https://github.com/torvalds/linux/blob/v6.12/drivers/nvme/host/pci.c): `nvme.noacpi` disables the driver's ACPI BIOS quirks.
- [Debian 13 lid handling](https://manpages.debian.org/trixie/systemd/logind.conf.5.en.html): lid actions and desktop inhibitors.

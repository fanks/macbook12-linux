# Bluetooth

For **MacBook10,1 (12-inch, 2017)** with **Debian 13 amd64** and Debian's **6.12** kernel built with GCC 14. The tested BCM4350C0 controller failed with baud-rate/reset timeouts (`0xfc18`, `-110`). The patch worked after a normal boot and the tested suspend/resume cycle. Other models and kernel series are not validated.

## 1. Prepare and build

Run from the repository root on the MacBook. If an update installed a newer kernel, reboot into it first.

```sh
sudo apt update
sudo apt install build-essential gcc-14 binutils curl xz-utils python3 kmod \
  initramfs-tools mokutil bluez "linux-headers-$(uname -r)"
bt_build="$HOME/macbook12-bluetooth-$(uname -r)"
./scripts/bluetooth-build.sh "$bt_build"
```

Build without sudo into a **new** directory. The script requires matching image/headers and downloads their exact Debian source version. If unavailable, update Debian and boot the matching kernel first. Never substitute an approximate upstream tarball. Secure Boot/key enrollment is not supported.

## 2. Install for the next boot

```sh
sudo ./scripts/bluetooth-install.sh --dry-run --update-initramfs "$bt_build"
sudo ./scripts/bluetooth-install.sh --update-initramfs "$bt_build"
```

The original module remains intact. The replacement is `/lib/modules/<kernel>/updates/macbook12-linux/hci_uart.ko`.

If the normal initramfs already includes this module, `--update-initramfs` permits a protected backup/rebuild and verifies the included candidate. Otherwise the image is untouched. Allow space for the backup and temporary extraction. No GRUB/encryption settings, firmware, pairings or running drivers are changed.

Save work, **shut down fully**, then power on manually. Hot reload after failed initialization did not fix the tested controller. The scripts never reboot automatically.

## 3. Check Bluetooth

```sh
modinfo -n hci_uart
cat /sys/module/hci_uart/initstate
rfkill list bluetooth
bluetoothctl list
bluetoothctl show
sudo journalctl -b -k --no-pager | grep -Ei 'Bluetooth|hci_uart|BCM|0xfc18'
```

After the fresh boot, expect the override path, state `live`, a valid controller address and `Powered: yes`. Enable Bluetooth in GNOME Settings if switched off. An optional `bluetoothctl scan on` should be followed by `bluetoothctl scan off`.

If `-110` returns, save the boot log and roll back. A `BCM.hcd` warning alone does not justify downloading arbitrary firmware: it also occurred during a working boot. Test suspend/resume separately.

## 4. Roll back or update the kernel

```sh
sudo ./scripts/bluetooth-rollback.sh --dry-run --update-initramfs
sudo ./scripts/bluetooth-rollback.sh --update-initramfs
```

Then shut down and power on. Only this project's hash-matched override is removed; the boot image is checked/rebuilt when needed. Audio, Wi-Fi and pairings stay intact. From another working kernel, add `--kernel AFFECTED_KERNEL_RELEASE`.

Repeat build/install after every kernel update. **This is not DKMS.** Keep a previously working kernel available.

<details>
<summary>Patch, source and recovery details</summary>

The build reproduces the six-line deletion in [leifliddy's pinned installer d393bf9](https://github.com/leifliddy/macbook12-bluetooth-driver/blob/d393bf988644f0ce24f57c031febe0d9a4256f89/install.bluetooth.sh), based on [Christoph Gysin's original patch](https://github.com/christophgysin/linux/commit/ddf622a0a19697af473051c8019fffc1eb66efe7). It removes a wakeup call/error path from `bcm_gpio_set_power`; separate suspend/resume handling is retained. Exact source blocks must match or the build stops.

Only `hci_uart` is built from the matching Debian source. Kernel source retains its original licenses and is downloaded, not vendored/relicensed. `--source-deb /path/to/exact-package.deb` accepts an authentic previously downloaded package. Protected metadata/backups are under `/var/lib/macbook12-linux/kernel/<kernel>/bluetooth/`. Keep initramfs backups private because they may contain disk-unlock material.

References: [kernel external modules](https://docs.kernel.org/kbuild/modules.html), [Debian update-initramfs](https://manpages.debian.org/trixie/initramfs-tools/update-initramfs.8.en.html).

</details>

# Fresh Debian installation

Use **Debian 13 amd64 with GNOME** on a **2017 MacBook10,1**. These scripts configure an installed system; they do not partition or erase disks.

## Install Debian

1. Back up anything you want to keep.
2. Download an official [Debian installation image](https://www.debian.org/distrib/). Follow Debian's [USB installation instructions](https://www.debian.org/releases/trixie/amd64/ch04s03.en.html) to write it to a USB drive.
3. Connect the drive through a USB-C adapter. Start the Mac while holding **Option/Alt**, then select the USB's **EFI Boot** entry.
4. Follow the installer. Choose **GNOME** and **standard system utilities** when selecting software. Choose your keyboard layout normally.
5. Let the installer handle firmware when offered. If Wi-Fi is unavailable, use a USB Ethernet adapter or USB tethering to finish installation.
6. Reboot, sign in to GNOME and open Terminal. On the login screen, choose the ordinary **GNOME** session, not **GNOME on Xorg**.

For the partitioning and encryption choices, follow the [Debian installation guide](https://www.debian.org/releases/trixie/amd64/). “Use entire disk” erases that disk; choose a layout suitable for what you intend to keep.

## Prepare the system

The examples use `sudo`. If you left the installer's root password empty, Debian normally gives the first user sudo access. Otherwise, as root, install `sudo` and add your account to the `sudo` group, then sign out and in.

```sh
sudo apt update
sudo apt full-upgrade
sudo apt install git
```

Save your work and reboot into the updated kernel. Then:

```sh
git clone https://github.com/fanks/macbook12-linux.git
cd macbook12-linux
python3 scripts/check-system.py
```

Continue with the [feature guides](../README.md#start-here). Commands containing `sudo` change system files. Run the keyboard and trackpad installers **without sudo** from your own GNOME Terminal; they request only the system access they need.

## Wi-Fi

This project does not replace the Wi-Fi driver. If Wi-Fi already works, leave it alone. The tested machine uses `brcmfmac` with Debian's firmware. If firmware is missing, enable Debian's `non-free-firmware` repository component and install:

```sh
sudo apt update
sudo apt install firmware-brcm80211
```

Then restart. See [Debian's firmware guide](https://www.debian.org/releases/trixie/amd64/ch06s04.en.html) if the installer could not load it. Do not download random firmware files or blacklist DMA devices based on advice for a different MacBook model.

## Encrypted disks

Debian's normal encrypted installation is compatible with the tested setup. Enter your disk passphrase during boot as usual. The suspend guide covers **sleep to RAM**, not hibernation; it does not need a resume partition or changes to disk encryption. Cmd-key mappings load after login and do not affect the disk-unlock screen.

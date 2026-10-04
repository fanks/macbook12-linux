# Internal speakers

For **MacBook10,1 (12-inch, 2017)** running **Debian 13 amd64**, a Debian **6.12** kernel built with GCC 14, and **WirePlumber 0.5**. Speakers and volume control worked on the tested machine. Headphone switching with the replacement remains unverified. Other models and kernel series are not supported by these scripts.

**Do not unload or hot-swap the audio driver.** Doing so caused a kernel Oops during testing. Installation changes the next boot only; activation needs a full shutdown and power on.

## 1. Install prerequisites

Run from the repository root on the MacBook. If Debian installed a newer kernel, reboot into it first. Keep a working kernel available in GRUB and save your work.

```sh
sudo apt update
sudo apt install build-essential gcc-14 binutils curl xz-utils python3 kmod \
  initramfs-tools mokutil "linux-headers-$(uname -r)" \
  wireplumber pipewire-pulse alsa-utils sound-theme-freedesktop
```

The scripts require matching image, headers and exact Debian source-package versions. If that source version is unavailable, update Debian and boot its matching kernel first. Previous SOF/NHLT or driver-blacklist experiments need separate review. Secure Boot/key enrollment is not supported.

## 2. Build, then install

Build as your normal user into a **new** directory:

```sh
audio_build="$HOME/macbook12-audio-$(uname -r)"
./scripts/audio-build.sh "$audio_build"
sudo ./scripts/audio-install.sh --dry-run --update-initramfs "$audio_build"
```

Close media players, mute the internal output and lower its desktop volume to approximately 5%. Then install:

```sh
sudo ./scripts/audio-install.sh --update-initramfs "$audio_build"
```

The original module stays intact. The replacement goes under `/lib/modules/<kernel>/updates/macbook12-linux/`. The installer also adds the required **software-volume** rule for the internal audio card. Existing conflicting files are not overwritten.

`--update-initramfs` permits a backed-up rebuild **only if** the boot image already contains Cirrus. Otherwise that image is untouched. Rebuilt images are checked against the candidate; failure restores the previous image and module selection. Allow free disk space for the backup and temporary extraction. No GRUB/encryption settings are changed, and no reboot happens automatically.

## 3. First boot at low volume

Shut down fully, then power on. For this first check, press **e** on Debian's ordinary GRUB entry and append `systemd.unit=multi-user.target` to the `linux` line. Boot with Ctrl-X/F10. This one-time text-mode boot prevents desktop autoplay. Unlock the disk and log in as your normal user.

```sh
modinfo -n snd_hda_codec_cirrus
cat /sys/module/snd_hda_codec_cirrus/initstate
sudo journalctl -b -k --no-pager | grep -Ei 'oops|BUG:|cirrus|cs4208'
systemctl --user start pipewire.service pipewire-pulse.service wireplumber.service
wpctl status
```

Expect the override path and `live`. Stop if there is a new Oops/BUG. In `wpctl status`, find the internal audio **device ID** and analog speaker **sink ID**. Replace `DEVICE_ID` and `SINK_ID` below with those numbers:

```sh
wpctl inspect DEVICE_ID | grep 'api.alsa.soft-mixer'
wpctl set-volume SINK_ID 0.05
wpctl set-mute SINK_ID 1
```

Require `api.alsa.soft-mixer = "true"` before unmuting. Then:

```sh
wpctl set-mute SINK_ID 0
wpctl set-default SINK_ID
pw-play --volume=0.05 /usr/share/sounds/freedesktop/stereo/audio-volume-change.oga
```

Check both speakers and low-volume adjustment. Direct ALSA playback bypasses software volume; do not run full-scale tests against `hw:0`. Afterward start the login screen:

```sh
sudo systemctl start graphical.target
```

Check desktop volume before opening media. The greeter has its own volume state. Test headphone switching separately. The next ordinary boot returns to the normal graphical target.

## 4. Roll back or update the kernel

```sh
sudo ./scripts/audio-rollback.sh --dry-run --update-initramfs
sudo ./scripts/audio-rollback.sh --update-initramfs
```

Then shut down fully and power on. Rollback removes only this project's hash-matched replacement and checks/rebuilds the boot image when necessary. Software volume is retained while the replacement may still be running. For recovery from another kernel, add `--kernel AFFECTED_KERNEL_RELEASE`.

After each kernel update, repeat build/install for the newly running kernel. **This is not DKMS.** Keep the previous working kernel until checks pass.

<details>
<summary>Sources and build details</summary>

Audio files are fetched from [leifliddy's pinned commit 4cdfcdb](https://github.com/leifliddy/macbook12-audio-driver/tree/4cdfcdbac2db3f300cc45d9679cfd21df6590a8a), checked by source SHA256, and combined with exact Debian HDA source/headers. The Cirrus source carries GPL-2.0-or-later and Takashi Iwai's copyright; the A1534 setup credits davidjo. Third-party source is downloaded, not vendored or relicensed here.

Only Cirrus is built. The 6.12 compatibility edit changes the exact quirk identifiers; missing-prototype warnings in upstream code were observed. Debian's header source version is used because the signed image comes from a differently versioned source package. `--source-deb /path/to/exact-package.deb` accepts an already downloaded authentic Debian package.

The software-volume rule is `/etc/wireplumber/wireplumber.conf.d/51-macbook12-linux-softvol.conf`, matching `alsa_card.pci-0000_00_1f.3` and setting `api.alsa.soft-mixer=true`. Protected manifests/backups are under `/var/lib/macbook12-linux/kernel/<kernel>/audio/`; initramfs backups may contain disk-unlock material and must remain private.

References: [WirePlumber 0.5.8 ALSA configuration](https://github.com/PipeWire/wireplumber/blob/0.5.8/docs/rst/daemon/configuration/alsa.rst), [kernel external-module builds](https://docs.kernel.org/kbuild/modules.html), [Debian update-initramfs](https://manpages.debian.org/trixie/initramfs-tools/update-initramfs.8.en.html).

</details>

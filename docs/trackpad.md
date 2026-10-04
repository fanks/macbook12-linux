# Trackpad Curve

A small native GNOME app for precise slow movements and longer fast swipes. Supports the **2017 MacBook10,1, Debian 13, GNOME 48 on Wayland and libinput 1.28.1**.

## Install

Open Terminal in GNOME, in the repository folder:

```sh
sudo apt update
sudo apt install build-essential libinput-dev python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1
python3 scripts/trackpad.py check
python3 scripts/trackpad.py install
```

Save your work, **sign out and back in once**. Then run:

```sh
trackpad-curve
```

You can also search for **Trackpad Curve** in GNOME. If the command is not in your shell's path, run `~/.local/bin/trackpad-curve`.

The installer compiles the small support library locally, installs the app for your account and records your original acceleration profile. No sudo is used by the installer or the app. It does not change scroll direction, tapping or keyboard settings.

## Adjust the feel

1. Drag one of the three chart points **up for more travel**, or **down for less**. The sliders do the same thing and support keyboard adjustments.
2. Press **Apply** and try the trackpad. Changes take effect immediately once support is loaded.
3. Press **Undo** to restore the previous applied change. One Undo is kept across app restarts.

**Reset** previews the starting curve; press Apply to use it. Closing the app discards unapplied changes.

| Control | Use it for | Available range |
| --- | --- | --- |
| Slow movements | Small targets and careful aiming | 60–180% |
| Medium movements | Everyday movement across the screen | 80–144% |
| Fast swipes | Moving from one side of the screen to the other | About 52–208% |

**100% is the supplied starting curve**, not macOS speed or Linux's default. The default factors are slow `0.5`, medium `1.25` and fast `1.92`. The dashed line is that starting curve. The chart uses fixed logarithmic scales so all three points remain easy to reach; horizontal dragging does not move the speed bands.

These settings aim for a Mac-like balance of precision and reach. They do not reproduce Apple's acceleration algorithm. Once the custom curve is active, use this app to change its shape; GNOME's ordinary speed slider does not control it.

## Update

From the repository folder:

```sh
git pull --ff-only
python3 scripts/trackpad.py install
```

Close the app first if it is open. The update preserves the saved curve, fixed baseline and Undo history, and refuses to overwrite manually edited installed files. Sign out and back in to load a replaced support library. Higher settings cannot be applied until the matching new library is loaded.

## Check or remove

```sh
~/.local/bin/trackpad-curve --status
```

To remove the app and custom support:

```sh
python3 scripts/trackpad.py uninstall
```

Sign out and back in. The original acceleration profile is restored if it still has the value managed by this setup. The ordinary GNOME speed setting is never overwritten. Your curve and backups remain in `${XDG_DATA_HOME:-$HOME/.local/share}/macbook-trackpad` for reference. Move that folder aside if you want a later clean installation.

## Recovery if GNOME will not start

Press **Ctrl-Alt-F3** for a text login (you may need Fn for F3), log in as your normal user, and run:

```sh
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/macbook-trackpad"
touch "${XDG_DATA_HOME:-$HOME/.local/share}/macbook-trackpad/disabled"
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/org.gnome.Shell@wayland.service.d/80-macbook-trackpad.conf"
systemctl --user daemon-reload
```

Then restart the computer normally. This removes only this project's GNOME override; it does not remove your settings or other overrides.

## How it works

The per-user GNOME service runs through a small wrapper. It uses the dynamic loader's `--preload` option for **GNOME Shell only**, without exporting `LD_PRELOAD` to applications or changing `/etc/ld.so.preload`. The wrapper falls back to the standard GNOME executable if the model or software version is unsupported, the configuration is missing, or the `disabled` marker exists.

The library configures only the Apple SPI Touchpad (`06cb:0417`) through libinput's public custom-acceleration API. It does not read, grab or log input events. Scroll/fallback scaling is kept constant. The app checks the running GNOME process, the loaded library identity and confirmation in its journal before reporting a curve as active.

The app stores slow/fast factors in `curve.conf`, medium in `medium.conf`, and its baseline/Undo records alongside them. File writes are atomic and competing edits are detected. The library is replaced with a new file on upgrades, never overwritten while mapped into GNOME.

Reference: [libinput's custom acceleration model](https://wayland.freedesktop.org/libinput/doc/latest/pointer-acceleration.html#the-custom-acceleration-profile).

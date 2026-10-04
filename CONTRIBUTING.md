# Contributing

Keep the user-facing instructions short: prerequisites, copy-and-paste commands, a check and an undo path. Put implementation details after those steps.

## Report a problem

Include the feature, exact command, error text and output from:

```sh
python3 scripts/check-system.py
```

Say whether this is a clean installation or an upgrade, and which desktop/session you use. Remove serial numbers, Bluetooth/MAC addresses, usernames and unrelated logs before posting. Never attach passwords, encryption keys, full disk images or private backup folders.

## Change a script

- Work on a branch and open a pull request.
- Preserve existing files and later user edits. Include uninstall/recovery behavior.
- Derive usernames, home directories, kernel versions and hardware addresses at runtime.
- Keep hardware/version checks explicit. Add evidence before extending model support.
- Never hot-unload the audio driver, restart GNOME, suspend or reboot automatically.
- Pin downloaded driver revisions and verify artifacts. Do not pipe a network download into a root shell.
- Do not commit build output, private state, module binaries or system logs.

## Run checks

On Debian 13:

```sh
sudo apt install build-essential libinput-dev python3 python3-gi python3-gi-cairo \
  gir1.2-gtk-4.0 gir1.2-adw-1 shellcheck nodejs libglib2.0-bin \
  xvfb xauth dbus-daemon
bash scripts/check.sh
```

The checks use temporary files, fake desktop commands and an isolated GTK test backend. They never apply a curve, load a kernel module or modify your desktop. The C test asks libinput to validate curves without opening an input device.

Hardware testing is separate: record the MacBook model, Debian/kernel/GNOME versions, the feature tested and the result. Software tests passing does not establish that another MacBook generation is supported.

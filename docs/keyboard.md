# Mac-style shortcuts on GNOME

For **MacBook10,1 (12-inch, 2017), Debian 13, GNOME Shell 48 on Wayland and Debian keyd 2.5.0**. The installer checks these versions; other models are untested.

Open Terminal in your normal GNOME session, enter the repository directory, and run:

```sh
python3 scripts/keyboard.py install
```

The script installs keyd through apt if needed and asks for sudo only for system changes. Do not run the whole script with sudo. Existing keyd configurations or extension installations are refused. The built-in Apple SPI Keyboard is detected from metadata without reading keystrokes; no device ID or account name is hardcoded.

**Save your work and reboot to activate the shortcuts:**

```sh
systemctl reboot
```

The installer never reboots automatically. Alternatively, log out of GNOME and every SSH/TTY session for this account, then sign in again. Reboot if a retained systemd user manager still has the old groups.

## Shortcuts

| Shortcut | Ordinary applications | GNOME Terminal |
| --- | --- | --- |
| Cmd-X | Cut | Unchanged; Terminal has no cut-output action |
| Cmd-C / V | Copy / paste | Copy / paste |
| Cmd-T / W | New tab / close tab | New tab / close tab |
| Cmd-N | Ask the focused app for a new window | Same |
| Cmd-Q | Ask the focused app to quit normally | Same |
| Cmd-Space | Overview/search | Same |
| Cmd alone | No overview action | Same |

Both Cmd keys work. Existing Ctrl shortcuts remain, including Terminal's Ctrl-C. X/C/V/T/W become Ctrl shortcuts, or Ctrl-Shift shortcuts in Terminal. Applications control tab behavior and unsaved-work prompts. New windows need not inherit the current folder or browser profile; Quit follows GNOME's application grouping.

X/C/V/T/W affect only the built-in keyboard. N/Q, search and the standalone Cmd setting affect all keyboards in this GNOME session. Input-language switching moves to Ctrl-Alt-Space (add Shift for reverse). Conflicting notification shortcuts are removed; unrelated bindings are retained.

## Check or remove

```sh
python3 scripts/keyboard.py status
python3 scripts/keyboard.py uninstall
```

Run uninstall only when you want to remove the integration, as the same normal desktop user. It restores captured settings only when they still match the installed values, removes unchanged files and the extension registration, and restores its service/group changes where appropriate. Later edits and other extensions are preserved. If another keyd configuration was added or this one edited, service/group changes are left for manual review. Packages remain installed; a fresh login drops removed group membership. Records are kept under `~/.local/state/macbook12-linux/`.

After activation, test copy/paste, Terminal Ctrl-C, tabs, search, and Files new-window/quit. Socket permission errors usually mean GNOME needs a fresh session with the `keyd` group. Diagnostics are in `$XDG_RUNTIME_DIR/macbook12-shortcuts-status.json`; no titles, clipboard contents or keystrokes are logged.

<details>
<summary>Limitations and recovery</summary>

- Focus changes apply asynchronously; an immediate shortcut can race the update. Test Terminal transitions and lock/unlock.
- Terminal rules assume its default shortcuts. Embedded terminals are not detected. GNOME Console is recognized but untested.
- Normal logout resets mappings. A GNOME crash can retain the last profile; `sudo /usr/bin/keyd.rvaiya reload` restores the empty base configuration.
- Do not combine with other keyd configurations: dynamic reset affects all of them. The extension pauses further commands if another configuration appears or its own file changes.
- Earlier separate integrations were tested on the reference MacBook. Cmd-T, this combined extension and the reusable installer still need physical fresh-install testing. Automated checks cover metadata, conflicts, rollback and syntax.

</details>

Sources: [keyd 2.5.0 identification](https://github.com/rvaiya/keyd/blob/v2.5.0/src/device.c), [IPC permissions](https://github.com/rvaiya/keyd/blob/v2.5.0/src/ipc.c), [GNOME application actions](https://github.com/GNOME/gnome-shell/blob/48.7/src/shell-app.c), [Terminal shortcuts](https://help.gnome.org/gnome-terminal/adv-keyboard-shortcuts.html). The ID algorithm port retains the [upstream license](../keyboard/KEYD-LICENSE).

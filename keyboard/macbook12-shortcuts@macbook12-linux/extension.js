// GNOME 48 only. No keyboard events, window titles or clipboard data are read.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const CONFIG_NAME = '80-macbook12-shortcuts.conf';
const BINDINGS = {
    desktop: ['meta.x = C-x', 'meta.c = C-c', 'meta.v = C-v', 'meta.t = C-t', 'meta.w = C-w'],
    terminal: ['meta.x = M-x', 'meta.c = C-S-c', 'meta.v = C-S-v', 'meta.t = C-S-t', 'meta.w = C-S-w'],
    passthrough: [],
};
const ACTIONS = {
    'macbook12-new-window': '_newWindow',
    'macbook12-quit-application': '_quitApplication',
};

export default class MacBook12Shortcuts extends Extension {
    _busy = false;
    _pending = null;
    _active = false;
    _idle = 0;

    enable() {
        this._registered = [];
        this._settings = this.getSettings();
        this._active = true;
        try {
            for (const [name, method] of Object.entries(ACTIONS)) {
                const action = Main.wm.addKeybinding(name, this._settings,
                    Meta.KeyBindingFlags.IGNORE_AUTOREPEAT, Shell.ActionMode.NORMAL,
                    () => this[method]());
                if (action === Meta.KeyBindingAction.NONE)
                    throw new Error(`Unable to register ${name}`);
                this._registered.push(name);
            }
            global.display.connectObject('notify::focus-window', () => this._scheduleUpdate(), this);
            global.stage.connectObject('notify::key-focus', () => this._scheduleUpdate(), this);
            Main.overview.connectObject('showing', () => this._scheduleUpdate(),
                'hidden', () => this._scheduleUpdate(), this);
            Main.layoutManager.connectObject('system-modal-opened', () => this._scheduleUpdate(), this);
            this._scheduleUpdate();
        } catch (error) {
            this.disable();
            throw error;
        }
    }

    disable() {
        this._active = false;
        for (const name of this._registered ?? [])
            Main.wm.removeKeybinding(name);
        this._registered = [];
        this._settings = null;
        global.display.disconnectObject(this);
        global.stage.disconnectObject(this);
        Main.overview.disconnectObject(this);
        Main.layoutManager.disconnectObject(this);
        if (this._idle) {
            GLib.source_remove(this._idle);
            this._idle = 0;
        }
        this._queue('passthrough', '');
    }

    _focusedApp() {
        const win = Main.modalCount === 0 ? global.display.focus_window : null;
        return win ? Shell.WindowTracker.get_default().get_window_app(win) : null;
    }

    _newWindow() {
        const app = this._focusedApp();
        if (app && !app.is_window_backed() && app.can_open_new_window())
            app.open_new_window(-1);
    }

    _quitApplication() {
        this._focusedApp()?.request_quit();
    }

    _scheduleUpdate() {
        if (this._idle || !this._active)
            return;
        this._idle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._idle = 0;
            this._update();
            return GLib.SOURCE_REMOVE;
        });
    }

    _update() {
        const win = global.display.focus_window;
        const app = win?.get_wm_class() ?? '';
        const focus = global.stage.get_key_focus();
        const shellTextField = focus instanceof Clutter.Text && focus.editable;
        let profile = this._active ? 'desktop' : 'passthrough';
        if (this._active && Main.modalCount === 0 && !Main.overview.visible && !shellTextField && win) {
            const normalized = app.toLowerCase().replace(/[^a-z0-9]+/g, '-');
            profile = ['org-gnome-terminal', 'gnome-terminal', 'gnome-terminal-server',
                'org-gnome-console', 'kgx'].includes(normalized) ? 'terminal' : 'desktop';
        }
        this._queue(profile, app);
    }

    _configurationSafe() {
        // keyd bind/reset affects every loaded configuration. Pause if another
        // configuration appears or ours changes; never reset someone else's.
        const directory = Gio.File.new_for_path('/etc/keyd');
        const entries = directory.enumerate_children('standard::name', Gio.FileQueryInfoFlags.NONE, null);
        const names = [];
        try {
            for (let entry = entries.next_file(null); entry; entry = entries.next_file(null)) {
                if (entry.get_name().endsWith('.conf'))
                    names.push(entry.get_name());
            }
        } finally {
            entries.close(null);
        }
        if (names.length !== 1 || names[0] !== CONFIG_NAME)
            return false;
        const [, content] = directory.get_child(CONFIG_NAME).load_contents(null);
        return new TextDecoder().decode(content) === this.metadata['keyd-config'];
    }

    _queue(profile, app) {
        // Keep the queue across disable/enable: an older child cannot run after
        // a newer lifecycle's mapping. Only the newest pending focus is kept.
        this._pending = {profile, app};
        this._drain();
    }

    _drain() {
        if (this._busy || !this._pending)
            return;
        const requested = this._pending;
        this._pending = null;
        this._busy = true;
        try {
            if (!this._configurationSafe())
                throw new Error('keyd configuration changed; shortcut integration is paused');
            const child = Gio.Subprocess.new(
                ['/usr/bin/keyd.rvaiya', 'bind', 'reset', ...BINDINGS[requested.profile]],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE);
            let timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2000, () => {
                timeout = 0;
                child.force_exit();
                return GLib.SOURCE_REMOVE;
            });
            // The timeout is short and scoped to this child, including while
            // disabling; completing the queue still sends the final reset.
            child.communicate_utf8_async(null, null, (proc, result) => {
                try {
                    const [, , error] = proc.communicate_utf8_finish(result);
                    this._status(requested, proc.get_successful(), (error ?? '').trim());
                } catch (error) {
                    this._status(requested, false, String(error));
                } finally {
                    if (timeout) {
                        GLib.source_remove(timeout);
                        timeout = 0;
                    }
                    this._busy = false;
                    this._drain();
                }
            });
        } catch (error) {
            this._status(requested, false, String(error));
            this._busy = false;
            this._drain();
        }
    }

    _status(requested, success, error) {
        try {
            const file = Gio.File.new_for_path(`${GLib.get_user_runtime_dir()}/macbook12-shortcuts-status.json`);
            file.replace_contents(JSON.stringify({...requested, success, error}),
                null, false, Gio.FileCreateFlags.PRIVATE, null);
        } catch (error) {
            console.error(`MacBook12 shortcut status: ${error}`);
        }
        if (!success)
            console.error(`MacBook12 shortcut mapping: ${error}`);
    }
}

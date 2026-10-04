#!/usr/bin/env python3
"""A small native editor for this MacBook's installed acceleration curve."""
from __future__ import annotations

import argparse
from dataclasses import replace
import math
import sys
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_foreign('cairo')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from backend import (Backend, BackendError, Curve, SLOW_MIN, SLOW_MAX,
                     MEDIUM_MIN, MEDIUM_MAX, FAST_MIN, FAST_MAX)

CSS = '''
window.trackpad-window { background: @window_bg_color; }
.intro-title { font-size: 25px; font-weight: 700; letter-spacing: -0.5px; }
.intro-copy { font-size: 13px; color: alpha(@window_fg_color, 0.66); }
.curve-panel { background: alpha(@window_fg_color, 0.035); border: 1px solid alpha(@window_fg_color, 0.075); border-radius: 16px; }
.eyebrow { font-size: 10px; font-weight: 700; letter-spacing: 1.1px; color: alpha(@window_fg_color, 0.53); }
.response-value { font-size: 19px; font-weight: 600; font-feature-settings: "tnum"; }
.response-title { font-size: 14px; font-weight: 600; }
.response-help { font-size: 12px; color: alpha(@window_fg_color, 0.59); }
.quiet-note { font-size: 11px; color: alpha(@window_fg_color, 0.55); }
.state-text { font-size: 12px; }
.state-icon { color: @accent_color; }
.warning-text { color: @warning_color; }
.error-text { color: @error_color; }
scale { padding: 9px 0; }
scale trough { min-height: 5px; }
scale highlight { min-height: 5px; }
button.apply-button { min-width: 98px; }
'''


def label(text, css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap)
    if css:
        widget.add_css_class(css)
    if wrap:
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return widget


class CurvePreview(Gtk.DrawingArea):
    HANDLES = (
        ('slow', 10, 0.2968 * 1000 / 2413, SLOW_MIN, SLOW_MAX),
        ('medium', 100, 0.9 * 0.2968 * 1000 / 2413, MEDIUM_MIN, MEDIUM_MAX),
        ('fast', 400, (387 / 130) * 0.2968 * 1000 / 2413, FAST_MIN, FAST_MAX),
    )
    MIN_GAIN, MAX_GAIN = 0.025, 2.5

    def __init__(self, baseline, curve, on_change):
        super().__init__()
        self.baseline = baseline
        self.curve = curve
        self.on_change = on_change
        self.dragged = None
        self.hovered = None
        self.set_content_height(162)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             ['Drag the Slow, Medium or Fast point up or down. The sliders below offer keyboard control.'])
        self.set_tooltip_text('Drag a point up for more travel, down for less. Apply to try it.')
        self.drag = Gtk.GestureDrag.new()
        self.drag.set_button(1)
        self.drag.connect('drag-begin', self.drag_begin)
        self.drag.connect('drag-update', self.drag_update)
        self.drag.connect('drag-end', self.drag_end)
        self.drag.connect('cancel', self.drag_cancel)
        self.add_controller(self.drag)
        motion = Gtk.EventControllerMotion.new()
        motion.connect('motion', self.pointer_motion)
        motion.connect('leave', self.pointer_leave)
        self.add_controller(motion)

    def xy(self, speed, gain, width=None, height=None):
        width = self.get_width() if width is None else width
        height = self.get_height() if height is None else height
        left, right, top, bottom = 12, width - 12, 10, height - 27
        # Fixed logarithmic axes keep all three handles useful at every setting.
        x = left + math.log1p(speed / 30) / math.log1p(520 / 30) * (right - left)
        fraction = math.log(gain / self.MIN_GAIN) / math.log(self.MAX_GAIN / self.MIN_GAIN)
        return x, bottom - fraction * (bottom - top)

    def gain_at_y(self, y):
        top, bottom = 10, self.get_height() - 27
        fraction = max(0, min(1, (bottom - y) / max(1, bottom - top)))
        return self.MIN_GAIN * (self.MAX_GAIN / self.MIN_GAIN) ** fraction

    def handle_at(self, x, y):
        nearest = None
        distance = 20 ** 2
        for handle in self.HANDLES:
            key, speed, factor, _low, _high = handle
            hx, hy = self.xy(speed, getattr(self.curve, key) * factor)
            squared = (x - hx) ** 2 + (y - hy) ** 2
            if squared <= distance:
                distance, nearest = squared, handle
        return nearest

    def pointer_motion(self, _controller, x, y):
        self.hovered = self.handle_at(x, y)
        self.set_cursor_from_name('ns-resize' if self.dragged or self.hovered else 'default')
        self.queue_draw()

    def pointer_leave(self, _controller):
        self.hovered = None
        if not self.dragged:
            self.set_cursor_from_name('default')
        self.queue_draw()

    def drag_begin(self, gesture, x, y):
        self.dragged = self.handle_at(x, y)
        if self.dragged is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        key, speed, factor, _low, _high = self.dragged
        self.drag_origin_y = self.xy(speed, getattr(self.curve, key) * factor)[1]
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.set_cursor_from_name('ns-resize')
        self.queue_draw()

    def drag_update(self, _gesture, _dx, dy):
        if self.dragged is None:
            return
        key, _speed, factor, low, high = self.dragged
        value = max(low, min(high, self.gain_at_y(self.drag_origin_y + dy) / factor))
        self.on_change(replace(self.curve, **{key: value}))

    def drag_end(self, gesture, dx, dy):
        self.drag_update(gesture, dx, dy)
        self.drag_cancel(gesture, None)

    def drag_cancel(self, _gesture, _sequence):
        self.dragged = None
        self.set_cursor_from_name('default')
        self.queue_draw()

    def update_curve(self, curve):
        self.curve = curve
        self.queue_draw()

    @staticmethod
    def points(curve):
        # Interpolate output speed first, exactly like libinput, then show gain.
        points = Backend.propose(curve)
        first_gain = points[1][1] / (points[1][0] * 2413 / 25400)
        plot = [(0, first_gain)]
        for v in range(1, 521):
            i = min(v // 10, len(points) - 2)
            (x0, y0), (x1, y1) = points[i:i + 2]
            output = y0 + (y1 - y0) * (v - x0) / (x1 - x0)
            plot.append((v, output / (v * 2413 / 25400)))
        return plot

    def draw(self, _area, ctx, width, height):
        dark = Adw.StyleManager.get_default().get_dark()
        ink = (0.89, 0.91, 0.94) if dark else (0.15, 0.19, 0.24)
        accent = (0.42, 0.70, 0.98) if dark else (0.16, 0.39, 0.69)
        left, right, top, bottom = 12, width - 12, 10, height - 27

        def xy(v, gain):
            return self.xy(v, gain, width, height)

        ctx.set_line_width(1)
        for fraction in (0.0, 0.5, 1.0):
            y = bottom - fraction * (bottom - top)
            ctx.set_source_rgba(*ink, 0.075)
            ctx.move_to(left, y)
            ctx.line_to(right, y)
            ctx.stroke()

        current = self.points(self.curve)
        starting = self.points(self.baseline)
        ctx.move_to(left, bottom)
        for v, gain in current:
            ctx.line_to(*xy(v, gain))
        ctx.line_to(right, bottom)
        ctx.close_path()
        ctx.set_source_rgba(*accent, 0.07)
        ctx.fill()

        ctx.set_dash([3, 5])
        ctx.set_line_width(1.5)
        ctx.set_source_rgba(*ink, 0.30)
        for i, (v, gain) in enumerate(starting):
            (ctx.move_to if i == 0 else ctx.line_to)(*xy(v, gain))
        ctx.stroke()
        ctx.set_dash([])
        ctx.set_line_width(2.4)
        ctx.set_source_rgb(*accent)
        for i, (v, gain) in enumerate(current):
            (ctx.move_to if i == 0 else ctx.line_to)(*xy(v, gain))
        ctx.stroke()

        for handle in self.HANDLES:
            key, speed, _factor, _low, _high = handle
            gain = next(g for v, g in current if v == speed)
            x, y = xy(speed, gain)
            if handle == self.dragged or handle == self.hovered:
                ctx.arc(x, y, 12, 0, 2 * math.pi)
                ctx.set_source_rgba(*accent, 0.15)
                ctx.fill()
            ctx.arc(x, y, 6, 0, 2 * math.pi)
            ctx.set_source_rgb(*accent)
            ctx.fill()
            ctx.arc(x, y, 2.3, 0, 2 * math.pi)
            ctx.set_source_rgb(*(0.15, 0.19, 0.24) if dark else (1, 1, 1))
            ctx.fill()
        for text, x, alignment in [('Slow', left, 0), ('Medium', width * 0.5, 0.5), ('Fast', right, 1)]:
            layout = self.create_pango_layout(text)
            layout.set_font_description(Pango.FontDescription('Sans 9'))
            tw, _ = layout.get_pixel_size()
            ctx.move_to(x - tw * alignment, height - 17)
            ctx.set_source_rgba(*ink, 0.54)
            from gi.repository import PangoCairo
            PangoCairo.show_layout(ctx, layout)


class TunerWindow(Adw.ApplicationWindow):
    def __init__(self, app, backend):
        super().__init__(application=app, title='Trackpad Curve')
        self.backend = backend
        self.snapshot = backend.read()
        self.baseline = backend.baseline()
        self.current = self.snapshot.curve or self.baseline
        self.draft = self.current
        self.busy = False
        self.setting_sliders = False
        self.active_status = None
        self.set_default_size(560, 720)
        self.set_size_request(420, 520)
        self.add_css_class('trackpad-window')
        self.connect('close-request', self.on_close_request)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title='Trackpad Curve'))
        root.append(header)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.append(scroller)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                          margin_start=28, margin_end=28, margin_top=9, margin_bottom=12)
        scroller.set_child(content)
        content.append(label('Fine-tune your trackpad', 'intro-title'))
        intro = label('A little precision. A little more reach.', 'intro-copy', True)
        intro.set_margin_top(5)
        intro.set_margin_bottom(19)
        content.append(intro)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        panel.add_css_class('curve-panel')
        chart_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                            margin_start=18, margin_end=18, margin_top=14, margin_bottom=10)
        panel.append(chart_box)
        chart_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        chart_header.append(label('POINTER RESPONSE', 'eyebrow'))
        legend = label('Dashed = starting setup', 'quiet-note')
        legend.set_hexpand(True)
        legend.set_halign(Gtk.Align.END)
        chart_header.append(legend)
        chart_box.append(chart_header)
        self.preview = CurvePreview(self.baseline, self.draft, self.set_draft)
        chart_box.append(self.preview)
        chart_box.append(label('Drag points up or down · Apply to try your curve', 'quiet-note', True))
        content.append(panel)

        self.scales = {}
        self.values = {}
        descriptions = [
            ('slow', 'Slow movements', 'Small adjustments and careful aiming', SLOW_MIN, SLOW_MAX, 0.01),
            ('medium', 'Medium movements', 'Everyday movement across the screen', MEDIUM_MIN, MEDIUM_MAX, 0.01),
            ('fast', 'Fast swipes', 'Quick movement from side to side', FAST_MIN, FAST_MAX, 0.01),
        ]
        for key, title, help_text, lower, upper, step in descriptions:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_top=17)
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
            text.append(label(title, 'response-title'))
            text.append(label(help_text, 'response-help', True))
            top.append(text)
            value = label('', 'response-value')
            value.set_xalign(1)
            value.set_valign(Gtk.Align.CENTER)
            top.append(value)
            row.append(top)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lower, upper, step)
            scale.set_draw_value(False)
            scale.set_digits(3)
            scale.set_value(getattr(self.current, key))
            scale.set_hexpand(True)
            scale.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                                  [title, help_text + '. Higher values mean more cursor travel.'])
            scale.set_tooltip_text('Less travel on the left · More travel on the right')
            scale.connect('value-changed', self.on_slider_changed)
            self.scales[key] = scale
            self.values[key] = value
            row.append(scale)
            content.append(row)

        note = label('100% is your starting setup. Higher values move the cursor farther.', 'quiet-note', True)
        note.set_margin_top(5)
        content.append(note)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         margin_start=28, margin_end=28, margin_bottom=20)
        root.append(footer)
        separator = Gtk.Separator(margin_top=0, margin_bottom=13)
        footer.append(separator)
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.status_icon = Gtk.Image.new_from_icon_name('object-select-symbolic')
        self.status_icon.add_css_class('state-icon')
        self.status_label = label('Checking current settings…', 'state-text', True)
        self.status_label.set_hexpand(True)
        status_box.append(self.status_icon)
        status_box.append(self.status_label)
        footer.append(status_box)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=14)
        self.reset_button = Gtk.Button(label='Reset')
        self.reset_button.add_css_class('flat')
        self.reset_button.set_tooltip_text('Preview your starting setup; Apply to save it')
        self.reset_button.connect('clicked', lambda *_: self.set_draft(self.baseline))
        actions.append(self.reset_button)
        actions.append(Gtk.Box(hexpand=True))
        self.undo_button = Gtk.Button(label='Undo')
        self.undo_button.set_tooltip_text('Undo the last applied change')
        self.undo_button.connect('clicked', self.on_undo)
        actions.append(self.undo_button)
        self.apply_button = Gtk.Button(label='Apply')
        self.apply_button.add_css_class('suggested-action')
        self.apply_button.add_css_class('apply-button')
        self.apply_button.connect('clicked', self.on_apply)
        actions.append(self.apply_button)
        footer.append(actions)
        self.set_content(root)
        self.refresh()
        starting = self.snapshot
        self.run_task(lambda: self.backend.status(starting.curve),
                      lambda status: self.got_status(status) if self.snapshot.revision == starting.revision else None,
                      busy=False)

    def set_state(self, text, kind='ok'):
        self.status_label.set_text(text)
        for css in ('warning-text', 'error-text'):
            self.status_label.remove_css_class(css)
        icons = {'ok': 'object-select-symbolic', 'draft': 'document-edit-symbolic',
                 'busy': 'content-loading-symbolic', 'warning': 'dialog-warning-symbolic',
                 'error': 'dialog-error-symbolic'}
        self.status_icon.set_from_icon_name(icons[kind])
        if kind in ('warning', 'error'):
            self.status_label.add_css_class(kind + '-text')

    def refresh(self):
        dirty = self.draft != self.current or self.snapshot.curve is None
        for key, value in self.values.items():
            percent = getattr(self.draft, key) / getattr(self.baseline, key) * 100
            value.set_text(f'{percent:.0f}%' if abs(percent - round(percent)) < 0.05 else f'{percent:.1f}%')
        self.preview.update_curve(self.draft)
        self.preview.set_sensitive(not self.busy)
        self.apply_button.set_sensitive(dirty and not self.busy)
        self.reset_button.set_sensitive(self.draft != self.baseline and not self.busy)
        self.undo_button.set_sensitive(self.backend.can_undo(self.snapshot) and not self.busy)
        for scale in self.scales.values():
            scale.set_sensitive(not self.busy)
        if self.busy:
            self.set_state('Applying your curve…', 'busy')
        elif dirty:
            self.set_state('Preview only — Apply to try this curve.', 'draft')
        elif self.active_status:
            self.set_state(self.active_status.message,
                           'ok' if self.active_status.status == 'active' else 'warning')

    def set_draft(self, curve):
        self.setting_sliders = True
        for key, scale in self.scales.items():
            scale.set_value(getattr(curve, key))
        self.setting_sliders = False
        self.draft = curve  # Opening/resetting never rounds the stored settings.
        self.refresh()

    def on_slider_changed(self, scale):
        if self.setting_sliders:
            return
        key = next(key for key, widget in self.scales.items() if widget is scale)
        self.draft = replace(self.draft, **{key: scale.get_value()})
        self.refresh()

    def got_status(self, status):
        self.active_status = status
        self.refresh()

    def on_apply(self, *_args):
        curve, snapshot = self.draft, self.snapshot
        self.run_task(lambda: self.backend.apply(curve, expected=snapshot), self.changed)

    def on_undo(self, *_args):
        snapshot = self.snapshot
        self.run_task(lambda: self.backend.undo(expected=snapshot), self.changed)

    def changed(self, result):
        self.snapshot = result.snapshot
        self.current = result.snapshot.curve or self.baseline
        self.active_status = result
        self.set_draft(self.current)

    def run_task(self, task, done, busy=True):
        if busy:
            self.busy = True
            self.refresh()
        def work():
            try:
                result, error = task(), None
            except Exception as exc:
                result, error = None, str(exc)
            GLib.idle_add(finish, result, error)
        def finish(result, error):
            if busy:
                self.busy = False
            if error:
                # Reload to avoid overwriting a concurrently edited configuration.
                try:
                    self.snapshot = self.backend.read()
                    self.current = self.snapshot.curve or self.baseline
                except BackendError:
                    pass
                self.refresh()
                self.set_state(error, 'error')
            else:
                done(result)
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, daemon=True).start()

    def on_close_request(self, *_args):
        if self.busy:
            self.set_state('Finishing the change before closing…', 'busy')
            return True
        return False


class TunerApplication(Adw.Application):
    def __init__(self, backend=None, *, unique=True):
        super().__init__(application_id='local.macbook.TrackpadCurve',
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS if unique else Gio.ApplicationFlags.NON_UNIQUE)
        self.backend = backend or Backend()
        self.window = None

    def do_activate(self):
        if not self.window:
            provider = Gtk.CssProvider()
            provider.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                       Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            try:
                self.window = TunerWindow(self, self.backend)
            except Exception as exc:
                print(f'Trackpad Curve: {exc}', file=sys.stderr)
                self.quit()
                return
        self.window.present()


def main():
    parser = argparse.ArgumentParser(description='Fine-tune slow, medium, and fast trackpad movement.')
    parser.add_argument('--status', action='store_true', help='Show current curve and activation status without opening a window')
    args = parser.parse_args()
    backend = Backend()
    if args.status:
        try:
            snapshot = backend.read()
            print(snapshot.curve or 'Custom curve disabled')
            print(backend.status(snapshot.curve).message)
            return 0
        except BackendError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    return TunerApplication(backend).run([sys.argv[0]])


if __name__ == '__main__':
    raise SystemExit(main())

"""Render and exercise the real GTK UI against an in-memory backend only."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import app
from gi.repository import GLib, Gtk, Graphene


class TestBackend:
    def __init__(self):
        self.start = app.Curve(.5, 1.25, 1.92)
        self.curve = self.start
        self.revision = 0
        self.previous = None
        self.calls = []

    def read(self):
        return SimpleNamespace(curve=self.curve, revision=str(self.revision))

    def baseline(self):
        return self.start

    def status(self, _curve=None):
        return SimpleNamespace(status='active', message='Your curve is active.')

    def can_undo(self, _snapshot=None):
        return self.previous is not None

    def apply(self, curve, *, expected):
        assert expected.revision == str(self.revision)
        curve.validate()
        self.previous, self.curve = self.curve, curve
        self.revision += 1
        self.calls.append('apply')
        return SimpleNamespace(snapshot=self.read(), status='active', message='Your curve is active.')

    def undo(self, *, expected):
        assert expected.revision == str(self.revision)
        self.curve, self.previous = self.previous, None
        self.revision += 1
        self.calls.append('undo')
        return SimpleNamespace(snapshot=self.read(), status='active', message='Previous settings restored.')


out = Path(sys.argv[1])
out.mkdir(exist_ok=True, parents=True)
backend = TestBackend()
application = app.TunerApplication(backend, unique=False)
failures = []


def capture(window, name):
    widget = window.get_content()
    print('RENDER', widget.get_width(), widget.get_height(), widget.get_mapped(), window.get_width(), window.get_height(), flush=True)
    snapshot = Gtk.Snapshot.new()
    found, background = window.get_style_context().lookup_color('window_bg_color')
    assert found
    bounds = Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height())
    snapshot.append_color(background, bounds)
    widget.get_parent().snapshot_child(widget, snapshot)
    node = snapshot.to_node()
    assert node is not None
    renderer = window.get_renderer()
    texture = renderer.render_texture(node, None)
    assert texture.save_to_png(str(out / name))


def check(fn):
    try:
        fn()
    except Exception as exc:
        import traceback
        failures.append(traceback.format_exc())
        print(f'QA FAILURE: {exc}', file=sys.stderr)
        application.quit()
    return GLib.SOURCE_REMOVE


def ready():
    window = application.window
    assert window is not None
    assert backend.calls == []  # Opening and previewing must never save.
    assert window.draft == backend.start
    assert not window.apply_button.get_sensitive()
    assert not window.undo_button.get_sensitive()
    assert all(value.get_label() == '100%' for value in window.values.values())
    capture(window, 'trackpad-start.png')
    preview = window.preview
    for handle in preview.HANDLES:
        key, speed, factor, lower, upper = handle
        for target in (lower, upper, (lower + upper) / 2):
            before = window.draft
            x, y = preview.xy(speed, getattr(before, key) * factor)
            # Exercise the actual GTK gesture callbacks, including hit testing.
            preview.drag.emit('drag-begin', x + 5, y + 3)
            assert preview.dragged == handle
            target_y = preview.xy(speed, target * factor)[1]
            preview.drag.emit('drag-update', 50., target_y - y)
            preview.drag.emit('drag-end', 50., target_y - y)
            assert abs(getattr(window.draft, key) - target) < 1e-12
            for other in ('slow', 'medium', 'fast'):
                if other != key:
                    assert getattr(window.draft, other) == getattr(before, other)
            assert abs(window.scales[key].get_value() - target) < 1e-12
            assert backend.calls == []
        x, y = preview.xy(speed, getattr(window.draft, key) * factor)
        preview.drag.emit('drag-begin', x, y)
        preview.drag.emit('drag-update', 0., -1000.)
        assert getattr(window.draft, key) == upper
        preview.drag.emit('drag-end', 0., 1000.)
        assert getattr(window.draft, key) == lower
        assert preview.dragged is None
    before = window.draft
    preview.drag.emit('drag-begin', 0., 0.)
    preview.drag.emit('drag-end', 0., 100.)
    assert window.draft == before
    window.set_draft(backend.start)
    window.scales['medium'].set_value(1.40)
    assert window.draft.medium == 1.40
    assert window.apply_button.get_sensitive()
    assert backend.calls == []
    window.apply_button.emit('clicked')
    GLib.timeout_add(500, check, applied)


def applied():
    window = application.window
    assert not window.busy
    assert backend.calls == ['apply']
    assert backend.curve == app.Curve(.5, 1.4, 1.92)
    assert not window.apply_button.get_sensitive()
    assert window.undo_button.get_sensitive()
    window.reset_button.emit('clicked')
    assert window.draft == backend.start and backend.curve.medium == 1.4
    assert backend.calls == ['apply']  # Reset is a draft until Apply.
    window.undo_button.emit('clicked')
    GLib.timeout_add(500, check, undone)


def undone():
    window = application.window
    assert backend.calls == ['apply', 'undo']
    assert backend.curve == backend.start
    assert window.draft == backend.start
    assert not window.undo_button.get_sensitive()
    window.scales['slow'].set_value(.3)
    window.scales['medium'].set_value(1.8)
    window.scales['fast'].set_value(4.0)
    assert window.draft == app.Curve(.3, 1.8, 4.0)
    for speed, gain in window.preview.points(window.draft):
        x, y = window.preview.xy(speed, gain)
        assert 0 <= x <= window.preview.get_width()
        assert 0 <= y <= window.preview.get_height() - 27
    window.set_default_size(440, 600)
    GLib.timeout_add(500, check, finish)


def finish():
    window = application.window
    capture(window, 'trackpad-compact-draft.png')
    (out / 'ui-results.json').write_text(json.dumps({
        'opening_preserves_settings': True, 'sliders_are_preview_only': True,
        'apply_saves': True, 'reset_is_preview_only': True, 'undo_restores': True,
        'limits_accepted': True, 'calls': backend.calls,
        'drag_all_points': True, 'drag_clamps_to_limits': True,
        'drag_preserves_other_values': True, 'drag_is_preview_only': True,
        'drag_and_sliders_agree': True, 'expanded_curve_fits_chart': True,
        'compact_window': [window.get_width(), window.get_height()],
    }, indent=2) + '\n')
    application.quit()


if '--interactive' not in sys.argv:
    GLib.timeout_add(1500, check, ready)
    GLib.timeout_add(20000, lambda: (failures.append('Timed out'), application.quit(), GLib.SOURCE_REMOVE)[-1])
application.run([sys.argv[0]])
if failures:
    raise SystemExit('\n'.join(failures))
print('Native GTK rendering and interaction checks passed.')

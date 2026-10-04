"""Metadata, conflict and rollback tests; never accesses the real desktop."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("keyboard_installer", Path(__file__).resolve().parents[1] / "scripts/keyboard.py")
keyboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(keyboard)

KEYS = sum(1 << code for code in (*range(2, 12), *range(16, 22)))


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def event(self, event="event0", *, spi=True, vendor="05ac", product="0273"):
        device = self.root / ("spi-controller" if spi else "usb-controller") / event
        (device / "id").mkdir(parents=True)
        (device / "capabilities").mkdir()
        (device / "name").write_text("Apple SPI Keyboard\n")
        (device / "id/vendor").write_text(vendor)
        (device / "id/product").write_text(product)
        for name, value in (("key", f"{KEYS:x}"), ("abs", "0"), ("rel", "0")):
            (device / "capabilities" / name).write_text(value)
        link = self.root / "class" / event
        link.mkdir(parents=True)
        (link / "device").symlink_to(device, target_is_directory=True)
        return device

    def test_bitmap_word_order_and_padding(self):
        self.assertEqual(keyboard.bitmap("1 0"), 1 << 64)
        self.assertEqual(keyboard.bitmap("20 0000000000000001"), (0x20 << 64) | 1)
        for value in ("", "xyz", "1" * 17):
            with self.assertRaises(keyboard.Error):
                keyboard.bitmap(value)

    def test_id_uses_live_metadata_and_keyd_bit_limits(self):
        initial = keyboard.keyd_id(0x05ac, 0x0273, "Apple SPI Keyboard", KEYS, 0, 0)
        self.assertRegex(initial, r"^k:05ac:0273:[0-9a-f]{8}$")
        self.assertNotEqual(initial, keyboard.keyd_id(0x05ac, 0x0273, "Apple SPI Keyboard", KEYS | (1 << 200), 0, 0))
        self.assertEqual(initial, keyboard.keyd_id(0x05ac, 0x0273, "Apple SPI Keyboard", KEYS | (1 << 288), 0x100, 0x100))
        with self.assertRaises(keyboard.Error):
            keyboard.keyd_id(0, 0, "Apple SPI Keyboard", 0, 0, 0)

    def test_detects_unique_spi_keyboard_without_event_access(self):
        self.event()
        result = keyboard.detect_keyboard(self.root / "class")
        self.assertEqual(result, keyboard.keyd_id(0x05ac, 0x0273, "Apple SPI Keyboard", KEYS, 0, 0))
        self.event("event1")
        with self.assertRaisesRegex(keyboard.Error, "found 2"):
            keyboard.detect_keyboard(self.root / "class")

    def test_refuses_same_name_on_non_spi_device(self):
        self.event(spi=False)
        with self.assertRaisesRegex(keyboard.Error, "outside an SPI"):
            keyboard.detect_keyboard(self.root / "class")

    def test_config_injection_and_version_guards(self):
        with self.assertRaises(keyboard.Error):
            keyboard.config_text("*\n[main]\na=b")
        for version in ("2.5.0-4", "2.5.0-4+b1", "2.5.0-4~deb13u1"):
            keyboard.check_keyd_version(version)
        for version in (None, "2.6.0-1", "2.5.0", "2.5.0-4\nextra"):
            with self.assertRaises(keyboard.Error):
                keyboard.check_keyd_version(version)

    def settings(self):
        values = ["'Super_L'", "['<Super>s']", "['<Super>space', 'XF86Keyboard']",
                  "['<Shift><Super>space', '<Shift>XF86Keyboard']", "['<Super>v', '<Super>m']", "['<Super>n']"]
        return [{"schema": schema, "key": key, "before": value} for (schema, key), value in zip(keyboard.KEYS, values)]

    def test_only_intended_shortcuts_change(self):
        plan = {entry["key"]: entry["desired"] for entry in keyboard.settings_plan(self.settings())}
        self.assertEqual(plan["overlay-key"], "''")
        self.assertEqual(keyboard.array(plan["toggle-overview"]), ["<Super>s", "<Super>space"])
        self.assertEqual(keyboard.array(plan["switch-input-source"]), ["<Control><Alt>space", "XF86Keyboard"])
        self.assertEqual(keyboard.array(plan["switch-input-source-backward"]), ["<Shift><Control><Alt>space", "<Shift>XF86Keyboard"])
        self.assertEqual(keyboard.array(plan["toggle-message-tray"]), ["<Super>m"])
        self.assertEqual(keyboard.array(plan["focus-active-notification"]), [])

    def test_rollback_preserves_later_gsettings_changes(self):
        entries = keyboard.settings_plan(self.settings())
        for entry in entries:
            entry["after"] = entry["desired"]
        current = {(e["schema"], e["key"]): e["after"] for e in entries}
        changed = (entries[0]["schema"], entries[0]["key"])
        current[changed] = "'Super_R'"
        calls = []
        def put(schema, key, value):
            calls.append((schema, key, value))
            current[schema, key] = value
        kept = keyboard.restore_settings(entries, lambda s, k: current[s, k], put)
        self.assertEqual(current[changed], "'Super_R'")
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(calls), len(entries) - 1)

    def test_planned_empty_list_is_restored_after_failed_readback(self):
        entries = [{"schema": "example", "key": "shortcut", "before": "['<Super>n']", "after": "[]"}]
        restored = []
        kept = keyboard.restore_settings(entries, lambda *_: "@as []", lambda *args: restored.append(args))
        self.assertEqual(kept, [])
        self.assertEqual(restored, [("example", "shortcut", "['<Super>n']")])

    def test_file_removal_preserves_later_edits_and_rejects_traversal(self):
        extension = self.root / "extension"
        extension.mkdir()
        unchanged = extension / "metadata.json"
        edited = extension / "extension.js"
        unchanged.write_text("original metadata")
        edited.write_text("original source")
        files = {p.name: keyboard.digest(p) for p in (unchanged, edited)}
        edited.write_text("later user edit")
        kept = keyboard.remove_files(extension, files)
        self.assertFalse(unchanged.exists())
        self.assertEqual(edited.read_text(), "later user edit")
        self.assertEqual(kept, [str(edited)])
        with self.assertRaises(keyboard.Error):
            keyboard.remove_files(extension, {"../other": "unused"})

    def test_file_removal_never_follows_replaced_parent_symlink(self):
        extension = self.root / "extension"
        extension.mkdir()
        other = self.root / "other"
        other.mkdir()
        target = other / "schema.xml"
        target.write_text("same original contents")
        (extension / "schemas").symlink_to(other, target_is_directory=True)
        kept = keyboard.remove_files(extension, {"schemas/schema.xml": keyboard.digest(target)})
        self.assertTrue(target.exists())
        self.assertEqual(len(kept), 1)

    def test_file_removal_never_prunes_replaced_root_symlink(self):
        other = self.root / "other"
        other.mkdir()
        (other / "empty-directory").mkdir()
        target = other / "extension.js"
        target.write_text("same original contents")
        extension = self.root / "extension"
        extension.symlink_to(other, target_is_directory=True)
        kept = keyboard.remove_files(extension, {"extension.js": keyboard.digest(target)})
        self.assertTrue(target.exists())
        self.assertTrue((other / "empty-directory").is_dir())
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()

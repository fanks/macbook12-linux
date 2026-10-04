"""Exercise system-file ownership and recovery in an isolated fake filesystem."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "suspend.py"
SPEC = importlib.util.spec_from_file_location("suspend_setup", SOURCE)
suspend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suspend)


class SuspendTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.calls = []
        self.fail_grub = False
        self.installer = suspend.Installer(self.root, self.run_command)
        self.write("/etc/os-release", 'ID=debian\nVERSION_ID="13"\n')
        self.write("/sys/class/dmi/id/product_name", "MacBook10,1\n")
        self.ssd = "/sys/bus/pci/devices/0000:03:00.0"
        self.port = "/sys/bus/pci/devices/0000:00:1c.0"
        self.write(self.ssd + "/vendor", "0x106b\n")
        self.write(self.ssd + "/device", "0x2003\n")
        self.write(self.ssd + "/d3cold_allowed", "1\n")
        self.write(self.port + "/vendor", "0x8086\n")
        self.write(self.port + "/device", "0x9d14\n")
        self.write(self.port + "/d3cold_allowed", "1\n")
        self.write("/sys/power/mem_sleep", "[s2idle] deep\n")
        self.write("/proc/cmdline", "quiet\n")
        self.write("/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT=\'quiet splash audit=1\'\n')
        self.write("/usr/sbin/update-grub", "placeholder\n")
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def write(self, name, contents):
        path = self.installer.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    def run_command(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ["/bin/sh", "-c"]:
            return subprocess.run(args, **kwargs)
        if args == ["/usr/sbin/update-grub"] and self.fail_grub:
            raise subprocess.CalledProcessError(1, args, stderr="simulated failure")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def test_full_install_and_uninstall_with_dynamic_ssd_address(self):
        original_grub = self.installer.path("/etc/default/grub").read_bytes()
        self.installer.install(boot_parameters=True, lid_suspend=True)
        self.assertEqual(self.installer.path(self.ssd + "/d3cold_allowed").read_text().strip(), "0")
        self.assertEqual(self.installer.path(self.port + "/d3cold_allowed").read_text().strip(), "1")
        self.assertEqual(self.installer.path("/etc/default/grub").read_bytes(), original_grub)
        self.assertEqual(set(self.installer.read_state()["files"]), set(suspend.PAYLOADS))
        self.installer.install(boot_parameters=True, lid_suspend=True)
        self.installer.uninstall()
        self.assertEqual(self.installer.path(self.ssd + "/d3cold_allowed").read_text().strip(), "1")
        self.assertEqual(self.installer.path("/etc/default/grub").read_bytes(), original_grub)
        self.assertFalse(self.installer.state_path.exists())
        self.assertTrue(all(not self.installer.path(name).exists() for name in suspend.PAYLOADS))
        self.assertFalse(any("systemctl" in args[0] for args in self.calls))

    def test_default_installs_ssd_rule_only(self):
        self.installer.install()
        self.assertEqual(set(self.installer.read_state()["files"]), {suspend.RULE_PATH})
        self.assertNotIn(["/usr/sbin/update-grub"], self.calls)
        rule = self.installer.path(suspend.RULE_PATH).read_text()
        self.assertNotIn('KERNEL==', rule)
        self.assertIn('product_name}=="MacBook10,1"', rule)
        self.assertIn('vendor}=="0x106b"', rule)
        self.assertIn('device}=="0x2003"', rule)

    def test_edited_file_blocks_uninstall_before_any_changes(self):
        self.installer.install(boot_parameters=True, lid_suspend=True)
        self.write(suspend.LID_PATH, "# user edit\n")
        with self.assertRaisesRegex(suspend.Error, "changed since installation"):
            self.installer.uninstall()
        self.assertTrue(self.installer.path(suspend.RULE_PATH).exists())
        self.assertTrue(self.installer.path(suspend.GRUB_PATH).exists())
        self.assertEqual(self.installer.path(self.ssd + "/d3cold_allowed").read_text().strip(), "0")

    def test_unmanaged_file_is_not_overwritten(self):
        self.write(suspend.RULE_PATH, "# existing\n")
        with self.assertRaisesRegex(suspend.Error, "unmanaged file"):
            self.installer.install()
        self.assertEqual(self.installer.path(suspend.RULE_PATH).read_text(), "# existing\n")
        self.assertFalse(self.installer.state_path.exists())

    def test_symlink_is_not_followed(self):
        self.write("/some-file", "original\n")
        path = self.installer.path(suspend.RULE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(self.installer.path("/some-file"))
        with self.assertRaisesRegex(suspend.Error, "non-regular"):
            self.installer.install()
        self.assertEqual(self.installer.path("/some-file").read_text(), "original\n")

    def test_wrong_model_or_ssd_is_rejected(self):
        self.write("/sys/class/dmi/id/product_name", "MacBook9,1\n")
        with self.assertRaises(suspend.Error):
            self.installer.install()
        self.write("/sys/class/dmi/id/product_name", "MacBook10,1\n")
        self.write(self.ssd + "/device", "0x2001\n")
        with self.assertRaises(suspend.Error):
            self.installer.install()
        self.assertFalse(self.installer.state_path.exists())

    def test_changed_distro_can_uninstall_but_not_install(self):
        self.installer.install()
        self.write("/etc/os-release", 'ID=debian\nVERSION_ID="14"\n')
        with self.assertRaises(suspend.Error):
            self.installer.install()
        self.installer.uninstall()

    def test_conflicting_existing_grub_option_blocks_before_writing(self):
        self.write("/etc/default/grub.d/20-other.cfg", 'GRUB_CMDLINE_LINUX="mem_sleep_default=deep"\n')
        with self.assertRaisesRegex(suspend.Error, "Conflicting GRUB option"):
            self.installer.install(boot_parameters=True)
        self.assertFalse(self.installer.state_path.exists())
        self.assertFalse(self.installer.path(suspend.RULE_PATH).exists())

    def test_grub_dropin_preserves_unrelated_arguments_and_avoids_duplicates(self):
        self.write("/etc/default/grub", 'GRUB_CMDLINE_LINUX=\'nvme.noacpi=1\'\nGRUB_CMDLINE_LINUX_DEFAULT=\'quiet param="two words" mem_sleep_default=s2idle\'\n')
        self.installer.install(boot_parameters=True)
        main = self.installer.path("/etc/default/grub")
        addition = self.installer.path(suspend.GRUB_PATH)
        result = subprocess.run(["/bin/sh", "-c", '. "$1"; . "$2"; . "$2"; printf "%s\\n%s\\n" "$GRUB_CMDLINE_LINUX" "$GRUB_CMDLINE_LINUX_DEFAULT"', "sh", str(main), str(addition)], check=True, capture_output=True, text=True)
        self.assertIn('quiet param="two words" mem_sleep_default=s2idle', result.stdout)
        for option in suspend.PARAMETERS:
            self.assertEqual(result.stdout.count(option), 1)

    def test_failed_update_grub_retains_recovery_state(self):
        self.fail_grub = True
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                self.installer.install(boot_parameters=True)
        self.assertEqual(self.installer.read_state()["phase"], "installing")
        self.fail_grub = False
        self.installer.uninstall()
        self.assertFalse(self.installer.state_path.exists())
        self.assertEqual(self.installer.path(self.ssd + "/d3cold_allowed").read_text().strip(), "1")

    def test_failed_uninstall_can_be_retried(self):
        self.installer.install(boot_parameters=True)
        self.fail_grub = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.installer.uninstall()
        self.assertEqual(self.installer.read_state()["phase"], "removing")
        self.fail_grub = False
        self.installer.uninstall()
        self.assertFalse(self.installer.state_path.exists())

    def test_preexisting_zero_is_restored_as_zero(self):
        self.write(self.ssd + "/d3cold_allowed", "0\n")
        self.installer.install()
        self.installer.uninstall()
        self.assertEqual(self.installer.path(self.ssd + "/d3cold_allowed").read_text().strip(), "0")

    def test_status_distinguishes_configuration_from_running_boot(self):
        self.installer.install(boot_parameters=True)
        self.installer.status()
        self.assertIn("Reboot to activate", self.output.getvalue())
        self.write("/proc/cmdline", "quiet " + " ".join(suspend.PARAMETERS))
        self.installer.status()
        self.assertIn("All three tested boot settings are active", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()

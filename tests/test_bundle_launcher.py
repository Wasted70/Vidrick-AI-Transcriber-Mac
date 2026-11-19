"""Verify the Finder entry point without launching Tk or AppleScript."""

import sys
import unittest
from unittest.mock import patch

from vidrick_mac import bundle_launcher, preview
from vidrick_mac.environment import Environment


class BundleLauncherTests(unittest.TestCase):
    def test_finder_launch_uses_native_gui_even_with_platform_arguments(self):
        mac = Environment("Darwin", "arm64", (3, 12, 0), True)
        with patch.object(bundle_launcher, "inspect_environment", return_value=mac), \
                patch.object(preview, "launch_preview") as launch, \
                patch.object(sys, "argv", ["Vidrick", "-psn_0_12345"]):
            self.assertEqual(bundle_launcher.main(), 0)
        launch.assert_called_once_with()

    def test_windows_launch_refuses_before_gui_or_native_alert(self):
        windows = Environment("Windows", "AMD64", (3, 12, 0), True)
        with patch.object(bundle_launcher, "inspect_environment", return_value=windows), \
                patch.object(preview, "launch_preview") as launch, \
                patch.object(bundle_launcher.subprocess, "run") as run, \
                patch("sys.stderr") as stderr:
            self.assertEqual(bundle_launcher.main(), 2)
        launch.assert_not_called()
        run.assert_not_called()
        self.assertIn("requires macOS", stderr.write.call_args_list[0].args[0])

    def test_build_check_stays_separate_from_finder_startup(self):
        with patch.object(sys, "argv", ["Vidrick", "--bundle-check"]), \
                patch.object(bundle_launcher, "_check_bundle") as check, \
                patch.object(preview, "launch_preview") as launch:
            self.assertEqual(bundle_launcher.main(), 0)
        check.assert_called_once_with()
        launch.assert_not_called()

    def test_startup_error_is_forwarded_as_native_dialog_argument(self):
        mac = Environment("Darwin", "arm64", (3, 12, 0), True)
        failure = 'Tk failed to load a library named "test"'
        with patch.object(bundle_launcher, "inspect_environment", return_value=mac), \
                patch.object(preview, "launch_preview", side_effect=RuntimeError(failure)), \
                patch.object(bundle_launcher.subprocess, "run") as run, \
                patch.object(sys, "stderr", None):
            self.assertEqual(bundle_launcher.main(), 2)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/osascript", "-", failure])
        self.assertNotIn(failure, run.call_args.kwargs["input"])

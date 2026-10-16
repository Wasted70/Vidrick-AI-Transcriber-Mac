"""Offline starter tests: no real GUI, user data, or network access."""

import builtins
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib
import io
from pathlib import Path
import unittest
from unittest.mock import call, patch

from vidrick_mac import __main__ as cli
from vidrick_mac import environment, preview


def native_environment(**changes):
    values = dict(
        system="Darwin", machine="arm64", python_version=(3, 11, 0),
        tk_discoverable=True,
    )
    values.update(changes)
    return environment.Environment(**values)


@contextmanager
def forbid_gui_imports(include_preview=False):
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ("tkinter", "_tkinter") or name.startswith("tkinter."):
            raise AssertionError("A headless check attempted to import Tk.")
        if include_preview and name == "preview" and level == 1:
            raise AssertionError("A headless check attempted to import the preview.")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=guarded_import):
        yield


class EnvironmentTests(unittest.TestCase):
    def test_native_arm64_mac_passes_starter_checks(self):
        self.assertEqual(native_environment().issues(), ())

    def test_architecture_whitespace_and_case_are_normalized(self):
        self.assertEqual(native_environment(machine=" ARM64 ").issues(), ())

    def test_windows_arm64_is_not_a_supported_mac(self):
        issues = native_environment(system="Windows").issues()
        self.assertEqual(len(issues), 1)
        self.assertIn("not running macOS", issues[0])

    def test_other_operating_systems_are_rejected(self):
        for system in ("Linux", "", "Unknown"):
            with self.subTest(system=system):
                self.assertTrue(native_environment(system=system).issues())

    def test_x86_mac_message_preserves_rosetta_ambiguity(self):
        issues = native_environment(machine="x86_64").issues()
        self.assertEqual(len(issues), 1)
        self.assertIn("Intel or Rosetta", issues[0])
        self.assertIn("does not identify the physical chip", issues[0])

    def test_unknown_or_non_arm64_interpreter_is_rejected(self):
        for machine in ("", "unknown", "aarch64", "AMD64"):
            with self.subTest(machine=machine):
                self.assertTrue(native_environment(machine=machine).issues())

    def test_python_minimum_boundary(self):
        for version in ((3, 9, 20), (3, 10, 99)):
            with self.subTest(version=version):
                self.assertIn("Python 3.11", " ".join(
                    native_environment(python_version=version).issues()))
        for version in ((3, 11, 0), (3, 12, 14), (3, 14, 0)):
            with self.subTest(version=version):
                self.assertEqual(native_environment(python_version=version).issues(), ())

    def test_missing_tk_is_reported(self):
        self.assertIn("Tk modules were not found", " ".join(
            native_environment(tk_discoverable=False).issues()))

    def test_multiple_issues_are_reported_together(self):
        value = native_environment(
            system="Windows", python_version=(3, 10, 0), tk_discoverable=False)
        self.assertEqual(len(value.issues()), 3)

    def test_inspection_discovers_tk_without_importing_or_opening_it(self):
        with patch.object(environment.platform, "system", return_value="Darwin"), \
                patch.object(environment.platform, "machine", return_value="arm64"), \
                patch.object(environment.sys, "version_info", (3, 12, 1)), \
                patch.object(environment, "find_spec", return_value=object()) as discover, \
                forbid_gui_imports():
            result = environment.inspect_environment()
        self.assertEqual(result, native_environment(python_version=(3, 12, 1)))
        self.assertEqual(discover.call_args_list, [call("tkinter"), call("_tkinter")])

    def test_inspection_requires_both_tk_modules(self):
        for available in (set(), {"tkinter"}, {"_tkinter"}):
            with self.subTest(available=available), \
                    patch.object(environment, "find_spec", side_effect=lambda name:
                                 object() if name in available else None), \
                    forbid_gui_imports():
                self.assertFalse(environment.inspect_environment().tk_discoverable)

    def test_data_path_is_distinct_and_has_no_filesystem_effects(self):
        fake_home = Path("mock-home")
        with patch.object(Path, "home", return_value=fake_home), \
                patch.object(Path, "mkdir", side_effect=AssertionError("mkdir attempted")), \
                patch.object(Path, "open", side_effect=AssertionError("open attempted")), \
                patch.object(Path, "stat", side_effect=AssertionError("stat attempted")):
            expected = fake_home / "Library" / "Application Support" / \
                "Vidrick AI Transcriber Mac Preview"
            self.assertEqual(environment.preview_data_directory(), expected)
            self.assertEqual(environment.preview_data_directory(fake_home), expected)

    def test_passing_report_does_not_claim_mac_app_validation(self):
        report = environment.format_report(native_environment())
        self.assertIn("Preflight: PASSED", report)
        self.assertIn("opening the preview is the next manual check", report)
        self.assertIn("have NOT been validated", report)
        self.assertIn("no transcription", report)

    def test_failed_report_handles_unknown_platform_values(self):
        report = environment.format_report(native_environment(system="", machine=""))
        self.assertIn("Operating system: unknown", report)
        self.assertIn("Interpreter architecture: unknown", report)
        self.assertIn("Preflight: NOT READY", report)


class CommandTests(unittest.TestCase):
    def run_main(self, args, value):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "inspect_environment", return_value=value), \
                redirect_stdout(output), redirect_stderr(errors):
            result = cli.main(args)
        return result, output.getvalue(), errors.getvalue()

    def test_help_does_not_inspect_environment_or_import_gui(self):
        output = io.StringIO()
        with patch.object(cli, "inspect_environment") as inspect, \
                forbid_gui_imports(include_preview=True), redirect_stdout(output), \
                self.assertRaises(SystemExit) as ended:
            cli.main(["--help"])
        self.assertEqual(ended.exception.code, 0)
        inspect.assert_not_called()
        self.assertIn("--preview", output.getvalue())

    def test_default_and_check_never_import_or_launch_gui(self):
        for args in ([], ["--check"]):
            with self.subTest(args=args), forbid_gui_imports(include_preview=True):
                result, output, errors = self.run_main(args, native_environment())
            self.assertEqual(result, 0)
            self.assertIn("Preflight: PASSED", output)
            self.assertEqual(errors, "")

    def test_unsupported_preview_is_rejected_before_gui_import(self):
        for value in (
            native_environment(system="Windows"),
            native_environment(machine="x86_64"),
            native_environment(tk_discoverable=False),
            native_environment(python_version=(3, 10, 0)),
        ):
            with self.subTest(value=value), forbid_gui_imports(include_preview=True):
                result, output, _ = self.run_main(["--preview"], value)
            self.assertEqual(result, 2)
            self.assertIn("Preflight: NOT READY", output)

    def test_check_returns_failure_for_unsupported_host(self):
        with forbid_gui_imports(include_preview=True):
            result, _, _ = self.run_main(["--check"], native_environment(system="Windows"))
        self.assertEqual(result, 2)

    def test_supported_preview_calls_only_mocked_launcher(self):
        with patch.object(preview, "launch_preview") as launch, forbid_gui_imports():
            result, _, errors = self.run_main(["--preview"], native_environment())
        launch.assert_called_once_with()
        self.assertEqual(result, 0)
        self.assertEqual(errors, "")

    def test_tk_failure_is_reported_cleanly(self):
        for failure in (ImportError("Tk native library missing"), RuntimeError("no display")):
            with self.subTest(failure=failure), \
                    patch.object(preview, "launch_preview", side_effect=failure), \
                    forbid_gui_imports():
                result, _, errors = self.run_main(["--preview"], native_environment())
            self.assertEqual(result, 2)
            self.assertIn("Preview could not open:", errors)
            self.assertIn(str(failure), errors)
            self.assertNotIn("Traceback", errors)

    def test_modes_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ended:
            cli.main(["--check", "--preview"])
        self.assertEqual(ended.exception.code, 2)

    def test_direct_launcher_rechecks_host_before_tk_import(self):
        with patch.object(preview, "inspect_environment", return_value=
                          native_environment(system="Windows")), \
                forbid_gui_imports(), self.assertRaisesRegex(RuntimeError, "macOS"):
            preview.launch_preview()

    def test_module_imports_have_no_gui_side_effects(self):
        with forbid_gui_imports():
            importlib.reload(environment)
            importlib.reload(cli)
            importlib.reload(preview)


if __name__ == "__main__":
    unittest.main()

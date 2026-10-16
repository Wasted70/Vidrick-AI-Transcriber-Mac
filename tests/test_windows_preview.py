"""Tests for the explicit Windows-only UI preview; never open a real window."""

import builtins
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import MagicMock, patch

from vidrick_mac import __main__ as cli
from vidrick_mac import environment, preview


def windows_environment(**changes):
    values = dict(
        system="Windows", machine="AMD64", python_version=(3, 11, 0),
        tk_discoverable=True,
    )
    values.update(changes)
    return environment.Environment(**values)


@contextmanager
def forbid_preview_imports():
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name in ("tkinter", "_tkinter") or name.startswith("tkinter."):
            raise AssertionError("Unexpected Tk import.")
        if name == "preview" and level == 1:
            raise AssertionError("Unexpected preview import.")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=guarded):
        yield


class WindowsEnvironmentTests(unittest.TestCase):
    def test_explicit_windows_mode_allows_amd64_and_arm64(self):
        for machine in ("AMD64", "arm64"):
            with self.subTest(machine=machine):
                self.assertEqual(
                    windows_environment(machine=machine).windows_preview_issues(), ())

    def test_explicit_windows_mode_rejects_other_operating_systems(self):
        for system in ("Darwin", "Linux", "", "Unknown"):
            with self.subTest(system=system):
                issues = windows_environment(system=system).windows_preview_issues()
                self.assertTrue(issues)
                self.assertIn("Windows", " ".join(issues))

    def test_windows_mode_still_requires_supported_python(self):
        self.assertIn("Python 3.11", " ".join(windows_environment(
            python_version=(3, 10, 99)).windows_preview_issues()))

    def test_windows_mode_still_requires_tk(self):
        self.assertIn("Tk", " ".join(windows_environment(
            tk_discoverable=False).windows_preview_issues()))

    def test_native_mac_checks_still_reject_every_windows_architecture(self):
        for machine in ("AMD64", "arm64"):
            with self.subTest(machine=machine):
                self.assertIn("macOS", " ".join(
                    windows_environment(machine=machine).issues()))

    def test_windows_report_is_explicitly_not_a_mac_compatibility_test(self):
        report = environment.format_report(windows_environment(), windows_preview=True)
        self.assertIn("Windows development preview", report)
        self.assertIn("not a Mac compatibility test", report)
        self.assertIn("Preflight: PASSED", report)
        self.assertIn("have NOT been validated", report)

    def test_default_report_does_not_silently_enable_windows_mode(self):
        self.assertIn("Preflight: NOT READY",
                      environment.format_report(windows_environment()))


class WindowsCommandTests(unittest.TestCase):
    def build_mocked_ui(self, screen_width=1920, screen_height=1080, windows_preview=True):
        fake_tk, fake_ttk, fake_dialog = MagicMock(), MagicMock(), MagicMock()
        fake_tk.StringVar.side_effect = lambda *args, **kwargs: MagicMock()
        fake_tk.BooleanVar.side_effect = lambda *args, **kwargs: MagicMock()
        fake_root = MagicMock()
        fake_root.winfo_screenwidth.return_value = screen_width
        fake_root.winfo_screenheight.return_value = screen_height
        ui = preview.PreviewUI(
            fake_root, windows_preview=windows_preview, tk=fake_tk,
            ttk=fake_ttk, filedialog=fake_dialog)
        return ui, fake_root, fake_tk, fake_ttk, fake_dialog

    def run_main(self, args, value):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cli, "inspect_environment", return_value=value), \
                redirect_stdout(output), redirect_stderr(errors):
            result = cli.main(args)
        return result, output.getvalue(), errors.getvalue()

    def test_explicit_flag_passes_true_keyword_to_mocked_launcher(self):
        with patch.object(preview, "launch_preview") as launch:
            result, output, errors = self.run_main(
                ["--windows-preview"], windows_environment())
        launch.assert_called_once_with(windows_preview=True)
        self.assertEqual(result, 0)
        self.assertIn("not a Mac compatibility test", output)
        self.assertEqual(errors, "")

    def test_explicit_mode_rejects_invalid_hosts_before_gui_import(self):
        for value in (
            windows_environment(system="Darwin", machine="arm64"),
            windows_environment(system="Linux"),
            windows_environment(tk_discoverable=False),
            windows_environment(python_version=(3, 10, 0)),
        ):
            with self.subTest(value=value), forbid_preview_imports():
                result, output, _ = self.run_main(["--windows-preview"], value)
            self.assertEqual(result, 2)
            self.assertIn("Preflight: NOT READY", output)

    def test_default_check_and_mac_preview_still_reject_windows(self):
        for args in ([], ["--check"], ["--preview"]):
            with self.subTest(args=args), forbid_preview_imports():
                result, output, _ = self.run_main(args, windows_environment())
            self.assertEqual(result, 2)
            self.assertIn("Preflight: NOT READY", output)

    def test_preview_flags_are_mutually_exclusive(self):
        for args in (
            ["--windows-preview", "--check"],
            ["--windows-preview", "--preview"],
        ):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), \
                    patch.object(cli, "inspect_environment") as inspect, \
                    self.assertRaises(SystemExit) as ended:
                cli.main(args)
            self.assertEqual(ended.exception.code, 2)
            inspect.assert_not_called()

    def test_windows_preview_tk_failure_is_reported_without_traceback(self):
        with patch.object(preview, "launch_preview", side_effect=ImportError("Tk unavailable")):
            result, _, errors = self.run_main(
                ["--windows-preview"], windows_environment())
        self.assertEqual(result, 2)
        self.assertIn("Preview could not open: Tk unavailable", errors)
        self.assertNotIn("Traceback", errors)

    def test_direct_windows_launcher_rejects_mac_before_tk_import(self):
        with patch.object(preview, "inspect_environment", return_value=
                          windows_environment(system="Darwin", machine="arm64")), \
                forbid_preview_imports(), self.assertRaisesRegex(RuntimeError, "Windows"):
            preview.launch_preview(windows_preview=True)

    def test_launcher_injects_mocked_gui_into_preview_ui(self):
        fake_tk = ModuleType("tkinter")
        fake_tk.Tk = MagicMock()
        fake_tk.ttk = MagicMock()
        fake_tk.filedialog = MagicMock()
        with patch.dict(sys.modules, {"tkinter": fake_tk}), \
                patch.object(preview, "inspect_environment", return_value=windows_environment()), \
                patch.object(preview, "PreviewUI") as build_ui:
            preview.launch_preview(windows_preview=True)
        build_ui.assert_called_once_with(
            fake_tk.Tk.return_value, windows_preview=True, tk=fake_tk,
            ttk=fake_tk.ttk, filedialog=fake_tk.filedialog)
        fake_tk.Tk.return_value.mainloop.assert_called_once_with()
        fake_tk.Tk.return_value.destroy.assert_called_once_with()

    def test_mocked_windows_ui_is_labeled_and_manages_paths_without_reading_files(self):
        # Replace the complete GUI interface with mocks before executing any UI
        # construction. No Tk module is imported and no OS window is created.
        ui, fake_root, fake_tk, fake_ttk, fake_dialog = self.build_mocked_ui()
        fake_root.geometry.assert_called_once_with("1180x800")
        visible_labels = " ".join(
            str(item.kwargs.get("text", "")) for item in
            fake_tk.Label.call_args_list + fake_ttk.Label.call_args_list)
        self.assertIn("Windows development preview", visible_labels)
        self.assertIn("not a Mac compatibility test", visible_labels)
        buttons = {
            item.kwargs["text"]: item.kwargs
            for item in fake_tk.Button.call_args_list + fake_ttk.Button.call_args_list
            if "text" in item.kwargs
        }
        for title in ("Manage Account", "Check for Updates"):
            with self.subTest(button=title):
                self.assertEqual(buttons[title].get("state"), "disabled")
                self.assertFalse(buttons[title].get("command"))
        ui.pipeline.start_button.configure.assert_any_call(state="disabled")
        self.assertFalse(ui.pipeline.start())
        for title in ("Open…", "Save", "Save As…"):
            with self.subTest(editor_button=title):
                self.assertNotEqual(buttons[title].get("state"), "disabled")
                self.assertTrue(callable(buttons[title].get("command")))

        # All dialogs and widget calls remain mocks. Cancellation retains the
        # queue; remove and clear change only in-memory state.
        paths = ["synthetic/one.wav", "synthetic/two.mp4", "synthetic/three.mp3"]
        fake_dialog.askopenfilenames.side_effect = [tuple(paths), (), (paths[0],)]
        with patch("builtins.open", side_effect=AssertionError("File read attempted")), \
                patch.object(Path, "open", side_effect=AssertionError("Path read attempted")), \
                patch.object(Path, "stat", side_effect=AssertionError("Path inspected")):
            ui.choose_files()
            self.assertEqual(ui.state.files, paths)
            ui.choose_files()
            self.assertEqual(ui.state.files, paths)
            ui.choose_files()
            self.assertEqual(ui.state.files, paths)
            ui.file_list.selection.return_value = ("0", "2")
            ui.remove_selected()
            self.assertEqual(ui.state.files, [paths[1]])
            ui.clear_files()
            self.assertEqual(ui.state.files, [])
        ui.files_summary.set.assert_called_with(ui.state.summary)
        self.assertEqual(fake_dialog.askopenfilenames.call_count, 3)

    def test_file_actions_require_relevant_queue_and_selection(self):
        ui, _, _, _, _ = self.build_mocked_ui()
        ui.remove_button, ui.clear_button = MagicMock(), MagicMock()
        ui.file_list.selection.return_value = ()
        ui._update_file_actions()
        ui.remove_button.configure.assert_called_with(state="disabled")
        ui.clear_button.configure.assert_called_with(state="disabled")
        ui.state.add_files(["synthetic/one.wav"])
        ui._update_file_actions()
        ui.remove_button.configure.assert_called_with(state="disabled")
        ui.clear_button.configure.assert_called_with(state="normal")
        ui.file_list.selection.return_value = ("0",)
        ui._update_file_actions()
        ui.remove_button.configure.assert_called_with(state="normal")
        ui.path_detail.insert.assert_called_with("1.0", "synthetic/one.wav")

    def test_file_picker_reentry_and_failure_do_not_lock_future_browsing(self):
        ui, _, _, _, dialog = self.build_mocked_ui()

        def nested_picker(**kwargs):
            ui.choose_files()
            return ()

        dialog.askopenfilenames.side_effect = nested_picker
        ui.choose_files()
        self.assertEqual(dialog.askopenfilenames.call_count, 1)
        self.assertFalse(ui._file_dialog_open)
        dialog.askopenfilenames.side_effect = RuntimeError("test dialog failure")
        with self.assertRaisesRegex(RuntimeError, "test dialog failure"):
            ui.choose_files()
        self.assertFalse(ui._file_dialog_open)
        self.assertEqual(ui.state.files, [])

    def test_shortcut_modifiers_follow_mode_and_removal_is_scoped_to_list(self):
        for windows, modifier in ((True, "Control"), (False, "Command")):
            with self.subTest(windows_preview=windows):
                ui, root, _, _, _ = self.build_mocked_ui(windows_preview=windows)
                global_keys = [call.args[1] for call in root.bind_class.call_args_list]
                self.assertIn(f"<{modifier}-o>", global_keys)
                self.assertIn(f"<{modifier}-comma>", global_keys)
                self.assertIn(f"<{modifier}-f>", global_keys)
                self.assertIn(f"<{modifier}-s>", global_keys)
                self.assertIn(f"<{modifier}-Shift-S>", global_keys)
                self.assertNotIn("<Delete>", global_keys)
                self.assertNotIn("<BackSpace>", global_keys)
                self.assertNotIn(f"<{modifier}-a>", global_keys)
                ui.file_list.bind.assert_any_call("<Delete>", ui._remove_files_shortcut)
                ui.file_list.bind.assert_any_call(f"<{modifier}-a>", ui._select_all_files)

    def test_initial_geometry_leaves_room_on_small_laptop_screen(self):
        _, fake_root, _, _, _ = self.build_mocked_ui(screen_width=1366, screen_height=768)
        fake_root.geometry.assert_called_once_with("1180x668")
        fake_root.minsize.assert_called_once_with(940, 640)


if __name__ == "__main__":
    unittest.main()

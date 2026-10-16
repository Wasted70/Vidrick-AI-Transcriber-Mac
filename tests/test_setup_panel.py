"""Opt-in desktop setup checks with synthetic jobs and mocked preferences only."""

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from vidrick_mac.preview import PreviewUI
from vidrick_mac.setup_io import SetupError
from vidrick_mac.setup_panel import SetupPanel
from vidrick_mac.setup_service import MODELS
from vidrick_mac.transcription import Progress, TranscriptionCancelled
from vidrick_mac.transcription_job import JobEvent, JobManager


class FakeSetupManager:
    """Deliver worker events without downloads, installation, or tool execution."""

    def __init__(self):
        self.requests = []
        self.events = []
        self.active = False
        self.cancelling = False
        self.cancel_calls = 0
        self.job_id = 0
        self.start_error = None

    def start(self, request):
        if self.start_error is not None:
            raise self.start_error
        if self.active:
            return False
        self.requests.append(request)
        self.job_id += 1
        self.active = True
        self.cancelling = False
        return True

    def cancel(self):
        self.cancel_calls += 1
        if not self.active:
            return False
        self.cancelling = True
        return True

    def drain(self):
        events, self.events = self.events, []
        return events

    def emit(self, kind, payload=None, *, job_id=None):
        self.events.append(JobEvent(self.job_id if job_id is None else job_id, kind, payload))
        if kind != "progress":
            self.active = False
            self.cancelling = False


@unittest.skipUnless(os.environ.get("VIDRICK_TEST_TK") == "1", "Desktop Tk checks are opt-in")
class SetupPanelTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=output, prefix="setup-tk-")
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.dialogs, self.messages = MagicMock(), MagicMock()
        self.dialogs.askopenfilename.return_value = ""
        self.dialogs.askopenfilenames.return_value = ()
        self.dialogs.asksaveasfilename.return_value = ""
        self.messages.askyesno.return_value = False
        self.messages.askyesnocancel.return_value = None
        self.manager = FakeSetupManager()
        self.transcription_manager = FakeSetupManager()
        self.patches = [
            patch("vidrick_mac.setup_panel.JobManager", return_value=self.manager),
            patch("vidrick_mac.transcription_panel.JobManager", return_value=self.transcription_manager),
            patch("vidrick_mac.setup_panel.load_selection", return_value={}),
            patch("vidrick_mac.setup_panel.save_selection"),
            patch("vidrick_mac.setup_panel.run_setup", side_effect=AssertionError("No real setup in UI tests")),
        ]
        mocks = [item.start() for item in self.patches]
        self.load_selection, self.save_selection = mocks[2:4]
        self.ui = PreviewUI(self.root, windows_preview=True, tk=tk, ttk=ttk,
                            filedialog=self.dialogs, messagebox=self.messages)
        self.pipeline, self.editor = self.ui.pipeline, self.ui.editor_panel
        self.setup = self.pipeline.setup
        self.root.attributes("-alpha", 0)
        self.root.geometry("940x640")
        self.root.deiconify()
        self.root.update()

    def tearDown(self):
        try:
            self.pipeline.dispose()
            self.editor.cancel_refresh()
            self.root.destroy()
        except self.tk.TclError:
            pass
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def open_native(self):
        # Only UI decisions are switched. Both managers remain synthetic; no
        # host guard is patched and no real setup backend can be invoked.
        self.ui.windows_preview = False
        self.setup.open()
        self.setup.window.attributes("-alpha", 0)
        self.setup.window.geometry("600x560")
        self.root.update()

    def selection(self, label="previous"):
        result = {}
        for key, suffix in (("engine", "-cli"), ("model", ".bin"), ("converter", "-converter")):
            path = self.folder / (label + suffix)
            path.write_text("Synthetic setup fixture; never executable.", encoding="utf-8")
            result[key] = str(path)
        return result

    def current_selection(self):
        return {key: getattr(self.pipeline, key).get() for key in ("engine", "model", "converter")}

    def poll(self):
        if self.setup.poll_id is not None:
            self.root.after_cancel(self.setup.poll_id)
            self.setup.poll_id = None
        self.setup._poll()
        try:
            self.root.update_idletasks()
        except self.tk.TclError:
            pass

    def test_windows_startup_and_setup_actions_never_load_save_or_install(self):
        self.load_selection.assert_not_called()
        self.setup.open()
        self.setup.window.attributes("-alpha", 0)
        self.root.update()
        self.assertIn("Windows", self.setup.status.get())
        for button in (self.setup.install_button, self.setup.apple_button, self.setup.apply_button):
            self.assertEqual(str(button.cget("state")), "disabled")
            button.invoke()
        self.assertFalse(self.setup.start())
        self.assertFalse(self.setup.install_apple_tools())
        self.assertFalse(self.setup.use_existing())
        self.setup.choose_existing("model")
        self.assertEqual(self.manager.requests, [])
        self.save_selection.assert_not_called()
        self.dialogs.askopenfilename.assert_not_called()

    def test_native_startup_restores_selection_without_starting_a_download(self):
        previous = self.selection()
        previous["model_id"] = "small"
        self.load_selection.return_value = previous
        self.ui.windows_preview = False
        self.setup.dispose()
        self.setup = SetupPanel(self.pipeline, manager=self.manager)
        self.pipeline.setup = self.setup
        self.load_selection.assert_called_once_with()
        self.assertEqual(self.current_selection(), {key: previous[key] for key in self.setup.manual})
        self.assertEqual(self.setup.model_choice.get(), MODELS["small"].title)
        self.assertEqual(self.manager.requests, [])
        self.save_selection.assert_not_called()

    def test_summary_opens_one_window_and_model_choice_updates_download_size(self):
        self.ui.windows_preview = False
        self.setup.setup_button.invoke()
        self.setup.window.attributes("-alpha", 0)
        first = self.setup.window
        self.setup.open()
        self.assertIs(self.setup.window, first)
        self.assertEqual(len(self.setup.notebook.tabs()), 2)
        self.setup.model_choice.set(MODELS["tiny"].title)
        self.setup._model_changed()
        self.assertEqual(self.setup.model_id, "tiny")
        self.assertIn("78 MB", self.setup.download_note.get())
        self.assertEqual(self.manager.requests, [])

    def test_setup_controls_fit_small_window_on_both_tabs(self):
        self.open_native()
        window = self.setup.window
        for tab in self.setup.notebook.tabs():
            self.setup.notebook.select(tab)
            self.root.update()
            left, top = window.winfo_rootx(), window.winfo_rooty()
            right, bottom = left + window.winfo_width(), top + window.winfo_height()
            pending = list(window.winfo_children())
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if not widget.winfo_ismapped():
                    continue
                with self.subTest(tab=tab, widget=str(widget)):
                    self.assertGreaterEqual(widget.winfo_rootx(), left - 1)
                    self.assertGreaterEqual(widget.winfo_rooty(), top - 1)
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), right + 1)
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), bottom + 1)
                    parent = widget.master
                    self.assertGreaterEqual(widget.winfo_rootx(), parent.winfo_rootx() - 1)
                    self.assertGreaterEqual(widget.winfo_rooty(), parent.winfo_rooty() - 1)
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                         parent.winfo_rootx() + parent.winfo_width() + 1)
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                         parent.winfo_rooty() + parent.winfo_height() + 1)

    def test_success_applies_and_saves_once_only_after_completion(self):
        self.open_native()
        previous, completed = self.selection(), self.selection("completed")
        completed["model_id"] = "small"
        self.setup.apply_selection(previous)
        self.assertTrue(self.setup.start())
        self.save_selection.assert_not_called()
        self.assertEqual(self.current_selection(), previous)
        self.manager.emit("progress", Progress("download", "Downloading selected model", 37))
        self.poll()
        self.assertEqual(float(self.setup.progress.cget("value")), 37)
        self.assertIn("Downloading", self.setup.status.get())
        self.manager.emit("completed", completed)
        self.poll()
        self.save_selection.assert_called_once_with(completed)
        self.assertEqual(self.current_selection(), {key: completed[key] for key in previous})
        self.assertEqual(self.setup.status.get(), "Setup complete")
        self.assertEqual(float(self.setup.progress.cget("value")), 100)
        self.assertFalse(self.setup.running)

    def test_cancel_keeps_previous_selection_and_does_not_save(self):
        self.open_native()
        previous = self.selection()
        self.setup.apply_selection(previous)
        self.setup.start()
        self.setup.cancel()
        self.assertTrue(self.setup.running)
        self.assertTrue(self.manager.cancelling)
        self.assertFalse(self.setup.start())
        self.assertEqual(str(self.setup.cancel_button.cget("state")), "disabled")
        self.manager.emit("cancelled")
        self.poll()
        self.assertEqual(self.current_selection(), previous)
        self.assertFalse(self.setup.running)
        self.assertEqual(self.setup.status.get(), "Setup cancelled")
        self.save_selection.assert_not_called()

    def test_failure_preserves_previous_setup_and_allows_retry(self):
        self.open_native()
        previous = self.selection()
        self.setup.apply_selection(previous)
        self.setup.start()
        self.manager.emit("failed", "Synthetic checksum mismatch; retry setup.")
        self.poll()
        self.assertEqual(self.current_selection(), previous)
        self.assertIn("checksum", self.setup.error)
        self.assertEqual(str(self.setup.install_button.cget("state")), "normal")
        self.save_selection.assert_not_called()
        self.assertTrue(self.setup.start())
        self.assertEqual(self.setup.error, "")
        self.assertEqual(len(self.manager.requests), 2)

    def test_worker_start_error_leaves_previous_setup_and_no_poll(self):
        self.open_native()
        previous = self.selection()
        self.setup.apply_selection(previous)
        self.manager.start_error = RuntimeError("Synthetic worker could not start")
        self.assertFalse(self.setup.start())
        self.assertFalse(self.setup.running)
        self.assertIsNone(self.setup.poll_id)
        self.assertEqual(self.current_selection(), previous)
        self.assertIn("worker", self.setup.error)
        self.save_selection.assert_not_called()

    def test_setup_locks_transcription_but_editor_remains_usable(self):
        self.open_native()
        self.setup.apply_selection(self.selection())
        recording = str(self.folder / "synthetic.wav")
        self.ui.state.add_files([recording])
        self.ui.refresh_files(selected_paths=[recording])
        self.assertEqual(str(self.pipeline.start_button.cget("state")), "normal")
        self.setup.start()
        self.assertFalse(self.pipeline.start())
        self.assertEqual(str(self.pipeline.start_button.cget("state")), "disabled")
        for control, _ in self.setup.controls:
            self.assertEqual(str(control.cget("state")), "disabled")
        self.ui.select_tab("Editor")
        self.editor.text.insert("1.0", "Editor work continues during setup.")
        self.assertEqual(self.editor.content(), "Editor work continues during setup.")
        self.assertEqual(str(self.editor.text.cget("state")), "normal")
        self.assertTrue(self.setup.running)
        self.assertEqual(self.transcription_manager.requests, [])

    def test_existing_paths_cancelled_picker_and_unsaved_dialog_do_not_apply(self):
        self.open_native()
        previous = self.selection()
        self.setup.apply_selection(previous)
        self.dialogs.askopenfilename.return_value = ""
        self.setup.choose_existing("model")
        self.assertEqual(self.setup.manual["model"].get(), previous["model"])
        draft = self.selection("draft")
        self.dialogs.askopenfilename.return_value = draft["model"]
        self.setup.choose_existing("model")
        self.assertEqual(self.setup.manual["model"].get(), draft["model"])
        self.assertEqual(self.current_selection(), previous)
        self.setup.close()
        self.setup.open()
        self.setup.window.attributes("-alpha", 0)
        self.assertEqual(self.setup.manual["model"].get(), previous["model"])
        self.save_selection.assert_not_called()

    def test_existing_paths_commit_only_after_successful_save(self):
        self.open_native()
        previous, draft = self.selection(), self.selection("draft")
        self.setup.apply_selection(previous)
        for key, value in draft.items():
            self.setup.manual[key].set(value)
        self.save_selection.side_effect = SetupError("Synthetic preferences are read-only")
        self.assertFalse(self.setup.use_existing())
        self.assertEqual(self.current_selection(), previous)
        self.assertIn("read-only", self.setup.error)
        self.save_selection.side_effect = None
        self.assertTrue(self.setup.use_existing())
        self.assertEqual(self.current_selection(), draft)
        self.assertEqual(self.save_selection.call_count, 2)
        self.assertEqual(self.setup.error, "")

    def test_invalid_manual_path_never_overwrites_selection_or_preferences(self):
        self.open_native()
        previous = self.selection()
        self.setup.apply_selection(previous)
        self.setup.manual["model"].set(str(self.folder / "missing-model.bin"))
        self.assertFalse(self.setup.use_existing())
        self.assertEqual(self.current_selection(), previous)
        self.save_selection.assert_not_called()

    def test_open_dialog_guard_blocks_setup_mutations_and_close(self):
        self.open_native()
        self.setup.apply_selection(self.selection())
        self.ui._file_dialog_open = True
        try:
            self.assertFalse(self.setup.start())
            self.assertFalse(self.setup.install_apple_tools())
            self.assertFalse(self.setup.use_existing())
            self.setup.choose_existing("model")
            self.setup.close()
            self.assertTrue(self.setup.window.winfo_exists())
        finally:
            self.ui._file_dialog_open = False
        self.save_selection.assert_not_called()
        self.dialogs.askopenfilename.assert_not_called()
        self.assertEqual(self.manager.requests, [])

    def test_apple_installer_result_preserves_selection_and_never_saves(self):
        self.open_native()
        previous = self.selection()
        self.setup.apply_selection(previous)
        self.assertTrue(self.setup.install_apple_tools())
        self.assertEqual(self.manager.requests, ["apple_tools"])
        self.assertFalse(self.setup.allow_close())
        self.setup.cancel()
        self.assertEqual(self.manager.cancel_calls, 0)
        self.manager.emit("completed", {"apple_tools_requested": True})
        self.poll()
        self.assertEqual(self.current_selection(), previous)
        self.assertIn("installer opened", self.setup.status.get())
        self.save_selection.assert_not_called()

    def test_close_declined_keeps_setup_running_then_accept_waits_for_cleanup(self):
        self.open_native()
        self.setup.start()
        self.setup.close()
        self.assertTrue(self.setup.window.winfo_exists())
        self.assertEqual(self.manager.cancel_calls, 0)
        self.messages.askyesno.return_value = True
        self.setup.close()
        self.assertEqual(self.setup.close_when_done, "window")
        self.assertTrue(self.setup.window.winfo_exists())
        self.assertEqual(self.manager.cancel_calls, 1)
        self.manager.emit("cancelled")
        self.poll()
        self.assertIsNone(self.setup.window)
        self.assertTrue(self.root.winfo_exists())

    def test_cleanup_failure_after_close_request_keeps_window_and_error(self):
        self.open_native()
        self.setup.start()
        self.messages.askyesno.return_value = True
        self.setup.close()
        self.manager.emit("failed", "Synthetic cleanup failed; setup files retained.")
        self.poll()
        self.assertIsNone(self.setup.close_when_done)
        self.assertTrue(self.setup.window.winfo_exists())
        self.assertIn("cleanup failed", self.setup.error)

    def test_completion_inside_close_confirmation_is_saved_without_cancelling(self):
        self.open_native()
        completed = self.selection("completed")
        self.setup.start()

        def finish_inside_dialog(*args, **kwargs):
            self.manager.emit("completed", completed)
            self.poll()
            return True

        self.messages.askyesno.side_effect = finish_inside_dialog
        self.setup.close()
        self.assertIsNone(self.setup.window)
        self.assertEqual(self.current_selection(), completed)
        self.save_selection.assert_called_once_with(completed)
        self.assertEqual(self.manager.cancel_calls, 0)

    def test_failure_inside_close_confirmation_keeps_error_available(self):
        self.open_native()
        self.setup.start()

        def fail_inside_dialog(*args, **kwargs):
            self.manager.emit("failed", "Synthetic build failure")
            self.poll()
            return True

        self.messages.askyesno.side_effect = fail_inside_dialog
        self.setup.close()
        self.assertTrue(self.setup.window.winfo_exists())
        self.assertIn("build failure", self.setup.error)
        self.assertEqual(self.manager.cancel_calls, 0)

    def test_persistence_failure_after_completion_keeps_previous_selection_and_window(self):
        self.open_native()
        previous, completed = self.selection(), self.selection("completed")
        self.setup.apply_selection(previous)
        self.setup.start()
        self.save_selection.side_effect = SetupError("Synthetic preferences could not be saved")
        self.setup.close_when_done = "window"
        self.manager.emit("completed", completed)
        self.poll()
        self.assertEqual(self.current_selection(), previous)
        self.assertIsNone(self.setup.close_when_done)
        self.assertTrue(self.setup.window.winfo_exists())
        self.assertIn("preferences", self.setup.error)
        self.assertNotEqual(self.setup.status.get(), "Setup complete")

    def test_app_close_waits_for_worker_then_rechecks_editor_edits(self):
        self.open_native()
        entered, release = threading.Event(), threading.Event()

        def synthetic_worker(request, cancel, emit):
            entered.set()
            release.wait(5)
            if cancel.is_set():
                raise TranscriptionCancelled()
            return {}

        manager = JobManager(synthetic_worker)
        self.setup.manager = manager
        try:
            self.assertTrue(self.setup.start())
            self.assertTrue(entered.wait(1))
            self.editor.text.insert("1.0", "Keep these Editor changes.")
            self.messages.askyesno.return_value = True
            self.ui.close_preview()
            self.assertTrue(self.root.winfo_exists())
            self.assertTrue(manager.active)
            self.assertEqual(self.setup.close_when_done, "app")
            self.editor.text.insert("end", " Still responsive.")
            self.assertIn("Still responsive", self.editor.content())
            release.set()
            deadline = time.monotonic() + 2
            while manager.active and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(manager.active)
            self.poll()
            self.assertFalse(self.setup.running)
            self.assertTrue(self.root.winfo_exists())
            self.messages.askyesnocancel.assert_called_once()
            self.assertIn("Keep these", self.editor.content())
            self.save_selection.assert_not_called()
        finally:
            release.set()
            deadline = time.monotonic() + 2
            while manager.active and time.monotonic() < deadline:
                time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()

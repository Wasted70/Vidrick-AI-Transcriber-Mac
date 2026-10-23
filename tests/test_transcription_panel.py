"""Opt-in Tk workflow checks using synthetic jobs, never a real transcription engine."""

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from vidrick_mac.preview import PreviewUI
from vidrick_mac.transcription import Progress, TranscriptResult, TranscriptionError
from vidrick_mac.transcription_job import JobEvent, JobManager
from vidrick_mac.history_store import HistoryError


class FakeManager:
    """Controlled UI event source; performs no file access or background work."""

    def __init__(self):
        self.specs = []
        self.events = []
        self.active = False
        self.cancelling = False
        self.cancel_calls = 0
        self.start_error = None
        self.job_id = 0

    def start(self, spec):
        if self.start_error is not None:
            raise self.start_error
        if self.active:
            return False
        self.specs.append(spec)
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
class TranscriptionPanelTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=output, prefix="pipeline-tk-")
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.dialogs, self.messages = MagicMock(), MagicMock()
        self.dialogs.askopenfilename.return_value = ""
        self.dialogs.askopenfilenames.return_value = ()
        self.dialogs.asksaveasfilename.return_value = ""
        self.messages.askyesnocancel.return_value = None
        self.messages.askyesno.return_value = False
        self.manager = FakeManager()
        with patch("vidrick_mac.transcription_panel.JobManager", return_value=self.manager):
            self.ui = PreviewUI(self.root, windows_preview=True, tk=tk, ttk=ttk,
                                filedialog=self.dialogs, messagebox=self.messages)
        self.panel = self.ui.pipeline
        self.editor = self.ui.editor_panel
        # Exercise native-mode UI decisions on Windows only after installing the
        # synthetic manager. This does not test or enable the native backend.
        self.ui.windows_preview = False
        self.panel.engine.set(str(self.folder / "synthetic-cli"))
        self.panel.model.set(str(self.folder / "synthetic-model.bin"))
        self.recordings = [str(self.folder / "First.wav"), str(self.folder / "Second.wav")]
        self.ui.state.add_files(self.recordings)
        self.ui.refresh_files(selected_paths=[self.recordings[1]])
        self.root.attributes("-alpha", 0)
        self.root.geometry("940x640")
        self.root.deiconify()
        self.root.update()

    def tearDown(self):
        try:
            self.panel.dispose()
            self.editor.cancel_refresh()
            self.root.destroy()
        except self.tk.TclError:
            pass
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def poll(self):
        """Deliver events synchronously without retaining a duplicate Tk timer."""
        if self.panel.poll_id is not None:
            self.root.after_cancel(self.panel.poll_id)
            self.panel.poll_id = None
        self.panel._poll()
        self.root.update_idletasks()

    def finish(self, text="Finished synthetic transcript", name="Second.txt", timings=()):
        result = TranscriptResult(text, name, len(timings) or int(bool(text.strip())), "en", timings)
        self.manager.emit("completed", result)
        self.poll()
        return result

    def test_start_requires_one_selection_and_snapshots_selected_file_and_settings(self):
        self.ui.file_list.selection_set(("0", "1"))
        self.ui._update_file_actions()
        self.assertEqual(str(self.panel.start_button.cget("state")), "disabled")
        self.assertFalse(self.panel.start())
        self.ui.file_list.selection_set(("1",))
        self.panel.language.set("Japanese")
        self.panel.task.set("Translate to English")
        self.panel.timestamps.set(False)
        self.panel.converter.set(str(self.folder / "synthetic-ffmpeg"))
        self.ui._update_file_actions()
        self.panel.start_button.invoke()
        self.assertEqual(len(self.manager.specs), 1)
        spec = self.manager.specs[0]
        self.assertEqual(spec.recording, self.recordings[1])
        self.assertEqual(spec.language, "ja")
        self.assertTrue(spec.translate)
        self.assertFalse(spec.timestamps)
        self.assertEqual(spec.converter, str(self.folder / "synthetic-ffmpeg"))
        self.ui.file_list.selection_set(("0",))
        self.panel.language.set("English")
        self.panel.model.set("another-model.bin")
        self.assertEqual(spec.recording, self.recordings[1])
        self.assertEqual(spec.language, "ja")
        self.assertEqual(spec.model, str(self.folder / "synthetic-model.bin"))
        with self.assertRaises(FrozenInstanceError):
            spec.recording = self.recordings[0]
        self.assertFalse(self.panel.start())
        self.assertEqual(len(self.manager.specs), 1)

    def test_windows_guard_blocks_button_and_direct_callback_with_complete_setup(self):
        self.ui.windows_preview = True
        self.panel.update_controls()
        self.assertEqual(str(self.panel.start_button.cget("state")), "disabled")
        self.assertIn("Windows UI preview", self.panel.readiness.get())
        self.panel.start_button.invoke()
        self.assertFalse(self.panel.start())
        self.assertEqual(self.manager.specs, [])

    def test_running_locks_queue_and_settings_but_editor_can_edit_and_save(self):
        self.assertTrue(self.panel.start())
        for button in (self.ui.add_files_button, self.ui.remove_button, self.ui.clear_button):
            self.assertEqual(str(button.cget("state")), "disabled")
        for control, _ in self.panel.controls:
            self.assertEqual(str(control.cget("state")), "disabled")
        self.ui.choose_files()
        self.ui.remove_selected()
        self.ui.clear_files()
        self.panel.choose_tool(self.panel.engine, "engine")
        self.assertEqual(self.ui.state.files, self.recordings)
        self.dialogs.askopenfilename.assert_not_called()
        self.dialogs.askopenfilenames.assert_not_called()
        self.ui.select_tab("Editor")
        self.editor.text.insert("1.0", "Edited while a synthetic job runs")
        saved = self.folder / "work-in-progress.txt"
        self.dialogs.asksaveasfilename.return_value = str(saved)
        self.assertTrue(self.editor.save())
        self.assertEqual(saved.read_text(encoding="utf-8"), self.editor.content())
        self.assertTrue(self.panel.running)

    def test_real_progress_then_cancellation_waits_for_terminal_and_preserves_editor(self):
        self.editor.text.insert("1.0", "Keep these edits")
        self.assertTrue(self.panel.start())
        self.assertEqual(str(self.panel.progress.cget("mode")), "indeterminate")
        self.manager.emit("progress", Progress("transcribing", "Transcribing recording", 42))
        self.poll()
        self.assertEqual(str(self.panel.progress.cget("mode")), "determinate")
        self.assertEqual(float(self.panel.progress.cget("value")), 42)
        self.assertIn("42%", self.panel.status.get())
        self.panel.cancel_button.invoke()
        self.assertTrue(self.panel.running)
        self.assertTrue(self.manager.cancelling)
        self.assertEqual(str(self.panel.cancel_button.cget("state")), "disabled")
        self.manager.emit("progress", Progress("transcribing", "Late progress", 80))
        self.poll()
        self.assertIn("Cancelling", self.panel.status.get())
        self.assertEqual(str(self.ui.clear_button.cget("state")), "disabled")
        self.manager.emit("cancelled")
        self.poll()
        self.assertFalse(self.panel.running)
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.panel.status.get(), "Transcription cancelled")
        self.assertEqual(str(self.ui.clear_button.cget("state")), "normal")
        self.assertEqual(self.editor.content(), "Keep these edits")
        self.messages.askyesnocancel.assert_not_called()

    def test_completion_opens_new_unsaved_document_and_selects_editor(self):
        self.assertTrue(self.panel.start())
        result = self.finish("Hello 🌍\nFinished transcript")
        self.assertFalse(self.panel.running)
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.editor.content(), result.text)
        self.assertTrue(self.editor.dirty)
        self.assertIsNone(self.editor.document)
        self.assertEqual(self.editor.suggested_name, "Second.txt")
        self.assertEqual(self.ui.current_tab, "Editor")
        self.assertEqual(float(self.panel.progress.cget("value")), 100)
        self.assertEqual(self.ui.tab_buttons["Transcribe"].cget("text"), "Transcribe")

    def test_completion_preserves_timing_in_history_and_editor_handoff(self):
        self.assertTrue(self.panel.start())
        timings = ((25, 1050), (1600, 2300))
        with patch.object(self.editor, "load_transcription", wraps=self.editor.load_transcription) as load:
            result = self.finish("First cue\nSecond cue\n", timings=timings)
        load.assert_called_once_with(result.text, result.suggested_name, timings=timings)
        entries = self.ui.history_store.list_entries()
        self.assertEqual(len(entries), 1)
        saved = self.ui.history_store.get_entry(entries[0].id)
        self.assertEqual(saved.text, result.text)
        self.assertEqual(saved.timings, timings)
        self.assertIsNone(self.panel.pending)

    def test_history_retry_keeps_timing_after_storage_failure(self):
        self.assertTrue(self.panel.start())
        timings = ((200, 1400),)
        with patch.object(self.ui.history_store, "add_result", side_effect=HistoryError("Synthetic disk full")):
            result = self.finish("Timed cue\n", timings=timings)
        self.assertIs(self.panel.pending, result)
        self.assertEqual(self.panel.pending.timings, timings)
        self.assertTrue(self.panel.retry_history())
        entry = self.ui.history_store.get_entry(self.panel.pending_history_id)
        self.assertEqual(entry.timings, timings)
        with patch.object(self.editor, "load_transcription", wraps=self.editor.load_transcription) as load:
            self.assertTrue(self.panel.open_result())
        load.assert_called_once_with(result.text, result.suggested_name, timings=timings)

    def test_completed_result_waits_during_dialog_then_cancelled_handoff_keeps_it(self):
        self.editor.text.insert("1.0", "Current unsaved work")
        self.assertTrue(self.panel.start())
        self.ui._file_dialog_open = True
        result = self.finish()
        self.assertIs(self.panel.pending, result)
        self.assertEqual(self.editor.content(), "Current unsaved work")
        self.messages.askyesnocancel.assert_not_called()
        self.ui._file_dialog_open = False
        self.assertFalse(self.panel.open_result())
        self.assertIs(self.panel.pending, result)
        self.assertFalse(self.panel.start())
        self.assertEqual(self.editor.content(), "Current unsaved work")
        self.assertEqual(self.ui.tab_buttons["Transcribe"].cget("text"), "Transcribe •")
        self.messages.askyesnocancel.return_value = False
        self.assertTrue(self.panel.open_result())
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.editor.content(), result.text)

    def test_editor_busy_defers_completion_until_open_button_is_used(self):
        self.assertTrue(self.panel.start())
        self.editor.busy = True
        result = self.finish()
        self.assertIs(self.panel.pending, result)
        self.assertEqual(self.editor.content(), "")
        self.assertFalse(self.panel.open_result())
        self.editor.busy = False
        self.panel.open_button.invoke()
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.editor.content(), result.text)

    def test_no_speech_preserves_editor_and_allows_another_job(self):
        self.editor.text.insert("1.0", "Keep existing document")
        self.assertTrue(self.panel.start())
        self.finish(" \n")
        self.assertEqual(self.panel.status.get(), "Finished · no speech found")
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.editor.content(), "Keep existing document")
        self.messages.askyesnocancel.assert_not_called()
        self.assertTrue(self.panel.start())

    def test_failed_job_exposes_details_and_can_retry_without_losing_editor(self):
        self.editor.text.insert("1.0", "Keep existing document")
        self.assertTrue(self.panel.start())
        self.manager.emit("failed", "Synthetic model could not load")
        self.poll()
        self.assertFalse(self.panel.running)
        self.assertIsNone(self.panel.pending)
        self.assertIn("could not finish", self.panel.status.get())
        self.panel.details_button.invoke()
        self.messages.showerror.assert_called_once()
        self.assertIn("Synthetic model", self.messages.showerror.call_args.args[1])
        self.assertEqual(self.editor.content(), "Keep existing document")
        self.assertTrue(self.panel.start())
        self.assertEqual(self.panel.error, "")
        self.assertEqual(len(self.manager.specs), 2)

    def test_start_failure_and_modal_guard_are_retryable(self):
        self.ui._file_dialog_open = True
        self.assertFalse(self.panel.start())
        self.assertEqual(self.manager.specs, [])
        self.ui._file_dialog_open = False
        self.manager.start_error = TranscriptionError("Synthetic thread could not start")
        self.assertFalse(self.panel.start())
        self.assertFalse(self.panel.running)
        self.assertEqual(self.panel.status.get(), "Could not start transcription")
        self.manager.start_error = None
        self.assertTrue(self.panel.start())

    def test_discard_requires_confirmation_and_leaves_editor_untouched(self):
        self.editor.text.insert("1.0", "Keep current work")
        self.assertTrue(self.panel.start())
        result = self.finish()
        self.assertIs(self.panel.pending, result)
        self.panel.discard_button.invoke()
        self.assertIs(self.panel.pending, result)
        self.messages.askyesno.return_value = True
        self.panel.discard_button.invoke()
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.editor.content(), "Keep current work")
        self.assertTrue(self.panel.start())

    def test_close_decline_keeps_job_running_and_accept_waits_for_worker_terminal(self):
        self.assertTrue(self.panel.start())
        with patch.object(self.root, "destroy") as destroy:
            self.ui.close_preview()
            self.assertFalse(self.panel.close_after_cancel)
            self.assertEqual(self.manager.cancel_calls, 0)
            self.messages.askyesno.return_value = True
            self.ui.close_preview()
            self.assertTrue(self.panel.close_after_cancel)
            self.assertTrue(self.panel.running)
            self.assertTrue(self.manager.cancelling)
            destroy.assert_not_called()
            self.ui.close_preview()
            self.assertEqual(self.manager.cancel_calls, 1)
            self.manager.emit("cancelled")
            self.poll()
            destroy.assert_called_once()
            self.assertFalse(self.panel.close_after_cancel)
            self.assertIsNone(self.panel.poll_id)

    def test_close_after_cancellation_still_honors_editor_cancel(self):
        self.editor.text.insert("1.0", "Keep unsaved work")
        self.assertTrue(self.panel.start())
        self.messages.askyesno.return_value = True
        with patch.object(self.root, "destroy") as destroy:
            self.ui.close_preview()
            self.manager.emit("cancelled")
            self.poll()
            destroy.assert_not_called()
        self.assertFalse(self.panel.running)
        self.assertFalse(self.panel.close_after_cancel)
        self.assertEqual(self.editor.content(), "Keep unsaved work")
        self.messages.askyesnocancel.assert_called_once()

    def test_waiting_result_is_preserved_if_editor_cancels_close(self):
        self.editor.text.insert("1.0", "Keep unsaved work")
        self.assertTrue(self.panel.start())
        result = self.finish()
        self.assertIs(self.panel.pending, result)
        self.messages.askyesno.return_value = True
        with patch.object(self.root, "destroy") as destroy:
            self.ui.close_preview()
            destroy.assert_not_called()
        self.assertIs(self.panel.pending, result)
        self.assertEqual(self.editor.content(), "Keep unsaved work")
        self.assertFalse(self.panel.start())

    def test_completion_inside_close_confirmation_preserves_waiting_result(self):
        self.assertTrue(self.panel.start())
        result = TranscriptResult("Finished during close confirmation", "Second.txt", 1)

        def confirm(title, *_args, **_kwargs):
            if title == "Transcription is running":
                self.assertTrue(self.ui._file_dialog_open)
                self.manager.emit("completed", result)
                self.poll()
                return True
            self.assertEqual(title, "Saved finished transcript")
            return False

        self.messages.askyesno.side_effect = confirm
        with patch.object(self.root, "destroy") as destroy:
            self.ui.close_preview()
            destroy.assert_not_called()
        self.assertFalse(self.panel.running)
        self.assertFalse(self.panel.close_after_cancel)
        self.assertIs(self.panel.pending, result)
        self.assertEqual(self.manager.cancel_calls, 0)
        self.assertEqual(self.messages.askyesno.call_count, 2)

    def test_no_speech_inside_close_confirmation_closes_without_stale_cancel_flag(self):
        self.assertTrue(self.panel.start())

        def confirm(*_args, **_kwargs):
            self.assertTrue(self.ui._file_dialog_open)
            self.manager.emit("completed", TranscriptResult("", "Second.txt", 0))
            self.poll()
            return True

        self.messages.askyesno.side_effect = confirm
        with patch.object(self.root, "destroy") as destroy:
            self.ui.close_preview()
            destroy.assert_called_once()
        self.assertFalse(self.panel.running)
        self.assertFalse(self.panel.close_after_cancel)
        self.assertIsNone(self.panel.pending)
        self.assertEqual(self.manager.cancel_calls, 0)

    def test_result_is_saved_once_before_editor_handoff_with_frozen_metadata(self):
        self.assertTrue(self.panel.start())
        self.panel.model.set("changed-after-start.bin")
        self.editor.busy = True
        result = self.finish("Durable result 🌍")
        entries = self.ui.history_store.list_entries()
        self.assertEqual(len(entries), 1)
        entry = self.ui.history_store.get_entry(entries[0].id)
        self.assertEqual(entry.text, result.text)
        self.assertEqual(entry.model, "synthetic-model.bin")
        self.assertEqual(entry.language, "en")
        self.assertTrue(self.panel.retry_history() is False)  # Editor is busy.
        self.editor.busy = False
        self.assertTrue(self.panel.retry_history())
        self.assertTrue(self.panel.open_result())
        self.assertEqual(len(self.ui.history_store.list_entries()), 1)

    def test_history_failure_retains_result_and_retry_persists_before_handoff(self):
        self.assertTrue(self.panel.start())
        with patch.object(self.ui.history_store, "add_result", side_effect=HistoryError("Synthetic disk full")):
            result = self.finish("Retain on failure")
        self.assertIs(self.panel.pending, result)
        self.assertEqual(self.editor.content(), "")
        self.assertIn("History save failed", self.panel.status.get())
        self.assertTrue(self.panel.retry_history_button.winfo_manager())
        self.assertTrue(self.panel.details_button.winfo_manager())
        self.assertFalse(self.panel.start())
        self.assertTrue(self.panel.retry_history())
        self.assertIs(self.panel.pending, result)
        self.assertTrue(self.panel.open_result())
        self.assertEqual(self.editor.content(), result.text)
        self.assertEqual(len(self.ui.history_store.list_entries()), 1)

    def test_manual_save_fallback_keeps_failed_history_result_until_retry(self):
        self.assertTrue(self.panel.start())
        with patch.object(self.ui.history_store, "add_result", side_effect=HistoryError("Synthetic disk full")):
            result = self.finish("Fallback text")
        self.assertTrue(self.panel.open_result())
        self.assertIs(self.panel.pending, result)
        self.assertEqual(self.editor.content(), result.text)
        self.assertTrue(self.panel.retry_history())
        self.assertIsNone(self.panel.pending)
        self.assertEqual(len(self.ui.history_store.list_entries()), 1)

    def test_empty_cancelled_and_failed_jobs_do_not_create_history(self):
        self.assertTrue(self.panel.start())
        self.finish(" ")
        self.assertTrue(self.panel.start())
        self.manager.emit("cancelled")
        self.poll()
        self.assertTrue(self.panel.start())
        self.manager.emit("failed", "Synthetic failure")
        self.poll()
        self.assertEqual(self.ui.history_store.list_entries(), [])

    def test_dismissing_result_keeps_saved_history_and_original_recording(self):
        recording = Path(self.recordings[1])
        recording.write_text("Synthetic recording marker", encoding="utf-8")
        self.editor.text.insert("1.0", "Keep current work")
        self.assertTrue(self.panel.start())
        self.finish()
        self.messages.askyesno.return_value = True
        self.panel.discard_result()
        self.assertIsNone(self.panel.pending)
        self.assertEqual(len(self.ui.history_store.list_entries()), 1)
        self.assertEqual(recording.read_text(encoding="utf-8"), "Synthetic recording marker")

    def test_deleting_waiting_result_history_copy_updates_close_and_retry_state(self):
        self.editor.text.insert("1.0", "Keep current work")
        self.assertTrue(self.panel.start())
        result = self.finish()
        self.assertIs(self.panel.pending, result)
        saved_id = self.panel.pending_history_id
        self.ui.select_tab("History")
        self.ui.history_panel.tree.selection_set(saved_id)
        self.messages.askyesno.return_value = True
        self.assertTrue(self.ui.history_panel.delete_selected())
        self.assertIsNone(self.panel.pending_history_id)
        self.assertIn("History copy deleted", self.panel.status.get())
        self.assertEqual(self.ui.history_store.list_entries(), [])
        self.messages.askyesno.return_value = False
        self.assertFalse(self.panel.allow_close())
        self.assertEqual(self.messages.askyesno.call_args.args[0], "Unsaved finished transcript")
        self.assertTrue(self.panel.retry_history())
        self.assertEqual(len(self.ui.history_store.list_entries()), 1)

    def test_cleanup_failure_after_close_cancellation_keeps_error_visible(self):
        self.assertTrue(self.panel.start())
        self.messages.askyesno.return_value = True
        with patch.object(self.root, "destroy") as destroy:
            self.ui.close_preview()
            self.assertTrue(self.panel.close_after_cancel)
            self.manager.emit("failed", "Could not remove the synthetic temporary job folder")
            self.poll()
            destroy.assert_not_called()
        self.assertFalse(self.panel.running)
        self.assertFalse(self.panel.close_after_cancel)
        self.assertIn("temporary job folder", self.panel.error)
        self.assertIn("could not finish", self.panel.status.get())

    def test_cleanup_failure_inside_close_confirmation_keeps_error_visible(self):
        self.assertTrue(self.panel.start())

        def confirm(*_args, **_kwargs):
            self.manager.emit("failed", "Could not remove the synthetic temporary job folder")
            self.poll()
            return True

        self.messages.askyesno.side_effect = confirm
        with patch.object(self.root, "destroy") as destroy:
            self.ui.close_preview()
            destroy.assert_not_called()
        self.assertFalse(self.panel.running)
        self.assertFalse(self.panel.close_after_cancel)
        self.assertIn("temporary job folder", self.panel.error)

    def test_real_job_manager_delivers_synthetic_backend_result_through_ui_poll(self):
        entered, release = threading.Event(), threading.Event()
        observed = []
        result = TranscriptResult("Synthetic worker result", "Worker.txt", 1)

        def backend(spec, cancel, emit):
            observed.append(threading.get_ident())
            emit(Progress("transcribing", "Synthetic worker progress", 35))
            entered.set()
            release.wait(3)
            return result

        manager = JobManager(backend)
        self.panel.manager = manager
        try:
            self.assertTrue(self.panel.start())
            self.assertTrue(entered.wait(2))
            self.poll()
            self.assertIn("35%", self.panel.status.get())
            self.assertNotEqual(observed, [threading.get_ident()])
            release.set()
            deadline = time.monotonic() + 3
            while manager.active and time.monotonic() < deadline:
                threading.Event().wait(0.005)
            self.assertFalse(manager.active)
            self.poll()
            self.assertEqual(self.editor.content(), result.text)
            self.assertFalse(self.panel.running)
            self.assertIsNone(self.panel.pending)
        finally:
            manager.cancel()
            release.set()
            deadline = time.monotonic() + 3
            while manager.active and time.monotonic() < deadline:
                threading.Event().wait(0.005)
            self.assertFalse(manager.active, "Synthetic worker did not finish")


if __name__ == "__main__":
    unittest.main()

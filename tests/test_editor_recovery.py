"""Autosave lifecycle checks using synthetic text and project-local test files."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from vidrick_mac.editor import TranscriptEditor
from vidrick_mac.history_store import HistoryError, HistoryStore, MAX_DRAFTS
from vidrick_mac.transcript_io import LoadedTranscript, TranscriptError


class Scheduler:
    """Deterministic Tk-after stand-in; continuous typing needs no real sleeps."""

    def __init__(self):
        self.now = 0
        self.sequence = 0
        self.pending = {}

    def after(self, delay, callback):
        self.sequence += 1
        self.pending[self.sequence] = (self.now + delay, callback)
        return self.sequence

    def after_cancel(self, identifier):
        self.pending.pop(identifier, None)

    def advance(self, elapsed):
        target = self.now + elapsed
        while self.pending:
            identifier = min(self.pending, key=lambda key: self.pending[key][0])
            due, callback = self.pending[identifier]
            if due > target:
                break
            self.now = due
            del self.pending[identifier]
            callback()
        self.now = target


class EditorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store = HistoryStore()
        self.addCleanup(self.store.close)

    def make_editor(self, text="Unsaved draft", saved="", store=None):
        editor = TranscriptEditor.__new__(TranscriptEditor)
        editor.root = Scheduler()
        editor.tk = SimpleNamespace(TclError=RuntimeError)
        editor.ui = SimpleNamespace(
            history_store=store or self.store, history_panel=SimpleNamespace(refresh=MagicMock()),
            _file_dialog_open=False, filedialog=MagicMock(), messagebox=MagicMock(), select_tab=MagicMock())
        editor.ui.messagebox.askyesnocancel.return_value = None
        editor.ui.filedialog.askopenfilename.return_value = ""
        editor.ui.filedialog.asksaveasfilename.return_value = ""
        editor.document = None
        editor.saved_text = saved
        editor.suggested_name = "Synthetic.txt"
        editor.busy = False
        editor.refresh_id = None
        editor.refresh = MagicMock()
        editor.search_visible = False
        editor.find_panel = MagicMock()
        editor.find_text = MagicMock()
        editor.replace_text = MagicMock()
        editor.match_case = MagicMock()
        editor.match_label = MagicMock()
        editor.text = MagicMock()
        editor.text.value = text
        editor.text.get.side_effect = lambda *_: editor.text.value
        editor.text.delete.side_effect = lambda *_: setattr(editor.text, "value", "")
        editor.text.insert.side_effect = lambda _, value: setattr(editor.text, "value", value)
        editor._draft_id = uuid4().hex
        editor._draft_written = False
        editor._last_recovery_text = None
        editor._recovery_after = None
        editor._recovery_deadline = None
        editor._recovery_disposed = False
        editor.recovery_status = MagicMock()
        return editor

    def draft_text(self, editor):
        return self.store.get_draft(editor.current_draft_id).text

    def test_clean_start_does_not_create_draft_or_storage_write(self):
        editor = self.make_editor(text="")
        with patch.object(self.store, "save_draft") as save:
            self.assertTrue(editor.flush_recovery())
        save.assert_not_called()
        self.assertEqual(self.store.list_drafts(), [])

    def test_edit_bursts_debounce_and_continuous_typing_has_three_second_cap(self):
        editor = self.make_editor()
        editor._schedule_recovery()
        editor.root.advance(600)
        editor.text.value = "Second edit"
        editor._schedule_recovery()
        editor.root.advance(600)
        self.assertEqual(self.store.list_drafts(), [])
        editor.root.advance(150)
        self.assertEqual(self.draft_text(editor), "Second edit")
        for number in range(6):
            editor.text.value = f"Continuous edit {number}"
            editor._schedule_recovery()
            editor.root.advance(500)
        self.assertEqual(self.draft_text(editor), "Continuous edit 5")
        self.assertEqual(editor.root.pending, {})

    def test_only_basename_is_kept_and_external_file_is_not_overwritten(self):
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output, prefix="recovery-") as folder:
            path = Path(folder) / "source.txt"
            path.write_text("Original source", encoding="utf-8")
            editor = self.make_editor()
            editor.document = LoadedTranscript(path, "Original source", "utf-8", "\n", "fingerprint")
            self.assertTrue(editor.flush_recovery())
            draft = self.store.get_draft(editor.current_draft_id)
            self.assertEqual(draft.title, "source.txt")
            self.assertEqual(path.read_text(encoding="utf-8"), "Original source")
            self.assertNotIn(str(folder), str(draft))

    def test_failed_autosave_retains_previous_snapshot_and_current_memory_then_retries(self):
        editor = self.make_editor()
        editor.flush_recovery()
        editor.text.value = "Newer unsaved text"
        with patch.object(self.store, "save_draft", side_effect=HistoryError("Disk full")):
            self.assertFalse(editor.flush_recovery())
        self.assertEqual(self.draft_text(editor), "Unsaved draft")
        self.assertEqual(editor.content(), "Newer unsaved text")
        editor.recovery_status.set.assert_called_with("Recovery unavailable · Save a copy")
        self.assertTrue(editor.flush_recovery())
        self.assertEqual(self.draft_text(editor), "Newer unsaved text")

    def test_undo_to_saved_text_refreshes_snapshot_instead_of_leaving_stale_edits(self):
        editor = self.make_editor(text="Edited", saved="Original")
        editor.flush_recovery()
        editor.text.value = "Original"
        editor.flush_recovery()
        self.assertEqual(self.draft_text(editor), "Original")
        self.assertFalse(editor.dirty)

    def test_failed_open_after_discard_preserves_draft_and_document(self):
        editor = self.make_editor()
        editor.ui.messagebox.askyesnocancel.return_value = False
        editor.ui.filedialog.askopenfilename.return_value = "synthetic.txt"
        identifier = editor.current_draft_id
        with patch("vidrick_mac.editor.read_transcript", side_effect=TranscriptError("Unavailable")):
            self.assertFalse(editor.open())
        self.assertEqual(editor.current_draft_id, identifier)
        self.assertEqual(self.draft_text(editor), "Unsaved draft")
        self.assertEqual(editor.content(), "Unsaved draft")

    def test_successful_open_discards_only_prior_current_draft(self):
        editor = self.make_editor()
        editor.flush_recovery()
        prior = editor.current_draft_id
        other = self.store.save_draft(uuid4().hex, "Other session", "Other.txt")
        editor.ui.messagebox.askyesnocancel.return_value = False
        editor.ui.filedialog.askopenfilename.return_value = "synthetic.txt"
        opened = LoadedTranscript(Path("synthetic.txt"), "Opened", "utf-8", "\n", "fingerprint")
        with patch("vidrick_mac.editor.read_transcript", return_value=opened):
            self.assertTrue(editor.open())
        self.assertNotEqual(editor.current_draft_id, prior)
        self.assertEqual([draft.id for draft in self.store.list_drafts()], [other.id])

    def test_cancelled_and_failed_saves_keep_recoverable_edits(self):
        editor = self.make_editor()
        self.assertFalse(editor.save())
        self.assertEqual(self.draft_text(editor), "Unsaved draft")
        editor.ui.filedialog.asksaveasfilename.return_value = "synthetic.txt"
        with patch("vidrick_mac.editor.write_transcript", side_effect=TranscriptError("Disk full")):
            self.assertFalse(editor.save())
        self.assertEqual(self.draft_text(editor), "Unsaved draft")

    def test_external_save_success_is_still_success_if_draft_cleanup_fails(self):
        editor = self.make_editor()
        editor.ui.filedialog.asksaveasfilename.return_value = "synthetic.txt"
        saved = LoadedTranscript(Path("synthetic.txt"), editor.content(), "utf-8", "\n", "fingerprint")
        with patch("vidrick_mac.editor.write_transcript", return_value=saved), \
                patch.object(self.store, "delete_draft", side_effect=HistoryError("Locked")):
            self.assertTrue(editor.save())
        self.assertFalse(editor.dirty)
        editor.recovery_status.set.assert_called_with("Recovery cleanup unavailable")
        editor.ui.messagebox.showerror.assert_not_called()

    def test_successful_save_removes_current_draft_and_cancels_scheduled_write(self):
        editor = self.make_editor()
        editor._schedule_recovery()
        editor.ui.filedialog.asksaveasfilename.return_value = "synthetic.txt"
        saved = LoadedTranscript(Path("synthetic.txt"), editor.content(), "utf-8", "\n", "fingerprint")
        with patch("vidrick_mac.editor.write_transcript", return_value=saved):
            self.assertTrue(editor.save())
        self.assertEqual(self.store.list_drafts(), [])
        editor.root.advance(4000)
        self.assertEqual(self.store.list_drafts(), [])

    def test_cancel_or_interrupted_close_keeps_draft_until_final_close_commit(self):
        editor = self.make_editor()
        self.assertFalse(editor.confirm_close())
        self.assertEqual(self.draft_text(editor), "Unsaved draft")
        editor.ui.messagebox.askyesnocancel.return_value = False
        self.assertTrue(editor.confirm_close())
        self.assertEqual(self.draft_text(editor), "Unsaved draft")
        # Other app close guards can still reject closing at this point.
        self.assertTrue(editor.finish_close())
        self.assertEqual(self.store.list_drafts(), [])
        self.assertTrue(editor._recovery_disposed)

    def test_final_close_cleanup_error_keeps_editor_open_and_recovery_intact(self):
        editor = self.make_editor()
        editor.flush_recovery()
        with patch.object(self.store, "delete_draft", side_effect=HistoryError("Locked")):
            self.assertFalse(editor.finish_close())
        self.assertFalse(editor._recovery_disposed)
        self.assertEqual(self.draft_text(editor), "Unsaved draft")
        editor.ui.messagebox.showerror.assert_called_once()

    def test_dispose_cancels_callbacks_without_deleting_crash_recovery(self):
        editor = self.make_editor()
        editor.flush_recovery()
        editor._schedule_recovery()
        editor.dispose_recovery()
        self.assertEqual(editor.root.pending, {})
        self.assertEqual(self.draft_text(editor), "Unsaved draft")

    def test_finished_result_and_history_open_receive_immediate_new_snapshot(self):
        editor = self.make_editor()
        editor.flush_recovery()
        previous = editor.current_draft_id
        editor.ui.messagebox.askyesnocancel.return_value = False
        self.assertTrue(editor.load_transcription("Finished 🌍\r\ntext", "Finished.txt"))
        self.assertNotEqual(editor.current_draft_id, previous)
        self.assertEqual(self.draft_text(editor), "Finished 🌍\ntext")
        self.assertEqual(len(self.store.list_drafts()), 1)

    def test_recovery_cancel_keeps_selected_copy_and_current_edits(self):
        draft = self.store.save_draft(uuid4().hex, "Recover this", "Recovered.txt")
        editor = self.make_editor()
        self.assertFalse(editor.recover_draft(draft))
        self.assertEqual(editor.content(), "Unsaved draft")
        self.assertEqual(self.store.get_draft(draft.id), draft)

    def test_recovery_adopts_durable_copy_before_removing_previous_current_draft(self):
        draft = self.store.save_draft(uuid4().hex, "Recover this 🌍", "Recovered.txt")
        editor = self.make_editor()
        editor.flush_recovery()
        old_identifier = editor.current_draft_id
        editor.ui.messagebox.askyesnocancel.return_value = False
        original_delete = self.store.delete_draft

        def checked_delete(identifier):
            self.assertEqual(editor.current_draft_id, draft.id)
            self.assertEqual(identifier, old_identifier)
            self.assertEqual(self.draft_text(editor), draft.text)
            self.assertEqual(editor.content(), draft.text)
            original_delete(identifier)

        with patch.object(self.store, "delete_draft", side_effect=checked_delete):
            self.assertTrue(editor.recover_draft(draft))
        self.assertEqual(len(self.store.list_drafts()), 1)
        self.assertIsNone(editor.document)
        self.assertEqual(editor.saved_text, "")
        self.assertTrue(editor.dirty)

    def test_recovery_at_draft_capacity_needs_no_extra_slot(self):
        drafts = [self.store.save_draft(uuid4().hex, f"Recover {number}", f"Draft {number}.txt")
                  for number in range(MAX_DRAFTS)]
        editor = self.make_editor(text="")
        self.assertTrue(editor.recover_draft(drafts[0]))
        self.assertEqual(editor.current_draft_id, drafts[0].id)
        self.assertEqual(editor.content(), drafts[0].text)
        self.assertEqual(len(self.store.list_drafts()), MAX_DRAFTS)
        self.assertEqual(len([draft for draft in self.store.list_drafts()
                             if draft.id != editor.current_draft_id]), MAX_DRAFTS - 1)

    def test_recovery_at_capacity_retires_current_only_after_target_is_open(self):
        editor = self.make_editor()
        editor.flush_recovery()
        old_identifier = editor.current_draft_id
        drafts = [self.store.save_draft(uuid4().hex, f"Recover {number}", f"Draft {number}.txt")
                  for number in range(MAX_DRAFTS - 1)]
        editor.ui.messagebox.askyesnocancel.return_value = False
        self.assertTrue(editor.recover_draft(drafts[0]))
        identifiers = [draft.id for draft in self.store.list_drafts()]
        self.assertNotIn(old_identifier, identifiers)
        self.assertEqual(editor.current_draft_id, drafts[0].id)
        self.assertEqual(len(identifiers), MAX_DRAFTS - 1)

    def test_failed_recovery_snapshot_never_consumes_source_or_replaces_editor(self):
        draft = self.store.save_draft(uuid4().hex, "Recover this", "Recovered.txt")
        editor = self.make_editor()
        editor.flush_recovery()
        editor.ui.messagebox.askyesnocancel.return_value = False
        with patch.object(self.store, "save_draft", side_effect=HistoryError("Disk full")), \
                patch.object(self.store, "delete_draft") as delete:
            self.assertFalse(editor.recover_draft(draft))
        delete.assert_not_called()
        self.assertEqual(self.store.get_draft(draft.id), draft)
        self.assertEqual(editor.content(), "Unsaved draft")

    def test_recovery_display_error_retains_both_prior_snapshots(self):
        draft = self.store.save_draft(uuid4().hex, "Recover this", "Recovered.txt")
        editor = self.make_editor()
        editor.flush_recovery()
        old_identifier = editor.current_draft_id
        editor.ui.messagebox.askyesnocancel.return_value = False
        with patch.object(editor, "_show_unsaved", side_effect=RuntimeError("Tk unavailable")), \
                patch.object(self.store, "delete_draft") as delete:
            self.assertFalse(editor.recover_draft(draft))
        delete.assert_not_called()
        self.assertEqual(editor.current_draft_id, old_identifier)
        self.assertEqual(self.store.get_draft(draft.id).text, draft.text)
        self.assertEqual(self.store.get_draft(old_identifier).text, "Unsaved draft")
        self.assertEqual(editor.content(), "Unsaved draft")

    def test_recovery_cleanup_failure_keeps_previous_session_and_adopted_copy(self):
        draft = self.store.save_draft(uuid4().hex, "Recover this", "Recovered.txt")
        editor = self.make_editor()
        editor.flush_recovery()
        previous_id = editor.current_draft_id
        editor.ui.messagebox.askyesnocancel.return_value = False
        with patch.object(self.store, "delete_draft", side_effect=HistoryError("Locked")):
            self.assertTrue(editor.recover_draft(draft))
        self.assertEqual(self.store.get_draft(draft.id).text, draft.text)
        self.assertEqual(self.store.get_draft(previous_id).text, "Unsaved draft")
        self.assertEqual(self.draft_text(editor), draft.text)
        editor.recovery_status.set.assert_called_with("Previous recovery copy kept in History")

    def test_recovering_deleted_text_still_requires_save_as_or_explicit_discard(self):
        draft = self.store.save_draft(uuid4().hex, "", "Emptied.txt")
        editor = self.make_editor(text="")
        self.assertTrue(editor.recover_draft(draft))
        self.assertEqual(editor.content(), "")
        self.assertTrue(editor.dirty)
        self.assertIsNone(editor.document)
        self.assertFalse(editor.confirm_close())
        self.assertEqual(self.draft_text(editor), "")

    def test_missing_store_keeps_editor_usable_and_recovery_status_clear(self):
        editor = self.make_editor()
        editor.ui.history_store = None
        self.assertFalse(editor.flush_recovery())
        editor.recovery_status.set.assert_called_with("Recovery unavailable · Save a copy")
        self.assertEqual(editor.content(), "Unsaved draft")
        self.assertEqual(self.store.list_drafts(), [])

    def test_crash_snapshot_survives_reopen_and_restores_without_source_path(self):
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output, prefix="recovery-restart-") as folder:
            first_store = HistoryStore(Path(folder))
            editor = self.make_editor(text="Recovered after restart 🌍", store=first_store)
            editor.flush_recovery()
            editor.dispose_recovery()  # Abrupt teardown does not approve discard.
            first_store.close()
            second_store = HistoryStore(Path(folder))
            try:
                recovered = second_store.get_draft(second_store.list_drafts()[0].id)
                restarted = self.make_editor(text="", store=second_store)
                self.assertTrue(restarted.recover_draft(recovered))
                self.assertEqual(restarted.content(), "Recovered after restart 🌍")
                self.assertIsNone(restarted.document)
                self.assertEqual(len(second_store.list_drafts()), 1)
            finally:
                second_store.close()


@unittest.skipUnless(os.environ.get("VIDRICK_TEST_TK") == "1", "Desktop Tk checks are opt-in")
class EditorRecoveryTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        from vidrick_mac.preview import PreviewUI
        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.messages = MagicMock()
        self.messages.askyesnocancel.return_value = None
        self.ui = PreviewUI(self.root, windows_preview=sys.platform == "win32", tk=tk, ttk=ttk,
                            filedialog=MagicMock(), messagebox=self.messages)
        self.editor = self.ui.editor_panel
        self.store = self.ui.history_store

    def tearDown(self):
        self.editor.dispose_recovery()
        self.editor.cancel_refresh()
        self.ui.pipeline.dispose()
        self.root.destroy()
        self.store.close()
        self.assertEqual(self.errors, [])

    def test_real_modified_events_autosave_and_restore_unicode_edits(self):
        self.editor.AUTOSAVE_DELAY_MS = 1
        self.editor.text.insert("1.0", "A recoverable edit 🌍")
        ready = self.tk.BooleanVar(value=False)
        self.root.after(30, lambda: ready.set(True))
        self.root.wait_variable(ready)
        self.assertEqual(self.store.get_draft(self.editor.current_draft_id).text,
                         "A recoverable edit 🌍")
        self.assertEqual(self.editor.recovery_status.get(), "Saved locally")
        draft = self.store.save_draft(uuid4().hex, "Previous session 🌍", "Recovered")
        self.messages.askyesnocancel.return_value = False
        self.assertTrue(self.editor.recover_draft(draft))
        self.assertEqual(self.editor.content(), "Previous session 🌍")
        self.assertTrue(self.editor.dirty)
        self.assertIsNone(self.editor.document)
        self.assertEqual(self.editor.suggested_name, "Recovered.txt")
        self.assertFalse(self.editor.text.edit("canundo"))


if __name__ == "__main__":
    unittest.main()

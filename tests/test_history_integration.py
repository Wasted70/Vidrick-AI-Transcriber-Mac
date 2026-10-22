"""Opt-in desktop restart workflows using synthetic private libraries only."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from vidrick_mac.history_store import HistoryStore
from vidrick_mac.preview import PreviewUI


@unittest.skipUnless(os.environ.get("VIDRICK_TEST_TK") == "1", "Desktop Tk checks are opt-in")
class HistoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=output, prefix="history-integration-")
        self.folder = Path(self.temporary.name)
        self.library = self.folder / "library"
        self.windows, self.errors = [], []

    def make_ui(self):
        root = self.tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda *error: self.errors.append(error)
        self.windows.append(root)
        dialogs, messages = MagicMock(), MagicMock()
        dialogs.asksaveasfilename.return_value = ""
        dialogs.askopenfilename.return_value = ""
        messages.askyesnocancel.return_value = None
        messages.askyesno.return_value = False
        ui = PreviewUI(root, windows_preview=True, tk=self.tk, ttk=self.ttk,
                       filedialog=dialogs, messagebox=messages, history_root=self.library)
        root.update_idletasks()
        return ui

    def tearDown(self):
        for root in self.windows:
            try:
                root.destroy()
            except self.tk.TclError:
                pass
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def test_unexpected_window_close_preserves_draft_and_next_launch_offers_restore(self):
        first = self.make_ui()
        first.editor_panel.text.insert("1.0", "Synthetic restart draft 🌍")
        self.assertTrue(first.editor_panel.flush_recovery())
        old_id = first.editor_panel.current_draft_id
        # Bypass the normal close prompt to simulate a lost UI session. The
        # storage layer separately tests a real process exit during a write.
        first.root.destroy()
        second = self.make_ui()
        self.assertEqual(second.current_tab, "History")
        self.assertIn(old_id, second.history_panel.drafts)
        second.messagebox.askyesno.return_value = True
        self.assertTrue(second.history_panel.restore_selected())
        self.assertEqual(second.editor_panel.content(), "Synthetic restart draft 🌍")
        self.assertTrue(second.editor_panel.dirty)
        self.assertIsNone(second.editor_panel.document)
        self.assertEqual(second.current_tab, "Editor")
        drafts = second.history_store.list_drafts()
        self.assertEqual([draft.id for draft in drafts], [second.editor_panel.current_draft_id])

    def test_saved_result_survives_restart_and_opens_as_independent_editable_copy(self):
        first = self.make_ui()
        entry = first.history_store.add_result("Original result", "Interview.txt", model="base.bin")
        first.close_preview()
        second = self.make_ui()
        second.select_tab("History")
        self.assertTrue(second.history_panel.open_selected())
        second.editor_panel.text.insert("end", " with edits")
        second.editor_panel.flush_recovery()
        self.assertEqual(second.history_store.get_entry(entry.id).text, "Original result")
        self.assertEqual(second.editor_panel.content(), "Original result with edits")

    def test_storage_retry_immediately_snapshots_edits_made_while_library_locked(self):
        held = HistoryStore(self.library)
        try:
            ui = self.make_ui()
            self.assertIsNone(ui.history_store)
            ui.editor_panel.text.insert("1.0", "Typed while library locked")
            self.assertFalse(ui.editor_panel.flush_recovery())
        finally:
            held.close()
        ui.history_panel.retry_refresh()
        self.assertIsNotNone(ui.history_store)
        draft = ui.history_store.get_draft(ui.editor_panel.current_draft_id)
        self.assertEqual(draft.text, "Typed while library locked")
        self.assertEqual(ui.editor_panel.recovery_status.get(), "Saved locally")

    def test_cancelled_close_keeps_snapshot_and_approved_discard_removes_only_current_draft(self):
        ui = self.make_ui()
        previous_id = uuid4().hex
        ui.history_store.save_draft(previous_id, "Another recoverable copy", "Older.txt")
        ui.editor_panel.text.insert("1.0", "Current edits")
        current_id = ui.editor_panel.current_draft_id
        ui.close_preview()
        self.assertEqual(ui.history_store.get_draft(current_id).text, "Current edits")
        ui.messagebox.askyesnocancel.return_value = False
        ui.close_preview()
        reopened = HistoryStore(self.library)
        try:
            self.assertEqual([draft.id for draft in reopened.list_drafts()], [previous_id])
        finally:
            reopened.close()

    def test_first_launch_without_drafts_stays_on_transcribe(self):
        ui = self.make_ui()
        self.assertEqual(ui.current_tab, "Transcribe")
        self.assertEqual(ui.history_store.list_entries(), [])
        self.assertEqual(ui.history_store.list_drafts(), [])


if __name__ == "__main__":
    unittest.main()

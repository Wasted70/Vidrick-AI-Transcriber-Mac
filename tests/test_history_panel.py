"""Opt-in real Tk History tests, using only a synthetic project-local library."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from vidrick_mac.history_store import HistoryError, HistoryStore
from vidrick_mac.preview import PreviewUI


@unittest.skipUnless(os.environ.get("VIDRICK_TEST_TK") == "1", "Desktop Tk checks are opt-in")
class HistoryPanelTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=output, prefix="history-tk-")
        self.folder = Path(self.temporary.name)
        self.store = HistoryStore(self.folder / "library")
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.dialogs, self.messages = MagicMock(), MagicMock()
        self.dialogs.askopenfilename.return_value = ""
        self.dialogs.asksaveasfilename.return_value = ""
        self.messages.askyesnocancel.return_value = None
        self.messages.askyesno.return_value = False
        self.ui = PreviewUI(self.root, windows_preview=True, tk=tk, ttk=ttk,
                            filedialog=self.dialogs, messagebox=self.messages,
                            history_store=self.store)
        self.panel, self.editor = self.ui.history_panel, self.ui.editor_panel
        self.root.attributes("-alpha", 0)
        self.root.geometry("940x640")
        self.root.deiconify()
        self.ui.select_tab("History")
        self.root.update()

    def tearDown(self):
        try:
            self.ui.dispose()
            self.root.destroy()
        except self.tk.TclError:
            pass
        self.store.close()
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def add_entry(self, name="Synthetic recording.txt", text="Original synthetic text", model="Base"):
        entry = self.store.add_result(text, name, model=model, language="English")
        self.panel.refresh()
        self.panel.tree.selection_set(entry.id)
        self.panel._selection_changed()
        return entry

    def add_draft(self, text="Recovered synthetic edits", title="Unsaved notes.txt", identifier=None):
        draft = self.store.save_draft(identifier or uuid4().hex, text, title)
        self.panel.refresh()
        return draft

    def test_empty_history_has_useful_state_and_disabled_actions(self):
        self.assertIn("automatically", self.panel.status.get())
        self.assertEqual(str(self.panel.open_button.cget("state")), "disabled")
        self.assertFalse(self.panel.has_recovery)
        self.assertFalse(self.panel.recovery_frame.winfo_ismapped())

    def test_search_filters_by_title_and_model_and_preserves_selection(self):
        first = self.add_entry("First meeting.txt", model="Tiny")
        second = self.add_entry("Planning.txt", model="Small")
        self.panel.refresh()
        self.assertEqual(self.panel.tree.selection(), (second.id,))
        self.panel.query.set("FIRST")
        self.panel.refresh()
        self.assertEqual(self.panel.tree.get_children(), (first.id,))
        self.panel.query.set("small")
        self.panel.refresh()
        self.assertEqual(self.panel.tree.get_children(), (second.id,))
        self.panel.query.set("no such result")
        self.panel.refresh()
        self.assertFalse(self.panel.tree.get_children())
        self.assertIn("No matching", self.panel.status.get())
        self.assertEqual(str(self.panel.open_button.cget("state")), "disabled")

    def test_open_reads_full_text_and_edits_do_not_change_saved_entry(self):
        entry = self.add_entry(text="Unicode café 🌎\nsecond line")
        self.assertEqual(self.panel.entries[entry.id].text, "")
        self.assertTrue(self.panel.open_selected())
        self.assertEqual(self.ui.current_tab, "Editor")
        self.assertEqual(self.editor.content(), entry.text)
        self.assertTrue(self.editor.dirty)
        self.assertIsNone(self.editor.document)
        self.editor.text.insert("end", " edited")
        self.editor.flush_recovery()
        self.assertEqual(self.store.get_entry(entry.id).text, entry.text)

    def test_open_preserves_subtitle_timing_in_editor_handoff(self):
        timings = ((25, 1050), (1600, 2300))
        entry = self.store.add_result("First cue\nSecond cue\n", "Timed transcript.txt", timings=timings)
        self.panel.refresh()
        self.panel.tree.selection_set(entry.id)
        with patch.object(self.editor, "load_transcription", wraps=self.editor.load_transcription) as load:
            self.assertTrue(self.panel.open_selected())
        load.assert_called_once_with(entry.text, entry.suggested_name, timings=timings)
        self.assertEqual(self.editor.content(), entry.text)
        self.assertEqual(self.store.get_entry(entry.id).timings, timings)

    def test_open_respects_unsaved_editor_cancel(self):
        entry = self.add_entry()
        self.editor.text.insert("1.0", "Current unsaved draft")
        self.assertFalse(self.panel.open_selected())
        self.assertEqual(self.editor.content(), "Current unsaved draft")
        self.assertEqual(self.store.get_entry(entry.id).text, entry.text)

    def test_missing_entry_reports_error_without_replacing_editor(self):
        entry = self.add_entry()
        self.store.delete_entry(entry.id)
        self.assertFalse(self.panel.open_selected())
        self.assertEqual(self.editor.content(), "")
        self.assertIn("Could not open", self.panel.status.get())

    def test_rename_keeps_text_and_rejects_blank_name(self):
        entry = self.add_entry()
        with patch("tkinter.simpledialog.askstring", return_value="Updated title"):
            self.assertTrue(self.panel.rename_selected())
        updated = self.store.get_entry(entry.id)
        self.assertEqual(updated.title, "Updated title")
        self.assertEqual(updated.text, entry.text)
        with patch("tkinter.simpledialog.askstring", return_value=" "):
            self.assertFalse(self.panel.rename_selected())
        self.assertEqual(self.store.get_entry(entry.id).title, "Updated title")
        self.assertIn("Could not rename", self.panel.status.get())

    def test_delete_confirmation_preserves_recording_and_editor(self):
        recording = self.folder / "synthetic-recording.wav"
        recording.write_bytes(b"synthetic audio fixture")
        entry = self.add_entry()
        self.editor.text.insert("1.0", "Current edits")
        self.assertFalse(self.panel.delete_selected())
        self.assertEqual(self.store.get_entry(entry.id).text, entry.text)
        self.messages.askyesno.return_value = True
        self.assertTrue(self.panel.delete_selected())
        self.assertFalse(self.store.list_entries())
        self.assertEqual(recording.read_bytes(), b"synthetic audio fixture")
        self.assertEqual(self.editor.content(), "Current edits")

    def test_modal_blocks_reentrant_actions_and_clears_flag(self):
        entry = self.add_entry()

        def nested_dialog(*args, **kwargs):
            self.assertTrue(self.ui._file_dialog_open)
            self.assertFalse(self.panel.open_selected())
            self.assertFalse(self.panel.rename_selected())
            self.assertFalse(self.panel.delete_selected())
            self.panel.refresh()
            return False

        self.messages.askyesno.side_effect = nested_dialog
        self.assertFalse(self.panel.delete_selected())
        self.assertFalse(self.ui._file_dialog_open)
        self.assertFalse(self.panel.busy)
        self.assertEqual(self.store.get_entry(entry.id).text, entry.text)

    def test_modal_store_change_cannot_delete_from_old_library(self):
        entry = self.add_entry()

        def replace_store(*args, **kwargs):
            self.ui.history_store = None
            self.ui.history_error = "Library unavailable"
            return True

        self.messages.askyesno.side_effect = replace_store
        self.assertFalse(self.panel.delete_selected())
        self.assertEqual(self.store.get_entry(entry.id).text, entry.text)
        self.assertIn("unavailable", self.panel.status.get())
        self.ui.history_store = self.store

    def test_current_draft_is_excluded_from_recovery_list(self):
        previous = self.add_draft()
        self.add_draft(identifier=self.editor.current_draft_id, title="Current.txt")
        self.assertEqual(self.panel.recovery_tree.get_children(), (previous.id,))
        self.assertTrue(self.panel.has_recovery)

    def test_restore_uses_full_draft_and_transfers_safe_current_copy(self):
        draft = self.add_draft()
        self.assertEqual(self.panel.drafts[draft.id].text, "")
        self.messages.askyesno.return_value = True
        self.assertTrue(self.panel.restore_selected())
        self.assertEqual(self.editor.content(), draft.text)
        self.assertTrue(self.editor.dirty)
        self.assertIsNone(self.editor.document)
        self.assertEqual(self.store.get_draft(self.editor.current_draft_id).text, draft.text)
        self.assertEqual(self.editor.current_draft_id, draft.id)
        self.assertFalse(self.panel.has_recovery)

    def test_restore_cancel_and_editor_conflict_preserve_recovery(self):
        draft = self.add_draft()
        self.assertFalse(self.panel.restore_selected())
        self.editor.text.insert("1.0", "Current work")
        self.messages.askyesno.return_value = True
        self.assertFalse(self.panel.restore_selected())
        self.assertEqual(self.editor.content(), "Current work")
        self.assertEqual(self.store.get_draft(draft.id).text, draft.text)

    def test_discard_requires_confirmation_and_only_removes_target(self):
        entry = self.add_entry()
        draft = self.add_draft()
        self.editor.text.insert("1.0", "Current work")
        self.assertFalse(self.panel.discard_selected())
        self.assertEqual(self.store.get_draft(draft.id).text, draft.text)
        self.messages.askyesno.return_value = True
        self.assertTrue(self.panel.discard_selected())
        with self.assertRaises(HistoryError):
            self.store.get_draft(draft.id)
        self.assertEqual(self.store.get_entry(entry.id).text, entry.text)
        self.assertEqual(self.editor.content(), "Current work")

    def test_storage_failures_leave_recovery_and_show_actionable_state(self):
        draft = self.add_draft()
        self.messages.askyesno.return_value = True
        with patch.object(self.store, "delete_draft", side_effect=HistoryError("Disk is read-only")):
            self.assertFalse(self.panel.discard_selected())
        self.assertEqual(self.store.get_draft(draft.id).text, draft.text)
        self.assertIn("read-only", self.panel.status.get())
        with patch.object(self.store, "list_entries", side_effect=HistoryError("Cannot read library")):
            self.panel.refresh()
        self.assertIn("Cannot read library", self.panel.status.get())

    def test_refresh_retry_is_explicit_when_library_is_unavailable(self):
        self.ui.history_store = None
        self.ui.history_error = "Library locked by another window"
        with patch.object(self.ui, "ensure_history_store", return_value=False) as retry:
            self.panel.refresh()
            retry.assert_not_called()
            self.assertIn("locked", self.panel.status.get())
            self.panel.retry_refresh()
            retry.assert_called_once()
        self.ui.history_store = self.store

    def test_keyboard_open_and_dispose_cancel_pending_search(self):
        entry = self.add_entry()
        self.panel.tree.focus_force()
        self.root.update()
        self.panel.tree.event_generate("<Return>")
        self.root.update()
        self.assertEqual(self.editor.content(), entry.text)
        self.panel.query.set("pending search")
        self.assertIsNotNone(self.panel.refresh_id)
        self.panel.dispose()
        self.assertIsNone(self.panel.refresh_id)
        self.panel.query.set("ignored after disposal")
        self.assertIsNone(self.panel.refresh_id)

    def test_compact_window_keeps_history_and_recovery_controls_visible(self):
        for index in range(4):
            self.add_entry(name=f"Transcript {index}.txt")
        for index in range(3):
            self.add_draft(title=f"Draft {index}.txt")
        self.root.update()
        for widget in (self.panel.tree, self.panel.open_button, self.panel.search_entry,
                       self.panel.recovery_tree, self.panel.recovery_scroll,
                       self.panel.restore_button, self.panel.discard_button):
            self.assertTrue(widget.winfo_ismapped())
            self.assertGreater(widget.winfo_width(), 10)
            self.assertGreater(widget.winfo_height(), 10)
            self.assertGreaterEqual(widget.winfo_rootx(), self.root.winfo_rootx())
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                 self.root.winfo_rootx() + self.root.winfo_width())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                 self.root.winfo_rooty() + self.root.winfo_height())
        row_height = int(self.ui.ttk.Style(self.root).lookup("Preview.Treeview", "rowheight"))
        for tree, minimum_rows in ((self.panel.tree, 2), (self.panel.recovery_tree, 1)):
            header_height = tree.winfo_reqheight() - int(tree.cget("height")) * row_height
            self.assertGreaterEqual(tree.winfo_height(), header_height + minimum_rows * row_height)
            tree.yview_moveto(0)
            self.root.update()
            for identifier in tree.get_children()[:minimum_rows]:
                bounds = tree.bbox(identifier)
                self.assertTrue(bounds, "A required transcript row is not visible")
                self.assertLessEqual(bounds[1] + bounds[3], tree.winfo_height())

    def test_recovery_list_scrolls_and_displays_dates_for_older_drafts(self):
        for index in range(5):
            self.add_draft(title=f"Draft {index}.txt")
        self.root.update()
        self.assertEqual(len(self.panel.recovery_tree.get_children()), 5)
        self.assertIn("headings", str(self.panel.recovery_tree.cget("show")))
        self.assertTrue(self.panel.recovery_scroll.winfo_ismapped())
        self.panel.recovery_tree.yview_moveto(1)
        self.root.update()
        self.assertGreater(self.panel.recovery_tree.yview()[0], 0)


if __name__ == "__main__":
    unittest.main()

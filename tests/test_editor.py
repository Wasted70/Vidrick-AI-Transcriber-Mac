"""Headless tests for document lifecycle decisions, without native dialogs or files."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from vidrick_mac.editor import TranscriptEditor
from vidrick_mac.transcript_io import LoadedTranscript, TranscriptConflict, TranscriptError


class EditorLifecycleTests(unittest.TestCase):
    def make_editor(self, *, content="Unsaved edit", saved="Original", untitled=False):
        editor = TranscriptEditor.__new__(TranscriptEditor)
        editor.root = MagicMock()
        editor.tk = SimpleNamespace(TclError=RuntimeError)
        editor.ui = SimpleNamespace(
            _file_dialog_open=False, filedialog=MagicMock(), messagebox=MagicMock(),
            select_tab=MagicMock(),
        )
        self.path = Path(__file__).resolve().parents[1] / "test-output" / "synthetic.txt"
        editor.document = None if untitled else LoadedTranscript(self.path, saved, "utf-8", "\n", "original-fingerprint")
        editor.saved_text = saved
        editor.busy = False
        editor.active_match = ("1.0", "1.4")
        editor.refresh = MagicMock()
        editor.text = MagicMock()
        editor.text.value = content
        editor.text.get.side_effect = lambda *_: editor.text.value
        editor.text.delete.side_effect = lambda *_: setattr(editor.text, "value", "")
        editor.text.insert.side_effect = lambda _, value: setattr(editor.text, "value", value)
        return editor

    def assert_preserved(self, editor, before):
        self.assertEqual((editor.document, editor.saved_text, editor.content()), before)
        self.assertFalse(editor.busy)

    @staticmethod
    def snapshot(editor):
        return editor.document, editor.saved_text, editor.content()

    def test_dirty_uses_content_and_undoing_to_saved_text_clears_it(self):
        editor = self.make_editor(content="Original")
        self.assertFalse(editor.dirty)
        editor.text.value = "New edit"
        self.assertTrue(editor.dirty)

        def undo_text(operation):
            if operation == "undo":
                editor.text.value = editor.saved_text

        editor.text.edit.side_effect = undo_text
        self.assertEqual(editor.undo(), "break")
        self.assertFalse(editor.dirty)
        self.assertEqual(editor.document.text, "Original")

    def test_cancelled_open_keeps_current_document_without_save_prompt(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.filedialog.askopenfilename.return_value = ""
        with patch("vidrick_mac.editor.read_transcript") as read:
            self.assertFalse(editor.open())
        read.assert_not_called()
        editor.ui.messagebox.askyesnocancel.assert_not_called()
        self.assert_preserved(editor, before)

    def test_failed_open_preserves_edits_even_after_user_chooses_discard(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.filedialog.askopenfilename.return_value = str(self.path)
        editor.ui.messagebox.askyesnocancel.return_value = False
        with patch("vidrick_mac.editor.read_transcript", side_effect=TranscriptError("Invalid text")):
            self.assertFalse(editor.open())
        editor.ui.messagebox.showerror.assert_called_once()
        self.assert_preserved(editor, before)
        self.assertTrue(editor.dirty)

    def test_cancelled_unsaved_prompt_does_not_read_or_replace_document(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.filedialog.askopenfilename.return_value = str(self.path)
        editor.ui.messagebox.askyesnocancel.return_value = None
        with patch("vidrick_mac.editor.read_transcript") as read:
            self.assertFalse(editor.open())
        read.assert_not_called()
        self.assert_preserved(editor, before)

    def test_successful_open_sets_clean_baseline_and_resets_undo_history(self):
        editor = self.make_editor(content="Original")
        new_document = LoadedTranscript(self.path, "New transcript\n", "utf-8-sig", "\r\n", "new-fingerprint")
        editor.ui.filedialog.askopenfilename.return_value = str(self.path)
        with patch("vidrick_mac.editor.read_transcript", return_value=new_document):
            self.assertTrue(editor.open())
        self.assertIs(editor.document, new_document)
        self.assertEqual(editor.content(), new_document.text)
        self.assertEqual(editor.saved_text, new_document.text)
        self.assertFalse(editor.dirty)
        self.assertIsNone(editor.active_match)
        self.assertFalse(editor.busy)
        editor.text.edit_reset.assert_called_once()
        editor.ui.select_tab.assert_called_once_with("Editor")

    def test_cancelled_save_as_keeps_document_identity_and_unsaved_edits(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.filedialog.asksaveasfilename.return_value = ""
        with patch("vidrick_mac.editor.write_transcript") as write:
            self.assertFalse(editor.save_as())
        write.assert_not_called()
        self.assert_preserved(editor, before)

    def test_failed_save_keeps_original_baseline_and_unsaved_edits(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        with patch("vidrick_mac.editor.write_transcript", side_effect=TranscriptError("Disk full")):
            self.assertFalse(editor.save())
        editor.ui.messagebox.showerror.assert_called_once()
        self.assert_preserved(editor, before)
        self.assertTrue(editor.dirty)

    def test_save_before_close_cancelled_destination_keeps_window_open(self):
        editor = self.make_editor(saved="", untitled=True)
        before = self.snapshot(editor)
        editor.ui.messagebox.askyesnocancel.return_value = True
        editor.ui.filedialog.asksaveasfilename.return_value = ""
        with patch("vidrick_mac.editor.write_transcript") as write:
            self.assertFalse(editor.confirm_close())
        write.assert_not_called()
        self.assert_preserved(editor, before)

    def test_save_before_close_failure_keeps_window_open(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.messagebox.askyesnocancel.return_value = True
        with patch("vidrick_mac.editor.write_transcript", side_effect=TranscriptError("Permission denied")):
            self.assertFalse(editor.confirm_close())
        editor.ui.messagebox.showerror.assert_called_once()
        self.assert_preserved(editor, before)

    def test_save_before_open_failure_never_reads_another_document(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.filedialog.askopenfilename.return_value = str(self.path)
        editor.ui.messagebox.askyesnocancel.return_value = True
        with patch("vidrick_mac.editor.write_transcript", side_effect=TranscriptError("Disk full")), \
                patch("vidrick_mac.editor.read_transcript") as read:
            self.assertFalse(editor.open())
        read.assert_not_called()
        self.assert_preserved(editor, before)

    def test_close_discard_and_cancel_decisions_do_not_save_or_mutate_text(self):
        for decision, closes in ((False, True), (None, False)):
            with self.subTest(decision=decision):
                editor = self.make_editor()
                before = self.snapshot(editor)
                editor.ui.messagebox.askyesnocancel.return_value = decision
                with patch("vidrick_mac.editor.write_transcript") as write:
                    self.assertEqual(editor.confirm_close(), closes)
                write.assert_not_called()
                editor.ui.filedialog.asksaveasfilename.assert_not_called()
                self.assert_preserved(editor, before)

    def test_clean_close_needs_no_confirmation(self):
        editor = self.make_editor(content="Original")
        self.assertTrue(editor.confirm_close())
        editor.ui.messagebox.askyesnocancel.assert_not_called()
        self.assertFalse(editor.busy)

    def test_successful_save_before_close_updates_baseline_and_permits_close(self):
        editor = self.make_editor()
        editor.ui.messagebox.askyesnocancel.return_value = True
        saved = LoadedTranscript(self.path, editor.content(), "utf-8", "\n", "saved-fingerprint")
        with patch("vidrick_mac.editor.write_transcript", return_value=saved):
            self.assertTrue(editor.confirm_close())
        self.assertIs(editor.document, saved)
        self.assertEqual(editor.saved_text, saved.text)
        self.assertFalse(editor.dirty)
        self.assertFalse(editor.busy)

    def test_conflict_decline_keeps_unsaved_edits_and_does_not_retry_write(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.messagebox.askyesno.return_value = False
        with patch("vidrick_mac.editor.write_transcript", side_effect=TranscriptConflict("Changed on disk")) as write:
            self.assertFalse(editor.save())
        self.assertEqual(write.call_count, 1)
        editor.ui.messagebox.askyesno.assert_called_once()
        editor.ui.messagebox.showerror.assert_not_called()
        self.assert_preserved(editor, before)

    def test_conflict_accept_retries_with_explicit_overwrite_and_becomes_clean(self):
        editor = self.make_editor()
        previous = editor.document
        saved = LoadedTranscript(self.path, editor.content(), "utf-8", "\n", "saved-fingerprint")
        editor.ui.messagebox.askyesno.return_value = True
        with patch("vidrick_mac.editor.write_transcript", side_effect=[TranscriptConflict("Changed on disk"), saved]) as write:
            self.assertTrue(editor.save())
        self.assertEqual(write.call_count, 2)
        write.assert_called_with(self.path, saved.text, previous=previous, overwrite_changed=True)
        self.assertIs(editor.document, saved)
        self.assertFalse(editor.dirty)
        self.assertFalse(editor.busy)

    def test_conflict_overwrite_failure_still_preserves_editor(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.ui.messagebox.askyesno.return_value = True
        with patch("vidrick_mac.editor.write_transcript", side_effect=[TranscriptConflict("Changed on disk"),
                                                                       TranscriptError("Write denied")]):
            self.assertFalse(editor.save())
        editor.ui.messagebox.showerror.assert_called_once()
        self.assert_preserved(editor, before)

    def test_recording_picker_or_editor_dialog_blocks_other_document_actions(self):
        for guard in ("recording", "editor"):
            with self.subTest(guard=guard):
                editor = self.make_editor()
                before = self.snapshot(editor)
                editor.ui._file_dialog_open = guard == "recording"
                editor.busy = guard == "editor"
                with patch("vidrick_mac.editor.read_transcript") as read, \
                        patch("vidrick_mac.editor.write_transcript") as write:
                    self.assertFalse(editor.open())
                    self.assertFalse(editor.save())
                    self.assertFalse(editor.save_as())
                    self.assertFalse(editor.confirm_close())
                read.assert_not_called()
                write.assert_not_called()
                self.assertEqual(editor.ui.filedialog.mock_calls, [])
                self.assertEqual(editor.ui.messagebox.mock_calls, [])
                self.assertEqual(self.snapshot(editor), before)
                self.assertEqual(editor.busy, guard == "editor")

    def test_open_dialog_reentry_is_blocked_and_guard_released_after_cancel(self):
        editor = self.make_editor()
        before = self.snapshot(editor)

        def nested_shortcuts(**_):
            self.assertTrue(editor.busy)
            self.assertFalse(editor.open())
            self.assertFalse(editor.save())
            self.assertFalse(editor.confirm_close())
            return ""

        editor.ui.filedialog.askopenfilename.side_effect = nested_shortcuts
        self.assertFalse(editor.open())
        editor.ui.filedialog.askopenfilename.assert_called_once()
        editor.ui.filedialog.asksaveasfilename.assert_not_called()
        self.assert_preserved(editor, before)


if __name__ == "__main__":
    unittest.main()

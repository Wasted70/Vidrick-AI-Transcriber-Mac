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
        editor.suggested_name = None
        editor.busy = False
        editor.refresh_id = None
        editor.active_match = ("1.0", "1.4")
        editor.find_panel = MagicMock()
        editor.find_text = MagicMock()
        editor.replace_text = MagicMock()
        editor.match_case = MagicMock()
        editor.match_label = MagicMock()
        editor.search_visible = True
        editor.matches = [editor.active_match]
        editor.matches_limited = True
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
        editor.suggested_name = "Previous recording.txt"
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
        self.assertIsNone(editor.suggested_name)
        editor.text.edit_reset.assert_called_once()
        editor.ui.select_tab.assert_called_once_with("Editor")

    def test_finished_transcript_is_unsaved_normalized_and_clears_previous_search(self):
        editor = self.make_editor(content="Original")
        self.assertTrue(editor.load_transcription("Hello 🌍\r\nAnother line\rEnd", "Interview.txt"))
        self.assertEqual(editor.content(), "Hello 🌍\nAnother line\nEnd")
        self.assertIsNone(editor.document)
        self.assertEqual(editor.saved_text, "")
        self.assertEqual(editor.suggested_name, "Interview.txt")
        self.assertTrue(editor.dirty)
        self.assertFalse(editor.busy)
        self.assertFalse(editor.search_visible)
        self.assertEqual(editor.matches, [])
        self.assertFalse(editor.matches_limited)
        self.assertIsNone(editor.active_match)
        editor.find_text.set.assert_called_once_with("")
        editor.replace_text.set.assert_called_once_with("")
        editor.match_case.set.assert_called_once_with(False)
        editor.find_panel.grid_remove.assert_called_once()
        editor.text.edit_reset.assert_called_once()
        editor.ui.select_tab.assert_called_once_with("Editor")
        editor.ui.messagebox.askyesnocancel.assert_not_called()

    def test_cancelled_finished_handoff_preserves_current_document_and_search(self):
        editor = self.make_editor()
        before = self.snapshot(editor)
        editor.suggested_name = "Keep this name.txt"
        editor.ui.messagebox.askyesnocancel.return_value = None
        self.assertFalse(editor.load_transcription("Finished result", "Recording.txt"))
        self.assert_preserved(editor, before)
        self.assertEqual(editor.suggested_name, "Keep this name.txt")
        self.assertTrue(editor.search_visible)
        editor.find_text.set.assert_not_called()
        editor.text.edit_reset.assert_not_called()
        editor.ui.select_tab.assert_not_called()

    def test_finished_handoff_discard_replaces_only_after_explicit_choice(self):
        editor = self.make_editor()
        editor.ui.messagebox.askyesnocancel.return_value = False
        self.assertTrue(editor.load_transcription("Finished result"))
        self.assertEqual(editor.content(), "Finished result")
        self.assertTrue(editor.dirty)
        self.assertIn("opening the finished transcript", editor.ui.messagebox.askyesnocancel.call_args.args[1])

    def test_finished_handoff_failed_or_cancelled_save_preserves_work(self):
        for failure in ("cancel", "error"):
            with self.subTest(failure=failure):
                editor = self.make_editor(saved="", untitled=True)
                before = self.snapshot(editor)
                editor.ui.messagebox.askyesnocancel.return_value = True
                editor.ui.filedialog.asksaveasfilename.return_value = "" if failure == "cancel" else str(self.path)
                with patch("vidrick_mac.editor.write_transcript", side_effect=TranscriptError("Disk full")) as write:
                    self.assertFalse(editor.load_transcription("Finished result"))
                self.assertEqual(write.call_count, int(failure == "error"))
                self.assert_preserved(editor, before)
                editor.text.edit_reset.assert_not_called()

    def test_finished_handoff_saves_previous_document_before_new_unsaved_result(self):
        editor = self.make_editor()
        previous = editor.document
        old_text = editor.content()
        editor.ui.messagebox.askyesnocancel.return_value = True
        saved = LoadedTranscript(self.path, old_text, "utf-8", "\n", "saved-fingerprint")
        with patch("vidrick_mac.editor.write_transcript", return_value=saved) as write:
            self.assertTrue(editor.load_transcription("Finished result", "Recording.txt"))
        write.assert_called_once_with(self.path, old_text, previous=previous)
        self.assertIsNone(editor.document)
        self.assertEqual(editor.content(), "Finished result")
        self.assertEqual(editor.saved_text, "")
        self.assertTrue(editor.dirty)

    def test_finished_transcript_save_uses_suggested_name_and_becomes_clean(self):
        editor = self.make_editor(content="Original")
        self.assertTrue(editor.load_transcription("Finished result", "Recording.txt"))
        editor.ui.filedialog.asksaveasfilename.return_value = str(self.path)
        saved = LoadedTranscript(self.path, editor.content(), "utf-8", "\n", "new-fingerprint")
        with patch("vidrick_mac.editor.write_transcript", return_value=saved):
            self.assertTrue(editor.save())
        self.assertEqual(editor.ui.filedialog.asksaveasfilename.call_args.kwargs["initialfile"], "Recording.txt")
        self.assertFalse(editor.dirty)
        self.assertIs(editor.document, saved)

    def test_finished_handoff_blocks_reentrant_document_actions(self):
        editor = self.make_editor()

        def nested_actions(*_, **__):
            self.assertTrue(editor.busy)
            self.assertFalse(editor.load_transcription("Nested result"))
            self.assertFalse(editor.open())
            self.assertFalse(editor.save())
            self.assertFalse(editor.confirm_close())
            return False

        editor.ui.messagebox.askyesnocancel.side_effect = nested_actions
        self.assertTrue(editor.load_transcription("Outer result"))
        self.assertEqual(editor.content(), "Outer result")
        editor.ui.messagebox.askyesnocancel.assert_called_once()

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
                    self.assertFalse(editor.load_transcription("Finished result"))
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

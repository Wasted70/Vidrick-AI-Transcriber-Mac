"""Subtitle export lifecycle and real Editor checks using synthetic documents."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from vidrick_mac.editor import TranscriptEditor
from vidrick_mac.history_store import HistoryStore
from vidrick_mac.preview import PreviewUI
from vidrick_mac.subtitle_io import SubtitleError
from vidrick_mac.transcript_io import LoadedTranscript


TIMED = "[00:00:01.250 --> 00:00:03.000] Hello, world!\n"


class SubtitleEditorTests(unittest.TestCase):
    def setUp(self):
        self.output = Path(__file__).resolve().parents[1] / "test-output"
        self.output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.output, prefix="subtitle-editor-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.editor = TranscriptEditor.__new__(TranscriptEditor)
        self.editor.root = MagicMock()
        self.editor.ui = SimpleNamespace(_file_dialog_open=False, filedialog=MagicMock(), messagebox=MagicMock())
        self.editor.busy = False
        self.editor.text = MagicMock()
        self.editor.text.get.return_value = TIMED
        self.editor.saved_text = "Earlier text"
        self.editor.document = LoadedTranscript(self.folder / "Original.txt", "Earlier text", "utf-8", "\n", "old")
        self.editor.suggested_name = None
        self.editor.timings = ()
        self.editor.subtitle_status = MagicMock()
        self.editor.flush_recovery = MagicMock(return_value=True)

    def test_export_current_edits_does_not_mark_saved_or_replace_document(self):
        original = self.editor.document
        for format in ("srt", "vtt"):
            with self.subTest(format=format):
                path = self.folder / ("Edited." + format)
                self.editor.ui.filedialog.asksaveasfilename.return_value = str(path)
                self.assertTrue(self.editor.export_subtitles(format))
                output = path.read_text(encoding="utf-8")
                self.assertIn("Hello, world!", output)
                self.assertNotIn("Earlier text", output)
                self.assertIn("00:00:01,250" if format == "srt" else "00:00:01.250", output)
                self.assertIs(self.editor.document, original)
                self.assertEqual(self.editor.saved_text, "Earlier text")
                self.assertTrue(self.editor.dirty)
                self.assertFalse(self.editor.busy)
                self.editor.text.edit_reset.assert_not_called()
                self.assertEqual(self.editor.ui.filedialog.asksaveasfilename.call_args.kwargs["initialfile"], "Original." + format)

    def test_cancel_keeps_editor_and_does_not_write(self):
        self.editor.ui.filedialog.asksaveasfilename.return_value = ""
        with patch("vidrick_mac.editor.write_subtitles") as write:
            self.assertFalse(self.editor.export_subtitles("srt"))
        write.assert_not_called()
        self.assertTrue(self.editor.dirty)
        self.assertFalse(self.editor.busy)

    def test_failure_keeps_editor_and_draft(self):
        self.editor.ui.filedialog.asksaveasfilename.return_value = str(self.folder / "Failure.srt")
        with patch("vidrick_mac.editor.write_subtitles", side_effect=SubtitleError("Disk full")):
            self.assertFalse(self.editor.export_subtitles("srt"))
        self.editor.ui.messagebox.showerror.assert_called_once()
        self.assertTrue(self.editor.dirty)
        self.assertEqual(self.editor.content(), TIMED)
        self.assertFalse(self.editor.busy)

    def test_missing_timing_rejected_before_save_dialog(self):
        self.editor.text.get.return_value = "Plain text has no timing"
        self.assertFalse(self.editor.export_subtitles("vtt"))
        self.editor.ui.messagebox.showerror.assert_called_once()
        self.editor.ui.filedialog.asksaveasfilename.assert_not_called()

    def test_hidden_timings_use_current_line_text(self):
        self.editor.text.get.return_value = "Edited first line\nSecond line\n"
        self.editor.timings = ((100, 800), (950, 1200))
        path = self.folder / "Hidden.srt"
        self.editor.ui.filedialog.asksaveasfilename.return_value = str(path)
        self.assertTrue(self.editor.export_subtitles("srt"))
        self.assertIn("00:00:00,100 --> 00:00:00,800\nEdited first line", path.read_text(encoding="utf-8"))
        self.editor.text.get.return_value = "Merged lines"
        self.editor.ui.filedialog.asksaveasfilename.reset_mock()
        self.assertFalse(self.editor.export_subtitles("srt"))
        self.editor.ui.filedialog.asksaveasfilename.assert_not_called()

    def test_export_dialog_blocks_nested_document_actions(self):
        def nested(**kwargs):
            self.assertTrue(self.editor.busy)
            self.assertFalse(self.editor.export_subtitles("vtt"))
            self.assertFalse(self.editor.open())
            self.assertFalse(self.editor.save())
            self.assertFalse(self.editor.load_transcription("Other"))
            self.assertFalse(self.editor.confirm_close())
            return ""
        self.editor.ui.filedialog.asksaveasfilename.side_effect = nested
        self.assertFalse(self.editor.export_subtitles("srt"))
        self.assertFalse(self.editor.busy)

    def test_other_open_dialog_blocks_export(self):
        self.editor.ui._file_dialog_open = True
        self.assertFalse(self.editor.export_subtitles("srt"))
        self.editor.flush_recovery.assert_not_called()


@unittest.skipUnless(os.environ.get("VIDRICK_TEST_TK") == "1", "Desktop Tk checks are opt-in")
class SubtitleEditorTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=output, prefix="subtitle-tk-")
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.dialogs, self.messages = MagicMock(), MagicMock()
        self.messages.askyesnocancel.return_value = False
        self.store = HistoryStore()
        self.ui = PreviewUI(self.root, windows_preview=sys.platform == "win32", tk=tk,
                            ttk=ttk, filedialog=self.dialogs, messagebox=self.messages,
                            history_store=self.store)
        self.editor = self.ui.editor_panel
        self.root.attributes("-alpha", 0)
        self.root.geometry("940x640")
        self.root.deiconify()
        self.ui.select_tab("Editor")
        self.root.update()

    def tearDown(self):
        self.ui.dispose()
        self.root.destroy()
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def test_menu_exports_both_formats_and_fits_minimum_window(self):
        self.editor.load_transcription(TIMED, "Synthetic.txt", timings=((1250, 3000),))
        identifier = self.editor.current_draft_id
        for index, format in enumerate(("srt", "vtt")):
            path = self.folder / ("Sample." + format)
            self.dialogs.asksaveasfilename.return_value = str(path)
            self.editor.export_menu.invoke(index)
            self.assertTrue(path.is_file())
            self.assertTrue(self.editor.dirty)
            self.assertEqual(self.store.get_draft(identifier).text, TIMED)
        self.root.update()
        button = self.editor.export_button
        self.assertTrue(button.winfo_ismapped())
        self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
        self.assertGreater(self.editor.text.winfo_height(), 100)
        self.messages.showerror.assert_not_called()

    def test_recovery_retains_offsets_and_exports_edited_hidden_text(self):
        offsets = ((0, 1000), (1250, 3000))
        self.editor.load_transcription("First\nSecond\n", "Hidden.txt", timings=offsets)
        self.editor.text.delete("1.0", "1.end")
        self.editor.text.insert("1.0", "Edited first")
        self.editor.flush_recovery()
        draft = self.store.get_draft(self.editor.current_draft_id)
        self.assertEqual(draft.timings, offsets)
        self.assertTrue(self.editor.recover_draft(draft))
        self.assertEqual(self.editor.timings, offsets)
        path = self.folder / "Recovered.vtt"
        self.dialogs.asksaveasfilename.return_value = str(path)
        self.assertTrue(self.editor.export_subtitles("vtt"))
        self.assertIn("00:00:00.000 --> 00:00:01.000\nEdited first", path.read_text(encoding="utf-8"))

    def test_opening_unrelated_text_clears_hidden_offsets(self):
        self.editor.load_transcription("First\n", timings=((0, 1000),))
        source = self.folder / "Other.txt"
        source.write_text("Unrelated text", encoding="utf-8")
        self.dialogs.askopenfilename.return_value = str(source)
        self.assertTrue(self.editor.open())
        self.assertEqual(self.editor.timings, ())
        self.assertFalse(self.editor.export_subtitles("srt"))

    def test_cancelled_replacement_preserves_current_text_and_timings(self):
        offsets = ((0, 1000),)
        self.editor.load_transcription("Original line\n", timings=offsets)
        self.messages.askyesnocancel.return_value = None
        self.assertFalse(self.editor.load_transcription("Replacement\n", timings=((2000, 3000),)))
        self.assertEqual(self.editor.content(), "Original line\n")
        self.assertEqual(self.editor.timings, offsets)
        self.assertEqual(self.store.get_draft(self.editor.current_draft_id).timings, offsets)

    def test_failed_recovery_display_restores_previous_timings(self):
        from uuid import uuid4
        self.editor.load_transcription("Original line\n", timings=((0, 1000),))
        original_id = self.editor.current_draft_id
        draft = self.store.save_draft(uuid4().hex, "Replacement\n", "Recovered.txt", timings=((2000, 3000),))
        original_show = self.editor._show_unsaved

        def fail_after_display(*args, **kwargs):
            original_show(*args, **kwargs)
            raise self.ui.tk.TclError("Synthetic display failure")

        with patch.object(self.editor, "_show_unsaved", side_effect=fail_after_display):
            self.assertFalse(self.editor.recover_draft(draft))
        self.assertEqual(self.editor.content(), "Original line\n")
        self.assertEqual(self.editor.timings, ((0, 1000),))
        self.assertEqual(self.editor.current_draft_id, original_id)
        self.assertEqual(self.store.get_draft(original_id).timings, ((0, 1000),))
        self.assertEqual(self.store.get_draft(draft.id).timings, ((2000, 3000),))


if __name__ == "__main__":
    unittest.main()

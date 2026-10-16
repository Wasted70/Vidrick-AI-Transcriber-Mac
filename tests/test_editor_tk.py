"""Opt-in desktop tests: set VIDRICK_TEST_TK=1; use only synthetic transcripts."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

from vidrick_mac.preview import PreviewUI


@unittest.skipUnless(os.environ.get("VIDRICK_TEST_TK") == "1", "Desktop Tk checks are opt-in")
class EditorTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import ttk
        self.tk = tk
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=output, prefix="editor-tk-")
        self.folder = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.dialogs, self.messages = MagicMock(), MagicMock()
        self.dialogs.askopenfilename.return_value = ""
        self.dialogs.asksaveasfilename.return_value = ""
        self.dialogs.askopenfilenames.return_value = ()
        self.messages.askyesnocancel.return_value = None
        self.ui = PreviewUI(self.root, windows_preview=sys.platform == "win32", tk=tk,
                            ttk=ttk, filedialog=self.dialogs, messagebox=self.messages)
        self.editor = self.ui.editor_panel
        self.root.attributes("-alpha", 0)
        self.root.geometry("940x640")
        self.root.deiconify()
        self.ui.select_tab("Editor")
        self.root.update()

    def tearDown(self):
        try:
            self.editor.cancel_refresh()
            self.root.destroy()
        except self.tk.TclError:
            pass
        self.temporary.cleanup()
        self.assertEqual(self.errors, [])

    def reset_text(self, text):
        self.editor.text.delete("1.0", "end")
        self.editor.text.insert("1.0", text)
        self.editor.saved_text = text
        self.editor.document = None
        self.editor.suggested_name = None
        self.editor.text.edit_reset()
        self.editor.text.edit_modified(False)
        self.editor.refresh()
        self.root.update()

    def key(self, widget, sequence):
        widget.focus_force()
        self.root.update()
        widget.event_generate(sequence)
        self.root.update()

    def test_literal_unicode_case_search_does_not_change_document(self):
        self.reset_text("Alpha alpha .* 🌍 🌍\nalpha")
        self.editor.show_find()
        self.editor.find_text.set(".*")
        self.editor._refresh_matches()
        self.assertEqual(len(self.editor.matches), 1)
        self.editor.find_text.set("🌍")
        self.editor._refresh_matches()
        self.assertEqual(len(self.editor.matches), 2)
        self.assertTrue(all(self.editor.text.get(*match) == "🌍" for match in self.editor.matches))
        self.editor.replace_text.set("🌍!")
        self.editor.replace_all()
        self.assertEqual(self.editor.content(), "Alpha alpha .* 🌍! 🌍!\nalpha")
        self.editor.undo()
        self.editor.find_next()
        first = self.editor.active_match
        self.editor.find_previous()
        self.assertNotEqual(first, self.editor.active_match)
        self.editor.find_text.set("alpha")
        self.editor._refresh_matches()
        self.assertEqual(len(self.editor.matches), 3)
        self.editor.match_case.set(True)
        self.editor._refresh_matches()
        self.assertEqual(len(self.editor.matches), 2)
        self.assertFalse(self.editor.dirty)
        self.assertFalse(self.editor.text.edit("canundo"))

    def test_replace_all_is_literal_unicode_safe_and_one_undo_step(self):
        original = "🌍 one one\nONE"
        self.reset_text(original)
        self.editor.show_find()
        self.editor.find_text.set("one")
        self.editor.replace_text.set("one \\1 🌍")
        self.editor.replace_all()
        self.assertEqual(self.editor.content(), "🌍 one \\1 🌍 one \\1 🌍\none \\1 🌍")
        self.assertTrue(self.editor.dirty)
        self.editor.undo()
        self.assertEqual(self.editor.content(), original)
        self.assertFalse(self.editor.dirty)
        self.editor.redo()
        self.assertTrue(self.editor.dirty)

    def test_replace_current_advances_and_match_limit_prevents_partial_replace_all(self):
        self.reset_text("one one one")
        self.editor.show_find()
        self.editor.find_text.set("one")
        self.editor.replace_text.set("two")
        self.editor.replace_current()
        self.assertEqual(self.editor.content(), "two one one")
        self.assertEqual(self.editor.active_match, ("1.4", "1.7"))
        self.editor.replace_current()
        self.assertEqual(self.editor.content(), "two two one")
        self.reset_text("one one one")
        self.editor.MATCH_LIMIT = 2
        self.editor.replace_all()
        self.assertEqual(self.editor.content(), "one one one")
        self.messages.showinfo.assert_called_once()

    def test_save_separates_undo_history_and_undo_returns_to_saved_state(self):
        path = self.folder / "save-point.txt"
        self.editor.text.insert("1.0", "Saved text")
        self.dialogs.asksaveasfilename.return_value = str(path)
        self.assertTrue(self.editor.save())
        self.editor.text.insert("end-1c", " plus unsaved edit")
        self.editor.refresh()
        self.assertTrue(self.editor.dirty)
        self.editor.undo()
        self.assertEqual(self.editor.content(), "Saved text")
        self.assertFalse(self.editor.dirty)
        self.assertIn("Saved", self.editor.document_label.get())
        self.assertEqual(path.read_text(encoding="utf-8"), "Saved text")

    def test_finished_result_resets_search_and_undo_then_saves_as_new_document(self):
        self.reset_text("Previous document")
        self.editor.show_find()
        self.editor.find_text.set("document")
        self.editor.replace_text.set("replacement")
        self.editor.match_case.set(True)
        self.editor._refresh_matches()
        self.assertTrue(self.editor.matches)
        self.assertTrue(self.editor.load_transcription("Hello 🌍\r\nA finished result", "Interview.txt"))
        self.root.update()
        self.assertEqual(self.editor.content(), "Hello 🌍\nA finished result")
        self.assertTrue(self.editor.dirty)
        self.assertIsNone(self.editor.document)
        self.assertIn("Interview.txt · Unsaved changes", self.editor.document_label.get())
        self.assertFalse(self.editor.text.edit("canundo"))
        self.assertFalse(self.editor.find_panel.winfo_ismapped())
        self.assertEqual(self.editor.find_text.get(), "")
        self.assertEqual(self.editor.replace_text.get(), "")
        self.assertFalse(self.editor.match_case.get())
        self.assertEqual(self.editor.text.tag_ranges("find_match"), ())
        self.editor.text.insert("end-1c", " plus an edit")
        self.editor.undo()
        self.assertEqual(self.editor.content(), "Hello 🌍\nA finished result")
        self.assertTrue(self.editor.dirty)
        path = self.folder / "Interview.txt"
        self.dialogs.asksaveasfilename.return_value = str(path)
        self.assertTrue(self.editor.save())
        self.assertEqual(self.dialogs.asksaveasfilename.call_args.kwargs["initialfile"], "Interview.txt")
        self.assertFalse(self.editor.dirty)
        self.assertEqual(path.read_text(encoding="utf-8"), "Hello 🌍\nA finished result")

    def test_finished_result_can_be_retried_after_cancel_without_losing_edits(self):
        self.reset_text("Saved text")
        self.editor.text.insert("end-1c", " edited while transcribing")
        self.editor.show_find()
        self.editor.find_text.set("edited")
        self.editor._refresh_matches()
        self.assertFalse(self.editor.load_transcription("Finished transcript", "Recording.txt"))
        self.assertEqual(self.editor.content(), "Saved text edited while transcribing")
        self.assertEqual(self.editor.saved_text, "Saved text")
        self.assertEqual(self.editor.find_text.get(), "edited")
        self.assertTrue(self.editor.text.edit("canundo"))
        self.messages.askyesnocancel.return_value = False
        self.assertTrue(self.editor.load_transcription("Finished transcript", "Recording.txt"))
        self.assertEqual(self.editor.content(), "Finished transcript")
        self.assertEqual(self.messages.askyesnocancel.call_count, 2)
        self.assertFalse(self.editor.text.edit("canundo"))

    def test_shortcuts_route_open_save_find_and_preserve_recording_queue(self):
        source, copy = self.folder / "opened.txt", self.folder / "copy.txt"
        source.write_text("Open this transcript", encoding="utf-8")
        self.dialogs.askopenfilename.return_value = str(source)
        self.ui.state.add_files(["synthetic/recording.wav"])
        self.ui.refresh_files(selected_paths=self.ui.state.files)
        modifier = self.ui.primary_key
        self.key(self.editor.text, f"<{modifier}-o>")
        self.assertEqual(self.editor.content(), "Open this transcript")
        self.dialogs.askopenfilenames.assert_not_called()
        self.key(self.editor.text, f"<{modifier}-f>")
        self.assertEqual(self.root.focus_get(), self.editor.find_entry)
        self.key(self.editor.find_entry, "<Escape>")
        self.assertFalse(self.editor.find_panel.winfo_ismapped())
        self.key(self.editor.text, f"<{modifier}-a>")
        self.key(self.editor.text, "<Delete>")
        self.assertEqual(self.editor.content(), "")
        self.assertEqual(self.ui.state.files, ["synthetic/recording.wav"])
        self.key(self.editor.text, f"<{modifier}-z>")
        self.assertEqual(self.editor.content(), "Open this transcript")
        self.key(self.editor.text, f"<{modifier}-Shift-Z>")
        self.assertEqual(self.editor.content(), "")
        self.key(self.editor.text, f"<{modifier}-z>")
        self.dialogs.asksaveasfilename.return_value = str(copy)
        self.key(self.editor.text, f"<{modifier}-Shift-S>")
        self.assertEqual(copy.read_text(encoding="utf-8"), "Open this transcript")
        self.editor.text.insert("end-1c", "!")
        self.key(self.editor.text, f"<{modifier}-s>")
        self.assertEqual(copy.read_text(encoding="utf-8"), "Open this transcript!")
        self.assertEqual(source.read_text(encoding="utf-8"), "Open this transcript")

    def test_close_cancel_and_canceled_save_keep_the_actual_window_open(self):
        self.editor.text.insert("1.0", "Do not lose this")
        self.ui.close_preview()
        self.assertTrue(self.root.winfo_exists())
        self.messages.askyesnocancel.return_value = True
        self.dialogs.asksaveasfilename.return_value = ""
        self.ui.close_preview()
        self.assertTrue(self.root.winfo_exists())
        self.assertEqual(self.editor.content(), "Do not lose this")
        path = self.folder / "closed.txt"
        self.dialogs.asksaveasfilename.return_value = str(path)
        self.ui.close_preview()
        self.assertEqual(path.read_text(encoding="utf-8"), "Do not lose this")

    def test_word_count_font_size_and_find_layout_at_minimum_window(self):
        self.reset_text("Hello 🌍\nTwo words")
        self.assertTrue(self.editor.count_label.get().startswith("4 words"))
        self.editor.font_size.set("24")
        self.editor.change_font_size()
        size = self.root.tk.call("font", "actual", self.editor.text.cget("font"), "-size")
        self.assertEqual(int(size), 24)
        self.assertFalse(self.editor.dirty)
        self.editor.show_find()
        self.root.update()

        def assert_bounds(widget):
            for child in widget.winfo_children():
                if child.winfo_ismapped():
                    self.assertGreaterEqual(child.winfo_x(), -2, str(child))
                    self.assertGreaterEqual(child.winfo_y(), -2, str(child))
                    self.assertLessEqual(child.winfo_x() + child.winfo_width(), widget.winfo_width() + 2, str(child))
                    self.assertLessEqual(child.winfo_y() + child.winfo_height(), widget.winfo_height() + 2, str(child))
                    if child != self.ui.settings_canvas:
                        assert_bounds(child)
        assert_bounds(self.root)
        self.assertGreater(self.editor.text.winfo_height(), 100)


if __name__ == "__main__":
    unittest.main()

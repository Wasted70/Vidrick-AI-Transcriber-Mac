"""Pure preview-state tests. Fake paths are never opened or inspected."""

from contextlib import contextmanager
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from vidrick_mac.preview_model import COLORS, PREVIEW_TABS, PreviewState, display_filename


@contextmanager
def forbid_external_io():
    with patch("builtins.open", side_effect=AssertionError("File I/O attempted")), \
            patch.object(Path, "open", side_effect=AssertionError("Path I/O attempted")), \
            patch.object(Path, "stat", side_effect=AssertionError("Filesystem inspection attempted")), \
            patch.object(Path, "mkdir", side_effect=AssertionError("Directory creation attempted")), \
            patch.object(socket, "create_connection", side_effect=AssertionError("Network attempted")):
        yield


class PreviewModelTests(unittest.TestCase):
    def test_windows_display_names_handle_drives_unc_and_unicode_without_io(self):
        with forbid_external_io():
            self.assertEqual(display_filename(r"C:\Recordings\Client call.wav", windows_preview=True), "Client call.wav")
            self.assertEqual(display_filename(r"\\server\share\会議.mp4", windows_preview=True), "会議.mp4")
            self.assertEqual(display_filename("synthetic/subfolder/interview.mp3", windows_preview=True), "interview.mp3")

    def test_mac_display_names_preserve_literal_backslashes_without_io(self):
        with forbid_external_io():
            self.assertEqual(display_filename(r"/Users/test/notes\part one.wav", windows_preview=False), r"notes\part one.wav")
            self.assertEqual(display_filename("/Recordings/entrevista español.wav", windows_preview=False), "entrevista español.wav")

    def test_state_starts_empty_and_instances_do_not_share_files(self):
        first, second = PreviewState(), PreviewState()
        self.assertEqual(first.files, [])
        first.add_files(["synthetic/one.wav"])
        self.assertEqual(second.files, [])

    def test_add_files_preserves_order_and_counts_only_new_paths(self):
        state = PreviewState()
        self.assertEqual(state.add_files(["synthetic/b.wav", "synthetic/a.mp4"]), 2)
        self.assertEqual(state.add_files(["synthetic/b.wav", "synthetic/c.mp3",
                                          "synthetic/c.mp3"]), 1)
        self.assertEqual(state.files, ["synthetic/b.wav", "synthetic/a.mp4", "synthetic/c.mp3"])

    def test_canceled_or_empty_selection_changes_nothing(self):
        state = PreviewState()
        state.add_files(["synthetic/keep.wav"])
        self.assertEqual(state.add_files(()), 0)
        self.assertEqual(state.files, ["synthetic/keep.wav"])

    def test_multi_remove_uses_original_indices_without_shift_errors(self):
        state = PreviewState()
        state.add_files(["a.wav", "b.wav", "c.wav", "d.wav"])
        state.remove_files([0, 2])
        self.assertEqual(state.files, ["b.wav", "d.wav"])

    def test_repeated_and_out_of_range_indices_are_safe(self):
        state = PreviewState()
        state.add_files(["a.wav", "b.wav", "c.wav"])
        state.remove_files([-1, 99, 1, 1])
        self.assertEqual(state.files, ["a.wav", "c.wav"])

    def test_remove_empty_selection_and_remove_from_empty_are_safe(self):
        state = PreviewState()
        state.remove_files([0, -1, 50])
        self.assertEqual(state.files, [])
        state.add_files(["a.wav"])
        state.remove_files([])
        self.assertEqual(state.files, ["a.wav"])

    def test_clear_is_repeatable_and_files_can_be_added_again(self):
        state = PreviewState()
        state.add_files(["a.wav", "b.wav"])
        state.clear_files()
        state.clear_files()
        self.assertEqual(state.files, [])
        self.assertEqual(state.add_files(["a.wav"]), 1)

    def test_summary_reports_count_without_implying_processing(self):
        state = PreviewState()
        self.assertIsInstance(state.summary, str)
        self.assertTrue(state.summary)
        state.add_files(["a.wav", "b.wav"])
        self.assertIn("2", state.summary)
        self.assertIn("read", state.summary.lower())
        self.assertTrue("not" in state.summary.lower() or "no" in state.summary.lower())

    def test_fake_and_unicode_paths_require_no_filesystem_or_network_access(self):
        paths = ["does-not-exist/entrevista espa\u00f1ol.wav", "unavailable/\u4f1a\u8bae.mp4"]
        with forbid_external_io():
            state = PreviewState()
            self.assertEqual(state.add_files(paths), 2)
            self.assertEqual(state.files, paths)
            self.assertTrue(state.summary)
            state.remove_files([1])
            state.clear_files()
        self.assertEqual(state.files, [])

    def test_preview_navigation_uses_expected_windows_app_sections(self):
        self.assertEqual(PREVIEW_TABS, (
            "Transcribe", "Editor", "Insights", "History", "About & Updates"))

    def test_palette_is_nonempty_and_colors_are_hex_values(self):
        self.assertTrue(COLORS)
        for name, color in COLORS.items():
            with self.subTest(name=name):
                self.assertRegex(color, r"^#[0-9a-fA-F]{6}$")


if __name__ == "__main__":
    unittest.main()

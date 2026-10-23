"""Subtitle format and atomic export tests using synthetic project-local files."""

import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vidrick_mac.subtitle_io import (
    MAX_TIMESTAMP_MS, SubtitleCue, SubtitleError, cues_for_text,
    parse_timed_transcript, render_subtitles, write_subtitles,
)


class SubtitleFormatTests(unittest.TestCase):
    def test_srt_has_indexes_comma_milliseconds_and_blank_separators(self):
        cues = (SubtitleCue(999, 61002, "Hello"), SubtitleCue(3723004, 3724506, "World"))
        self.assertEqual(render_subtitles(cues, "srt"),
                         "1\n00:00:00,999 --> 00:01:01,002\nHello\n\n"
                         "2\n01:02:03,004 --> 01:02:04,506\nWorld\n\n")

    def test_vtt_has_header_dot_milliseconds_and_literal_text(self):
        cues = (SubtitleCue(0, 1000, 'Use <b> & --> "quotes"'),)
        self.assertEqual(render_subtitles(cues, "vtt"),
                         'WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\n'
                         'Use &lt;b&gt; &amp; --&gt; "quotes"\n\n')

    def test_srt_does_not_turn_literal_editor_text_into_formatting_tags(self):
        result = render_subtitles((SubtitleCue(0, 1, "<i>literal</i> &amp;"),), "srt")
        self.assertIn("&lt;i&gt;literal&lt;/i&gt; &amp;amp;", result)

    def test_multiline_unicode_text_and_input_newlines_are_preserved(self):
        result = render_subtitles((SubtitleCue(0, 1, "Résumé 👋\r\n会議\rThird"),), "vtt")
        self.assertIn("Résumé 👋\n会議\nThird\n\n", result)
        self.assertNotIn("\r", result)

    def test_long_hours_do_not_wrap_and_overlapping_cues_are_allowed(self):
        cues = (SubtitleCue(360000000, 360100000, "A"),
                SubtitleCue(360000000, MAX_TIMESTAMP_MS, "B"))
        result = render_subtitles(cues, "vtt")
        self.assertIn("100:00:00.000 --> 100:01:40.000", result)
        self.assertIn("277777:46:39.999", result)

    def test_invalid_times_are_rejected_without_coercion(self):
        for start, end in ((True, 1), (0, False), (0.0, 1), ("0", 1),
                           (-1, 1), (2, 2), (3, 2), (0, MAX_TIMESTAMP_MS + 1)):
            with self.subTest(start=start, end=end), self.assertRaises(SubtitleError):
                render_subtitles((SubtitleCue(start, end, "word"),), "srt")

    def test_out_of_order_empty_and_invalid_cues_are_rejected(self):
        cases = ((), (None,), (SubtitleCue(2, 3, "a"), SubtitleCue(1, 2, "b")),
                 (SubtitleCue(0, 1, "  \n"),), (SubtitleCue(0, 1, "a\n \nb"),),
                 (SubtitleCue(0, 1, "binary\x00"),), (SubtitleCue(0, 1, "bad\ud800"),),
                 (SubtitleCue(0, 1, 42),))
        for cues in cases:
            with self.subTest(cues=cues), self.assertRaises(SubtitleError):
                render_subtitles(cues, "vtt")

    def test_format_is_explicit(self):
        with self.assertRaises(SubtitleError):
            render_subtitles((SubtitleCue(0, 1, "word"),), "ass")

    def test_limits_include_utf8_bytes_escaping_and_total_cue_count(self):
        with patch("vidrick_mac.subtitle_io.MAX_TRANSCRIPT_BYTES", 5):
            with self.assertRaises(SubtitleError):
                render_subtitles((SubtitleCue(0, 1, "会議"),), "srt")
            with self.assertRaises(SubtitleError):
                render_subtitles((SubtitleCue(0, 1, "abc"), SubtitleCue(1, 2, "def")), "srt")
        with patch("vidrick_mac.subtitle_io.MAX_SUBTITLE_BYTES", 70):
            with self.assertRaises(SubtitleError):
                render_subtitles((SubtitleCue(0, 1, "&" * 10),), "vtt")
        with patch("vidrick_mac.subtitle_io.MAX_CUES", 1):
            with self.assertRaises(SubtitleError):
                render_subtitles((SubtitleCue(0, 1, "a"), SubtitleCue(1, 2, "b")), "srt")

    def test_markers_are_parsed_with_multiline_text_and_optional_separators(self):
        text = ("\r\n[00:00:00.000 --> 00:00:02.030] First\r\ncontinued\r\n\r\n"
                "[01:02:03.004 --> 01:02:04.005]\r\nRésumé 👋\r\n")
        self.assertEqual(parse_timed_transcript(text),
                         (SubtitleCue(0, 2030, "First\ncontinued"),
                          SubtitleCue(3723004, 3724005, "Résumé 👋")))

    def test_malformed_markers_and_untimed_prefix_fail(self):
        invalid = ("Introduction\n[00:00:00.000 --> 00:00:01.000] word",
                   "[00:60:00.000 --> 01:00:01.000] word",
                   "[00:00:60.000 --> 01:00:01.000] word",
                   "[00:00:00,000 --> 00:00:01,000] word",
                   "[0:00:00.000 --> 00:00:01.000] word",
                   "[999999999999:00:00.000 --> 00:00:01.000] word",
                   "[00:00:00.000 --> 00:00:01.000] first\n[bad --> marker] second",
                   "[00:00:00.000 --> 00:00:01.000] first\n\ncontinuation",
                   "[00:00:00.000 --> 00:00:01.000]\n\n")
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(SubtitleError):
                parse_timed_transcript(text)

    def test_parse_rejects_nontext_and_unicode_nul_or_byte_overflow(self):
        for value in (None, "\x00", "\ud800"):
            with self.subTest(value=value), self.assertRaises(SubtitleError):
                parse_timed_transcript(value)
        with patch("vidrick_mac.subtitle_io.MAX_TRANSCRIPT_BYTES", 3):
            with self.assertRaises(SubtitleError):
                parse_timed_transcript("会議")

    def test_hidden_timestamps_pair_current_text_and_keep_saved_times(self):
        self.assertEqual(cues_for_text("\nUpdated first line\r\n\r\nSecond 👋\n", ((0, 1000), (2000, 3000))),
                         (SubtitleCue(0, 1000, "Updated first line"), SubtitleCue(2000, 3000, "Second 👋")))

    def test_visible_timestamps_take_precedence_over_saved_times(self):
        self.assertEqual(cues_for_text("[00:00:03.000 --> 00:00:04.000] Edited", ((0, 1000),)),
                         (SubtitleCue(3000, 4000, "Edited"),))

    def test_damaged_marker_does_not_fall_back_to_hidden_timestamps(self):
        with self.assertRaisesRegex(SubtitleError, "timestamp"):
            cues_for_text("[00:00:bad --> 00:00:01.000] word", ((0, 1000),))

    def test_deleted_marker_brackets_cannot_become_literal_caption_text(self):
        for text in ("00:00:00.000 --> 00:00:01.000] word",
                     "  00:00:00.000 --> 00:00:01.000] word",
                     "[00:00:00.000 --> 00:00:01.000 word"):
            with self.subTest(text=text):
                with self.assertRaisesRegex(SubtitleError, "Fix the timestamp on line 1"):
                    cues_for_text(text, ((0, 1000),))
                with self.assertRaisesRegex(SubtitleError, "Fix the timestamp on line 2"):
                    parse_timed_transcript("[00:00:00.000 --> 00:00:01.000] First\n" + text)

    def test_invalid_minutes_and_empty_visible_cue_cannot_use_stored_timing(self):
        with self.assertRaisesRegex(SubtitleError, "minutes and seconds"):
            cues_for_text("[00:60:00.000 --> 01:00:01.000] word", ((0, 1000),))
        with self.assertRaisesRegex(SubtitleError, "no subtitle text"):
            cues_for_text("[00:00:00.000 --> 00:00:01.000]  \n", ((0, 1000),))

    def test_ordinary_text_with_a_time_or_arrow_remains_literal(self):
        for text in ("00:00:01.000 on the clock", "Look --> here", "Start at 00:00:01.000 --> here"):
            with self.subTest(text=text):
                self.assertEqual(cues_for_text(text, ((0, 1000),)), (SubtitleCue(0, 1000, text),))

    def test_hidden_timestamps_require_one_nonempty_line_per_segment(self):
        for text in ("", "First", "First\nSecond\nThird"):
            with self.subTest(text=text), self.assertRaisesRegex(SubtitleError, "number of text lines"):
                cues_for_text(text, ((0, 1000), (1000, 2000)))

    def test_plain_text_never_gets_invented_timings(self):
        with self.assertRaisesRegex(SubtitleError, "needs segment timings"):
            cues_for_text("Words without timings")

    def test_invalid_saved_timing_shape_type_and_order_fail(self):
        for timings in (None, (1,), ((0,),), ((False, 1000),), ((1000, 0),)):
            with self.subTest(timings=timings), self.assertRaises(SubtitleError):
                cues_for_text("One", timings)


class SubtitleWriteTests(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="subtitle-io-", dir=output)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / "字幕.srt"
        self.cues = (SubtitleCue(0, 1234, "Résumé 👋\n会議"),)

    def test_writes_utf8_without_bom_and_returns_absolute_path(self):
        self.assertEqual(write_subtitles(self.path, self.cues, "srt"), self.path)
        self.assertEqual(self.path.read_bytes(), render_subtitles(self.cues, "srt").encode("utf-8"))
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_replaces_existing_file_and_supports_uppercase_extensions(self):
        self.path = self.path.with_suffix(".VTT")
        self.path.write_bytes(b"old")
        write_subtitles(self.path, self.cues, "vtt")
        self.assertTrue(self.path.read_bytes().startswith(b"WEBVTT\n\n"))
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_extension_mismatch_and_invalid_content_do_not_touch_destination(self):
        self.path.write_bytes(b"original")
        with self.assertRaises(SubtitleError):
            write_subtitles(self.path, self.cues, "vtt")
        with self.assertRaises(SubtitleError):
            write_subtitles(self.path, (SubtitleCue(0, 0, "invalid"),), "srt")
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_flush_replace_and_chmod_failure_preserve_original_and_clean_temp(self):
        self.path.write_bytes(b"original")
        for operation in ("fsync", "replace", "chmod"):
            with self.subTest(operation=operation):
                with patch(f"vidrick_mac.subtitle_io.os.{operation}", side_effect=OSError("disk failure")):
                    with self.assertRaises(SubtitleError):
                        write_subtitles(self.path, self.cues, "srt")
                self.assertEqual(self.path.read_bytes(), b"original")
                self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_changed_target_during_staging_is_preserved(self):
        self.path.write_bytes(b"original")
        real_fsync = os.fsync

        def external_edit(descriptor):
            real_fsync(descriptor)
            self.path.write_bytes(b"external edit during export")

        with patch("vidrick_mac.subtitle_io.os.fsync", side_effect=external_edit):
            with self.assertRaisesRegex(SubtitleError, "changed while exporting"):
                write_subtitles(self.path, self.cues, "srt")
        self.assertEqual(self.path.read_bytes(), b"external edit during export")
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_new_target_appearing_during_staging_is_preserved(self):
        real_fsync = os.fsync

        def external_create(descriptor):
            real_fsync(descriptor)
            self.path.write_bytes(b"another application")

        with patch("vidrick_mac.subtitle_io.os.fsync", side_effect=external_create):
            with self.assertRaises(SubtitleError):
                write_subtitles(self.path, self.cues, "srt")
        self.assertEqual(self.path.read_bytes(), b"another application")
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_directory_and_nonregular_targets_are_rejected_before_staging(self):
        self.path.mkdir()
        with patch("vidrick_mac.subtitle_io.tempfile.NamedTemporaryFile") as staged:
            with self.assertRaisesRegex(SubtitleError, "regular"):
                write_subtitles(self.path, self.cues, "srt")
        staged.assert_not_called()
        self.assertEqual(list(self.path.iterdir()), [])
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK):
            with self.subTest(mode=mode):
                with patch("vidrick_mac.subtitle_io.Path.lstat", return_value=SimpleNamespace(st_mode=mode)):
                    with self.assertRaisesRegex(SubtitleError, "regular"):
                        write_subtitles(self.path, self.cues, "srt")

    def test_nonregular_target_swapped_in_during_write_is_preserved(self):
        real_fsync = os.fsync

        def swapped_directory(descriptor):
            real_fsync(descriptor)
            self.path.mkdir()

        with patch("vidrick_mac.subtitle_io.os.fsync", side_effect=swapped_directory):
            with self.assertRaisesRegex(SubtitleError, "regular"):
                write_subtitles(self.path, self.cues, "srt")
        self.assertTrue(self.path.is_dir())
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_read_only_staging_copy_is_cleaned_after_replace_failure(self):
        self.path.write_bytes(b"original")
        self.path.chmod(stat.S_IREAD)
        try:
            with patch("vidrick_mac.subtitle_io.os.replace", side_effect=PermissionError("read-only target")):
                with self.assertRaises(SubtitleError):
                    write_subtitles(self.path, self.cues, "srt")
            self.assertEqual(self.path.read_bytes(), b"original")
            self.assertEqual(list(self.folder.iterdir()), [self.path])
        finally:
            self.path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_invalid_or_missing_parent_reports_subtitle_error(self):
        for target in (self.folder / "missing" / "captions.srt", "invalid\x00.srt", None):
            with self.subTest(target=target), self.assertRaises(SubtitleError):
                write_subtitles(target, self.cues, "srt")

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "POSIX symlink test")
    def test_symlink_preserves_referent_and_link(self):
        referent = self.folder / "referent.txt"
        referent.write_bytes(b"original")
        self.path.symlink_to(referent)
        with self.assertRaisesRegex(SubtitleError, "regular"):
            write_subtitles(self.path, self.cues, "srt")
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(referent.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()

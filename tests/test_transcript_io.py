"""Actual transcript I/O, isolated to ignored temporary folders in the Mac project."""

import codecs
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from vidrick_mac.transcript_io import (
    MAX_TRANSCRIPT_BYTES, TranscriptConflict, TranscriptError,
    read_transcript, write_transcript,
)


class TranscriptIOTests(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[1] / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="transcript-io-", dir=output)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / "会議 notes.txt"

    def test_new_unicode_transcript_is_utf8_and_roundtrips(self):
        saved = write_transcript(self.path, "Résumé 👋\n会議\n")
        self.assertEqual(self.path.read_bytes(), "Résumé 👋\n会議\n".encode("utf-8"))
        self.assertEqual(saved, read_transcript(self.path))
        self.assertEqual(saved.path, self.path)
        self.assertEqual(saved.encoding, "utf-8")
        self.assertEqual(saved.newline, "\n")

    def test_bom_and_crlf_are_preserved_while_editor_gets_lf(self):
        for encoding, bom in (("utf-8", codecs.BOM_UTF8), ("utf-16-le", codecs.BOM_UTF16_LE),
                              ("utf-16-be", codecs.BOM_UTF16_BE)):
            with self.subTest(encoding=encoding):
                original = bom + "First 👋\r\nSecond\r\n".encode(encoding)
                self.path.write_bytes(original)
                loaded = read_transcript(self.path)
                self.assertEqual(loaded.text, "First 👋\nSecond\n")
                self.assertEqual(loaded.newline, "\r\n")
                saved = write_transcript(self.path, loaded.text + "Third\n", previous=loaded)
                self.assertEqual(self.path.read_bytes(), bom + "First 👋\r\nSecond\r\nThird\r\n".encode(encoding))
                self.assertEqual(saved, read_transcript(self.path))

    def test_mixed_endings_use_dominant_style_and_ties_use_first(self):
        for original, expected in ((b"one\rtwo\rthree\n", "\r"), (b"one\ntwo\r\n", "\n"),
                                   (b"one\r\ntwo\n", "\r\n"), (b"no ending", "\n")):
            with self.subTest(original=original):
                self.path.write_bytes(original)
                loaded = read_transcript(self.path)
                self.assertNotIn("\r", loaded.text)
                self.assertEqual(loaded.newline, expected)

    def test_save_as_preserves_format_without_checking_old_target(self):
        self.path.write_bytes(codecs.BOM_UTF8 + b"one\r\n")
        loaded = read_transcript(self.path)
        self.path.unlink()
        other = self.folder / "Copy.txt"
        saved = write_transcript(other, "two\n", previous=loaded)
        self.assertEqual(saved.path, other)
        self.assertEqual(other.read_bytes(), codecs.BOM_UTF8 + b"two\r\n")

    def test_external_edit_or_deleted_file_requires_explicit_overwrite(self):
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                loaded = write_transcript(self.path, "original")
                if deleted:
                    self.path.unlink()
                else:
                    self.path.write_text("external edit", encoding="utf-8")
                with self.assertRaises(TranscriptConflict):
                    write_transcript(self.path, "editor change", previous=loaded)
                if deleted:
                    self.assertFalse(self.path.exists())
                else:
                    self.assertEqual(self.path.read_text(encoding="utf-8"), "external edit")
                saved = write_transcript(self.path, "editor change", previous=loaded, overwrite_changed=True)
                self.assertEqual(saved, read_transcript(self.path))
                self.assertEqual(saved.text, "editor change")

    def test_change_while_staging_is_detected_and_temporary_file_is_removed(self):
        loaded = write_transcript(self.path, "original")
        real_fsync = os.fsync

        def external_edit(descriptor):
            real_fsync(descriptor)
            self.path.write_bytes(b"changed during save")

        with patch("vidrick_mac.transcript_io.os.fsync", side_effect=external_edit):
            with self.assertRaises(TranscriptConflict):
                write_transcript(self.path, "my changes", previous=loaded)
        self.assertEqual(self.path.read_bytes(), b"changed during save")
        self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_failed_replace_or_flush_preserves_original_and_cleans_temporary_file(self):
        for operation in ("replace", "fsync"):
            with self.subTest(operation=operation):
                loaded = write_transcript(self.path, "original")
                with patch(f"vidrick_mac.transcript_io.os.{operation}", side_effect=OSError("simulated disk failure")):
                    with self.assertRaises(TranscriptError):
                        write_transcript(self.path, "changed", previous=loaded)
                self.assertEqual(self.path.read_bytes(), b"original")
                self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_read_failure_is_reported_without_writing(self):
        with self.assertRaises(TranscriptError):
            read_transcript(self.path)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.path.write_bytes(b"original")
        with patch("vidrick_mac.transcript_io.os.open", side_effect=PermissionError("simulated permission denial")):
            with self.assertRaises(TranscriptError):
                read_transcript(self.path)
        self.assertEqual(self.path.read_bytes(), b"original")

    def test_non_regular_target_is_rejected_before_open_or_conflict_read(self):
        loaded = write_transcript(self.path, "original")
        self.path.unlink()
        self.path.mkdir()
        with patch("vidrick_mac.transcript_io.os.open") as open_file:
            with self.assertRaisesRegex(TranscriptError, "regular"):
                read_transcript(self.path)
            with self.assertRaisesRegex(TranscriptError, "regular"):
                write_transcript(self.path, "editor changes", previous=loaded)
        open_file.assert_not_called()
        self.assertTrue(self.path.is_dir())
        self.assertEqual(list(self.path.iterdir()), [])

    def test_non_regular_opened_descriptor_is_closed_without_reading(self):
        self.path.write_bytes(b"original")
        real_close = os.close
        with patch("vidrick_mac.transcript_io.os.fstat", return_value=SimpleNamespace(st_mode=stat.S_IFIFO)), \
                patch("vidrick_mac.transcript_io.os.fdopen") as stream, \
                patch("vidrick_mac.transcript_io.os.close", wraps=real_close) as close:
            with self.assertRaisesRegex(TranscriptError, "regular"):
                read_transcript(self.path)
        stream.assert_not_called()
        close.assert_called_once()
        self.assertEqual(self.path.read_bytes(), b"original")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFOs are not available on this host")
    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        os.mkfifo(self.path)
        with patch("vidrick_mac.transcript_io.os.open", side_effect=AssertionError("FIFO must not be opened")):
            with self.assertRaisesRegex(TranscriptError, "regular"):
                read_transcript(self.path)

    def test_failed_replace_cleans_temporary_copy_of_read_only_file(self):
        loaded = write_transcript(self.path, "original")
        self.path.chmod(stat.S_IREAD)
        try:
            with patch("vidrick_mac.transcript_io.os.replace", side_effect=PermissionError("read-only target")):
                with self.assertRaises(TranscriptError):
                    write_transcript(self.path, "changes", previous=loaded)
            self.assertEqual(self.path.read_bytes(), b"original")
            self.assertEqual(list(self.folder.iterdir()), [self.path])
        finally:
            self.path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_invalid_encoding_binary_and_non_txt_are_rejected(self):
        for content in (b"\xff\x80", b"binary\0text", codecs.BOM_UTF16_LE + "binary\0text".encode("utf-16-le"),
                        codecs.BOM_UTF32_LE + "hello".encode("utf-32-le"), codecs.BOM_UTF16_BE + b"\x00"):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(TranscriptError):
                    read_transcript(self.path)
                self.assertEqual(self.path.read_bytes(), content)
        invalid = self.folder / "recording.wav"
        with self.assertRaises(TranscriptError):
            read_transcript(invalid)
        with self.assertRaises(TranscriptError):
            write_transcript(invalid, "not a recording")
        self.assertFalse(invalid.exists())

    def test_invalid_or_oversize_save_leaves_existing_file_unchanged(self):
        loaded = write_transcript(self.path, "keep me")
        for text in ("binary\0text", "unpaired surrogate \ud800", "é" * (MAX_TRANSCRIPT_BYTES // 2 + 1),
                     "x" * (MAX_TRANSCRIPT_BYTES + 1)):
            with self.subTest(length=len(text)):
                with self.assertRaises(TranscriptError):
                    write_transcript(self.path, text, previous=loaded)
                self.assertEqual(self.path.read_bytes(), b"keep me")
                self.assertEqual(list(self.folder.iterdir()), [self.path])

    def test_read_limit_and_save_limit_include_exact_boundary(self):
        content = "x" * MAX_TRANSCRIPT_BYTES
        saved = write_transcript(self.path, content)
        self.assertEqual(read_transcript(self.path), saved)
        with self.path.open("ab") as stream:
            stream.write(b"x")
        with self.assertRaises(TranscriptError):
            read_transcript(self.path)
        with self.assertRaises(TranscriptConflict):
            write_transcript(self.path, "edit", previous=saved)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are not available on Windows")
    def test_atomic_save_preserves_existing_posix_permission_bits(self):
        loaded = write_transcript(self.path, "original")
        self.path.chmod(0o640)
        write_transcript(self.path, "updated", previous=loaded)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)


if __name__ == "__main__":
    unittest.main()

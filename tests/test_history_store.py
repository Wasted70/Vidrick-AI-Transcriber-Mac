"""History persistence tests use only synthetic data beneath test-output/."""

import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from vidrick_mac.history_store import (
    DATABASE_NAME, MAX_TRANSCRIPT_BYTES, HistoryError, HistoryStore,
)


PROJECT = Path(__file__).resolve().parents[1]


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        output = PROJECT / "test-output"
        output.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="history-store-", dir=output)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "local-history" / "mac"
        self.store = HistoryStore(self.root)
        self.addCleanup(self.store.close)

    def reopen(self):
        self.store.close()
        self.store = HistoryStore(self.root)
        self.addCleanup(self.store.close)
        return self.store

    def test_result_survives_restart_and_lists_metadata_only(self):
        result = self.store.add_result("Hello café 🎤\nSecond line", "Interview.txt", model="Balanced", language="English")
        self.reopen()
        self.assertEqual(self.store.get_entry(result.id), result)
        listed = self.store.list_entries()[0]
        self.assertEqual(listed.id, result.id)
        self.assertEqual(listed.title, "Interview")
        self.assertEqual(listed.text, "")
        self.assertEqual(listed.model, "Balanced")

    def test_in_memory_store_does_not_create_storage(self):
        before = set(self.folder.rglob("*"))
        with patch("vidrick_mac.history_store._private_root", side_effect=AssertionError("disk access")):
            memory = HistoryStore()
            self.addCleanup(memory.close)
            memory.add_result("Synthetic only", "Memory.txt")
            self.assertIsNone(memory.root)
            self.assertEqual(memory.list_entries()[0].title, "Memory")
        self.assertEqual(set(self.folder.rglob("*")), before)

    def test_paths_become_basenames_without_reading_source(self):
        entry = self.store.add_result("Stored text", r"C:\Private\Recordings\Meeting.txt",
                                      model="/private/models/ggml-base.bin")
        self.assertEqual(entry.suggested_name, "Meeting.txt")
        self.assertEqual(entry.title, "Meeting")
        self.assertEqual(entry.model, "ggml-base.bin")
        self.assertNotIn("Private", repr(entry))

    def test_search_matches_title_filename_and_model_case_insensitively(self):
        first = self.store.add_result("body only", "Straße.txt", model="Tiny")
        second = self.store.add_result("secret needle", "Meeting.txt", model="Balanced")
        self.assertEqual([e.id for e in self.store.list_entries("STRASSE")], [first.id])
        self.assertEqual([e.id for e in self.store.list_entries(".TXT")], [second.id, first.id])
        self.assertEqual([e.id for e in self.store.list_entries("balanced")], [second.id])
        self.assertEqual(self.store.list_entries("needle"), [])
        self.assertEqual(self.store.list_entries("' OR 1=1 --"), [])

    def test_rename_preserves_original_result_and_order(self):
        first = self.store.add_result("First body", "First.txt")
        second = self.store.add_result("Second body", "Second.txt")
        self.store.rename_entry(first.id, "  Edited title  ")
        changed = self.store.get_entry(first.id)
        self.assertEqual(changed.title, "Edited title")
        self.assertEqual((changed.text, changed.suggested_name, changed.created_at),
                         (first.text, first.suggested_name, first.created_at))
        self.assertEqual([entry.id for entry in self.store.list_entries()], [second.id, first.id])

    def test_delete_only_removes_selected_database_entry(self):
        recording = self.folder / "Recording.wav"
        recording.write_bytes(b"synthetic recording fixture")
        result = self.store.add_result("Result", str(recording.with_suffix(".txt")))
        other = self.store.add_result("Other", "Other.txt")
        draft_id = uuid.uuid4().hex
        self.store.save_draft(draft_id, "Draft body", "Recording")
        self.store.delete_entry(result.id)
        self.store.delete_entry(result.id)  # idempotent after a stale UI refresh
        self.assertEqual(self.store.list_entries()[0].id, other.id)
        self.assertEqual(self.store.get_draft(draft_id).text, "Draft body")
        self.assertEqual(recording.read_bytes(), b"synthetic recording fixture")
        with self.assertRaisesRegex(HistoryError, "no longer"):
            self.store.get_entry(result.id)

    def test_draft_update_replaces_same_id_and_survives_restart(self):
        identifier = uuid.uuid4().hex
        self.store.save_draft(identifier, "Version one", "Draft")
        updated = self.store.save_draft(identifier, "Version two\nwith edits", "Updated draft")
        self.reopen()
        self.assertEqual(self.store.get_draft(identifier), updated)
        self.assertEqual(len(self.store.list_drafts()), 1)
        self.assertEqual(self.store.list_drafts()[0].text, "")

    def test_empty_draft_is_retained_and_drafts_sort_by_last_update(self):
        first, second = "1" * 32, "2" * 32
        with patch("vidrick_mac.history_store._now", return_value="2026-10-23T12:00:00.000000+00:00"):
            self.store.save_draft(first, "initial", "First")
            self.store.save_draft(second, "second", "Second")
            self.store.save_draft(first, "", "First")
        self.assertEqual([draft.id for draft in self.store.list_drafts()], [first, second])
        self.assertEqual(self.store.get_draft(first).text, "")

    def test_result_order_survives_equal_clock_ticks(self):
        with patch("vidrick_mac.history_store._now", return_value="2026-10-23T12:00:00.000000+00:00"), \
                patch("vidrick_mac.history_store.uuid.uuid4", side_effect=[uuid.UUID(int=2), uuid.UUID(int=1)]):
            first = self.store.add_result("First", "First.txt")
            second = self.store.add_result("Second", "Second.txt")
        self.assertEqual([entry.id for entry in self.store.list_entries()], [second.id, first.id])
        self.assertGreater(second.created_at, first.created_at)

    def test_save_order_survives_restart_and_clock_rollback(self):
        first, second = "1" * 32, "2" * 32
        with patch("vidrick_mac.history_store._now", return_value="2026-10-23T12:00:00.000000+00:00"):
            self.store.save_draft(first, "First", "First")
            entry = self.store.add_result("First", "First.txt")
        self.reopen()
        with patch("vidrick_mac.history_store._now", return_value="2026-10-22T12:00:00.000000+00:00"):
            self.store.save_draft(second, "Second", "Second")
            self.store.save_draft(first, "Latest edit", "First")
            latest = self.store.add_result("Latest", "Latest.txt")
        self.assertEqual([draft.id for draft in self.store.list_drafts()], [first, second])
        self.assertEqual([item.id for item in self.store.list_entries()], [latest.id, entry.id])

    def test_delete_draft_preserves_history(self):
        entry = self.store.add_result("Result", "Result.txt")
        identifier = uuid.uuid4().hex
        self.store.save_draft(identifier, "Draft", "Draft")
        self.store.delete_draft(identifier)
        self.store.delete_draft(identifier)
        self.assertEqual(self.store.list_drafts(), [])
        self.assertEqual(self.store.get_entry(entry.id), entry)
        with self.assertRaisesRegex(HistoryError, "no longer"):
            self.store.get_draft(identifier)

    def test_8mib_utf8_limit_rejects_before_overwriting_draft(self):
        identifier = uuid.uuid4().hex
        previous = self.store.save_draft(identifier, "Preserve me", "Draft")
        with self.assertRaisesRegex(HistoryError, "8 MiB"):
            self.store.save_draft(identifier, "é" * (MAX_TRANSCRIPT_BYTES // 2 + 1), "Draft")
        self.assertEqual(self.store.get_draft(identifier), previous)
        with self.assertRaisesRegex(HistoryError, "8 MiB"):
            self.store.add_result("x" * (MAX_TRANSCRIPT_BYTES + 1), "Large.txt")
        self.assertEqual(self.store.list_entries(), [])

    def test_exact_limit_round_trips(self):
        text = "x" * MAX_TRANSCRIPT_BYTES
        entry = self.store.add_result(text, "At limit.txt")
        self.assertEqual(self.store.get_entry(entry.id).text, text)
        self.assertEqual(self.store.list_entries()[0].text, "")

    def test_invalid_text_is_rejected(self):
        for text in (None, b"bytes", "binary\x00data", "bad surrogate\ud800"):
            with self.subTest(text=repr(text)):
                with self.assertRaises(HistoryError):
                    self.store.add_result(text, "Invalid.txt")
        self.assertEqual(self.store.list_entries(), [])

    def test_invalid_metadata_and_identifiers_do_not_change_saved_data(self):
        entry = self.store.add_result("Safe", "Safe.txt")
        for title in ("", "x" * 241, "line\nbreak", "a/b", r"a\b", "..", "bad\ud800"):
            with self.subTest(title=repr(title)), self.assertRaises(HistoryError):
                self.store.rename_entry(entry.id, title)
        self.assertEqual(self.store.get_entry(entry.id), entry)
        for identifier in ("../anything", "A" * 32, "xyz", "' OR 1=1", None):
            with self.subTest(identifier=identifier), self.assertRaises(HistoryError):
                self.store.delete_entry(identifier)
        for metadata in ({"model": "x" * 241}, {"language": "x" * 81}, {"language": "../en"}):
            with self.subTest(metadata=metadata), self.assertRaises(HistoryError):
                self.store.add_result("Text", "Result.txt", **metadata)

    def test_count_limits_never_silently_delete_saved_content(self):
        entry = self.store.add_result("One", "One.txt")
        identifier = uuid.uuid4().hex
        self.store.save_draft(identifier, "One draft", "Draft")
        with patch("vidrick_mac.history_store.MAX_ENTRIES", 1):
            with self.assertRaisesRegex(HistoryError, "Delete some"):
                self.store.add_result("Two", "Two.txt")
        with patch("vidrick_mac.history_store.MAX_DRAFTS", 1):
            with self.assertRaisesRegex(HistoryError, "Recover or discard"):
                self.store.save_draft(uuid.uuid4().hex, "Two", "Draft")
            self.store.save_draft(identifier, "Updated existing", "Draft")
        self.assertEqual(self.store.get_entry(entry.id), entry)
        self.assertEqual(self.store.get_draft(identifier).text, "Updated existing")

    def test_database_failure_rolls_back_previous_draft(self):
        identifier = uuid.uuid4().hex
        previous = self.store.save_draft(identifier, "Committed", "Draft")
        self.store._connection.execute("CREATE TRIGGER fail_update AFTER UPDATE ON drafts BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END")
        with self.assertRaisesRegex(HistoryError, "previous saved data is unchanged"):
            self.store.save_draft(identifier, "Must roll back", "Changed title")
        self.assertEqual(self.store.get_draft(identifier), previous)
        self.assertFalse(self.store._connection.in_transaction)

    def test_invalid_stored_metadata_and_text_are_rejected(self):
        entry = self.store.add_result("Safe text", "Safe.txt")
        for field, invalid in (("title", "x" * 1000), ("title", "Safe\x00hidden"), ("model", "../hidden"),
                               ("created_at", "no date"), ("text", "binary\x00data")):
            self.store._connection.execute(f"UPDATE entries SET {field}=? WHERE id=?", (invalid, entry.id))
            with self.subTest(field=field, invalid=repr(invalid)), self.assertRaises(HistoryError):
                self.store.get_entry(entry.id)
            self.store._connection.execute(f"UPDATE entries SET {field}=? WHERE id=?", (getattr(entry, field), entry.id))
        self.assertEqual(self.store.get_entry(entry.id), entry)

    def test_metadata_lists_do_not_read_bad_or_large_transcript_bodies(self):
        entry = self.store.add_result("Safe", "Safe.txt")
        self.store._connection.execute("UPDATE entries SET text=? WHERE id=?", ("\x00", entry.id))
        listed = self.store.list_entries()
        self.assertEqual(listed[0].title, "Safe")
        self.assertEqual(listed[0].text, "")
        with self.assertRaises(HistoryError):
            self.store.get_entry(entry.id)

    def test_same_process_second_store_cannot_open_until_first_closes(self):
        entry = self.store.add_result("Saved", "Saved.txt")
        with self.assertRaisesRegex(HistoryError, "already open"):
            HistoryStore(self.root)
        self.reopen()
        self.assertEqual(self.store.get_entry(entry.id), entry)

    def test_other_process_cannot_open_locked_history(self):
        code = "from pathlib import Path; import sys; from vidrick_mac.history_store import HistoryStore, HistoryError\ntry:\n s=HistoryStore(Path(sys.argv[1]))\nexcept HistoryError as e:\n print(str(e)); sys.exit(23)\nelse:\n s.close(); sys.exit(0)"
        result = subprocess.run([sys.executable, "-E", "-B", "-c", code, str(self.root)], cwd=PROJECT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn("already open", result.stdout)

    def test_crash_releases_lock_and_rolls_back_uncommitted_draft(self):
        identifier = uuid.uuid4().hex
        previous = self.store.save_draft(identifier, "Last successful autosave", "Draft")
        self.store.close()
        code = "from pathlib import Path; import os,sys; from vidrick_mac.history_store import HistoryStore\ns=HistoryStore(Path(sys.argv[1]))\ns._connection.execute('BEGIN IMMEDIATE')\ns._connection.execute('UPDATE drafts SET text=? WHERE id=?', ('uncommitted edit',sys.argv[2]))\nos._exit(0)"
        result = subprocess.run([sys.executable, "-E", "-B", "-c", code, str(self.root), identifier],
                                cwd=PROJECT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.reopen()
        self.assertEqual(self.store.get_draft(identifier), previous)

    def test_unknown_future_schema_version_is_not_rewritten(self):
        self.store._connection.execute("PRAGMA user_version=99")
        self.store.close()
        with self.assertRaisesRegex(HistoryError, "newer app"):
            HistoryStore(self.root)
        connection = sqlite3.connect(self.root / DATABASE_NAME)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_corrupt_database_fails_without_deleting_it(self):
        self.store.close()
        path = self.root / DATABASE_NAME
        data = b"synthetic file that is not a sqlite database"
        path.write_bytes(data)
        with self.assertRaises(HistoryError):
            HistoryStore(self.root)
        self.assertEqual(path.read_bytes(), data)

    def test_outside_project_and_traversal_roots_are_rejected(self):
        for root in (PROJECT.parent / "outside-history", PROJECT, self.folder / ".." / "other"):
            with self.subTest(root=root), self.assertRaises(HistoryError):
                HistoryStore(root)

    def test_non_directory_root_and_non_regular_database_are_rejected(self):
        file_root = self.folder / "file-root"
        file_root.write_text("fixture", encoding="utf-8")
        with self.assertRaises(HistoryError):
            HistoryStore(file_root)
        self.store.close()
        database = self.root / DATABASE_NAME
        database.unlink()
        database.mkdir()
        with self.assertRaises(HistoryError):
            HistoryStore(self.root)

    def test_hard_linked_database_is_rejected_without_changing_target(self):
        self.store.close()
        database = self.root / DATABASE_NAME
        other = self.folder / "other.sqlite3"
        other.write_bytes(b"synthetic")
        database.unlink()
        os.link(other, database)
        with self.assertRaisesRegex(HistoryError, "without links"):
            HistoryStore(self.root)
        self.assertEqual(other.read_bytes(), b"synthetic")

    @unittest.skipIf(os.name == "nt", "POSIX symlink test requires native filesystem permissions")
    def test_symlink_root_database_and_journal_are_rejected(self):
        linked = self.folder / "linked-root"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(HistoryError, "symbolic links"):
            HistoryStore(linked)
        self.store.close()
        database = self.root / DATABASE_NAME
        preserved = self.folder / "preserved.sqlite3"
        database.replace(preserved)
        database.symlink_to(preserved)
        with self.assertRaisesRegex(HistoryError, "without links"):
            HistoryStore(self.root)
        database.unlink()
        preserved.replace(database)
        journal = self.root / (DATABASE_NAME + "-journal")
        journal.symlink_to(database)
        with self.assertRaisesRegex(HistoryError, "without links"):
            HistoryStore(self.root)

    @unittest.skipIf(os.name == "nt", "Private Unix permission modes require native POSIX")
    def test_private_permissions(self):
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for name in (DATABASE_NAME, "history.lock"):
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)

    def test_close_is_idempotent_and_further_calls_are_actionable(self):
        self.store.close()
        self.store.close()
        with self.assertRaisesRegex(HistoryError, "History is closed"):
            self.store.list_entries()
        with self.assertRaisesRegex(HistoryError, "History is closed"):
            self.store.add_result("text", "Result.txt")


if __name__ == "__main__":
    unittest.main()

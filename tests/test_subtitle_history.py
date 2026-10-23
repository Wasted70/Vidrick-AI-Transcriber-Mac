"""Synthetic timing persistence, migration, and corruption checks."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from vidrick_mac.history_store import (
    DATABASE_NAME, MAX_TIMINGS, MAX_TIMING_BYTES, MAX_TIMING_OFFSET,
    MAX_TRANSCRIPT_BYTES, Draft, Entry, HistoryError, HistoryStore,
)


PROJECT = Path(__file__).resolve().parents[1]
DATE = "2026-10-22T12:00:00+00:00"


class SubtitleHistoryTests(unittest.TestCase):
    def setUp(self):
        output = PROJECT / "test-output"
        output.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="subtitle-history-", dir=output)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "history"

    def open_store(self):
        store = HistoryStore(self.root)
        self.addCleanup(store.close)
        return store

    def legacy_database(self, *, extra_draft_column=False):
        self.root.mkdir()
        connection = sqlite3.connect(self.root / DATABASE_NAME)
        connection.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL, suggested_name TEXT NOT NULL, created_at TEXT NOT NULL, model TEXT NOT NULL, language TEXT NOT NULL)")
        connection.execute("CREATE TABLE drafts (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("CREATE INDEX entries_date ON entries(created_at DESC,id DESC)")
        connection.execute("CREATE INDEX drafts_date ON drafts(updated_at DESC,id DESC)")
        entry = Entry(uuid.uuid4().hex, "Legacy", "Original transcript", "Legacy.txt", DATE, "Base", "en")
        draft = Draft(uuid.uuid4().hex, "Legacy draft", "Edited transcript", DATE)
        connection.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?)", tuple(entry.__dict__.values())[:-1])
        connection.execute("INSERT INTO drafts VALUES (?,?,?,?)", tuple(draft.__dict__.values())[:-1])
        if extra_draft_column:
            connection.execute("ALTER TABLE drafts ADD COLUMN timings TEXT NOT NULL DEFAULT '[]'")
        connection.execute("PRAGMA user_version=1")
        connection.commit()
        connection.close()
        return entry, draft

    def test_new_schema_round_trips_timing_offsets_without_duplicating_text(self):
        store = self.open_store()
        timings = ((0, 1456), (1200, 2200), (2200, 3600001))
        entry = store.add_result("First\nSecond\nThird", "Timed.txt", timings=timings)
        draft = store.save_draft(uuid.uuid4().hex, "Edited text", "Timed draft", timings=timings)
        self.assertEqual(store._connection.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertEqual(store._connection.execute("SELECT timings FROM entries").fetchone()[0],
                         "[[0,1456],[1200,2200],[2200,3600001]]")
        store.close()
        store = self.open_store()
        self.assertEqual(store.get_entry(entry.id), entry)
        self.assertEqual(store.get_draft(draft.id), draft)
        self.assertEqual(store.list_entries()[0].timings, ())
        self.assertEqual(store.list_drafts()[0].timings, ())

    def test_update_replaces_timing_metadata_and_allows_plain_text_documents(self):
        store = self.open_store()
        identifier = uuid.uuid4().hex
        store.save_draft(identifier, "First", "Draft", timings=((0, 1000),))
        store.save_draft(identifier, "Second", "Draft", timings=[[10, 1100], [1300, 1900]])
        self.assertEqual(store.get_draft(identifier).timings, ((10, 1100), (1300, 1900)))
        store.save_draft(identifier, "Plain text", "Draft")
        self.assertEqual(store.get_draft(identifier).timings, ())
        self.assertEqual(store.add_result("Untimed", "Plain.txt").timings, ())

    def test_zero_duration_and_out_of_order_offsets_remain_available_for_export_validation(self):
        store = self.open_store()
        timings = ((1000, 1000), (0, 500))
        entry = store.add_result("Useful text", "Result.txt", timings=timings)
        draft = store.save_draft(uuid.uuid4().hex, "Different number of lines\nStill useful", "Draft", timings=timings)
        self.assertEqual(store.get_entry(entry.id).timings, timings)
        self.assertEqual(store.get_draft(draft.id).timings, timings)

    def test_legacy_schema_migration_preserves_records_dates_and_indexes(self):
        entry, draft = self.legacy_database()
        store = self.open_store()
        self.assertEqual(store.get_entry(entry.id), entry)
        self.assertEqual(store.get_draft(draft.id), draft)
        self.assertEqual(store._connection.execute("PRAGMA user_version").fetchone()[0], 2)
        indexes = {row[0] for row in store._connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn("entries_date", indexes)
        self.assertIn("drafts_date", indexes)
        updated = store.save_draft(draft.id, "Updated", draft.title, timings=((100, 900),))
        store.close()
        store = self.open_store()
        self.assertEqual(store.get_draft(draft.id), updated)
        self.assertEqual(store.get_entry(entry.id), entry)

    def test_failed_second_column_migration_rolls_back_first_and_keeps_v1_data(self):
        entry, draft = self.legacy_database(extra_draft_column=True)
        with self.assertRaises(HistoryError):
            self.open_store()
        connection = sqlite3.connect(self.root / DATABASE_NAME)
        self.addCleanup(connection.close)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertNotIn("timings", {row[1] for row in connection.execute("PRAGMA table_info(entries)")})
        self.assertEqual(connection.execute("SELECT text FROM entries WHERE id=?", (entry.id,)).fetchone()[0], entry.text)
        self.assertEqual(connection.execute("SELECT text FROM drafts WHERE id=?", (draft.id,)).fetchone()[0], draft.text)

    def test_invalid_offsets_do_not_replace_a_good_draft_or_create_a_result(self):
        store = self.open_store()
        previous = store.save_draft(uuid.uuid4().hex, "Keep this", "Draft", timings=((0, 1000),))
        bad_values = (
            None, "[]", {}, [0, 100], [[0]], [[0, 1, 2]], [[False, 1]], [[0, True]],
            [[0.0, 1]], [[0, "1"]], [[-1, 10]], [[11, 10]], [[0, MAX_TIMING_OFFSET + 1]],
            [(0, 1)] * (MAX_TIMINGS + 1),
        )
        for invalid in bad_values:
            with self.subTest(type=type(invalid), size=len(invalid) if hasattr(invalid, "__len__") else None):
                with self.assertRaises(HistoryError):
                    store.save_draft(previous.id, "Discard this", "Changed", timings=invalid)
                with self.assertRaises(HistoryError):
                    store.add_result("Discard this", "Invalid.txt", timings=invalid)
                self.assertEqual(store.get_draft(previous.id), previous)
        self.assertEqual(store.list_entries(), [])

    def test_largest_supported_text_and_timing_metadata_share_a_row(self):
        store = self.open_store()
        text = "x" * MAX_TRANSCRIPT_BYTES
        timings = ((MAX_TIMING_OFFSET - 1, MAX_TIMING_OFFSET),) * MAX_TIMINGS
        entry = store.add_result(text, "Boundary.txt", timings=timings)
        actual = store.get_entry(entry.id)
        self.assertEqual(actual.text, text)
        self.assertEqual(actual.timings, timings)
        self.assertEqual(store._connection.execute("SELECT length(timings) FROM entries").fetchone()[0], MAX_TIMING_BYTES)

    def test_corrupt_timing_data_fails_on_open_but_metadata_lists_stay_available(self):
        store = self.open_store()
        entry = store.add_result("Keep text", "Result.txt")
        draft = store.save_draft(uuid.uuid4().hex, "Keep draft", "Draft")
        corrupt_values = (
            "not JSON", "{}", "null", "[[true,1]]", "[[0,1.5]]", "[[2,1]]", "[[0,NaN]]",
            "[[0,1,2]]", "[[0]]", "[" * 1100 + "]" * 1100,
            json.dumps([[0, 1]] * (MAX_TIMINGS + 1), separators=(",", ":")),
            " " * (MAX_TIMING_BYTES + 1), sqlite3.Binary(b"[]"),
        )
        for corrupt in corrupt_values:
            with self.subTest(size=len(corrupt)):
                store._connection.execute("UPDATE entries SET timings=? WHERE id=?", (corrupt, entry.id))
                store._connection.execute("UPDATE drafts SET timings=? WHERE id=?", (corrupt, draft.id))
                self.assertEqual(store.list_entries()[0].timings, ())
                self.assertEqual(store.list_drafts()[0].timings, ())
                self.assertEqual(store.list_entries()[0].title, entry.title)
                with self.assertRaises(HistoryError):
                    store.get_entry(entry.id)
                with self.assertRaises(HistoryError):
                    store.get_draft(draft.id)


if __name__ == "__main__":
    unittest.main()

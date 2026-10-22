"""Private local transcript history and draft recovery, without recording I/O.

Construction with no root is in-memory. The app explicitly selects its own
project-local folder on launch. List methods return metadata (``text=''``);
individual text is read only by get_entry/get_draft.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from .transcript_io import MAX_TRANSCRIPT_BYTES


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_ENTRIES = 2000
MAX_DRAFTS = 100
MAX_TITLE = 240
DATABASE_NAME = "history.sqlite3"
_ID = re.compile(r"[0-9a-f]{32}\Z")


def _metadata_projection(fields) -> str:
    # Limit every field before it crosses into Python, including a corrupt
    # database. One extra character makes over-limit metadata fail validation.
    return ",".join(
        f"CASE WHEN typeof({name})='text' AND instr({name},char(0))=0 "
        f"THEN substr({name},1,{limit + 1}) ELSE NULL END AS {name}"
        for name, limit in fields
    )


_ENTRY_METADATA = _metadata_projection((("id", 32), ("title", MAX_TITLE), ("suggested_name", MAX_TITLE),
                                       ("created_at", 40), ("model", MAX_TITLE), ("language", 80)))
_DRAFT_METADATA = _metadata_projection((("id", 32), ("title", MAX_TITLE), ("updated_at", 40)))


class HistoryError(Exception):
    """An actionable persistence error; existing transcripts remain untouched."""


@dataclass(frozen=True)
class Entry:
    id: str
    title: str
    text: str
    suggested_name: str
    created_at: str
    model: str
    language: str


@dataclass(frozen=True)
class Draft:
    id: str
    title: str
    text: str
    updated_at: str


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise HistoryError("The saved transcript identifier is invalid.")
    return value


def _label(value: str, limit: int, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise HistoryError(f"The {field} must be text.")
    value = value.strip()
    if (not value and not empty) or len(value) > limit:
        raise HistoryError(f"The {field} must contain {'0' if empty else '1'} to {limit} characters.")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise HistoryError(f"The {field} contains invalid text.") from error
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise HistoryError(f"The {field} cannot contain control characters.")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise HistoryError(f"The {field} must be a name, without a folder path.")
    return value


def _basename(value: str, limit: int, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise HistoryError(f"The {field} must be text.")
    # Treat either platform's path separators as separators, without touching
    # the source file or retaining its folder path in the database.
    return _label(value.replace("\\", "/").rsplit("/", 1)[-1], limit, field, empty=empty)


def _text(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise HistoryError("A transcript must contain text without binary NUL characters.")
    try:
        # The character check avoids encoding an already oversized string.
        if len(value) > MAX_TRANSCRIPT_BYTES or len(value.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
            raise HistoryError("This transcript is larger than the 8 MiB Editor limit.")
    except UnicodeError as error:
        raise HistoryError("This transcript contains invalid Unicode text.") from error
    return value


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise HistoryError("The saved transcript date is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError as error:
        raise HistoryError("The saved transcript date is invalid.") from error
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _is_link(metadata) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _check_components(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        if _is_link(metadata):
            raise HistoryError("History storage cannot contain symbolic links or junctions.")
        if component != path and not stat.S_ISDIR(metadata.st_mode):
            raise HistoryError("A History storage parent is not a directory.")


def _regular_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if _is_link(metadata) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise HistoryError("History files must be regular files without links.")


def _private_root(root: Path) -> Path:
    root = Path(root)
    if ".." in root.parts:
        raise HistoryError("History storage cannot contain parent-directory traversal.")
    root = root.absolute()
    try:
        relative = root.relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise HistoryError("History storage must stay inside the Mac project folder.") from error
    if not relative.parts:
        raise HistoryError("Choose a separate History folder inside the Mac project.")
    _check_components(root)
    current = PROJECT_ROOT
    for part in relative.parts:
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
        if _is_link(current.lstat()) or not current.is_dir():
            raise HistoryError("History storage must use regular directories without links.")
    root.chmod(0o700)
    return root


class HistoryStore:
    """Transactional history with one writer/reader app instance per folder.

    A disk instance owns a nonblocking lifetime OS lock. Keep it on the calling
    thread, and close it when the app exits. A crash releases the OS lock and
    SQLite's rollback journal preserves the last committed draft.
    """

    def __init__(self, root: Path | None = None):
        self.root = None
        self._connection = None
        self._lock_descriptor = None
        self._identities = {}
        try:
            if root is not None:
                self.root = _private_root(root)
                self._check_storage()
                self._acquire_lock()
                path = self.root / DATABASE_NAME
                # Precreate with private permissions; SQLite also inherits
                # this mode for rollback journals on POSIX.
                flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                descriptor = os.open(path, flags, 0o600)
                os.close(descriptor)
                _regular_file(path)
                path.chmod(0o600)
                self._connection = sqlite3.connect(path, timeout=0, isolation_level=None)
                for member in (self.root, path, self.root / "history.lock"):
                    metadata = member.stat()
                    self._identities[member] = (metadata.st_dev, metadata.st_ino)
            else:
                self._connection = sqlite3.connect(":memory:", isolation_level=None)
            self._connection.row_factory = sqlite3.Row
            self._connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_TRANSCRIPT_BYTES + 8192)
            self._connection.execute("PRAGMA trusted_schema=OFF")
            self._connection.execute("PRAGMA temp_store=MEMORY")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA journal_mode=DELETE")
            self._initialize_schema()
        except (OSError, sqlite3.Error, HistoryError) as error:
            self.close()
            if isinstance(error, HistoryError):
                raise
            raise HistoryError("Could not open local History. Check the History folder's permissions and available disk space.") from error

    def _acquire_lock(self) -> None:
        path = self.root / "history.lock"
        _regular_file(path)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
        self._lock_descriptor = descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise HistoryError("The History lock must be a regular private file.")
        try:
            if os.name == "nt":
                import msvcrt
                # A one-byte range can be locked even in an empty file.
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise HistoryError("History is already open in another Vidrick window. Close that app before opening this History folder again.") from error
        path.chmod(0o600)

    def _check_storage(self) -> None:
        if self.root is None:
            return
        _check_components(self.root)
        if not self.root.is_dir():
            raise HistoryError("The History folder is no longer available.")
        for name in (DATABASE_NAME, DATABASE_NAME + "-journal", DATABASE_NAME + "-wal", DATABASE_NAME + "-shm", "history.lock"):
            _regular_file(self.root / name)
        for path, identity in self._identities.items():
            try:
                metadata = path.stat()
            except FileNotFoundError as error:
                raise HistoryError("History storage changed while the app was open. Restart Vidrick before saving again.") from error
            if (metadata.st_dev, metadata.st_ino) != identity:
                raise HistoryError("History storage changed while the app was open. Restart Vidrick before saving again.")

    def _ready(self) -> sqlite3.Connection:
        if self._connection is None:
            raise HistoryError("History is closed. Reopen Vidrick to use saved transcripts.")
        try:
            self._check_storage()
        except OSError as error:
            raise HistoryError("Could not access the local History folder. Check its permissions.") from error
        return self._connection

    @contextmanager
    def _transaction(self):
        connection = self._ready()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException as error:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error as rollback_error:
                    raise HistoryError("Could not finish the History transaction safely. Keep the History folder and restart Vidrick before saving again.") from rollback_error
            if isinstance(error, sqlite3.Error):
                raise HistoryError("Could not save local History. Check available disk space and folder permissions; the previous saved data is unchanged.") from error
            raise

    def _initialize_schema(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise HistoryError("This History was created by a newer app. Open it with that version to keep your saved transcripts.")
        if version == 0:
            tables = self._connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables:
                raise HistoryError("The History database format is not recognized. Keep the file and choose another History folder.")
            with self._transaction() as connection:
                connection.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL, suggested_name TEXT NOT NULL, created_at TEXT NOT NULL, model TEXT NOT NULL, language TEXT NOT NULL)")
                connection.execute("CREATE TABLE drafts (id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL, updated_at TEXT NOT NULL)")
                connection.execute("CREATE INDEX entries_date ON entries(created_at DESC, id DESC)")
                connection.execute("CREATE INDEX drafts_date ON drafts(updated_at DESC, id DESC)")
                connection.execute("PRAGMA user_version=1")
        # Validate structure without loading transcript text.
        self._connection.execute("SELECT id,title,text,suggested_name,created_at,model,language FROM entries LIMIT 0")
        self._connection.execute("SELECT id,title,text,updated_at FROM drafts LIMIT 0")

    @staticmethod
    def _entry(row) -> Entry:
        return Entry(_identifier(row["id"]), _label(row["title"], MAX_TITLE, "title"), _text(row["text"]),
                     _label(row["suggested_name"], MAX_TITLE, "suggested filename"), _timestamp(row["created_at"]),
                     _label(row["model"], MAX_TITLE, "model", empty=True), _label(row["language"], 80, "language", empty=True))

    @staticmethod
    def _draft(row) -> Draft:
        return Draft(_identifier(row["id"]), _label(row["title"], MAX_TITLE, "title"), _text(row["text"]), _timestamp(row["updated_at"]))

    def _read(self, query: str, parameters=()):
        try:
            return self._ready().execute(query, parameters).fetchall()
        except sqlite3.Error as error:
            raise HistoryError("Could not read local History. Keep the database file for recovery and check its permissions.") from error

    def add_result(self, text: str, suggested_name: str, *, model: str = "", language: str = "") -> Entry:
        name = _basename(suggested_name, MAX_TITLE, "suggested filename")
        title = _label(Path(name).stem, MAX_TITLE, "title")
        entry = Entry(uuid.uuid4().hex, title, _text(text), name, _now(),
                      _basename(model, MAX_TITLE, "model", empty=True), _label(language, 80, "language", empty=True))
        with self._transaction() as connection:
            if connection.execute("SELECT COUNT(*) FROM entries").fetchone()[0] >= MAX_ENTRIES:
                raise HistoryError(f"History contains {MAX_ENTRIES} results. Delete some saved results before adding another.")
            connection.execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?)", tuple(entry.__dict__.values()))
        return entry

    def list_entries(self, query: str = "") -> list[Entry]:
        if not isinstance(query, str) or len(query) > MAX_TITLE or "\x00" in query:
            raise HistoryError("Search must contain up to 240 characters of text.")
        query = query.strip().casefold()
        # Metadata only: searching never materializes transcript bodies. Python
        # casefold gives reliable matching for non-ASCII recording names.
        rows = self._read(f"SELECT {_ENTRY_METADATA},'' AS text FROM entries ORDER BY created_at DESC,id DESC LIMIT ?", (MAX_ENTRIES + 1,))
        if len(rows) > MAX_ENTRIES:
            raise HistoryError("History has too many results to open safely.")
        entries = [self._entry(row) for row in rows]
        return [entry for entry in entries if not query or any(query in value.casefold() for value in (entry.title, entry.suggested_name, entry.model))]

    def get_entry(self, identifier: str) -> Entry:
        rows = self._read(f"SELECT {_ENTRY_METADATA},text FROM entries WHERE id=?", (_identifier(identifier),))
        if not rows:
            raise HistoryError("This saved transcript is no longer in History.")
        return self._entry(rows[0])

    def rename_entry(self, identifier: str, title: str) -> None:
        identifier, title = _identifier(identifier), _label(title, MAX_TITLE, "title")
        with self._transaction() as connection:
            if connection.execute("UPDATE entries SET title=? WHERE id=?", (title, identifier)).rowcount != 1:
                raise HistoryError("This saved transcript is no longer in History.")

    def delete_entry(self, identifier: str) -> None:
        with self._transaction() as connection:
            connection.execute("DELETE FROM entries WHERE id=?", (_identifier(identifier),))

    def save_draft(self, identifier: str, text: str, title: str) -> Draft:
        draft = Draft(_identifier(identifier), _label(title, MAX_TITLE, "title"), _text(text), _now())
        with self._transaction() as connection:
            exists = connection.execute("SELECT 1 FROM drafts WHERE id=?", (draft.id,)).fetchone()
            if not exists and connection.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] >= MAX_DRAFTS:
                raise HistoryError(f"There are {MAX_DRAFTS} recovery drafts. Recover or discard older drafts before saving another.")
            connection.execute("INSERT INTO drafts VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,text=excluded.text,updated_at=excluded.updated_at", tuple(draft.__dict__.values()))
        return draft

    def list_drafts(self) -> list[Draft]:
        rows = self._read(f"SELECT {_DRAFT_METADATA},'' AS text FROM drafts ORDER BY updated_at DESC,id DESC LIMIT ?", (MAX_DRAFTS + 1,))
        if len(rows) > MAX_DRAFTS:
            raise HistoryError("There are too many recovery drafts to open safely.")
        return [self._draft(row) for row in rows]

    def get_draft(self, identifier: str) -> Draft:
        rows = self._read(f"SELECT {_DRAFT_METADATA},text FROM drafts WHERE id=?", (_identifier(identifier),))
        if not rows:
            raise HistoryError("This recovery draft is no longer available.")
        return self._draft(rows[0])

    def delete_draft(self, identifier: str) -> None:
        with self._transaction() as connection:
            connection.execute("DELETE FROM drafts WHERE id=?", (_identifier(identifier),))

    def close(self) -> None:
        connection, self._connection = self._connection, None
        try:
            if connection is not None:
                connection.close()
        finally:
            descriptor, self._lock_descriptor = self._lock_descriptor, None
            if descriptor is not None:
                # Closing the descriptor releases both Windows and POSIX locks.
                os.close(descriptor)

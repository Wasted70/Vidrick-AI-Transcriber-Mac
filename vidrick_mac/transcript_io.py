"""Bounded, local-only transcript reads and atomic saves for the Editor."""

from dataclasses import dataclass
import codecs
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile


MAX_TRANSCRIPT_BYTES = 8 * 1024 * 1024


class TranscriptError(Exception):
    """A selected transcript could not be opened or saved safely."""


class TranscriptConflict(TranscriptError):
    """The file on disk no longer matches the Editor's last open or save."""


@dataclass(frozen=True)
class LoadedTranscript:
    path: Path
    text: str
    encoding: str
    newline: str
    fingerprint: str


def _target(path: str | os.PathLike[str]) -> Path:
    target = Path(os.path.abspath(os.fspath(path)))
    if target.suffix.lower() != ".txt":
        raise TranscriptError("Choose a transcript with a .txt extension.")
    return target


def _fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path) -> bytes:
    message = "Choose a regular .txt transcript file; folders and special files cannot be opened."
    if not stat.S_ISREG(path.stat().st_mode):
        raise TranscriptError(message)
    # Nonblocking open prevents a FIFO swapped in after stat from waiting for a
    # writer on macOS. Check the opened descriptor before reading any bytes.
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TranscriptError(message)
        stream = os.fdopen(descriptor, "rb")
        descriptor = None
        with stream:
            return stream.read(MAX_TRANSCRIPT_BYTES + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _newline(text: str) -> str:
    crlf = text.count("\r\n")
    counts = {"\r\n": crlf, "\n": text.count("\n") - crlf,
              "\r": text.count("\r") - crlf}
    # Stable ties prefer the first actual line ending in the source.
    positions = {}
    for ending, pattern in (("\r\n", r"\r\n"), ("\n", r"(?<!\r)\n"), ("\r", r"\r(?!\n)")):
        if counts[ending]:
            positions[ending] = re.search(pattern, text).start()
    if not positions:
        return "\n"
    return max(positions, key=lambda ending: (counts[ending], -positions[ending]))


def read_transcript(path: str | os.PathLike[str]) -> LoadedTranscript:
    """Read only the chosen .txt file, remembering its byte format for saving."""
    target = _target(path)
    try:
        data = _read_bytes(target)
    except OSError as error:
        raise TranscriptError(f"Could not open {target.name}: {error.strerror or error}") from error
    if len(data) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptError("This transcript is larger than the 8 MiB Editor limit.")

    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise TranscriptError("UTF-32 transcripts are not supported. Save a UTF-8 .txt copy first.")
    if data.startswith(codecs.BOM_UTF8):
        encoding, content = "utf-8-sig", data[len(codecs.BOM_UTF8):]
        decoder = "utf-8"
    elif data.startswith(codecs.BOM_UTF16_LE):
        encoding, content, decoder = "utf-16-le", data[2:], "utf-16-le"
    elif data.startswith(codecs.BOM_UTF16_BE):
        encoding, content, decoder = "utf-16-be", data[2:], "utf-16-be"
    else:
        encoding, content, decoder = "utf-8", data, "utf-8"
    try:
        text = content.decode(decoder)
    except UnicodeError as error:
        raise TranscriptError("This file is not valid UTF-8 or BOM-marked UTF-16 text. "
                              "Save a UTF-8 .txt copy first.") from error
    if "\x00" in text:
        raise TranscriptError("This file contains binary NUL characters and cannot be opened as a transcript.")
    return LoadedTranscript(target, _normalize(text), encoding, _newline(text), _fingerprint(data))


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def _check_unchanged(target: Path, previous: LoadedTranscript) -> None:
    try:
        data = _read_bytes(target)
    except FileNotFoundError as error:
        raise TranscriptConflict("This transcript was moved or deleted since you opened or saved it.") from error
    if len(data) > MAX_TRANSCRIPT_BYTES or _fingerprint(data) != previous.fingerprint:
        raise TranscriptConflict("This transcript changed on disk since you opened or saved it.")


def write_transcript(path: str | os.PathLike[str], text: str, *,
                     previous: LoadedTranscript | None = None,
                     overwrite_changed: bool = False) -> LoadedTranscript:
    """Atomically save one .txt file; detect edits to a previously opened target."""
    target = _target(path)
    normalized = _normalize(text)
    if "\x00" in normalized:
        raise TranscriptError("Remove binary NUL characters before saving this transcript.")
    if len(normalized) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptError("This transcript is larger than the 8 MiB Editor limit.")
    encoding = previous.encoding if previous else "utf-8"
    newline = previous.newline if previous else "\n"
    if encoding not in {"utf-8", "utf-8-sig", "utf-16-le", "utf-16-be"} or newline not in {"\n", "\r\n", "\r"}:
        raise TranscriptError("This transcript's text format is not supported.")
    try:
        data = normalized.replace("\n", newline).encode(encoding)
    except UnicodeError as error:
        raise TranscriptError("This transcript contains characters that cannot be saved as Unicode text.") from error
    if encoding == "utf-16-le":
        data = codecs.BOM_UTF16_LE + data
    elif encoding == "utf-16-be":
        data = codecs.BOM_UTF16_BE + data
    if len(data) > MAX_TRANSCRIPT_BYTES:
        raise TranscriptError("This transcript is larger than the 8 MiB Editor limit.")

    check_previous = previous is not None and _same_path(target, previous.path) and not overwrite_changed
    temporary = None
    try:
        if check_previous:
            _check_unchanged(target, previous)
        try:
            target_stat = target.lstat()
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
            raise TranscriptError("Choose a regular .txt file to save to; folders and symbolic links cannot be replaced.")
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{target.name}.", suffix=".tmp",
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if target_stat is not None:
            os.chmod(temporary, stat.S_IMODE(target_stat.st_mode))
        # Recheck after staging, so a change made during encoding/writing is caught.
        if check_previous:
            _check_unchanged(target, previous)
        os.replace(temporary, target)
        temporary = None
    except OSError as error:
        raise TranscriptError(f"Could not save {target.name}: {error.strerror or error}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # A copied read-only bit can prevent cleanup on Windows.
                try:
                    os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return LoadedTranscript(target, normalized, encoding, newline, _fingerprint(data))

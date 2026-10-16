"""Bounded, verified setup downloads and private project-local storage.

This module does no work on import. Callers explicitly start a setup job and own
the platform guard; tests supply synthetic responses and private project roots.
"""

from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import stat
import tarfile
import tempfile
from threading import Event
from typing import Callable
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


SETUP_ROOT = Path(__file__).resolve().parents[1] / "local-setup"
CHUNK_BYTES = 64 * 1024
NETWORK_TIMEOUT = 10
SELECTION_LIMIT = 32 * 1024
SELECTION_KEYS = frozenset({"engine", "model", "converter", "model_id"})
DOWNLOAD_HOSTS = frozenset({
    "github.com", "codeload.github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "huggingface.co", "hf.co",
    "cmake.org", "www.cmake.org", "ffmpeg.org", "www.ffmpeg.org",
})


class SetupError(Exception):
    """An actionable setup failure, without exposing response URLs or tokens."""


class SetupCancelled(SetupError):
    """The user cancelled before an artifact was committed."""


class SetupCleanupError(SetupError):
    """Incomplete files or a setup process could not be confirmed cleaned up.

    Cancellation handlers must preserve this failure instead of reporting a
    clean cancellation. It is shared by storage and process helpers.
    """


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class SetupProgress:
    stage: str
    message: str
    percent: int | None = None

    def __post_init__(self):
        if self.percent is not None and (type(self.percent) is not int or not 0 <= self.percent <= 100):
            raise ValueError("Progress must be an integer from 0 to 100, or unknown.")


Emit = Callable[[SetupProgress], None]


def _check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise SetupCancelled("Setup cancelled.")


def _is_link(metadata) -> bool:
    # Junctions and other Windows reparse points must not redirect test writes.
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
            raise SetupError("Setup storage cannot contain symbolic links or junctions.")
        if component != path and not stat.S_ISDIR(metadata.st_mode):
            raise SetupError("A setup storage parent is not a directory.")


def _managed_path(path: Path, root: Path) -> Path:
    path, root = Path(path), Path(root)
    if ".." in path.parts or ".." in root.parts:
        raise SetupError("Setup paths cannot contain parent-directory traversal.")
    path, root = path.absolute(), root.absolute()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise SetupError("Setup files must stay inside the selected setup folder.") from error
    _check_components(path)
    return path


def _private_directory(path: Path, root: Path) -> Path:
    path = _managed_path(path, root)
    root = Path(root).absolute()
    if not root.parent.is_dir():
        raise SetupError("The setup folder's parent must already exist.")
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        if current == root:
            break
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
    _check_components(path)
    if not path.is_dir():
        raise SetupError("Setup storage must be a directory.")
    return path


def _regular_file(path: Path):
    metadata = path.lstat()
    if _is_link(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise SetupError("Setup files must be regular files, without symbolic links.")
    return metadata


def _open_read(path: Path):
    _regular_file(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SetupError("Setup files must be regular files.")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _remove_partial(path: Path, root: Path) -> None:
    try:
        _managed_path(path, root)
        path.unlink(missing_ok=True)
    except (OSError, SetupError) as error:
        raise SetupCleanupError(
            f"Could not remove the incomplete setup file {path}. "
            "Check that file before trying setup again."
        ) from error


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        valid_host = hostname in DOWNLOAD_HOSTS or hostname.endswith(".hf.co") or hostname.endswith(".huggingface.co")
        valid = (parsed.scheme == "https" and valid_host and parsed.port in (None, 443)
                 and parsed.username is None and parsed.password is None and not parsed.fragment)
    except (ValueError, TypeError) as error:
        raise SetupError("The setup download address is invalid.") from error
    if not valid:
        raise SetupError("Setup downloads require HTTPS from an approved source.")


class _VerifiedRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def _validate_artifact(artifact: Artifact) -> None:
    if (not isinstance(artifact.name, str) or not artifact.name or len(artifact.name) > 200
            or any(ord(character) < 32 for character in artifact.name)
            or type(artifact.size) is not int or artifact.size <= 0
            or not isinstance(artifact.sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", artifact.sha256)):
        raise SetupError("The setup artifact is missing its pinned size or SHA-256 checksum.")
    _validate_url(artifact.url)


def _verified_existing(path: Path, artifact: Artifact, cancel: Event, emit: Emit) -> bool:
    try:
        metadata = _regular_file(path)
    except FileNotFoundError:
        return False
    if metadata.st_size != artifact.size:
        return False
    digest = hashlib.sha256()
    count = 0
    with _open_read(path) as stream:
        while True:
            _check_cancel(cancel)
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            count += len(chunk)
            if count > artifact.size:
                return False
            digest.update(chunk)
            emit(SetupProgress("verifying", f"Checking {artifact.name}", min(99, count * 100 // artifact.size)))
    _check_cancel(cancel)
    return count == artifact.size and digest.hexdigest().lower() == artifact.sha256.lower()


def download_artifact(artifact: Artifact, destination: Path, cancel: Event, emit: Emit,
                      *, root: Path = SETUP_ROOT) -> Path:
    """Fetch a pinned artifact with verified TLS, bounded size, and atomic commit.

    Redirects are checked before following them. An older file survives any
    failed or cancelled replacement; only a verified new file becomes visible.
    """
    temporary = None
    try:
        _check_cancel(cancel)
        _validate_artifact(artifact)
        destination = _managed_path(destination, root)
        if destination == Path(root).absolute():
            raise SetupError("An artifact must have its own file name.")
        if _verified_existing(destination, artifact, cancel, emit):
            emit(SetupProgress("verified", f"{artifact.name} is ready", 100))
            return destination
        _private_directory(destination.parent, root)
        digest = hashlib.sha256()
        count = 0
        emit(SetupProgress("downloading", f"Downloading {artifact.name}", 0))
        opener = build_opener(_VerifiedRedirects(), HTTPSHandler(context=ssl.create_default_context()))
        request = Request(artifact.url, headers={"User-Agent": "Vidrick-Mac-Development-Setup/1", "Accept-Encoding": "identity"})
        _check_cancel(cancel)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as output:
            _check_cancel(cancel)
            with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
                _validate_url(response.geturl())
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        length = int(length)
                    except (ValueError, TypeError) as error:
                        raise SetupError("The download has an invalid size header.") from error
                    if length != artifact.size:
                        raise SetupError("The download size differs from the pinned artifact.")
                encoding = response.headers.get("Content-Encoding", "identity")
                if encoding.lower() != "identity":
                    raise SetupError("The server returned an unexpected compressed download.")
                read_chunk = getattr(response, "read1", response.read)
                while True:
                    _check_cancel(cancel)
                    # read1 performs at most one buffered/socket read. A server
                    # that drips bytes cannot keep a large read from returning
                    # indefinitely and delay the next cancellation check.
                    chunk = read_chunk(min(CHUNK_BYTES, artifact.size - count + 1))
                    _check_cancel(cancel)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > artifact.size:
                        raise SetupError("The download exceeds its pinned size.")
                    output.write(chunk)
                    digest.update(chunk)
                    emit(SetupProgress("downloading", f"Downloading {artifact.name}", min(99, count * 100 // artifact.size)))
            if count != artifact.size:
                raise SetupError("The download ended before its pinned size was reached.")
            emit(SetupProgress("verifying", f"Checking {artifact.name}", 99))
            if digest.hexdigest().lower() != artifact.sha256.lower():
                raise SetupError("The download failed its SHA-256 checksum. Please try setup again.")
            output.flush()
            os.fsync(output.fileno())
        _check_cancel(cancel)
        _managed_path(destination, root)
        if destination.exists():
            _regular_file(destination)
        os.replace(temporary, destination)
        temporary = None
        emit(SetupProgress("verified", f"{artifact.name} is ready", 100))
        return destination
    except SetupError:
        raise
    except (OSError, URLError, http.client.HTTPException) as error:
        raise SetupError("The setup download could not finish. Check your connection and available storage, then retry.") from error
    finally:
        if temporary is not None:
            _remove_partial(temporary, root)


def _archive_path(name: str) -> Path:
    if (not name or len(name) > 1024 or "\\" in name or "\0" in name or name.startswith("/")
            or any(part == ".." or ":" in part for part in name.split("/"))):
        raise SetupError("The setup archive contains an unsafe path.")
    relative = PurePosixPath(name)
    if not relative.parts or relative.is_absolute():
        raise SetupError("The setup archive contains an unsafe path.")
    return Path(*relative.parts)


def safe_extract_tar(archive: Path, destination: Path, cancel: Event, *, root: Path = SETUP_ROOT,
                     max_bytes: int = 1024 * 1024 * 1024, max_files: int = 100_000,
                     include: Callable[[str], bool] | None = None) -> Path:
    """Extract only ordinary files/directories, with no links or special entries.

    The destination must be absent or empty. Selected member names and limits
    are checked before writing. A failed extraction removes only its own files.
    ``include`` allows a pinned bundle's unrelated GUI/link entries to be
    omitted; it does not weaken validation for any selected member.
    """
    created: list[Path] = []
    try:
        _check_cancel(cancel)
        destination = _managed_path(destination, root)
        if destination == Path(root).absolute():
            raise SetupError("Extract into a dedicated setup subfolder.")
        if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
            raise SetupError("The setup extraction folder must be empty.")
        if type(max_bytes) is not int or max_bytes < 0 or type(max_files) is not int or max_files < 1:
            raise SetupError("Archive limits must be positive integers.")
        with _open_read(Path(archive)) as archive_stream, tarfile.open(fileobj=archive_stream, mode="r:*") as source:
            members = []
            names = {}
            total = 0
            for member_number, member in enumerate(source, 1):
                _check_cancel(cancel)
                if member_number > max_files:
                    raise SetupError("The setup archive exceeds its extraction limits.")
                if include is not None and not include(member.name):
                    continue
                relative = _archive_path(member.name)
                if not (member.isfile() or member.isdir()):
                    raise SetupError("The setup archive contains links or special files.")
                if member.size < 0 or (member.isdir() and member.size):
                    raise SetupError("The setup archive contains an invalid file size.")
                key = relative.as_posix().casefold()
                if key in names:
                    raise SetupError("The setup archive contains duplicate file paths.")
                names[key] = member.isdir()
                total += member.size
                if len(names) > max_files or total > max_bytes:
                    raise SetupError("The setup archive exceeds its extraction limits.")
                members.append((member, relative))
            for key in names:
                parts = key.split("/")
                if any(names.get("/".join(parts[:index])) is False for index in range(1, len(parts))):
                    raise SetupError("The setup archive has a file where a directory is required.")
            _private_directory(destination.parent, root)
            if not destination.exists():
                destination.mkdir(mode=0o700)
                created.append(destination)
            for member, relative in members:
                _check_cancel(cancel)
                target = _managed_path(destination / relative, root)
                directories = target.parents if member.isfile() else (target, *target.parents)
                missing = []
                for directory in directories:
                    if directory == destination:
                        break
                    if not directory.exists():
                        missing.append(directory)
                for directory in reversed(missing):
                    directory.mkdir(mode=0o700)
                    created.append(directory)
                if member.isdir():
                    continue
                with source.extractfile(member) as input_stream, target.open("xb") as output:
                    created.append(target)
                    remaining = member.size
                    while remaining:
                        _check_cancel(cancel)
                        chunk = input_stream.read(min(CHUNK_BYTES, remaining))
                        if not chunk:
                            raise SetupError("The setup archive contains an incomplete file.")
                        output.write(chunk)
                        remaining -= len(chunk)
                    output.flush()
                target.chmod(0o700 if member.mode & 0o111 else 0o600)
            _check_cancel(cancel)
        return destination
    except BaseException as error:
        cleanup_error = None
        for path in reversed(created):
            try:
                _managed_path(path, root)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink(missing_ok=True)
            except (OSError, SetupError) as failure:
                cleanup_error = cleanup_error or failure
        if cleanup_error is not None:
            raise SetupCleanupError(
                f"Could not remove incomplete setup files from {destination}. "
                "Check that folder before trying setup again."
            ) from cleanup_error
        if isinstance(error, (OSError, tarfile.TarError, EOFError)):
            raise SetupError("The setup archive could not be extracted safely.") from error
        raise


def _validate_selection(selection: dict) -> dict[str, str]:
    if not isinstance(selection, dict) or set(selection) - SELECTION_KEYS:
        raise SetupError("Saved setup settings contain unrecognized fields.")
    result = {}
    for key, value in selection.items():
        if not isinstance(value, str) or len(value) > 4096 or "\0" in value or "\n" in value or "\r" in value:
            raise SetupError("Saved setup settings contain an invalid value.")
        if key != "model_id" and value and not Path(value).is_absolute():
            raise SetupError("Saved tool and model paths must be absolute.")
        result[key] = value
    return result


def load_selection(root: Path = SETUP_ROOT) -> dict[str, str]:
    """Load optional preferences without creating folders or repairing files."""
    try:
        path = _managed_path(Path(root) / "selection.json", root)
        if _regular_file(path).st_size > SELECTION_LIMIT:
            return {}
        with _open_read(path) as stream:
            raw = stream.read(SELECTION_LIMIT + 1)
        if len(raw) > SELECTION_LIMIT:
            return {}
        return _validate_selection(json.loads(raw.decode("utf-8")))
    except (SetupError, OSError, UnicodeError, ValueError, RecursionError):
        return {}


def save_selection(selection: dict[str, str], root: Path = SETUP_ROOT) -> None:
    """Atomically store validated setup paths; never open the selected tools."""
    temporary = None
    try:
        payload = (json.dumps(_validate_selection(selection), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(payload) > SELECTION_LIMIT:
            raise SetupError("Saved setup settings are too large.")
        folder = _private_directory(Path(root), root)
        path = _managed_path(folder / "selection.json", root)
        if path.exists():
            _regular_file(path)
        descriptor, name = tempfile.mkstemp(prefix=".selection.", suffix=".part", dir=folder)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _managed_path(path, root)
        if path.exists():
            _regular_file(path)
        os.replace(temporary, path)
        temporary = None
    except SetupError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError) as error:
        raise SetupError("The setup choices could not be saved. Check available storage and retry.") from error
    finally:
        if temporary is not None:
            _remove_partial(temporary, root)

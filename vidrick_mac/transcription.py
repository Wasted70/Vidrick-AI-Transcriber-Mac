"""Local, cancellable whisper.cpp integration for the Mac development build.

No engine or model is downloaded. The caller supplies installed executables and
a local model. The Windows UI preview cannot execute this backend.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
from typing import Callable
import wave

from .transcript_io import MAX_TRANSCRIPT_BYTES


RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "runtime-output"
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 100_000
POLL_SECONDS = 0.08
STOP_GRACE_SECONDS = 1.0
LANGUAGES = frozenset({"auto", "en", "es", "fr", "de", "pt", "it", "ja"})
# Force one ordinary media demuxer. A renamed playlist cannot select a network
# or concatenation demuxer. The protocol whitelist also denies network access.
MEDIA_FORMATS = {
    ".wav": "wav", ".mp3": "mp3", ".m4a": "mov", ".mp4": "mov",
    ".mov": "mov", ".flac": "flac", ".ogg": "ogg", ".aac": "aac",
    ".wma": "asf", ".webm": "matroska", ".mkv": "matroska",
    ".aif": "aiff", ".aiff": "aiff",
}
_PROGRESS = re.compile(r"^whisper_print_progress_callback: progress =\s*(\d{1,3})%\s*$")


class TranscriptionError(Exception):
    """The selected recording or local engine could not produce a transcript."""


class TranscriptionCleanupError(TranscriptionError):
    """Stopping a child or cleaning its files failed; cancellation cannot hide it."""

    def __init__(self, message: str, *, preserve_job_files: bool = True):
        super().__init__(message)
        self.preserve_job_files = preserve_job_files


class TranscriptionCancelled(Exception):
    """The user stopped the job before its result was accepted."""


@dataclass(frozen=True)
class JobSpec:
    recording: str
    engine: str
    model: str
    converter: str = ""
    language: str = "auto"
    translate: bool = False
    timestamps: bool = True


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    suggested_name: str
    segment_count: int
    language: str = ""


@dataclass(frozen=True)
class Progress:
    stage: str
    message: str
    percent: int | None = None


def _check_cancelled(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise TranscriptionCancelled()


def _require_native_mac() -> None:
    if platform.system() != "Darwin" or platform.machine().lower() != "arm64":
        raise TranscriptionError("Transcription runs only on an Apple Silicon Mac. "
                                 "The Windows development preview cannot transcribe.")


def _regular_path(value: str, label: str, *, executable: bool = False) -> Path:
    if not value or "\x00" in value:
        raise TranscriptionError(f"Choose a local {label} file.")
    path = Path(value)
    if not path.is_absolute() or value.startswith(("//", "\\\\")):
        raise TranscriptionError(f"Choose an absolute local path for the {label}.")
    try:
        path = path.resolve(strict=True)
        if not stat.S_ISREG(path.stat().st_mode):
            raise TranscriptionError(f"The {label} must be a regular local file.")
        if executable and not os.access(path, os.X_OK):
            raise TranscriptionError(f"The selected {label} is not executable.")
    except (OSError, RuntimeError) as error:
        raise TranscriptionError(f"Could not access the {label}: {error}") from error
    return path


def _pcm_wav(path: Path) -> bool:
    """Check the format header without loading the recording into Python."""
    try:
        with wave.open(str(path), "rb") as audio:
            return (audio.getcomptype() == "NONE" and audio.getnchannels() == 1
                    and audio.getsampwidth() == 2 and audio.getframerate() == 16000
                    and audio.getnframes() > 0)
    except (OSError, EOFError, wave.Error):
        return False


def _stop_process(process: subprocess.Popen) -> None:
    """Stop the private Mac process group, then reap the direct child."""
    try:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif process.poll() is None:  # Synthetic subprocess tests on Windows only.
            process.terminate()
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        # A child can outlive a process-group leader that already exited.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise TranscriptionCleanupError("The local engine did not stop after cancellation.") from error
    except OSError as error:
        raise TranscriptionCleanupError(f"Could not confirm that the local engine stopped: {error}") from error


@contextmanager
def _job_directory():
    """Clean a finished job, retaining evidence if a child might still be alive."""
    directory = Path(tempfile.mkdtemp(prefix="job-", dir=RUNTIME_ROOT))
    preserve = False
    try:
        yield directory
    except TranscriptionCleanupError as error:
        preserve = error.preserve_job_files
        raise
    finally:
        if not preserve:
            try:
                shutil.rmtree(directory)
            except OSError as error:
                raise TranscriptionCleanupError(
                    f"Temporary job files could not be removed from {directory}: {error}"
                ) from error


def _diagnostic(tail: bytes) -> str:
    # Bound the dialog and omit terminal controls. Engine output is data.
    decoded = tail.decode("utf-8", errors="replace")
    decoded = "".join(char for char in decoded if char in "\n\t" or char.isprintable())
    return decoded.strip()[-1600:]


def _run_process(command: list[str], directory: Path, log_name: str,
                 cancel: threading.Event, emit: Callable[[Progress], None], *,
                 label: str, report_progress: bool = False) -> None:
    """Poll a disk-backed diagnostic stream; no pipes or reader threads remain.

    whisper.cpp v1.9.5's CLI callback is the only source of percentages. Loading
    and conversion stay indeterminate. A successful child still needs valid JSON.
    """
    _check_cancelled(cancel)
    log_path = directory / log_name
    tail = b""
    pending = b""
    last_percent = -1
    process = None
    try:
        with log_path.open("wb") as output, log_path.open("rb") as reader:
            environment = os.environ.copy()
            # FFREPORT can redirect a converter's report to an arbitrary path.
            # Diagnostics for these jobs belong only in their temporary folder.
            environment.pop("FFREPORT", None)
            environment["AV_LOG_FORCE_NOCOLOR"] = "1"
            process = subprocess.Popen(
                command, cwd=directory, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=output, shell=False,
                start_new_session=(os.name == "posix"),
                env=environment,
            )
            while True:
                _check_cancelled(cancel)
                if os.fstat(reader.fileno()).st_size > MAX_LOG_BYTES:
                    raise TranscriptionError(f"{label} produced too much diagnostic output.")
                chunk = reader.read(64 * 1024)
                if chunk:
                    tail = (tail + chunk)[-8192:]
                    pending += chunk
                    lines = pending.split(b"\n")
                    pending = lines.pop()[-4096:]
                    if report_progress:
                        for line in lines:
                            match = _PROGRESS.fullmatch(line.decode("ascii", errors="replace"))
                            if match:
                                value = int(match.group(1))
                                if 0 <= value <= 100:
                                    percent = min(value, 99)
                                    if percent > last_percent:
                                        last_percent = percent
                                        emit(Progress("transcribing", "Transcribing recording…", percent))
                elif process.poll() is not None:
                    break
                # Catch up on an existing chunk before sleeping, without leaving
                # cancellation dependent on a noisy child's output rate.
                if not chunk:
                    cancel.wait(POLL_SECONDS)
            _check_cancelled(cancel)
            if process.returncode:
                details = _diagnostic(tail)
                message = f"{label} failed (exit {process.returncode})."
                if details:
                    message += "\n\n" + details
                raise TranscriptionError(message)
    except OSError as error:
        raise TranscriptionError(f"Could not run {label}: {error}") from error
    finally:
        if process is not None:
            # Also remove unexpected descendants after a normally exited leader.
            try:
                _stop_process(process)
            except TranscriptionCleanupError as error:
                raise TranscriptionCleanupError(
                    f"{error}\n\nJob files were kept in {directory}. "
                    "Close the local engine before removing them."
                ) from error


def _milliseconds(value: int) -> str:
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{millis:03}"


def _read_result(path: Path, spec: JobSpec) -> TranscriptResult:
    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise TranscriptionError("The engine did not create a regular transcript result.")
        with path.open("rb") as stream:
            data = stream.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise TranscriptionError("The engine result is too large to open safely.")
        result = json.loads(data.decode("utf-8"))
    except FileNotFoundError as error:
        raise TranscriptionError("The engine finished without a transcript. "
                                 "Choose a compatible whisper-cli executable and model.") from error
    except (OSError, ValueError, UnicodeError, RecursionError) as error:
        raise TranscriptionError("The engine produced an unreadable transcript result.") from error
    if not isinstance(result, dict) or not isinstance(result.get("transcription"), list):
        raise TranscriptionError("The engine result does not contain transcript segments.")
    segments = result["transcription"]
    if len(segments) > MAX_SEGMENTS:
        raise TranscriptionError("The engine result contains too many transcript segments.")
    lines = []
    size = 0
    count = 0
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("text"), str):
            raise TranscriptionError("The engine returned an invalid transcript segment.")
        text = segment["text"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if "\x00" in text:
            raise TranscriptionError("The engine result contains binary characters.")
        offsets = segment.get("offsets")
        if not isinstance(offsets, dict):
            raise TranscriptionError("The engine returned invalid segment timing.")
        start, end = offsets.get("from"), offsets.get("to")
        if (type(start) is not int or type(end) is not int
                or not 0 <= start <= end <= 999_999_999_999):
            raise TranscriptionError("The engine returned invalid segment timing.")
        if not text:
            continue
        line = f"[{_milliseconds(start)} --> {_milliseconds(end)}] {text}" if spec.timestamps else text
        try:
            size += len(line.encode("utf-8")) + 1
        except UnicodeError as error:
            raise TranscriptionError("The engine result contains invalid Unicode text.") from error
        if size > MAX_TRANSCRIPT_BYTES:
            raise TranscriptionError("The finished transcript exceeds the 8 MiB Editor limit.")
        lines.append(line)
        count += 1
    metadata = result.get("result", {})
    language = metadata.get("language", "") if isinstance(metadata, dict) else ""
    if not isinstance(language, str) or len(language) > 32:
        language = ""
    text = "\n".join(lines) + ("\n" if lines else "")
    return TranscriptResult(text, Path(spec.recording).stem + ".txt", count, language)


def run_transcription(spec: JobSpec, cancel_event: threading.Event,
                      emit: Callable[[Progress], None]) -> TranscriptResult:
    """Produce one local transcript, cleaning all temporary job files afterward."""
    _check_cancelled(cancel_event)
    _require_native_mac()
    emit(Progress("validating", "Checking recording and local engine…"))
    if spec.language not in LANGUAGES:
        raise TranscriptionError("Choose one of the supported languages or Auto-detect.")
    recording = _regular_path(spec.recording, "recording")
    if recording.suffix.lower() not in MEDIA_FORMATS:
        raise TranscriptionError("This recording format is not supported. Choose WAV, MP3, M4A, "
                                 "MP4, MOV, FLAC, OGG, AAC, WMA, WebM, MKV, or AIFF.")
    engine = _regular_path(spec.engine, "whisper-cli executable", executable=True)
    model = _regular_path(spec.model, "Whisper model")
    direct_wav = recording.suffix.lower() == ".wav" and _pcm_wav(recording)
    converter = None
    if not direct_wav:
        if not spec.converter:
            raise TranscriptionError("This recording needs conversion. Choose a local ffmpeg executable, "
                                     "or use a 16-bit, mono, 16 kHz PCM WAV recording.")
        converter = _regular_path(spec.converter, "ffmpeg executable", executable=True)
    _check_cancelled(cancel_event)
    try:
        RUNTIME_ROOT.mkdir(mode=0o700, parents=False, exist_ok=True)
        if RUNTIME_ROOT.is_symlink() or not RUNTIME_ROOT.is_dir():
            raise TranscriptionError("The local runtime-output folder must be a regular directory.")
        with _job_directory() as directory:
            audio = recording
            if converter is not None:
                emit(Progress("preparing", "Preparing audio…"))
                audio = directory / "recording.wav"
                command = [str(converter), "-nostdin", "-hide_banner", "-loglevel", "error",
                           "-protocol_whitelist", "file,pipe", "-f", MEDIA_FORMATS[recording.suffix.lower()],
                           "-i", str(recording), "-map", "0:a:0", "-vn", "-sn", "-dn",
                           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(audio)]
                _run_process(command, directory, "conversion.log", cancel_event, emit, label="Audio conversion")
                if not _pcm_wav(audio):
                    raise TranscriptionError("Audio conversion did not produce a usable recording.")
            _check_cancelled(cancel_event)
            emit(Progress("loading", "Loading local model and preparing transcription…"))
            output_base = directory / "transcript"
            command = [str(engine), "-m", str(model), "-f", str(audio), "-l", spec.language,
                       "-oj", "-of", str(output_base), "-pp", "-ng"]
            if spec.translate:
                command.append("-tr")
            _run_process(command, directory, "engine.log", cancel_event, emit,
                         label="Transcription", report_progress=True)
            _check_cancelled(cancel_event)
            emit(Progress("finishing", "Preparing finished transcript…"))
            result = _read_result(output_base.with_suffix(".json"), spec)
            _check_cancelled(cancel_event)
        # Cleanup is part of completing the job. A final cancellation wins.
        _check_cancelled(cancel_event)
        emit(Progress("finished", "Transcript ready." if result.text else "No speech was found.", 100))
        return result
    except OSError as error:
        raise TranscriptionError(f"Could not prepare or clean up the local job files: {error}") from error

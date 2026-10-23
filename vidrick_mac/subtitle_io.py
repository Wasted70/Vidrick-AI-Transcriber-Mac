"""Timed transcript conversion and bounded, atomic SRT/WebVTT exports.

Formats follow https://www.w3.org/TR/webvtt1/ and the SubRip description at
https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml.
Editor text is literal caption text, not subtitle markup.
"""

from dataclasses import dataclass
import html
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable

from .transcript_io import MAX_TRANSCRIPT_BYTES


MAX_CUES = 100_000
MAX_TIMESTAMP_MS = 999_999_999_999
MAX_SUBTITLE_BYTES = 16 * 1024 * 1024
_STAMP = r"([0-9]{2,9}):([0-9]{2}):([0-9]{2})\.([0-9]{3})"
_MARKER = re.compile(r"^[ \t]*\[" + _STAMP + r"[ \t]+-->[ \t]+" + _STAMP + r"\](?:[ \t]*(.*))?$")


class SubtitleError(Exception):
    """The current text or selected export destination is not usable."""


@dataclass(frozen=True)
class SubtitleCue:
    start_ms: int
    end_ms: int
    text: str


def _text(text: str) -> str:
    if not isinstance(text, str):
        raise SubtitleError("Subtitle text must be Unicode text.")
    if len(text) > MAX_TRANSCRIPT_BYTES:
        raise SubtitleError("Subtitle text is larger than the 8 MiB Editor limit.")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError as error:
        raise SubtitleError("Subtitle text contains characters that cannot be saved as Unicode.") from error
    if size > MAX_TRANSCRIPT_BYTES:
        raise SubtitleError("Subtitle text is larger than the 8 MiB Editor limit.")
    if "\x00" in text:
        raise SubtitleError("Remove binary NUL characters before exporting subtitles.")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _validate(cues: Iterable[SubtitleCue]) -> tuple[SubtitleCue, ...]:
    result = []
    previous = -1
    total_bytes = 0
    try:
        iterator = iter(cues)
    except TypeError as error:
        raise SubtitleError("Subtitle cues are missing or invalid.") from error
    for number, cue in enumerate(iterator, 1):
        if number > MAX_CUES:
            raise SubtitleError("This transcript exceeds the 100,000 subtitle cue limit.")
        if not isinstance(cue, SubtitleCue):
            raise SubtitleError(f"Subtitle cue {number} is invalid.")
        if (type(cue.start_ms) is not int or type(cue.end_ms) is not int
                or not 0 <= cue.start_ms < cue.end_ms <= MAX_TIMESTAMP_MS):
            raise SubtitleError(f"Cue {number} needs a valid end time later than its start time.")
        if cue.start_ms < previous:
            raise SubtitleError(f"Cue {number} starts before the previous cue. Keep cues in time order.")
        text = _text(cue.text).strip()
        if not text:
            raise SubtitleError(f"Cue {number} has no subtitle text.")
        if any(not line.strip() for line in text.split("\n")):
            raise SubtitleError(f"Remove blank lines within cue {number}; blank lines separate subtitle cues.")
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_TRANSCRIPT_BYTES:
            raise SubtitleError("Subtitle text is larger than the 8 MiB Editor limit.")
        result.append(SubtitleCue(cue.start_ms, cue.end_ms, text))
        previous = cue.start_ms
    if not result:
        raise SubtitleError("There are no timed subtitle cues to export.")
    return tuple(result)


def _looks_like_marker(line: str) -> bool:
    stripped = line.lstrip()
    # Also catch damaged markers so stored timings never hide a timestamp typo.
    # An accidentally deleted opening bracket must not turn the marker itself
    # into caption text when there is still one line per stored timing.
    return bool(re.match(r"\[[0-9]+:", stripped) or
                (stripped.startswith("[") and "-->" in stripped) or
                ("-->" in stripped and
                 re.match(r"[0-9]+:[0-9]{1,2}:[0-9]{1,2}(?:[.,][0-9]+)?", stripped)))


def _milliseconds(groups: tuple[str, ...]) -> int:
    hours, minutes, seconds, millis = map(int, groups)
    if minutes > 59 or seconds > 59:
        raise SubtitleError("Timestamp minutes and seconds must be between 00 and 59.")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_timed_transcript(text: str) -> tuple[SubtitleCue, ...]:
    """Parse current Editor markers, retaining multiline text for each cue.

    Each cue begins ``[HH:MM:SS.mmm --> HH:MM:SS.mmm]``. Blank lines may
    separate cues, but cannot occur within a cue's caption text.
    """
    normalized = _text(text)
    cues = []
    start = end = None
    lines = []
    for line_number, line in enumerate(normalized.split("\n"), 1):
        match = _MARKER.fullmatch(line)
        if match:
            if start is not None:
                cues.append(SubtitleCue(start, end, "\n".join(lines)))
            if len(cues) >= MAX_CUES:
                raise SubtitleError("This transcript exceeds the 100,000 subtitle cue limit.")
            groups = match.groups()
            start = _milliseconds(groups[:4])
            end = _milliseconds(groups[4:8])
            lines = [groups[8] or ""]
        elif _looks_like_marker(line):
            raise SubtitleError(f"Fix the timestamp on line {line_number}. Use [HH:MM:SS.mmm --> HH:MM:SS.mmm].")
        elif start is None:
            if line.strip():
                raise SubtitleError("Subtitle export needs timestamps before the text. "
                                    "Open a timed transcript or transcribe the recording first.")
        else:
            lines.append(line)
    if start is not None:
        cues.append(SubtitleCue(start, end, "\n".join(lines)))
    return _validate(cues)


def cues_for_text(text: str, timings: Iterable[tuple[int, int]] = ()) -> tuple[SubtitleCue, ...]:
    """Use visible timing markers, or pair stored timings with current text lines.

    With hidden timestamps, each nonempty Editor line is one original segment.
    Changing that line count is ambiguous and requires visible timing markers.
    Timings are never estimated from word counts or recording duration.
    """
    normalized = _text(text)
    lines = normalized.split("\n")
    if any(_looks_like_marker(line) for line in lines):
        return parse_timed_transcript(normalized)
    payloads = [line.strip() for line in lines if line.strip()]
    cues = []
    try:
        iterator = iter(timings)
    except TypeError as error:
        raise SubtitleError("Stored subtitle timings are invalid.") from error
    for number, timing in enumerate(iterator):
        if number >= MAX_CUES:
            raise SubtitleError("This transcript exceeds the 100,000 subtitle cue limit.")
        if not isinstance(timing, (tuple, list)) or len(timing) != 2:
            raise SubtitleError("Stored subtitle timings are invalid.")
        if number >= len(payloads):
            raise SubtitleError("The number of text lines no longer matches the saved timings. "
                                "Keep one nonempty line per segment or use visible timestamp markers.")
        cues.append(SubtitleCue(timing[0], timing[1], payloads[number]))
    if not cues:
        raise SubtitleError("Subtitle export needs segment timings. Open a timed transcript "
                            "or transcribe the recording first.")
    if len(cues) != len(payloads):
        raise SubtitleError("The number of text lines no longer matches the saved timings. "
                            "Keep one nonempty line per segment or use visible timestamp markers.")
    return _validate(cues)


def _format(format: str) -> str:
    if format not in ("srt", "vtt"):
        raise SubtitleError("Choose SRT or VTT as the subtitle export format.")
    return format


def _timestamp(milliseconds: int, separator: str) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def render_subtitles(cues: Iterable[SubtitleCue], format: str) -> str:
    """Render UTF-8-ready text, escaping literal text rather than adding styles."""
    selected = _format(format)
    validated = _validate(cues)
    parts = ["WEBVTT\n\n"] if selected == "vtt" else []
    size = 8 if selected == "vtt" else 0
    separator = "," if selected == "srt" else "."
    for number, cue in enumerate(validated, 1):
        payload = html.escape(cue.text, quote=False)
        timing = f"{_timestamp(cue.start_ms, separator)} --> {_timestamp(cue.end_ms, separator)}"
        block = f"{number}\n{timing}\n{payload}\n\n"
        size += len(block.encode("utf-8"))
        if size > MAX_SUBTITLE_BYTES:
            raise SubtitleError("This subtitle export exceeds the 16 MiB output limit.")
        parts.append(block)
    return "".join(parts)


def _regular_target(target: Path):
    try:
        details = target.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(details.st_mode)
            or getattr(details, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise SubtitleError("Choose a regular subtitle file; folders, symbolic links, and special files cannot be replaced.")
    return details


def _identity(details):
    if details is None:
        return None
    return details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns


def write_subtitles(path: str | os.PathLike[str], cues: Iterable[SubtitleCue], format: str) -> Path:
    """Atomically write one explicitly selected .srt or .vtt, preserving on failure."""
    selected = _format(format)
    try:
        target = Path(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError) as error:
        raise SubtitleError("Choose a valid subtitle export destination.") from error
    if target.suffix.lower() != f".{selected}":
        raise SubtitleError(f"Choose a filename ending in .{selected} for this export.")
    data = render_subtitles(cues, selected).encode("utf-8")
    temporary = None
    try:
        original = _regular_target(target)
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{target.name}.", suffix=".tmp",
                                         dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if original is not None:
            os.chmod(temporary, stat.S_IMODE(original.st_mode))
        current = _regular_target(target)
        if _identity(current) != _identity(original):
            raise SubtitleError("The subtitle file changed while exporting. Choose Export again to retry.")
        os.replace(temporary, target)
        temporary = None
    except (OSError, ValueError) as error:
        detail = getattr(error, "strerror", None) or str(error)
        raise SubtitleError(f"Could not export {target.name}: {detail}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                try:
                    os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return target

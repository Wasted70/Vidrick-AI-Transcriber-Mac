"""Read-only preflight and local storage paths for source and installed builds."""

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
import platform
import stat
import sys


MIN_PYTHON = (3, 11)
DATA_DIRECTORY_NAME = "Vidrick AI Transcriber Mac Preview"
SOURCE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Environment:
    system: str
    machine: str
    python_version: tuple[int, int, int]
    tk_discoverable: bool

    def issues(self) -> tuple[str, ...]:
        problems = []
        if self.system != "Darwin":
            problems.append("The preview requires macOS; this computer is not running macOS.")
        elif self.machine.strip().lower() != "arm64":
            problems.append(
                "Native arm64 Python on an M-series Mac is required. "
                "x86_64 may mean Intel or Rosetta; it does not identify the physical chip."
            )
        problems.extend(self._runtime_issues())
        return tuple(problems)

    def windows_preview_issues(self) -> tuple[str, ...]:
        """Explicit Windows UI development only; never a native Mac pass."""
        problems = []
        if self.system != "Windows":
            problems.append("Windows development preview requires Windows. Use --preview on a native M-series Mac.")
        problems.extend(self._runtime_issues())
        return tuple(problems)

    def _runtime_issues(self) -> tuple[str, ...]:
        problems = []
        if self.python_version[:2] < MIN_PYTHON:
            problems.append("Python 3.11 or newer is required for this starter.")
        if not self.tk_discoverable:
            problems.append("Tk modules were not found. Use a Python installation with Tk support.")
        return tuple(problems)


def inspect_environment() -> Environment:
    # Discover modules without importing Tk or opening a display. The presence
    # of a module is not proof that its native libraries can load or open a GUI.
    return Environment(
        system=platform.system(),
        machine=platform.machine(),
        python_version=tuple(sys.version_info[:3]),
        tk_discoverable=find_spec("tkinter") is not None and find_spec("_tkinter") is not None,
    )


def preview_data_directory(home: Path | None = None) -> Path:
    """Calculate the installed app's data path without creating it."""
    base = Path.home() if home is None else Path(home)
    return base / "Library" / "Application Support" / DATA_DIRECTORY_NAME


def is_bundled_mac() -> bool:
    return bool(getattr(sys, "frozen", False) and platform.system() == "Darwin"
                and platform.machine().strip().lower() == "arm64")


def app_data_root() -> Path:
    """Keep installed app data outside the bundle and source data in the project."""
    return preview_data_directory() if is_bundled_mac() else SOURCE_ROOT


def prepare_bundled_data_directory(path: Path) -> Path:
    """Create a private directory under Application Support without following links."""
    home = Path.home()
    path = Path(path)
    if (not is_bundled_mac() or not path.is_absolute() or ".." in path.parts
            or not path.is_relative_to(preview_data_directory(home))):
        raise OSError("This folder is outside Vidrick's Application Support directory.")
    for component in reversed((path, *path.parents)):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            if component == home or not component.is_relative_to(home):
                raise
            component.mkdir(mode=0o700, exist_ok=True)
            metadata = component.lstat()
        if (not stat.S_ISDIR(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise OSError(f"Vidrick data needs a regular directory without links: {component}")
    return path


def format_report(environment: Environment, *, windows_preview: bool = False) -> str:
    issues = environment.windows_preview_issues() if windows_preview else environment.issues()
    lines = [
        "Vidrick Mac - development preflight (no transcription)",
        f"Operating system: {environment.system or 'unknown'}",
        f"Interpreter architecture: {environment.machine or 'unknown'}",
        f"Python: {'.'.join(map(str, environment.python_version))}",
        f"Tk modules discoverable: {'yes' if environment.tk_discoverable else 'no'}",
    ]
    if windows_preview:
        lines.append("Windows development preview - not a Mac compatibility test.")
    if issues:
        lines.append("Preflight: NOT READY")
        lines.extend(f"- {issue}" for issue in issues)
    else:
        lines.append("Preflight: PASSED - opening the preview is the next manual check.")
    if windows_preview:
        lines.append("Native Mac support has NOT been validated; this only previews the UI on Windows.")
    lines.append("GUI operation, AI dependencies, and performance have NOT been validated.")
    lines.append("No app data created; no models, customer accounts, or network services accessed.")
    return "\n".join(lines)

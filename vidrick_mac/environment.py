"""Read-only starter checks, with no GUI, network, or filesystem writes."""

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
import platform
import sys


MIN_PYTHON = (3, 11)
DATA_DIRECTORY_NAME = "Vidrick AI Transcriber Mac Preview"


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
    """Calculate a future Mac-only path; never create or read that directory."""
    base = Path.home() if home is None else Path(home)
    return base / "Library" / "Application Support" / DATA_DIRECTORY_NAME


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

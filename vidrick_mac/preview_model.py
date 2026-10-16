"""In-memory UI state and design tokens; no file access or production imports."""

from dataclasses import dataclass, field
from collections.abc import Iterable
from pathlib import PurePosixPath, PureWindowsPath


# Independent Mac-preview palette. No shared Windows design files or assets.
COLORS = {
    "window": "#edf1f8", "header": "#f8faff", "panel": "#e8edf5",
    "panel_alt": "#fbfcff", "card": "#fbfcff", "card_hover": "#e6effc",
    "border": "#d5dfed", "accent": "#086bd8", "accent_hover": "#0059bb",
    "accent_soft": "#e7f0fc", "text": "#202124", "muted": "#62646b",
    "sidebar": "#e7edf7", "selected": "#d4e5fb", "on_accent": "#ffffff",
    "shadow": "#dfe6f1", "highlight": "#ffffff",
}
PREVIEW_TABS = ("Transcribe", "Editor", "Insights", "History", "About & Updates")


def display_filename(path: str, *, windows_preview: bool) -> str:
    """Extract a display name lexically; never touch the selected file."""
    path_type = PureWindowsPath if windows_preview else PurePosixPath
    return path_type(path).name or path


@dataclass
class PreviewState:
    files: list[str] = field(default_factory=list)

    def add_files(self, paths: Iterable[str]) -> int:
        """Store path strings only; never open, stat, or copy selected files."""
        added = 0
        for path in paths:
            if path and path not in self.files:
                self.files.append(path)
                added += 1
        return added

    def remove_files(self, indices: Iterable[int]) -> None:
        for index in sorted(set(indices), reverse=True):
            if 0 <= index < len(self.files):
                del self.files[index]

    def clear_files(self) -> None:
        self.files.clear()

    @property
    def summary(self) -> str:
        count = len(self.files)
        if not count:
            return "No files added"
        return f"{count} {'file' if count == 1 else 'files'} added - not read or processed"

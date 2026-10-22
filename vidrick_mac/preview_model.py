"""In-memory UI state and design tokens; no file access or production imports."""

from dataclasses import dataclass, field
from collections.abc import Iterable
from pathlib import PurePosixPath, PureWindowsPath


# Independent Mac-preview palette. No shared Windows design files or assets.
COLORS = {
    "window": "#f5f5f7", "header": "#f5f5f7", "panel": "#ebebed",
    "panel_alt": "#f7f7f8", "card": "#ffffff", "card_hover": "#f0f0f2",
    "border": "#dcdce0", "accent": "#007aff", "accent_hover": "#0064d2",
    "accent_soft": "#eaf2ff", "text": "#242426", "muted": "#6c6c72",
    "sidebar": "#ededf0", "selected": "#dce9fb", "on_accent": "#ffffff",
    "shadow": "#eaeaed", "highlight": "#ffffff", "sidebar_selected": "#dcdce1",
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

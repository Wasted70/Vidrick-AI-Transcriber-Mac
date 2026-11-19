"""Windowed entry point for the private Mac app bundle."""

import subprocess
import sys

from .environment import inspect_environment, is_bundled_mac


def _launch_error(message: str, *, macos: bool) -> int:
    if macos:
        # A native dialog also works if the bundled Tk runtime cannot load.
        script = '''on run argv
    display alert "Vidrick couldn't open" message (item 1 of argv) as critical
end run
'''
        try:
            subprocess.run(["/usr/bin/osascript", "-", message], input=script, text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True)
            return 2
        except (OSError, subprocess.CalledProcessError):
            pass
    if sys.stderr is not None:
        print(f"Vidrick couldn't open. {message}", file=sys.stderr)
        return 2
    # The windowed PyInstaller bootloader can report an uncaught startup error.
    raise RuntimeError(message)


def _check_bundle() -> None:
    """Exercise the bundled Tk and HTTPS trust store without opening user data."""
    if not is_bundled_mac():
        raise RuntimeError("The bundle check requires the native Apple Silicon app.")
    import tkinter as tk

    from .setup_io import _download_context

    if not _download_context().cert_store_stats()["x509_ca"]:
        raise RuntimeError("The bundled HTTPS trust store has no certificates.")
    root = tk.Tk()
    try:
        root.withdraw()
        root.update_idletasks()
    finally:
        root.destroy()


def main() -> int:
    """Open the Mac UI directly when launched by Finder."""
    if sys.argv[1:] == ["--bundle-check"]:
        # The builder uses exit status and stderr rather than a modal dialog.
        try:
            _check_bundle()
        except Exception as error:
            if sys.stderr is not None:
                print(f"Vidrick bundle check failed: {error}", file=sys.stderr)
            return 2
        return 0
    environment = inspect_environment()
    issues = environment.issues()
    if issues:
        return _launch_error("\n".join(issues), macos=environment.system == "Darwin")

    try:
        from .preview import launch_preview
        launch_preview()
    except Exception as error:
        detail = str(error).strip()[:1500].replace("\x00", " ") or type(error).__name__
        return _launch_error(detail, macos=True)
    return 0

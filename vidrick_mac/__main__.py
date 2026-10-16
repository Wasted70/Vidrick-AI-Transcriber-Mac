"""Explicit native-Mac preflight/preview or separate Windows UI preview."""

import argparse
import sys

from .environment import format_report, inspect_environment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vidrick Mac starter: no transcription or network access.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Print read-only environment checks (default).")
    mode.add_argument("--preview", action="store_true", help="Open the non-transcribing preview on native arm64 macOS.")
    mode.add_argument("--windows-preview", action="store_true", help="Preview the UI on Windows only; does not test Mac compatibility.")
    args = parser.parse_args(argv)
    environment = inspect_environment()
    print(format_report(environment, windows_preview=args.windows_preview))
    issues = environment.windows_preview_issues() if args.windows_preview else environment.issues()
    if issues:
        return 2
    if args.preview or args.windows_preview:
        try:
            from .preview import launch_preview
            if args.windows_preview:
                launch_preview(windows_preview=True)
            else:
                launch_preview()
        except Exception as exc:
            print(f"Preview could not open: {exc}", file=sys.stderr)
            runtime = "Windows Python" if args.windows_preview else "native arm64 Python"
            print(f"Check that {runtime} has working Tk support and a desktop session.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

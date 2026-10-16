VIDRICK MAC - APPLE SILICON STARTER
Prototype 0.1.0-dev | Started October 14, 2026

WHAT THIS IS
A small, independent foundation for an M-series Mac version. It is not a
Mac release of Vidrick 4.3.1, an installer, or a working transcriber yet.
Windows remains the primary product. Nothing here changes the Windows app.

WHAT WORKS AT THIS STAGE
- A read-only command that checks macOS, native arm64 Python, Python version,
  and whether the optional Tk modules can be found.
- A light Mac-style interface: navigation sidebar, compact toolbar, white
  content surfaces, blue accents, and a separate transcription settings pane.
- Soft rounded cards, sidebar selection pills, subtle highlights/shadows,
  and rounded buttons in the Windows development preview.
- Five working sections: Transcribe, Editor, Insights, History, About & Updates.
- Platform system font; native Aqua controls are preferred on macOS. The
  Windows preview uses a separately styled Tk theme. Native rendering still
  needs Mac testing; this is not SwiftUI or a Liquid Glass implementation.
- Multi-file browsing with clear filenames, selectable full-path details,
  selection-aware remove/clear controls, and temporary settings selections.
- Keyboard shortcuts for adding/selecting/removing files and opening settings.
- A local transcript Editor with .txt Open/Save/Save As, find and replace,
  undo/redo, unsaved-change protection, word/character counts, and text sizing.
- Clearly labeled Insights, History, and About & Updates placeholder panels.
- A separately labeled Windows development preview of that same small UI.
- A separate future Mac-preview data path. It is calculated, not created.
- Standard-library unit tests; no pip install or AI dependencies required.

WHAT DOES NOT HAPPEN
No recording is read, transcribed, uploaded, or copied. A selected file path
in the recording list is shown only in the preview window and is not saved.
The Editor reads and writes only the .txt files you explicitly open or save.
There are no models,
production account connections, license requests, analytics, or updates.
The transcription button is deliberately disabled. No Windows source,
settings, credentials, release files, or dependencies were copied here.

TRY THE PREVIEW ON YOUR WINDOWS PC
Double-click Launch_Windows_Preview.cmd in this folder. It uses the existing
Codex-bundled Python on this PC, or the system Python launcher if available.
It does not install anything. A console window may remain behind the preview;
closing the preview closes the launcher after a successful run.

Alternatively, from THIS Mac folder with Python 3.11+ and Tk:

  python -E -B -m vidrick_mac --windows-preview

The title and footer identify this as a Windows development preview. You can
choose multiple files, remove/clear selections, switch the five sidebar sections, try the
settings controls, and edit local text transcripts in the Editor. The Settings
button jumps to the top of the Transcribe section's scrollable settings pane.
Use the mouse wheel or scrollbar in that pane, or arrow/Page Up/Page Down keys
when the settings canvas has focus. Keyboard focus is visible on controls.
Tab skips hidden pages and unavailable controls. Settings scroll into view
as keyboard focus reaches them. File rows show names; selecting a row shows
its original full path in a read-only area where you can select and copy text.
Multiple selection shows all selected paths. Remove affects selected rows;
Clear empties the list. Duplicate additions and canceled browsing preserve
the current selection. Removing rows selects the nearest remaining row.

Keyboard shortcuts (while the preview window is active):
  Mac Command+O / Windows Ctrl+O: Open a .txt transcript in the Editor;
    add recording paths from the other sections.
  Mac Command+, / Windows Ctrl+,: Open and focus transcription settings.
  Mac Command+F / Windows Ctrl+F: Show the Editor's find/replace panel.
  Mac Command+S / Windows Ctrl+S: Save the Editor transcript.
  Mac Command+Shift+S / Windows Ctrl+Shift+S: Save As a separate .txt file.
  Mac Command+Z / Windows Ctrl+Z: Undo while editing the transcript.
  Mac Command+Shift+Z / Windows Ctrl+Shift+Z: Redo (Windows Ctrl+Y also works).
  Mac Command+A / Windows Ctrl+A: Select all rows when the file list has focus.
  Delete or Backspace: Remove selected rows when the file list has focus.
  Tab / Shift+Tab: Move between controls, including the read-only path area.
The Editor retains normal text editing; list removal shortcuts never remove
files while typing there. Use Ctrl+Tab to leave the Editor's text area.
Native Command shortcuts still require an actual Mac keyboard check.

EDITING TRANSCRIPTS
Open a .txt file or begin typing in the empty Editor. Save writes to the
current file; an untitled document prompts for a destination. Save As writes
a separate copy. The filename line shows Unsaved changes until the current
text matches the last successful save or open. Before closing or replacing
an edited document, the app asks whether to save, discard, or keep editing.
Canceling or failing a save keeps the window and your edits open.

Find and replace uses literal text, with optional Match case. Previous/Next
wrap through highlighted matches; Return / Shift+Return navigate from the
search fields and Escape closes the panel. Replace changes the current
match; Replace All is one undoable action. Searches show up to 2,000 matches;
larger results ask you to refine the term before Replace All, with no partial
replacement. Text size ranges from 12 to 24 points and does not modify the
saved text. Word counts use whitespace-separated words.

Files may be up to 8 MiB, encoded as UTF-8 or BOM-marked UTF-16. Opened BOMs
and the dominant line ending are preserved when saving; new files use UTF-8
with LF line endings. Invalid/binary text is rejected with an error message.
Saves use a temporary file beside the destination and replace it only after
writing succeeds. Changes made to the current file by another app trigger
an overwrite decision. For a symbolic-link transcript, use Save As to a new
regular .txt file. There is no autosave or crash recovery yet.

The toolbar's Transparency checkbox adds a subtle effect at 97% window
opacity. Uncheck it for a fully opaque window. This choice is temporary;
unsupported systems fall back to opaque mode. The effect applies to the
whole window, including text; it is not background blur or native Liquid
Glass. Rounded button artwork is generated in memory using the standard
library; no assets or extra dependencies are downloaded. Aqua keeps its
native button rendering on macOS.
Transcription, formatted exports, recording output-folder selection, account
management, and updates remain disabled. Drag and drop is not implemented.
Recording paths and settings disappear when the preview closes; only text
explicitly saved from the Editor persists.
This is not a Mac emulator or a functional port of the Windows app. Appearance,
installation, Apple-chip performance, and Mac compatibility still need a Mac.
It never uses the installed Windows Vidrick app or its project, data, models,
accounts, or settings. The -E flag ignores Python environment overrides, and
-B prevents bytecode files from being written during the preview.

DEVELOPMENT STATUS
GitHub holds ongoing development updates. Friend testing is deferred until
the owner says the app is ready. The following Mac setup instructions are
available for that later stage; a push is not a signed release or test invite.

RUN ON AN M-SERIES MAC WHEN READY
The Mac source lives in its own GitHub repository:
https://github.com/Wasted70/Vidrick-AI-Transcriber-Mac

Use a native arm64 Python 3.11 or newer with Tk available. No pip packages
are required at this stage. In Terminal, download the source once:

  git clone https://github.com/Wasted70/Vidrick-AI-Transcriber-Mac.git
  cd Vidrick-AI-Transcriber-Mac
  python3 -E -B -m vidrick_mac --check
  python3 -E -B -m vidrick_mac --preview

If GitHub requires sign-in, use your own GitHub account with access to this
repository. Never share the owner's password or token. Downloading the ZIP
from GitHub is also possible; extract it and run the same Python commands
from the extracted folder. A ZIP download cannot be updated with git pull.

GET THE NEXT TEST VERSION
Completed, verified Mac changes from the development chat are committed and
pushed to main. This happens when a development task finishes; there is no
background file watcher or automatic updater inside the app.

Close the running preview first. In your cloned Mac repository, run:

  git pull --ff-only origin main
  python3 -E -B -m vidrick_mac --check
  python3 -E -B -m vidrick_mac --preview

If pull reports local edits or divergent history, keep your changes and
report the message instead of using reset or a force update. The preview
does not fetch changes itself, so relaunch after every pull.

Run --check first. Exit code 0 means only that the starter preflight passed;
it does not certify GUI operation or transcription performance. Exit code 2
means a requirement is missing. --check never opens a window.

If the reported architecture is x86_64, the interpreter may be running under
Rosetta or on an Intel Mac. Use native arm64 Python on an M-series Mac. The
checker does not attempt to identify the physical chip through emulation.

Tk is optional in some Python installations. The Python documentation explains
how to check it with `python3 -m tkinter` and obtain a suitable Mac Python:
https://docs.python.org/3/library/tkinter.html
https://docs.python.org/3/using/mac.html

Do not change system Python or disable macOS security protections for this
preview. It is not an app bundle, so it cannot yet be installed by dragging
an app to Applications. No minimum macOS release has been validated yet.

FEEDBACK FROM MAC TESTING
Include the commit from `git rev-parse --short HEAD`, the --check output,
your macOS version, and a short description or screenshot of any issue.
Useful first checks: all five sections, file-picker cancellation, duplicate
file additions, same-name files in different folders, Command+O / Command+,
and list Command+A / Delete. Confirm typing in the Editor never removes
file rows, Tab reveals focused settings, resizing remains usable, and the
Transparency checkbox and closing/reopening work. Use non-sensitive sample
files and keep private paths or customer content out of screenshots.
For the Editor, check text open/save, canceled save-before-close, find/replace,
undo back to a saved state, Unicode text, and Command shortcuts.

RUN TESTS (MAC OR WINDOWS)
From THIS Mac folder, using an existing Python 3.11+ interpreter:

  python -B -m unittest discover -s tests -v

The normal suite is headless; file tests create only synthetic data under
test-output/ and clean up their temporary folders. To include real Tk Editor
checks in a desktop session, set VIDRICK_TEST_TK=1 for that test process:

  Mac: VIDRICK_TEST_TK=1 python3 -E -B -m unittest discover -s tests -v
  Windows PowerShell: $env:VIDRICK_TEST_TK = '1'
                      python -E -B -m unittest discover -s tests -v

Windows results and mocked Mac environments do not prove native Mac support.
Running --preview on Windows is intentionally refused without opening a window.
Use the explicit --windows-preview mode instead. That flag does not make the
default Mac preflight pass, and it is refused on macOS/Linux.

SEPARATION FROM WINDOWS
Windows reference: C:\Vidrick_AI_Transcriber_4.3 - Main
Mac work:          C:\Vidrick_AI_Transcriber_Mac
Future data path:  ~/Library/Application Support/Vidrick AI Transcriber Mac Preview

There is no live source link, automatic synchronization, or dependency on the
Windows folder. Future shared fixes should be deliberately ported and tested
after Windows work, not automatically copied in either direction.

NEXT SMALL MILESTONE
Develop a one-recording transcription workflow with progress, cancellation,
and a result that opens in the Editor. Native Mac validation will follow when
the owner is ready: preflight, window, Command shortcuts, file picker, focus,
and closing behavior. Separately scope a CPU transcription experiment,
Mac dependencies, and licensing integration. Packaging, signing, notarization,
hosted builds, GPU acceleration, and packaged app releases are later work.

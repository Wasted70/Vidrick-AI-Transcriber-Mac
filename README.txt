VIDRICK MAC - APPLE SILICON STARTER
Prototype 0.1.0-dev | Started October 14, 2026

WHAT THIS IS
A small, independent foundation for an M-series Mac version. It is not a
Mac release of Vidrick 4.3.1 or an installer. The local development pipeline
is implemented, but actual inference and native Mac behavior are unverified.
Windows remains the primary product. Nothing here changes the Windows app.

WHAT WORKS AT THIS STAGE
- A read-only command that checks macOS, native arm64 Python, Python version,
  and whether the optional Tk modules can be found.
- A macOS-oriented desktop layout: neutral sidebar, integrated toolbar, white
  grouped content, compact rounded controls, and restrained blue accents.
- Smaller headings, regular-weight list rows, aligned Editor tools/status,
  and alternating History rows in the Windows development preview.
- Five sections: Transcribe, Editor, History, and placeholder Insights and About & Updates.
- Platform system font; native Aqua controls are preferred on macOS. The
  Windows preview uses a separately styled Tk theme. Native rendering still
  needs Mac testing; this is not SwiftUI or a Liquid Glass implementation.
- Multi-file browsing with clear filenames, selectable full-path details,
  selection-aware remove/clear controls, and remembered Mac engine/model paths.
- Keyboard shortcuts for adding/selecting/removing files and opening settings.
- A local transcript Editor with .txt Open/Save/Save As, find and replace,
  undo/redo, unsaved-change protection, word/character counts, and text sizing.
- SRT and VTT subtitle exports using real segment timings and current Editor
  text. Timings remain available through History and autosave recovery.
- One-recording local transcription on native arm64 macOS using an existing
  whisper.cpp engine/model, with genuine engine progress, cancellation, and
  a finished transcript that opens as an unsaved document in the Editor.
- In-app engine/model setup: choose a model, download verified components,
  build local tools, and retain setup across launches. Cancellation and retry
  preserve the previous configuration. Native installation is not yet verified.
- Local searchable History with recording names, dates, models, reopen, rename,
  and delete. Completed transcripts are saved before opening in the Editor.
- Automatic local recovery drafts for Editor changes, with restore/discard
  choices after an unexpected close. Manually saved files remain separate.
- Clearly labeled Insights and About & Updates placeholder panels.
- A separately labeled Windows development preview of that same small UI.
- A separate future Mac-preview data path. It is calculated, not created.
- Standard-library unit tests; no pip install or AI dependencies required.

WHAT DOES NOT HAPPEN
Choosing a recording only adds its path to the list. On the Mac, Start reads
the one selected recording and may create a temporary converted audio file.
The app does not upload recordings. Downloads begin only after clicking Set Up
on a Mac. The Windows UI preview never downloads or installs setup components,
starts transcription, saves setup preferences, or reads selected recordings.
The Editor reads and writes the .txt files you explicitly open or save, and
writes .srt/.vtt files when you choose Export. History also keeps finished
transcript text and segment timings locally, and autosave keeps recovery drafts
in the project's private local library. It never copies source recordings.
There are no bundled models, production account connections, license
requests, analytics, or updates. No Windows source,
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
regular .txt file. Local recovery drafts protect edits between explicit saves;
see HISTORY AND DRAFT RECOVERY below for the recovery window and limits.

The toolbar's optional Transparency checkbox adds a subtle effect at 98.5%
window opacity. It starts off so reading surfaces are fully opaque. This choice is temporary;
unsupported systems fall back to opaque mode. The effect applies to the
whole window, including text; it is not background blur or native Liquid
Glass. Rounded button artwork is generated in memory using the standard
library; no assets or extra dependencies are downloaded. Aqua keeps its
native button rendering on macOS. Native window decorations are retained:
the Windows preview still has the Windows title bar. It does not simulate
macOS window controls or implement native vibrancy. Sidebar/toolbar structure
draws on Apple's macOS design guidance:
https://developer.apple.com/design/human-interface-guidelines/designing-for-macos/
https://developer.apple.com/design/human-interface-guidelines/toolbars
PDF/Word exports, recording output-folder selection, account management,
and updates remain unavailable. Drag and drop is not implemented.
Recording selections, language/task choices, and window preferences are temporary.
On a Mac, successful setup paths are saved locally for the next launch. Text
can be exported with Save/Save As. Completed results and recovery drafts also
persist in the local library described below.
This is not a Mac emulator or a full port of the Windows app. Appearance,
installation, Apple-chip performance, and Mac compatibility still need a Mac.
It never uses the installed Windows Vidrick app or its project, data, models,
accounts, or settings. The -E flag ignores Python environment overrides, and
-B prevents bytecode files from being written during the preview.

EXPORTING SUBTITLES
In the Editor, choose Export, then SubRip (.srt) or WebVTT (.vtt), and select
a destination. Exports use current edits without marking the .txt document
saved, replacing its path, or removing its recovery draft. Cancel and failed
writes leave the Editor unchanged. Existing files require the save dialog's
overwrite confirmation; files changed during writing are preserved.

New transcriptions retain segment timings even when Show timestamps is off.
When editing text with hidden timestamps, keep one nonempty line per segment;
export rejects a changed line count rather than estimating new timing. With
visible timestamps, edit words or line breaks inside each timed segment:

  [00:00:01.250 --> 00:00:03.000] Hello, world!

Each cue needs an end later than its start, and cues must stay in start-time
order. Multiline captions work inside timestamped segments; blank lines can
separate cues but cannot split a caption. No automatic timing estimation,
caption reflow, or subtitle import is included. Plain text without segment
timings cannot be exported as subtitles.

History and recovery keep timing metadata alongside text. Older timestamped
results and timestamped .txt files can also export. Old results without any
timings must be transcribed again. Saving a .txt copy without visible timestamps
does not embed hidden timing metadata in that external file; reopen the result
from History to retain it, or export subtitles before closing the Editor.

Output is UTF-8 without a BOM, with LF line endings. SRT has numbered blocks
and comma milliseconds; VTT starts with WEBVTT and uses dot milliseconds.
Transcript text is treated literally: &, <, and > are escaped, not interpreted
as subtitle styling. SRT player support for literal entities can vary. Input
is limited to 8 MiB and 100,000 cues, with a 16 MiB subtitle output limit.
Files are staged beside the destination and replaced only after a complete
write. Symbolic-link and nonregular destinations are rejected.
Format references:
https://www.w3.org/TR/webvtt1/
https://www.loc.gov/preservation/digital/formats/fdd/fdd000569.shtml

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

LOCAL TRANSCRIPTION DEVELOPMENT WORKFLOW
This is a development integration, not a licensed product release. No friend
testing, packaging, model distribution, or licensing integration is enabled
by a push. Production licensing must be integrated before distribution.

On a native arm64 Mac, open Transcribe > Engine & Models. Easy setup
prepares whisper.cpp and FFmpeg and downloads the selected multilingual model.
Use existing files is available for an installed whisper-cli, local GGML .bin
model, and optional FFmpeg. Those paths are saved only after Use These Files.
The adapter targets the v1.9.5 CLI protocol; actual native build and inference
are still unverified:
https://github.com/ggml-org/whisper.cpp/tree/v1.9.5
https://github.com/ggml-org/whisper.cpp/blob/v1.9.5/examples/cli/cli.cpp

FFmpeg handles MP3, M4A, MP4, MOV, FLAC, OGG, AAC, WMA, WebM, MKV, AIFF, or
WAV needing conversion. A nonempty 16-bit mono PCM WAV
at 16 kHz can go directly to the engine. Only local regular files are accepted.
FFmpeg inputs use the selected extension's media demuxer with network
protocols excluded; conversion produces a temporary mono 16 kHz PCM WAV.
https://ffmpeg.org/ffmpeg.html
https://ffmpeg.org/ffmpeg-protocols.html

Add recordings, select exactly one row, choose language/task and whether to
include timestamps, then Transcribe selected file. The list is a workspace,
not an automatic batch queue. The selected recording and settings are fixed
for that run. File additions/removal and settings lock while it runs; the
Editor remains usable. The engine is invoked with GPU use disabled (-ng).
There are no simulated transcripts or timer-based percentages in the app.

Preparation and model loading show indeterminate progress. Percentages come
from whisper-cli callbacks during inference; 100% requires successful output
validation and cleanup. Cancel stops the local process and clears temporary
files before another job can start. Closing during a run offers cancellation
and waits without freezing the UI. Errors remain available under Details.

A finished transcript is saved in local History, then opens in the Editor
as an editable copy with the recording's name. Use Save to write a .txt file.
Timestamped output uses [HH:MM:SS.mmm --> ...]. If
existing edits cannot be replaced, or a file dialog is already open, the result
waits under Transcribe (marked with a dot). Open in Editor retries the handoff.
Open or explicitly dismiss that result before starting another job. Dismissing
a result saved in History does not delete the History entry. If History cannot
save, the result remains waiting with Retry History save and Details; Open in
Editor lets you save a .txt copy. Closing
checks both the pending result and Editor edits. An empty engine result is
shown as 'no speech found' without replacing the Editor document.

Temporary audio, JSON, and bounded diagnostic logs stay under the project's
ignored runtime-output/job-* directory and are removed when the job ends.
If a process cannot be confirmed stopped, its job files are kept and Details
explains where they are. Cleanup failures keep the window open with an error.
An OS/app crash may also leave temporary files; draft recovery does not resume
interrupted transcription or clean up interrupted engine jobs.
There is no batch processing, playback, speaker separation,
or Word export yet. Never commit recordings, models, or local preferences.

IN-APP ENGINE AND MODEL SETUP
Easy setup has three multilingual choices:
  Fast (Tiny):       about 78 MB
  Balanced (Base):  about 148 MB, the starting recommendation
  Detailed (Small): about 488 MB, larger and slower to process

The first setup additionally downloads about 108 MB of tools and sources.
Allow at least 4 GB of free space and several minutes for local compilation.
Apple's developer tools may need an additional download managed by macOS.
No benchmark or minimum macOS release has been validated for this app yet.

Choose Set Up on This Mac to download, build, check, and select the components.
If Details reports missing Apple developer tools, choose Install Apple tools,
complete Apple's own installer, then run setup again. There are no Terminal,
Homebrew, pip, sudo, or global package installation steps in this flow. Apple's
installer is a separate system process and is not cancelled by closing Vidrick.
https://developer.apple.com/library/archive/technotes/tn2339/_index.html

Downloads use pinned versions, exact sizes, and SHA-256 verification:
- whisper.cpp v1.9.5 source commit d1be6fde11ac6e0407606b4e42fe72d34add8037.
- CMake 3.31.12 portable Mac CLI from Kitware's official release. Only its CLI
  and build modules are extracted; no system installation occurs.
  https://github.com/Kitware/CMake/releases/tag/v3.31.12
- FFmpeg 9.0.2 official source, built with network and external dependency
  discovery disabled. The adapter only needs local media to PCM WAV conversion.
  https://ffmpeg.org/download.html
- GGML models from ggerganov/whisper.cpp at immutable Hugging Face revision
  5359861c739e955e79d9a303bcbc70fb988958b1.
  https://huggingface.co/ggerganov/whisper.cpp/tree/5359861c739e955e79d9a303bcbc70fb988958b1

The progress bar measures the current file download/check. Compilation uses
indeterminate progress and names the current stage. Cancel waits for active
work to stop; the Editor remains usable. A failed download/build or cancelled
setup keeps the prior selection. A failure to stop or clean up remains visible
under Details. Close also waits for setup cleanup before checking Editor edits.

Managed files live under this project's ignored local-setup/ directory:
downloads/ holds verified retry files, tools/ holds versioned executables and
licenses, models/ holds models, work/ holds temporary builds, and selection.json
stores chosen paths. Verified downloads/tools can remain after cancellation;
partial downloads and normal build scratch space are removed. A process-stop
or cleanup failure can retain scratch files and reports the next step.
There is no background updater, startup network request, download resumption
for partial files, or automatic removal of older model/tool versions.

Opening Set Up again lets you switch models; verified tools and downloads are
reused. Manual files are left where you selected them. Moving or removing those
files requires choosing new paths. Windows mode can preview this screen but
all setup installation, persistence, and execution actions remain disabled.

HISTORY AND DRAFT RECOVERY
On native Mac, every nonempty completed result is stored locally before the
Editor handoff. History shows newest results first, with their title, date and
model. Search filters titles/model names; Open loads an editable copy, Rename
changes its library title, and Delete removes only that selected saved result.
Original recordings, exported files, and current Editor edits are not
changed by deleting History. Editing a reopened result does not replace its
original History text; use Save/Save As to keep an edited .txt copy.

The Editor saves a separate recovery draft after a short pause in editing,
and periodically during continued typing. Its recovery status shows whether
the current text has been kept locally. A sudden crash may lose the last few
seconds of typing; this is not an undo-history backup or a recording backup.
After restart, History offers Restore and Discard for saved recovery drafts.
Restore opens an untitled editable copy; use Save/Save As or subtitle Export,
so it cannot overwrite a file that changed while the app was closed.

Successful Save clears that document's recovery draft. Cancelled or failed
saves/open operations keep it. A normal close still asks about unsaved edits;
choosing Discard intentionally removes that session's recovery draft after all
close checks pass. Other recovery drafts remain available until restored or
explicitly discarded. Storage failures remain visible and preserve the last
successful snapshot; save a .txt copy if recovery storage is unavailable.

The prototype keeps its private SQLite library under the project:
  local-history/mac/             native Mac results and recovery drafts
  local-history/windows-preview/ isolated Windows UI development drafts
These directories are Git-ignored. They contain transcript text, segment
timings, and minimal metadata, not recordings, account data or model files. They are local files,
not encrypted storage or a cloud backup. Moving/deleting the project can make
this library unavailable; keep important work with Save/Save As outside it.
The development library holds up to 2,000 results and 100 recovery drafts;
reaching a limit asks you to remove older entries without pruning anything.
Restoring an existing draft still works when the draft limit is reached.
Only one app instance may use each library at a time. If another instance has
it open, close that instance and use History's Refresh to retry. Storage is
transactional; existing results are not silently removed to free space.
The subtitle update migrates the library from schema 1 to schema 2 atomically,
preserving old text. Older app revisions cannot open a schema 2 library.

The Windows UI preview can exercise local History and Editor recovery using
its own separate library. It still cannot run transcription or install models.
Automated tests use in-memory or temporary synthetic libraries, never either
real preview library. Native Mac filesystem and crash behavior require Mac
validation; Windows tests do not establish those guarantees.

SEPARATION FROM WINDOWS
Windows reference: C:\Vidrick_AI_Transcriber_4.3 - Main
Mac work:          C:\Vidrick_AI_Transcriber_Mac
Future data path:  ~/Library/Application Support/Vidrick AI Transcriber Mac Preview

There is no live source link, automatic synchronization, or dependency on the
Windows folder. Future shared fixes should be deliberately ported and tested
after Windows work, not automatically copied in either direction.

NEXT SMALL MILESTONE
When the owner is ready, validate on an actual M-series Mac: native preflight,
Aqua rendering, Command shortcuts, local engine/model setup, WAV and converted
media, real progress, cancellation during conversion/model loading/inference,
and saving the final transcript. Check SRT/VTT export from the Editor, reopened
History results, and recovered drafts. Check that cancelling or closing leaves no
child process or temporary job files. Also check no-speech and failure recovery.
Licensing integration, packaging, signing, notarization, hosted builds, GPU
acceleration, and packaged app releases are separately scoped later work.

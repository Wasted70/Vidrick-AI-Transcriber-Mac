# Mac-only development boundaries

- This folder is the isolated Apple Silicon Mac prototype, not the Windows app.
- Windows visual references may be read, but recreating their appearance here does not authorize Windows edits. Only change the Windows project when the user explicitly requests a Windows update/fix.
- The Windows project at `C:\Vidrick_AI_Transcriber_4.3 - Main` is read-only reference. Windows remains the primary product and receives customer-feedback fixes first.
- Do not edit, build, test, install packages into, publish from, or create caches in the Windows project. Never import its modules or add it to `sys.path`.
- Keep all code, tests, outputs, environments, and build work inside this Mac folder. No links to shared writable source or automatic two-way synchronization.
- Do not copy whole project trees, customer recordings, account state, secrets, signing keys, installers, CUDA libraries, portal code, or website code here.
- Current scope is an offline preflight and non-transcribing UI preview. No model downloads, licensing requests, account activation, automatic updates, telemetry, or production services.
- A future functional port must preserve licensing enforcement; this preview has no transcription engine and is not a licensing bypass.
- Native M-series/arm64 macOS is the initial target. Intel/Rosetta and Apple GPU acceleration are outside the current scope.
- The user authorized a separate `--windows-preview` UI development mode in this Mac folder. It must be explicitly selected, Windows-only, visibly labeled, non-transcribing, and disconnected from the production Windows app. Preserve the native-Mac guards on `--check` and `--preview`.
- Do not claim Mac compatibility, benchmark results, or release readiness from Windows-based or mocked tests. Native Mac testing is still required.
- Do not push, publish, purchase services, or configure hosted builds without a user request covering that work.
- Run tests with `python -B -m unittest discover -s tests -v`. Do not install dependencies globally.

## GitHub updates for Mac testing

- The user authorized publishing this isolated Mac project and automatically pushing completed, verified changes from this chat so their friend can test them.
- Repository: `https://github.com/Wasted70/Vidrick-AI-Transcriber-Mac.git`; remote `origin`; testing branch `main`. This authorization covers this repository only. It does not authorize changing repository visibility, inviting collaborators, hosted builds, releases, or publishing anything from the Windows project.
- At the end of each completed Mac development task, inspect the changes and verify `origin` still targets this exact repository. Run `python -E -B -m unittest discover -s tests -v` using the appropriate existing Python interpreter; perform focused UI checks when needed.
- Stage only reviewed files belonging to the completed task. Keep recordings, customer data, credentials, models, virtual environments, and build output out of Git. Never include unrelated user changes in an automatic commit.
- Commit with a descriptive message and push `main` to `origin` without asking again. This is an end-of-task workflow, not a background file watcher or a timed auto-commit job; incomplete edits must not be published automatically.
- Fetch/check the remote before pushing. If another contributor has advanced it, integrate safely and revalidate; do not force-push, reset, or overwrite their work. Respect an explicitly requested feature branch instead of switching branches behind the user's back.
- If tests, authentication, or a push fail, preserve the local work and clearly report that GitHub is not yet updated. Confirm the remote commit matches the local commit before reporting a successful update.

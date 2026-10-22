"""In-app setup, with explicit downloads and a responsive native tool installer."""

from pathlib import Path
import platform
import subprocess

from .preview_model import COLORS as C
from .setup_io import SetupCancelled, SetupError, load_selection, save_selection
from .setup_service import MODELS, SetupCleanupError, run_setup
from .transcription import TranscriptionCancelled, TranscriptionCleanupError
from .transcription_job import JobManager


def _request_apple_tools(cancel, emit):
    """Open Apple's own installer only in response to its dedicated button."""
    if platform.system() != "Darwin" or platform.machine().strip().lower() != "arm64":
        raise SetupError("Apple tools can only be installed on an Apple Silicon Mac.")
    if cancel.is_set():
        raise SetupCancelled()
    try:
        result = subprocess.run(["/usr/bin/xcode-select", "--install"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=15, check=False, text=True)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SetupError("Could not open Apple's installer. Try again from Install Apple tools.") from error
    if result.returncode:
        message = (result.stdout or "").strip()[-700:]
        raise SetupError("Apple's installer could not be opened. The tools may already be installed; "
                         "try Set Up on This Mac again.\n\n" + message)
    return {"apple_tools_requested": True}


def _setup_task(request, cancel, emit):
    try:
        return (_request_apple_tools(cancel, emit) if request == "apple_tools"
                else run_setup(request, cancel, emit))
    except SetupCleanupError as error:
        raise TranscriptionCleanupError(str(error)) from error
    except SetupCancelled as error:
        raise TranscriptionCancelled() from error


class SetupPanel:
    """Own the setup window, leaving the main-thread Editor available during builds."""

    def __init__(self, pipeline, *, manager=None):
        self.pipeline, self.ui = pipeline, pipeline.ui
        self.root, self.tk, self.ttk = self.ui.root, self.ui.tk, self.ui.ttk
        self.manager = manager if manager is not None else JobManager(_setup_task)
        self.window = None
        self.running = False
        self.poll_id = None
        self.close_when_done = None
        self.job_id = None
        self.operation = None
        self.error = ""
        self.controls = []
        self.summary = self.tk.StringVar(value="Set up the engine, a model, and media support here.")
        self.status = self.tk.StringVar(value="Ready to set up")
        self.detail = self.tk.StringVar(value="Choose a model, then start setup. Downloads begin only when you ask.")
        self.model_id = "base"
        self.model_choice = self.tk.StringVar(value=MODELS[self.model_id].title)
        self.download_note = self.tk.StringVar(value="")
        self.manual = {key: self.tk.StringVar(value="") for key in ("engine", "model", "converter")}
        self._bar_mode = None
        if not self.ui.windows_preview:
            self.apply_selection(load_selection())

    def apply_selection(self, selection):
        for key in ("engine", "model", "converter"):
            if key in selection:
                getattr(self.pipeline, key).set(selection[key])
                self.manual[key].set(selection[key])
        if selection.get("model_id") in MODELS:
            self.model_id = selection["model_id"]
            self.model_choice.set(MODELS[self.model_id].title)
        self.update_summary()

    def update_summary(self):
        if self.running:
            text = "Setup is running. You can keep using the Editor."
        elif self.pipeline.engine.get() and self.pipeline.model.get():
            model = Path(self.pipeline.model.get()).name
            if len(model) > 36:
                model = model[:24] + "…" + model[-11:]
            text = f"Selected model: {model}"
            if not self.pipeline.converter.get():
                text += " · media converter not selected"
        else:
            text = "Set up the engine, a model, and media support here."
        self.summary.set(text)

    def build_summary(self, form, row):
        self.ui._form_section(form, "Engine & models", row)
        label = self.ui.note(form, "", size=9)
        label.configure(textvariable=self.summary)
        label.grid(row=row+1, column=0, sticky="ew", pady=(0, 8))
        self.ui.note(form, "Download a model, prepare local tools, or use existing files. "
                     "Your setup is remembered on this Mac.", size=9).grid(row=row+2, column=0, sticky="ew")

    def open(self):
        if self.pipeline.blocked or self.pipeline.running:
            return
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return
        self.window = self.tk.Toplevel(self.root)
        self.window.title("Vidrick · Engine & Models")
        self.window.configure(bg=C["window"])
        self.window.geometry("660x580")
        self.window.minsize(600, 560)
        self.window.transient(self.root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        heading = self.tk.Frame(self.window, bg=C["window"], padx=22, pady=16)
        heading.grid(row=0, column=0, sticky="ew")
        self.ui.label(heading, "Engine & Models", bg=C["window"], size=16, bold=True).pack(anchor="w")
        self.ui.note(heading, "Prepare this Mac for local transcription.", bg=C["window"]).pack(fill="x", pady=(5, 0))
        self.notebook = self.ttk.Notebook(self.window, style="Preview.TNotebook")
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=22)
        automatic = self.tk.Frame(self.notebook, bg=C["card"], padx=18, pady=14)
        existing = self.tk.Frame(self.notebook, bg=C["card"], padx=18, pady=14)
        self.notebook.add(automatic, text="Easy setup")
        self.notebook.add(existing, text="Use existing files")
        self.controls = []
        self._build_automatic(automatic)
        self._build_existing(existing)
        footer = self.tk.Frame(self.window, bg=C["window"], padx=22, pady=14)
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.ui.label(footer, textvariable=self.status, bg=C["window"], size=11, bold=True,
                      wraplength=580).grid(row=0, column=0, columnspan=2, sticky="w")
        note = self.ui.note(footer, "", bg=C["window"], size=9)
        note.configure(textvariable=self.detail)
        note.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        self.progress = self.ttk.Progressbar(footer, mode="determinate", maximum=100, value=0,
                                             style="Preview.Horizontal.TProgressbar")
        self.progress.grid(row=2, column=0, sticky="ew", padx=(0, 12))
        buttons = self.tk.Frame(footer, bg=C["window"])
        buttons.grid(row=2, column=1, sticky="e")
        self.cancel_button = self.ui.button(buttons, "Cancel", self.cancel)
        self.details_button = self.ui.button(buttons, "Details…", self.show_error)
        self.close_button = self.ui.button(buttons, "Done", self.close)
        self.close_button.pack(side="right")
        self._bar_mode = None
        self._model_changed()
        self.refresh_controls()

    def _build_automatic(self, parent):
        parent.columnconfigure(0, weight=1)
        self.ui.label(parent, "Choose a multilingual model", size=11, bold=True).grid(row=0, column=0, sticky="w")
        selector = self.ttk.Combobox(parent, state="readonly", textvariable=self.model_choice,
                                     values=[item.title for item in MODELS.values()], style="Preview.TCombobox")
        selector.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        selector.bind("<<ComboboxSelected>>", self._model_changed)
        self.controls.append((selector, "readonly"))
        note = self.ui.note(parent, "", size=9)
        note.configure(textvariable=self.download_note)
        note.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.install_button = self.ui.button(parent, "Set Up on This Mac", self.start, primary=True)
        self.install_button.grid(row=3, column=0, sticky="w", pady=(0, 10))
        self.controls.append((self.install_button, "normal"))
        self.ui.note(parent, "Downloads come from GitHub, Hugging Face, and FFmpeg. "
                     "Tools are built locally; your recordings stay on your Mac. "
                     "Setup may take several minutes and needs free disk space.", size=9).grid(
                         row=4, column=0, sticky="ew", pady=(0, 10))
        self.apple_button = self.ui.button(parent, "Install Apple tools…", self.install_apple_tools)
        self.apple_button.grid(row=5, column=0, sticky="w")
        self.controls.append((self.apple_button, "normal"))
        self.ui.note(parent, "Use this if setup reports missing Apple developer tools. "
                     "Finish Apple's installer, then run setup again.", size=9).grid(row=6, column=0, sticky="ew", pady=(6, 0))

    def _build_existing(self, parent):
        parent.columnconfigure(0, weight=1)
        for index, (key, title) in enumerate((("engine", "Whisper executable"), ("model", "Whisper model (.bin)"),
                                              ("converter", "FFmpeg (optional for PCM WAV)"))):
            self.manual[key].set(getattr(self.pipeline, key).get())
            self.ui.label(parent, title, size=10).grid(row=index*2, column=0, sticky="w", pady=(0, 4))
            entry = self.ttk.Entry(parent, textvariable=self.manual[key], state="readonly", width=30,
                                   style="Preview.TEntry")
            entry.grid(row=index*2+1, column=0, sticky="ew", pady=(0, 10), padx=(0, 8))
            button = self.ui.button(parent, "Choose…", lambda k=key: self.choose_existing(k))
            button.grid(row=index*2+1, column=1, pady=(0, 10))
            self.controls.append((button, "normal"))
        self.apply_button = self.ui.button(parent, "Use These Files", self.use_existing, primary=True)
        self.apply_button.grid(row=6, column=0, sticky="w", pady=(4, 8))
        self.controls.append((self.apply_button, "normal"))
        self.ui.note(parent, "These paths are saved only when you choose Use These Files. "
                     "The files themselves stay in their original folders.", size=9).grid(row=7, column=0, columnspan=2, sticky="ew")

    def _model_changed(self, event=None):
        option = next((item for item in MODELS.values() if item.title == self.model_choice.get()), MODELS["base"])
        self.model_id = option.id
        model_mb = option.artifact.size / 1_000_000
        self.download_note.set(f"{option.description}\nModel download: {model_mb:.0f} MB. "
                               "First setup also downloads about 108 MB of tools and sources.")

    def refresh_controls(self):
        self.update_summary()
        if hasattr(self, "setup_button"):
            self.setup_button.configure(state="disabled" if self.pipeline.running else "normal")
        if self.window is None or not self.window.winfo_exists():
            return
        disabled = self.running or self.pipeline.running or self.ui.windows_preview
        for control, normal in self.controls:
            control.configure(state="disabled" if disabled else normal)
        self.cancel_button.pack_forget()
        self.details_button.pack_forget()
        if self.running and self.operation != "apple_tools":
            self.cancel_button.configure(text="Cancelling…" if self.manager.cancelling else "Cancel",
                                         state="disabled" if self.manager.cancelling else "normal")
            self.cancel_button.pack(side="left", padx=(0, 6))
        elif self.error:
            self.details_button.pack(side="left", padx=(0, 6))
        if self.ui.windows_preview:
            self.status.set("Windows UI preview")
            self.detail.set("Setup downloads, installs, and saved settings are available on Apple Silicon Macs only.")

    def start(self):
        return self._start(self.model_id)

    def install_apple_tools(self):
        return self._start("apple_tools")

    def _start(self, request):
        if self.ui.windows_preview or self.running or self.pipeline.running or self.pipeline.blocked:
            return False
        try:
            if not self.manager.start(request):
                return False
        except Exception as error:
            self.error = str(error)
            self.status.set("Setup could not start")
            self.detail.set("Open Details, then try again.")
            self.refresh_controls()
            return False
        self.running = True
        self.operation = request
        self.job_id = None
        self.error = ""
        self.status.set("Opening Apple's installer…" if request == "apple_tools" else "Preparing setup…")
        self.detail.set("Keep this window open. You can continue editing your transcript.")
        self._set_progress(None)
        self.pipeline.update_controls()
        self._schedule()
        return True

    def _set_progress(self, percent):
        mode = "indeterminate" if percent is None else "determinate"
        if mode != self._bar_mode:
            self.progress.stop()
            self.progress.configure(mode=mode, value=0)
            self._bar_mode = mode
            if percent is None:
                self.progress.start(15)
        if percent is not None:
            self.progress.configure(value=percent)

    def _schedule(self):
        if self.poll_id is None:
            self.poll_id = self.root.after(100, self._poll)

    def _poll(self):
        self.poll_id = None
        finished = False
        for event in self.manager.drain():
            if self.job_id is None:
                self.job_id = event.job_id
            if event.job_id != self.job_id:
                continue
            if event.kind == "progress":
                if not self.manager.cancelling:
                    value = event.payload
                    self.status.set(value.message)
                    self.detail.set(f"{value.percent}% · current step" if value.percent is not None else
                                    "Working locally. You can continue editing your transcript.")
                    self._set_progress(value.percent)
            else:
                finished = True
                self.running = False
                self._set_progress(0)
                if event.kind == "completed":
                    if event.payload.get("apple_tools_requested"):
                        self.status.set("Apple's installer opened")
                        self.detail.set("Finish Apple's installer, then choose Set Up on This Mac. "
                                        "Closing Vidrick does not stop Apple's installer.")
                    else:
                        try:
                            # Commit selection only after completion wins the
                            # cancellation race. Worker output alone isn't a commit.
                            save_selection(event.payload)
                        except (SetupError, OSError) as error:
                            self.error = str(error)
                            self.close_when_done = None
                            self.status.set("Could not save setup")
                            self.detail.set("Your previous setup is unchanged. Open Details, then retry setup.")
                        else:
                            self.apply_selection(event.payload)
                            self.status.set("Setup complete")
                            self.detail.set("Engine, model, and media support are ready. Your choices are saved on this Mac.")
                            self._set_progress(100)
                elif event.kind == "cancelled":
                    self.status.set("Setup cancelled")
                    self.detail.set("Your previous setup is unchanged. Verified downloads are kept for retry.")
                else:
                    self.error = str(event.payload)
                    self.close_when_done = None
                    self.status.set("Setup needs attention")
                    self.detail.set("Open Details for the next step. If Apple tools are missing, use Install Apple tools above.")
        if finished:
            self.pipeline.update_controls()
        if self.running:
            self._schedule()
        elif self.close_when_done:
            action = self.close_when_done
            self.close_when_done = None
            if action == "app":
                self.ui.close_preview()
            else:
                self.close()

    def cancel(self):
        if self.running and self.operation != "apple_tools" and self.manager.cancel():
            self.status.set("Cancelling setup…")
            self.detail.set("Stopping local work and removing incomplete files.")
            self._set_progress(None)
            self.refresh_controls()

    def allow_close(self, *, app=False):
        if self.pipeline.blocked:
            return False
        if not self.running:
            return True
        if self.operation == "apple_tools":
            self.detail.set("Waiting for Apple's installer to open. Please try closing again in a moment.")
            return False
        if self.close_when_done or not self.pipeline._ask("Setup is running", "Cancel setup and close?",
                "Wait for downloads and local builds to stop. Your previous setup will be kept."):
            return False
        if self.running:
            self.close_when_done = "app" if app else "window"
            self.cancel()
            return False
        return not bool(self.error)

    def close(self):
        if self.allow_close():
            if self.window is not None:
                self.window.destroy()
                self.window = None

    def choose_existing(self, key):
        if self.ui.windows_preview or self.running or self.pipeline.running or self.pipeline.blocked:
            return
        self.ui._file_dialog_open = True
        try:
            path = self.ui.filedialog.askopenfilename(parent=self.window, title=f"Choose local {key}",
                filetypes=[("Whisper model", "*.bin")] if key == "model" else [("All files", "*")])
            if path:
                self.manual[key].set(path)
        finally:
            self.ui._file_dialog_open = False

    def use_existing(self):
        if self.ui.windows_preview or self.running or self.pipeline.running or self.pipeline.blocked:
            return False
        selection = {key: value.get() for key, value in self.manual.items()}
        if not selection["engine"] or not selection["model"]:
            self.status.set("Choose an executable and model first")
            return False
        try:
            for key, value in selection.items():
                if value and (not Path(value).is_absolute() or not Path(value).is_file()):
                    raise SetupError(f"Choose an existing local {key} file.")
            save_selection(selection)
        except (SetupError, OSError) as error:
            self.error = str(error)
            self.status.set("Could not save setup")
            self.detail.set("Your previous setup is unchanged. Open Details for the error.")
            self.refresh_controls()
            return False
        self.apply_selection(selection)
        self.error = ""
        self.status.set("Existing files selected")
        self.detail.set("Paths saved. The engine and model will be checked when transcription starts.")
        self.pipeline.update_controls()
        return True

    def show_error(self):
        if not self.error or self.pipeline.blocked:
            return
        self.ui._file_dialog_open = True
        try:
            self.ui.editor_panel._messages().showerror("Setup needs attention", self.error, parent=self.window or self.root)
        finally:
            self.ui._file_dialog_open = False

    def dispose(self):
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        if self.window is not None:
            self.progress.stop()
            self.window.destroy()
            self.window = None

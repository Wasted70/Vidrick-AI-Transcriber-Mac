"""Tk coordinator for a single local job; all engine work stays off the UI thread."""

from .preview_model import COLORS as C, display_filename
from .transcription import JobSpec, TranscriptionError
from .transcription_job import JobManager


LANGUAGES = {"Auto-detect": "auto", "English": "en", "Spanish": "es", "French": "fr",
             "German": "de", "Portuguese": "pt", "Italian": "it", "Japanese": "ja"}


class TranscriptionPanel:
    def __init__(self, ui, *, manager=None):
        self.ui, self.root, self.tk, self.ttk = ui, ui.root, ui.tk, ui.ttk
        self.manager = manager if manager is not None else JobManager()
        self.running = False
        self.pending = None
        self.poll_id = None
        self.close_after_cancel = False
        self.job_id = None
        self.error = ""
        self.active_filename = ""
        self.controls = []
        self.engine = self.tk.StringVar(value="")
        self.model = self.tk.StringVar(value="")
        self.converter = self.tk.StringVar(value="")
        self.language = self.tk.StringVar(value="Auto-detect")
        self.task = self.tk.StringVar(value="Transcribe")
        self.timestamps = self.tk.BooleanVar(value=True)
        self.readiness = self.tk.StringVar(value="")
        self.status = self.tk.StringVar(value="Ready when you are")
        self.detail = self.tk.StringVar(value="One selected recording at a time. Results open in the Editor.")
        self.bar_mode = None

    @property
    def blocked(self):
        editor = getattr(self.ui, "editor_panel", None)
        return self.ui._file_dialog_open or bool(editor and editor.busy)

    def build_settings(self, form):
        row = 0
        for title, variable, values in (("Language", self.language, tuple(LANGUAGES)),
                                        ("Task", self.task, ("Transcribe", "Translate to English"))):
            self.ui.label(form, title, size=10).grid(row=row, column=0, sticky="w", pady=(0, 5))
            control = self.ttk.Combobox(form, textvariable=variable, values=values,
                                        state="readonly", width=18, style="Preview.TCombobox")
            control.grid(row=row+1, column=0, sticky="ew", pady=(0, 12))
            self.controls.append((control, "readonly"))
            row += 2
        control = self.ttk.Checkbutton(form, text="Include timestamps", variable=self.timestamps,
                                       style="Preview.TCheckbutton")
        control.grid(row=row, column=0, sticky="w", pady=(0, 8))
        self.controls.append((control, "normal"))
        self.ui.note(form, "Save the finished text from the Editor. Speaker labels and insights are planned.",
                     size=9).grid(row=row+1, column=0, sticky="ew", pady=(0, 8))
        row += 2
        self.ui._form_section(form, "Local engine setup", row)
        row += 1
        for title, variable, kind in (("Whisper executable", self.engine, "engine"),
                                       ("Whisper model (.bin)", self.model, "model"),
                                       ("FFmpeg · optional for WAV", self.converter, "converter")):
            self.ui.label(form, title, size=10).grid(row=row, column=0, sticky="w", pady=(0, 5))
            control = self.ttk.Entry(form, textvariable=variable, state="readonly", width=18)
            control.grid(row=row+1, column=0, sticky="ew")
            button = self.ui.button(form, "Choose…", lambda v=variable, k=kind: self.choose_tool(v, k))
            button.grid(row=row+2, column=0, sticky="w", pady=(5, 12))
            self.controls.append((button, "normal"))
            variable.trace_add("write", lambda *_: self.update_controls())
            row += 3
        self.ui.note(form, "Choose existing local tools and a model. CPU processing on Apple Silicon. "
                     "Nothing downloads automatically; setup lasts for this session.", size=9).grid(
                         row=row, column=0, sticky="ew", pady=(0, 8))

    def build_start(self, parent):
        self.start_button = self.ui.button(parent, "Transcribe selected file", self.start, primary=True)
        self.start_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        label = self.ui.note(parent, "", size=9)
        label.configure(textvariable=self.readiness)
        label.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    def build_status(self, page):
        shell, body = self.ui.card(page, padding=14)
        shell.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        body.columnconfigure(0, weight=1)
        self.ui.label(body, textvariable=self.status, size=11, bold=True).grid(row=0, column=0, sticky="w")
        label = self.ui.note(body, "", size=9)
        label.configure(textvariable=self.detail)
        label.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        self.progress = self.ttk.Progressbar(body, mode="determinate", maximum=100, value=0,
                                             style="Preview.Horizontal.TProgressbar")
        self.progress.grid(row=2, column=0, sticky="ew")
        buttons = self.tk.Frame(body, bg=C["card"])
        buttons.grid(row=0, column=1, rowspan=3, sticky="e", padx=(14, 0))
        self.cancel_button = self.ui.button(buttons, "Cancel", self.cancel)
        self.open_button = self.ui.button(buttons, "Open in Editor", self.open_result, primary=True)
        self.discard_button = self.ui.button(buttons, "Discard result", self.discard_result)
        self.details_button = self.ui.button(buttons, "Details…", self.show_error)
        for button in (self.cancel_button, self.open_button, self.discard_button, self.details_button):
            button.pack(fill="x", pady=2)
            button.pack_forget()
        self.update_controls()

    def choose_tool(self, variable, kind):
        if self.blocked or self.running:
            return
        self.ui._file_dialog_open = True
        try:
            titles = {"engine": "Choose the local whisper-cli executable",
                      "model": "Choose a local Whisper GGML model", "converter": "Choose the local ffmpeg executable"}
            path = self.ui.filedialog.askopenfilename(parent=self.root, title=titles[kind],
                filetypes=[("GGML model", "*.bin")] if kind == "model" else [("All files", "*")])
            if path:
                variable.set(path)
        finally:
            self.ui._file_dialog_open = False

    def update_controls(self):
        if not hasattr(self, "start_button"):
            return
        count = len(self.ui._selected_file_indices())
        if self.ui.windows_preview:
            reason = "Windows UI preview · transcription requires an Apple Silicon Mac."
        elif self.running:
            reason = "This recording is running. You can keep using the Editor."
        elif self.pending is not None:
            reason = "Open or discard the finished result before starting another."
        elif count != 1:
            reason = "Select one recording to transcribe."
        elif not self.engine.get() or not self.model.get():
            reason = "Choose a local Whisper executable and model below."
        else:
            reason = "Ready · processes only the selected recording."
        ready = (not self.ui.windows_preview and not self.running and self.pending is None
                 and count == 1 and bool(self.engine.get()) and bool(self.model.get()))
        self.start_button.configure(state="normal" if ready else "disabled")
        self.readiness.set(reason)
        for control, normal in self.controls:
            control.configure(state="disabled" if self.running else normal)
        if not hasattr(self, "cancel_button"):
            return
        for button in (self.cancel_button, self.open_button, self.discard_button, self.details_button):
            button.pack_forget()
        if self.running:
            self.cancel_button.configure(text="Cancelling…" if self.manager.cancelling else "Cancel",
                                         state="disabled" if self.manager.cancelling else "normal")
            self.cancel_button.pack(fill="x", pady=2)
        elif self.pending is not None:
            self.open_button.pack(fill="x", pady=2)
            self.discard_button.pack(fill="x", pady=2)
        elif self.error:
            self.details_button.pack(fill="x", pady=2)

    def start(self):
        if self.ui.windows_preview or self.running or self.pending is not None or self.blocked:
            return False
        selected = self.ui._selected_file_indices()
        if len(selected) != 1 or not self.engine.get() or not self.model.get():
            self.update_controls()
            return False
        path = self.ui.state.files[selected[0]]
        spec = JobSpec(recording=path, engine=self.engine.get(), model=self.model.get(),
                       converter=self.converter.get(), language=LANGUAGES[self.language.get()],
                       translate=self.task.get() == "Translate to English", timestamps=self.timestamps.get())
        try:
            if not self.manager.start(spec):
                return False
        except TranscriptionError as exc:
            self.error = str(exc)
            self.status.set("Could not start transcription")
            self.detail.set("Open Details for the error, then try again.")
            self.update_controls()
            return False
        self.running = True
        self.error = ""
        self.job_id = None
        name = display_filename(path, windows_preview=self.ui.windows_preview)
        self.active_filename = name if len(name) <= 44 else name[:29] + "…" + name[-14:]
        self.status.set("Preparing recording")
        self.detail.set(self.active_filename)
        self._set_progress(None)
        self.ui._update_file_actions()
        self._schedule_poll()
        return True

    def _schedule_poll(self):
        if self.poll_id is None:
            self.poll_id = self.root.after(80, self._poll)

    def _set_progress(self, percent):
        mode = "indeterminate" if percent is None else "determinate"
        if mode != self.bar_mode:
            self.progress.stop()
            self.progress.configure(mode=mode, value=0)
            self.bar_mode = mode
            if percent is None:
                self.progress.start(15)
        if percent is not None:
            self.progress.configure(value=percent)

    def _poll(self):
        self.poll_id = None
        completed = False
        terminal = False
        for event in self.manager.drain():
            if self.job_id is None:
                self.job_id = event.job_id
            if event.job_id != self.job_id:
                continue
            if event.kind == "progress":
                if not self.manager.cancelling:
                    progress = event.payload
                    self.status.set(progress.message + (f" · {progress.percent}%" if progress.percent is not None else ""))
                    self._set_progress(progress.percent)
            else:
                terminal = True
                self.running = False
                self._set_progress(0)
                if event.kind == "completed":
                    completed = True
                    result = event.payload
                    self._set_progress(100)
                    if result.text.strip():
                        self.pending = result
                        self.status.set("Transcript ready")
                        self.detail.set(f"{self.active_filename} · Open in the Editor to review and save.")
                        self.ui.tab_buttons["Transcribe"].configure(text="Transcribe •")
                    else:
                        self.status.set("Finished · no speech found")
                        self.detail.set("No transcript text was returned. Try another recording or language setting.")
                elif event.kind == "cancelled":
                    self.status.set("Transcription cancelled")
                    self.detail.set("Temporary files cleared. Your Editor document is unchanged.")
                else:
                    self.error = str(event.payload)
                    self.status.set("Transcription could not finish")
                    self.detail.set("Open Details for the error. Adjust the recording or setup, then try again.")
                    self.close_after_cancel = False
        if terminal:
            self.ui._update_file_actions()
        if self.running:
            self._schedule_poll()
        elif self.close_after_cancel:
            self.close_after_cancel = False
            self.ui.close_preview()
        elif completed and self.pending is not None and not self.blocked:
            self.open_result()

    def cancel(self):
        if self.running and self.manager.cancel():
            self.status.set("Cancelling transcription…")
            self.detail.set("Stopping the local process and clearing temporary files.")
            self._set_progress(None)
            self.update_controls()

    def open_result(self):
        if self.running or self.pending is None or self.blocked:
            return False
        result = self.pending
        if not self.ui.editor_panel.load_transcription(result.text, result.suggested_name):
            self.detail.set("Your finished result is waiting here while you keep the current Editor document.")
            return False
        self.pending = None
        self.ui.tab_buttons["Transcribe"].configure(text="Transcribe")
        self.status.set("Finished · opened in Editor")
        self.detail.set(f"{self.active_filename} · Save the transcript when you're ready.")
        self.update_controls()
        return True

    def _ask(self, title, message, detail):
        self.ui._file_dialog_open = True
        try:
            return self.ui.editor_panel._messages().askyesno(title, message, detail=detail,
                                                            parent=self.root, default="no")
        finally:
            self.ui._file_dialog_open = False

    def discard_result(self):
        if self.pending is None or self.blocked:
            return
        if self._ask("Discard finished transcript?", "This result has not been saved.",
                     "Discard it and allow another transcription? The current Editor document will stay open."):
            self.pending = None
            self.ui.tab_buttons["Transcribe"].configure(text="Transcribe")
            self.status.set("Finished result discarded")
            self.detail.set("Select a recording to start another transcription.")
            self._set_progress(0)
            self.update_controls()

    def show_error(self):
        if not self.error or self.blocked:
            return
        self.ui._file_dialog_open = True
        try:
            self.ui.editor_panel._messages().showerror("Transcription error", self.error, parent=self.root)
        finally:
            self.ui._file_dialog_open = False

    def allow_close(self):
        if self.blocked:
            return False
        if self.running:
            if self.close_after_cancel or not self._ask("Transcription is running", "Cancel transcription and close?",
                    "The window will stay open until the local process has stopped. Unsaved Editor changes will still be checked."):
                return False
            # Native dialogs run a nested event loop: the job may have finished
            # while the user considered the question. Recheck before arming close.
            if self.running:
                self.close_after_cancel = True
                self.cancel()
                return False
            if self.error:
                # A failure may also arrive inside the confirmation's event
                # loop. Leave its Details available instead of hiding it.
                return False
        if self.pending is not None:
            return self._ask("Unsaved finished transcript", "Discard the waiting result and close?",
                             "Choose No, then Open in Editor to review and save the finished transcript.")
        return True

    def dispose(self):
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        self.progress.stop()

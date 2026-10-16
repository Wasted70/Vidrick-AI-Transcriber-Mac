"""Local plain-text transcript editor; no recording or network access."""

from .preview_model import COLORS as C
from .transcript_io import TranscriptConflict, TranscriptError, read_transcript, write_transcript


class TranscriptEditor:
    MATCH_LIMIT = 2000

    def __init__(self, ui, parent):
        self.ui, self.root, self.tk, self.ttk = ui, ui.root, ui.tk, ui.ttk
        self.document = None
        self.saved_text = ""
        self.busy = False
        self.refresh_id = None
        self.search_visible = False
        self.matches = []
        self.active_match = None
        self.matches_limited = False
        self.document_label = self.tk.StringVar(value="Untitled.txt · Not saved")
        self.count_label = self.tk.StringVar(value="0 words · 0 characters")
        self.find_text = self.tk.StringVar(value="")
        self.replace_text = self.tk.StringVar(value="")
        self.match_case = self.tk.BooleanVar(value=False)
        self.match_label = self.tk.StringVar(value="Enter text to find")
        self.font_size = self.tk.StringVar(value="12")
        shell, body = ui.card(parent)
        shell.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)

        toolbar = self.tk.Frame(body, bg=C["card"])
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ui.label(toolbar, "Transcript", size=18, bold=True).pack(side="left")
        ui.button(toolbar, "Save As…", self.save_as).pack(side="right")
        self.save_button = ui.button(toolbar, "Save", self.save, primary=True)
        self.save_button.pack(side="right", padx=6)
        ui.button(toolbar, "Open…", self.open).pack(side="right")
        self.name_label = ui.label(body, textvariable=self.document_label, size=10, muted=True)
        self.name_label.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.name_label.bind("<Configure>", lambda event: self.name_label.configure(
            wraplength=max(100, event.width - 4)))

        tools = self.tk.Frame(body, bg=C["card"])
        tools.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self.undo_button = ui.button(tools, "Undo", self.undo)
        self.undo_button.pack(side="left")
        self.redo_button = ui.button(tools, "Redo", self.redo)
        self.redo_button.pack(side="left", padx=6)
        self.undo_button.configure(state="disabled")
        self.redo_button.configure(state="disabled")
        ui.button(tools, "Find & Replace", self.show_find).pack(side="left")
        size_box = self.ttk.Combobox(tools, textvariable=self.font_size,
                                    values=("12", "14", "16", "18", "20", "24"),
                                    state="readonly", width=3, style="Preview.TCombobox")
        size_box.pack(side="right")
        size_box.bind("<<ComboboxSelected>>", self.change_font_size)
        ui.label(tools, "Text size", size=10, muted=True).pack(side="right", padx=(8, 6))

        self._build_find(body)
        frame, self.text = ui._text_area(body, "", editable=True)
        frame.grid(row=4, column=0, sticky="nsew")
        self.text.configure(autoseparators=True, maxundo=1000)
        self.text.tag_configure("find_match", background="#fff0bb", foreground=C["text"])
        self.text.tag_configure("find_current", background=C["selected"], foreground=C["text"])
        self.text.bind("<<Modified>>", self._modified)
        for key in ("z", "Z"):
            self.text.bind(f"<{ui.primary_key}-{key}>", self.undo)
            self.text.bind(f"<{ui.primary_key}-Shift-{key}>", self.redo)
        if ui.windows_preview:
            self.text.bind("<Control-y>", self.redo)
        self.text.edit_reset()
        self.text.edit_modified(False)
        ui.label(body, textvariable=self.count_label, size=9, muted=True).grid(
            row=5, column=0, sticky="w", pady=(10, 0))
        self.find_text.trace_add("write", self._query_changed)
        self.match_case.trace_add("write", self._query_changed)

    def _build_find(self, body):
        ui = self.ui
        self.find_panel = self.tk.Frame(body, bg=C["card"])
        self.find_panel.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.find_panel.columnconfigure(1, weight=1)
        ui.label(self.find_panel, "Find", size=10).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.find_entry = self.ttk.Entry(self.find_panel, textvariable=self.find_text,
                                         width=10, font=(ui.family, 11))
        self.find_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=3)
        ui.button(self.find_panel, "Previous", self.find_previous).grid(row=0, column=2, padx=(0, 5))
        ui.button(self.find_panel, "Next", self.find_next).grid(row=0, column=3, padx=(0, 5))
        ui.button(self.find_panel, "Done", self.hide_find).grid(row=0, column=4)
        ui.label(self.find_panel, "Replace", size=10).grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.replace_entry = self.ttk.Entry(self.find_panel, textvariable=self.replace_text,
                                            width=10, font=(ui.family, 11))
        self.replace_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=3)
        ui.button(self.find_panel, "Replace", self.replace_current).grid(row=1, column=2, padx=(0, 5))
        ui.button(self.find_panel, "Replace All", self.replace_all).grid(
            row=1, column=3, columnspan=2, sticky="ew")
        self.ttk.Checkbutton(self.find_panel, text="Match case", variable=self.match_case,
                             style="Preview.TCheckbutton").grid(row=2, column=0, columnspan=2, sticky="w")
        ui.label(self.find_panel, textvariable=self.match_label, size=9, muted=True,
                 wraplength=260).grid(row=2, column=2, columnspan=3, sticky="e")
        for entry in (self.find_entry, self.replace_entry):
            entry.bind("<Return>", self.find_next)
            entry.bind("<Shift-Return>", self.find_previous)
            entry.bind("<Escape>", self.hide_find)
        self.find_panel.grid_remove()

    def content(self):
        return self.text.get("1.0", "end-1c")

    @property
    def dirty(self):
        return self.content() != self.saved_text

    @property
    def blocked(self):
        return self.busy or self.ui._file_dialog_open

    def _messages(self):
        if self.ui.messagebox is None:
            from tkinter import messagebox
            return messagebox
        return self.ui.messagebox

    def _modified(self, event=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._schedule_refresh()

    def _schedule_refresh(self):
        self.cancel_refresh()
        self.refresh_id = self.root.after(120, self.refresh)

    def cancel_refresh(self):
        if self.refresh_id is not None:
            self.root.after_cancel(self.refresh_id)
            self.refresh_id = None

    def refresh(self):
        self.cancel_refresh()
        text = self.content()
        name = self.document.path.name if self.document else "Untitled.txt"
        if len(name) > 70:
            name = name[:45] + "…" + name[-20:]
        dirty = text != self.saved_text
        state = "Unsaved changes" if dirty else ("Saved" if self.document else "Not saved")
        self.document_label.set(f"{'• ' if dirty else ''}{name} · {state}")
        words, characters = len(text.split()), len(text)
        self.count_label.set(f"{words:,} {'word' if words == 1 else 'words'} · "
                             f"{characters:,} {'character' if characters == 1 else 'characters'}")
        for button, operation in ((self.undo_button, "canundo"), (self.redo_button, "canredo")):
            button.configure(state="normal" if self.text.edit(operation) else "disabled")
        if self.search_visible:
            self._refresh_matches()

    def _confirm_changes(self, action):
        if not self.dirty:
            return True
        name = self.document.path.name if self.document else "Untitled.txt"
        answer = self._messages().askyesnocancel(
            "Save changes?", f"Save changes to {name} before {action}?",
            detail="Choose Yes to save, No to discard your changes, or Cancel to keep editing.",
            parent=self.root, default="cancel")
        if answer is None:
            return False
        return self._save() if answer else True

    def confirm_close(self):
        if self.blocked:
            return False
        self.busy = True
        try:
            return self._confirm_changes("closing")
        finally:
            self.busy = False

    def open(self):
        if self.blocked:
            return False
        self.busy = True
        try:
            path = self.ui.filedialog.askopenfilename(
                parent=self.root, title="Open transcript", filetypes=[("Text transcripts", "*.txt")])
            if not path or not self._confirm_changes("opening another transcript"):
                return False
            try:
                document = read_transcript(path)
            except TranscriptError as exc:
                self._messages().showerror("Could not open transcript", str(exc), parent=self.root)
                return False
            self.text.delete("1.0", "end")
            self.text.insert("1.0", document.text)
            self.text.edit_reset()
            self.text.edit_modified(False)
            self.document = document
            self.saved_text = document.text
            self.active_match = None
            self.text.mark_set("insert", "1.0")
            self.text.yview_moveto(0)
            self.ui.select_tab("Editor")
            self.text.focus_set()
            self.refresh()
            return True
        finally:
            self.busy = False

    def save_as(self):
        return self.save(save_as=True)

    def save(self, *, save_as=False):
        if self.blocked:
            return False
        self.busy = True
        try:
            return self._save(save_as=save_as)
        finally:
            self.busy = False

    def _save(self, *, save_as=False):
        path = self.document.path if self.document and not save_as else None
        if path is None:
            path = self.ui.filedialog.asksaveasfilename(
                parent=self.root, title="Save transcript", defaultextension=".txt",
                initialfile=self.document.path.name if self.document else "Transcript.txt",
                filetypes=[("Text transcripts", "*.txt")], confirmoverwrite=True)
            if not path:
                return False
        text = self.content()
        try:
            try:
                document = write_transcript(path, text, previous=self.document)
            except TranscriptConflict as exc:
                if not self._messages().askyesno(
                    "Transcript changed on disk", str(exc),
                    detail="Replace the file on disk with the text currently in the Editor? "
                           "Choose No to keep editing or use Save As for a separate copy.",
                    parent=self.root, default="no"):
                    return False
                document = write_transcript(path, text, previous=self.document, overwrite_changed=True)
        except TranscriptError as exc:
            self._messages().showerror("Could not save transcript", str(exc), parent=self.root)
            return False
        self.document = document
        self.saved_text = text
        self.text.edit_separator()
        self.refresh()
        return True

    def undo(self, event=None):
        return self._history("undo")

    def redo(self, event=None):
        return self._history("redo")

    def _history(self, operation):
        if not self.blocked:
            self.text.edit_separator()
            try:
                self.text.edit(operation)
            except self.tk.TclError:
                pass
            self.text.edit_separator()
            self.refresh()
        return "break"

    def change_font_size(self, event=None):
        size = int(self.font_size.get())
        if size in (12, 14, 16, 18, 20, 24):
            self.text.configure(font=(self.ui.family, size))

    def show_find(self, event=None):
        if self.blocked:
            return "break"
        self.ui.select_tab("Editor")
        selection = self.text.tag_ranges("sel")
        if selection:
            selected = self.text.get(selection[0], selection[1])
            if "\n" not in selected and len(selected) <= 200:
                self.find_text.set(selected)
        self.search_visible = True
        self.find_panel.grid()
        self._refresh_matches()
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, "end")
        return "break"

    def hide_find(self, event=None):
        self.search_visible = False
        self.find_panel.grid_remove()
        self.text.tag_remove("find_match", "1.0", "end")
        self.text.tag_remove("find_current", "1.0", "end")
        self.text.focus_set()
        return "break"

    def _query_changed(self, *args):
        self.active_match = None
        if self.search_visible:
            self._schedule_refresh()

    def _refresh_matches(self):
        self.text.tag_remove("find_match", "1.0", "end")
        self.text.tag_remove("find_current", "1.0", "end")
        self.matches = []
        self.matches_limited = False
        query = self.find_text.get()
        if not query:
            self.active_match = None
            self.match_label.set("Enter text to find")
            return
        start = "1.0"
        while True:
            found = self.text.search(query, start, stopindex="end-1c", exact=True,
                                     nocase=not self.match_case.get())
            if not found:
                break
            if len(self.matches) == self.MATCH_LIMIT:
                self.matches_limited = True
                break
            # Tk 8.6 search -count reports UTF-16 units for emoji, while +Nc
            # moves by Unicode characters. Literal matches use query length.
            end = self.text.index(f"{found}+{len(query)}c")
            self.matches.append((str(found), end))
            self.text.tag_add("find_match", found, end)
            start = end
        if self.active_match not in self.matches:
            self.active_match = None
        self._highlight_active()

    def _highlight_active(self):
        self.text.tag_remove("find_current", "1.0", "end")
        if self.active_match:
            self.text.tag_add("find_current", *self.active_match)
            self.text.tag_raise("find_current")
            self.text.tag_raise("sel")
        count = len(self.matches)
        if self.matches_limited:
            self.match_label.set(f"{count:,}+ matches · refine search")
        elif self.active_match:
            self.match_label.set(f"{self.matches.index(self.active_match) + 1} of {count:,} matches")
        else:
            self.match_label.set(f"{count:,} {'match' if count == 1 else 'matches'}")

    def find_next(self, event=None):
        return self._navigate_match(1)

    def find_previous(self, event=None):
        return self._navigate_match(-1)

    def _navigate_match(self, direction):
        self._refresh_matches()
        if self.matches:
            if self.active_match:
                index = (self.matches.index(self.active_match) + direction) % len(self.matches)
            else:
                index = 0 if direction > 0 else len(self.matches) - 1
            self.active_match = self.matches[index]
            self.text.see(self.active_match[0])
            self._highlight_active()
        return "break"

    def replace_current(self):
        if self.blocked:
            return
        self._refresh_matches()
        if not self.active_match:
            self.find_next()
        if not self.active_match:
            return
        start, end = self.active_match
        self._replace_ranges([(start, end)])
        self.active_match = next((match for match in self.matches
                                  if self.text.compare(match[0], ">=", "insert")),
                                 self.matches[0] if self.matches else None)
        self._highlight_active()
        if self.active_match:
            self.text.see(self.active_match[0])

    def replace_all(self):
        if self.blocked:
            return
        self._refresh_matches()
        if self.matches_limited:
            self.busy = True
            try:
                self._messages().showinfo("Refine your search",
                    f"More than {self.MATCH_LIMIT:,} matches were found. Use a longer search term "
                    "before replacing all. No text has been changed.", parent=self.root)
            finally:
                self.busy = False
            return
        if self.matches:
            self._replace_ranges(list(reversed(self.matches)))

    def _replace_ranges(self, ranges):
        replacement = self.replace_text.get()
        self.text.edit_separator()
        self.text.configure(autoseparators=False)
        try:
            for start, end in ranges:
                self.text.replace(start, end, replacement)
            self.text.mark_set("insert", f"{ranges[-1][0]}+{len(replacement)}c")
        finally:
            self.text.configure(autoseparators=True)
            self.text.edit_separator()
        self.active_match = None
        self.refresh()

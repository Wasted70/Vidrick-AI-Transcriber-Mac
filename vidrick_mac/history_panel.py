"""Local transcript history and explicit recovery actions for the Mac UI."""

from datetime import datetime

from .history_store import HistoryError
from .preview_model import COLORS as C


def _date_label(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%b %d · %H:%M")
    except (AttributeError, TypeError, ValueError):
        return "Unknown date"


def _model_label(value):
    return {"ggml-tiny.bin": "Tiny", "ggml-base.bin": "Base",
            "ggml-small.bin": "Small"}.get(value, value or "—")


class HistoryPanel:
    def __init__(self, ui, parent):
        self.ui, self.root = ui, ui.root
        self.busy = False
        self.disposed = False
        self.refresh_id = None
        self.entries, self.drafts = {}, {}
        self.query = ui.tk.StringVar(value="")
        self.status = ui.tk.StringVar(value="")
        self.detail = ui.tk.StringVar(value="Open a saved result as an editable copy.")
        self.recovery_status = ui.tk.StringVar(value="")

        shell, body = ui.card(parent, padding=16)
        shell.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ui.label(body, "Recent transcriptions", size=18, bold=True).grid(
            row=0, column=0, sticky="w", pady=(0, 6))
        search = ui.tk.Frame(body, bg=C["card"])
        search.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        search.columnconfigure(1, weight=1)
        ui.label(search, "Search", size=10, muted=True).grid(row=0, column=0, padx=(0, 8))
        self.search_entry = ui.ttk.Entry(search, textvariable=self.query, width=16,
                                         font=(ui.family, 11))
        self.search_entry.grid(row=0, column=1, sticky="ew")
        ui.button(search, "Refresh", self.retry_refresh).grid(row=0, column=2, padx=(8, 0))

        listing = ui.tk.Frame(body, bg=C["card"])
        listing.grid(row=2, column=0, sticky="nsew")
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.tree = ui.ttk.Treeview(listing, columns=("date", "model"), show="tree headings",
                                    selectmode="browse", height=3, style="Preview.Treeview")
        self.tree.heading("#0", text="Transcript", anchor="w")
        self.tree.column("#0", width=250, minwidth=120, stretch=True)
        self.tree.heading("date", text="Created", anchor="w")
        self.tree.column("date", width=120, minwidth=110, stretch=False)
        self.tree.heading("model", text="Model", anchor="w")
        self.tree.column("model", width=100, minwidth=65, stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ui.ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", self._open_row)
        self.tree.bind("<Return>", self._open_key)
        self.tree.bind("<Delete>", self._delete_key)
        status = ui.label(body, textvariable=self.status, size=10, muted=True, anchor="w")
        status.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        status.bind("<Configure>", lambda event: status.configure(wraplength=max(100, event.width - 4)))
        detail = ui.label(body, textvariable=self.detail, size=9, muted=True, anchor="w")
        detail.grid(row=4, column=0, sticky="ew", pady=(2, 6))
        detail.bind("<Configure>", lambda event: detail.configure(wraplength=max(100, event.width - 4)))
        actions = ui.tk.Frame(body, bg=C["card"])
        actions.grid(row=5, column=0, sticky="ew")
        self.open_button = ui.button(actions, "Open in Editor", self.open_selected, primary=True)
        self.open_button.pack(side="left")
        self.rename_button = ui.button(actions, "Rename…", self.rename_selected)
        self.rename_button.pack(side="left", padx=6)
        self.delete_button = ui.button(actions, "Delete…", self.delete_selected)
        self.delete_button.pack(side="left")

        self.recovery_frame = ui.tk.Frame(body, bg=C["card"])
        self.recovery_frame.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        self.recovery_frame.columnconfigure(0, weight=1)
        ui.tk.Frame(self.recovery_frame, bg=C["border"], height=1).grid(
            row=0, column=0, sticky="ew", pady=(0, 6))
        recovery_actions = ui.tk.Frame(self.recovery_frame, bg=C["card"])
        recovery_actions.grid(row=1, column=0, sticky="ew")
        recovery_title = ui.tk.Frame(recovery_actions, bg=C["card"])
        recovery_title.pack(side="left")
        ui.label(recovery_title, "Recover unsaved work", size=11, bold=True).pack(anchor="w")
        ui.label(recovery_title, textvariable=self.recovery_status, size=9,
                 muted=True).pack(anchor="w")
        self.discard_button = ui.button(recovery_actions, "Discard…", self.discard_selected)
        self.discard_button.pack(side="right")
        self.restore_button = ui.button(recovery_actions, "Restore", self.restore_selected)
        self.restore_button.pack(side="right", padx=6)
        recovery_list = ui.tk.Frame(self.recovery_frame, bg=C["card"])
        recovery_list.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        recovery_list.columnconfigure(0, weight=1)
        self.recovery_tree = ui.ttk.Treeview(
            recovery_list, columns=("date",), show="tree headings", selectmode="browse",
            height=1, style="Preview.Treeview")
        self.recovery_tree.heading("#0", text="Draft", anchor="w")
        self.recovery_tree.heading("date", text="Last saved", anchor="w")
        self.recovery_tree.column("#0", width=300, minwidth=100, stretch=True)
        self.recovery_tree.column("date", width=120, minwidth=110, stretch=False)
        self.recovery_tree.grid(row=0, column=0, sticky="ew")
        self.recovery_scroll = ui.ttk.Scrollbar(recovery_list, orient="vertical",
                                              command=self.recovery_tree.yview)
        self.recovery_scroll.grid(row=0, column=1, sticky="ns")
        self.recovery_tree.configure(yscrollcommand=self.recovery_scroll.set)
        self.recovery_tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.recovery_tree.bind("<Return>", self._restore_key)
        self.query_trace = self.query.trace_add("write", self._query_changed)
        self.refresh()

    @property
    def blocked(self):
        return (self.disposed or self.busy or self.ui._file_dialog_open or
                self.ui.editor_panel.blocked or self.ui.history_store is None)

    @property
    def has_recovery(self):
        return bool(self.drafts)

    def retry_refresh(self):
        if self.disposed or self.busy or self.ui._file_dialog_open or self.ui.editor_panel.blocked:
            return
        if self.ui.history_store is None:
            self.ui.ensure_history_store()
        self.refresh()

    def _messages(self):
        if self.ui.messagebox is not None:
            return self.ui.messagebox
        from tkinter import messagebox
        return messagebox

    def _query_changed(self, *args):
        if self.disposed:
            return
        self._cancel_refresh()
        self.refresh_id = self.root.after(120, self.refresh)

    def _cancel_refresh(self):
        if self.refresh_id is not None:
            self.root.after_cancel(self.refresh_id)
            self.refresh_id = None

    def refresh(self):
        if self.disposed:
            return
        self._cancel_refresh()
        if self.busy:
            return
        store = self.ui.history_store
        query = self.query.get()
        query = query.strip() if isinstance(query, str) else ""
        if store is None:
            self._replace_rows(self.tree, (), "entry")
            self._replace_rows(self.recovery_tree, (), "draft")
            self.entries, self.drafts = {}, {}
            self.recovery_frame.grid_remove()
            self.status.set(self.ui.history_error or "Local history is unavailable.")
            self._selection_changed()
            return
        try:
            entries = store.list_entries(query)
            drafts = [draft for draft in store.list_drafts()
                      if draft.id != self.ui.editor_panel.current_draft_id]
        except HistoryError as exc:
            self.status.set(f"Could not read local history: {exc}")
            self._selection_changed()
            return
        self.entries = {entry.id: entry for entry in entries}
        self.drafts = {draft.id: draft for draft in drafts}
        self._replace_rows(self.tree, entries, "entry")
        self._replace_rows(self.recovery_tree, drafts, "draft")
        if drafts:
            self.recovery_frame.grid()
            self.recovery_status.set(f"{len(drafts)} recovery {'draft' if len(drafts) == 1 else 'drafts'}")
        else:
            self.recovery_frame.grid_remove()
        if entries:
            self.status.set(f"{len(entries)} saved {'transcript' if len(entries) == 1 else 'transcripts'}")
        elif query:
            self.status.set("No matching transcripts. Try another title or model.")
        else:
            self.status.set("Completed transcriptions will be saved here automatically.")
        self._selection_changed()

    def _replace_rows(self, tree, records, kind):
        selected = tree.selection()
        yview = tree.yview()
        children = tree.get_children()
        if children:
            tree.delete(*children)
        for item in records:
            values = ((_date_label(item.created_at), _model_label(item.model)) if kind == "entry"
                      else (_date_label(item.updated_at),))
            tree.insert("", "end", iid=item.id, text=item.title, values=values)
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])
            tree.focus(selected[0])
        elif records:
            tree.selection_set(records[0].id)
            tree.focus(records[0].id)
        if yview:
            tree.yview_moveto(yview[0])

    def _selection_changed(self, event=None):
        if self.disposed:
            return
        entry = self._selected(self.tree, self.entries)
        draft = self._selected(self.recovery_tree, self.drafts)
        allowed = not self.blocked
        for button in (self.open_button, self.rename_button, self.delete_button):
            button.configure(state="normal" if allowed and entry else "disabled")
        for button in (self.restore_button, self.discard_button):
            button.configure(state="normal" if allowed and draft else "disabled")
        self.detail.set(f"Language: {entry.language or 'Auto'} · Opens as an editable copy."
                        if entry else "Open a saved result as an editable copy.")

    @staticmethod
    def _selected(tree, records):
        selection = tree.selection()
        return records.get(selection[0]) if selection else None

    def _modal(self, callback):
        self.busy = True
        self.ui._file_dialog_open = True
        try:
            return callback()
        finally:
            self.ui._file_dialog_open = False
            self.busy = False

    def _still_available(self, store):
        return not self.blocked and self.ui.history_store is store

    def _failure(self, action, exc):
        if self.disposed:
            return
        self.refresh()
        self.status.set(f"Could not {action}: {exc}")

    def open_selected(self):
        if self.blocked:
            return False
        entry = self._selected(self.tree, self.entries)
        if entry is None:
            return False
        store = self.ui.history_store
        try:
            entry = store.get_entry(entry.id)
        except HistoryError as exc:
            self._failure("open transcript", exc)
            return False
        self.busy = True
        try:
            return self.ui.editor_panel.load_transcription(entry.text, entry.suggested_name)
        finally:
            self.busy = False
            self.refresh()

    def rename_selected(self):
        if self.blocked:
            return False
        entry = self._selected(self.tree, self.entries)
        if entry is None:
            return False
        store = self.ui.history_store
        from tkinter import simpledialog
        title = self._modal(lambda: simpledialog.askstring(
            "Rename transcript", "Name in History:", initialvalue=entry.title, parent=self.root))
        if title is None or not self._still_available(store):
            self.refresh()
            return False
        try:
            store.rename_entry(entry.id, title)
        except HistoryError as exc:
            self._failure("rename transcript", exc)
            return False
        self.refresh()
        return True

    def delete_selected(self):
        if self.blocked:
            return False
        entry = self._selected(self.tree, self.entries)
        if entry is None:
            return False
        store = self.ui.history_store
        confirmed = self._modal(lambda: self._messages().askyesno(
            "Delete saved transcript?", f'Delete “{entry.title}” from History?',
            detail="This removes the saved History copy. Recordings and current Editor text are kept.",
            parent=self.root, default="no"))
        if not confirmed or not self._still_available(store):
            self.refresh()
            return False
        try:
            store.delete_entry(entry.id)
        except HistoryError as exc:
            self._failure("delete transcript", exc)
            return False
        pipeline = getattr(self.ui, "pipeline", None)
        if pipeline is not None and hasattr(pipeline, "history_entry_deleted"):
            pipeline.history_entry_deleted(entry.id)
        self.refresh()
        return True

    def restore_selected(self):
        if self.blocked:
            return False
        draft = self._selected(self.recovery_tree, self.drafts)
        if draft is None:
            return False
        store = self.ui.history_store
        confirmed = self._modal(lambda: self._messages().askyesno(
            "Restore recovery draft?", f'Restore “{draft.title}” in the Editor?',
            detail="You can review and save the recovered text. Any current edits are checked first.",
            parent=self.root, default="yes"))
        if not confirmed or not self._still_available(store):
            self.refresh()
            return False
        try:
            draft = store.get_draft(draft.id)
        except HistoryError as exc:
            self._failure("restore draft", exc)
            return False
        self.busy = True
        try:
            return self.ui.editor_panel.recover_draft(draft)
        finally:
            self.busy = False
            self.refresh()

    def discard_selected(self):
        if self.blocked:
            return False
        draft = self._selected(self.recovery_tree, self.drafts)
        if draft is None:
            return False
        store = self.ui.history_store
        confirmed = self._modal(lambda: self._messages().askyesno(
            "Discard recovery draft?", f'Permanently discard “{draft.title}”?',
            detail="This deletes the unsaved recovery text. Saved transcripts and recordings are kept.",
            parent=self.root, default="no"))
        if not confirmed or not self._still_available(store):
            self.refresh()
            return False
        if draft.id == self.ui.editor_panel.current_draft_id:
            self.refresh()
            return False
        try:
            store.delete_draft(draft.id)
        except HistoryError as exc:
            self._failure("discard draft", exc)
            return False
        self.refresh()
        return True

    def _open_row(self, event):
        if self.tree.identify_row(event.y):
            self.open_selected()
        return "break"

    def _open_key(self, event=None):
        self.open_selected()
        return "break"

    def _delete_key(self, event=None):
        self.delete_selected()
        return "break"

    def _restore_key(self, event=None):
        self.restore_selected()
        return "break"

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self._cancel_refresh()
        self.query.trace_remove("write", self.query_trace)

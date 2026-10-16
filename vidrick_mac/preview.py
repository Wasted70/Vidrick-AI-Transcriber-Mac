"""Independent offline Mac UI with local plain-text transcript editing."""

from . import __version__
from .environment import inspect_environment
from .editor import TranscriptEditor
from .preview_model import COLORS as C, PREVIEW_TABS, PreviewState, display_filename
from .preview_style import rounded_surface


class PreviewUI:
    """Recording paths stay in memory; the Editor can open/save chosen text files."""

    def __init__(self, root, *, windows_preview, tk, ttk, filedialog, messagebox=None):
        self.root, self.tk, self.ttk, self.filedialog = root, tk, ttk, filedialog
        self.messagebox = messagebox
        self.windows_preview = windows_preview
        self.primary_key = "Control" if windows_preview else "Command"
        self.shortcut_label = "Ctrl+" if windows_preview else "⌘"
        self._file_dialog_open = False
        self.current_tab = None
        self.state = PreviewState()
        self.files_summary = tk.StringVar(value=self.state.summary)
        self.page_title = tk.StringVar(value="Transcribe")
        self.transparency = tk.BooleanVar(value=True)
        self.transparency_available = True
        self.style_images = []
        self.variables = []
        self.tab_buttons, self.tab_rows, self.tab_icons, self.pages = {}, {}, {}, {}
        self.tab_canvases, self.tab_fills = {}, {}
        self.family = root.tk.call("font", "actual", "TkDefaultFont", "-family")
        self._styles()
        root.configure(bg=C["window"])
        width = min(1180, max(940, root.winfo_screenwidth() - 80))
        height = min(800, max(640, root.winfo_screenheight() - 100))
        root.geometry(f"{width}x{height}")
        root.minsize(940, 640)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self._navigation()
        main = tk.Frame(root, bg=C["window"])
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self._header(main)
        self.content = tk.Frame(main, bg=C["window"])
        self.content.grid(row=1, column=0, sticky="nsew", padx=22, pady=20)
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        for name in PREVIEW_TABS:
            page = tk.Frame(self.content, bg=C["window"])
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self._transcribe(self.pages["Transcribe"])
        self._editor(self.pages["Editor"])
        self._insights(self.pages["Insights"])
        self._history(self.pages["History"])
        self._about(self.pages["About & Updates"])
        self._preview_notice(main)
        self.select_tab("Transcribe")
        self._install_shortcuts()
        self._apply_transparency()
        root.protocol("WM_DELETE_WINDOW", self.close_preview)
        if root.tk.call("tk", "windowingsystem") == "aqua":
            root.createcommand("::tk::mac::Quit", self.close_preview)

    def _styles(self):
        style = self.ttk.Style(self.root)
        # Preserve native Aqua controls on a Mac; style the Windows preview.
        themes = style.theme_names()
        native_theme = not self.windows_preview and "aqua" in themes
        style.theme_use("aqua" if native_theme else "clam")
        style.configure("Preview.Treeview", font=(self.family, 11, "bold"), rowheight=32)
        if native_theme:
            # Aqua owns control colors; a custom white foreground can disappear
            # on its native light button face, which ignores background colors.
            for name in ("Preview.TButton", "Primary.Preview.TButton", "Preview.TCombobox",
                         "Preview.TCheckbutton", "Header.Preview.TCheckbutton"):
                style.configure(name, font=(self.family, 11))
            return
        style.configure("Preview.TButton", font=(self.family, 10), padding=(12, 6),
                        background=C["card"], foreground=C["text"],
                        bordercolor=C["border"], lightcolor=C["card"], darkcolor=C["border"],
                        focuscolor=C["accent"], borderwidth=1, relief="flat")
        style.map("Preview.TButton", background=[("disabled", C["panel"]), ("active", C["card_hover"])],
                  foreground=[("disabled", C["muted"])])
        style.configure("Primary.Preview.TButton", background=C["accent"], foreground=C["on_accent"],
                        bordercolor=C["accent"], lightcolor=C["accent"], darkcolor=C["accent"],
                        focuscolor=C["on_accent"])
        style.map("Primary.Preview.TButton",
                  background=[("disabled", C["panel"]), ("active", C["accent_hover"])],
                  foreground=[("disabled", C["muted"]), ("!disabled", C["on_accent"])],
                  bordercolor=[("disabled", C["border"])])
        style.configure("Preview.TCombobox", foreground=C["text"], fieldbackground=C["card"],
                        background=C["window"], arrowcolor=C["muted"], bordercolor=C["border"],
                        lightcolor=C["border"], darkcolor=C["border"], padding=5, font=(self.family, 10))
        style.map("Preview.TCombobox", fieldbackground=[("readonly", C["card"])],
                  foreground=[("disabled", C["muted"]), ("readonly", C["text"])],
                  selectbackground=[("readonly", C["card"])], selectforeground=[("readonly", C["text"])],
                  bordercolor=[("focus", C["accent"])])
        style.configure("Preview.TCheckbutton", background=C["card"], foreground=C["text"],
                        font=(self.family, 10), focuscolor=C["accent"], padding=(0, 4))
        style.map("Preview.TCheckbutton", background=[("active", C["card"])])
        style.configure("Header.Preview.TCheckbutton", background=C["header"])
        style.map("Header.Preview.TCheckbutton", background=[("active", C["header"])])
        style.configure("Preview.Treeview", background=C["card"], fieldbackground=C["card"],
                        foreground=C["text"], bordercolor=C["border"], borderwidth=1,
                        lightcolor=C["border"], darkcolor=C["border"])
        style.map("Preview.Treeview", background=[("selected", C["selected"])],
                  foreground=[("selected", C["text"])], bordercolor=[("focus", C["accent"])])
        self._bubble_buttons(style)
        for orientation in ("Vertical", "Horizontal"):
            style.configure(f"Preview.{orientation}.TScrollbar", background=C["border"],
                            troughcolor=C["card"], bordercolor=C["card"], arrowcolor=C["muted"],
                            lightcolor=C["card"], darkcolor=C["card"], arrowsize=12, width=12)
        self.root.option_add("*TCombobox*Listbox.background", C["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["selected"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", C["text"])

    def _bubble_buttons(self, style):
        """Keep ttk interaction semantics while rounding the preview theme."""
        for name, normal, hover, edge in (
            ("Preview.TButton", "#eef3fc", C["card_hover"], C["border"]),
            ("Primary.Preview.TButton", C["accent"], C["accent_hover"], C["accent"]),
        ):
            images = []
            for fill, border in ((normal, edge), (hover, edge), (hover, C["accent"]),
                                 (C["panel"], C["border"]), (normal, C["accent"])):
                image = self.tk.PhotoImage(master=self.root, data=rounded_surface(fill, border))
                self.style_images.append(image)
                images.append(image)
            element = f"{name}.bubble"
            style.element_create(element, "image", images[0],
                                 ("disabled", images[3]), ("pressed", images[2]),
                                 ("focus", images[4]), ("active", images[1]),
                                 border=18, padding=0, sticky="nsew")
            style.layout(name, [(element, {"sticky": "nsew", "children": [
                ("Button.focus", {"sticky": "nsew", "children": [
                    ("Button.padding", {"sticky": "nsew", "children": [
                        ("Button.label", {"sticky": "nsew"})]})]})]})])
            # Transparent image corners must reveal the surface, never a
            # rectangular accent-colored ttk background underneath the pill.
            style.configure(name, padding=(14, 7), background=C["card"])
            style.map(name, background=[("disabled", C["card"]), ("!disabled", C["card"])])

    def _apply_transparency(self):
        """Whole-window opacity only; never blur or read the desktop behind it."""
        desired = 0.97 if self.transparency.get() else 1.0
        try:
            self.root.attributes("-alpha", desired)
            applied = float(self.root.attributes("-alpha"))
            available = abs(applied - desired) < 0.01
        except self.tk.TclError:
            available = False
        if not available:
            self.transparency_available = False
            self.transparency.set(False)
            try:
                self.root.attributes("-alpha", 1.0)
            except self.tk.TclError:
                pass
            self.transparency_control.configure(state="disabled", takefocus=False, text="Transparency unavailable")

    def label(self, parent, text="", *, bg=None, size=11, bold=False, muted=False, **kwargs):
        options = dict(text=text, bg=bg or C["card"],
                       fg=C["muted"] if muted else C["text"],
                       font=(self.family, size, "bold" if bold else "normal"),
                       anchor="w", justify="left", borderwidth=0)
        options.update(kwargs)
        return self.tk.Label(parent, **options)

    def note(self, parent, text, *, bg=None, size=10, centered=False):
        """Keep explanatory copy inside its available width at laptop sizes."""
        label = self.label(parent, text, bg=bg, size=size, muted=True,
                           anchor="center" if centered else "w",
                           justify="center" if centered else "left", wraplength=240)
        label.bind("<Configure>", lambda event: label.configure(wraplength=max(100, event.width - 4)))
        return label

    def button(self, parent, text, command=None, *, primary=False, disabled=False):
        options = dict(text=text, style="Primary.Preview.TButton" if primary else "Preview.TButton",
                       takefocus=False if disabled else "ttk::takefocus")
        if disabled:
            options["state"] = "disabled"
        elif command is not None:
            options["command"] = command
        return self.ttk.Button(parent, **options)

    @staticmethod
    def _round_rect(canvas, x, y, w, h, radius, **options):
        radius = min(radius, (w-x)/2, (h-y)/2)
        r = radius
        return canvas.create_polygon(x+r, y, w-r, y, w, y, w, y+r,
                                     w, h-r, w, h, w-r, h, x+r, h,
                                     x, h, x, h-r, x, y+r, x, y,
                                     smooth=True, splinesteps=32, **options)

    def card(self, parent, *, fill=None, background=None, padding=20):
        """Soft glass-like surface; opacity is managed by the window separately."""
        fill = fill or C["card"]
        shell = self.tk.Frame(parent, bg=background or C["window"])
        canvas = self.tk.Canvas(shell, bg=background or C["window"], highlightthickness=0)
        canvas.place(x=0, y=0, relwidth=1, relheight=1)
        body = self.tk.Frame(shell, bg=fill)
        body.pack(fill="both", expand=True, padx=padding, pady=padding)

        def paint(event):
            w, h = event.width - 1, event.height - 1
            if w < 4 or h < 4:
                return
            canvas.delete("card")
            self._round_rect(canvas, 1, 4, w, h, 44, fill=C["shadow"], outline="", tags="card")
            self._round_rect(canvas, 1, 1, w, h-3, 44, fill=fill,
                             outline=C["border"], width=1, tags="card")
            self._round_rect(canvas, 2, 2, w-1, h-4, 42, fill="",
                             outline=C["highlight"], width=1, tags="card")
        canvas.bind("<Configure>", paint)
        return shell, body

    def _icon(self, parent, kind, *, size=24, bg=None):
        """Original line icons; no platform symbol-font dependency."""
        canvas = self.tk.Canvas(parent, width=size, height=size, bg=bg or C["card"],
                                highlightthickness=0, takefocus=False)
        scale = size / 24

        def line(*points):
            canvas.create_line(*(p * scale for p in points), fill=C["accent"],
                               width=max(1.5, 1.5 * scale), capstyle="round", joinstyle="round")

        if kind == "wave":
            for x, top in ((4, 10), (8, 6), (12, 3), (16, 7), (20, 10)):
                line(x, top, x, 24-top)
        elif kind == "editor":
            line(6, 3, 18, 3, 18, 21, 6, 21, 6, 3)
            for y in (8, 12, 16):
                line(9, y, 15, y)
        elif kind == "insights":
            line(12, 3, 14.5, 9.5, 21, 12, 14.5, 14.5, 12, 21, 9.5, 14.5, 3, 12, 9.5, 9.5, 12, 3)
        else:
            canvas.create_oval(3*scale, 3*scale, 21*scale, 21*scale,
                               outline=C["accent"], width=max(1.5, 1.5*scale))
            if kind == "history":
                line(12, 7, 12, 12, 16, 14)
            else:
                line(12, 11, 12, 17)
                line(12, 7, 12, 7.5)
        return canvas

    def _navigation(self):
        sidebar = self.tk.Frame(self.root, bg=C["sidebar"], width=194)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)
        brand = self.tk.Frame(sidebar, bg=C["sidebar"])
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 27))
        self._icon(brand, "wave", size=30, bg=C["sidebar"]).pack(side="left", padx=(0, 10))
        words = self.tk.Frame(brand, bg=C["sidebar"])
        words.pack(side="left")
        self.label(words, "Vidrick", bg=C["sidebar"], size=17, bold=True).pack(anchor="w")
        self.label(words, "for Mac", bg=C["sidebar"], size=10, muted=True).pack(anchor="w")
        group = self.tk.Frame(sidebar, bg=C["sidebar"])
        group.grid(row=1, column=0, sticky="ew", padx=8)
        self.label(group, "Workspace", bg=C["sidebar"], size=9, bold=True, muted=True).pack(
            anchor="w", padx=12, pady=(0, 8))
        bottom = self.tk.Frame(sidebar, bg=C["sidebar"])
        bottom.grid(row=3, column=0, sticky="ew", padx=8, pady=(10, 18))
        for name, kind in zip(PREVIEW_TABS, ("wave", "editor", "insights", "history", "info")):
            shell = self.tk.Frame(bottom if name == "About & Updates" else group, bg=C["sidebar"])
            shell.pack(fill="x", pady=3)
            canvas = self.tk.Canvas(shell, bg=C["sidebar"], highlightthickness=0)
            canvas.place(x=0, y=0, relwidth=1, relheight=1)
            self.tab_canvases[name], self.tab_fills[name] = canvas, C["sidebar"]

            def paint(event, tab=name, surface=canvas):
                surface.delete("pill")
                self._round_rect(surface, 0, 0, event.width, event.height, 36,
                                 fill=self.tab_fills[tab], outline="", tags="pill")

            canvas.bind("<Configure>", paint)
            canvas.bind("<Button-1>", lambda event, tab=name: self.select_tab(tab))
            row = self.tk.Frame(shell, bg=C["sidebar"])
            row.pack(fill="x", padx=12, pady=4)
            icon = self._icon(row, kind, size=19, bg=C["sidebar"])
            icon.pack(side="left", padx=(0, 6))
            icon.bind("<Button-1>", lambda event, tab=name: self.select_tab(tab))
            button = self.tk.Button(row, text=name, command=lambda tab=name: self.select_tab(tab),
                                    font=(self.family, 10), bg=C["sidebar"], fg=C["text"],
                                    activebackground=C["selected"], activeforeground=C["text"],
                                    relief="flat", borderwidth=0, anchor="w", padx=2, pady=7,
                                    highlightthickness=1, highlightbackground=C["sidebar"],
                                    highlightcolor=C["accent"], takefocus=True)
            button.pack(side="left", fill="x", expand=True)
            self.tab_buttons[name], self.tab_rows[name], self.tab_icons[name] = button, row, icon
        self.label(bottom, f"Interface preview  {__version__}", bg=C["sidebar"], size=9, muted=True).pack(
            anchor="w", padx=10, pady=(12, 0))

    def _header(self, parent):
        shell, toolbar = self.card(parent, fill=C["header"], padding=12)
        shell.grid(row=0, column=0, sticky="ew", padx=22, pady=(16, 0))
        toolbar.columnconfigure(0, weight=1)
        self.label(toolbar, textvariable=self.page_title, bg=C["header"], size=14, bold=True).grid(
            row=0, column=0, sticky="w")
        self.transparency_control = self.ttk.Checkbutton(
            toolbar, text="Transparency", variable=self.transparency, command=self._apply_transparency,
            style="Header.Preview.TCheckbutton", takefocus=True)
        self.transparency_control.grid(row=0, column=1, padx=(12, 16))
        self.button(toolbar, "Settings", self.show_settings).grid(row=0, column=2, sticky="e")

    def _preview_notice(self, parent):
        notice = self.tk.Frame(parent, bg=C["header"], padx=22, pady=9)
        notice.grid(row=2, column=0, sticky="ew")
        mode = ("Windows development preview - not a Mac compatibility test" if self.windows_preview
                else "Apple Silicon interface preview - not a production release")
        self.note(notice, mode, bg=C["header"], size=9).pack(fill="x")
        self.shortcut_notice = self.tk.StringVar(value="")
        self.label(notice, textvariable=self.shortcut_notice, bg=C["header"], size=9, muted=True).pack(
            fill="x", pady=(2, 0))

    def select_tab(self, name):
        old_page = self.pages.get(self.current_tab)
        focused = self.root.focus_get()
        focus_was_hidden = old_page is not None and old_page != self.pages[name] and (
            focused is old_page or str(focused).startswith(str(old_page) + "."))
        for tab, page in self.pages.items():
            if tab == name:
                page.grid()
            else:
                page.grid_remove()
        self.current_tab = name
        if hasattr(self, "shortcut_notice"):
            self.shortcut_notice.set(
                f"{self.shortcut_label}O  Open transcript    ·    {self.shortcut_label}S  Save    ·    {self.shortcut_label}F  Find"
                if name == "Editor" else
                f"{self.shortcut_label}O  Add files    ·    {self.shortcut_label},  Settings    ·    Recordings stay unopened.")
        self.page_title.set(name)
        for tab, button in self.tab_buttons.items():
            color = C["selected"] if name == tab else C["sidebar"]
            button.configure(bg=color, activebackground=color, highlightbackground=color,
                             font=(self.family, 10, "bold" if name == tab else "normal"))
            self.tab_fills[tab] = color
            self.tab_canvases[tab].itemconfigure("pill", fill=color)
            self.tab_rows[tab].configure(bg=color)
            self.tab_icons[tab].configure(bg=color)
        if focus_was_hidden:
            self.tab_buttons[name].focus_set()

    def _install_shortcuts(self):
        # A window-local tag before class bindings prevents Text's Ctrl+O
        # editing behavior from running before the file-picker shortcut.
        tag = f"VidrickPreviewShortcuts{id(self)}"
        self.shortcut_tag = tag
        for key in ("o", "O"):
            self.root.bind_class(tag, f"<{self.primary_key}-{key}>", self._open_files_shortcut)
        self.root.bind_class(tag, f"<{self.primary_key}-comma>", self._settings_shortcut)
        for key in ("f", "F"):
            self.root.bind_class(tag, f"<{self.primary_key}-{key}>", self.editor_panel.show_find)
        for key in ("s", "S"):
            self.root.bind_class(tag, f"<{self.primary_key}-{key}>", self._save_shortcut)
            self.root.bind_class(tag, f"<{self.primary_key}-Shift-{key}>", self._save_as_shortcut)

        def attach(widget):
            tags = widget.bindtags()
            widget.bindtags((tags[0], tag, *tags[1:]) if tags else (tag,))
            for child in widget.winfo_children():
                attach(child)

        attach(self.root)
        for key in ("a", "A"):
            self.file_list.bind(f"<{self.primary_key}-{key}>", self._select_all_files)
        for key in ("<Delete>", "<BackSpace>"):
            self.file_list.bind(key, self._remove_files_shortcut)

    def _open_files_shortcut(self, event=None):
        if self.current_tab == "Editor":
            self.editor_panel.open()
        else:
            self.choose_files()
        return "break"

    def _save_shortcut(self, event=None):
        if not self.editor_panel.blocked:
            self.select_tab("Editor")
            self.editor_panel.save()
        return "break"

    def _save_as_shortcut(self, event=None):
        if not self.editor_panel.blocked:
            self.select_tab("Editor")
            self.editor_panel.save_as()
        return "break"

    def _settings_shortcut(self, event=None):
        if not self.editor_panel.blocked:
            self.show_settings()
        return "break"

    def _select_all_files(self, event=None):
        items = self.file_list.get_children()
        self.file_list.selection_set(items)
        if items and not self.file_list.focus():
            self.file_list.focus(items[0])
        self._update_file_actions()
        return "break"

    def _remove_files_shortcut(self, event=None):
        self.remove_selected()
        return "break"

    def show_settings(self):
        self.select_tab("Transcribe")
        self.settings_canvas.yview_moveto(0)
        self.settings_canvas.focus_set()

    def close_preview(self):
        if self.editor_panel.confirm_close():
            self.editor_panel.cancel_refresh()
            self.root.destroy()

    def _transcribe(self, page):
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        self.label(page, "New transcription", bg=C["window"], size=22, bold=True).grid(
            row=0, column=0, sticky="w", pady=(0, 18))
        workspace = self.tk.Frame(page, bg=C["window"])
        workspace.grid(row=1, column=0, sticky="nsew")
        workspace.columnconfigure(0, weight=1)
        workspace.columnconfigure(1, minsize=294)
        workspace.rowconfigure(0, weight=1)
        left_shell, left = self.card(workspace)
        left_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        heading = self.tk.Frame(left, bg=C["card"])
        heading.grid(row=0, column=0, sticky="ew")
        self.label(heading, "Files", size=13, bold=True).pack(side="left")
        self.add_files_button = self.button(heading, "Add Files…", self.choose_files)
        self.add_files_button.pack(side="right")
        self.label(left, textvariable=self.files_summary, size=9, muted=True,
                   wraplength=280).grid(row=1, column=0, sticky="w", pady=(8, 12))
        self.file_area = self.tk.Frame(left, bg=C["card"])
        self.file_area.grid(row=2, column=0, sticky="nsew")
        self.file_area.columnconfigure(0, weight=1)
        self.file_area.rowconfigure(0, weight=1)
        self.list_frame = self.tk.Frame(self.file_area, bg=C["card"])
        self.list_frame.grid(row=0, column=0, sticky="nsew")
        self.list_frame.columnconfigure(0, weight=1)
        self.list_frame.rowconfigure(0, weight=1)
        self.file_list = self.ttk.Treeview(self.list_frame, show="tree", selectmode="extended",
                                          height=3, takefocus=True, style="Preview.Treeview")
        self.file_list.column("#0", width=240, minwidth=120, stretch=True)
        self.file_list.grid(row=0, column=0, sticky="nsew")
        self.file_list.bind("<<TreeviewSelect>>", self._update_file_actions)
        vertical = self.ttk.Scrollbar(self.list_frame, orient="vertical", command=self.file_list.yview,
                                      style="Preview.Vertical.TScrollbar")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = self.ttk.Scrollbar(self.list_frame, orient="horizontal", command=self.file_list.xview,
                                        style="Preview.Horizontal.TScrollbar")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.file_list.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.path_caption = self.tk.StringVar(value="Select a file to see its full path")
        self.label(self.list_frame, textvariable=self.path_caption, size=9, muted=True).grid(
            row=2, column=0, sticky="w", pady=(10, 4))
        self.path_detail = self.tk.Text(self.list_frame, height=3, width=1, wrap="word",
                                        font=(self.family, 9), bg=C["card"], fg=C["muted"],
                                        selectbackground=C["selected"], selectforeground=C["text"],
                                        relief="flat", borderwidth=0, padx=4, pady=4,
                                        highlightthickness=1, highlightbackground=C["border"],
                                        highlightcolor=C["accent"], state="disabled", takefocus=False,
                                        exportselection=False)
        self.path_detail.grid(row=3, column=0, sticky="ew")
        self.path_detail.bind("<Tab>", self._path_focus_next)
        self.path_detail.bind("<Shift-Tab>", self._path_focus_previous)
        detail_scroll = self.ttk.Scrollbar(self.list_frame, orient="vertical", command=self.path_detail.yview,
                                          style="Preview.Vertical.TScrollbar")
        detail_scroll.grid(row=3, column=1, sticky="ns")
        self.path_detail.configure(yscrollcommand=detail_scroll.set)
        self.empty_files = self.tk.Frame(self.file_area, bg=C["card"])
        self.empty_files.grid(row=0, column=0, sticky="nsew")
        self.empty_files.columnconfigure(0, weight=1)
        self.empty_files.rowconfigure((0, 2), weight=1)
        empty = self.tk.Frame(self.empty_files, bg=C["card"])
        empty.grid(row=1, column=0, sticky="ew", padx=8, pady=2)
        bubble = self.tk.Frame(empty, bg=C["card"])
        bubble.pack(pady=(0, 12))
        badge = self.tk.Canvas(bubble, width=64, height=64, bg=C["card"], highlightthickness=0)
        badge.pack()
        badge.create_oval(2, 4, 62, 64, fill=C["shadow"], outline="")
        badge.create_oval(2, 1, 62, 61, fill=C["accent_soft"], outline=C["highlight"], width=2)
        self._icon(bubble, "wave", size=34, bg=C["accent_soft"]).place(x=15, y=14)
        self.label(empty, "Start with a recording", size=14, bold=True, anchor="center").pack(fill="x")
        self.note(empty, "Choose audio or video files to try the layout.", centered=True).pack(fill="x", pady=(8, 14))
        self.choose_files_button = self.button(empty, "Choose Files…", self.choose_files, primary=True)
        self.choose_files_button.pack()
        self.note(empty, "Only file names and paths are selected.\nNothing is opened or processed.",
                  size=9, centered=True).pack(fill="x", pady=(14, 0))
        actions = self.tk.Frame(left, bg=C["card"])
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.remove_button = self.button(actions, "Remove", self.remove_selected)
        self.remove_button.pack(side="left")
        self.clear_button = self.button(actions, "Clear", self.clear_files)
        self.clear_button.pack(side="left", padx=8)
        right_shell, right = self.card(workspace)
        right_shell.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)
        self.label(right, "Transcription settings", size=12, bold=True).grid(row=0, column=0, sticky="w")
        self.label(right, "Preview selections", size=9, muted=True).grid(row=1, column=0, sticky="w", pady=(4, 16))
        self.settings_canvas = self.tk.Canvas(right, bg=C["card"], highlightthickness=1,
                                             highlightbackground=C["card"], highlightcolor=C["accent"],
                                             width=244, takefocus=True)
        self.settings_canvas.grid(row=2, column=0, sticky="nsew")
        scrollbar = self.ttk.Scrollbar(right, orient="vertical", command=self.settings_canvas.yview,
                                       style="Preview.Vertical.TScrollbar")
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.settings_canvas.configure(yscrollcommand=scrollbar.set)
        form = self.tk.Frame(self.settings_canvas, bg=C["card"], padx=2)
        form.columnconfigure(0, weight=1)
        item = self.settings_canvas.create_window((0, 0), window=form, anchor="nw")
        form.bind("<Configure>", lambda event: self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox("all")))
        self.settings_canvas.bind("<Configure>", lambda event: self.settings_canvas.itemconfigure(item, width=max(1, event.width-2)))
        self._settings_form(form)
        self._bind_settings_scroll(form)
        self.settings_canvas.bind("<MouseWheel>", self._scroll_settings)
        for key, direction in (("<Down>", 1), ("<Up>", -1), ("<Next>", 1), ("<Prior>", -1)):
            units = "pages" if key in ("<Next>", "<Prior>") else "units"
            self.settings_canvas.bind(key, lambda event, d=direction, u=units: self._settings_key(d, u))
        self.button(right, "Start Transcription - unavailable", primary=True, disabled=True).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self.note(right, "Transcription isn't connected yet.", size=9).grid(
            row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.refresh_files()

    def _settings_key(self, direction, units):
        self.settings_canvas.yview_scroll(direction, units)
        return "break"

    def _scroll_settings(self, event):
        if event.delta:
            steps = -int(event.delta / 120) if abs(event.delta) >= 120 else (-1 if event.delta > 0 else 1)
            self.settings_canvas.yview_scroll(steps, "units")
        return "break"

    def _bind_settings_scroll(self, widget):
        widget.bind("<MouseWheel>", self._scroll_settings)
        widget.bind("<FocusIn>", self._reveal_setting, add="+")
        for child in widget.winfo_children():
            self._bind_settings_scroll(child)

    def _reveal_setting(self, event):
        """Keep keyboard focus visible inside the scrolling settings pane."""
        if event.widget != self.root.focus_get():
            return
        canvas = self.settings_canvas
        bounds = canvas.bbox("all")
        if not canvas.winfo_ismapped() or not bounds or bounds[3] <= bounds[1]:
            return
        top = canvas.canvasy(0)
        height = canvas.winfo_height()
        child_top = event.widget.winfo_rooty() - canvas.winfo_rooty() + top
        child_bottom = child_top + event.widget.winfo_height()
        if child_top < top + 4:
            target = child_top - 4
        elif child_bottom > top + height - 4:
            target = child_bottom - height + 4
        else:
            return
        canvas.yview_moveto((target - bounds[1]) / (bounds[3] - bounds[1]))

    def _path_focus_next(self, event):
        event.widget.tk_focusNext().focus_set()
        return "break"

    def _path_focus_previous(self, event):
        event.widget.tk_focusPrev().focus_set()
        return "break"

    def _settings_form(self, form):
        row = 0
        for title, values, disabled in (
            ("Language", ["Auto-detect", "English", "Spanish", "French", "German", "Portuguese", "Italian", "Japanese"], False),
            ("Task", ["Transcribe", "Translate to English"], False),
            ("Quality", ["Balanced", "Fast", "Accurate"], False),
            ("Processor", ["CPU (planned Mac target)"], True),
        ):
            self.label(form, title, size=10).grid(row=row, column=0, sticky="w", pady=(0, 5))
            variable = self.tk.StringVar(value=values[0])
            self.variables.append(variable)
            self.ttk.Combobox(form, textvariable=variable, values=values,
                              state="disabled" if disabled else "readonly", width=18,
                              takefocus=not disabled, style="Preview.TCombobox").grid(
                                  row=row+1, column=0, sticky="ew", pady=(0, 14))
            row += 2
        self._form_section(form, "Export", row)
        row += 1
        self.label(form, "Output folder", size=10).grid(row=row, column=0, sticky="w", pady=(0, 5))
        self.button(form, "Not connected in this preview", disabled=True).grid(row=row+1, column=0, sticky="ew", pady=(0, 10))
        formats = self.tk.Frame(form, bg=C["card"])
        formats.grid(row=row+2, column=0, sticky="ew", pady=(0, 8))
        for index, title in enumerate(("TXT", "Word", "SRT", "VTT")):
            self._checkbox(formats, title, title == "TXT").grid(row=index//2, column=index%2, sticky="w", padx=(0, 24))
        row += 3
        self._checkbox(form, "Include timestamps", True).grid(row=row, column=0, sticky="w")
        row += 1
        self._form_section(form, "Insights", row)
        row += 1
        for title, checked in (("Create a summary", False), ("Extract action items", False), ("Identify speakers", True)):
            self._checkbox(form, title, checked).grid(row=row, column=0, sticky="w")
            row += 1
        self.label(form, "Expected speakers", size=10).grid(row=row, column=0, sticky="w", pady=(12, 5))
        speakers = self.tk.StringVar(value="Automatic")
        self.variables.append(speakers)
        self.ttk.Combobox(form, textvariable=speakers, values=["Automatic", "2", "3", "4", "5"],
                          state="readonly", width=18, style="Preview.TCombobox").grid(row=row+1, column=0, sticky="ew")
        self.note(form, "Temporary controls only. No models or downloads are connected.", size=9).grid(
            row=row+2, column=0, sticky="ew", pady=(14, 6))

    def _form_section(self, form, title, row):
        group = self.tk.Frame(form, bg=C["card"])
        group.grid(row=row, column=0, sticky="ew", pady=(10, 10))
        self.tk.Frame(group, bg=C["border"], height=1).pack(fill="x", pady=(0, 14))
        self.label(group, title, size=10, bold=True).pack(anchor="w")

    def _checkbox(self, parent, title, checked):
        variable = self.tk.BooleanVar(value=checked)
        self.variables.append(variable)
        return self.ttk.Checkbutton(parent, text=title, variable=variable,
                                    style="Preview.TCheckbutton", takefocus=True)

    def choose_files(self):
        if self._file_dialog_open or self.editor_panel.busy:
            return
        focused = self.root.focus_get()
        self._file_dialog_open = True
        try:
            paths = self.filedialog.askopenfilenames(
                parent=self.root, title="Choose files - preview only; files will not be read")
        finally:
            self._file_dialog_open = False
        if paths:
            previous_paths = set(self.state.files)
            added = self.state.add_files(paths)
            self.select_tab("Transcribe")
            if added:
                self.refresh_files(selected_paths=[path for path in paths if path not in previous_paths])
            self.file_list.focus_set()
        elif focused is not None and focused.winfo_exists():
            focused.focus_set()

    def _selected_file_indices(self):
        return sorted(int(item) for item in self.file_list.selection()
                      if str(item).isdigit() and 0 <= int(item) < len(self.state.files))

    def _update_file_actions(self, event=None):
        selected = self._selected_file_indices()
        self.remove_button.configure(state="normal" if selected else "disabled")
        self.clear_button.configure(state="normal" if self.state.files else "disabled")
        caption = ("Full path" if len(selected) == 1 else f"Full paths · {len(selected)} selected")
        self.path_caption.set(caption if selected else "Select a file to see its full path")
        self.path_detail.configure(state="normal", takefocus=bool(selected))
        self.path_detail.delete("1.0", "end")
        self.path_detail.insert("1.0", "\n".join(self.state.files[index] for index in selected))
        self.path_detail.configure(state="disabled")

    def remove_selected(self):
        selected = self._selected_file_indices()
        if not selected:
            return
        next_index = min(selected)
        self.state.remove_files(selected)
        next_paths = [self.state.files[min(next_index, len(self.state.files)-1)]] if self.state.files else []
        self.refresh_files(selected_paths=next_paths)
        (self.file_list if self.state.files else self.choose_files_button).focus_set()

    def clear_files(self):
        self.state.clear_files()
        self.refresh_files(selected_paths=[])
        self.choose_files_button.focus_set()

    def refresh_files(self, *, selected_paths=None):
        if selected_paths is None:
            selected_paths = [self.state.files[index] for index in self._selected_file_indices()]
        selected_paths = set(selected_paths)
        self.file_list.delete(*self.file_list.get_children())
        selected_items = []
        for index, path in enumerate(self.state.files):
            self.file_list.insert("", "end", iid=str(index), text=display_filename(
                path, windows_preview=self.windows_preview))
            if path in selected_paths:
                selected_items.append(str(index))
        self.file_list.selection_set(selected_items)
        if selected_items:
            self.file_list.focus(selected_items[0])
            self.file_list.see(selected_items[0])
        self.files_summary.set(self.state.summary)
        if self.state.files:
            self.empty_files.grid_remove()
            self.list_frame.grid()
        else:
            self.list_frame.grid_remove()
            self.empty_files.grid()
        self._update_file_actions()

    def _text_area(self, parent, text, *, editable=False):
        frame = self.tk.Frame(parent, bg=C["card"])
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        box = self.tk.Text(frame, bg=C["card"], fg=C["text"], insertbackground=C["accent"],
                           selectbackground=C["selected"], selectforeground=C["text"], wrap="word", width=20, height=4,
                           font=(self.family, 12), relief="flat", padx=18, pady=18, spacing1=4, spacing3=6,
                           highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"],
                           undo=editable, exportselection=False)
        box.grid(row=0, column=0, sticky="nsew")
        scroll = self.ttk.Scrollbar(frame, orient="vertical", command=box.yview, style="Preview.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        box.configure(yscrollcommand=scroll.set)
        box.insert("1.0", text)
        if not editable:
            box.configure(state="disabled")
        return frame, box

    def _editor(self, page):
        self.editor_panel = TranscriptEditor(self, page)
        self.editor = self.editor_panel.text

    def _insights(self, page):
        page.columnconfigure(0, weight=1)
        page.rowconfigure((1, 2), weight=1, uniform="insights")
        self.label(page, "A clearer view of your conversation", bg=C["window"], size=19, bold=True).grid(
            row=0, column=0, sticky="w", pady=(0, 18))
        for row, title, text in ((1, "Summary", "A summary of your transcript will appear here. No analysis has been run in this preview."),
                                  (2, "Action items & key points", "Find the next steps from a conversation here once transcription and insights are connected.")):
            shell, body = self.card(page)
            shell.grid(row=row, column=0, sticky="nsew", pady=(0, 14) if row == 1 else 0)
            self._icon(body, "insights", size=24).pack(anchor="w", pady=(0, 10))
            self.label(body, title, size=14, bold=True).pack(anchor="w", pady=(0, 10))
            self.note(body, text).pack(fill="x")

    def _history(self, page):
        shell, body = self.card(page)
        shell.pack(fill="both", expand=True)
        self.label(body, "Recent transcriptions", size=18, bold=True).pack(anchor="w")
        middle = self.tk.Frame(body, bg=C["card"])
        middle.pack(fill="both", expand=True, padx=20, pady=20)
        middle.columnconfigure(0, weight=1)
        middle.rowconfigure((0, 2), weight=1)
        empty = self.tk.Frame(middle, bg=C["card"])
        empty.grid(row=1, column=0, sticky="ew")
        self._icon(empty, "history", size=48).pack(pady=(0, 18))
        self.label(empty, "Your work will appear here", size=16, bold=True, anchor="center").pack(fill="x")
        self.note(empty, "No recent transcriptions. This preview doesn't run jobs or save history.",
                  centered=True).pack(fill="x", pady=(10, 0))

    def _about(self, page):
        shell, body = self.card(page)
        shell.pack(fill="both", expand=True)
        self._icon(body, "wave", size=48).pack(anchor="w", pady=(4, 16))
        self.label(body, "Vidrick for Mac", size=23, bold=True).pack(anchor="w")
        self.label(body, f"Interface prototype {__version__}", size=10, muted=True).pack(anchor="w", pady=(6, 20))
        self.note(body, "Designed for Apple Silicon. Native Mac testing is still required.").pack(fill="x", pady=(0, 12))
        self.note(body, "An independent, offline interface preview with a local text editor. "
                  "Transcription, model downloads, formatted exports, licensing, and automatic updates "
                  "are not connected.").pack(fill="x", pady=(0, 24))
        self.tk.Frame(body, bg=C["border"], height=1).pack(fill="x", pady=(0, 20))
        self.label(body, "Account & updates", size=13, bold=True).pack(anchor="w", pady=(0, 8))
        self.note(body, "No customer account or license is accessed in this preview.").pack(fill="x", pady=(0, 16))
        buttons = self.tk.Frame(body, bg=C["card"])
        buttons.pack(anchor="w")
        self.button(buttons, "Manage Account", disabled=True).pack(side="left", padx=(0, 10))
        self.button(buttons, "Check for Updates", disabled=True).pack(side="left")


def launch_preview(*, windows_preview: bool = False) -> None:
    environment = inspect_environment()
    issues = environment.windows_preview_issues() if windows_preview else environment.issues()
    if issues:
        raise RuntimeError(" ".join(issues))
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = tk.Tk()
    title = "Vidrick Mac - Windows Development Preview" if windows_preview else "Vidrick Mac Preview"
    root.title(f"{title} - {__version__}")
    try:
        PreviewUI(root, windows_preview=windows_preview, tk=tk, ttk=ttk, filedialog=filedialog)
        root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass

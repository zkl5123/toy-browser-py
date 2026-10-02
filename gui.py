"""P31 — tkinter GUI shell for toy-browser-py.

Zero external dependencies (tkinter is in the standard library).
Renders local HTML files (or inline HTML) via the existing
browser.render_document pipeline and displays the resulting
Bitmap as a scaled pixel image.

Usage:
    python gui.py                # open with demo.html
    python gui.py some.html      # open a specific file
    python gui.py "inline html"  # open inline HTML (if starts with <)

Keyboard shortcuts:
    Ctrl+L  — focus address bar
    Ctrl+T  — new tab
    Ctrl+W  — close current tab
    Ctrl+R  — reload
    Alt+Left / Alt+Right — back / forward
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog

# P31 — pure stdlib: we do NOT require Pillow.  tkinter PhotoImage
# supports PPM/PGM directly (P4 format).  We convert our Bitmap to
# PGM text, which PhotoImage can load.
import paint


class PageInstance:
    """One tab's page state: DOM tree, layout root, bitmap, URL, history."""

    def __init__(self) -> None:
        self.url: str = "about:blank"
        self.title: str = "New Tab"
        self.bitmap: paint.Bitmap | None = None
        self.back_stack: list[str] = []
        self.fwd_stack: list[str] = []
        self.width: int = 800
        self.height: int = 600

    def load_html(self, html: str, css: str = "", w: int = 800, h: int = 600):
        """Render *html* via the toy browser pipeline; store the bitmap."""
        import browser
        self.width = w
        self.height = h
        self.bitmap = browser.render_document(html, css, w, h)

    def load_file(self, path: str, w: int = 800, h: int = 600):
        """Read *path* and render it."""
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        self.url = path
        self.title = os.path.basename(path)
        self.load_html(html, "", w, h)

    def load_url(self, url: str, w: int = 800, h: int = 600):
        """Dispatch: local file, about: page, or inline HTML.

        Navigation history is managed by the *caller* (TabBrowser._navigate
        or _open_file).  Here we just render the target.
        """
        url = url.strip()
        if not url:
            return
        # about: pages
        if url.lower().startswith("about:"):
            self.url = url
            self.title = url
            self.bitmap = self._about_page(url, w, h)
            return
        # inline HTML (starts with <)
        if url.startswith("<"):
            self.url = url
            self.title = "Inline HTML"
            self.load_html(url, "", w, h)
            return
        # local file path
        if os.path.isfile(url):
            self.load_file(url, w, h)
            return
        # http/https — toy browser has no network (M2); show a stub.
        self.url = url
        self.title = url
        html = (
            f"<html><body><h1>Not supported yet</h1>"
            f"<p>Network loading (http/https) arrives in P21.</p>"
            f"<p>URL: {url}</p></body></html>"
        )
        self.load_html(html, "", w, h)

    def _about_page(self, url: str, w: int, h: int) -> paint.Bitmap:
        import browser
        lower = url.lower()
        if "blank" in lower:
            html = '<html><body style="background:#ffffff"></body></html>'
        elif "version" in lower:
            html = (
                '<html><body><h1>toy-browser-py</h1>'
                '<p>Version 1.0 (P0–P19)</p>'
                '<p>Pure standard-library Python toy browser.</p>'
                '</body></html>'
            )
        elif "history" in lower:
            items = "".join(
                f"<li>{u}</li>" for u in (self.back_stack + [self.url] + self.fwd_stack)
            )
            html = f"<html><body><h1>History</h1><ul>{items}</ul></body></html>"
        else:
            html = f"<html><body><h1>{url}</h1></body></html>"
        return browser.render_document(html, "", w, h)

    def go_back(self):
        if self.back_stack:
            self.fwd_stack.append(self.url)
            target = self.back_stack.pop()
            self._load_no_history(target, self.width, self.height)

    def go_forward(self):
        if self.fwd_stack:
            self.back_stack.append(self.url)
            target = self.fwd_stack.pop()
            self._load_no_history(target, self.width, self.height)

    def _load_no_history(self, url: str, w: int, h: int):
        """Load *url* without touching back/fwd stacks (internal)."""
        url = url.strip()
        if not url:
            return
        if url.lower().startswith("about:"):
            self.url = url
            self.title = url
            self.bitmap = self._about_page(url, w, h)
            return
        if url.startswith("<"):
            self.title = "Inline HTML"
            self.load_html(url, "", w, h)
            return
        if os.path.isfile(url):
            self.load_file(url, w, h)
            return
        self.url = url
        self.title = url
        html = (
            f"<html><body><h1>Not supported yet</h1>"
            f"<p>Network loading (http/https) arrives in P21.</p>"
            f"<p>URL: {url}</p></body></html>"
        )
        self.load_html(html, "", w, h)

    def reload(self):
        if self.bitmap is not None:
            self._load_no_history(self.url, self.width, self.height)


class TabBrowser:
    """Multi-tab browser shell built on tkinter."""

    def __init__(self, root: tk.Tk, initial_path: str | None = None) -> None:
        self.root = root
        self.tabs: list[PageInstance] = []
        self.current: int = 0

        # --- build UI ---
        self._build_menu()
        self._build_toolbar()
        self._build_tab_frame()
        self._build_canvas()
        self._build_statusbar()

        # --- initial tab ---
        page = PageInstance()
        if initial_path and os.path.isfile(initial_path):
            page.load_file(initial_path, 800, 600)
        else:
            page.url = "about:blank"
            page.title = "about:blank"
            page.bitmap = page._about_page("about:blank", 800, 600)
        self.tabs.append(page)
        self._ensure_page_frame(0)
        self._show_tab(0)

        # --- bindings ---
        self.root.bind("<Control-l>", lambda e: self._focus_address())
        self.root.bind("<Control-t>", lambda e: self._new_tab())
        self.root.bind("<Control-w>", lambda e: self._close_tab())
        self.root.bind("<Control-r>", lambda e: self._reload())
        self.root.bind("<Alt-Left>", lambda e: self._back())
        self.root.bind("<Alt-Right>", lambda e: self._forward())

    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open…", command=self._open_file)
        file_menu.add_command(label="New Tab", accelerator="Ctrl+T", command=self._new_tab)
        file_menu.add_command(label="Close Tab", accelerator="Ctrl+W", command=self._close_tab)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label="Back", accelerator="Alt+←", command=self._back)
        view_menu.add_command(label="Forward", accelerator="Alt+→", command=self._forward)
        view_menu.add_command(label="Reload", accelerator="Ctrl+R", command=self._reload)
        menubar.add_cascade(label="View", menu=view_menu)
        self.root.config(menu=menubar)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self.root, padding=4)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        self.btn_back = ttk.Button(toolbar, text="←", width=3, command=self._back)
        self.btn_back.pack(side=tk.LEFT, padx=2)
        self.btn_fwd = ttk.Button(toolbar, text="→", width=3, command=self._forward)
        self.btn_fwd.pack(side=tk.LEFT, padx=2)
        self.btn_reload = ttk.Button(toolbar, text="⟳", width=3, command=self._reload)
        self.btn_reload.pack(side=tk.LEFT, padx=2)

        self.addr_var = tk.StringVar()
        self.addr_entry = ttk.Entry(toolbar, textvariable=self.addr_var)
        self.addr_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.addr_entry.bind("<Return>", lambda e: self._navigate())

        self.btn_go = ttk.Button(toolbar, text="Go", command=self._navigate)
        self.btn_go.pack(side=tk.LEFT, padx=2)

    def _build_tab_frame(self) -> None:
        self.tab_frame = ttk.Frame(self.root)
        self.tab_frame.pack(side=tk.TOP, fill=tk.X)
        self.notebook = ttk.Notebook(self.tab_frame)
        self.notebook.pack(fill=tk.X)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_canvas(self) -> None:
        self.canvas_var: dict[int, tk.PhotoImage] = {}
        self.page_frames: dict[int, tk.Frame] = {}

    def _ensure_page_frame(self, idx: int) -> None:
        """Create the notebook tab widget for *idx* if not already present."""
        if idx in self.page_frames:
            return
        frame = tk.Frame(self.notebook)
        label = tk.Label(frame)
        label.pack()
        frame._label = label
        self.page_frames[idx] = frame
        self.notebook.add(frame, text=self.tabs[idx].title or f"Tab {idx + 1}")

    def _create_page_frame(self, idx: int) -> None:
        self._ensure_page_frame(idx)

    def _build_statusbar(self) -> None:
        self.status_var = tk.StringVar(value="Ready")
        statusbar = ttk.Label(self.root, textvariable=self.status_var,
                              anchor=tk.W, relief=tk.SUNKEN, padding=(4, 2))
        statusbar.pack(side=tk.BOTTOM, fill=tk.X)
        # progress bar (toy: just show 0 % / 100 %)
        self.progress = ttk.Progressbar(self.root, maximum=100, length=120)
        self.progress.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=(0, 2))

    # ------------------------------------------------------------------
    # pixel display
    def _bitmap_to_photopix(self, bmp: paint.Bitmap, idx: int) -> tk.PhotoImage:
        """Convert a paint.Bitmap to a tkinter PhotoImage (P6 binary PPM).

        Tk's ``image create photo -data`` reliably parses P6 (binary
        PPM with #! comment), which is simpler and faster than the
        space-separated P3 text form.
        """
        import io
        buf = io.BytesIO()
        buf.write(f"P6\n{bmp.width} {bmp.height}\n255\n".encode("ascii"))
        for y in range(bmp.height):
            for x in range(bmp.width):
                p = bmp.pixels[y * bmp.width + x]
                buf.write(bytes([(p >> 16) & 0xFF, (p >> 8) & 0xFF, p & 0xFF]))
        return tk.PhotoImage(data=buf.getvalue())

    def _refresh_canvas(self, idx: int) -> None:
        page = self.tabs[idx]
        if page.bitmap is None:
            return
        photo = self._bitmap_to_photopix(page.bitmap, idx)
        # keep reference so GC doesn't collect it
        self.canvas_var[idx] = photo
        frame = self.page_frames.get(idx)
        if frame is None:
            return
        label: tk.Label = frame._label
        label.config(image=photo)

    # ------------------------------------------------------------------
    # tab management
    def _add_tab(self, page: PageInstance) -> int:
        idx = len(self.tabs)
        self.tabs.append(page)
        self._ensure_page_frame(idx)
        return idx

    def _remove_tab(self, idx: int) -> None:
        if len(self.tabs) <= 1:
            self.root.quit()
            return
        # Drop the removed tab and rebuild the notebook consistently.
        del self.tabs[idx]
        self._rebuild_tabs()
        self._show_tab(min(idx, len(self.tabs) - 1))

    def _rebuild_tabs(self) -> None:
        """Destroy and recreate all notebook tab widgets (after removal)."""
        for w in self.notebook.winfo_children():
            w.destroy()
        self.page_frames.clear()
        self.canvas_var.clear()
        for i, page in enumerate(self.tabs):
            self._ensure_page_frame(i)
        self.current = 0

    def _show_tab(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.tabs):
            return
        self.current = idx
        self.notebook.select(self.page_frames[idx])
        page = self.tabs[idx]
        self.addr_var.set(page.url)
        self._refresh_canvas(idx)
        self._update_status()

    def _on_tab_changed(self, event=None) -> None:
        sel = self.notebook.select()
        idx = self.notebook.index(sel)
        self.current = idx
        page = self.tabs[idx]
        self.addr_var.set(page.url)
        self._refresh_canvas(idx)
        self._update_status()

    # ------------------------------------------------------------------
    # actions
    def _focus_address(self) -> None:
        self.addr_entry.select_range(0, tk.END)
        self.addr_entry.focus_set()

    def _navigate(self) -> None:
        url = self.addr_var.get().strip()
        if not url:
            return
        page = self.tabs[self.current]
        # Push current URL onto the back stack (navigation history).
        if page.url and page.url != url:
            page.back_stack.append(page.url)
            page.fwd_stack.clear()
        page.load_url(url, 800, 600)
        self.notebook.tab(self.current, text=page.title)
        self._refresh_canvas(self.current)
        self._update_status()
        self.status_var.set(f"Loaded: {page.title}")

    def _new_tab(self) -> None:
        page = PageInstance()
        page.url = "about:blank"
        page.title = "about:blank"
        page.bitmap = page._about_page("about:blank", 800, 600)
        self._add_tab(page)
        self._show_tab(len(self.tabs) - 1)

    def _close_tab(self) -> None:
        self._remove_tab(self.current)

    def _open_file(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("HTML files", "*.html *.htm"), ("All files", "*.*")]
        )
        if not path:
            return
        page = self.tabs[self.current]
        if page.url and page.url != path:
            page.back_stack.append(page.url)
            page.fwd_stack.clear()
        page.load_file(path, 800, 600)
        self.notebook.tab(self.current, text=page.title)
        self._refresh_canvas(self.current)
        self._update_status()

    def _back(self) -> None:
        page = self.tabs[self.current]
        page.go_back()
        self.notebook.tab(self.current, text=page.title)
        self._refresh_canvas(self.current)
        self._update_status()

    def _forward(self) -> None:
        page = self.tabs[self.current]
        page.go_forward()
        self.notebook.tab(self.current, text=page.title)
        self._refresh_canvas(self.current)
        self._update_status()

    def _reload(self) -> None:
        page = self.tabs[self.current]
        page.reload()
        self.notebook.tab(self.current, text=page.title)
        self._refresh_canvas(self.current)
        self._update_status()

    def _update_status(self) -> None:
        page = self.tabs[self.current]
        w = page.width
        h = page.height
        nonwhite = 0
        if page.bitmap is not None:
            nonwhite = sum(1 for p in page.bitmap.pixels if p != 0xFFFFFF)
        self.status_var.set(f"Tab {self.current + 1}/{len(self.tabs)}  "
                           f"{w}×{h}  {nonwhite} non-white px")
        self.progress["value"] = 100 if page.bitmap is not None else 0


# ----------------------------------------------------------------------
def main() -> None:
    import tkinter as tk
    root = tk.Tk()
    root.title("toy-browser-py")
    root.geometry("900x700")
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    TabBrowser(root, initial)
    root.mainloop()


if __name__ == "__main__":
    main()

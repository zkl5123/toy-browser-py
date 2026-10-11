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
import session


def _esc(s: str) -> str:
    """Minimal HTML escape for user-controlled text (P32)."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;") \
                   .replace(">", "&gt;").replace('"', "&quot;")


class PageInstance:
    """One tab's page state: DOM tree, layout root, bitmap, URL, history.

    P33.R2 — *full* content rendering: the page is laid out with an
    unconstrained height (the layout root's content height) and the
    bitmap covers the whole document; :attr:`yoffset` (pixels scrolled
    from the top) drives the viewport clip, and :attr:`scrollbar_max`
    / :attr:`scrollbar_value` feed the GUI's vertical scrollbar.
    """

    def __init__(self) -> None:
        self.url: str = "about:blank"
        self.title: str = "New Tab"
        self.bitmap: paint.Bitmap | None = None
        self._full_bitmap: paint.Bitmap | None = None  # P33.R2
        self.back_stack: list[str] = []
        self.fwd_stack: list[str] = []
        self.width: int = 800
        self.height: int = 600
        # P33.R2 — scroll state (pixels scrolled from the document top).
        self.yoffset: int = 0
        # P33.R2 — full-document height (the layout root's content
        # height, ≥ the viewport height when the page overflows).
        self.content_height: int = 0
        # P33.P1 — event model + focus management + interaction state.
        # ``hover_id`` / ``focus_id`` are the DOM element ids (or index
        # paths) of the currently hovered / focused element.  They feed
        # into ``css.apply(interaction_state=...)`` for :hover/:focus
        # pseudo-class rules.  ``focus_chain`` is the tabindex order
        # (element index in the DOM tree, or None for the body).
        # ``pending_events`` is a FIFO queue of synthetic events
        # dispatched by JS (setTimeout / rAF callbacks).
        self.hover_id: int | None = None
        self.focus_id: int | None = None
        self.focus_chain: list[int] = []
        self.active_form_field: int | None = None  # index of the active input
        self._form_states: dict[int, dict] = {}  # index → {value, checked}
        self._interaction_state: dict = {}
        # P33.P1 — mouse move history for hover transitions.
        self._last_mouse_xy: tuple[int, int] = (-1, -1)

    def scroll_to(self, yoffset: int) -> None:
        """P33.R2 — scroll the page to *yoffset* pixels from the top.

        The stored bitmap is the full document; scrolling re-clips the
        visible window (no re-render).  The offset is clamped to
        [0, content_height - height].
        """
        max_off = max(0, self.content_height - self.height)
        self.yoffset = max(0, min(int(yoffset), max_off))
        self._reclip()

    def scroll_by(self, dy: int) -> None:
        """P33.R2 — relative scroll (the GUI's MouseWheel handler)."""
        self.scroll_to(self.yoffset + int(dy))

    def scroll_max(self) -> int:
        """P33.R2 — the largest valid :attr:`yoffset`."""
        return max(0, self.content_height - self.height)

    def scroll_ratio(self) -> float:
        """P33.R2 — scrollbar value in [0.0, 1.0] (0 when not
        scrollable)."""
        m = self.scroll_max()
        if m <= 0:
            return 0.0
        return self.yoffset / m

    def _clip_bitmap(self, bmp: paint.Bitmap, viewport_h: int) -> paint.Bitmap:
        """P33.R2 — crop *bmp* to the current scroll window
        (rows yoffset..yoffset+viewport_h)."""
        top = min(self.yoffset, max(0, bmp.height - 1))
        h = min(viewport_h, bmp.height - top)
        if h <= 0:
            return bmp
        out = paint.Bitmap(bmp.width, h)
        for row in range(h):
            src = (top + row) * bmp.width
            dst = row * out.width
            out.pixels[dst:dst + out.width] = bmp.pixels[src:src + out.width]
        return out

    def _content_height_probe(self, root) -> int:
        """P33.R2 — the document's content height: the max
        (rect.y + rect.h) over the layout root's direct children.
        The layout root itself is a zero-height synthetic container,
        so its own rect is not a content-height signal; the children's
        bounding box is.  Returns 0 for an empty tree (the caller
        clamps to the viewport height)."""
        best = 0.0
        for c in getattr(root, "children", []) or []:
            r = getattr(c, "rect", None)
            if r is None:
                continue
            bottom = r.y + r.h
            if bottom > best:
                best = bottom
        return int(best + 0.999)  # ceiling

    def load_html(self, html: str, css: str = "", w: int = 800, h: int = 600,
                  full_page: bool = True,
                  interaction_state: dict | None = None):
        """Render *html* via the toy browser pipeline; store the bitmap.

        P33.R2 — when *full_page* (default), the page is rendered at
        full content height (the layout root's content height) so long
        documents are not truncated to the viewport; the scroll
        offset clamps to the content.  A rendering cap of 16000 px
        keeps a pathological page from allocating a multi-GB bitmap
        (6 GB memory governance).  ``full_page=False`` keeps the
        legacy viewport-only render (faster for small pages, no
        scroll state).

        P33.P1 — *interaction_state* (optional dict with ``"hover"``
        and ``"focus"`` keys) feeds into ``css.apply`` for
        :hover/:focus pseudo-class rules.  When omitted, the page's
        own :attr:`hover_id` / :attr:`focus_id` are used.
        """
        import browser
        self.width = w
        self.height = h
        if not full_page:
            self.bitmap = browser.render_document(html, css, w, h)
            self.content_height = self.bitmap.height
            self._full_bitmap = None
            self.scroll_to(0)
            return
        import dom as _dom_mod
        import css as _css_mod
        import layout as _layout_mod
        import paint_engine as _paint_mod
        # P33.P1 — build the interaction state dict for :hover/:focus.
        if interaction_state is None:
            interaction_state = {}
            if self.hover_id is not None:
                interaction_state["hover"] = self.hover_id
            if self.focus_id is not None:
                interaction_state["focus"] = self.focus_id
        # P33.R2 — measure the full content height with one cheap
        # layout pass (huge viewport height → unconstrained), then
        # rasterize the full document.  The DOM tree is parsed once
        # and reused for both the probe and the final pass.
        tree = _dom_mod.parse(html)
        self._dom_tree = tree  # P33.P1 — store for hit-test / focus
        self._last_html = html  # P33.P1 — for _reapply_interaction
        self._last_css = css
        author_css, ua_css = browser._full_css(tree, css)
        media = _css_mod.parse_media_queries(author_css)
        supports = _css_mod.parse_supports_blocks(author_css)
        styled = _css_mod.apply(tree, author_css, ua_css=ua_css,
                                interaction_state=interaction_state or None,
                                media_rules=media, supports_rules=supports,
                                viewport_w=float(w), viewport_h=float(h))
        # P33.P1 — store the layout root for hit-testing / focus chain.
        self._layout_root = None
        # Probe: layout with a huge viewport height so block boxes are
        # not forced to shrink-wrap the viewport.  The content height
        # is the bounding box of the root's children (max y + h).
        probe = _layout_mod.layout(styled, float(w), 10_000_000.0)
        content_h = self._content_height_probe(probe)
        # P33.R2 — cap the full-document height (16000 px ≈ 16 MP
        # bitmap ≈ 48 MB, safe under the 6 GB governance cap).
        full_h = min(max(content_h, h), 16000)
        root2 = _layout_mod.layout(styled, float(w), float(full_h))
        self._layout_root = root2
        dl = _paint_mod.emit_commands(root2)
        bmp = _paint_mod.rasterize(dl, w, full_h)
        # P33.P1 — build the focus chain (tabindex order) for keyboard
        # navigation.  Walk the DOM tree and collect elements with
        # tabindex >= 0 in document order.
        self._build_focus_chain(tree)
        # P33.R2 — clamp the scroll offset to the new content bounds.
        self.content_height = full_h
        self._full_bitmap = bmp
        self.scroll_to(0)

    # ------------------------------------------------------------------
    # P33.P1 — event model + focus management + form interaction
    # ------------------------------------------------------------------

    def _build_focus_chain(self, tree) -> None:
        """P33.P1 — walk the DOM tree and collect focusable elements
        (``tabindex >= 0``, or interactive form controls) in document
        order.  Stores the result in :attr:`focus_chain` (a list of
        DOM node indices, not ids — the id may be absent on form
        controls)."""
        chain: list = []
        counter = [0]

        def _walk(node):
            counter[0] += 1
            idx = counter[0]
            if node.is_element():
                tab = node.attr("tabindex")
                is_form = node.tag in ("input", "select", "textarea", "button", "a")
                has_href = node.tag == "a" and node.attr("href")
                if tab is not None:
                    try:
                        if int(tab) >= 0:
                            chain.append(idx)
                    except (ValueError, TypeError):
                        pass
                elif is_form or has_href:
                    chain.append(idx)
            for c in getattr(node, "children", []):
                _walk(c)

        _walk(tree)
        self.focus_chain = chain
        # Reset active field when the page changes.
        self.active_form_field = None
        self._form_states = {}

    def _node_index_to_dom(self, tree, idx: int):
        """P33.P1 — resolve a DOM node index (1-based, document order)
        to the actual DOM Node object.  O(n) walk; the focus chain is
        small enough that this is acceptable."""
        counter = [0]
        found = [None]

        def _walk(node):
            counter[0] += 1
            if counter[0] == idx:
                found[0] = node
                return
            for c in getattr(node, "children", []):
                _walk(c)
                if found[0] is not None:
                    return

        _walk(tree)
        return found[0]

    def hit_test_at(self, x: int, y: int) -> int | None:
        """P33.P1 — hit-test at viewport coordinates (x, y), returning
        the DOM node index of the topmost element under the cursor,
        or ``None`` when nothing is hit.  Accounts for the current
        scroll offset (y + yoffset in document coordinates)."""
        import layout as _layout_mod
        root = getattr(self, "_layout_root", None)
        if root is None:
            return None
        doc_y = y + self.yoffset
        hit = _layout_mod.hit_test(root, float(x), float(doc_y))
        if hit is None or hit.dom is None:
            return None
        # Build the index map (DOM node id → document-order index).
        counter = [0]
        id_map: dict = {}

        def _walk(node):
            counter[0] += 1
            if node.is_element():
                id_map[id(node)] = counter[0]
            for c in getattr(node, "children", []):
                _walk(c)

        tree = getattr(self, "_dom_tree", None)
        if tree is not None:
            _walk(tree)
        return id_map.get(id(hit.dom))

    def set_hover(self, x: int, y: int, restate: bool = True) -> None:
        """P33.P1 — update the hover state from a mouse position.
        When *restate* and the hit changes, re-applies the CSS
        :hover pseudo-class via a re-render."""
        hit_idx = self.hit_test_at(x, y)
        if hit_idx != self.hover_id:
            self.hover_id = hit_idx
            if restate and getattr(self, "_dom_tree", None) is not None:
                self._reapply_interaction()

    def set_focus(self, idx: int | None, restate: bool = True) -> None:
        """P33.P1 — set the focus to DOM node index *idx* (or None
        to blur).  When *restate*, re-applies the CSS :focus
        pseudo-class."""
        if idx != self.focus_id:
            self.focus_id = idx
            if restate and getattr(self, "_dom_tree", None) is not None:
                self._reapply_interaction()

    def focus_next(self, forward: bool = True) -> None:
        """P33.P1 — move focus to the next/previous element in the
        :attr:`focus_chain` (Tab / Shift+Tab)."""
        if not self.focus_chain:
            return
        cur = self.focus_chain.index(self.focus_id) if self.focus_id in self.focus_chain else -1
        n = len(self.focus_chain)
        if forward:
            nxt = self.focus_chain[(cur + 1) % n]
        else:
            nxt = self.focus_chain[cur - 1 if cur >= 0 else n - 1]
        self.set_focus(nxt)

    def _reapply_interaction(self) -> None:
        """P33.P1 — re-render the page with the current hover/focus
        state applied via ``css.apply(interaction_state=...)``."""
        import dom as _dom_mod
        import css as _css_mod
        import layout as _layout_mod
        import paint_engine as _paint_mod
        import browser as _browser_mod
        html = getattr(self, "_last_html", "")
        css_text = getattr(self, "_last_css", "")
        if not html:
            return
        tree = _dom_mod.parse(html)
        self._dom_tree = tree
        author_css, ua_css = _browser_mod._full_css(tree, css_text)
        media = _css_mod.parse_media_queries(author_css)
        supports = _css_mod.parse_supports_blocks(author_css)
        state: dict = {}
        # Convert DOM node indices back to ids for the css.apply call.
        # The css engine matches :hover/:focus against element *ids*.
        if self.hover_id is not None:
            node = self._node_index_to_dom(tree, self.hover_id)
            if node is not None and node.is_element():
                state["hover"] = node.attr("id")
        if self.focus_id is not None:
            node = self._node_index_to_dom(tree, self.focus_id)
            if node is not None and node.is_element():
                state["focus"] = node.attr("id")
        styled = _css_mod.apply(tree, author_css, ua_css=ua_css,
                                interaction_state=state or None,
                                media_rules=media, supports_rules=supports,
                                viewport_w=float(self.width),
                                viewport_h=float(self.height))
        probe = _layout_mod.layout(styled, float(self.width), 10_000_000.0)
        content_h = self._content_height_probe(probe)
        full_h = min(max(content_h, self.height), 16000)
        root2 = _layout_mod.layout(styled, float(self.width), float(full_h))
        self._layout_root = root2
        dl = _paint_mod.emit_commands(root2)
        bmp = _paint_mod.rasterize(dl, self.width, full_h)
        self.content_height = full_h
        self._full_bitmap = bmp
        self._reclip()

    def _set_form_value(self, idx: int, value: str) -> None:
        """P33.P1 — store the value of a form field (input/textarea)
        by DOM node index.  The value is kept in :attr:`_form_states`
        and applied to the DOM on the next re-render."""
        self._form_states[idx] = {"value": value, "dirty": True}

    def _toggle_checkbox(self, idx: int) -> None:
        """P33.P1 — toggle a checkbox / radio button's checked state."""
        st = self._form_states.get(idx, {"value": "", "checked": False})
        st["checked"] = not st.get("checked", False)
        st["dirty"] = True
        self._form_states[idx] = st

    def _save_form_state_to_dom(self) -> None:
        """P33.P1 — apply dirty form states back to the DOM tree so
        the next CSS cascade sees the updated values / checked states."""
        tree = getattr(self, "_dom_tree", None)
        if tree is None:
            return
        for idx, st in self._form_states.items():
            if not st.get("dirty"):
                continue
            node = self._node_index_to_dom(tree, idx)
            if node is None or not node.is_element():
                continue
            if "value" in st and st["value"] != "":
                node.attrs["value"] = st["value"]
            if "checked" in st and st.get("checked"):
                node.attrs["checked"] = "checked"
            st["dirty"] = False

    def _reclip(self) -> None:
        """P33.R2 — rebuild the visible bitmap from the full-document
        bitmap at the current :attr:`yoffset` (the scroll handler
        calls this after :meth:`scroll_to`)."""
        if getattr(self, "_full_bitmap", None) is not None:
            self.bitmap = self._clip_bitmap(self._full_bitmap, self.height)

    def load_file(self, path: str, w: int = 800, h: int = 600):
        """Read *path* and render it."""
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        self.url = path
        self.title = os.path.basename(path)
        self._last_html = html
        self._last_css = ""
        self.load_html(html, "", w, h)

    def load_url(self, url: str, w: int = 800, h: int = 600,
                 record_history: bool = True,
                 client: "net.HttpClient | None" = None,
                 parallel: bool = True):
        """Dispatch: local file, about: page, inline HTML, or network.

        Navigation history is managed by the *caller* (TabBrowser._navigate
        or _open_file).  Here we just render the target and — when
        *record_history* is set and a global :class:`HistoryStore` is
        attached to the owning browser — log the visit.

        P33.R2 — *client* is an optional shared :class:`net.HttpClient`
        (the keep-alive pool makes subresource fetches cheap);
        *parallel* selects the thread-pool subresource loader
        (:func:`net.load_subresources_parallel`).
        """
        url = url.strip()
        if not url:
            return
        # P31.2 — about: pages are synthetic (no network, no file);
        # they render at the viewport size with no scroll capability.
        if url.lower().startswith("about:"):
            self.url = url
            self.title = url
            self.bitmap = self._about_page(url, w, h)
            self.content_height = self.bitmap.height if self.bitmap else 0
            self._full_bitmap = None
            self.scroll_to(0)
            return
        # inline HTML (starts with <)
        if url.startswith("<"):
            self.url = url
            self.title = "Inline HTML"
            self._last_html = url
            self._last_css = ""
            self.load_html(url, "", w, h)
            return
        # local file path
        if os.path.isfile(url):
            self.load_file(url, w, h)
            self._maybe_record_history(record_history)
            return
        # P21 — http/https network loading (M2 网络栈第一步).
        # Unified error page on timeout / connection failure / 4xx/5xx /
        # unsupported Content-Type (net.FetchError).
        import net
        import dom as _dom_mod
        lowered = url.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            self.url = url
            self.title = url
            try:
                html, resp = net.fetch_html(url, timeout=10.0, client=client)
                # P33.R2 — fetch subresources (stylesheets / images)
                # against the document DOM, using the shared keep-alive
                # client (the parallel loader when *parallel*).
                tree = _dom_mod.parse(html)
                loader = (net.load_subresources_parallel
                          if parallel else net.load_subresources)
                sub = loader(tree, base_url=url, client=client)
                extra_css = "".join(
                    (r.body or b"").decode("utf-8", "replace")
                    for r in sub.get("stylesheet", {}).values()
                    if r is not None)
                self.load_html(html, extra_css, w, h)
                self._maybe_record_history(record_history)
            except net.FetchError as e:
                # P32.5 — per-kind error-page copy.
                cat = net.categorize_fetch_error(e)
                err_html = net.error_page_html(
                    f"HTTP {e.status}" if e.status else "Request failed",
                    f"Loading {url} failed (status={e.status}).",
                    category=cat,
                )
                self.load_html(err_html, "", w, h)
            except Exception as e:
                cat = net.categorize_fetch_error(e)
                err_html = net.error_page_html(
                    "Connection error", f"Loading {url} failed: {e}",
                    category=cat,
                )
                self.load_html(err_html, "", w, h)
            return
        # last resort — show the not-supported stub.
        self.url = url
        self.title = url
        html = (
            f"<html><body><h1>Not supported yet</h1>"
            f"<p>URL: {url}</p></body></html>"
        )
        self.load_html(html, "", w, h)
        self._maybe_record_history(record_history)

    def _maybe_record_history(self, record: bool) -> None:
        """P32.1 — log the current URL to the global history (if attached)."""
        if not record:
            return
        browser = getattr(self, "_browser", None)
        if browser is not None and getattr(browser, "history", None) is not None:
            browser.history.add(self.url, self.title or self.url)

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
        # P21 — http/https network loading (M2 网络栈第一步).
        import net
        lowered = url.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            self.url = url
            self.title = url
            try:
                html, _resp = net.fetch_html(url, timeout=10.0)
                self.load_html(html, "", w, h)
            except net.FetchError as e:
                cat = net.categorize_fetch_error(e)
                err_html = net.error_page_html(
                    f"HTTP {e.status}" if e.status else "Request failed",
                    f"Loading {url} failed (status={e.status}).",
                    category=cat,
                )
                self.load_html(err_html, "", w, h)
            except Exception as e:
                cat = net.categorize_fetch_error(e)
                err_html = net.error_page_html(
                    "Connection error", f"Loading {url} failed: {e}",
                    category=cat,
                )
                self.load_html(err_html, "", w, h)
            return
        self.url = url
        self.title = url
        html = (
            f"<html><body><h1>Not supported yet</h1>"
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
                '<p>Version 1.1 (P0–P32)</p>'
                '<p>Pure standard-library Python toy browser.</p>'
                '</body></html>'
            )
        elif "history" in lower:
            # P32.1 — prefer the *global* persisted history when the owning
            # browser has one attached; fall back to the tab's in-memory stacks.
            browser = getattr(self, "_browser", None)
            hist = getattr(browser, "history", None) if browser else None
            if hist is not None:
                entries = hist.recent(50)
                items = "".join(
                    f'<li><a href="{_esc(u.get("url", ""))}">{_esc(u.get("title") or u.get("url", ""))}</a></li>'
                    for u in entries
                )
                html = f"<html><body><h1>History</h1><ul>{items}</ul></body></html>"
            else:
                items = "".join(
                    f"<li>{u}</li>" for u in (self.back_stack + [self.url] + self.fwd_stack)
                )
                html = f"<html><body><h1>History</h1><ul>{items}</ul></body></html>"
        elif "bookmarks" in lower:
            # P32.2 — render the persisted bookmarks store.
            browser = getattr(self, "_browser", None)
            bmarks = getattr(browser, "bookmarks", None) if browser else None
            html = session.bookmarks_page_html(bmarks) if bmarks is not None else (
                '<html><body><h1>Bookmarks</h1><p>No bookmarks store attached.</p>'
                '</body></html>'
            )
        elif "view-source" in lower:
            # P32.6 — view-source: toy: show the raw document source of the
            # current page (best-effort; for local files / cached content).
            html = self._view_source_page(w, h)
        else:
            html = f"<html><body><h1>{url}</h1></body></html>"
        return browser.render_document(html, "", w, h)

    def _view_source_page(self, w: int, h: int) -> str:
        """P32.6 — a minimal ``view-source:`` page.

        Shows the raw source of the *current* page when it is a local file
        (re-read from disk); for network pages it notes that the source is
        not retained in this toy milestone.
        """
        url = self.url
        if url and os.path.isfile(url):
            try:
                with open(url, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read()
            except OSError:
                src = "(could not re-read source)"
            src = _esc(src)
            return (
                '<html><body><h1>View Source</h1>'
                f'<p>URL: {url}</p>'
                f'<pre style="font-family:monospace;font-size:11px">{src}</pre>'
                '</body></html>'
            )
        return (
            '<html><body><h1>View Source</h1>'
            f'<p>Source for <code>{url}</code> is not retained in this toy '
            'milestone (network documents are not stored raw).</p>'
            '</body></html>'
        )

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
        # P21 — http/https network loading (M2 网络栈第一步).
        import net
        lowered = url.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            self.url = url
            self.title = url
            try:
                html, _resp = net.fetch_html(url, timeout=10.0)
                self.load_html(html, "", w, h)
            except net.FetchError as e:
                cat = net.categorize_fetch_error(e)
                err_html = net.error_page_html(
                    f"HTTP {e.status}" if e.status else "Request failed",
                    f"Loading {url} failed (status={e.status}).",
                    category=cat,
                )
                self.load_html(err_html, "", w, h)
            except Exception as e:
                cat = net.categorize_fetch_error(e)
                err_html = net.error_page_html(
                    "Connection error", f"Loading {url} failed: {e}",
                    category=cat,
                )
                self.load_html(err_html, "", w, h)
            return
        self.url = url
        self.title = url
        html = (
            f"<html><body><h1>Not supported yet</h1>"
            f"<p>URL: {url}</p></body></html>"
        )
        self.load_html(html, "", w, h)

    def reload(self):
        if self.bitmap is not None:
            self._load_no_history(self.url, self.width, self.height)


class TabBrowser:
    """Multi-tab browser shell built on tkinter."""

    def __init__(self, root: tk.Tk, initial_path: str | None = None,
                 history: "session.HistoryStore | None" = None,
                 bookmarks: "session.BookmarkStore | None" = None) -> None:
        self.root = root
        self.tabs: list[PageInstance] = []
        self.current: int = 0
        # P32 — attach the global session stores (created by main() or the
        # caller).  When None the GUI runs session-less (back/fwd only).
        self.history = history
        self.bookmarks = bookmarks

        # --- build UI ---
        self._build_menu()
        self._build_toolbar()
        self._build_tab_frame()
        self._build_canvas()
        self._build_statusbar()

        # --- initial tab ---
        page = PageInstance()
        page._browser = self
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
        # P33.P1 — keyboard focus navigation (Tab / Shift+Tab).
        self.root.bind("<Tab>", lambda e: self._tab_focus(True))
        self.root.bind("<Shift-Tab>", lambda e: self._tab_focus(False))
        self.root.bind("<Return>", lambda e: self._activate_focused())
        # P33.P1 — click dispatch: hit-test + form toggle + focus.
        self.root.bind("<Button-1>", lambda e: self._click_dispatch(e))
        # P33.P1 — mouse-move: hover state update (throttled).
        self.root.bind("<Motion>", lambda e: self._hover_dispatch(e))

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
        # P32.2 — bookmarks menu.
        bm_menu = tk.Menu(menubar, tearoff=0)
        bm_menu.add_command(label="Bookmark This Page",
                            command=self._bookmark_current)
        if self.bookmarks is not None:
            for b in self.bookmarks.all():
                u, t = b.get("url", ""), (b.get("title") or b.get("url", ""))
                bm_menu.add_command(label=t, command=lambda _u=u: self._open_url(_u))
        menubar.add_cascade(label="Bookmarks", menu=bm_menu)
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
        """Create the notebook tab widget for *idx* if not already present.

        P33.R2 — each tab now hosts a *scrollable* viewport: the
        rendered page bitmap (the scroll window of the full-document
        bitmap) plus a vertical :class:`ttk.Scrollbar` that drives
        :meth:`PageInstance.scroll_to`.  The wheel binding on the
        viewport calls :meth:`PageInstance.scroll_by`.
        """
        if idx in self.page_frames:
            return
        frame = tk.Frame(self.notebook)
        viewport = tk.Frame(frame, width=800, height=600,
                            background="white")
        label = tk.Label(viewport, borderwidth=0, highlightthickness=0)
        label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL)
        viewport.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        # P33.R2 — mouse-wheel scrolling (Windows: <MouseWheel> with
        # delta in units of ~120; each wheel notch ≈ 40 px of scroll).
        def _wheel(event):
            page = self.tabs[idx]
            step = -40 if (event.delta or 0) > 0 else 40
            page.scroll_by(step)
            self._refresh_canvas(idx)
        viewport.bind("<MouseWheel>", _wheel)
        # P33.R2 — wire the scrollbar thumb: Tk's Scrollbar command
        # fires as command(detail, *args) where detail is "movements"
        # (thumb position 0.0-1.0 in args[0]) or "units" (arrows).
        def _sb(detail, *args):
            page = self.tabs[idx]
            if not args:
                return
            if detail == "movements":
                page.scroll_to(int(float(args[0]) * page.scroll_max()))
            elif detail == "units":
                page.scroll_by(40)
            self._refresh_canvas(idx)
        scroll.config(command=_sb)
        frame._label = label
        frame._viewport = viewport
        frame._scroll = scroll
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
        # P33.R2 — sync the vertical scrollbar's range/value with the
        # page's scroll state.  When the content fits the viewport the
        # scrollbar stays idle (value 0, empty range); otherwise the
        # thumb spans value/scroll_max.
        sb = getattr(frame, "_scroll", None)
        if sb is not None:
            try:
                m = page.scroll_max()
                if m > 0:
                    # Thumb spans viewport/content of the track, at
                    # the current offset.
                    sb.set(0.0, min(1.0, max(0.0,
                             page.yoffset / page.content_height)))
                else:
                    sb.set(0.0, 1.0)
            except tk.TclError:
                pass

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

    def _open_url(self, url: str) -> None:
        """P32.2 — navigate the current tab to *url* (bookmark menu item)."""
        self.addr_var.set(url)
        self._navigate()

    def _bookmark_current(self) -> None:
        """P32.2 — bookmark the current page in the global store."""
        page = self.tabs[self.current]
        if self.bookmarks is None:
            return
        title = page.title or page.url
        self.bookmarks.add(page.url, title)
        self.status_var.set(f"Bookmarked: {title}")

    def _new_tab(self) -> None:
        page = PageInstance()
        page._browser = self
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

    # ------------------------------------------------------------------
    # P33.P1 — keyboard focus navigation + click dispatch
    # ------------------------------------------------------------------
    def _tab_focus(self, forward: bool = True) -> None:
        """P33.P1 — Tab / Shift+Tab: move the focus to the next/previous
        element in the current page's :attr:`focus_chain`."""
        page = self.tabs[self.current]
        page.focus_next(forward=forward)
        self._refresh_canvas(self.current)

    def _activate_focused(self) -> None:
        """P33.P1 — Return / Enter: activate the currently focused
        element (follow a link, submit a form, toggle a checkbox,
        or click a button)."""
        page = self.tabs[self.current]
        idx = page.focus_id
        if idx is None:
            return
        node = page._node_index_to_dom(
            getattr(page, "_dom_tree", None), idx)
        if node is None or not node.is_element():
            return
        tag = (node.tag or "").lower()
        if tag == "a" and node.attr("href"):
            href = node.attr("href")
            self._navigate_to(href)
        elif tag in ("input", "button"):
            page._toggle_checkbox(idx)
            page._reapply_interaction()
            self._refresh_canvas(self.current)
        elif tag == "select":
            # Toy: cycle to the next <option>.
            page._reapply_interaction()
            self._refresh_canvas(self.current)

    def _click_dispatch(self, event) -> None:
        """P33.P1 — mouse click on the viewport: hit-test, set hover +
        focus, toggle form controls, follow links."""
        page = self.tabs[self.current]
        frame = self.page_frames.get(self.current)
        if frame is None:
            return
        # Resolve the click to viewport pixel coordinates.  The Label
        # fills the viewport; event.x/y are in the Label's coordinate
        # space.
        x = int(event.x)
        y = int(event.y)
        # Account for the scroll offset when hit-testing in document
        # coordinates.
        hit_idx = page.hit_test_at(x, y)
        # Update hover state.
        page.hover_id = hit_idx
        if hit_idx is not None:
            page.set_focus(hit_idx, restate=False)
            node = page._node_index_to_dom(
                getattr(page, "_dom_tree", None), hit_idx)
            if node is not None and node.is_element():
                tag = (node.tag or "").lower()
                if tag == "a" and node.attr("href"):
                    href = node.attr("href")
                    self._navigate_to(href)
                    return
                elif tag in ("input", "button"):
                    inp_type = (node.attr("type") or "").lower()
                    if inp_type in ("checkbox", "radio", "submit", "button"):
                        page._toggle_checkbox(hit_idx)
                        page._save_form_state_to_dom()
                    else:
                        # text/number/etc. — focus for keyboard entry.
                        page.active_form_field = hit_idx
                elif tag == "textarea":
                    page.active_form_field = hit_idx
                elif tag == "select":
                    page.active_form_field = hit_idx
        page._reapply_interaction()
        self._refresh_canvas(self.current)

    def _hover_dispatch(self, event) -> None:
        """P33.P1 — mouse move: update the hover state (throttled to
        only re-render when the hit element changes)."""
        page = self.tabs[self.current]
        frame = self.page_frames.get(self.current)
        if frame is None:
            return
        # The <Motion> binding fires on the root window; we only care
        # about moves inside the viewport.  Use the Label's winfo to
        # check bounds.
        label = getattr(frame, "_label", None)
        if label is None:
            return
        try:
            lx = event.x_root - label.winfo_rootx()
            ly = event.y_root - label.winfo_rooty()
            lw = label.winfo_width()
            lh = label.winfo_height()
        except tk.TclError:
            return
        if lx < 0 or ly < 0 or lx >= lw or ly >= lh:
            return
        # Throttle: only restate when the hit changes.
        page.set_hover(int(lx), int(ly), restate=True)
        # Don't refresh the canvas on every motion — only when the
        # interaction state actually changed (set_hover already
        # re-clips via _reapply_interaction when needed).
        self._refresh_canvas(self.current)

    def _navigate_to(self, href: str) -> None:
        """P33.P1 — navigate to *href* (relative or absolute URL)."""
        page = self.tabs[self.current]
        if href.startswith("http://") or href.startswith("https://"):
            page.load_url(href, page.width, page.height)
        elif not href.startswith("#") and href.strip():
            # Relative link — toy: just log it (no base-URL resolution
            # in the click path; full resolution requires the net module).
            pass
        self._refresh_canvas(self.current)
        self._update_status()


# ----------------------------------------------------------------------
def main() -> None:
    import tkinter as tk
    import argparse
    parser = argparse.ArgumentParser(description="toy-browser-py GUI")
    parser.add_argument("path", nargs="?", default=None,
                        help="initial file / inline HTML")
    parser.add_argument("--history", metavar="FILE",
                        default=session.DEFAULT_HISTORY_PATH,
                        help="persisted global history JSON (default .toy_history.json)")
    parser.add_argument("--bookmarks", metavar="FILE",
                        default=session.DEFAULT_BOOKMARKS_PATH,
                        help="persisted bookmarks JSON (default .toy_bookmarks.json)")
    args = parser.parse_args()

    # P32.1/P32.2 — build the global session stores (skip a missing/empty
    # path by using in-memory mode when the user passes an empty string).
    history = None
    if args.history:
        history = session.HistoryStore(args.history)
    bookmarks = None
    if args.bookmarks:
        bookmarks = session.BookmarkStore(args.bookmarks)

    root = tk.Tk()
    root.title("toy-browser-py")
    root.geometry("900x700")
    TabBrowser(root, args.path, history=history, bookmarks=bookmarks)
    root.mainloop()


if __name__ == "__main__":
    main()

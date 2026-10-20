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
from typing import Optional

# P31 — pure stdlib: we do NOT require Pillow.  tkinter PhotoImage
# supports PPM/PGM directly (P4 format).  We convert our Bitmap to
# PGM text, which PhotoImage can load.
from toy_browser.rendering import paint
from toy_browser.app import foci, session


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
        # R7-S03 第 5 点 — sessionStorage 的分区身份（tab_id）。由
        # BrowserWindow 在标签建立时写入（``_tab_ids[idx]``）；宿主用
        # 它向 StorageService 要"本标签"的 session 分区。
        self.tab_id: object = None
        # R7-A01 历史栈收敛 — 双栈字段（back_stack / fwd_stack）不再
        # 由页面自持，而是其页内 :class:`HistoryCommitter` 游标模型的
        # 只读投影（游标下界 / 上界）。外部消费者（TabBrowser 主链、
        # session 持久化 list(s.back_stack)、tab_identity 快照、
        # about:history 页）继续把这两个属性当 list 读，形态不变；
        # 真正的历史提交 / 前进后退统一走游标（单一所有者）。
        from toy_browser.app.history_entry import HistoryCommitter
        # 初始页 about:blank 作为游标第 0 项：此后每次新导航经
        # :meth:`_commit_history` 追加，游标始终指向"当前页"所在项；
        # 双栈字段（back/fwd）是游标的只读投影（见下方注释）。
        self._hist = HistoryCommitter(initial_url="about:blank")
        # R7-A01 历史栈收敛 — 双栈字段是游标模型的只读投影，
        # 由 :meth:`_sync_history_stacks` 维护（投影直接用
        # ``self._hist.cursor``，它始终指向当前页所在的历史项）；
        # 外部消费者继续按 list 读这两个属性，形态不变。
        self.back_stack: list[str] = []
        self.fwd_stack: list[str] = []
        self.width: int = 800
        self.height: int = 600
        # P33.R2 — scroll state (pixels scrolled from the document top).
        self.yoffset: int = 0
        # P33.R2 — full-document height (the layout root's content
        # height, ≥ the viewport height when the page overflows).
        self.content_height: int = 0
        # C01 / R7-A01 — the DocumentState container is the single
        # owner of this page's document lifecycle: version counters
        # (``session.doc_version``), DOM tree, script Host, layout
        # root, cached display list and the source HTML/CSS.  The
        # legacy PageInstance attribute names below stay available
        # as property proxies (TabBrowser / tests keep working), but
        # all writes land in one place.
        from toy_browser.app.document_state_full import DocumentState
        self.session = DocumentState()
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
        # R7-U01 (G06) — unified coordinate transform for the hit tree.
        # 审计实施方案 R7-U01 第 3 点："resize/scroll/zoom/DPR 使用统一
        # 坐标变换，命中测试应用逆变换；验收 150%/200% 缩放点击位置
        # 正确"。此前 hit_test_at 只用裸 ``y + yoffset``（纯垂直滚动），
        # 没把 zoom / device_scale 接进命中坐标——150%/200% 缩放下
        # 点同一物理位置会落到错误的 CSS 坐标。这里给页面挂一个
        # :class:`ViewportState`，命中坐标经它的 ``logical_to_css``
        # （(ui - 0) / (zoom·dpr) + scroll）转回 CSS 文档坐标；默认
        # zoom=1.0 / device_scale=1.0，此时 ``logical_to_css`` 退化为
        # ``ui + scroll``，与旧的 ``y + yoffset`` 行为完全一致（向后
        # 兼容，未启用缩放/DPI 的现有路径不变）。
        from toy_browser.app.viewport import ViewportState
        self._viewport = ViewportState()
        # P0 (assessment report) — the script-executed DOM tree and the
        # shared JS Host: when set (HTTP / script-running loads), re-render
        # paths must reuse them instead of re-parsing the source HTML
        # (which would discard the script mutations).
        # R7-A01 — owned by ``self.session``; the underscore attribute
        # names below resolve through property proxies onto it.
        # P0 (assessment report) — when True, load_html reuses the
        # stored _dom_tree / _script_host instead of re-parsing the
        # source HTML (set by the HTTP path after script execution).
        # R7-A01 — mouse move history for hover transitions.
        self._last_mouse_xy: tuple[int, int] = (-1, -1)
        # C01 / R7-A01 — start with one live document identity (matches
        # the legacy "construct a fresh DocumentVersion and bump once").
        self.session.doc_version.new_document()

    # ------------------------------------------------------------------
    # R7-A01 — legacy attribute proxies onto the DocumentState session
    # (TabBrowser / tests keep the underscore names; all state lives
    # in one container, never in two places).
    # ------------------------------------------------------------------
    @property
    def doc_version(self):
        """C01 — the page's document version counters, owned by
        :attr:`session` (a :class:`DocumentState`)."""
        return self.session.doc_version

    @doc_version.setter
    def doc_version(self, value) -> None:
        self.session.doc_version = value

    @property
    def _dom_tree(self):
        return self.session.dom_tree

    @_dom_tree.setter
    def _dom_tree(self, value) -> None:
        self.session.dom_tree = value

    @property
    def _script_host(self):
        return self.session.script_host

    @_script_host.setter
    def _script_host(self, value) -> None:
        self.session.script_host = value

    @property
    def _last_html(self):
        return self.session.last_html

    @_last_html.setter
    def _last_html(self, value) -> None:
        self.session.last_html = value

    @property
    def _last_css(self):
        return self.session.last_css

    @_last_css.setter
    def _last_css(self, value) -> None:
        self.session.last_css = value

    @property
    def _reuse_doc(self):
        return self.session.reuse_doc

    @_reuse_doc.setter
    def _reuse_doc(self, value) -> None:
        self.session.reuse_doc = value

    @property
    def _layout_root(self):
        return self.session.layout_root

    @_layout_root.setter
    def _layout_root(self, value) -> None:
        self.session.layout_root = value

    @property
    def _last_dl(self):
        return self.session.display_list

    @_last_dl.setter
    def _last_dl(self, value) -> None:
        self.session.display_list = value

    def _clear_document_state(self) -> None:
        """R7-A01 第 5 点 — about / error 页（及任何非文档导航）原子
        清空旧文档状态，避免旧 DOM / Host / hit tree / focus 残留。

        审计 B05/A01："about/error 后旧元素不可点击、旧脚本不执行"——
        旧实现的 about 路径只换 bitmap，DOM/Host 仍指向上个文档，
        hit-test 会命中已卸载的元素。现统一在此清空，并推进
        文档版本（让旧 frame / task 因 generation 不匹配被丢弃）。

        R7-A01 — 文档内容（DOM / Host / 布局 / 显示列表 / 源 HTML+CSS /
        复用标志 / 版本）经 :meth:`DocumentState.reset_context` 就地
        原子清空；交互 / 表单状态仍为页面自身字段，单独归零。
        """
        self.session.reset_context()
        self.hover_id = None
        self.focus_id = None
        self.focus_chain = []
        self.active_form_field = None
        self._form_states = {}
        self._interaction_state = {}

    def scroll_to(self, yoffset: int) -> None:
        """P33.R2 — scroll the page to *yoffset* pixels from the top.

        The stored bitmap is the full document; scrolling re-clips the
        visible window (no re-render).  The offset is clamped to
        [0, content_height - height].
        """
        max_off = max(0, self.content_height - self.height)
        self.yoffset = max(0, min(int(yoffset), max_off))
        # R7-U01 (G06) — keep the unified coordinate transform in sync
        # with the scroll offset so hit_test_at's logical_to_css uses
        # the current scroll (审计: "命中测试应用逆变换").
        self._viewport.scroll_y = self.yoffset
        self._viewport.scroll_x = 0
        self._reclip()

    def scroll_by(self, dy: int) -> None:
        """P33.R2 — relative scroll (the GUI's MouseWheel handler)."""
        self.scroll_to(self.yoffset + int(dy))

    def set_zoom(self, zoom: float) -> None:
        """R7-U01 (G06) — set the page zoom so the hit-test inverse
        transform (``logical_to_css``) scales UI pixels to CSS document
        coordinates correctly.

        The audit's acceptance ("150%/200% 缩放点击位置正确") requires
        the zoom to feed the hit-test coordinate inverse transform.  A
        re-layout is *not* performed here (调用方在需要时经
        :meth:`render` 重排)；本方法只更新缩放状态并夹紧滚动到新的
        可滚范围（G06 step 4: 尺寸 / zoom 变化 → 夹紧滚动）。
        """
        zoom = max(0.1, min(4.0, float(zoom)))
        self._viewport.page_zoom = zoom
        # 夹紧滚动到新的可滚范围（文档内容不变，只有视口 CSS 尺寸变）。
        # 直接夹紧 + 同步 _viewport，不经 scroll_to（避免 _reclip 副作用）。
        max_off = max(0, self.content_height - self.height)
        self.yoffset = max(0, min(int(self.yoffset), max_off))
        self._viewport.scroll_y = self.yoffset
        self._viewport.scroll_x = 0

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
                  interaction_state: dict | None = None,
                  run_scripts: bool = True):
        """Render *html* via the toy browser pipeline; store the bitmap.

        P33.R2 — when *full_page* (default), the page is rendered at
        full content height (the layout root's content height) so long
        documents are not truncated to the viewport; the scroll
        offset clamps to the content.  A rendering cap of 32000 px
        keeps a pathological page from allocating a multi-GB bitmap
        (6 GB memory governance).  ``full_page=False`` keeps the
        legacy viewport-only render (faster for small pages, no
        scroll state).

        P33.P1 — *interaction_state* (optional dict with ``"hover"``
        and ``"focus"`` keys) feeds into ``css.apply`` for
        :hover/:focus pseudo-class rules.  When omitted, the page's
        own :attr:`hover_id` / :attr:`focus_id` are used.

        P0 (retest report) — *run_scripts* executes the page's inline
        <script> blocks (and external <script src> bodies when loaded
        via the URL pipeline) against a *shared* Host bound to this
        page's DOM tree, so DOM mutations made by the script are
        visible to the layout / hit-test / focus chain built below
        (the legacy path rendered the bitmap first and never ran the
        script — ``BEFORE``/``AFTER`` page states came out wrong).

        P0 (assessment report) — when a previous load already executed
        the document's scripts and stored ``self._dom_tree`` /
        ``self._script_host`` for this *same* source HTML, the script-
        mutated tree and Host are *reused* (no re-parse, no re-run):
        re-parsing ``html`` would silently discard every DOM mutation
        the script made (the reported "GUI HTTP 页面丢失脚本修改").
        Set ``_force_repars_html = True`` on the page instance to
        opt out (new-document semantics).
        """
        from toy_browser.app import browser
        from toy_browser.document import dom as _dom_mod
        from toy_browser.scripting import js as _js_mod
        # C01 — a new document load: bump the identity so any in-flight
        # frame / task belonging to the previous document is dropped.
        # R7-A01 — the bump is routed through the DocumentState session.
        self.session.doc_version.new_document()
        self.width = w
        self.height = h
        if not full_page:
            if run_scripts and not getattr(self, "_reuse_doc", False):
                _tree = _dom_mod.parse(html)
                _host = _js_mod.Host(_tree, lambda _s: None)
                # R7-S03 第 5 点 — sessionStorage 按 tab+origin 分区。
                _host.tab_id = getattr(self, "tab_id", None) or ""
                _host.execute_all()
            self.bitmap = browser.render_document(html, css, w, h)
            self.content_height = self.bitmap.height
            self._full_bitmap = None
            self.scroll_to(0)
            return
        from toy_browser.style import css as _css_mod
        from toy_browser.layout import layout as _layout_mod
        from toy_browser.rendering import paint_engine as _paint_mod
        # P33.P1 — build the interaction state dict for :hover/:focus.
        if interaction_state is None:
            interaction_state = {}
            if self.hover_id is not None:
                interaction_state["hover"] = self.hover_id
            if self.focus_id is not None:
                interaction_state["focus"] = self.focus_id
        # P0 (assessment report) — reuse the script-mutated Document +
        # Host when the source HTML is unchanged and a Host already
        # exists (the HTTP path runs the scripts first, then calls
        # load_html(run_scripts=False) to render; re-parsing here
        # would undo the script's DOM mutations).
        _reuse = (getattr(self, "_reuse_doc", False)
                  and run_scripts is False
                  and getattr(self, "_dom_tree", None) is not None
                  and getattr(self, "_last_html", "") == html
                  and getattr(self, "_script_host", None) is not None)
        if _reuse:
            tree = self._dom_tree
            self._last_css = css
        else:
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
        # P0 (retest report) — run the page's scripts against *this*
        # tree (shared Document lifecycle) so script mutations land in
        # the DOM the layout below builds.
        if run_scripts:
            _host = _js_mod.Host(tree, lambda _s: None)
            # R7-S03 第 5 点 — sessionStorage 按 tab+origin 分区。
            _host.tab_id = getattr(self, "tab_id", None) or ""
            self._script_host = _host
            _host.execute_all()
        styled = _css_mod.apply(tree, author_css, ua_css=ua_css,
                                interaction_state=interaction_state or None,
                                media_rules=media, supports_rules=supports,
                                viewport_w=float(w), viewport_h=float(h))
        # P33.P1 — store the layout root for hit-testing / focus chain.
        self._layout_root = None
        # P0 (assessment report) — when the caller *did* request a
        # re-parse (run_scripts=True), the previous script-mutated tree
        # is replaced and the reuse flag is cleared.  When run_scripts
        # was False (the HTTP path re-rendering the stored tree), the
        # flag stays so a subsequent restate keeps reusing the live
        # tree.
        if not _reuse:
            self._reuse_doc = False
        # Probe: layout with a huge viewport height so block boxes are
        # not forced to shrink-wrap the viewport.  The content height
        # is the bounding box of the root's children (max y + h).
        probe = _layout_mod.layout(styled, float(w), 10_000_000.0)
        content_h = self._content_height_probe(probe)
        # P33.R2 — cap the full-document height (32000 px ≈ 32 MP
        # bitmap ≈ 96 MB, safe under the 6 GB governance cap; the
        # legacy 16000 px cap truncated long pages at row 160).
        full_h = min(max(content_h, h), 32000)
        root2 = _layout_mod.layout(styled, float(w), float(full_h))
        self._layout_root = root2
        dl = _paint_mod.emit_commands(root2)
        self._last_dl = dl  # C02 批次 40 — 缓存 DL 供 CPUImage 活显示呈现
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
        controls).

        G04 — explicit positive ``tabindex`` values are ordered
        numerically (tabindex=1 before tabindex=2 …), and elements
        without an explicit tabindex (implicit value 0) follow in
        document order after all explicit ones — the HTML
        §11.5.1.4 tabindex-including rule, simplified to the
        two-level form the toy engine needs.  ``tabindex="-1`` remains
        focusable via script / click but is excluded from the Tab
        chain (collected in ``_implicit_focusable`` for G03)."""
        chain: list = []
        # (tabindex, doc_pos, idx) for elements with an explicit
        # positive tabindex; (doc_pos, idx) for implicit ones.
        explicit: list = []
        implicit: list = []
        counter = [0]
        doc_pos = [0]

        def _walk(node):
            # P1 (assessment report) — element-only indexing: the counter
            # advances on element nodes only, matching
            # :meth:`_node_index_to_dom` and :meth:`hit_test_at` (the
            # legacy all-node counter shifted every focus-chain entry
            # on text-node-rich trees, so Tab-activation hit the wrong
            # element or no element at all).
            if node.is_element():
                counter[0] += 1
                idx = counter[0]
                doc_pos[0] += 1
                tab = node.attr("tabindex")
                is_form = node.tag in ("input", "select", "textarea", "button", "a")
                has_href = node.tag == "a" and node.attr("href")
                if tab is not None:
                    try:
                        tab_v = int(tab)
                    except (ValueError, TypeError):
                        tab_v = None
                    if tab_v is not None and tab_v > 0:
                        # G04 — positive tabindex: explicit order tier.
                        explicit.append((tab_v, doc_pos[0], idx))
                        return
                    elif tab_v is not None and tab_v == 0:
                        # tabindex="0" participates in the implicit tier
                        # (document order), not the positive tier.
                        implicit.append((doc_pos[0], idx))
                        return
                    # tab_v < 0 or unparseable — excluded from Tab
                    # order (still script-focusable).
                elif is_form or has_href:
                    implicit.append((doc_pos[0], idx))
            for c in getattr(node, "children", []):
                _walk(c)

        _walk(tree)
        # G04 — explicit positive tabindex in numeric order (ties keep
        # document order), then the implicit tier in document order.
        explicit.sort(key=lambda t: (t[0], t[1]))
        implicit.sort(key=lambda t: t[0])
        self.focus_chain = [i for _, _, i in explicit] + [i for _, i in implicit]
        # Reset active field when the page changes.
        self.active_form_field = None
        self._form_states = {}

    def _node_index_to_dom(self, tree, idx: int):
        """P33.P1 — resolve a DOM node index (1-based, element-only,
        document order) to the actual DOM Node object.  O(n) walk; the
        focus chain is small enough that this is acceptable.

        P1 (assessment report) — the index counts *elements only*
        (matching :meth:`hit_test_at`'s id_map which only records
        ``node.is_element()`` nodes); the legacy version counted every
        node including text, so a text-node-rich tree shifted all
        element indices and a hit-test that returned the correct
        element index resolved to the wrong node (or ``None``) in
        :meth:`_click_dispatch`.  Element-only indexing is what the
        layout boxes carry in their ``dom`` back-link."""
        counter = [0]
        found = [None]

        def _walk(node):
            if node.is_element():
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
        or ``None`` when nothing is hit.  Coordinates are converted to
        CSS document space via the unified :class:`ViewportState`
        inverse transform (R7-U01 / G06): ``logical_to_css`` divides by
        ``zoom × device_scale`` and adds the scroll offset.  When the
        page is at 100% with no DPI scaling this degrades exactly to
        the legacy ``y + yoffset`` behaviour, so existing 100%-zoom
        callers are unaffected."""
        from toy_browser.layout import layout as _layout_mod
        root = getattr(self, "_layout_root", None)
        if root is None:
            return None
        # R7-U01 (G06) — unified coordinate inverse transform.  The
        # click coordinates come in as Tk *logical* UI pixels; convert
        # them to CSS document coordinates (the space the layout boxes
        # live in) so a hit at the same physical point resolves to the
        # same element at any zoom / DPI.  Falls back to the legacy
        # ``y + yoffset`` when no ``_viewport`` exists (pre-G06 state).
        vp = getattr(self, "_viewport", None)
        if vp is not None:
            doc_x, doc_y = vp.logical_to_css(float(x), float(y))
        else:
            doc_x, doc_y = float(x), float(y) + self.yoffset
        hit = _layout_mod.hit_test(root, float(doc_x), float(doc_y))
        # R7-C02 §5 — stacking context 坐标同源：若页面含 transform/
        # scroll 容器（SC 路径偏移非 identity），经 hit_test_sc 累加
        # 路径偏移做命中补偿（渲染侧 _tag_scroll 减同一偏移）。
        # 无偏移时 hit_test_sc 退化等价裸 hit_test（向后兼容主路径）。
        try:
            hit = _layout_mod.hit_test_sc(root, float(doc_x), float(doc_y))
        except AttributeError:
            # 旧 layout 模块（无 hit_test_sc）退化裸 hit_test。
            hit = _layout_mod.hit_test(root, float(doc_x), float(doc_y))
        if hit is None or hit.dom is None:
            return None
        # R7-U01 — build the index map (DOM node → element-order index).
        # 统一计数源：与 :meth:`_node_index_to_dom` 和
        # :meth:`_build_focus_chain` 一致，只在 element 节点上推进
        # 计数（文本节点不占位）。旧实现在 is_element 判断**之前**
        # 就 counter+=1，于是文本节点丰富页面上 hit_test_at 返回的
        # 索引与 focus_chain / _node_index_to_dom 的索引整体偏移，
        # 点击 / Tab 激活落到错误元素（审计 §5 U01 第 1 点"两套遍历
        # 计数"）。
        counter = [0]
        id_map: dict = {}

        def _walk(node):
            if node.is_element():
                counter[0] += 1
                id_map[id(node)] = counter[0]
            for c in getattr(node, "children", []):
                _walk(c)

        tree = getattr(self, "_dom_tree", None)
        if tree is not None:
            _walk(tree)
        # R7-U01 (GUI audit §4 P01) — text-node hit attribution.
        # ``hit_test`` already falls back one level (``dom is None ->
        # parent``), but a *text* child's parent box may still carry a
        # text-node ``dom`` (e.g. an anonymous text box inside a
        # ``<span>`` or a nested ``<span>text</span>``), and ``id_map``
        # only records element nodes.  Without this climb a click on
        # the text is silently dropped (id_map lookup -> None).  Climb
        # ``hit.dom`` to the nearest *element* ancestor so the hover /
        # click consumer resolves to the real interactive element (the
        # audit's "button preceded by text / whitespace / nested span"
        # acceptance).  Document nodes stop the climb.
        dom = hit.dom
        guard = 0
        while (dom is not None
                 and not dom.is_element()
                 and guard < 1000):
            dom = getattr(dom, "parent", None)
            guard += 1
        if dom is None:
            return None
        return id_map.get(id(dom))

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
        state applied via ``css.apply(interaction_state=...)``.

        P0 (assessment report) — the restate must NOT re-parse the
        source HTML: it reuses the stored ``_dom_tree`` (which may
        carry script / interaction mutations — e.g. a button's
        ``onclick`` handler that changed ``textContent``) so that
        re-rendering for :hover/:focus does not wipe out the existing
        DOM state (the report's "重排不会清除既有 DOM 状态").

        C01 — a restate re-solves layout without changing the document
        identity: bump ``layout_revision`` (NOT ``document_id``), and
        tag the fresh frame with the new ``frame_id``.
        """
        from toy_browser.document import dom as _dom_mod
        from toy_browser.style import css as _css_mod
        from toy_browser.layout import layout as _layout_mod
        from toy_browser.rendering import paint_engine as _paint_mod
        from toy_browser.app import browser as _browser_mod
        html = getattr(self, "_last_html", "")
        css_text = getattr(self, "_last_css", "")
        if not html:
            return
        # C01 — layout re-solve (restate), not a new document.
        # R7-A01 — routed through the DocumentState session.
        self.session.mark_restate()
        # P0 (assessment report) — reuse the live tree when available
        # (the HTTP / local-script load paths store it); fall back to
        # a fresh parse only when no tree has been stored yet.
        tree = getattr(self, "_dom_tree", None)
        if tree is None:
            tree = _dom_mod.parse(html)
            self._dom_tree = tree
        self._save_form_state_to_dom()
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
        full_h = min(max(content_h, self.height), 32000)
        root2 = _layout_mod.layout(styled, float(self.width), float(full_h))
        self._layout_root = root2
        dl = _paint_mod.emit_commands(root2)
        self._last_dl = dl  # C02 批次 40 — 缓存 DL 供 CPUImage 活显示呈现
        bmp = _paint_mod.rasterize(dl, self.width, full_h)
        self.content_height = full_h
        self._full_bitmap = bmp
        # C01 — tag the fresh frame with its id so a presenter can
        # drop superseded frames (the full-document bitmap is the
        # frame source; the clipped viewport is derived from it).
        # R7-A01 — routed through the DocumentState session.
        self.session.mark_render()
        self._reclip()

    def _set_form_value(self, idx: int, value: str) -> None:
        """P33.P1 — store the value of a form field (input/textarea)
        by DOM node index.  The value is kept in :attr:`_form_states`
        and applied to the DOM on the next re-render."""
        self._form_states[idx] = {"value": value, "dirty": True}

    def _toggle_checkbox(self, idx: int) -> None:
        """P33.P1 — toggle a checkbox / radio button's checked state.

        G02 — when no state has been recorded for *idx* yet, seed it
        from the DOM's own ``checked`` attribute so the first toggle
        flips the *actual* initial state (a pre-checked box un-checks,
        an unchecked box checks) instead of defaulting to unchecked.
        """
        st = self._form_states.get(idx)
        if st is None:
            st = {"value": "", "checked": False}
            # Seed from the DOM when possible.
            tree = getattr(self, "_dom_tree", None)
            if tree is not None:
                node = self._node_index_to_dom(tree, idx)
                if node is not None and node.is_element():
                    st["checked"] = node.attr("checked") is not None
            self._form_states[idx] = st
        st["checked"] = not st.get("checked", False)
        st["dirty"] = True

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

    def activate_focused_keyboard(self) -> None:
        """G02 — keyboard activation entry (Enter / Space) without a
        Tk widget tree.

        Mirrors :meth:`TabBrowser.activate_element` but stays inside
        the page: run the focused element's click handler against the
        shared Host (when present), then the control's default action
        (checkbox/radio toggle, input/textarea/select focus).  The
        Tk path (``_activate_focused`` → ``activate_element``) adds
        canvas refresh + navigation; this variant is what the
        assessment's *SAFE/CLICKED* button test and the unit tests
        drive, so it is testable without a display.
        """
        idx = self.focus_id
        if idx is None:
            return
        tree = getattr(self, "_dom_tree", None)
        node = self._node_index_to_dom(tree, idx)
        if node is None or not node.is_element():
            return
        tag = (node.tag or "").lower()
        inp_type = (node.attr("type") or "").lower()
        # 1 — fire the click handler before the default action (G02).
        host = getattr(self, "_script_host", None)
        if host is not None:
            from toy_browser.scripting import js as _js_mod
            _el = _js_mod.DomElement(node, host)
            host.execute_event_bubbled(_el, "click")
        # 2 — default action by control type.
        if tag in ("input", "button"):
            if inp_type in ("checkbox", "radio"):
                self._toggle_checkbox(idx)
                self._save_form_state_to_dom()
            else:
                self.active_form_field = idx
        elif tag == "textarea":
            self.active_form_field = idx
        elif tag == "select":
            self._save_form_state_to_dom()
        self._reapply_interaction()


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
                 client: "net.HttpClient | None" = None,  # noqa: F821
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
            # R7-A01 — about 是合成页（非文档），原子清空旧文档状态，
            # 避免旧 DOM/Host/hit tree 残留（"about/error 后旧元素
            # 不可点击、旧脚本不执行"）。
            self._clear_document_state()
            self.bitmap = self._about_page(url, w, h)
            self.content_height = self.bitmap.height if self.bitmap else 0
            self._full_bitmap = None
            self.scroll_to(0)
            return
        # P0 (assessment report) — a new document: any previously
        # stored script-mutated DOM/Host is stale; force re-parse so
        # load_html does not reuse it.
        # R7-A01 — version bump now lives on the DocumentState session
        # (the single owner of the page's document lifecycle).
        self.session.doc_version.new_document()
        self._reuse_doc = False
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
        from toy_browser.network import net
        from toy_browser.document import dom as _dom_mod
        lowered = url.lower()
        if lowered.startswith("http://") or lowered.startswith("https://"):
            self.url = url
            self.title = url
            try:
                # R4 stage 3 — ensure a real HttpClient exists so the
                # in-page fetch()/XHR transport can issue requests.
                # The legacy path created a throwaway client inside
                # fetch_html when *client* was None, but that client
                # was discarded; the GUI now keeps it for the
                # subresource loader AND the JS transport.
                eff_client = client or net.HttpClient(timeout=10.0)
                html, resp = net.fetch_html(
                    url, timeout=10.0, client=eff_client)
                # P33.R2 — fetch subresources (stylesheets / images /
                # scripts) against the document DOM, using the shared
                # keep-alive client (the parallel loader when *parallel*).
                # P0 (retest report) — the legacy path only joined
                # stylesheets and re-parsed the document in load_html,
                # so the document's CSP was never applied and external
                # <script src> bodies were downloaded but never run.
                # P0 (assessment report) — parse the DOM *once* and keep
                # it; the subresource loader, script execution and the
                # render all see the same tree, so script mutations are
                # visible in the bitmap and in the hit-test / focus
                # chain.  The legacy path called load_html() which
                # re-parsed the source HTML and silently discarded the
                # script mutations (HTTP 页面仍显示 SAFE while local
                # HTML 已显示 AFTER).
                from toy_browser.scripting import js as _js_mod
                tree = _dom_mod.parse(html)
                self._dom_tree = tree
                self._last_html = html
                self._last_css = ""
                self._reuse_doc = True
                loader = (net.load_subresources_parallel
                          if parallel else net.load_subresources)
                # R4 stage 0 — unified document policy context: the
                # document's *own* response headers are the single
                # source of truth.  The legacy
                # ``getattr(resp, "content_security_policy", "")``
                # read a non-existent attribute and always yielded ""
                # (the 4th report's csp_self/csp_inline GUI
                # divergence); the context is built once and shared by
                # subresource loading and script execution below.
                _policy = net.DocumentPolicy.from_response(resp)
                _enforce_policy = _policy.enforce
                sub = loader(
                    tree, base_url=url, client=eff_client,
                    enforce_policy=_enforce_policy,
                    policy=_policy,
                )
                extra_css = "".join(
                    (r.body or b"").decode("utf-8", "replace")
                    for r in sub.get("stylesheet", {}).values()
                    if r is not None and getattr(r, "ok", True))
                self._last_css = extra_css
                # P0 (assessment report) — build the script cache
                # (downloaded once per URL, execution per element) and
                # run the document's scripts in document order against
                # the *shared* tree: inline blocks gated by the
                # document's CSP (script-src / default-src, including
                # nonce matching), plus the fetched external <script
                # src> bodies — each element occurrence executes its
                # own copy of the body (a duplicated tag runs it
                # twice, mirroring the HTML spec).
                _host = _js_mod.Host(tree, lambda _s: None)
                # R7-S03 第 5 点 — sessionStorage 按 tab+origin 分区。
                _host.tab_id = getattr(self, "tab_id", None) or ""
                self._script_host = _host
                # R4 stage 0 — script execution sees the SAME policy
                # context as subresource loading (one source of truth
                # from the document response headers).
                _csp_directives = _policy.csp_directives
                _host.doc_origin = _policy.doc_origin
                # R4 stage 1 — absolute document base URL so <script
                # src> relative attributes resolve against the
                # pipeline's absolute-URL cache.
                _host.doc_url = url
                # R4 stage 3 — in-page fetch()/XMLHttpRequest reach the
                # SAME real service the document load used (shared
                # keep-alive client), mirroring the CLI pipeline.
                # R7-S03 第 2 点 — 与 CLI 一致：in-page fetch/XHR 带
                # 文档归属 + 凭据默认 same-origin。
                if eff_client is not None:
                    try:
                        eff_client.top_level_url = url
                    except Exception:
                        pass
                _host._fetch_transport = net.FetchTransport(
                    eff_client, base_url=url, document_url=url)
                _script_cache: dict = {}
                for url_key, r in sub.get("script", {}).items():
                    if r is not None and getattr(r, "ok", True):
                        _script_cache[url_key] = (
                            (r.body or b"").decode("utf-8", "replace"))
                    else:
                        _script_cache[url_key] = None
                # R7-S01 第 1 点 — 远程脚本信任闸门（与 CLI 共用同一
                # 默认闸门实例，避免两条主链两套策略）。默认 local_only：
                # 任意网络页面的脚本默认不执行；回环 / file: / about:
                # 等可信源不受影响。判定挂 _host 上可观测，被拒则跳过
                # 执行（不是"跑了再丢结果"）。
                from toy_browser.scripting import script_trust as _trust_mod
                _trust = _trust_mod.default_script_trust_gate().decide(url)
                _host.script_trust_decision = _trust
                if _trust.allowed:
                    _host.execute_scripts_in_order(_script_cache,
                                                   _csp_directives)
                # R4 stage 3 — the fetch/XHR callbacks fire as
                # microtasks (fetch .then) or synchronously (XHR
                # onload); execute_scripts_in_order already flushes
                # timers + microtasks at its tail, but the GUI's
                # PageInstance reads the DOM tree via _dom_tree
                # (the same Node object the script host mutated), so
                # no extra flush is needed here — the flush inside
                # execute_scripts_in_order covers it.
                # B05（GUI 桥）— 若本页内联脚本发了 location 导航任务
                # （location.replace / assign / href=），消费
                # ``host._pending_nav`` 跟跳。
                # R7-A01 — 统一 B05 follow 契约：经
                # navigation.select_pending_navigation_target 解析
                # （与 CLI browser.render_document_from_url 同契约，
                # 消除 GUI/CLI 重复实现）。
                from toy_browser.app.navigation import (
                    select_pending_navigation_target,
                )
                pending = list(getattr(_host, "_pending_nav", []) or [])
                follow = select_pending_navigation_target(pending, url)
                if follow is not None:
                    # 跟跳到最终目标（循环预算由 seen 去重约束；
                    # 每跳都重新 fetch 并再检查其 _pending_nav）。
                    self.load_url(follow, w, h,
                                  record_history=record_history,
                                  client=client, parallel=parallel)
                    return
                # P0 (assessment report) — render the *script-mutated*
                # tree (run_scripts=False skips the legacy re-parse;
                # _reuse_doc routes load_html to the stored
                # _dom_tree / _script_host instead of a fresh parse).
                self.load_html(html, extra_css, w, h, run_scripts=False)
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

    def _sync_history_stacks(self) -> None:
        """R7-A01 历史栈收敛 — 把游标模型投影回双栈字段。

        ``self._hist.cursor`` 始终指向**当前页**所在的历史项；投影规则：

        * ``back_stack`` = 游标之前的历史项（oldest→当前前一项）；
        * ``fwd_stack`` = 游标之后的历史项（当前后一项→newest）。

        这与旧双栈模型一致：当前页 URL 在 ``self.url``，双栈只保留
        已提交的其它历史项。外部消费者据此读历史形态；所有提交 /
        移动都经游标，不直接改这两个 list。
        """
        entries = self._hist.entries
        cur = self._hist.cursor
        if cur < 0:
            self.back_stack = []
            self.fwd_stack = []
            return
        self.back_stack = [e.url for e in entries[:cur]]
        self.fwd_stack = [e.url for e in entries[cur + 1:]]

    def _commit_history(self, url: str, source: str = "address",
                       dedup: bool = True) -> None:
        """R7-A01 历史栈收敛 — 一次新导航提交到本标签游标（G09 step 3
        单一入口）。

        等价旧双栈时序：导航前先把"被离开的旧页"（``self.url``）落到
        cursor 项，再提交新目标 *url* 使 cursor 推进到新目标。这样
        投影出的 back_stack 含被离开的旧页（与旧模型 push X 一致），
        fwd_stack 被新导航清空。"""
        # 1) 保证"被离开的旧页"是 cursor 指向项（首次导航 / go 后
        #    再导航时 cursor 可能指向更早项或 -1）。
        old = self.url
        if old and self._hist.cursor < 0:
            self._hist.commit(old, source="address", dedup=False)
        elif old and self._hist.cursor >= 0 and \
                self._hist.entries[self._hist.cursor].url != old:
            # cursor 已落后于当前页（例如 go_back 后又直接 load）：
            # 把当前页作为新 cursor 项补进（清掉其后前进项）。
            self._hist.commit(old, source="address", dedup=False)
        # 2) 提交新目标（cursor 推进；清掉前进项 = 旧模型 fwd_stack.clear）。
        self._hist.commit(url, source=source, dedup=dedup)
        self._sync_history_stacks()

    def can_back(self) -> bool:
        """R7-A01 历史栈收敛 — 游标可后退（工具栏 / 键盘启停依据）。"""
        return self._hist.can_back()

    def can_forward(self) -> bool:
        """R7-A01 历史栈收敛 — 游标可前进。"""
        return self._hist.can_forward()

    def go_back(self):
        # R7-A01 历史栈收敛 — 经游标后退：back() 把游标移向前并返回
        # 新位置项（后退目标），双栈随后投影同步。
        tgt = self._hist.back()
        if tgt is not None:
            self._sync_history_stacks()
            self._load_no_history(tgt.url, self.width, self.height)

    def go_forward(self):
        # R7-A01 历史栈收敛 — 经游标前进：forward() 把游标移向后并
        # 返回新位置项（前进目标），双栈随后投影同步。
        tgt = self._hist.forward()
        if tgt is not None:
            self._sync_history_stacks()
            self._load_no_history(tgt.url, self.width, self.height)

    def _load_no_history(self, url: str, w: int, h: int):
        """Load *url* without touching back/fwd stacks (internal)."""
        url = url.strip()
        if not url:
            return
        if url.lower().startswith("about:"):
            self.url = url
            self.title = url
            # R7-A01 — about 合成页原子清空旧文档状态。
            self._clear_document_state()
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
        from toy_browser.network import net
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
        from toy_browser.app import browser as _browser_mod
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
            # P0 (retest report) — use a non-shadowing name: the legacy
            # `browser = getattr(self, "_browser", None)` overwrote the
            # module import, so the trailing
            # `browser.render_document(html, "", w, h)` resolved to the
            # owning TabBrowser (or None → AttributeError on the
            # bookmarks page).
            _owning = getattr(self, "_browser", None)
            hist = getattr(_owning, "history", None) if _owning else None
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
            _owning = getattr(self, "_browser", None)
            bmarks = getattr(_owning, "bookmarks", None) if _owning else None
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
        return _browser_mod.render_document(html, "", w, h)

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

    # P0 (agent report) — the legacy duplicate go_back / go_forward /
    # _load_no_history definitions (lines ~662–722) are removed; the
    # canonical implementations above (lines ~527–587) already do the
    # same work.  Removing them clears the F811 warnings.

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

        # G04 — centralise keyboard routing between chrome / document /
        # editor / dialog.  The address entry is the chrome owner;
        # everything inside a tab's page frame is the document owner.
        self._focus_manager = foci.FocusManager(self._widget_owner)

        # --- build UI ---
        self._build_menu()
        self._build_toolbar()
        self._build_tab_frame()
        self._build_canvas()
        self._build_statusbar()

        # --- initial tab ---
        # B01 批次 55 — 启动参数经统一路由判定（本地文件 / URL / blank）。
        # 在此之前先建 BGuiWiring（统一导航控制器 + 文档上下文），使
        # 启动导航与后续地址栏 / 链接导航共享同一 NavigationController
        # 语义（B01 "所有导航经单一控制器"不变式），且其 nav 的
        # current_url 可被初始导航即时更新。
        from toy_browser.app.b_series_wiring import BGuiWiring, resolve_initial_route
        self._b_wiring = BGuiWiring()
        page = PageInstance()
        page._browser = self
        route = resolve_initial_route(initial_path, os.path.isfile)
        if route.route == "local_file":
            page.load_file(route.target, 800, 600)
            self._b_wiring.nav.current_url = page.url or "about:blank"
        elif route.route == "url":
            # URL 形态（http(s)/about/裸主机等）— 经统一控制器提交，
            # 实际 I/O 走既有 load_url（不改渲染路径），控制器记录
            # 目标 / 历史 / 地址栏语义（B01 统一入口）。
            self._b_wiring.nav.current_url = route.target
            self._b_wiring.nav.address_text = route.target
            page.load_url(route.target, 800, 600)
        else:
            page.url = "about:blank"
            page.title = "about:blank"
            page.bitmap = page._about_page("about:blank", 800, 600)
        self.tabs.append(page)
        # G09 — 批次 37：统一标签会话协议层（稳定 tab_id + 关闭重建
        # 映射 + 旧回调防护 + 任务门控）。每个 PageInstance 配一个稳定
        # tab_id，``self._tab_ids[idx]`` 与 ``self.tabs[idx]`` 对应。
        # 旧回调（持旧 idx 的加载 / 历史闭包）经 :meth:`_g09_resolve`
        # 解析为当前 tab_id，失效则 no-op（不串页）。
        from toy_browser.app.tab_session import TabSession
        self._g09_session = TabSession()
        self._tab_ids: list[int] = [self._g09_session.open_tab()]
        # R7-S03 第 5 点 — 首个标签也要有稳定的 session 分区身份。
        try:
            self.tabs[0].tab_id = self._tab_ids[0]
        except Exception:
            pass
        self._ensure_page_frame(0)
        self._show_tab(0)

        # G03 — 批次 39：原生覆盖层控制器（input/textarea 的活动编辑
        # 层；位置同步 / 撤销经批次 34 OverlayPlacement 协议驱动，
        # 编辑逻辑经批次 7/9 EditorState）。覆盖层在激活表单控件时
        # 由 :meth:`_g03_ensure_overlay` 附着；G11 路由经
        # :meth:`_g11_get_router` 的 focus_in_edit / has_active_field
        # 反映覆盖层活动状态（编辑层按键路由到覆盖层）。
        from toy_browser.app.editor_overlay import EditorOverlayController
        self._g03_overlay = EditorOverlayController()

        # B 系列批次 42 — 活显示 GUI 接线：把 B01 NavigationController +
        # B07 activate_element + B08 DocumentContext 接成 BGuiWiring 统一
        # 分派入口。地址栏 / 链接 / 表单分派经 :meth:`_b_dispatch_address`
        # / :meth:`_b_dispatch_link` / :meth:`_b_dispatch_form` 走 BGuiWiring
        # （资源三态 + 任务版本门控），导航执行仍走既有
        # :meth:`_commit_navigation` / :meth:`_navigate_to`（不改呈现路径）。
        # （批次 55：self._b_wiring 已提前到初始标签导航前创建，
        # 使启动参数也经统一控制器提交——此处不再重复构造。）

        # G07 批次 46 — 真实 multiprocessing 文档进程池（多核收益落地）：
        # 导航 / resize / 输入经 post 投递（不阻塞 UI），事件循环经
        # drain 取回紧凑帧（经 C01 门控后应用），关标签经 close_tab
        # 丢弃在途身份（G09 step 4）。multiprocessing 不可用 / spawn
        # 失败时自动回退内置同步 worker（UI 不崩）。UI 销毁时经
        # _g07_doc_server_shutdown 清理子进程。
        self._g07_doc_server = self._g07_spawn_doc_server()
        # 活显示验收入口：UI 销毁钩子统一清理子进程（防泄漏）。
        self._g07_ui_closing = False
        # R7-A02 第 1 点 — UI 心跳 + task trace（默认导航入口接线，
        # 确认"真正调用路径"而非"有这个模块"）。
        # R7-A02 第 3 点 — 版本化 IPC 门控（旧任务不可覆盖新页）+
        # 命令合并 / 背压 / 取消确认。
        # 局部导入（与 gui.py 既有风格一致）：document_process 不可
        # 用时全部置 None，调用点经 getattr 判空退化（不崩）。
        try:
            from toy_browser.app.document_process import (
                UiHeartbeat, TaskTrace, NavVersionGate,
                CommandCoalescer, BackpressureGate, CancelTracker)
            self._r7_a02_heartbeat = UiHeartbeat()
            self._r7_a02_trace = TaskTrace()
            self._r7_a02_nav_gate = NavVersionGate()
            self._r7_a02_coalescer = CommandCoalescer()
            self._r7_a02_backpressure = BackpressureGate()
            self._r7_a02_cancel = CancelTracker()
        except Exception:
            self._r7_a02_heartbeat = None
            self._r7_a02_trace = None
            self._r7_a02_nav_gate = None
            self._r7_a02_coalescer = None
            self._r7_a02_backpressure = None
            self._r7_a02_cancel = None
        # 每 tab 当前 navigation_id（导航时 +1，与 worker 同源）。
        self._r7_a02_nav_id: dict = {}
        # R7-A02 第 6 点 — 文档工作模式 + 能力声明校验（不靠名字
        # 宣称"已多核渲染"）+ 显式单进程调试模式。
        try:
            from toy_browser.app.doc_process_mode import (
                SingleProcessDebugMode)
            self._r7_a02_debug_mode = SingleProcessDebugMode()
        except Exception:
            self._r7_a02_debug_mode = None

        try:
            self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        except Exception:
            pass

        # --- bindings ---
        self.root.bind("<Control-l>", lambda e: self._focus_address())
        self.root.bind("<Control-t>", lambda e: self._new_tab())
        self.root.bind("<Control-w>", lambda e: self._close_tab())
        self.root.bind("<Control-r>", lambda e: self._reload())
        self.root.bind("<Alt-Left>", lambda e: self._back())
        self.root.bind("<Alt-Right>", lambda e: self._forward())
        # P33.P1 — keyboard focus navigation (Tab / Shift+Tab).
        self.root.bind("<Tab>", lambda e: self._tab_focus_routed(True))
        self.root.bind("<Shift-Tab>", lambda e: self._tab_focus_routed(False))
        self.root.bind("<Return>", lambda e: self._return_routed())
        # P33.P1 — click dispatch: hit-test + form toggle + focus.
        self.root.bind("<Button-1>", lambda e: self._click_dispatch(e))
        # P33.P1 — mouse-move: hover state update (throttled).
        self.root.bind("<Motion>", lambda e: self._hover_dispatch(e))
        # G11 — 批次 35/36：完整按键集进 Tk 绑定（与上面 G04 专门绑定
        # 共存；G11 handler 只对增量键 break，Tab/Return/Control 让专门
        # 绑定接管）。
        self._install_g11_bindings()

    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open…", command=self._open_file)
        file_menu.add_command(label="New Tab", accelerator="Ctrl+T", command=self._new_tab)
        file_menu.add_command(label="Close Tab", accelerator="Ctrl+W", command=self._close_tab)
        file_menu.add_separator()
        # R7-U01 — File>Quit 经 _on_window_close 统一关闭（先关文档
        # server + 清理 PhotoImage + destroy + quit），不再裸调 quit。
        file_menu.add_command(label="Quit", command=self._on_window_close)
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

    # ------------------------------------------------------------------
    # G01/G05 — scroll protocol and wheel routing (extracted for testability)
    # ------------------------------------------------------------------

    def handle_scroll_command(self, tab_id: int, operation: str, *args) -> None:
        """G01 — Tk scrollbar command handler, extracted for independent
        testing.  *operation* is the Tk scrollbar protocol string
        (``"moveto"``, ``"scroll"``, ``"set"``, ``"none"``); *args*
        carries the Tk arguments.

        Protocol (Tk scrollbar man page):
          - moveto fraction   →  target = clamp(fraction * content_height,
                                      0, scroll_max)
          - scroll count units →  scroll_by(count * LINE_STEP)
          - scroll count pages →  scroll_by(count * viewport_height * 0.9)

        The Tk ``command`` callback is called as
        ``command('moveto', fraction)`` or ``command('scroll', count,
        'units')``; the legacy code only handled ``movements``/``units``
        which never matched Tk's actual protocol strings, so drags were
        silently ignored.
        """
        page = self.tabs[tab_id]
        m = page.scroll_max()
        if operation == "moveto":
            # fraction of total content height (NOT of scroll_max).
            if args:
                try:
                    fraction = float(args[0])
                except (TypeError, ValueError):
                    return
                target = int(fraction * page.content_height)
                page.scroll_to(target)
            self._refresh_canvas(tab_id)
        elif operation == "scroll":
            # Tk: command('scroll', count, 'units' | 'pages')
            count = int(args[0]) if args else 0
            unit = (args[1] if len(args) > 1 else "units")
            if unit == "pages":
                step = int(count * page.height * 0.9)
            else:
                step = count * self._LINE_STEP
            page.scroll_by(step)
            self._refresh_canvas(tab_id)
        # "set" and "none" are no-ops (Tk uses them for range updates).

    _LINE_STEP = 40  # pixels per scroll-unit arrow / wheel notch

    def handle_wheel_event(self, tab_id: int, delta: int) -> None:
        """G05 — unified wheel input: convert a raw Tk/OS delta to a
        scroll step, call :meth:`PageInstance.scroll_by`, refresh the
        canvas.  *delta* is the raw OS value (Windows: ±120 per notch
        or small positive/negative values; other platforms: ±1..±n).

        Small-delta accumulation: if ``abs(delta) < 120`` we treat it
        as a high-precision trackpad event and scroll by 10 px per
        unit; otherwise (coarse wheel) we scroll by 40 px per 120-unit
        delta (≈ 40 px per notch).
        """
        page = self.tabs[tab_id]
        if delta > 0:
            step = -self._LINE_STEP if delta >= 120 else -10
        elif delta < 0:
            step = self._LINE_STEP if delta <= -120 else 10
        else:
            return  # delta == 0: no movement
        page.scroll_by(step)
        self._refresh_canvas(tab_id)

    # ------------------------------------------------------------------

    def _ensure_page_frame(self, idx: int) -> None:
        """Create the notebook tab widget for *idx* if not already present.

        P33.R2 — each tab now hosts a *scrollable* viewport: the
        rendered page bitmap (the scroll window of the full-document
        bitmap) plus a vertical :class:`ttk.Scrollbar` that drives
        :meth:`PageInstance.scroll_to`.  The wheel binding on the
        viewport calls :meth:`PageInstance.scroll_by`.

        G01/G05 — the scrollbar command is wired through
        :meth:`handle_scroll_command` (testable) and the wheel is
        routed through :meth:`handle_wheel_event` (unified delta
        handling).
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
        # G05 — mouse-wheel scrolling: the wheel event is routed
        # through handle_wheel_event (unified delta → step conversion).
        def _wheel(event):
            self.handle_wheel_event(idx, event.delta)
        # Bind on the *label* (the actual display widget) AND on the
        # viewport frame so wheel events fire regardless of which
        # child has pointer focus.
        label.bind("<MouseWheel>", _wheel)
        label.bind("<Shift-MouseWheel>", _wheel)
        viewport.bind("<MouseWheel>", _wheel)
        viewport.bind("<Shift-MouseWheel>", _wheel)
        # G01 — wire the scrollbar via the testable handler.
        def _sb(operation, *args):
            self.handle_scroll_command(idx, operation, *args)
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

        C02 — route the pixel packing through the contiguous-buffer
        contract (:class:`cpu_image.CPUImage`) instead of the legacy
        per-pixel Python loop: one batch ``struct.pack`` per row, no
        per-pixel temporary bytes (the 5th-round measurement put the
        800×600 conversion chain at ~1209 ms; C02 targets ≥80% below
        that on this path).
        """
        from toy_browser.rendering import cpu_image as _cpuimg
        data = _cpuimg.CPUImage.from_bitmap(bmp).to_ppm_bytes()
        # P37 (G09) — 显式 master + 显式名字：Tk image 名由**全局 Tcl
        # 解释器**顺序分配（pyimage1/2/…）。_rebuild_tabs 里
        # canvas_var.clear() 让旧 PhotoImage 被 GC → Tcl 注销同名
        # image；新 PhotoImage 再创建时全局 image 注册表若脏化会报
        # "image pyimageN doesn't exist"（关标签重建的 latent bug）。
        # 显式命名 + 绑定 root，使 image 名不依赖全局分配顺序。
        return tk.PhotoImage(master=self.root,
                             name=f"toytab_{idx}", data=data)

    # ------------------------------------------------------------------
    # C02 批次 40 — 活显示 rasterize 切到 CPUImage 连续 buffer
    # ------------------------------------------------------------------
    def _c02_rasterize_cpu_image(self, page) -> "Optional[CPUImage]":
        """C02 批次 45 — 活显示呈现入口：把页面光栅化到
        :class:`CPUImage`（RGB8 连续 buffer，不逐帧还原 int 列表）。

        批次 45 起改用 :func:`paint_engine.rasterize_to_canvas`（命令
        分派**直接画到** :class:`CPUCanvas`，消除 scratch Bitmap 合并
        层——C02 目标"避免每次呈现又从连续缓冲区还原大 int 列表"的
        写侧落地），取页面缓存的 ``_last_dl``（restate / initial load
        时设置），无缓存（未加载页面）时返回 None。
        """
        from toy_browser.rendering import paint_engine as _pe
        dl = getattr(page, "_last_dl", None)
        if dl is None:
            return None
        full_h = page.content_height or page.height
        canvas = _pe.rasterize_to_canvas(dl, page.width, full_h)
        return canvas.img

    def _c02_refresh_canvas_cpu(self, idx: int) -> None:
        """C02 批次 40 — 活显示刷新：经 CPUImage 连续 buffer 呈现
        （替代 ``_bitmap_to_photopix`` 的 per-frame int 列表还原）。

        走 :meth:`_c02_rasterize_cpu_image` 得全页 CPUImage，再经
        :meth:`CPUCanvas.clip_viewport` 裁到当前滚动视口（与
        ``_clip_bitmap`` 语义一致但不还原 int 列表），``to_ppm_bytes``
        供 Tk PhotoImage；显式 master + 名字（P37 G09 同款，防全局
        image 注册表脏化）。
        """
        page = self.tabs[idx]
        full = self._c02_rasterize_cpu_image(page)
        if full is None:
            self._refresh_canvas(idx)  # 回退到 Bitmap 路径
            return
        from toy_browser.rendering.rasterize_cpu import CPUCanvas
        canvas = CPUCanvas(full)
        viewport = canvas.clip_viewport(page.yoffset, page.height)
        data = viewport.img.to_ppm_bytes()
        photo = tk.PhotoImage(master=self.root,
                              name=f"toytab_c02_{idx}", data=data)
        self.canvas_var[idx] = photo
        frame = self.page_frames.get(idx)
        if frame is None:
            return
        label: tk.Label = frame._label
        self._safe_set_image(label, photo, idx)

    def _c02_stats(self) -> dict:
        """C02 批次 40 — 诊断（当前 CPUImage 呈现路径可用）。"""
        return {"cpu_image_path": True,
                "module": "toy_browser.rendering.rasterize_cpu"}

    # ------------------------------------------------------------------
    # G07 批次 46 — 真实 multiprocessing 文档进程池（活显示验收入口）
    # ------------------------------------------------------------------
    def _g07_spawn_doc_server(self):
        """起真实 ``multiprocessing`` 文档进程池（多核收益）。

        经 :func:`toy_browser.app.document_process.spawn_doc_process_server`
        spawn 子进程 worker；``multiprocessing`` 不可用 / spawn 失败时
        返回 ``on_fallback`` 的池（内置同步 worker，UI 不崩）。"""
        from toy_browser.app.document_process import spawn_doc_process_server
        return spawn_doc_process_server()

    def _g07_post_nav(self, tab_id: int, url: str, seq: int = 0) -> bool:
        """G07 批次 46 — 经真实 spawn 池投递导航命令（不阻塞 UI，
        多核收益；回退态走内置同步 worker）。返回是否成功投递。"""
        from toy_browser.app.document_process import (
            DocCommand, DOC_CMD_NAVIGATE)
        return self._g07_doc_server.post(
            DocCommand(tab_id=tab_id, kind=DOC_CMD_NAVIGATE,
                       payload={"url": url}, seq=seq))

    def _g07_post_resize(self, tab_id: int, w: int, h: int,
                         seq: int = 0) -> bool:
        """G07 批次 46 — 经真实 spawn 池投递 resize 命令（几何重算，
        多核收益）。"""
        from toy_browser.app.document_process import (
            DocCommand, DOC_CMD_RESIZE)
        return self._g07_doc_server.post(
            DocCommand(tab_id=tab_id, kind=DOC_CMD_RESIZE,
                       payload={"w": w, "h": h}, seq=seq))

    def _g07_drain_frames(self) -> list:
        """G07 批次 46 — 非阻塞取回文档进程的紧凑帧结果（多核侧经
        pipe 收帧；回退态经内置同步）。供 UI 事件循环调用，经 C01
        门控后由呈现层应用。"""
        return self._g07_doc_server.drain()

    def _r7_a02_doc_mode(self):
        """R7-A02 第 6 点 — 探测**当前真实**文档工作模式。

        按真实接线判定，**不靠名字猜**（审计原话："现有 worker 存在
        且 spawn，但默认导航仍同步，不能据名称宣称已多核渲染"）：

        * ``navigation_is_sync=True`` — 默认导航 :meth:`_commit_navigation`
          仍**同步**调 ``page.load_url()``（事实）。
        * ``worker_does_doc_work`` — 取自真实文档进程池
          :meth:`~DocumentProcessServer.has_done_doc_work`（父进程
          内置同步 worker 是否执行过真实解析 / 样式 / 布局 / 绘制）。
          回退态 / 子进程未 spawn 时，真实文档工作发生在父进程
          worker，可观测；子进程存活（多核态）时父进程无法直接
          观测子进程是否做了真实文档工作，标记保持 False（保守，
          不据"子进程 spawn 了"宣称已多核渲染）。
        * ``worker_spawned`` — 取自真实文档进程池 ``is_spawned()``。

        返回 :class:`DocWorkMode`（document_process_mode 不可用或
        探测异常时返回 None，调用点判空退化）。
        """
        try:
            from toy_browser.app.doc_process_mode import detect_doc_work_mode
        except Exception:
            return None
        spawned = False
        try:
            spawned = bool(self._g07_doc_server.is_spawned())
        except Exception:
            spawned = False
        # R7-A02 第 2 点 — worker 是否做了真实文档工作（解析/样式/
        # 布局/绘制）。读父进程文档进程池的内置同步 worker 标记：
        # 回退态 / 子进程未 spawn 时，真实文档工作发生在父进程
        # worker，可观测；子进程存活（多核态）时父进程无法直接
        # 观测子进程是否做了真实文档工作，标记保持 False（保守，
        # 不据"子进程 spawn 了"宣称已多核渲染）。
        worker_does_doc_work = False
        try:
            if self._g07_doc_server is not None:
                worker_does_doc_work = bool(
                    self._g07_doc_server.has_done_doc_work())
        except Exception:
            worker_does_doc_work = False
        try:
            return detect_doc_work_mode(
                navigation_is_sync=True,        # 默认导航仍同步（事实）
                worker_does_doc_work=worker_does_doc_work,
                worker_spawned=spawned)
        except Exception:
            return None

    def _r7_a02_mode_describe(self) -> str:
        """R7-A02 第 6 点 — 模式诊断串（含"已多核渲染"声明校验结果）。

        关键：在 ``single_debug`` / ``worker_ipc`` 下，
        ``multi-core rendering`` 声明**判为不真实**并给出原因——
        这是"默认模式的性能声明必须真实"的可测闸门。
        """
        mode = self._r7_a02_doc_mode()
        if mode is None:
            return "r7_a02_mode: unavailable"
        try:
            from toy_browser.app.doc_process_mode import claim_multi_core_rendering
            claim = claim_multi_core_rendering(mode)
            claim_s = claim.describe()
        except Exception:
            claim_s = "claim=unavailable"
        dbg = getattr(self, "_r7_a02_debug_mode", None)
        eff = mode
        if dbg is not None:
            eff = dbg.effective_mode(mode)
        return (f"r7_a02_mode: {eff.describe()} | {claim_s} | "
                f"debug={dbg.describe() if dbg is not None else 'none'}")

    def _r7_a02_describe(self) -> str:
        """R7-A02 — 诊断串（UI 心跳 / task trace / 版本门控 / 合并 /
        背压 / 取消确认）。

        回答方案第 1 点"确认真正调用路径"：``trace`` 里的 span
        名与计数是**默认导航入口被真实调用**的证据（不是"模块
        存在"）。任一组件为 None（document_process 不可用）时退化
        为 ``unavailable``，不崩。
        """
        hb = getattr(self, "_r7_a02_heartbeat", None)
        tr = getattr(self, "_r7_a02_trace", None)
        gate = getattr(self, "_r7_a02_nav_gate", None)
        co = getattr(self, "_r7_a02_coalescer", None)
        bp = getattr(self, "_r7_a02_backpressure", None)
        ct = getattr(self, "_r7_a02_cancel", None)
        if hb is None or tr is None:
            return "r7_a02: unavailable"
        parts = [f"heartbeat={hb.beats}",
                 f"trace={tr.count()}",
                 f"nav_path={tr.names()[-4:]}"]
        if gate is not None:
            cur = gate.current(getattr(self, "current", 0))
            parts.append(f"nav_gate={cur}")
        if co is not None:
            parts.append(f"coalesce={co.describe()}")
        if bp is not None:
            parts.append(f"backpressure={bp.describe()}")
        if ct is not None:
            parts.append(f"cancel={ct.describe()}")
        return "r7_a02: " + " ".join(parts)

    def _g07_doc_server_shutdown(self) -> None:
        """G07 批次 46 — UI 销毁时终止文档进程池的子进程（防泄漏）。

        回退态（未真实 spawn）时 no-op；真实 spawn 态经
        :meth:`DocProcessServer.shutdown` 置 stop_evt + join +
        terminate（必要时）。"""
        try:
            self._g07_doc_server.shutdown()
        except Exception:
            pass

    def _on_window_close(self, _event=None) -> None:
        """R7-U01 — WM_DELETE_WINDOW 统一销毁入口。

        GUI 审计 §5 静态缺陷：原内联回调只设 ``_g07_ui_closing=True`` +
        关文档 server，**缺少** ``root.destroy()`` / ``root.quit()``；
        且重复关闭（``_g07_ui_closing`` 已 True）时直接 return，窗口可能
        残留。

        修复契约：
        1. 幂等：``_g07_ui_closing`` 已 True 时仍确保 root 被销毁（防泄漏）；
        2. 顺序：先关文档 server → 清理 PhotoImage → ``root.destroy()``；
        3. 异常安全：``root.destroy()`` 放 ``finally``，即使 server
           shutdown / 清理异常也不留窗口；
        4. ``root.quit()`` 在 destroy 后调用，让 ``mainloop`` 退出（
           destroy 已断开 mainloop 的事件源，quit 是双保险）。
        """
        if not self._g07_ui_closing:
            self._g07_ui_closing = True
            try:
                self._g07_doc_server_shutdown()
            except Exception:
                pass
            # 清理命名 PhotoImage（丢 Python 引用 + GC，让 Tcl 注销同名
            # image，避免后续重建时的 TclError）。
            try:
                self._destroy_named_images()
            except Exception:
                pass
        # 无论首次还是重复关闭，都要销毁 root（幂等：Tk 的 destroy 可
        # 在已销毁对象上调用而不崩——tkinter 内部会静默处理）。
        try:
            self.root.destroy()
        except Exception:
            pass
        # destroy 后 mainloop 事件源已断；quit 双保险让 mainloop 返回。
        try:
            self.root.quit()
        except Exception:
            pass

    def _g07_describe(self) -> str:
        """G07 批次 46 — 文档进程池诊断串（含 spawned 字段，区分
        真实多核 vs 回退态）。"""
        return self._g07_doc_server.describe()

    def _destroy_named_images(self) -> None:
        """P37/G09 — 幂等清理 canvas_var 中持有的命名 Tk PhotoImage。

        ``_bitmap_to_photopix`` / ``_c02_refresh_canvas_cpu`` 用显式名
        （``toytab_{idx}`` / ``toytab_c02_{idx}``）创建 image；这些名字在
        **全局 Tcl 注册表**里唯一。Tk PhotoImage 无 ``destroy()``（只有
        widget 才有），清理方式是丢弃 Python 强引用 + 强制 GC，让 Tcl
        侧同名 image 随 C 层对象释放而注销；否则关标签 →
        ``_rebuild_tabs`` 时同名重建（或后续 ``label.config(image=photo)``
        命中已失效引用）会触发
        ``_tkinter.TclError: image "toytab_N" doesn't exist``。
        """
        import gc
        # 丢弃 Python 引用（局部别名 + dict），再 GC 释放 C 层 image 对象，
        # 让全局 Tcl 注册表里的同名 image 注销，避免同名冲突。
        self.canvas_var.clear()
        gc.collect()

    def _safe_set_image(self, label: tk.Label, photo: tk.PhotoImage,
                         idx: int) -> None:
        """P37/G09 — 幂等地把 PhotoImage 设到 label。

        image 在 Tcl 注册表里已失效（``image "toytab_N" doesn't exist``）
        时不再 ``config``，避免反复触发 ``_on_tab_changed →
        _refresh_canvas`` 回调的 ``_tkinter.TclError`` stderr 噪声；同时
        把 canvas_var 里该 idx 的失效引用移除，避免后续再次命中。
        """
        try:
            label.config(image=photo)
        except tk.TclError:
            # 失效 image：回退到"无 image"，保证 UI 不崩，并清除脏引用。
            try:
                label.config(image="")
            except tk.TclError:
                pass
            self.canvas_var.pop(idx, None)

    def _refresh_canvas(self, idx: int) -> None:
        # 批次 44 — C02 活显示默认呈现路径：优先走 CPUImage 连续
        # buffer（消除 scratch Bitmap 合并层 + per-frame int 列表还原）。
        # 当 ``page._last_dl`` 可用（restate / initial load 时设置）时
        # 经 :meth:`_c02_refresh_canvas_cpu` 呈现；``_last_dl`` 不可用
        # （未加载页面 / 仅有 bitmap 的 about:blank 等）时回退到既有
        # Bitmap 路径（保持 ``page.bitmap`` 契约）。
        page = self.tabs[idx]
        if getattr(page, "_last_dl", None) is not None:
            self._c02_refresh_canvas_cpu(idx)
            # CPUImage 路径不产 Bitmap；滚动条同步仍走 Bitmap 路径的
            # page.scroll_max / content_height（与 CPUImage 呈现兼容）。
            self._sync_scrollbar(idx)
            return
        if page.bitmap is None:
            return
        photo = self._bitmap_to_photopix(page.bitmap, idx)
        # keep reference so GC doesn't collect it
        self.canvas_var[idx] = photo
        frame = self.page_frames.get(idx)
        if frame is None:
            return
        label: tk.Label = frame._label
        self._safe_set_image(label, photo, idx)
        self._sync_scrollbar(idx)

    def _sync_scrollbar(self, idx: int) -> None:
        """P33.R2 — 同步垂直滚动条 range/value 与页面滚动状态
        （与 CPUImage / Bitmap 两条呈现路径共用，消除重复逻辑）。"""
        page = self.tabs[idx]
        frame = self.page_frames.get(idx)
        if frame is None:
            return
        sb = getattr(frame, "_scroll", None)
        if sb is None:
            return
        try:
            m = page.scroll_max()
            if m > 0:
                ch = page.content_height
                first = min(1.0, max(0.0, page.yoffset / ch)) if ch > 0 else 0.0
                last = min(1.0, max(0.0,
                                    (page.yoffset + page.height) / ch)) if ch > 0 else 1.0
                sb.set(first, last)
            else:
                sb.set(0.0, 1.0)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # tab management
    def _add_tab(self, page: PageInstance) -> int:
        idx = len(self.tabs)
        self.tabs.append(page)
        # G09 — 批次 37：为新标签分配稳定 tab_id（与 idx 对应）。
        self._tab_ids.append(self._g09_session.open_tab())
        # R7-S03 第 5 点 — 让页面知道自己的 tab_id（sessionStorage
        # 按 tab+origin 分区）。
        try:
            page.tab_id = self._tab_ids[idx]
        except Exception:
            pass
        self._ensure_page_frame(idx)
        return idx

    def _remove_tab(self, idx: int) -> None:
        if len(self.tabs) <= 1:
            # R7-U01 — 最后一个标签关闭 = 关闭窗口，经统一关闭入口
            # （server shutdown + PhotoImage 清理 + destroy + quit），
            # 不再裸调 quit 残留窗口。
            self._on_window_close()
            return
        # G09 — 批次 37：关标签经会话协议层（取消在途任务 + 释放资源
        # + 重建 idx 映射 + 前台让给剩余标签 + 关闭审计）。
        # R7-S03 第 6 点 — 关标签即清理该标签的 sessionStorage 分区
        # （localStorage 不动）。
        try:
            _tid = getattr(self.tabs[idx], "tab_id", None)
            if _tid is not None:
                from toy_browser.scripting import storage_service as _ss
                _ss.default_storage_service().clear_tab(str(_tid))
        except Exception:
            pass
        self._g09_close_tab(idx)
        # Drop the removed tab and rebuild the notebook consistently.
        del self.tabs[idx]
        self._rebuild_tabs()
        self._show_tab(min(idx, len(self.tabs) - 1))

    def _rebuild_tabs(self) -> None:
        """Destroy and recreate all notebook tab widgets (after removal)."""
        for w in self.notebook.winfo_children():
            w.destroy()
        self.page_frames.clear()
        self._destroy_named_images()
        for i, page in enumerate(self.tabs):
            self._ensure_page_frame(i)
        self.current = 0

    # ------------------------------------------------------------------
    # G09 — 批次 37：统一标签会话协议层接线
    # ------------------------------------------------------------------
    def _g09_close_tab(self, idx: int) -> dict:
        """G09 批次 37 — 关标签经 :class:`TabSession`（取消在途任务 +
        释放资源 + 重建 idx 映射 + 关闭审计），并同步 :attr:`_tab_ids`。

        返回 :meth:`TabSession.close_tab` 的结果（``cancelled`` /
        ``compact_mapping`` / ``new_active``）。``compact_mapping`` 供
        旧闭包重绑：持旧 idx 的回调经 :meth:`_g09_resolve` 解析为
        新 tab_id（失效则 no-op，不串页）。
        """
        tab_id = self._tab_ids[idx]
        res = self._g09_session.close_tab(tab_id)
        # G07 批次 46 — 关标签同步丢弃文档进程池在途身份（子进程侧
        # 经 close_q drop_tab，G09 step 4 不泄漏已关闭标签任务）。
        try:
            self._g07_doc_server.close_tab(tab_id)
        except Exception:
            pass
        # 同步 _tab_ids（删掉被关槽位；compact 后槽位前移）
        del self._tab_ids[idx]
        return res

    def _g09_resolve(self, old_idx: int):
        """G09 批次 37 — 旧回调（持旧 idx）的统一拦截入口。

        关闭中间标签后旧 idx 对应槽位已前移 / 失效；直接用旧 idx 会
        **操作到别的标签**（G09 验收"不串页"）。本方法把 *old_idx*
        解析为当前稳定 tab_id：

        - 仍映射到存活 tab_id → 返回该 tab_id（调用方以 tab_id 为
          key 继续，安全）；
        - 已失效（被关闭 / 前移错位）→ 返回 None（调用方 no-op，
          不操作别的标签），并计入会话的 ``stale_callbacks``。
        """
        return self._g09_session.resolve_callback(old_idx)

    def _g09_tab_id(self, idx: int):
        """G09 批次 37 — idx → 稳定 tab_id（越界返回 None）。"""
        if 0 <= idx < len(self._tab_ids):
            return self._tab_ids[idx]
        return None

    def _g09_idx_of_tab(self, tab_id: int):
        """G09 批次 37 — 稳定 tab_id → 当前 idx（不存在返回 None）。"""
        try:
            return self._tab_ids.index(tab_id)
        except ValueError:
            return None

    def _g09_stats(self):
        """G09 批次 37 — 会话审计统计（供诊断区 / 状态栏）。"""
        return self._g09_session.stats()

    def _g09_describe(self) -> str:
        """G09 批次 37 — 会话诊断串（含 stale_callbacks 拦截计数）。"""
        return self._g09_session.describe()

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
        """B 系列批次 42 — 地址栏 Go / Return 经 BGuiWiring 分派。

        走 :meth:`_b_dispatch_address`（B01 三分类 + B08 任务版本门控），
        成功则导航到 ``result.target``（最终 URL），失败则回退到原始
        输入。导航执行仍走既有 :meth:`_commit_navigation`（不改呈现
        路径）。
        """
        url = self.addr_var.get().strip()
        res = self._b_dispatch_address(url)
        target = res.target if res.ok and res.target else url
        self._commit_navigation(target)

    def _b_dispatch_address(self, text: str):
        """B 系列批次 42 — 地址栏输入经 BGuiWiring 分派（活显示入口）。

        分派前把 BGuiWiring 内部 NavigationController 的 current_url
        同步到当前 page.url，保证 B01 三分类（URL / 相对 URL / 普通
        文本）基于真实文档 URL 而非 about:blank。
        """
        self._b_wiring.nav.current_url = (
            self.tabs[self.current].url or "about:blank")
        return self._b_wiring.dispatch_address(text)

    def _b_dispatch_link(self, href: str, target_attr: str = ""):
        """B 系列批次 42 — 链接点击经 BGuiWiring 分派（活显示入口）。"""
        return self._b_wiring.dispatch_link(href, target_attr)

    def _b_dispatch_form(self, fields, form_id: str = "",
                         action: str = "", method: str = "GET",
                         trigger: str = "", validate: bool = True):
        """B 系列批次 42 — 表单提交经 BGuiWiring 分派（活显示入口）。"""
        return self._b_wiring.dispatch_form(
            fields, form_id, action, method, trigger, validate)

    def _b_describe(self) -> str:
        """B 系列批次 42 — 诊断串（资源三态 + 任务版本）。"""
        return self._b_wiring.describe()

    def can_back(self) -> bool:
        """R7-A01 历史栈收敛 — 游标可后退（工具栏 / 键盘启停依据）。"""
        page = self.tabs[self.current] if self.tabs else None
        return page.can_back() if page is not None else False

    def can_forward(self) -> bool:
        """R7-A01 历史栈收敛 — 游标可前进。"""
        page = self.tabs[self.current] if self.tabs else None
        return page.can_forward() if page is not None else False

    def _commit_navigation(self, url: str) -> None:
        """G04 — the single navigation entry point shared by the Go
        button, the address-bar Return (routed by :meth:`_return_routed`),
        and bookmarks."""
        if not url:
            return
        page = self.tabs[self.current]
        # R7-A02 第 1 点 — UI 心跳：默认导航入口被真实调用一次 =
        # 一次心跳（慢页期间 UI 仍被推进的证据，可测）。
        hb = getattr(self, "_r7_a02_heartbeat", None)
        if hb is not None:
            hb.beat()
        # R7-A02 第 3 点 — navigation_id：本标签每次导航 +1（与
        # worker 侧 DocCommand.navigation_id 同源，供"旧任务不可
        # 覆盖新页"门控）。
        nav_map = getattr(self, "_r7_a02_nav_id", None)
        if nav_map is None:
            nav_map = {}
            self._r7_a02_nav_id = nav_map
        nav_id = nav_map.get(self.current, 0) + 1
        nav_map[self.current] = nav_id
        # R7-A02 第 1 点 — task trace：记录本次导航阶段（调用路径证据）。
        tr = getattr(self, "_r7_a02_trace", None)
        span = tr.start("navigate", tab_id=self.current,
                        navigation_id=nav_id) if tr is not None else None
        # R7-A01 历史栈收敛 — 历史提交经本标签游标（push 当前页 +
        # 清前进栈的语义由 HistoryCommitter.commit 承担：截断游标后的
        # 前进项、追加新项、推进游标）；双栈字段自动投影同步。
        if page.url and page.url != url:
            page._commit_history(url, source="address")
        page.load_url(url, 800, 600)
        if span is not None and tr is not None:
            tr.end(span, ok=True, note=url)
        # R7-A02 第 3 点 — 版本门控：本次导航的帧以 (nav_id, 1) 登记
        # 为"已应用"，此前（旧 navigation）的迟到帧一律被丢弃。
        gate = getattr(self, "_r7_a02_nav_gate", None)
        if gate is not None:
            gate.admit(self.current, nav_id, 1)
        self.notebook.tab(self.current, text=page.title)
        self._refresh_canvas(self.current)
        self._update_status()
        self.status_var.set(f"Loaded: {page.title}")

    def _open_url(self, url: str) -> None:
        """P32.2 — navigate the current tab to *url* (bookmark menu item)."""
        self.addr_var.set(url)
        self._commit_navigation(url)

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
            page._commit_history(path, source="file")
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
    def _widget_owner(self, widget) -> str | None:
        """G04 — classify a Tk widget into a focus owner.

        Returns ``"chrome"`` for the address entry, ``"document"`` for
        widgets inside a tab's page frame, ``"dialog"`` for modal
        dialogs (not yet present in the Tk transition — future hook),
        and ``None`` otherwise (FocusManager treats as document).
        """
        # The address entry and toolbar widgets are chrome.
        if widget is getattr(self, "addr_entry", None):
            return "chrome"
        # Walk up the widget hierarchy: if the widget is inside a page
        # frame, it's the document owner.
        if widget is getattr(self, "status_var", None):
            return "chrome"
        for idx, frame in getattr(self, "page_frames", {}).items():
            if widget is frame or widget in getattr(frame, "winfo_children", lambda: [])():
                return "document"
        return None

    def _current_focus_owner(self) -> str:
        """G04 — resolve the focus owner of the widget that currently
        has keyboard focus (``root.focus_get()``)."""
        try:
            w = self.root.focus_get()
        except tk.TclError:
            return "document"
        if w is None:
            return "document"
        return self._focus_manager.owner(w)

    def _tab_focus_routed(self, forward: bool) -> None:
        """G04 — Tab / Shift+Tab through the FocusManager: the
        event only reaches the page's focus chain when the *document*
        owns keyboard focus (address bar in focus → break)."""
        owner = self._current_focus_owner()
        if owner == "document":
            self._tab_focus(forward)
        # chrome / editor / dialog → break (Tk's native order).

    def _return_routed(self) -> None:
        """G04 — Return / Enter through the FocusManager: in the
        address bar, navigate the entry's text; in the page, activate
        the focused element.  The legacy code sent every Return to the
        page's focus chain — a Return while typing in the address bar
        accidentally activated a page button."""
        owner = self._current_focus_owner()
        if owner == "chrome":
            self._commit_navigation(self.addr_var.get().strip())
        elif owner == "document":
            self._activate_focused()
        # editor / dialog → break.

    # ------------------------------------------------------------------
    # G11 — Tk 侧真实键盘绑定桥（批次 35 桥 + 批次 36 接线）
    # ------------------------------------------------------------------
    def _g11_get_router(self):
        """G11 — 每次按键构造当前焦点状态的 :class:`KeyboardRouter`。

        从 :meth:`_current_focus_owner` 取 chrome / document / editor /
        dialog 域。**G03 批次 39**：GUI 过渡版已有原生编辑覆盖层
        （:meth:`_g03_ensure_overlay` 附着），故 ``focus_in_edit`` /
        ``has_active_field`` 反映当前覆盖层活动状态——有活动编辑
        覆盖层时编辑层按键（可打印 / caret / Escape）路由到覆盖层
        而非 document 焦点链。
        """
        from toy_browser.app.keyboard_router import KeyboardRouter
        owner = self._current_focus_owner()
        ov = getattr(self, "_g03_overlay", None)
        in_edit = bool(ov and ov.active)
        has_field = bool(ov and ov.state is not None and ov.state.value is not None)
        return KeyboardRouter(focus_owner=owner, focus_in_edit=in_edit,
                              has_active_field=has_field)

    def _g11_dispatch(self, decision, event) -> None:
        """G11 — 把批次 21 :class:`KeyDecision` 分派到既有 G04 动作。

        与既有 G04 专门绑定（``<Tab>`` / ``<Return>`` / Control 组合）
        **不重复触发**：那些键已有专门 ``lambda`` 绑定处理，本分派层
        只对 G04 未覆盖的 G11 完整按键集做**增量补全**——
        方向键 / Home / End / PageUp / PageDown（页面焦点链移动或
        caret-move）、Escape（chrome 清地址栏 / document 取消激活）。
        每次分派记录到 :attr:`_g11_dispatch_log`（纯逻辑可查，供
        回归核对接线正确性）。
        """
        action = decision.action
        owner = decision.target
        # 记录（供回归 / 诊断）
        self._g11_dispatch_log.append(
            (action, owner, bool(decision.consumed)))
        # 仅处理 G04 未覆盖的增量键；Tab / Return / Control 组合已被
        # 既有专门绑定处理，此处不重复执行（避免双重触发）。
        if action in ("tab", "shift-tab", "navigate"):
            return
        if action == "activate" and owner == "document":
            self._activate_focused()
        elif action == "escape":
            if owner == "address" or owner == "chrome":
                self.addr_var.set("")
                self._focus_address()
            elif owner == "document":
                # 取消当前焦点激活（blur）
                page = self.tabs[self.current]
                if page.focus_id is not None:
                    page.set_focus(None)
                    self._refresh_canvas(self.current)
        elif action in ("caret-move",) and owner == "document":
            # 编辑层（G03 批次 39）：有活动编辑覆盖层时，caret-move
            # 路由到覆盖层（Left/Right/Home/End 移动 caret）而非
            # 页面焦点链。
            ov = getattr(self, "_g03_overlay", None)
            if ov is not None and ov.active:
                key = getattr(event, "keysym", "")
                if key == "Left":
                    self._g03_dispatch("move-left")
                elif key == "Right":
                    self._g03_dispatch("move-right")
                elif key == "Home":
                    self._g03_dispatch("move-home")
                elif key == "End":
                    self._g03_dispatch("move-end")
                return
            # 页面焦点链上的 caret / 焦点移动（方向键 / Home / End /
            # PageUp / PageDown）：映射为焦点前进 / 后退的近似
            # （PageDown/Down 前进、PageUp/Up/Home 后退；Left/Right 依
            # 顺序）。
            key = getattr(event, "keysym", "")
            if key in ("Down", "Right", "PageDown", "End"):
                self._tab_focus(True)
            elif key in ("Up", "Left", "PageUp", "Home"):
                self._tab_focus(False)
        elif action in ("edit",):
            # 编辑层（G03 批次 39）：有活动编辑覆盖层时，可打印字符
            # / Backspace / Delete / Enter 路由到覆盖层编辑。
            # R7-U02 — IME 组合路由：中文输入法在 Tk 下发可打印字符
            # 时若组合进行中（state.is_composing），走 ime-update
            # （组合区内替换拼音，不派发 input）而非 insert-text；
            # 组合外的可打印字符仍走 insert-text（向后兼容）。
            ov = getattr(self, "_g03_overlay", None)
            if ov is not None and ov.active:
                key = getattr(event, "keysym", "")
                ch = getattr(event, "char", "")
                st = ov.state
                composing = bool(st and st.is_composing)
                if ch and key not in ("BackSpace", "Delete", "Return"):
                    if composing:
                        # 组合进行中：可打印字符是拼音/候选中间态，
                        # 走 ime-update（mark 用当前 caret）。
                        self._g03_dispatch("ime-update", ch,
                                           st.caret if st else 0)
                    else:
                        self._g03_dispatch("insert-text", ch)
                elif key == "BackSpace":
                    self._g03_dispatch("delete-backward")
                elif key == "Delete":
                    self._g03_dispatch("delete-forward")
                elif key == "Return":
                    self._g03_on_blur()  # Enter 提交 + 失焦撤销覆盖层
                return
            if owner == "address":
                # 地址栏可打印字符输入（chrome 焦点）：交由 Tk 原生
                # Entry 处理（fall-through），此处仅记录。
                return
        # 其余（drop / edit 无编辑层等）→ no-op。

    def _install_g11_bindings(self) -> None:
        """G11 — 把批次 35 :func:`install` 桥接进本 shell：在 root 上
        装 ``<Key>`` 绑定，``get_router`` / ``dispatch`` 由本类提供。

        与既有 G04 专门绑定共存：G11 handler 对已被专门绑定的键
        （Tab / Return / Control 组合）返回 ``None``（fall-through，让
        专门绑定接管），只对增量键返回 ``"break"``。``install`` 内部
        的返回值契约（批次 35 :func:`binding_result`）已保证：
        ``consumed=True`` → ``"break"``、``consumed=False`` → ``None``。
        而 :meth:`_g11_dispatch` 对 Tab/Return/Control 这些 G04 已处理
        的 action 不做动作（见上），双重触发的**执行**被消除——Tk 的
        ``<Key>`` 与 ``<Tab>`` 绑定同一事件都会跑，但只有 G04 的
        专门绑定真正移动焦点 / 导航。
        """
        from toy_browser.app import tk_keyboard_binding
        # 分派日志（纯逻辑可查）
        if not hasattr(self, "_g11_dispatch_log"):
            self._g11_dispatch_log: list = []
        tk_keyboard_binding.install(
            self.root, self._g11_get_router, self._g11_dispatch)

    def _tab_focus(self, forward: bool = True) -> None:
        """P33.P1 — Tab / Shift+Tab: move the focus to the next/previous
        element in the current page's :attr:`focus_chain`."""
        page = self.tabs[self.current]
        page.focus_next(forward=forward)
        self._refresh_canvas(self.current)

    def _activate_focused(self) -> None:
        """G02 — Return / Enter: activate the currently focused element.

        Delegates to :meth:`activate_element` with ``source="keyboard"``
        so the default-action table is shared with the mouse path.
        """
        page = self.tabs[self.current]
        idx = page.focus_id
        if idx is None:
            return
        node = page._node_index_to_dom(
            getattr(page, "_dom_tree", None), idx)
        if node is None or not node.is_element():
            return
        self.activate_element(page, node, idx, source="keyboard")

    def activate_element(self, page, node, idx: int,
                         source: str = "mouse") -> None:
        """G02 — unified mouse / keyboard activation.

        The legacy code had two divergent paths: ``_click_dispatch``
        (mouse) executed the element's ``onclick`` handler but only for
        input/button, while ``_activate_focused`` (Enter) *never*
        executed onclick — the assessment's button test showed the DOM
        still read SAFE after a keyboard Enter.  This method is the
        single activation entry point:

        1. Dispatch the element's click handler (``onclick`` attribute
           or JS-level property) on the shared Host so DOM mutations
           land in the live tree.
        2. Run the control's default action (navigate for ``<a>``,
           toggle for checkbox/radio, cycle for ``<select>``).
        3. Re-apply the interaction state and refresh the canvas.
        """
        host = getattr(page, "_script_host", None)
        tag = (node.tag or "").lower()
        inp_type = (node.attr("type") or "").lower()
        # 1 — fire the click handler before the default action so a
        # handler can inspect (or prevent) the default.
        if host is not None:
            from toy_browser.scripting import js as _js_mod
            _el = _js_mod.DomElement(node, host)
            host.execute_event_bubbled(_el, "click")
        # 2 — default action by control type.
        if tag == "a" and node.attr("href"):
            # B 系列批次 42 — 传递 target_attr（_blank/_self 等）经
            # BGuiWiring parse_link_target 分类；活显示走既有
            # _navigate_to → BGuiWiring dispatch_link 路径。
            self._navigate_to(node.attr("href"),
                              target_attr=node.attr("target") or "")
        elif tag in ("input", "button"):
            if inp_type in ("checkbox", "radio"):
                page._toggle_checkbox(idx)
                page._save_form_state_to_dom()
            else:
                # text / number / submit / button — focus for
                # keyboard entry.
                page.active_form_field = idx
                # G03 — 批次 39：文本类 input 附着原生编辑覆盖层
                # （positioned / editable；checkbox/radio 无文本编辑）。
                if inp_type not in ("checkbox", "radio"):
                    self._g03_ensure_overlay(page, node, idx)
        elif tag == "textarea":
            page.active_form_field = idx
            # G03 — 批次 39：textarea 附着原生编辑覆盖层。
            self._g03_ensure_overlay(page, node, idx)
        elif tag == "select":
            # Toy: cycle to the next <option>.
            page._save_form_state_to_dom()
        # 3 — re-apply interaction state + refresh.
        page._reapply_interaction()
        self._refresh_canvas(self.current)
        self._update_status()

    # ------------------------------------------------------------------
    # G03 — 批次 39：原生覆盖层接线（EditorOverlayController + 批次 34 协议）
    # ------------------------------------------------------------------
    def _g03_control_info(self, page, node, idx: int):
        """G03 批次 39 — 从 DOM 节点构造 :class:`OverlayControlInfo`。

        取 type / value / readonly / 几何（布局盒 rect → OverlayRect），
        供覆盖层附着。无布局 / 非 input / textarea 返回 None。
        """
        from toy_browser.app.editor_overlay import OverlayControlInfo
        from toy_browser.app.overlay_protocol import OverlayRect
        tag = (node.tag or "").lower()
        if tag not in ("input", "textarea"):
            return None
        inp_type = (node.attr("type") or "").lower()
        # checkbox / radio / button / submit 无文本编辑（不建覆盖层）
        if tag == "input" and inp_type in ("checkbox", "radio",
                                           "button", "submit"):
            return None
        value = node.attr("value") or ""
        readonly = node.attr("readonly") is not None
        is_password = (tag == "input" and inp_type == "password")
        # 几何：布局盒 rect（CSS px），找不到则退化为小矩形（呈现层
        # 活显示批次按真实布局 + 视口转换精化）
        rect = OverlayRect(x=10.0, y=10.0, w=160.0, h=24.0)
        try:
            box = self._g03_layout_box_for(page, idx)
            if box is not None and getattr(box, "rect", None) is not None:
                r = box.rect
                rect = OverlayRect(x=float(r.x), y=float(r.y),
                                   w=max(1.0, float(r.w)),
                                   h=max(1.0, float(r.h)))
        except Exception:
            pass
        return OverlayControlInfo(node_ref=idx, tag=tag, value=value,
                                  is_password=is_password,
                                  is_read_only=readonly, rect=rect)

    def _g03_layout_box_for(self, page, idx: int):
        """G03 批次 39 — DOM 节点 idx → 布局盒（活显示批次按真实
        布局 + 视口转换精化；本核心用退化矩形即可回归）。"""
        root = getattr(page, "_layout_root", None)
        if root is None:
            return None
        node = page._node_index_to_dom(getattr(page, "_dom_tree", None), idx)
        if node is None:
            return None
        found = []

        def _walk(bx):
            try:
                if getattr(bx, "dom", None) is node:
                    found.append(bx)
                    return
            except Exception:
                pass
            for c in getattr(bx, "children", []) or []:
                _walk(c)
        _walk(root)
        return found[0] if found else None

    def _g03_ensure_overlay(self, page, node, idx: int) -> bool:
        """G03 批次 39 — 激活 input/textarea 时附着原生编辑覆盖层。

        经 :meth:`_g03_control_info` 取控件信息，:meth:`attach` 建
        覆盖层（可编辑才显示）。返回是否显示。
        """
        info = self._g03_control_info(page, node, idx)
        if info is None:
            return False
        from toy_browser.app.overlay_protocol import Viewport
        vp = Viewport(scroll_x=0, scroll_y=page.yoffset,
                      width=page.width, height=page.height,
                      page_zoom=1.0)
        return self._g03_overlay.attach(info, vp, has_focus=True)

    def _g03_on_scroll(self, page) -> None:
        """G03 批次 39 — 页面滚动：同步覆盖层位置（滚出视口自动隐藏）。"""
        from toy_browser.app.overlay_protocol import Viewport
        if not self._g03_overlay.attached:
            return
        vp = Viewport(scroll_x=0, scroll_y=page.yoffset,
                      width=page.width, height=page.height,
                      page_zoom=1.0)
        self._g03_overlay.on_scroll(vp)

    def _g03_on_blur(self) -> None:
        """G03 批次 39 — 失焦：撤销覆盖层 + 提交 change + 写回 DOM。"""
        ov = self._g03_overlay
        if not ov.attached:
            return
        page = self.tabs[self.current]
        # 提交 change（value 偏离基线时触发）+ 写回 DOM（真实 value）
        node = self._g03_active_node(page)
        if node is not None:
            dom_attrs = getattr(node, "attrs", None)
            if isinstance(dom_attrs, dict):
                val = ov.write_back(dom_attrs)
                page._save_form_state_to_dom()
        ov.on_blur()

    def _g03_on_destroy(self) -> None:
        """G03 批次 39 — 节点销毁：覆盖层终态（协议 destroyed）。"""
        self._g03_overlay.on_node_destroy()

    def _g03_active_node(self, page):
        """G03 批次 39 — 当前覆盖层对应的 DOM 节点（node_ref=idx）。"""
        st = self._g03_overlay.state
        if st is None:
            return None
        try:
            idx = int(st.node_ref)
        except (TypeError, ValueError):
            return None
        return page._node_index_to_dom(getattr(page, "_dom_tree", None), idx)

    def _g03_dispatch(self, command: str, data: str = "",
                      ime_mark: int = 0) -> bool:
        """G03 批次 39 — 编辑命令 → 覆盖层（键盘 / G11 编辑层按键）。"""
        return self._g03_overlay.dispatch(command, data, ime_mark)

    def _g03_stats(self) -> dict:
        """G03 批次 39 — 覆盖层诊断（附着 / 活动 / 协议审计）。"""
        return self._g03_overlay.stats()

    def _click_dispatch(self, event) -> None:
        """P33.P1 — mouse click on the viewport: hit-test, set hover +
        focus, then activate the element via :meth:`activate_element`
        (G02 — the shared mouse / keyboard activation entry point).
        """
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
                # G02 — links navigate immediately; all other
                # interactive controls go through the shared
                # activation entry (click handler + default action).
                if tag == "a" and node.attr("href"):
                    self.activate_element(page, node, hit_idx,
                                          source="mouse")
                    return
                elif tag in ("input", "button", "textarea", "select"):
                    self.activate_element(page, node, hit_idx,
                                          source="mouse")
                    return
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

    def _navigate_to(self, href: str, target_attr: str = "") -> None:
        """P33.P1 / B 系列批次 42 — navigate to *href*（活显示经
        BGuiWiring 分派：B07 parse_link_target 相对解析 + B01 导航
        决策 + B08 任务版本门控）。

        * ``action == "navigate"`` — 导航到 ``result.target``（最终 URL）。
        * ``action == "none"`` + ``error == "scheme-not-supported"`` —
          不支持的 scheme（javascript: / mailto: 等），no-op。
        * ``ok == False`` — 分派失败，回退到原始 href（既有行为）。
        """
        # BGuiWiring 内部 NavigationController 的 current_url 需与当前
        # page 同步，否则相对链接解析基于 about:blank 而非真实 page.url。
        page = self.tabs[self.current]
        self._b_wiring.nav.current_url = page.url or "about:blank"
        res = self._b_dispatch_link(href, target_attr)
        if res.ok and res.action == "navigate" and res.target:
            self._commit_navigation(res.target)
            return
        # 回退到既有路径（相对链接 / 不支持 scheme / 分派失败）
        page = self.tabs[self.current]
        if href.startswith("http://") or href.startswith("https://"):
            page.load_url(href, page.width, page.height)
        elif not href.startswith("#") and href.strip():
            # 相对链接 — toy: just log it（既有行为不变）
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

"""Public API: render_document() — HTML + CSS -> PPM pixels.

Mirrors the Rust toy-browser's lib.rs. Runs the full pipeline:
parse -> style -> layout -> paint -> rasterize, and (optionally) executes
inline <script> blocks via the toy JS engine before rendering.
"""
from __future__ import annotations

import json
import struct
import zlib
from typing import Optional

import dom as dom_mod
import css as css_mod
import layout as layout_mod
import paint_engine
from paint import Bitmap, Color


def render_document(
    html: str,
    css_text: str = "",
    width: int = 800,
    height: int = 600,
) -> Bitmap:
    """Render an HTML string to a bitmap (runs inline scripts first)."""
    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    host = _run_scripts(tree)
    # P9.1 — extract @media rules and evaluate against the viewport.
    media_rules = css_mod.parse_media_queries(full_css)
    # P11.3 — extract @supports blocks (toy: always supported).
    supports_rules = css_mod.parse_supports_blocks(full_css)
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    dl = paint_engine.emit_commands(root)
    return paint_engine.rasterize(dl, width, height)


def render_document_from_url(
    url: str,
    css_text: str = "",
    width: int = 800,
    height: int = 600,
    load_subresources: bool = True,
    client=None,
    timeout: float | None = None,
) -> Bitmap:
    """P22 — load an http(s) *url* and render it, plus fetch its
    subresources (``<link rel=stylesheet>`` / ``<img src>``) against
    the document base.

    * P22.1 — the document fetch follows a 3xx redirect chain (cap 5,
      loop detection).
    * P22.2 — a ``Content-Encoding: gzip`` body is decoded transparently.
    * P22.4 — subresources are resolved relative to the document URL
      (or ``<base href>`` when present, P22.5) and fetched; a failed
      subresource (404 / timeout / non-HTML) is skipped silently and
      never blocks page rendering.  A successfully fetched stylesheet
      is appended to *css_text* before the pipeline runs.
    * P22.3 — chunked transfer-encoding is handled by ``http.client``
      transparently (``net.decode_chunked`` is the unit-testable hook).

    A top-level network failure raises :class:`net.FetchError`
    (including :class:`net.RedirectLoop`); the caller renders the
    unified error page.
    """
    import net
    html, resp = net.fetch_html(
        url, client=client, timeout=timeout,
        follow_redirects=True,
    )
    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)

    # P22.4/P22.5 — load subresources against the document base.
    if load_subresources:
        base = net.parse_url(resp.final_url or url)
        try:
            loaded = net.load_subresources(tree, base,
                                            client=client, timeout=timeout)
            # Append any successfully fetched stylesheets.
            for _u, r in (loaded.get("stylesheet") or {}).items():
                if r is not None and r.ok:
                    full_css += "\n" + r.body.decode("utf-8",
                                                     errors="replace")
        except Exception:
            # Subresource loading must never break page rendering (P22.4).
            pass

    host = _run_scripts(tree)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    dl = paint_engine.emit_commands(root)
    return paint_engine.rasterize(dl, width, height)


def _run_scripts(tree):
    """Execute inline <script> blocks in document order via the toy JS engine."""
    import js as js_mod

    host = js_mod.Host(tree, lambda _s: None)
    host.execute_all()
    return host


def _find_element_by_id(root, element_id: str):
    """Walk the DOM tree and return the first element whose ``id`` matches.

    P13.4 — helper for input / focus event simulation.
    """
    if root.is_element() and root.attr("id") == element_id:
        return root
    for child in root.children:
        found = _find_element_by_id(child, element_id)
        if found is not None:
            return found
    return None


def render_document_with_event(
    html: str,
    css_text: str,
    width: int,
    height: int,
    element_id: str,
    log_sink=None,
) -> Bitmap:
    """Render, then simulate a click on the element with the given id.

    Runs the element's onclick handler (via the toy JS engine), mutates the
    DOM/style, and re-renders.
    """
    import js as js_mod

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()
    host.execute_event(element_id)
    # Re-render after the DOM mutation.
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    dl = paint_engine.emit_commands(root)
    return paint_engine.rasterize(dl, width, height)


# --- P12.4 — hover / mousemove 状态机 + 渲染前回调 -------------------------------------


def render_document_with_hover_at(
    html: str,
    css_text: str,
    width: int,
    height: int,
    x: float,
    y: float,
    log_sink=None,
) -> Bitmap:
    """P12.4 — 把命中测试得到的元素设为 :hover 态渲染。

    与 :func:`render_document_with_hover` 不同，悬停目标不是 id，而是
    坐标 (x, y) 命中到的元素 id。命不中时不应用任何 :hover 规则。
    """
    import js as js_mod

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    hit = layout_mod.hit_test(root, x, y)
    hover_id = None
    if hit is not None and hit.dom is not None and hit.dom.is_element():
        hover_id = hit.dom.attr("id")
    styled2 = css_mod.apply(
        tree, full_css,
        interaction_state={"hover": hover_id} if hover_id else None,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root2 = layout_mod.layout(styled2, float(width), float(height))
    dl = paint_engine.emit_commands(root2)
    return paint_engine.rasterize(dl, width, height)


def render_document_with_mouse(
    html: str,
    css_text: str,
    width: int,
    height: int,
    events: list[dict],
    log_sink=None,
    on_before_render=None,
) -> list[Bitmap]:
    """P12.4 — 事件状态机 + 渲染前回调。

    按顺序处理一组鼠标事件：

    * ``{"type": "mousemove", "x": …, "y": …}`` — 命中 (x, y)，把 :hover 态
      切到该元素，触发 ``onmouseover``（若该元素定义）。
    * ``{"type": "mouseleave", "id": …}`` — 把 :hover 态切出指定元素，触发
      ``onmouseout``（若定义）。
    * ``{"type": "click", "x": …, "y": …}`` — 命中并触发 ``onclick``。

    ``on_before_render`` 是一个可选回调 ``(state_dict, index) -> None``，
    在每次合成渲染前调用；state_dict 含 ``{"hover": id, "mouse": (x, y)}``。
    回调可以修改 DOM（例如 ``document.getElementById(...).style.display``），
    引擎会在回调返回后重新合成。返回每个事件后的渲染位图列表。
    """
    import js as js_mod

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()

    state = {"hover": None, "mouse": (0.0, 0.0)}
    results: list[Bitmap] = []
    for idx, ev in enumerate(events):
        etype = ev.get("type", "")
        if etype == "mousemove":
            x, y = float(ev["x"]), float(ev["y"])
            state["mouse"] = (x, y)
            styled = css_mod.apply(
                tree, full_css,
                media_rules=media_rules,
                supports_rules=supports_rules,
                viewport_w=float(width),
                viewport_h=float(height),
            )
            root = layout_mod.layout(styled, float(width), float(height))
            hit = layout_mod.hit_test(root, x, y)
            if hit is not None and hit.dom is not None and hit.dom.is_element():
                state["hover"] = hit.dom.attr("id")
            else:
                state["hover"] = None
        elif etype == "mouseleave":
            state["hover"] = ev.get("id")
        elif etype == "click":
            x, y = float(ev["x"]), float(ev["y"])
            styled = css_mod.apply(
                tree, full_css,
                media_rules=media_rules,
                supports_rules=supports_rules,
                viewport_w=float(width),
                viewport_h=float(height),
            )
            root = layout_mod.layout(styled, float(width), float(height))
            hit = layout_mod.hit_test(root, x, y)
            if hit is not None and hit.dom is not None and hit.dom.is_element():
                eid = hit.dom.attr("id")
                if eid:
                    host.execute_event(eid)
        elif etype == "input":
            # P13.4 — input event: set the value of an <input> element and
            # trigger its oninput handler.
            target_id = ev.get("id", "")
            value = ev.get("value", "")
            target = _find_element_by_id(tree, target_id)
            if target is not None:
                target.set_attr("value", value)
                host.execute_event(target_id, event_name="oninput")
        elif etype == "focus":
            # P13.4 — focus event: trigger onfocus on the target element.
            target_id = ev.get("id", "")
            if target_id:
                host.execute_event(target_id, event_name="onfocus")
        # P12.4 — 渲染前回调（允许修改 DOM 或查看状态）。
        if on_before_render is not None:
            on_before_render(dict(state), idx)
        styled = css_mod.apply(
            tree, full_css,
            interaction_state={"hover": state["hover"]} if state["hover"] else None,
            media_rules=media_rules,
            supports_rules=supports_rules,
            viewport_w=float(width),
            viewport_h=float(height),
        )
        root = layout_mod.layout(styled, float(width), float(height))
        dl = paint_engine.emit_commands(root)
        results.append(paint_engine.rasterize(dl, width, height))
    return results


def render_document_with_hover(
    html: str,
    css_text: str,
    width: int,
    height: int,
    hover_id: str,
    log_sink=None,
) -> Bitmap:
    """P8.1 — Render with the :hover pseudo-class active on ``hover_id``.

    Passes ``{"hover": hover_id}`` to the CSS cascade so that
    ``#id:hover`` rules are applied.  Useful for testing hover-state
    rendering in headless mode.
    """
    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    host = _run_scripts(tree)
    styled = css_mod.apply(
        tree, full_css,
        interaction_state={"hover": hover_id},
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    dl = paint_engine.emit_commands(root)
    return paint_engine.rasterize(dl, width, height)


def render_document_with_click(
    html: str,
    css_text: str,
    width: int,
    height: int,
    x: float,
    y: float,
    log_sink=None,
) -> Bitmap:
    """Render, hit-test (x,y) against the layout, and run the matched
    element's onclick handler (via the toy JS engine), then re-render.
    """
    import js as js_mod

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()
    hit = layout_mod.hit_test(root, x, y)
    if hit is not None and hit.dom is not None and hit.dom.is_element():
        eid = hit.dom.attr("id")
        if eid:
            host.execute_event(eid)
    styled2 = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root2 = layout_mod.layout(styled2, float(width), float(height))
    dl = paint_engine.emit_commands(root2)
    return paint_engine.rasterize(dl, width, height)


def _full_css(tree, user_css: str) -> str:
    default_css = _default_css()
    style_tags = [n for n in _all_text_nodes(tree) if (n.tag or "").lower() == "style"]
    inline_style = "\n".join(n.text_content() for n in style_tags)
    return "\n".join(p for p in [default_css, inline_style, user_css] if p)


# P11.4 — hit-test + event coordinate APIs -------------------------------------------


def element_at(
    html: str,
    css_text: str,
    width: int,
    height: int,
    x: float,
    y: float,
) -> Optional[dict]:
    """P11.4 — resolve a screen coordinate to the deepest containing element.

    Returns ``{"id": ..., "tag": ..., "rect": (x, y, w, h)}`` for the
    matched DOM element, or ``None`` if no element contains the point.
    """
    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    hit = layout_mod.hit_test(root, x, y)
    if hit is None or hit.dom is None:
        return None
    r = hit.rect
    return {
        "id": hit.dom.attr("id"),
        "tag": hit.dom.tag,
        "rect": (r.x, r.y, r.w, r.h),
    }


def event_target(
    html: str,
    css_text: str,
    width: int,
    height: int,
    x: float,
    y: float,
    log_sink=None,
) -> Optional[str]:
    """P11.4 — run the onclick handler of the element under (x, y).

    Returns the element's id, or ``None`` if no matching element has an
    ``onclick`` handler.  Logs go to ``log_sink``.
    """
    import js as js_mod

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()
    hit = layout_mod.hit_test(root, x, y)
    if hit is None or hit.dom is None or not hit.dom.is_element():
        return None
    eid = hit.dom.attr("id")
    if not eid:
        return None
    handler = hit.dom.attr("onclick")
    if handler:
        host.execute_event(eid)
    return eid


def click_and_render(
    html: str,
    css_text: str,
    width: int,
    height: int,
    x: float,
    y: float,
    log_sink=None,
) -> tuple[Optional[str], Bitmap]:
    """P11.4 — click (x, y), run the handler, re-render; return (id, bitmap).

    The returned id is ``None`` when the hit falls on a non-element or an
    element without an ``id``.
    """
    import js as js_mod

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(full_css)
    supports_rules = css_mod.parse_supports_blocks(full_css)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()
    styled = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    hit = layout_mod.hit_test(root, x, y)
    hit_id: Optional[str] = None
    if hit is not None and hit.dom is not None and hit.dom.is_element():
        eid = hit.dom.attr("id")
        if eid:
            hit_id = eid
            host.execute_event(eid)
    styled2 = css_mod.apply(
        tree, full_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root2 = layout_mod.layout(styled2, float(width), float(height))
    dl = paint_engine.emit_commands(root2)
    return hit_id, paint_engine.rasterize(dl, width, height)


def _all_text_nodes(root) -> list:
    out: list = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.is_element() and (n.tag or "").lower() == "style":
            out.append(n)
        stack.extend(n.children)
    return out


def _default_css() -> str:
    """User-Agent stylesheet: default element styles (P1.3).

    Covers headings h1–h6, lists (ul/ol/li), tables (table/tr/td/th),
    horizontal rules (hr), preformatted blocks (pre), blockquotes,
    form controls (button/input/textarea/select), and emphasis tags.
    """
    return """
    html, body { font-size: 16px; }
    body { margin: 8px; }
    h1 { font-size: 28px; margin: 8px 0; }
    h2 { font-size: 22px; margin: 6px 0; }
    h3 { font-size: 18px; margin: 4px 0; }
    h4 { font-size: 16px; margin: 4px 0; }
    h5 { font-size: 14px; margin: 2px 0; }
    h6 { font-size: 13px; margin: 2px 0; }
    p { margin: 4px 0; }
    ul { margin: 4px 0 4px 20px; }
    ol { margin: 4px 0 4px 20px; }
    li { margin: 2px 0; }
    code { background: #eeeeee; }
    pre { background: #f4f4f4; padding: 8px; margin: 8px 0; }
    blockquote { margin: 8px 16px; }
    hr { margin: 8px 0; }
    iframe { border-width: 1px; border-color: #888888; padding: 2px; margin: 4px 0; }
    button { margin: 4px 0; padding: 4px 8px; border-width: 1px; border-color: #888888; background: #f0f0f0; }
    input { margin: 4px 0; padding: 2px 4px; border-width: 1px; border-color: #888888; }
    textarea { margin: 4px 0; padding: 2px 4px; border-width: 1px; border-color: #888888; }
    select { margin: 4px 0; padding: 2px; border-width: 1px; border-color: #888888; }
    table { border-width: 1px; border-color: #aaaaaa; margin: 4px 0; }
    td { padding: 2px 4px; border-width: 1px; border-color: #cccccc; }
    th { padding: 2px 4px; border-width: 1px; border-color: #cccccc; background: #f0f0f0; }
    a { color: #0000ee; }
    em { font-size: 16px; }
    """


# --- Invalidation helper (P0.5) -----------------------------------------------------


def changed_region(bmp_a: Bitmap, bmp_b: Bitmap) -> Optional[tuple[int, int, int, int]]:
    """Return the bounding box ``(x, y, w, h)`` of pixels that differ between
    two bitmaps, or ``None`` if they are identical.

    This is the minimal invalidation primitive: a future incremental renderer
    can re-rasterize only the changed region instead of the full frame.
    """
    if bmp_a.width != bmp_b.width or bmp_a.height != bmp_b.height:
        raise ValueError("bitmaps must have the same dimensions")
    w, h = bmp_a.width, bmp_a.height
    min_x, min_y = w, h
    max_x, max_y = -1, -1
    for py in range(h):
        row_a = bmp_a.pixels[py * w:(py + 1) * w]
        row_b = bmp_b.pixels[py * w:(py + 1) * w]
        for px in range(w):
            if row_a[px] != row_b[px]:
                if px < min_x:
                    min_x = px
                if px > max_x:
                    max_x = px
                if py < min_y:
                    min_y = py
                if py > max_y:
                    max_y = py
    if max_x < 0:
        return None
    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def write_ppm(bmp: Bitmap, path: str) -> None:
    bmp.save_ppm(path)


# --- P2.1 — Accessibility ----------------------------------------------------

# Tags that are structural / hidden from accessibility (skip in a11y tree).
_SKIP_A11Y = {"script", "style", "link", "meta", "noscript", "template"}


def collect_accessibility_tree(root_dom) -> list[dict]:
    """Build a flat accessibility tree from a DOM root (P2.1).

    Each entry is ``{"role": <str>, "name": <str|None>, "children": [...]}``.
    Structural tags (script/style/link/meta) are skipped.  Elements that are
    not in the ROLES map get their tag name as the role.
    """
    ROLES = {
        "a": "link", "button": "button", "input": "textbox",
        "h1": "heading", "h2": "heading", "h3": "heading",
        "h4": "heading", "h5": "heading", "h6": "heading",
        "li": "listitem", "ul": "list", "ol": "list",
        "table": "table", "img": "image", "iframe": "iframe",
        "textarea": "textbox", "select": "combobox",
        "div": "section", "span": "generic",
        "p": "paragraph", "blockquote": "blockquote", "pre": "code",
        "hr": "separator", "br": "linebreak",
    }

    def walk(n) -> list[dict]:
        out: list[dict] = []
        for c in n.children:
            if not c.is_element():
                continue
            tag = (c.tag or "").lower()
            if tag in _SKIP_A11Y:
                continue
            node: dict = {"role": ROLES.get(tag, tag)}
            # Accessible name: aria-label > text content > alt (for img).
            aria = c.attr("aria-label")
            if aria:
                node["name"] = aria
            else:
                text = c.text_content().strip()
                node["name"] = text or None
            # img alt fallback (P2.1).
            if tag == "img":
                alt = c.attr("alt")
                if alt:
                    node["name"] = alt
            if tag == "a":
                href = c.attr("href")
                if href:
                    node["url"] = href
            sub = walk(c)
            if sub:
                node["children"] = sub
            out.append(node)
        return out

    return walk(root_dom)


def write_a11y_json(a11y: list[dict], path: str) -> None:
    """Write the accessibility tree to a JSON file (P2.1)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(a11y, f, indent=2, ensure_ascii=False)


# --- P2.2 — Link collection ---------------------------------------------------


def collect_links(root_dom) -> list[str]:
    """Collect all ``<a href>`` URLs from a DOM tree (P2.2).

    Returns a list of URL strings (deduplicated, order-preserving).
    """
    seen: set[str] = set()
    out: list[str] = []

    def walk(n) -> None:
        for c in n.children:
            if c.is_element() and (c.tag or "").lower() == "a":
                href = c.attr("href")
                if href and href not in seen:
                    seen.add(href)
                    out.append(href)
            walk(c)

    walk(root_dom)
    return out


def write_links(links: list[str], path: str) -> None:
    """Write collected links to a plain-text file (one URL per line)."""
    with open(path, "w", encoding="utf-8") as f:
        for l in links:
            f.write(l + "\n")


# --- P2.3 — PPM → PNG (pure stdlib) -------------------------------------------


def write_png(bmp: Bitmap, path: str) -> None:
    """Encode a bitmap as a minimal PNG file using only stdlib (P2.3).

    Produces an 8-bit RGB (no alpha) PNG.  Uses ``zlib`` for the IDAT
    payload and ``struct`` for the binary framing.
    """
    w, h = bmp.width, bmp.height
    raw_rows = bytearray()
    for py in range(h):
        raw_rows.append(0)  # filter type: none
        row = bmp.pixels[py * w:(py + 1) * w]
        for px in row:
            raw_rows.extend(((px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF))

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows))
    png = sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


# --- P3.4 — CSS transition engine ---------------------------------------------


def run_transition(
    html: str,
    css_text: str,
    width: int,
    height: int,
    element_id: str,
    prop_start,
    prop_end,
    frames: int = 10,
    property_name: str = "background",
    log_sink=None,
) -> list[Bitmap]:
    """P3.4 — render N frames of a transition on the given element.

    Returns a list of N+1 Bitmaps: frame 0 is the start state, frame N is the
    end state, and intermediate frames are linearly interpolated.
    """
    import js as js_mod
    import transition as trans_mod
    from paint import Color

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()

    # Build the transition spec.
    spec = trans_mod.TransitionSpec(
        property=property_name,
        start=prop_start,
        end=prop_end,
        frames=frames,
    )
    engine = trans_mod.TransitionEngine()
    engine.add(spec)

    # Find the target element's StyledNode so we can override its property.
    target = tree.find_by_id(element_id) if hasattr(tree, "find_by_id") else None
    results: list[Bitmap] = []

    for f in range(frames + 1):
        engine.frame = f
        override = engine.frame_overrides()
        styled = css_mod.apply(tree, full_css)
        # Apply the interpolated value to the target element.
        _apply_override(styled, element_id, property_name, override.get(property_name))
        root = layout_mod.layout(styled, float(width), float(height))
        dl = paint_engine.emit_commands(root)
        results.append(paint_engine.rasterize(dl, width, height))

    return results


# --- P4.1 — @keyframes animation -----------------------------------------------


def animate_keyframes(
    html: str,
    css_text: str,
    width: int,
    height: int,
    element_id: str,
    animation_name: str,
    total_frames: int = 10,
    log_sink=None,
) -> list[Bitmap]:
    """P4.1 — render ``total_frames`` frames of a named @keyframes animation.

    Parses the CSS for ``@keyframes <animation_name>`` and produces a list of
    ``total_frames + 1`` Bitmaps, one per frame.  Frame ``i`` corresponds to
    normalized time ``i / total_frames`` within one iteration of the animation.

    Returns the list of rendered frames.
    """
    import js as js_mod

    tree = dom_mod.parse(html)
    full_css = _full_css(tree, css_text)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()

    anims = css_mod.parse_keyframes(full_css)
    if animation_name not in anims:
        raise ValueError(f"@keyframes '{animation_name}' not found in CSS")
    anim = anims[animation_name]

    results: list[Bitmap] = []
    for f in range(total_frames + 1):
        t = f / total_frames if total_frames > 0 else 0.0
        styled = css_mod.apply(tree, full_css)
        css_mod.apply_animation_frame(styled, element_id, anim, t)
        root = layout_mod.layout(styled, float(width), float(height))
        dl = paint_engine.emit_commands(root)
        results.append(paint_engine.rasterize(dl, width, height))

    return results


def _apply_override(styled_root, element_id: str, prop: str, value) -> None:
    """Recursively find the StyledNode for element_id and set the property."""
    import css as _css

    def walk(sn):
        dom = sn.dom
        if dom and getattr(dom, "attr", None) and dom.attr("id") == element_id:
            if prop == "background" and isinstance(value, Color):
                sn.props["background"] = value
            elif prop in ("width", "height", "opacity"):
                sn.props[prop] = _css.Length(value=value, unit="px") if prop != "opacity" else _css.Length(value=value, unit="")
            return
        for c in sn.children:
            walk(c)

    for c in styled_root.children:
        walk(c)


# --- P3.3 — JS built-in objects exposed via browser API ------------------------


def js_math_obj() -> dict:
    """Return the toy Math object (P3.3)."""
    import js as js_mod
    host = js_mod.Host(dom_mod.parse("<html><body></body></html>"), lambda _s: None)
    return host._math_obj()


def js_json_obj() -> dict:
    """Return the toy JSON object (P3.3)."""
    import js as js_mod
    host = js_mod.Host(dom_mod.parse("<html><body></body></html>"), lambda _s: None)
    return host._json_obj()


def render_file(
    html_path: str,
    out_path: str,
    width: int = 800,
    height: int = 600,
    event_id: str | None = None,
    click: tuple[float, float] | None = None,
) -> Bitmap:
    """High-level CLI entry: read an HTML file, render it, write PPM.

    If event_id is given, simulate a click on it; if click is given,
    hit-test the coordinates.
    """
    with open(html_path, encoding="utf-8", errors="replace") as f:
        html = f.read()
    if event_id:
        bmp = render_document_with_event(html, "", width, height, event_id)
    elif click:
        bmp = render_document_with_click(html, "", width, height, click[0], click[1])
    else:
        bmp = render_document(html, "", width, height)
    write_ppm(bmp, out_path)
    return bmp

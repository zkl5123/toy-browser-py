"""Public API: render_document() — HTML + CSS -> PPM pixels.

Runs the full pipeline:
parse -> style -> layout -> paint -> rasterize, and (optionally) executes
inline <script> blocks via the built-in JS engine before rendering.
"""
from __future__ import annotations

import json
import struct
import zlib
from typing import Optional

from toy_browser.document import dom as dom_mod
from toy_browser.style import css as css_mod
from toy_browser.layout import layout as layout_mod
from toy_browser.rendering import paint_engine
from toy_browser.rendering.paint import Bitmap, Color


def render_document(
    html: str,
    css_text: str = "",
    width: int = 800,
    height: int = 600,
) -> Bitmap:
    """Render an HTML string to a bitmap (runs inline scripts first)."""
    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    host = _run_scripts(tree)
    # P9.1 — extract @media rules and evaluate against the viewport.
    media_rules = css_mod.parse_media_queries(author_css)
    # P11.3 — extract @supports blocks (toy: always supported).
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
    use_cache: "str | bool" = False,
    progress: "net.LoadEventSink | None" = None,  # noqa: F821
    max_script_redirects: int = 3,
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
    * P23.1 — TLS is on by default; pass a client with
      ``verify_tls=False`` (the ``--insecure`` switch) for self-signed
      / debug servers.
    * P23.2/P23.3 — when *use_cache* is a directory path (or True for
      the default ``.toy_cache``), GETs consult the on-disk cache first
      (fresh → no wire; stale-with-ETag → ``If-None-Match``
      revalidation, 304 reuses the body).
    * P23.4 — when *progress* is a :class:`net.LoadEventSink`, each
      fetch emits start/document/subresource/done events (drive a GUI
      progress bar).

    A top-level network failure raises :class:`net.FetchError`
    (including :class:`net.RedirectLoop`); the caller renders the
    unified error page.
    """
    from toy_browser.network import net
    eff_client = client
    if eff_client is None:
        # P0 (GLM report) — the legacy ``timeout=None`` default
        # propagated straight into ``HttpClient(timeout=None)``,
        # which set ``self.timeout = None`` on the underlying
        # ``http.client.HTTPConnection``.  A ``None`` timeout means
        # the socket blocks forever: an unreachable host (blackholed
        # IP, dropped packets) hangs the render pipeline with no
        # feedback and no way to cancel.  The P0 fix substitutes
        # ``net.DEFAULT_TIMEOUT`` (10 s) when the caller did not
        # supply an explicit timeout, matching the behaviour of every
        # other entry point in :mod:`net`.
        eff_timeout = timeout if timeout is not None else net.DEFAULT_TIMEOUT
        eff_client = net.HttpClient(
            timeout=eff_timeout,
            cache=(net.CacheStore(use_cache if isinstance(use_cache, str)
                                  else ".toy_cache")
                   if use_cache else None),
            progress=progress,
        )
    elif progress is not None and getattr(eff_client, "progress", None) is None:
        eff_client.progress = progress

    html, resp = net.fetch_html(
        url, client=eff_client,
        follow_redirects=True,
    )
    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)

    # P22.4/P22.5 — load subresources against the document base.
    # R4 stage 0 — unified DocumentPolicy context: the document's
    # response headers (Content-Security-Policy + origin) are parsed
    # ONCE and shared by subresource loading and script execution, so
    # the CLI and GUI entry points gate subresources identically
    # (legacy code re-derived doc_csp from resp.headers here, while
    # the GUI read a non-existent attribute and silently dropped the
    # policy — the 4th report's csp_self/csp_inline GUI divergence).
    doc_csp = ""
    loaded: dict = {}
    doc_policy: object = None
    if load_subresources:
        base = net.parse_url(resp.final_url or url)
        doc_policy = net.DocumentPolicy.from_response(
            resp, doc_origin=net._origin_str(resp.final_url or url))
        try:
            loaded = net.load_subresources(
                tree, base,
                client=eff_client, timeout=timeout,
                progress=progress,
                policy=doc_policy,
            )
            # Append any successfully fetched stylesheets.
            for _u, r in (loaded.get("stylesheet") or {}).items():
                if r is not None and r.ok:
                    author_css += "\n" + r.body.decode("utf-8",
                                                     errors="replace")
        except Exception:
            # Subresource loading must never break page rendering (P22.4).
            pass

    # P0 (retest report) — run scripts with CSP gating + external scripts.
    from toy_browser.scripting import js as _js_mod
    csp_directives = None
    # R4 stage 0 — the unified DocumentPolicy (built above from the
    # document's actual response headers) is the single source of
    # truth; legacy callers without a policy context keep the None
    # (no CSP) behaviour.
    if doc_policy is not None:
        csp_directives = doc_policy.csp_directives
    # P0 (assessment report) — resource download cache: the fetched
    # response for a <script src> is reused when the same URL appears
    # more than once, but the *execution* of each script element is
    # managed independently (a duplicated tag still runs its body
    # once per element — the HTML scripting model, not a URL-level
    # dedup).  The cache is {absolute_url: code-or-None}; None means
    # the fetch failed / was policy-blocked.
    script_cache: dict = {}
    for _u, r in (loaded.get("script") or {}).items():
        if r is not None and r.ok:
            script_cache[_u] = r.body.decode("utf-8", errors="replace")
        else:
            script_cache[_u] = None
    host = _js_mod.Host(tree, lambda _s: None)
    # P1 (assessment report) — carry the document origin so CSP 'self'
    # matches the document origin, not unconditionally.
    # R4 stage 0 — prefer the unified policy context's origin (it is
    # the document's own origin, identical to the legacy value here).
    host.doc_origin = (doc_policy.doc_origin if doc_policy is not None
                       else net._origin_str(resp.final_url or url))
    # attributes resolve against the pipeline's absolute-URL cache.
    host.doc_url = resp.final_url or url
    # R4 stage 3 — in-page fetch()/XMLHttpRequest reach the SAME real
    # service the document load used (shared keep-alive client), so a
    # local /data.json fetch reports the real status + body (the
    # report's fetch_real / xhr_real cases).
    host._fetch_transport = net.FetchTransport(
        eff_client, base_url=resp.final_url or url)
    # R7-S01 第 1 点 — 远程脚本信任闸门：默认策略 local_only，任意
    # 网络页面的脚本**默认不执行**（回环 / file: / about: 等可信源
    # 不受影响）。判定结果挂在 host 上（可观测），被拒时**跳过**
    # execute_scripts_in_order——不是"跑了再丢结果"，而是真的不跑。
    from toy_browser.scripting import script_trust as _trust_mod
    _doc_url = resp.final_url or url
    _trust = _trust_mod.default_script_trust_gate().decide(_doc_url)
    host.script_trust_decision = _trust
    if _trust.allowed:
        host.execute_scripts_in_order(script_cache, csp_directives)

    # B05 — if the page's inline scripts emitted a location navigation
    # task (location.replace / assign / href=), follow it with a loop
    # guard: the classic baidu 227-byte page replaces
    # https:// -> http://, which would otherwise loop forever.  We
    # dedupe by seen-URL and cap total script-redirect hops.
    # R7-A01 — 统一 B05 follow 契约：经 navigation.select_pending_navigation_target
    # 解析（与 GUI PageInstance.load_url 同契约，消除 CLI/GUI 重复实现）。
    from toy_browser.app.navigation import select_pending_navigation_target
    pending = list(getattr(host, "_pending_nav", []) or [])
    base_url = resp.final_url or url
    follow = select_pending_navigation_target(pending, base_url) if (
        pending and max_script_redirects > 0) else None
    if follow is not None:
        return render_document_from_url(
            follow, css_text, width, height,
            load_subresources=load_subresources, client=client,
            timeout=timeout, use_cache=use_cache, progress=progress,
            max_script_redirects=max_script_redirects - 1,
        )

    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    dl = paint_engine.emit_commands(root)
    return paint_engine.rasterize(dl, width, height)


# --- C02 批次 43 — CPUImage 渲染入口（直接落 RGB8 buffer，CLI 消费）------

def render_document_to_cpu_image(
    html: str,
    css_text: str = "",
    width: int = 800,
    height: int = 600,
) -> "CPUImage":
    """C02 批次 43 — 渲染 HTML 出 :class:`CPUImage`（RGB8 连续 buffer）。

    与 :func:`render_document` 同一管线（parse → style → layout →
    paint → rasterize），但经 :func:`rasterize_cpu.rasterize_to_cpu_image`
    直接落 :class:`CPUImage`（C09 步骤 1 "直接读连续 RGB 行，消除导出
    时再次遍历 Python int 列表"）。CLI 输出走 ``CPUImage.save_ppm`` /
    ``save_png``（PNG 经 :meth:`CPUImage.to_png_bytes` 复用 C09
    ``encode_rows``）。
    """
    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    host = _run_scripts(tree)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    dl = paint_engine.emit_commands(root)
    from toy_browser.rendering.rasterize_cpu import rasterize_to_cpu_image
    return rasterize_to_cpu_image(dl, width, height)


def render_document_with_event_to_cpu_image(
    html: str,
    css_text: str,
    width: int,
    height: int,
    element_id: str,
    log_sink=None,
) -> "CPUImage":
    """C02 批次 43 — :func:`render_document_with_event` 的 CPUImage 变体。"""
    from toy_browser.scripting import js as js_mod
    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()
    host.execute_event(element_id)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root = layout_mod.layout(styled, float(width), float(height))
    dl = paint_engine.emit_commands(root)
    from toy_browser.rendering.rasterize_cpu import rasterize_to_cpu_image
    return rasterize_to_cpu_image(dl, width, height)


def render_document_with_click_to_cpu_image(
    html: str,
    css_text: str,
    width: int,
    height: int,
    x: float,
    y: float,
    log_sink=None,
) -> "CPUImage":
    """C02 批次 43 — :func:`render_document_with_click` 的 CPUImage 变体
    （命中测试 + 点击事件 + 重渲染，逐像素与 Bitmap 路径一致）。"""
    from toy_browser.scripting import js as js_mod
    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
        tree, author_css, ua_css=ua_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root2 = layout_mod.layout(styled2, float(width), float(height))
    dl = paint_engine.emit_commands(root2)
    from toy_browser.rendering.rasterize_cpu import rasterize_to_cpu_image
    return rasterize_to_cpu_image(dl, width, height)


def _run_scripts(tree):
    """Execute inline <script> blocks in document order via the toy JS engine."""
    from toy_browser.scripting import js as js_mod

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
    from toy_browser.scripting import js as js_mod

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()
    host.execute_event(element_id)
    # Re-render after the DOM mutation.
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
    from toy_browser.scripting import js as js_mod

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
        tree, author_css, ua_css=ua_css,
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
    from toy_browser.scripting import js as js_mod

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
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
                tree, author_css, ua_css=ua_css,
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
                tree, author_css, ua_css=ua_css,
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
            tree, author_css, ua_css=ua_css,
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
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    host = _run_scripts(tree)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
    from toy_browser.scripting import js as js_mod

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
        tree, author_css, ua_css=ua_css,
        media_rules=media_rules,
        supports_rules=supports_rules,
        viewport_w=float(width),
        viewport_h=float(height),
    )
    root2 = layout_mod.layout(styled2, float(width), float(height))
    dl = paint_engine.emit_commands(root2)
    return paint_engine.rasterize(dl, width, height)


def _full_css(tree, user_css: str) -> tuple[str, str]:
    """Return ``(author_css, ua_css)`` for the cascade.

    P0 (GLM report) — the legacy implementation returned a single
    concatenated string that :func:`css.parse_rulesheet` parsed with
    every rule tagged ``source="author"``, so the UA
    ``body { margin: 8px }`` cascaded on the same specificity tie as
    an author ``* { margin: 0 }`` reset and (depending on
    concatenation order) beat it.  The P0 fix returns the two layers
    separately; :func:`css.apply` parses the UA sheet with
    ``source="ua"`` and the author sheet with ``source="author"``,
    and the cascade key puts every author rule ahead of every UA rule
    in the normal layer (the CSS spec's origin precedence).
    """
    default_css = _default_css()
    style_tags = [n for n in _all_text_nodes(tree) if (n.tag or "").lower() == "style"]
    inline_style = "\n".join(n.text_content() for n in style_tags)
    author_css = "\n".join(p for p in [inline_style, user_css] if p)
    return author_css, default_css


# ---------------------------------------------------------------------------
# P32.4 — mini DevTools (DOM tree + computed style + selector provenance)
# ---------------------------------------------------------------------------

def devtools_dom_tree(root_dom, max_depth: int = 10,
                      max_nodes: int = 400) -> str:
    """P32.4 — print a readable DOM tree with tag, id, class, text.

    Indentation = depth; text-node-only elements show their trimmed
    text in ``[...]``.  Truncates at *max_depth* / *max_nodes*.
    """
    lines: list[str] = []
    count = 0

    def walk(node, depth: int) -> None:
        nonlocal count
        if depth > max_depth or count >= max_nodes:
            return
        if not node.is_element():
            return
        count += 1
        tag = (node.tag or "?").lower()
        ident = ""
        eid = node.attr("id")
        cls = node.attr("class")
        if eid:
            ident += f"#{eid}"
        if cls:
            ident += "." + ".".join(cls.split())
        text = node.text_content().strip()
        text_part = f" [{text[:40]}]" if text else ""
        pad = "  " * depth
        lines.append(f"{pad}<{tag}>{ident}{text_part}")
        for c in node.children:
            walk(c, depth + 1)

    # The root of the parse tree is a synthetic document node; descend
    # into its element children so the tree starts at <html>/<body>.
    if root_dom.is_element() and (root_dom.tag or "").lower() in ("html", "body", "document"):
        walk(root_dom, 0)
    else:
        for c in root_dom.children:
            walk(c, 0)
    return "\n".join(lines)


def _fmt_css_value(v) -> str:
    """P32.4 — human-readable form of a CSS value."""
    if v is None:
        return ""
    from toy_browser.style import css as _css
    if isinstance(v, _css.Length):
        unit = v.unit or "px"
        return f"{v.value:g}{unit}"
    if isinstance(v, _css.Color):
        return f"rgb({v.r},{v.g},{v.b})"
    if isinstance(v, _css.EdgeList):
        return " ".join(
            _fmt_css_value(getattr(v, side)) for side in ("top", "right", "bottom", "left")
        )
    return str(v)


def _devtools_effective_rules(css_text: str,
                              viewport_w: float = 800.0,
                              viewport_h: float = 600.0) -> list:
    """P32.4 — the same at-rule-aware rule set the renderer uses.

    ``css.parse_rulesheet`` drops at-rule blocks; the pipeline feeds their
    inner rules back in via :func:`css.parse_media_queries` (matched
    against the viewport) and :func:`css.parse_supports_blocks` (toy:
    always supported).  DevTools must use the same set, else a selector
    that only lives inside an at-rule would appear to match nothing.
    """
    rules = list(css_mod.parse_rulesheet(css_text))
    for mq in css_mod.parse_media_queries(css_text):
        if css_mod.media_matches(mq.condition, viewport_w, viewport_h):
            rules.extend(mq.rules)
    for sr in css_mod.parse_supports_blocks(css_text):
        if css_mod.supports_matches(sr.condition):
            rules.extend(sr.rules)
    return rules


def devtools_computed_style(root_dom, css_text: str,
                            element_id: str,
                             width: int = 800, height: int = 600,
                             ua_css: str = "") -> dict:
    """P32.4 — computed style for the element with *element_id*.

    Runs the cascade (UA + inline + author) and returns
    ``{prop: "value"}`` for every property the element actually carries,
    plus ``"__meta__"`` with tag / id / class.  Missing element → empty
    dict with a ``__notfound__`` flag.
    """
    styled = css_mod.apply(
        root_dom, css_text,
        media_rules=css_mod.parse_media_queries(css_text),
        supports_rules=css_mod.parse_supports_blocks(css_text),
        viewport_w=float(width), viewport_h=float(height),
        ua_css=ua_css,
    )
    target = None
    found_meta = None

    def walk(sn):
        nonlocal target, found_meta
        d = sn.dom
        if d.is_element() and d.attr("id") == element_id:
            target = sn
            found_meta = {"tag": (d.tag or "").lower(),
                          "id": element_id,
                          "class": d.attr("class") or ""}
            return
        for c in sn.children:
            walk(c)

    walk(styled)
    if target is None:
        return {"__notfound__": True, "__meta__": {"id": element_id}}
    out: dict = {}
    for k, v in target.props.items():
        out[k] = _fmt_css_value(v)
    out["__meta__"] = found_meta
    return out


def devtools_selector_hits(css_text: str, root_dom,
                           selector: str,
                           width: int = 800, height: int = 600) -> list[dict]:
    """P32.4 — which DOM nodes a *selector* matches, and the matching
    rule(s) that produce it.  Returns
    ``[{"node": tag#id.cls, "selectors": [...], "rules": [...]}]``.

    *selectors* = the query's own selector strings that matched the node.
    *rules*      = author/inline rule selector texts that matched it.
    """
    results: list[dict] = []
    sels = css_mod.parse_selector_list(selector)
    # P32.4 — use the *same* at-rule-aware rule set as the renderer so a
    # selector inside @media / @supports still reports a match (the toy
    # treats every @supports as supported; media rules only when their
    # query matches the default 800x600 viewport the DevTools run uses).
    author_rules = _devtools_effective_rules(
        css_text,
        viewport_w=float(width), viewport_h=float(height))

    def walk(node):
        if node.is_element():
            matched_query = []
            for s in sels:
                if s.matches(node):
                    txt = s.selector_text
                    if not txt:
                        txt = (" " + s.combinator + " ").join(s.parts)
                    matched_query.append(txt)
            if matched_query:
                rules_hit = []
                for r in author_rules:
                    for rs in r.selectors:
                        if rs.matches(node):
                            rules_hit.append(r.selector_text or "?")
                            break
                eid = node.attr("id")
                cls = node.attr("class")
                label = (node.tag or "?").lower()
                if eid:
                    label += f"#{eid}"
                if cls:
                    label += "." + ".".join(cls.split())
                results.append({
                    "node": label,
                    "selectors": matched_query,
                    "rules": rules_hit,
                })
        for c in node.children:
            walk(c)

    walk(root_dom)
    return results


def devtools_report(html: str, css_text: str = "",
                    width: int = 800, height: int = 600,
                    element_id: str = "", selector: str = "") -> str:
    """P32.4 — a single human-readable DevTools report.

    Includes the DOM tree, the computed style for *element_id* (when
    given), and selector provenance for *selector* (when given).  Used
    by the ``--devtools`` CLI flag.
    """
    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    _run_scripts(tree)  # let inline scripts mutate the DOM first
    parts: list[str] = []
    parts.append("=== DOM tree ===")
    parts.append(devtools_dom_tree(tree))
    if element_id:
        parts.append(f"\n=== computed style: #{element_id} ===")
        cs = devtools_computed_style(tree, author_css, element_id, width, height,
                                     ua_css=ua_css)
        if cs.get("__notfound__"):
            parts.append("(element not found)")
        else:
            meta = cs.pop("__meta__", {})
            for k, v in sorted(cs.items()):
                parts.append(f"  {k}: {v}")
            parts.append(f"  [meta] {meta}")
    if selector:
        parts.append(f"\n=== selector: {selector} ===")
        hits = devtools_selector_hits(author_css, tree, selector,
                                      width=width, height=height)
        if not hits:
            parts.append("(no matches)")
        for h in hits:
            parts.append(f"  {h['node']}  ←  {', '.join(h['selectors'])}")
            if h.get("rules"):
                parts.append(f"     rules: {', '.join(h['rules'])}")
    return "\n".join(parts)


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
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
    from toy_browser.scripting import js as js_mod

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
    from toy_browser.scripting import js as js_mod

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    media_rules = css_mod.parse_media_queries(author_css)
    supports_rules = css_mod.parse_supports_blocks(author_css)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
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
        tree, author_css, ua_css=ua_css,
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
    head, meta, link, style, script, title { display: none; }
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
    table { display: table; border-width: 1px; border-color: #aaaaaa; margin: 4px 0; }
    tr { display: table-row; }
    td { display: table-cell; padding: 2px 4px; border-width: 1px; border-color: #cccccc; }
    th { display: table-cell; padding: 2px 4px; border-width: 1px; border-color: #cccccc; background: #f0f0f0; }
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


# --- P28 — form serialization + submission --------------------------------------


def _form_field(node, name: str, value: str, kind: str) -> dict:
    """P28 — one collected form field (kind: "text" / "checkbox" / "radio" / "select" / "file" / "textarea" / "hidden")."""
    return {"name": name, "value": value, "kind": kind}


def _walk_dom(node, visit):
    """P28 — depth-first walk over the DOM subtree rooted at ``node``."""
    visit(node)
    for c in node.children:
        if c.is_element():
            _walk_dom(c, visit)


def collect_form_fields(root_dom, form_id: str = "") -> list[dict]:
    """P28.2 — collect all form fields in the document (or one <form>).

    Returns a list of ``{"name": …, "value": …, "kind": …}`` in DOM order.
    Only fields with a ``name`` attribute are serialisable; ``type="button"``
    and ``type="submit"`` without a name are skipped.  Checkbox / radio
    fields contribute only when ``checked`` is set.
    """
    fields: list[dict] = []

    def visit(node):
        if not node.is_element():
            return
        tag = (node.tag or "").lower()
        if form_id and tag == "form" and node.attr("id") != form_id:
            # skip the whole subtree when a different form is targeted
            return
        if form_id and tag != "form" and node.parent is not None:
            # walk only inside the requested form (or the whole document).
            pass
        if tag in ("input", "textarea", "select", "button"):
            name = node.attr("name") or ""
            if not name:
                return
            kind_map = {
                "input": "input", "textarea": "textarea",
                "select": "select", "button": "button",
            }
            base_kind = kind_map.get(tag, "text")
            itype = (node.attr("type") or "").lower()
            if tag == "input":
                if itype in ("button", "submit", "image", "reset") and itype != "submit":
                    return
                if itype in ("checkbox", "radio"):
                    if node.attr("checked") is None:
                        return
                    fields.append(_form_field(node, name, node.attr("value") or "on", itype))
                    return
                if itype == "hidden" or itype == "text" or itype == "password" \
                        or itype == "email" or itype == "number" or itype == "url" \
                        or itype == "date" or itype == "time" or itype == "month" \
                        or itype == "week" or itype == "color" or itype == "range" \
                        or itype == "tel" or itype == "search":
                    fields.append(_form_field(node, name, node.attr("value") or "", itype or "text"))
                    return
                if itype == "file":
                    fields.append(_form_field(node, name, node.attr("value") or "", "file"))
                    return
                # unknown input type: treat as text.
                fields.append(_form_field(node, name, node.attr("value") or "", "text"))
            elif tag == "textarea":
                text = "".join(c.text or "" for c in node.children if not c.is_element())
                fields.append(_form_field(node, name, text, "textarea"))
            elif tag == "select":
                multi = node.attr("multiple") is not None
                selected: list[str] = []
                for opt in node.children:
                    if opt.is_element() and (opt.tag or "").lower() == "option":
                        if opt.attr("selected") is not None:
                            selected.append(opt.attr("value") or (opt.text or ""))
                if multi:
                    for v in selected:
                        fields.append(_form_field(node, name, v, "select"))
                else:
                    fields.append(_form_field(node, name, selected[0] if selected else "", "select"))
            elif tag == "button":
                fields.append(_form_field(node, name, node.attr("value") or "", "button"))

    # If a form id is given, only collect fields inside that form.
    if form_id:
        _collect_in_form(root_dom, form_id, fields)
        return fields
    _walk_dom(root_dom, visit)
    return fields


def _walk_form_fields(form_node, fields: list[dict]) -> None:
    """P28.2 — walk a <form> subtree and append serialisable fields."""
    for node in _form_subtree(form_node):
        if not node.is_element():
            continue
        tag = (node.tag or "").lower()
        if tag == "input":
            name = node.attr("name") or ""
            if not name:
                continue
            itype = (node.attr("type") or "").lower()
            if itype in ("button", "image", "reset"):
                continue
            if itype in ("checkbox", "radio"):
                if node.attr("checked") is None:
                    continue
                fields.append(_form_field(node, name, node.attr("value") or "on", itype))
            else:
                fields.append(_form_field(node, name, node.attr("value") or "", itype or "text"))
        elif tag == "textarea":
            name = node.attr("name") or ""
            if not name:
                continue
            text = "".join(c.text or "" for c in node.children if not c.is_element())
            fields.append(_form_field(node, name, text, "textarea"))
        elif tag == "select":
            name = node.attr("name") or ""
            if not name:
                continue
            multi = node.attr("multiple") is not None
            selected = [
                (o.attr("value") or (o.text or ""))
                for o in node.children
                if o.is_element() and (o.tag or "").lower() == "option" and o.attr("selected") is not None
            ]
            if multi:
                for v in selected:
                    fields.append(_form_field(node, name, v, "select"))
            else:
                fields.append(_form_field(node, name, selected[0] if selected else "", "select"))
        elif tag == "button":
            name = node.attr("name") or ""
            if not name:
                continue
            fields.append(_form_field(node, name, node.attr("value") or "", "button"))


def _form_subtree(form_node):
    """P28.2 — yield every element in the form subtree (excluding the form itself)."""
    stack = list(form_node.children)
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _collect_in_form(root_dom, form_id: str, fields: list[dict]) -> None:
    """P28.2 — find the form with the given id and collect its fields."""
    stack = [root_dom]
    while stack:
        n = stack.pop()
        if not n.is_element():
            continue
        if (n.tag or "").lower() == "form" and n.attr("id") == form_id:
            _walk_form_fields(n, fields)
            return
        stack.extend(n.children)


def serialize_form_fields(fields: list[dict], mode: str = "GET") -> str:
    """P28.2 — serialise form fields to an ``application/x-www-form-urlencoded``
    query string (GET) or POST body (POST).  Multiple values for the same
    name (e.g. ``<select multiple>``) are emitted as repeated ``k=v`` pairs.
    """
    import urllib.parse
    parts: list[str] = []
    for f in fields:
        parts.append(urllib.parse.quote(f["name"], safe=""))
        parts.append("=")
        parts.append(urllib.parse.quote(str(f["value"]), safe=""))
        parts.append("&")
    qs = "".join(parts)
    return qs[:-1] if qs else ""


def form_action_url(root_dom, form_id: str = "", action: str = "") -> str:
    """P28.2 — resolve a <form>'s action attribute against the document base.

    Returns the target URL for GET/POST submission.  ``action`` is first
    read from the form's DOM attribute (when ``form_id`` is given); the
    ``action`` parameter is a fallback.  Root-relative (``/…``) actions
    resolve against the document's base (``<base href>`` or the current
    URL passed via the ``action`` parameter).
    """
    import urllib.parse
    action_found = ""
    if form_id:
        stack = [root_dom]
        while stack:
            n = stack.pop()
            if not n.is_element():
                continue
            if (n.tag or "").lower() == "form" and n.attr("id") == form_id:
                action_found = n.attr("action") or ""
                break
            stack.extend(n.children)
    target = action_found or action
    # Find the document's base URL (``<base href>`` tag, else "").
    base = ""
    def _find_base(node):
        for c in node.children:
            if c.is_element() and (c.tag or "").lower() == "base":
                b = c.attr("href") or ""
                if b:
                    return b
                continue
            r = _find_base(c)
            if r:
                return r
        return ""
    base = _find_base(root_dom)
    if not target:
        return base
    if target.startswith("http://") or target.startswith("https://"):
        return target
    if target.startswith("//"):
        scheme = (base or "http://").split("://")[0]
        return scheme + "://" + target
    if target.startswith("/"):
        if base:
            origin = base.split("://", 1)[1]
            origin = "://" + origin.split("/", 1)[0]
            return "http" + origin + target
        return target
    # Relative (no leading slash): resolve against the base's directory.
    if base:
        return urllib.parse.urljoin(base, target)
    return target


def form_get_url(root_dom, form_id: str = "", base_url: str = "") -> str:
    """P28.2 — build the full GET submission URL: action + "?" + query string."""
    fields = collect_form_fields(root_dom, form_id)
    qs = serialize_form_fields(fields, mode="GET")
    action = form_action_url(root_dom, form_id, base_url)
    if not action:
        return qs
    if "?" in action:
        return action + "&" + qs if qs else action
    return action + ("?" + qs if qs else "")


def form_post_body(root_dom, form_id: str = "", base_url: str = "") -> dict:
    """P28.2 — build the POST body + headers for a form submission.

    Returns ``{"url": …, "body": …, "content_type": …}``.
    """
    fields = collect_form_fields(root_dom, form_id)
    body = serialize_form_fields(fields, mode="POST")
    action = form_action_url(root_dom, form_id, base_url)
    return {
        "url": action,
        "body": body,
        "content_type": "application/x-www-form-urlencoded",
    }


def validate_form(root_dom, form_id: str = "") -> list[dict]:
    """P28.3 — toy form validation: return a list of violations
    ``{"field": name, "kind": "required" / "pattern", "message": …}``.

    A field with the ``required`` attribute that is empty (text /
    select / textarea) or unchecked (checkbox / radio) is a violation.
    Pattern validation (``pattern`` attribute, regex, toy: substring
    check only when the pattern has no metacharacters) is also checked.
    """
    import re
    violations: list[dict] = []
    stack = [root_dom]
    if form_id:
        # Find the target form first.
        target = None
        while stack:
            n = stack.pop()
            if not n.is_element():
                continue
            if (n.tag or "").lower() == "form" and n.attr("id") == form_id:
                target = n
                break
            stack.extend(n.children)
        if target is None:
            return violations
        stack = [target]
    while stack:
        n = stack.pop()
        if not n.is_element():
            continue
        tag = (n.tag or "").lower()
        if tag in ("input", "textarea", "select"):
            name = n.attr("name") or ""
            required = n.attr("required") is not None
            itype = (n.attr("type") or "").lower()
            value = n.attr("value") or ""
            if tag == "textarea":
                value = "".join(c.text or "" for c in n.children if not c.is_element())
            if tag == "select":
                value = ""
                for opt in n.children:
                    if opt.is_element() and (opt.tag or "").lower() == "option" and opt.attr("selected") is not None:
                        value = opt.attr("value") or (opt.text or "")
                        break
            if required:
                # P28.3 — checkbox / radio "emptiness" is the absence of
                # the checked attribute, not an empty value.
                if itype in ("checkbox", "radio"):
                    empty = n.attr("checked") is None
                else:
                    empty = value == ""
                if empty:
                    violations.append({"field": name, "kind": "required",
                                       "message": f"'{name}' is required"})
            pat = n.attr("pattern") or ""
            if pat and value:
                try:
                    if not re.fullmatch(pat, value):
                        violations.append({"field": name, "kind": "pattern",
                                           "message": f"'{name}' does not match '{pat}'"})
                except re.error:
                    pass
        stack.extend(n.children)
    return violations


def tab_order(root_dom) -> list[str]:
    """P28.3 — compute the Tab-key navigation order for focusable elements.

    Returns a list of element ids in tab order.  Focusable elements are:
    ``<a href>``, ``<button>``, ``<input>`` (except type="hidden"/"image"),
    ``<select>``, ``<textarea>``, and any element with a ``tabindex``
    attribute (>= 0).  Elements with ``tabindex >= 1`` sort before
    natural-order elements; ``tabindex="0"`` keeps DOM order;
    ``tabindex < 0`` (or absent) is last, in DOM order.
    """
    focusable: list[tuple[int, int, str, int]] = []  # (priority, dom_index, id, tabindex)
    idx = 0
    def walk(node):
        nonlocal idx
        if not node.is_element():
            return
        tag = (node.tag or "").lower()
        eid = node.attr("id") or ""
        itype = (node.attr("type") or "").lower()
        tabindex_raw = node.attr("tabindex")
        tabindex = 0
        has_tabindex = tabindex_raw is not None
        if has_tabindex:
            try:
                tabindex = int(tabindex_raw)
            except ValueError:
                tabindex = 0
        is_focusable = (
            tag == "a" and node.attr("href") is not None
            or tag == "button"
            or (tag == "input" and itype not in ("hidden", "image"))
            or tag == "select"
            or tag == "textarea"
            or (tag == "form" and node.attr("tabindex") is not None)
        )
        if is_focusable or (has_tabindex and tabindex >= 0 and tag not in ("script", "style")):
            if has_tabindex and tabindex > 0:
                priority = 0
            elif has_tabindex and tabindex == 0:
                priority = 1
            else:
                priority = 1
            if eid:
                focusable.append((priority, idx, eid, tabindex))
        idx += 1
        for c in node.children:
            walk(c)
    walk(root_dom)
    # Sort: priority asc, then DOM index asc.
    focusable.sort(key=lambda t: (t[0], t[1]))
    return [eid for _, _, eid, _ in focusable]


def focus_element(root_dom, element_id: str, set_attr: bool = True) -> bool:
    """P28.3 — set the DOM ``focus`` attribute on the target element.

    Used to visually mark the focused element (the renderer reads
    ``bx.control_focus`` from the DOM ``focus=""`` attribute).  Returns
    True if the element was found and focused.
    """
    stack = [root_dom]
    while stack:
        n = stack.pop()
        if not n.is_element():
            continue
        if n.attr("id") == element_id:
            if set_attr:
                n.set_attr("focus", "")
            return True
        stack.extend(n.children)
    return False


def form_submit(root_dom, form_id: str = "", method: str = "GET",
                base_url: str = "", submit_host=None) -> dict:
    """P28.2 — submit a form: validate, serialise, and (if a host is
    provided) POST/GET the result via the P21 network layer.

    Returns ``{"ok": bool, "url": …, "body": …, "violations": […]}``.
    When ``submit_host`` is a ``net.HttpClient`` instance the method
    dispatches a real HTTP request; otherwise the result is a dry-run
    with ``url`` / ``body`` populated but no network I/O.
    """
    violations = validate_form(root_dom, form_id)
    if violations:
        return {"ok": False, "url": "", "body": "", "violations": violations}
    if method.upper() == "POST":
        result = form_post_body(root_dom, form_id, base_url)
        url = result["url"]
        body = result["body"]
        content_type = result["content_type"]
    else:
        fields = collect_form_fields(root_dom, form_id)
        qs = serialize_form_fields(fields, mode="GET")
        url = form_action_url(root_dom, form_id, base_url)
        if not url:
            url = base_url
        if qs and url:
            sep = "&" if "?" in url else "?"
            url = url + sep + qs
        body = ""
        content_type = ""
    out = {"ok": True, "url": url, "body": body, "violations": []}
    # P21 — dispatch via the network layer when a host is provided.
    if submit_host is not None:
        try:
            if method.upper() == "POST":
                resp = submit_host.post(url, body=body,
                                         headers={"Content-Type": content_type})
                out["status"] = getattr(resp, "status", 0)
                out["response_body"] = getattr(resp, "body", "")
            else:
                resp = submit_host.get(url)
                out["status"] = getattr(resp, "status", 0)
                out["response_body"] = getattr(resp, "body", "")
        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)
    return out



# --- P2.3 — PPM → PNG (pure stdlib) -------------------------------------------


def _png_pack_row(row: list, raw_rows: bytearray, off: int) -> int:
    """P2 (R4 stage 7) — pack one row of 32-bit pixels
    (``0x00RRGGBB``, alpha dropped by :meth:`Color.to_u32`) into the
    filter-0 raw-row buffer at *raw_rows[off:]*, writing the standard
    PNG pixel-major ``R G B R G B …`` channel order.  Returns the new
    offset.

    Pre-sized buffer + direct index assignment instead of the legacy
    growing-``bytearray`` ``extend((r, g, b), ...)`` call — bit-
    identical output (verified on real-render + 800×600 random-pixel
    + 400×16000 bitmaps).  The deep C-level rasterization/encoding the
    plan's P2 decision point targets is out of scope for the stdlib
    engine; this is a ~1.4x local win on the PNG stage.
    """
    for px in row:
        raw_rows[off] = (px >> 16) & 0xFF
        raw_rows[off + 1] = (px >> 8) & 0xFF
        raw_rows[off + 2] = px & 0xFF
        off += 3
    return off


def _png_encode_rows(bmp: Bitmap, raw_rows: bytearray, size_hint: int) -> bytes:
    """P2.3 — encode a stdlib PNG body from a pre-built raw RGB row
    buffer (filter-type-0 rows, R=0x00 + GGB triplets).  Shared by
    :func:`write_png` and :func:`write_png_bytes`.
    """
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", bmp.width, bmp.height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows))
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _png_raw_rows(bmp: Bitmap) -> bytearray:
    """Build the filter-0 raw-row payload for *bmp*: a pre-sized
    bytearray plus one :func:`_png_pack_row` per scanline.  The
    pre-size avoids the append-growth reallocation cascade the
    legacy ``bytearray() + append`` loop paid (P2 profiling finding)."""
    w, h = bmp.width, bmp.height
    raw_rows = bytearray(h * (w * 3 + 1))
    off = 0
    for py in range(h):
        raw_rows[off] = 0  # filter type: none
        off += 1
        off = _png_pack_row(bmp.pixels[py * w:(py + 1) * w], raw_rows, off)
    return raw_rows


def write_png(bmp: Bitmap, path: str) -> None:
    """Encode a bitmap as a minimal PNG file using only stdlib (P2.3).

    Produces an 8-bit RGB (no alpha) PNG.  Uses ``zlib`` for the IDAT
    payload and ``struct`` for the binary framing.

    P2 (R4 stage 7) — the row-packing loop now writes into a
    pre-sized buffer (see :func:`_png_pack_row`) instead of the legacy
    growing-``bytearray`` ``extend`` call.  Output bytes are
    bit-identical to the legacy encoder.
    """
    raw_rows = _png_raw_rows(bmp)
    png = _png_encode_rows(bmp, raw_rows, len(raw_rows))
    with open(path, "wb") as f:
        f.write(png)


def write_png_bytes(bmp: Bitmap) -> bytes:
    """P2 (R4 stage 7) — the PNG encode of *bmp* as in-memory bytes
    (no file I/O); the profiling stage measures this so the encode
    segment is isolated from disk latency.  Shares the exact row
    packing + zlib path of :func:`write_png`."""
    raw_rows = _png_raw_rows(bmp)
    return _png_encode_rows(bmp, raw_rows, len(raw_rows))


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
    from toy_browser.scripting import js as js_mod
    from toy_browser.style import transition as trans_mod
    from toy_browser.rendering.paint import Color

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
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
        styled = css_mod.apply(tree, author_css, ua_css=ua_css)
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
    from toy_browser.scripting import js as js_mod

    tree = dom_mod.parse(html)
    author_css, ua_css = _full_css(tree, css_text)
    host = js_mod.Host(tree, log_sink or (lambda _s: None))
    host.execute_all()

    anims = css_mod.parse_keyframes(author_css)
    if animation_name not in anims:
        raise ValueError(f"@keyframes '{animation_name}' not found in CSS")
    anim = anims[animation_name]

    results: list[Bitmap] = []
    for f in range(total_frames + 1):
        t = f / total_frames if total_frames > 0 else 0.0
        styled = css_mod.apply(tree, author_css, ua_css=ua_css)
        css_mod.apply_animation_frame(styled, element_id, anim, t)
        root = layout_mod.layout(styled, float(width), float(height))
        dl = paint_engine.emit_commands(root)
        results.append(paint_engine.rasterize(dl, width, height))

    return results


def _apply_override(styled_root, element_id: str, prop: str, value) -> None:
    """Recursively find the StyledNode for element_id and set the property."""
    from toy_browser.style import css as _css

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
    from toy_browser.scripting import js as js_mod
    host = js_mod.Host(dom_mod.parse("<html><body></body></html>"), lambda _s: None)
    return host._math_obj()


def js_json_obj() -> dict:
    """Return the toy JSON object (P3.3)."""
    from toy_browser.scripting import js as js_mod
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

"""C01 step 4 — 公共 API 包装层：渲染管线接同一文档生命周期入口
（批次 9，纯逻辑核心）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C01 step 4：

> 建立 DocumentController 的唯一修改入口，把 GUI、CLI 和测试都接到
> 同一流程，保留公共 API 包装层兼容旧调用。

批次 7 落地了 :class:`DocumentState`（完整生命周期容器）；本批把
**渲染管线**接到同一入口：:func:`render_document_state` 在
:class:`DocumentState` 上跑 "解析 → 脚本初始化 → 样式 → 布局 → 光栅化"，
每阶段按 C01 规则 bump 正确的版本（新文档 = new_document；重排 =
mark_restate；渲染 = mark_render），脚本初始化只发生一次（验收
"脚本初始化计数只增加一次"）。

旧公共 API（``browser.render_document`` / ``render_document_from_url``
等）保持**签名与行为不变**（兼容旧调用）；本包装层是 CLI / 测试
接同一流程的新入口，GUI 侧在活显示环境批次把 ``PageInstance`` 也
接到这里。
"""

from __future__ import annotations

from typing import Optional

from toy_browser.app.document_state_full import DocumentState
from toy_browser.document import dom as dom_mod
from toy_browser.scripting import js as js_mod
from toy_browser.style import css as css_mod
from toy_browser.layout import layout as layout_mod
from toy_browser.rendering import paint_engine as paint_mod
from toy_browser.rendering.paint import Bitmap


def _full_css(tree, user_css: str) -> tuple[str, str]:
    """author CSS（user_css + 文档内联 <style>）与 UA CSS 的拼接
    （与 browser._full_css 同款，保持包装层自洽、不跨模块私有依赖）。"""
    author = user_css or ""
    stack = [tree]
    while stack:
        n = stack.pop()
        if not n.is_element():
            continue
        if (n.tag or "").lower() == "style":
            author += "\n" + (n.text or "")
        stack.extend(n.children)
    ua = layout_mod.UA_CSS if hasattr(layout_mod, "UA_CSS") else ""
    return author, ua


def render_document_state(doc: DocumentState,
                          html: str,
                          css_text: str = "",
                          width: int = 800,
                          height: int = 600,
                          base_url: str = "",
                          policy: Optional[dict] = None) -> Bitmap:
    """C01 step 4 — 在 *doc*（:class:`DocumentState`）上渲染一次文档。

    阶段与版本规则：

    1. **解析 + 脚本初始化 = 新文档**：``dom_mod.parse(html)`` +
       ``js_mod.Host(tree, ...).execute_all()``，经
       :meth:`DocumentState.new_document` 一次性完成（文档身份 +
       脚本初始化计数 +1）。
    2. **样式 + 布局 = 重排**：``css.apply`` + ``layout.layout`` 经
       :meth:`DocumentState.mark_restate`（只 bump ``layout_revision``，
       **不**重建 Host / 重跑脚本——重排后点击监听仍在）。
    3. **光栅化 = 渲染**：``paint.emit_commands`` +
       ``paint.rasterize`` 经 :meth:`DocumentState.mark_render`
       （只 bump ``frame_id``）。

    返回 :class:`Bitmap`（与旧 ``browser.render_document`` 同款）。
    调用方对同一 *doc* 反复调用本函数（如 hover / resize 重排）时，
    只有**第一次**经过脚本初始化；后续只重排 + 渲染。

    与旧 API 的关系：``browser.render_document(html, ...)`` 保持原样
    （不接 DocumentState），本函数是 CLI / 测试接"同一流程"的包装
    入口——两者对相同输入得到**同一 DOM 与同一位图**（C01 验收，
    可经 :func:`document_state_full.assert_same_dom` 比对 DOM 指纹）。
    """
    # 1 — 解析 + 脚本初始化（新文档；脚本初始化只发生一次）
    tree = dom_mod.parse(html)
    host = js_mod.Host(tree, lambda _s: None)
    host.execute_all()
    doc.new_document(tree, host, policy=policy or {},
                     base_url=base_url or html)

    # 2 — 样式 + 布局（重排：只 bump layout_revision）
    doc.mark_restate()
    author_css, ua_css = _full_css(tree, css_text)
    media = css_mod.parse_media_queries(author_css)
    supports = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
        media_rules=media, supports_rules=supports,
        viewport_w=float(width), viewport_h=float(height))
    root = layout_mod.layout(styled, float(width), float(height))

    # 3 — 光栅化（渲染：只 bump frame_id）
    doc.mark_render()
    dl = paint_mod.emit_commands(root)
    return paint_mod.rasterize(dl, width, height)


def render_document_state_restate(doc: DocumentState,
                                  css_text: str = "",
                                  width: int = 800,
                                  height: int = 600) -> Bitmap:
    """C01 — 在**已有** *doc* 上重排 + 渲染（hover / focus / scroll /
    resize 场景）：复用 *doc* 的 DOM 与 Host（**不**重新解析 / 不
    重跑脚本——验收"重排后点击监听仍在、脚本初始化只增加一次"），
    只重解样式 + 布局 + 光栅化。

    版本规则：:meth:`DocumentState.mark_restate`（bump
    layout_revision）+ :meth:`DocumentState.mark_render`（bump
    frame_id）；``doc.script_init_count`` 保持不变。"""
    tree = doc.dom_tree
    host = doc.script_host
    if tree is None:
        raise ValueError("render_document_state_restate 需要 doc 已有"
                         " dom_tree（先经 render_document_state 初始化）")
    # 重排：只 bump layout_revision，不重建 Host
    doc.mark_restate()
    author_css, ua_css = _full_css(tree, css_text)
    media = css_mod.parse_media_queries(author_css)
    supports = css_mod.parse_supports_blocks(author_css)
    styled = css_mod.apply(
        tree, author_css, ua_css=ua_css,
        media_rules=media, supports_rules=supports,
        viewport_w=float(width), viewport_h=float(height))
    root = layout_mod.layout(styled, float(width), float(height))
    # 渲染：只 bump frame_id
    doc.mark_render()
    dl = paint_mod.emit_commands(root)
    return paint_mod.rasterize(dl, width, height)

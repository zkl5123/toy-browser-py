"""C01 step 4 — 公共 API 包装层（渲染管线接同一文档生命周期入口）
回归（round5 docs 批次 9）。

覆盖 toy_browser/app/document_renderer.py：
- render_document_state：解析+脚本初始化=新文档（计数 +1），样式+
  布局=重排（mark_restate 只 bump layout_revision），光栅化=渲染
  （mark_render 只 bump frame_id）
- 同一 doc 反复调用：脚本初始化只发生一次（重排/渲染不重跑脚本）
- 包装层与旧 browser.render_document 相同输入同一 DOM + 同一位图
  （C01 验收：CLI/GUI 同一流程）
"""
from __future__ import annotations

from toy_browser.app.document_state_full import DocumentState
from toy_browser.app.document_renderer import (
    render_document_state, render_document_state_restate,
)
from toy_browser.app.document_state import reset_version_sequences
from toy_browser.document import dom as dom_mod
from toy_browser.app import browser


TESTS = []

_HTML = ("<!DOCTYPE html><html><head><style>h1{color:#0000ff}</style>"
         "</head><body><h1 id=\"t\">Hello</h1>"
         "<script>document.getElementById('t').textContent='X';"
         "</script></body></html>")


def _render(doc, html=_HTML, w=800, h=600):
    return render_document_state(doc, html, "", w, h,
                                 base_url="file:///x.html")


def test_c01_renderer_script_init_once():
    """验收：脚本初始化计数只增加一次（渲染后重排不重跑脚本）。"""
    reset_version_sequences()
    doc = DocumentState()
    _render(doc)
    assert doc.script_init_count == 1
    # 重排 + 渲染（同一 doc）：计数不变
    for _ in range(4):
        doc.mark_restate()
        doc.mark_render()
    assert doc.script_init_count == 1


def test_c01_renderer_restate_no_host_rebuild():
    """重排不重建 Host / 不重跑脚本（node_id 跨重排不变）。"""
    reset_version_sequences()
    doc = DocumentState()
    _render(doc)
    host_before = doc.script_host
    ids_before = dict(doc.node_ids)
    rev_before = doc.doc_version.layout_revision
    doc.mark_restate()
    doc.mark_render()
    assert doc.script_host is host_before          # Host 未重建
    assert doc.node_ids == ids_before              # node_id 不变
    assert doc.doc_version.layout_revision > rev_before
    assert doc.script_init_count == 1


def test_c01_renderer_reuse_via_restate_fn():
    """render_document_state_restate：复用已有 doc 的 DOM/Host 重排
    + 渲染，不重新解析 / 不重跑脚本。"""
    reset_version_sequences()
    doc = DocumentState()
    _render(doc)
    assert doc.script_init_count == 1
    # 用 restate 入口重排 + 渲染（模拟 hover / resize）
    bmp = render_document_state_restate(doc, "", 800, 600)
    assert bmp is not None and bmp.width == 800 and bmp.height == 600
    assert doc.script_init_count == 1   # 未重跑脚本


def test_c01_renderer_new_document_via_new_context():
    """新导航 new_context：旧上下文 disposed，新文档脚本初始化 +1。"""
    reset_version_sequences()
    doc = DocumentState()
    _render(doc)
    old_doc_id = doc.doc_version.document_id
    old_host = doc.script_host
    fresh = doc.new_context()
    assert doc.disposed is True
    assert fresh.script_init_count == 0
    _render(fresh)
    assert fresh.script_init_count == 1
    assert fresh.script_host is not old_host   # 不跨源复用 JS 全局
    assert fresh.doc_version.document_id > old_doc_id
    # 旧文档的帧对新文档全 stale
    assert fresh.is_stale_frame(old_doc_id, 0) is True


def test_c01_renderer_matches_legacy_browser_dom():
    """C01 验收：包装层与旧 browser.render_document 相同输入 →
    同一 DOM 指纹（同流程，CLI/GUI 一致）。"""
    reset_version_sequences()
    doc = DocumentState()
    _render(doc)
    # 旧 API 对同一 HTML 的 DOM（脚本初始化后的树）
    legacy_tree = dom_mod.parse(_HTML)
    from toy_browser.scripting import js as js_mod
    h = js_mod.Host(legacy_tree, lambda _s: None)
    h.execute_all()
    from toy_browser.app.document_state_full import assert_same_dom
    assert assert_same_dom(doc.dom_tree, legacy_tree) is True


def test_c01_renderer_frame_version_bumps():
    """渲染 bump frame_id（光栅化 = 渲染阶段）。"""
    reset_version_sequences()
    doc = DocumentState()
    fid_before = doc.doc_version.frame_id
    _render(doc)
    assert doc.doc_version.frame_id > fid_before
    rev = doc.doc_version.layout_revision
    fid = doc.doc_version.frame_id
    assert doc.is_stale_frame(doc.doc_version.document_id, fid) is False
    assert doc.is_stale_layout(rev) is False


TESTS.append(("test_c01_renderer_script_init_once",
              test_c01_renderer_script_init_once))
TESTS.append(("test_c01_renderer_restate_no_host_rebuild",
              test_c01_renderer_restate_no_host_rebuild))
TESTS.append(("test_c01_renderer_reuse_via_restate_fn",
              test_c01_renderer_reuse_via_restate_fn))
TESTS.append(("test_c01_renderer_new_document_via_new_context",
              test_c01_renderer_new_document_via_new_context))
TESTS.append(("test_c01_renderer_matches_legacy_browser_dom",
              test_c01_renderer_matches_legacy_browser_dom))
TESTS.append(("test_c01_renderer_frame_version_bumps",
              test_c01_renderer_frame_version_bumps))

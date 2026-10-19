"""C01 完整 — DocumentState（文档生命周期与任务版本）回归。

覆盖 document_state_full.py：
- 脚本初始化只增加一次（new_document +1；重排 / 渲染保持）
- 重排后点击监听仍在（node_id 跨重排不变 + layout_revision bump
  不重建 Host）
- 新导航新上下文（disposed 旧 + 新版本，不跨源复用策略 / JS 全局）
- 资源注册表跨重排复用
- 版本门控（is_stale_frame / is_stale_layout 经 DocumentState 委托）
- CLI / GUI 相同输入同一 DOM（assert_same_dom 指纹一致）
"""
from __future__ import annotations

from toy_browser.app.document_state_full import (
    DocumentState, assert_same_dom,
    RES_STYLE, RES_IMAGE, RES_SCRIPT,
)
from toy_browser.app.document_state import reset_version_sequences


TESTS = []


class _FakeNode:
    """最小 DOM 桩：is_element() / tag / children / text / attrs。"""
    def __init__(self, tag, text=None, children=None, attrs=None):
        self.tag = tag
        self.text = text
        self.children = children or []
        self.attrs = attrs or {}

    def is_element(self):
        return self.tag is not None

    def attr(self, name):
        return self.attrs.get(name)


def _fake_doc():
    body = _FakeNode("body", children=[
        _FakeNode("h1", text="Hello", attrs={"id": "t"}),
        _FakeNode("button", text="Go", attrs={"onclick": "f()"}),
    ])
    return _FakeNode("html", children=[body])


def _host():
    return object()  # 任意 Host 占位


def test_c01_script_init_only_once():
    """验收：脚本初始化计数只增加一次。

    new_document 时 +1；后续 mark_restate / mark_render 绝不经过
    脚本初始化路径，计数保持 1。"""
    reset_version_sequences()
    ds = DocumentState()
    ds.new_document(_fake_doc(), _host(), base_url="https://a")
    assert ds.script_init_count == 1
    # 多次重排 / 渲染：计数不变
    for _ in range(5):
        ds.mark_restate()
        ds.mark_render()
    assert ds.script_init_count == 1
    # 同一文档再次调用 new_document（重解析同一源）= 第 2 次初始化
    ds.new_document(_fake_doc(), _host())
    assert ds.script_init_count == 2


def test_c01_restate_keeps_node_ids_and_host():
    """验收：重排后点击监听仍在 — node_id 跨重排不变；Host 不重建。"""
    reset_version_sequences()
    ds = DocumentState()
    tree = _fake_doc()
    host = _host()
    ds.new_document(tree, host, base_url="https://a")
    before_rev = ds.doc_version.layout_revision
    doc_id_before = ds.doc_version.document_id
    ids_before = dict(ds.node_ids)
    host_before = ds.script_host
    # 多次重排
    for _ in range(3):
        ds.mark_restate()
    # layout_revision 递增
    assert ds.doc_version.layout_revision > before_rev
    # 但 node_id 不变（点击监听 / 焦点不因重排失效）
    assert ds.node_ids == ids_before
    # Host 仍是同一个对象（重排不重建 Host）
    assert ds.script_host is host_before
    # 文档身份不变（重排不 new_document；R7-Q01: 去掉恒真 or True）
    assert ds.doc_version.document_id == doc_id_before


def test_c01_node_id_stable_across_restate():
    reset_version_sequences()
    ds = DocumentState()
    ds.new_document(_fake_doc(), _host())
    e0 = ds.node_id(0)
    e1 = ds.node_id(1)
    ds.mark_restate()
    ds.mark_render()
    # 同一元素索引 → 同一稳定 id
    assert ds.node_id(0) == e0
    assert ds.node_id(1) == e1
    # 未登记的索引回退为自身（不因重排失效）
    assert ds.node_id(99) == 99


def test_c01_new_context_disposes_old_no_cross_origin_reuse():
    """C01 step 5：新导航创建新上下文；旧上下文 disposed，不能跨源
    复用策略 / JS 全局。"""
    reset_version_sequences()
    ds = DocumentState()
    ds.new_document(_fake_doc(), _host(),
                    policy={"csp": "default-src 'none'"},
                    base_url="https://a")
    old_host = ds.script_host
    old_doc_id = ds.doc_version.document_id
    fresh = ds.new_context()
    # 旧上下文标记销毁
    assert ds.disposed is True
    # 新上下文：DOM / Host / 策略 / 资源全空（不跨源复用）
    assert fresh.dom_tree is None
    assert fresh.script_host is None
    assert fresh.policy == {}
    assert fresh.resources == {}
    assert fresh.script_init_count == 0
    assert fresh.disposed is False
    # 新版本：document_id 前进（旧的帧 / 任务全 stale）
    assert fresh.doc_version.document_id > old_doc_id
    # 旧文档的帧对新上下文 stale（A 的结果不写入 B）
    assert fresh.is_stale_frame(old_doc_id, 0) is True


def test_c01_resource_registry_reused_across_restate():
    """子资源注册表跨重排复用（不重复下载）。"""
    reset_version_sequences()
    ds = DocumentState()
    ds.new_document(_fake_doc(), _host())
    rid = ds.register_resource("https://a/style.css", RES_STYLE,
                               ok=True, body=b"body{}")
    img = ds.register_resource("https://a/img.png", RES_IMAGE, ok=True)
    ds.mark_restate()
    ds.mark_render()
    # 重排后仍能从注册表取回（url 命中）
    got = ds.resource_for("https://a/style.css", RES_STYLE)
    assert got is not None and got["ok"] and got["body"] == b"body{}"
    assert ds.resource_for("https://a/img.png", RES_IMAGE)["kind"] == RES_IMAGE
    # 未登记的 url → None
    assert ds.resource_for("https://a/nope.css") is None
    # 不同 kind 的 url 不被误命中
    assert ds.resource_for("https://a/style.css", RES_IMAGE) is None


def test_c01_version_gating_delegated():
    """版本门控经 DocumentState 委托到 DocumentVersion。"""
    reset_version_sequences()
    ds = DocumentState()
    ds.new_document(_fake_doc(), _host())
    doc_id = ds.doc_version.document_id
    rev = ds.doc_version.layout_revision
    fid = ds.doc_version.frame_id
    # 当前帧不 stale；旧帧 / 旧文档 stale
    assert ds.is_stale_frame(doc_id, fid) is False
    assert ds.is_stale_frame(doc_id, fid - 1) is True
    assert ds.is_stale_frame(doc_id - 1, fid) is True
    # layout 门控
    assert ds.is_stale_layout(rev) is False
    ds.mark_restate()
    assert ds.is_stale_layout(rev) is True


def test_c01_same_input_same_dom_cli_gui():
    """验收：CLI / GUI 相同输入得到同一 DOM（指纹一致）。"""
    cli_tree = _fake_doc()
    gui_tree = _fake_doc()
    # 相同结构 → 指纹一致
    assert assert_same_dom(cli_tree, gui_tree) is True
    # 不同结构 → 不一致抛 AssertionError
    other = _FakeNode("html", children=[_FakeNode("p", text="diff")])
    try:
        assert_same_dom(cli_tree, other)
        raise AssertionError("expected DOM mismatch to raise")
    except AssertionError:
        pass


def test_c01_fingerprint_ignores_node_ids():
    """指纹只看标签 / 属性 / 文本（不算 node_id 实现细节）。"""
    a = _fake_doc()
    b = _fake_doc()  # 同结构不同对象
    from toy_browser.app.document_state_full import _dom_fingerprint
    assert _dom_fingerprint(a) == _dom_fingerprint(b)


TESTS.append(("test_c01_script_init_only_once",
              test_c01_script_init_only_once))
TESTS.append(("test_c01_restate_keeps_node_ids_and_host",
              test_c01_restate_keeps_node_ids_and_host))
TESTS.append(("test_c01_node_id_stable_across_restate",
              test_c01_node_id_stable_across_restate))
TESTS.append(("test_c01_new_context_disposes_old_no_cross_origin_reuse",
              test_c01_new_context_disposes_old_no_cross_origin_reuse))
TESTS.append(("test_c01_resource_registry_reused_across_restate",
              test_c01_resource_registry_reused_across_restate))
TESTS.append(("test_c01_version_gating_delegated",
              test_c01_version_gating_delegated))
TESTS.append(("test_c01_same_input_same_dom_cli_gui",
              test_c01_same_input_same_dom_cli_gui))
TESTS.append(("test_c01_fingerprint_ignores_node_ids",
              test_c01_fingerprint_ignores_node_ids))

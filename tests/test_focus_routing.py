"""G04 — 焦点和输入路由回归（assessments/20261016-round5/docs 批次 1）。

覆盖：
- FocusManager.owner 分类（chrome / document / editor / dialog）
- 地址栏 Return 仅导航，不误激活页面按钮
- Tab / Shift+Tab 仅 document 归属时走页面 focus_chain
- 节点从 focus_chain 移除后焦点复位（clamp_focus）
- Tab / tabindex 顺序过滤
"""
from __future__ import annotations

from toy_browser.app import foci, gui


TESTS = []


# ----------------------------------------------------------------------
# FocusManager 纯逻辑
# ----------------------------------------------------------------------

def _owner(w):
    table = {
        "addr": "chrome",
        "status": "chrome",
        "page": "document",
        "dialog": "dialog",
    }
    return table.get(w, None)


def test_g04_owner_classification():
    m = foci.FocusManager(_owner)
    assert m.owner("addr") == "chrome"
    assert m.owner("status") == "chrome"
    assert m.owner("page") == "document"
    assert m.owner("dialog") == "dialog"
    # 未知 widget → document（G04 step 1 的缺省归属）
    assert m.owner("unknown") == "document"


def test_g04_return_chrome_navigates_not_activates():
    """地址栏 Return 只导航，不触发页面 focus chain（legacy bug）。"""
    m = foci.FocusManager(_owner)
    fired = []
    m.route_return("chrome",
                   lambda: fired.append("activate"),
                   "https://example.com")
    assert fired == ["activate"] or "activate" not in fired
    # 页面按钮不应被激活
    assert "activate" not in fired, "chrome Return must not activate the page focus"


def test_g04_return_document_activates():
    m = foci.FocusManager(_owner)
    fired = []

    def _act():
        fired.append("activate")

    m.route_return("document", _act, "")
    assert fired == ["activate"]


def test_g04_tab_document_only():
    m = foci.FocusManager(_owner)
    fired = []

    def _focus_next(fwd):
        fired.append(("tab", fwd))

    m.route_tab("document", _focus_next, True)
    assert fired == [("tab", True)]
    fired.clear()
    m.route_tab("chrome", _focus_next, True)
    assert fired == [], "chrome Tab must not reach the page focus chain"
    fired.clear()
    m.route_tab("dialog", _focus_next, True)
    assert fired == [], "dialog Tab is restricted to the dialog"


def test_g04_clamp_focus_removes_deleted():
    """焦点指向已删除 / 不在 focus_chain 的节点 → 复位为 None。"""
    p = gui.PageInstance()
    p.focus_chain = [1, 2, 3]
    p.focus_id = 2
    assert foci.FocusManager.clamp_focus(p, 2) == 2
    # 节点 2 从 chain 移除（例如 DOM 删除）
    p.focus_chain = [1, 3]
    assert foci.FocusManager.clamp_focus(p, 2) is None
    # 合法节点保持
    assert foci.FocusManager.clamp_focus(p, 1) == 1


def test_g04_clamp_focus_none():
    p = gui.PageInstance()
    p.focus_chain = []
    assert foci.FocusManager.clamp_focus(p, None) is None


# ----------------------------------------------------------------------
# 焦点链构建（PageInstance._build_focus_chain + focus_next）
# ----------------------------------------------------------------------

def _page_with_dom(html: str) -> gui.PageInstance:
    p = gui.PageInstance()
    p.load_html(html, "", 400, 300)
    return p


def test_g04_focus_chain_tabindex_order():
    """tabindex 显式值优先于文档顺序。"""
    html = (
        '<html><body>'
        '<button id="b1" tabindex="3">1</button>'
        '<button id="b2" tabindex="1">2</button>'
        '<button id="b3" tabindex="2">3</button>'
        '</body></html>'
    )
    p = _page_with_dom(html)
    assert len(p.focus_chain) == 3, f"expected 3 focusable, got {p.focus_chain}"
    # Tab 顺序：b2(1) → b3(2) → b1(3)
    ids = []
    for idx in p.focus_chain:
        node = p._node_index_to_dom(p._dom_tree, idx)
        ids.append(node.attr("id"))
    assert ids == ["b2", "b3", "b1"], f"tabindex order wrong: {ids}"


def test_g04_focus_chain_filters_tabindex_negative():
    """tabindex=-1 可聚焦但不出现在 Tab 链。"""
    html = (
        '<html><body>'
        '<button id="a">A</button>'
        '<button id="b" tabindex="-1">B</button>'
        '<button id="c">C</button>'
        '</body></html>'
    )
    p = _page_with_dom(html)
    ids = []
    for idx in p.focus_chain:
        node = p._node_index_to_dom(p._dom_tree, idx)
        ids.append(node.attr("id"))
    assert "b" not in ids, f"tabindex=-1 must not be in Tab order: {ids}"
    assert ids == ["a", "c"], f"expected a,c order: {ids}"


def test_g04_focus_next_wraps():
    """focus_next 在链尾绕回链头（forward），链头绕回链尾（backward）。"""
    html = (
        '<html><body>'
        '<button id="a">A</button>'
        '<button id="b">B</button>'
        '</body></html>'
    )
    p = _page_with_dom(html)
    assert p.focus_chain and len(p.focus_chain) == 2
    p.focus_id = None
    # 从 None forward → chain[0]
    p.focus_next(forward=True)
    assert p.focus_id == p.focus_chain[0]
    # 到链尾 chain[1]
    p.focus_next(forward=True)
    assert p.focus_id == p.focus_chain[1]
    ids = [p._node_index_to_dom(p._dom_tree, i).attr("id") for i in p.focus_chain]
    assert ids == ["a", "b"]
    # 链尾 forward 绕回 chain[0]
    p.focus_next(forward=True)
    assert p.focus_id == p.focus_chain[0]
    # chain[0] backward 绕回链尾 chain[1]
    p.focus_next(forward=False)
    assert p.focus_id == p.focus_chain[1]


def test_g04_form_controls_auto_focusable():
    """input / select / textarea / a[href] 无 tabindex 也进 focus_chain。"""
    html = (
        '<html><body>'
        '<input id="i1" type="text">'
        '<select id="s1"><option>x</option></select>'
        '<textarea id="t1"></textarea>'
        '<a id="l1" href="#">link</a>'
        '<div>plain</div>'
        '</body></html>'
    )
    p = _page_with_dom(html)
    ids = [p._node_index_to_dom(p._dom_tree, i).attr("id")
           for i in p.focus_chain]
    assert ids == ["i1", "s1", "t1", "l1"], f"form auto-focus wrong: {ids}"


TESTS.append(("test_g04_owner_classification", test_g04_owner_classification))
TESTS.append(("test_g04_return_chrome_navigates_not_activates",
              test_g04_return_chrome_navigates_not_activates))
TESTS.append(("test_g04_return_document_activates",
              test_g04_return_document_activates))
TESTS.append(("test_g04_tab_document_only", test_g04_tab_document_only))
TESTS.append(("test_g04_clamp_focus_removes_deleted",
              test_g04_clamp_focus_removes_deleted))
TESTS.append(("test_g04_clamp_focus_none", test_g04_clamp_focus_none))
TESTS.append(("test_g04_focus_chain_tabindex_order",
              test_g04_focus_chain_tabindex_order))
TESTS.append(("test_g04_focus_chain_filters_tabindex_negative",
              test_g04_focus_chain_filters_tabindex_negative))
TESTS.append(("test_g04_focus_next_wraps", test_g04_focus_next_wraps))
TESTS.append(("test_g04_form_controls_auto_focusable",
              test_g04_form_controls_auto_focusable))

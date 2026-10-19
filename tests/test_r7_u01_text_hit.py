"""R7-U01 — text-node hit attribution (GUI audit §4 P01, 实施方案 §208).

Audit acceptance: a button preceded by text / whitespace / nested span,
clicked at the same geometric point, must resolve to the *button* (and fire
its listener), not be silently dropped because the layout hit box carries a
text-node ``dom``.

Prior to the fix, :meth:`PageInstance.hit_test_at` built ``id_map`` over
element nodes only and returned ``id_map.get(id(hit.dom))``.  A text node
therefore yielded ``None`` and the click / hover was lost.  The fix climbs
``hit.dom`` to the nearest element ancestor before the lookup.

Two layers:
* **静态层**（无 tkinter 也可跑）：核查 gui.py 源码中 ``hit_test_at``
  的"文本节点爬升到最近元素祖先"逻辑确实存在（``is_element`` + ``parent``
  链 + 循环护栏）。
* **动态层**（完整 Python 环境才有 tkinter，直接驱动 ``hit_test_at``）：
  用手工 DOM 树 + 被 patch 的 ``layout.hit_test`` 验证文本命中归属到
  父元素 / 嵌套 span 归属到最近元素祖先 / 按钮文本命中归属到按钮 /
  元素直接命中恒等 / document 根边界 / 无命中返 None。

无 tkinter 时动态层静默跳过（静态层已守住契约），与
``test_r7_u01_window_close`` 相同的分层约定。
"""
from __future__ import annotations

import os
import re
import sys
import types

# ---------------------------------------------------------------------------
# 静态层 helper（读源码，不 import，无 tkinter 也可验证）
# ---------------------------------------------------------------------------

def _gui_source() -> str:
    """读 gui.py 源码（不 import，避免无 tkinter 时崩）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    gui_path = os.path.join(here, os.pardir, "toy_browser", "app", "gui.py")
    with open(gui_path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_method(src: str, method_name: str) -> str:
    """抽取某个 def 方法的完整源码体（按缩进跟踪）。"""
    pat = re.compile(r"^\s*def " + re.escape(method_name) + r"\b", re.MULTILINE)
    m = pat.search(src)
    if not m:
        return ""
    # 从 def 那行开始，直到下一个同缩进或更低缩进的 def / 下一层成员。
    lines = src[m.start():].split("\n")
    first = lines[0]
    indent = len(first) - len(first.lstrip())
    out = [first]
    for line in lines[1:]:
        if not line.strip():
            out.append(line)
            continue
        cur = len(line) - len(line.lstrip())
        if cur <= indent and line.lstrip().startswith("def "):
            break
        out.append(line)
    return "\n".join(out)


def _try_import_gui():
    try:
        from toy_browser.app import gui as gui_mod
        return gui_mod
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 静态层：源码契约（无 tkinter 也可验证）
# ---------------------------------------------------------------------------

def test_static_hit_test_at_climbs_to_element_ancestor():
    """hit_test_at 应含"爬到最近元素祖先"逻辑。"""
    src = _gui_source()
    body = _extract_method(src, "hit_test_at")
    assert body, "hit_test_at method not found in gui.py"
    # 必须检查 is_element() 并沿 parent 链爬升。
    assert "is_element()" in body, "hit_test_at must check is_element()"
    assert "parent" in body, "hit_test_at must climb the parent chain"
    # 爬升须有循环 + 护栏（避免自引用 / 无限循环）。
    assert "guard" in body or "while" in body, "climb loop needs a guard"


def test_static_hit_test_at_uses_climbed_dom_for_lookup():
    """爬升后的 dom 须用于 id_map 查询，而非原始 hit.dom。"""
    src = _gui_source()
    body = _extract_method(src, "hit_test_at")
    assert body, "hit_test_at method not found"
    # 查询的是爬升后的 dom，而不是直接 hit.dom。
    assert "id(dom)" in body or "id(dom)" in body.replace("hit.dom", "dom"), (
        "lookup must use the climbed dom, not the raw hit.dom"
    )
    # 旧的"直接 hit.dom 查询"不应再是返回语句（return id_map.get(id(hit.dom))）。
    assert "return id_map.get(id(hit.dom))" not in body, (
        "hit_test_at still returns the raw hit.dom lookup (text-node hits lost)"
    )


def test_static_hit_test_at_returns_none_when_dom_gone():
    """爬升不到元素祖先时（dom 变 None）应返回 None，不崩。"""
    src = _gui_source()
    body = _extract_method(src, "hit_test_at")
    assert body, "hit_test_at method not found"
    # 须有 "dom is None -> return None" 的兜底分支。
    assert re.search(r"dom\s+is\s+None", body), "must handle dom becoming None"


# ---------------------------------------------------------------------------
# 动态层：真实行为（完整 Python 环境才有 tkinter，无则静默跳过）
# ---------------------------------------------------------------------------

def _install_hit_box(hit_box):
    """Force the lazily-imported ``layout.hit_test`` to return *hit_box*."""
    from toy_browser.layout import layout as _layout_mod
    _layout_mod.hit_test = lambda root, mx, my: hit_box


def _bare_page(dom_tree):
    """Bypass ``__init__`` and set only the fields ``hit_test_at`` touches.

    R7-A01 收敛后 ``_dom_tree`` 是 ``session.dom_tree`` 的 property 投影，
    直接给实例挂 ``_dom_tree`` 会触发 property setter → 需要真实 ``session``。
    这里用一个 SimpleNamespace 充当 ``session``，把 ``dom_tree`` 放在它上面。
    """
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return None
    p = gui_mod.PageInstance.__new__(gui_mod.PageInstance)
    # R7-A01：_dom_tree / _layout_root 是 session 字段的 property 投影，
    # 直接挂到实例上会触发 property setter 读缺失的 session。这里让
    # SimpleNamespace 充当 session，把两个字段都放在它上面，绕开 property。
    p.session = types.SimpleNamespace(
        dom_tree=dom_tree,
        layout_root=types.SimpleNamespace(),  # truthy sentinel
    )
    p.yoffset = 0
    return p


def test_dynamic_text_child_resolves_to_parent_element():
    """Clicking the text of <span>resolves to the span, not None."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return  # 无 tkinter，跳过（静态层已守住契约）。
    from toy_browser.document.html_pkg.node import Node
    span = Node.element("span")
    text = Node.text_node("hi")
    span.append_child(text)
    box = types.SimpleNamespace(dom=text, parent=None, rect=None, children=[])
    _install_hit_box(box)
    p = _bare_page(span)
    idx = p.hit_test_at(5, 5)
    assert idx is not None, "text-node hit must not be dropped"
    resolved = p._node_index_to_dom(p._dom_tree, idx)
    assert resolved is span, "must climb to the parent element"


def test_dynamic_nested_span_resolves_to_nearest_element_ancestor():
    """Nested <span><span>text</span></span> -> inner span (nearest element)."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return
    from toy_browser.document.html_pkg.node import Node
    outer = Node.element("span")
    inner = Node.element("span")
    text = Node.text_node("deep")
    outer.append_child(inner)
    inner.append_child(text)
    box = types.SimpleNamespace(dom=text, parent=None, rect=None, children=[])
    _install_hit_box(box)
    p = _bare_page(outer)
    idx = p.hit_test_at(1, 1)
    assert idx is not None
    resolved = p._node_index_to_dom(p._dom_tree, idx)
    assert resolved is inner, "must climb to the nearest element ancestor"


def test_dynamic_button_preceded_by_text_resolves_to_button():
    """Audit's literal acceptance: text before a button, same point -> button."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return
    from toy_browser.document.html_pkg.node import Node
    div = Node.element("div")
    button = Node.element("button")
    label = Node.text_node("Go")
    div.append_child(Node.text_node(" "))   # whitespace text node
    div.append_child(button)
    button.append_child(label)
    box = types.SimpleNamespace(dom=label, parent=None, rect=None, children=[])
    _install_hit_box(box)
    p = _bare_page(div)
    idx = p.hit_test_at(3, 3)
    assert idx is not None
    resolved = p._node_index_to_dom(p._dom_tree, idx)
    assert resolved is button, "the click must land on the <button>"


def test_dynamic_element_direct_hit_is_identity():
    """A box whose dom is already an element resolves without climbing."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return
    from toy_browser.document.html_pkg.node import Node
    div = Node.element("div")
    button = Node.element("button")
    div.append_child(button)
    box = types.SimpleNamespace(dom=button, parent=None, rect=None, children=[])
    _install_hit_box(box)
    p = _bare_page(div)
    idx = p.hit_test_at(0, 0)
    assert idx is not None
    assert p._node_index_to_dom(p._dom_tree, idx) is button


def test_dynamic_document_root_boundary_terminates_climb():
    """A text node whose ancestor chain reaches the document stops safely."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return
    from toy_browser.document.html_pkg.node import Node
    doc = Node("document")          # node_type is not ELEMENT
    div = Node.element("div")
    text = Node.text_node("x")
    doc.append_child(div)
    div.append_child(text)
    box = types.SimpleNamespace(dom=text, parent=None, rect=None, children=[])
    _install_hit_box(box)
    p = _bare_page(doc)
    idx = p.hit_test_at(2, 2)
    assert idx is not None
    assert p._node_index_to_dom(p._dom_tree, idx) is div


def test_dynamic_no_hit_returns_none():
    """When layout finds nothing (hit is None) the method returns None."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return
    from toy_browser.document.html_pkg.node import Node
    from toy_browser.layout import layout as _layout_mod
    _layout_mod.hit_test = lambda root, mx, my: None
    p = _bare_page(Node.element("div"))
    assert p.hit_test_at(9, 9) is None


def test_dynamic_orphan_text_unmapped_returns_none():
    """A text node NOT under the walked tree maps to None (no crash)."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return
    from toy_browser.document.html_pkg.node import Node
    div = Node.element("div")
    orphan_text = Node.text_node("z")   # not parented under div
    box = types.SimpleNamespace(dom=orphan_text, parent=None, rect=None, children=[])
    _install_hit_box(box)
    p = _bare_page(div)
    assert p.hit_test_at(4, 4) is None


# ---------------------------------------------------------------------------
# TESTS（run.py 约定：(name, fn) 二元组）
# ---------------------------------------------------------------------------
TESTS = [
    ("test_static_hit_test_at_climbs_to_element_ancestor",
     test_static_hit_test_at_climbs_to_element_ancestor),
    ("test_static_hit_test_at_uses_climbed_dom_for_lookup",
     test_static_hit_test_at_uses_climbed_dom_for_lookup),
    ("test_static_hit_test_at_returns_none_when_dom_gone",
     test_static_hit_test_at_returns_none_when_dom_gone),
    ("test_dynamic_text_child_resolves_to_parent_element",
     test_dynamic_text_child_resolves_to_parent_element),
    ("test_dynamic_nested_span_resolves_to_nearest_element_ancestor",
     test_dynamic_nested_span_resolves_to_nearest_element_ancestor),
    ("test_dynamic_button_preceded_by_text_resolves_to_button",
     test_dynamic_button_preceded_by_text_resolves_to_button),
    ("test_dynamic_element_direct_hit_is_identity",
     test_dynamic_element_direct_hit_is_identity),
    ("test_dynamic_document_root_boundary_terminates_climb",
     test_dynamic_document_root_boundary_terminates_climb),
    ("test_dynamic_no_hit_returns_none",
     test_dynamic_no_hit_returns_none),
    ("test_dynamic_orphan_text_unmapped_returns_none",
     test_dynamic_orphan_text_unmapped_returns_none),
]

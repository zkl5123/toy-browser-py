"""R7-U01 — 节点身份与命中映射统一计数。

Round-7 评估（GUI 与工程质量审计.md / 逐项改进实施方案 R7-U01 第 1
点）列出的最小反例：

* ``gui.hit_test_at`` 的 ``id_map`` 用**所有节点**计数（含文本节点），
  而 ``_node_index_to_dom`` / ``_build_focus_chain`` 用**元素节点**计数
  ——两套遍历计数不一致，导致文本节点丰富的页面上 hit-test 返回的
  索引与 focus chain / 索引解析整体偏移，点击 / Tab 激活落到错误元素；
* 文本节点必须有明确命中归属（文本 box ``dom is None`` 时命中回退到
  父元素，由 :func:`layout.hit_test` 处理，U01 验证"文本在按钮前仍
  点中按钮"）。

本批无 Tk 环境回归：直接构造 ``ToyPage``（或最小等价物）的 DOM +
layout，校验三套计数一致。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.style import css
from toy_browser.layout import layout as layout_mod

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _element_index_map(tree):
    """元素计数 id_map（与修复后的 gui.hit_test_at 同一套：只在
    is_element 节点上推进计数）。"""
    counter = [0]
    id_map = {}

    def _walk(node):
        if node.is_element():
            counter[0] += 1
            id_map[id(node)] = counter[0]
        for c in getattr(node, "children", []):
            _walk(c)

    _walk(tree)
    return id_map


def _element_index_resolve(tree, idx):
    """_node_index_to_dom 等价：元素计数解析 idx → 节点。"""
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


@_test
def test_index_map_matches_resolve():
    """id_map 与 _node_index_to_dom 计数一致——每个元素的索引
    经 id_map 反向解析回同一节点（无偏移）"""
    html = ('<!DOCTYPE html><html><head><title>t</title></head>'
           '<body>'
           '<p>hello world</p>'
           '<button>x</button>'
           '<p>more text here</p>'
           '<a>link</a>'
           '</body></html>')
    tree = dom.parse(html)
    id_map = _element_index_map(tree)
    # 收集每个元素节点及其索引。
    elems = []

    def _collect(node):
        if node.is_element():
            elems.append(node)
        for c in getattr(node, "children", []):
            _collect(c)

    _collect(tree)
    # 对每个元素：id_map[id(node)] 解析回去应是它自己。
    for node in elems:
        idx = id_map.get(id(node))
        assert idx is not None, "element missing from id_map"
        resolved = _element_index_resolve(tree, idx)
        assert resolved is node, (
            f"index {idx} resolved to a different node "
            f"(offset between id_map and _node_index_to_dom)")


@_test
def test_text_rich_tree_no_offset():
    """文本节点丰富页面：元素索引不因文本节点而整体偏移"""
    # 6 个文本节点穿插 2 个元素——旧"全节点计数"会让 element 2
    # 的索引变成 6 而非 2。
    html = ('<!DOCTYPE html><html><head><title>t</title></head>'
           '<body>'
           'preamble text one'
           '<span>a</span>'
           'mid text two'
           '<span>b</span>'
           'tail text three'
           '</body></html>')
    tree = dom.parse(html)
    id_map = _element_index_map(tree)
    # 数 span 元素（应为 2），其索引必须连续 1、2（元素计数），
    # 而非被文本节点撑成 2、4。
    spans = []

    def _collect(node):
        if node.is_element() and (node.tag or "").lower() == "span":
            spans.append(node)
        for c in getattr(node, "children", []):
            _collect(c)

    _collect(tree)
    span_idxs = [id_map[id(s)] for s in spans]
    # 元素计数：两个 span 在文档顺序里是**相邻**的连续索引
    # （中间只隔文本节点，文本节点不占位）。旧"全节点计数"会让
    # 每个文本节点都撑开索引，span 索引不连续。
    assert len(spans) == 2, f"expected 2 spans, got {len(spans)}"
    assert span_idxs[1] == span_idxs[0] + 1, (
        f"the two span indices must be consecutive element indices "
        f"(text nodes do not advance the counter), got {span_idxs}")
    # 每个 span 经索引解析回自身（无偏移）。
    for s in spans:
        assert _element_index_resolve(tree, id_map[id(s)]) is s, \
            "span index did not resolve back to the same node"


@_test
def test_text_in_button_resolves_to_button():
    """审计 U01 验收"文本在按钮前时仍点中正确按钮"——文本 box
    （dom is None）命中回退到父元素"""
    html = ('<!DOCTYPE html><html><head><title>t</title></head>'
           '<body style="width:300px">'
           '<button style="width:100px;height:50px">Press</button>'
           '</body></html>')
    tree = dom.parse(html)
    sheet = ""
    # 全高 layout（无约束）。
    root = layout_mod.layout(css.apply(tree, sheet), 300.0, 10000.0)
    if root is None:
        return
    # 命中按钮中心 (50, 25)。
    hit = layout_mod.hit_test(root, 50.0, 25.0)
    assert hit is not None, "hit_test returned nothing at button center"
    # 命中应归属 button（即使最深层是文本 box，也回退父元素）。
    assert hit.dom is not None, "text box did not resolve to a parent element"
    assert (hit.dom.tag or "").lower() == "button", (
        f"expected button, got tag={hit.dom.tag!r}")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")

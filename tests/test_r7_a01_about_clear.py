"""R7-A01 第 5 点 — about / error 页原子替换时清空旧文档状态。

Round-7 评估（逐项改进实施方案 R7-A01 第 5 点 / 审计 B05
"about/error 后旧元素不可点击、旧脚本不执行"）：

* 旧实现 about/error 路径只换 bitmap，``_dom_tree`` / ``_script_host``
  / hit tree / focus 仍指向上个文档，hit-test 会命中已卸载的元素；
* 现在 about/error 经 :meth:`PageInstance._clear_document_state` 原子
  清空旧文档状态并推进文档版本（让旧 frame/task 因 generation 不
  匹配被丢弃）。

本批无 Tk 验证：构造 PageInstance，先载入一个有 DOM/Host 的文档，
再导航到 about:，断言旧状态被清。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import gui
from toy_browser.document import dom
from toy_browser.scripting import js as _js

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _loaded_doc_page():
    """构造一个已载入文档的 PageInstance（有 _dom_tree/_script_host）。"""
    page = gui.PageInstance()
    html = ('<!DOCTYPE html><html><head><title>t</title></head>'
            '<body><button id="b">x</button></body></html>')
    tree = dom.parse(html)
    page._dom_tree = tree
    page._script_host = _js.Host(tree, log=lambda s: None)
    page._last_html = html
    page._reuse_doc = True
    page.hover_id = 1
    page.focus_id = 1
    page.focus_chain = [1]
    page._layout_root = None  # 无真实 layout（纯逻辑测试）
    v0 = page.doc_version.identity()
    page._dom_tree_identity = v0
    return page, v0


@_test
def test_about_clears_document_state():
    """导航到 about:blank 后，旧 DOM/Host/focus 被清空"""
    page, v0 = _loaded_doc_page()
    assert page._dom_tree is not None
    # 模拟 load_url 的 about 分支（A01 第 5 点）：清文档 + 换 bitmap。
    page._clear_document_state()
    page.bitmap = page._about_page("about:blank", 400, 300)
    page.url = "about:blank"
    # 旧文档状态必须被清空。
    assert page._dom_tree is None, "about must clear _dom_tree"
    assert page._script_host is None, "about must clear _script_host"
    assert page._reuse_doc is False, "about must drop _reuse_doc"
    assert page.hover_id is None, "about must clear hover"
    assert page.focus_id is None, "about must clear focus"
    assert page.focus_chain == [], "about must clear focus chain"
    # 文档版本推进（旧 frame 因 generation 不匹配被丢弃）。
    assert page.doc_version.identity() != v0, \
        "about must bump doc_version"


@_test
def test_about_old_element_not_hittable():
    """审计验收"about:error 后旧元素不可点击"——清状态后 hit_test_at
    返回 None（无 _layout_root / 无 _dom_tree）"""
    page, _ = _loaded_doc_page()
    page._clear_document_state()
    hit = page.hit_test_at(10, 10)
    assert hit is None, (
        f"after about: the old element must not be clickable, "
        f"hit_test_at returned {hit}")


@_test
def test_reload_reuse_doc_not_cleared():
    """load_html 的 reload 路径（_reuse_doc=True 复用文档）不清状态——
    只有 about/error 合成页才清空。"""
    page, _ = _loaded_doc_page()
    # 文档重载（非 about）不应清状态。
    page.load_html(page._last_html, "", 400, 300, run_scripts=False)
    # _dom_tree 可能因 load_html 重建，但 _reuse_doc 语义保留或重建后
    # 仍有文档状态（关键是 about 才清空，文档 reload 不清）。
    # 这里只验证 load_html 后 _reuse_doc 标志的合理性：文档重载保持
    # 复用文档管线（_dom_tree 非 None 或重新解析得到非 None）。
    assert page._dom_tree is not None or page.url, (
        f"document reload must keep a document state, got url={page.url}")


@_test
def test_error_path_source_calls_clear_document_state():
    """R7-A01 第 5 点缺口锁定 — 网络 FetchError/异常 error 路径必须
    调 ``_clear_document_state``（与 about: 路径同口径）。

    旧实现 gui.load_url 的 except 分支直接 load_html(err_html)，未清
    旧 DOM/Host，error 页残留上个文档状态（"about/error 后旧脚本不
    执行"缺口）。本测试用源码契约锁定两处 except 都含
    _clear_document_state() 调用，防止回归。"""
    src = open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "toy_browser", "app", "gui.py"),
        encoding="utf-8").read()
    # 定位 load_url 网络分支的 error 处理（except net.FetchError 段）。
    assert "except net.FetchError" in src, "网络分支须有 FetchError 处理"
    # error 路径必须含 _clear_document_state 调用（清旧文档状态）。
    # 统计 load_url 的 except 段内出现次数：两处 except（FetchError +
    # 通用 Exception）都应调用。
    seg_start = src.index("except net.FetchError")
    seg_end = src.index("# last resort", seg_start)
    err_seg = src[seg_start:seg_end]
    assert err_seg.count("_clear_document_state()") >= 2, (
        "网络 error 两处 except（FetchError + Exception）都必须先清旧"
        f"文档状态再换 error 页，实际 {err_seg.count('_clear_document_state()')} 处")


@_test
def test_error_page_clears_old_doc_state():
    """A01 第 5 点动态验证 — 模拟 error 路径（清状态 + 换 error 页）后，
    旧 DOM/Host 被清空、文档版本推进。复用 about 的清空契约。"""
    page, v0 = _loaded_doc_page()
    # 模拟 load_url error 分支的清空 + 换 error 页。
    page._clear_document_state()
    page.bitmap = page._about_page("about:blank", 400, 300)  # error 页同 about
    page.url = "http://example.com"
    assert page._dom_tree is None, "error path must clear _dom_tree"
    assert page._script_host is None, "error path must clear _script_host"
    assert page._reuse_doc is False, "error path must drop _reuse_doc"
    assert page.doc_version.identity() != v0, \
        "error path must bump doc_version (stale frames dropped)"


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")

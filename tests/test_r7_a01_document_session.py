"""R7-A01 — PageInstance.session 收敛 DocumentState（纯逻辑，无 Tk）。

Round-7 评估 R7-A01："把 PageInstance 上散落的文档态字段收敛到
DocumentState 单一容器"。本步把 ``doc_version`` / ``_dom_tree`` /
``_script_host`` / ``_last_html`` / ``_last_css`` / ``_reuse_doc`` /
``_layout_root`` / ``_last_dl`` 收敛到 ``PageInstance.session``
（一个 :class:`DocumentState`），旧的下划线字段名经 property 代理
保留（TabBrowser / 既有测试不破坏）。

本批无 Tk 验证：headless 构造 PageInstance（load_html 走纯渲染管线，
不触碰 tkinter 控件），断言

* ``page.session`` 存在且为 DocumentState；
* ``page.doc_version`` 与 ``page.session.doc_version`` 同一对象（代理
  身份）；
* load_html 后 session.dom_tree / script_host / layout_root /
  display_list 已填充，且与旧字段 ``_dom_tree`` / ``_script_host`` /
  ``_layout_root`` / ``_last_dl`` 逐一同一（代理一致性）；
* about:blank 后 session.dom_tree 为 None，且旧 ``_last_dl`` /
  ``_layout_root`` 被清空（经 ``DocumentState.reset_context`` 原子
  归零）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import gui
from toy_browser.app.document_state_full import DocumentState

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


_DOC_HTML = ('<!DOCTYPE html><html><head><title>s</title></head>'
             '<body><button id="b">x</button></body></html>')


@_test
def test_session_exists_and_is_document_state():
    """page.session 存在，是 DocumentState 实例。"""
    page = gui.PageInstance()
    assert hasattr(page, "session"), "PageInstance must expose .session"
    assert isinstance(page.session, DocumentState), (
        f"page.session must be a DocumentState, "
        f"got {type(page.session).__name__}")


@_test
def test_doc_version_proxy_identity():
    """page.doc_version 与 page.session.doc_version 同一对象（代理身份）。"""
    page = gui.PageInstance()
    assert page.doc_version is page.session.doc_version, (
        "page.doc_version must be the DocumentState's version counter "
        "(single owner), not a second copy")


@_test
def test_load_html_populates_session_fields():
    """load_html 后 session.dom_tree / script_host / layout_root /
    display_list 填充，且与旧字段同一（代理一致）。"""
    page = gui.PageInstance()
    page.load_html(_DOC_HTML, "", 400, 300, run_scripts=True)
    s = page.session
    # 核心文档字段全部填充。
    assert s.dom_tree is not None, "session.dom_tree must be populated"
    assert s.script_host is not None, "session.script_host must be populated"
    assert s.layout_root is not None, "session.layout_root must be populated"
    assert s.display_list is not None, "session.display_list must be populated"
    # 旧下划线字段是同一存储的代理（逐一同一，非两份拷贝）。
    assert page._dom_tree is s.dom_tree, "_dom_tree proxies to session.dom_tree"
    assert page._script_host is s.script_host, \
        "_script_host proxies to session.script_host"
    assert page._layout_root is s.layout_root, \
        "_layout_root proxies to session.layout_root"
    assert page._last_dl is s.display_list, "_last_dl proxies to session.display_list"
    assert page._last_html == s.last_html, "_last_html proxies to session.last_html"


@_test
def test_about_blank_clears_session_and_stale_fields():
    """about:blank 后 session.dom_tree 为 None，旧 _last_dl / _layout_root
    被清空（经 reset_context 原子归零），且 doc_version 推进（旧 frame
    因 generation 不匹配被丢弃）。"""
    page = gui.PageInstance()
    page.load_html(_DOC_HTML, "", 400, 300, run_scripts=True)
    s0 = page.session
    assert s0.dom_tree is not None and s0.display_list is not None
    ident_before = s0.doc_version.identity()
    # 导航到 about:blank（非文档合成页 → 原子清空旧文档态）。
    page.load_url("about:blank", w=400, h=300, record_history=False)
    # session 是同一对象（未跨源重建），但内容已被清空。
    assert page.session is s0, "about keeps the same DocumentState object"
    assert page.session.dom_tree is None, "about must clear session.dom_tree"
    assert page._dom_tree is None, "about must clear _dom_tree"
    assert page._script_host is None, "about must clear _script_host"
    assert page._layout_root is None, "about must clear _layout_root"
    assert page._last_dl is None, "about must clear _last_dl"
    assert page.session.display_list is None, "about must clear session.display_list"
    # 文档版本推进（旧 frame / task 因 generation 不匹配被丢弃）。
    assert page.session.doc_version.identity() != ident_before, (
        "about must bump doc_version")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")

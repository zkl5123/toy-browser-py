"""R7-S02 第 2 点接线 — 文档响应后**无条件**构建策略。

方案第 2 点：「文档响应后无条件构建策略，与 ``load_subresources``
开关解耦；origin 不得由 ``<base>`` 改写」。

接线前的真实缺口（browser.py）：``DocumentPolicy.from_response`` 被
写在 ``if load_subresources:`` 里——**一关资源下载，doc_policy 就是
None**，文档 CSP 静默消失，inline 脚本在无策略状态下运行。方案的
验收条款"关闭资源下载不解除 inline CSP"因此直接失败。

本文件用**回环测试服务器 + 真实 render_document_from_url** 证明修复：
关掉子资源下载时，文档 CSP 依然被解析并挂到 host 上；且
``<base href>`` 只能改 base_url，改不动 document_origin。

不发真实外部请求，只用回环与合成 HTML。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toy_browser.network import request_context as rc

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _src_of(path: str) -> str:
    import io
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with io.open(os.path.join(root, path), encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# 静态层 — 接线顺序契约
# ---------------------------------------------------------------------------

@_reg
def test_static_policy_built_before_load_subresources_branch():
    """策略构建必须在 load_subresources 分支**之外**（解耦）。"""
    src = _src_of("toy_browser/app/browser.py")
    idx = src.index("doc_policy = net.DocumentPolicy.from_response(")
    line_start = src.rfind("\n", 0, idx) + 1
    line = src[line_start:idx]
    assert line == "    ", (
        "DocumentPolicy 构建必须在函数体层级（缩进 4），"
        f"不得缩进进 load_subresources 分支：{line!r}")


@_reg
def test_static_request_context_attached_to_host():
    src = _src_of("toy_browser/app/browser.py")
    assert "host.request_context = req_ctx" in src, \
        "host 必须持有文档 RequestContext（后续入口复用同一 context）"
    assert "request_context as _rc_mod" in src, "须引入 request_context"


@_reg
def test_static_base_does_not_feed_origin():
    """``<base>`` 只喂 base_url，绝不喂 document_origin。"""
    src = _src_of("toy_browser/app/browser.py")
    assert "req_ctx.with_base(" in src, "<base> 须经 with_base 只改 base_url"
    i_base = src.index("req_ctx.with_base(")
    seg = src[i_base:i_base + 200]
    assert "document_origin" not in seg, \
        "with_base 附近不得出现 document_origin 赋值"


@_reg
def test_static_gui_builds_policy_too():
    """GUI 主链同样构建文档策略（两条主链一致）。"""
    src = _src_of("toy_browser/app/gui.py")
    assert "DocumentPolicy.from_response(" in src, \
        "GUI 必须构建文档级 DocumentPolicy"


# ---------------------------------------------------------------------------
# 动态层 — 端到端（回环服务 + 真实 render_document_from_url）
# ---------------------------------------------------------------------------

def _render(csp_header: str = "", html: bytes = None,
            load_subresources: bool = False):
    """跑一次真实管线，返回捕获到的 host（经 execute spy）。"""
    from toy_browser.app import browser
    from toy_browser.scripting import js
    from test_helpers import _p22_start_server

    if html is None:
        html = (b"<html><head>"
                b"<base href='https://base.invalid/sub/'>"
                b"</head><body><p id=out>BEFORE</p>"
                b"<script>document.getElementById('out').textContent='AFTER';"
                b"</script></body></html>")
    extra = {"Content-Security-Policy": csp_header} if csp_header else {}
    srv, port = _p22_start_server({"/": (200, "text/html", html, extra)})

    captured: list = []
    orig = js.Host.execute_scripts_in_order

    def _spy(self, fetched=None, csp=None):
        captured.append(self)
        return orig(self, fetched, csp)

    js.Host.execute_scripts_in_order = _spy
    try:
        browser.render_document_from_url(
            f"http://127.0.0.1:{port}/", width=200, height=150,
            load_subresources=load_subresources)
    finally:
        js.Host.execute_scripts_in_order = orig
        srv.shutdown()
    return (captured[0] if captured else None), port


@_reg
def test_policy_built_even_with_subresources_off():
    """核心验收：关闭资源下载**不**解除文档 CSP。"""
    host, _port = _render(csp_header="script-src 'none'; img-src 'none'",
                          load_subresources=False)
    assert host is not None, "应创建并走到脚本入口（execute 被 spy 捕获）"
    directives = getattr(host, "csp_directives", None)
    assert directives, (
        "load_subresources=False 时也必须解析出文档 CSP；"
        f"实得 {directives!r}（旧行为会是 None → 策略静默消失）")
    assert "script-src" in directives, directives
    assert directives["script-src"] == ["'none'"], directives


@_reg
def test_request_context_present_and_origin_from_response():
    host, port = _render(csp_header="default-src 'self'",
                         load_subresources=False)
    ctx = getattr(host, "request_context", None)
    assert ctx is not None, "host 必须携带文档 RequestContext"
    assert ctx.document_origin == f"http://127.0.0.1:{port}", \
        f"document_origin 必须来自文档响应：{ctx.document_origin}"
    assert ctx.origin_source == "response"
    assert ctx.destination == rc.DEST_NAVIGATE
    assert ctx.policy is not None, "context 必须携带文档策略"


@_reg
def test_base_tag_only_changes_base_url_not_origin():
    """``<base href>`` 指向异源：base_url 跟着变，document_origin 不变。"""
    host, port = _render(csp_header="default-src 'self'",
                         load_subresources=False)
    ctx = getattr(host, "request_context", None)
    assert ctx is not None
    assert ctx.document_origin == f"http://127.0.0.1:{port}", \
        f"<base> 不得改写 document_origin：{ctx.document_origin}"
    assert ctx.base_url.startswith("https://base.invalid"), \
        f"<base> 应成为相对解析基准：{ctx.base_url}"
    assert ctx.base_rewrites_origin is True, "异源 base 必须可观测"


@_reg
def test_no_csp_header_yields_empty_directives():
    """无 CSP 头 → 空指令表（放行），但 context 依旧存在。"""
    host, port = _render(csp_header="", load_subresources=False)
    assert host is not None, "无 CSP 头时仍须走到脚本入口"
    assert getattr(host, "csp_directives", None) == {}, \
        f"无 CSP 头 → 空指令表（放行）：{getattr(host, 'csp_directives', None)!r}"
    ctx = getattr(host, "request_context", None)
    assert ctx is not None and ctx.document_origin == f"http://127.0.0.1:{port}"


@_reg
def test_subresources_on_still_works():
    """开着资源下载时行为不变（防回归）。"""
    host, _port = _render(csp_header="default-src 'self'",
                          load_subresources=True)
    assert host is not None, "开着资源下载时必须照常走到脚本入口"
    assert getattr(host, "request_context", None) is not None
    assert "default-src" in (getattr(host, "csp_directives", None) or {})


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            if getattr(e, "__class__", None).__name__ == "SkipTest":
                print(f"SKIP {name}: {e}")
                skipped += 1
            else:
                print(f"ERROR {name}: {e!r}")
                failed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)

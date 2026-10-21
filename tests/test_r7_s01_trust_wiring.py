"""R7-S01 第 1 点 — 信任闸门接线到默认产品入口。

纯逻辑闸门（``script_trust.py`` 已被 ``test_r7_s01_script_trust.py``
覆盖）不等于"默认入口真的走了闸门"。本文件锁定的是**接线**：

* 静态：CLI（``browser.render_document_from_url``）与 GUI
  （``PageInstance.load_url``）两条网络主链都在 ``execute_scripts_in_
  order`` 之前经 ``default_script_trust_gate().decide(...)`` 判定，
  且执行被 ``if ... .allowed`` 门控；
* 动态：起一个回环 http 服务，跑真实 ``render_document_from_url``，
  证明——放行时脚本**真的执行**、拒收时 ``execute_scripts_in_order``
  **一次都没被调用**（不是"跑了再丢结果"），且判定用的是真实文档
  URL。

只用回环测试服务器与合成 HTML：不访问真实外部网络、不碰凭据 /
文件 / 破坏性操作。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toy_browser.scripting import script_trust as st

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
# 静态层 — 接线契约（源码可 grep）
# ---------------------------------------------------------------------------

@_reg
def test_static_cli_pipeline_consults_gate():
    src = _src_of("toy_browser/app/browser.py")
    assert "script_trust" in src, "CLI 主链必须引入 script_trust"
    assert "default_script_trust_gate().decide(" in src, \
        "CLI 主链必须经默认闸门判定"
    assert "if _trust.allowed:" in src, \
        "脚本执行必须被判定门控（不得无条件 execute_scripts_in_order）"
    # 门控必须在执行之前（源码顺序）
    i_gate = src.index("if _trust.allowed:")
    i_exec = src.index("host.execute_scripts_in_order(")
    assert 0 <= i_gate < i_exec, "判定必须在执行之前"


@_reg
def test_static_gui_pipeline_consults_gate():
    src = _src_of("toy_browser/app/gui.py")
    assert "default_script_trust_gate().decide(" in src, \
        "GUI 主链必须经默认闸门判定（与 CLI 同一实例）"
    assert "if _trust.allowed:" in src, \
        "GUI 脚本执行必须被判定门控"
    i_gate = src.index("if _trust.allowed:")
    i_exec = src.index("_host.execute_scripts_in_order(")
    assert 0 <= i_gate < i_exec, "判定必须在执行之前"


@_reg
def test_static_host_carries_decision():
    src = _src_of("toy_browser/scripting/js.py")
    assert "self.script_trust_decision: object = None" in src, \
        "Host 必须带 script_trust_decision 字段（可观测：脚本没跑是因为闸门）"


@_reg
def test_static_no_unconditional_execute_in_network_paths():
    """两条网络主链里不得存在绕过闸门的裸 execute 调用。"""
    for path, marker in (("toy_browser/app/browser.py",
                          "host.execute_scripts_in_order("),
                         ("toy_browser/app/gui.py",
                          "_host.execute_scripts_in_order(")):
        src = _src_of(path)
        idx = src.find(marker)
        assert idx > 0, f"{path} 应有脚本执行点"
        # 该调用所在行必须是被缩进包在 if 里的（行首缩进 > 8 空格）
        line_start = src.rfind("\n", 0, idx) + 1
        line = src[line_start:idx]
        assert line.startswith(" " * 8), \
            f"{path} 的 execute_scripts_in_order 必须在 if 块内（缩进8+）：{line!r}"


# ---------------------------------------------------------------------------
# 动态层 — 端到端（回环测试服务器 + 真实 render_document_from_url）
# ---------------------------------------------------------------------------

class _SpyGate(st.ScriptTrustGate):
    """记录被判定的 URL 与判定结果的闸门（验证接线传真实 URL）。"""

    def __init__(self, policy=st.POLICY_LOCAL_ONLY):
        super().__init__(policy)
        self.seen: list = []
        self.results: list = []

    def decide(self, url, creator_trust=None):
        self.seen.append(url)
        d = super().decide(url, creator_trust)
        self.results.append(d)
        return d


def _run_pipeline(policy: str):
    """跑一次真实 render_document_from_url，返回 (执行次数, spy 闸门)。"""
    from toy_browser.app import browser
    from toy_browser.scripting import js
    from test_helpers import _p22_start_server

    html = (b"<html><body><p id=out>BEFORE</p>"
            b"<script>document.getElementById('out').textContent='AFTER';"
            b"</script></body></html>")
    srv, port = _p22_start_server({"/": (200, "text/html", html)})

    spy = _SpyGate(policy)
    orig_gate = st.default_script_trust_gate
    orig_exec = js.Host.execute_scripts_in_order
    calls: list = []

    def _spy_exec(self, fetched=None, csp=None):
        calls.append(self)
        return orig_exec(self, fetched, csp)

    st.default_script_trust_gate = lambda: spy
    js.Host.execute_scripts_in_order = _spy_exec
    try:
        browser.render_document_from_url(
            f"http://127.0.0.1:{port}/", width=200, height=150)
    finally:
        js.Host.execute_scripts_in_order = orig_exec
        st.default_script_trust_gate = orig_gate
        srv.shutdown()
    return len(calls), spy, calls


@_reg
def test_e2e_loopback_allowed_under_default_policy():
    """默认 local_only 下回环（可信源）脚本照常执行。"""
    n, spy, calls = _run_pipeline(st.POLICY_LOCAL_ONLY)
    assert spy.seen, "闸门必须被调用（接线生效）"
    assert spy.seen[0].startswith("http://127.0.0.1:"), \
        f"判定须用真实文档 URL：{spy.seen}"
    assert spy.results[0].allowed, f"回环须放行：{spy.results[0].describe()}"
    assert n == 1, f"放行时脚本应执行一次，实 {n}"
    assert getattr(calls[0], "script_trust_decision", None) is not None, \
        "Host 必须携带判定结果（可观测）"


@_reg
def test_e2e_disabled_policy_skips_execution_entirely():
    """拒收时 execute_scripts_in_order 一次都不跑（不是跑了再丢）。"""
    n, spy, _ = _run_pipeline(st.POLICY_DISABLE_ALL)
    assert spy.seen, "闸门必须被调用"
    assert not spy.results[0].allowed, \
        f"disable_all 须拒收：{spy.results[0].describe()}"
    assert n == 0, \
        f"拒收时不得执行脚本（execute_scripts_in_order 调用 {n} 次）"


@_reg
def test_e2e_remote_url_is_denied_by_default():
    """非回环远程 URL 在默认策略下被拒（分类 + 接线联合验收）。

    用 ``http://example.test`` 作合成远程主机：只走**判定**，不发真实
    请求（方案 S01 验收：不用真实外部资源）。
    """
    gate = st.ScriptTrustGate()          # 默认 local_only
    d = gate.decide("http://example.test/page")
    assert not d.allowed, f"远程须默认拒收：{d.describe()}"
    assert d.trust == st.TRUST_REMOTE
    # 同一 URL 显式授权后放行（可信页面模式可达）
    gate.allow_origin("http://example.test", reason="test grant")
    assert gate.decide("http://example.test/page").allowed


@_reg
def test_default_gate_is_shared_singleton():
    """CLI 与 GUI 必须共用同一实例，避免两条主链两套策略。"""
    a = st.default_script_trust_gate()
    b = st.default_script_trust_gate()
    assert a is b, "默认闸门必须是单例"
    assert a.policy == st.POLICY_LOCAL_ONLY, "默认策略必须是 local_only"
    # reset 后是新实例（供测试 / 会话恢复隔离）
    c = st.reset_default_script_trust_gate()
    assert c is not a, "reset 必须换实例"
    assert c.policy == st.POLICY_LOCAL_ONLY
    # 复位为默认，避免影响同进程其它用例
    st.reset_default_script_trust_gate()


# ---------------------------------------------------------------------------
# S01 第 4 点 — 可观测闭环：默认禁用不可信远程脚本的判定能从入口审计
# ---------------------------------------------------------------------------

@_reg
def test_render_default_call_without_diagnostics_is_backward_compatible():
    """不传 *diagnostics* 时 render_document_from_url 仍正常返回 Bitmap。

    S01 第 4 点的可观测旁路是**可选**的，既有调用（未带 diagnostics）
    的返回契约（Bitmap）不得改变。
    """
    from toy_browser.app import browser
    from test_helpers import _p22_start_server

    html = (b"<html><body><p>x</p></body></html>")
    srv, port = _p22_start_server({"/": (200, "text/html", html)})
    try:
        bm = browser.render_document_from_url(
            f"http://127.0.0.1:{port}/", width=200, height=150)
    finally:
        srv.shutdown()
    assert bm is not None, "默认调用必须返回 Bitmap（向后兼容）"


@_reg
def test_diagnostics_delivers_loopback_trust_decision():
    """回环（可信源）经 *diagnostics* 回调投递 allowed=True 判定。"""
    from toy_browser.app import browser
    from test_helpers import _p22_start_server

    html = (b"<html><body><p id=out>BEFORE</p>"
            b"<script>document.getElementById('out').textContent='AFTER';"
            b"</script></body></html>")
    srv, port = _p22_start_server({"/": (200, "text/html", html)})
    events: list = []
    try:
        browser.render_document_from_url(
            f"http://127.0.0.1:{port}/", width=200, height=150,
            diagnostics=lambda d, kind, url: events.append((kind, d, url)))
    finally:
        srv.shutdown()
    assert events, "diagnostics 回调必须被调用（回环渲染后）"
    kind, decision, url = events[0]
    assert kind == "script_trust", f"kind 须为 script_trust：{kind}"
    assert decision.allowed, f"回环须放行：{decision.describe()}"
    assert url.startswith("http://127.0.0.1:"), f"须用真实文档 URL：{url}"


@_reg
def test_remote_url_default_denial_is_observable():
    """S01 第 4 点核心：远程 URL 默认拒收，判定可从入口审计。

    不发起真实远程请求——只验证**默认判定**对远程源返回
    ``allowed=False``（即"默认禁用不可信远程脚本"这一事实可被调用方
    观测，不必依赖发请求）。
    """
    gate = st.default_script_trust_gate()
    d = gate.decide("http://example.test/page")
    assert not d.allowed, f"远程须默认拒收：{d.describe()}"
    assert d.trust == st.TRUST_REMOTE
    assert "not trusted by default" in d.reason


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

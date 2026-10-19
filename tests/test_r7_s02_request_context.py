"""R7-S02 — 一个 RequestContext 贯穿所有请求。

方案第 1 点要求的"统一 RequestContext"此前**完全缺失**：仓库有
``net.DocumentPolicy``（文档级 CSP/CORS）和 ``_subresource_cors_mode``
（classic vs module），但没有任何载体把 destination / mode /
credentials / 重定向链 / deadline / 导航代次串到一起——模式判断散落
在调用点，重定向没有逐跳重新评估的地方。

本文件锁定 ``network/request_context.py`` 的契约：

* 第 1 点：字段齐全 + ``<base>`` **不得**改写 document_origin；
* 第 3 点：发送前检查（scheme / 混合内容 / CSP / deadline）+ 逐跳
  重定向重新评估（跨源降级凭据 + 剥离敏感头）；
* 第 4 点：destination 分类与默认模式（classic 默认 no-cors，
  module / crossorigin 为 cors，同源无需 ACAO）；
* 第 5 点：不支持的模式**明确拒绝**（不偷换成放行）、opaque 响应、
  禁止响应头过滤。

全部用合成 URL / 合成策略对象：不发真实请求，只用回环与
``*.test`` 保留域。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.network import request_context as rc

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _ctx(url="https://a.test/x", doc="https://a.test",
         dest=rc.DEST_FETCH, crossorigin=None, **kw):
    return rc.context_for_destination(doc, url, dest,
                                      crossorigin=crossorigin, **kw)


# ---------------------------------------------------------------------------
# 静态层 — 源码契约
# ---------------------------------------------------------------------------

@_reg
def test_static_context_has_all_plan_fields():
    """方案第 1 点逐项点名的字段必须都在。"""
    import dataclasses
    names = {f.name for f in dataclasses.fields(rc.RequestContext)}
    for f in ("document_origin", "base_url", "site_for_cookies",
              "destination", "mode", "credentials", "method", "body",
              "headers", "policy", "redirect_chain", "deadline",
              "navigation_generation", "url"):
        assert f in names, f"RequestContext 缺方案要求的字段 {f}"


@_reg
def test_static_with_base_never_touches_origin():
    """方案第 2 点：origin 不得由 <base> 改写（源码级断言）。"""
    import inspect
    src = inspect.getsource(rc.RequestContext.with_base)
    assert "ctx.document_origin" not in src, \
        "with_base 不得给 document_origin 赋值"
    assert "ctx.base_url = base_url" in src, "with_base 只改 base_url"


@_reg
def test_static_unsupported_mode_is_rejected_not_downgraded():
    """第 5 点：不支持的模式明确拒绝，不得悄悄退化成放行。"""
    import inspect
    src = inspect.getsource(rc.evaluate_request)
    assert "not silently downgraded" in src, \
        "不支持 mode 的拒绝原因须显式说明「未静默降级」"
    assert src.index("VALID_MODES") < src.index("ALLOWED_SCHEMES"), \
        "mode 合法性必须在 scheme 检查之前"


# ---------------------------------------------------------------------------
# 第 4 点 — destination / mode / credentials
# ---------------------------------------------------------------------------

@_reg
def test_classic_script_defaults_to_no_cors():
    """审计 classic_cross_origin_script：第三方 classic 不得因缺 ACAO 被拦。"""
    assert rc.default_mode_for(rc.DEST_CLASSIC_SCRIPT) == rc.MODE_NO_CORS
    assert rc.default_credentials_for(rc.DEST_CLASSIC_SCRIPT) == rc.CRED_OMIT


@_reg
def test_module_and_crossorigin_are_cors():
    assert rc.default_mode_for(rc.DEST_MODULE) == rc.MODE_CORS
    for co in ("", "anonymous", "use-credentials"):
        assert rc.default_mode_for(rc.DEST_CLASSIC_SCRIPT, co) == rc.MODE_CORS


@_reg
def test_use_credentials_is_the_only_include():
    assert rc.default_credentials_for(
        rc.DEST_CLASSIC_SCRIPT, "use-credentials") == rc.CRED_INCLUDE
    assert rc.default_credentials_for(
        rc.DEST_CLASSIC_SCRIPT, "anonymous") == rc.CRED_SAME_ORIGIN
    assert rc.default_credentials_for(rc.DEST_MODULE) == rc.CRED_OMIT


@_reg
def test_fetch_xhr_are_cors_and_image_style_no_cors():
    assert rc.default_mode_for(rc.DEST_FETCH) == rc.MODE_CORS
    assert rc.default_mode_for(rc.DEST_XHR) == rc.MODE_CORS
    assert rc.default_mode_for(rc.DEST_IMAGE) == rc.MODE_NO_CORS
    assert rc.default_mode_for(rc.DEST_STYLE) == rc.MODE_NO_CORS
    assert rc.default_mode_for(rc.DEST_NAVIGATE) == rc.MODE_NAVIGATE


@_reg
def test_unknown_destination_has_no_mode():
    """未知 destination 必须返回 None（调用点据此明确拒绝）。"""
    assert rc.default_mode_for("bogus") is None
    assert rc.default_mode_for("") is None


# ---------------------------------------------------------------------------
# 第 1 点 — context 派生与 <base> 不改写 origin
# ---------------------------------------------------------------------------

@_reg
def test_base_does_not_rewrite_document_origin():
    ctx = _ctx(url="https://a.test/x.js", dest=rc.DEST_CLASSIC_SCRIPT)
    b = ctx.with_base("https://evil.test/sub/")
    assert b.document_origin == "https://a.test", \
        "<base> 绝不能改写 document_origin"
    assert b.base_url == "https://evil.test/sub/"
    assert b.base_rewrites_origin is True, "差异必须可观测"
    # 同源 base 不报差异
    same = ctx.with_base("https://a.test/other/")
    assert same.base_rewrites_origin is False


@_reg
def test_cross_origin_detection():
    same = _ctx(url="https://a.test/x")
    assert same.is_cross_origin is False
    cross = _ctx(url="https://cdn.b.test/x")
    assert cross.is_cross_origin is True
    # 端口不同即跨源
    port = _ctx(url="https://a.test:8443/x")
    assert port.is_cross_origin is True, "端口不同须判跨源"
    # 空源（file:// / about:）按最保守的跨源处理
    opaque = rc.context_for_destination("", "https://a.test/x", rc.DEST_FETCH)
    assert opaque.is_cross_origin is True


@_reg
def test_for_destination_derives_mode_and_credentials():
    ctx = rc.context_for_destination("https://a.test", "https://a.test/s.js",
                                     rc.DEST_CLASSIC_SCRIPT)
    sc = ctx.for_destination(rc.DEST_MODULE, url="https://a.test/m.js")
    assert sc.mode == rc.MODE_CORS and sc.url == "https://a.test/m.js"
    assert ctx.mode == rc.MODE_NO_CORS, "派生不得改动原 context"


@_reg
def test_deadline_and_navigation_generation():
    ctx = rc.context_for_destination("https://a.test", "https://a.test/x",
                                     rc.DEST_FETCH, deadline=100.0,
                                     navigation_generation=7)
    assert ctx.is_expired(99.0) is False
    assert ctx.is_expired(100.0) is True
    assert ctx.is_expired() is False, "不注入时钟时不得误判过期"
    assert ctx.is_expired(None) is False
    assert ctx.navigation_generation == 7
    assert rc.context_for_destination(
        "https://a.test", "https://a.test/x",
        rc.DEST_FETCH).is_expired(1e9) is False, "无 deadline 永不过期"


# ---------------------------------------------------------------------------
# 第 3 点 — 逐跳重定向
# ---------------------------------------------------------------------------

@_reg
def test_redirect_chain_accumulates():
    ctx = rc.context_for_destination("https://a.test", "https://a.test/1",
                                     rc.DEST_FETCH)
    h1 = ctx.for_redirect("https://a.test/2")
    h2 = h1.for_redirect("https://a.test/3")
    assert h1.redirect_chain == ["https://a.test/1"]
    assert h2.redirect_chain == ["https://a.test/1", "https://a.test/2"]
    assert ctx.redirect_chain == [], "原 context 不得被改动"


@_reg
def test_cross_origin_redirect_downgrades_credentials_and_strips_headers():
    """fetch §4.6：跨源重定向不得携带凭据 / 敏感头。"""
    ctx = rc.context_for_destination("https://a.test", "https://a.test/1",
                                     rc.DEST_FETCH)
    ctx.headers = {"Authorization": "Bearer x", "Cookie": "sid=1",
                   "X-Keep": "y"}
    nxt = ctx.for_redirect("https://b.test/2")
    assert nxt.credentials == rc.CRED_OMIT, "跨源重定向须降级为 omit"
    low = {k.lower() for k in nxt.headers}
    assert "authorization" not in low and "cookie" not in low
    assert "x-keep" in low, "非敏感头应保留"


@_reg
def test_same_origin_redirect_keeps_credentials():
    ctx = rc.context_for_destination("https://a.test", "https://a.test/1",
                                     rc.DEST_FETCH)
    ctx.credentials = rc.CRED_INCLUDE
    nxt = ctx.for_redirect("https://a.test/2")
    assert nxt.credentials == rc.CRED_INCLUDE, "同源重定向保留凭据"


# ---------------------------------------------------------------------------
# 第 3/5 点 — 发送前判定
# ---------------------------------------------------------------------------

@_reg
def test_same_origin_needs_no_acao():
    """第 4/5 点：同源无需 ACAO。"""
    ctx = _ctx(url="https://a.test/data.json")
    d = rc.evaluate_request(ctx)
    assert d.allowed, d.describe()
    assert d.requires_acao is False, "同源不得要求 ACAO"
    assert "no ACAO required" in d.reason


@_reg
def test_cross_origin_classic_script_is_opaque_not_blocked():
    """审计验收：跨源 classic 脚本不因缺 ACAO 被拦，只是 opaque。"""
    ctx = rc.context_for_destination("https://a.test",
                                     "https://cdn.b.test/s.js",
                                     rc.DEST_CLASSIC_SCRIPT)
    d = rc.evaluate_request(ctx)
    assert d.allowed, f"跨源 classic 脚本不得被拦：{d.describe()}"
    assert d.opaque_response is True
    assert d.requires_acao is False


@_reg
def test_cross_origin_fetch_requires_acao_and_preflight():
    ctx = rc.context_for_destination("https://a.test",
                                     "https://api.b.test/v1",
                                     rc.DEST_FETCH)
    d = rc.evaluate_request(ctx)
    assert d.allowed and d.requires_acao, d.describe()
    assert d.requires_preflight is False, "简单 GET 无需预检"
    # 非简单方法 → 需预检
    ctx2 = _ctx(url="https://api.b.test/v1")
    ctx2.method = "PUT"
    assert rc.evaluate_request(ctx2).requires_preflight is True
    # 带自定义头 → 需预检
    ctx3 = _ctx(url="https://api.b.test/v1")
    ctx3.headers = {"X-Custom": "1"}
    assert rc.evaluate_request(ctx3).requires_preflight is True


@_reg
def test_same_origin_mode_cross_origin_target_is_rejected():
    ctx = _ctx(url="https://b.test/x")
    ctx.mode = rc.MODE_SAME_ORIGIN
    d = rc.evaluate_request(ctx)
    assert not d.allowed and d.blocked_by == "cors", d.describe()


@_reg
def test_unsupported_scheme_and_mode_and_destination_are_rejected():
    """第 5 点：不支持的模式明确拒绝，不偷换成放行。"""
    ftp = _ctx(url="ftp://a.test/x")
    d1 = rc.evaluate_request(ftp)
    assert not d1.allowed and d1.blocked_by == "scheme"

    nomode = _ctx(url="https://b.test/x")
    nomode.mode = ""
    d2 = rc.evaluate_request(nomode)
    assert not d2.allowed and d2.blocked_by == "mode"

    unknown = _ctx(url="https://a.test/x")
    unknown.destination = "bogus"
    d3 = rc.evaluate_request(unknown)
    assert not d3.allowed and d3.blocked_by == "destination"


@_reg
def test_mixed_content_blocks_active_allows_image_flagged():
    """https 文档里的 http 资源：active 阻断，image 只标记。"""
    for dest in (rc.DEST_CLASSIC_SCRIPT, rc.DEST_MODULE, rc.DEST_STYLE,
                 rc.DEST_FETCH, rc.DEST_XHR, rc.DEST_FONT):
        ctx = rc.context_for_destination("https://a.test",
                                         "http://a.test/x", dest)
        d = rc.evaluate_request(ctx)
        assert not d.allowed, f"{dest} 属 blockable mixed content：{d.describe()}"
        assert d.blocked_by == "mixed-content"
    img = rc.context_for_destination("https://a.test", "http://a.test/i.png",
                                     rc.DEST_IMAGE)
    di = rc.evaluate_request(img)
    assert di.allowed, "image 是 optionally-blockable，不断然阻断"
    assert di.mixed_content is True, "但必须标记为混合内容"


@_reg
def test_deadline_exceeded_is_rejected_before_send():
    ctx = rc.context_for_destination("https://a.test", "https://a.test/x",
                                     rc.DEST_FETCH, deadline=50.0)
    d = rc.evaluate_request(ctx, now=60.0)
    assert not d.allowed and d.blocked_by == "deadline", d.describe()
    assert rc.evaluate_request(ctx, now=10.0).allowed


@_reg
def test_csp_blocks_before_send():
    """第 6 点：被 CSP 禁止的请求不得先发包。"""
    from toy_browser.network import net

    class _Pol:
        def __init__(self, directives):
            self.directives = directives

        def csp_allowed(self, kind, value):
            vals = net._csp_values_for(self.directives, kind)
            if vals is None:
                return True
            if not vals:
                return False
            return not net._csp_blocks(vals, value, "https://a.test")

    # connect-src 'none' → fetch 被拒
    ctx = rc.context_for_destination("https://a.test", "https://api.b.test/v1",
                                     rc.DEST_FETCH,
                                     policy=_Pol({"connect-src": ["'none'"]}))
    d = rc.evaluate_request(ctx)
    assert not d.allowed and d.blocked_by == "csp", d.describe()

    # img-src 空指令（等价 'none'）→ 图片被拒
    ctx2 = rc.context_for_destination("https://a.test",
                                      "https://a.test/i.png",
                                      rc.DEST_IMAGE,
                                      policy=_Pol({"img-src": []}))
    d2 = rc.evaluate_request(ctx2)
    assert not d2.allowed and d2.blocked_by == "csp", d2.describe()

    # 无指令 → 放行（"无指令" ≠ "空指令"）
    ctx3 = rc.context_for_destination("https://a.test",
                                      "https://a.test/i.png",
                                      rc.DEST_IMAGE, policy=_Pol({}))
    assert rc.evaluate_request(ctx3).allowed

    # connect-src 有值但允许 self → 放行
    ctx4 = rc.context_for_destination("https://a.test",
                                      "https://a.test/v1",
                                      rc.DEST_FETCH,
                                      policy=_Pol({"connect-src": ["'self'"]}))
    assert rc.evaluate_request(ctx4).allowed


@_reg
def test_policy_exception_does_not_crash():
    """P13：策略对象抛异常不得让判定崩（退化为放行，不静默吞错致死）。"""

    class _Boom:
        def csp_allowed(self, kind, value):
            raise RuntimeError("boom")

    ctx = rc.context_for_destination("https://a.test", "https://a.test/x",
                                     rc.DEST_FETCH, policy=_Boom())
    assert rc.evaluate_request(ctx).allowed, "策略异常须退化为放行且不崩"


# ---------------------------------------------------------------------------
# 第 5 点 — 禁止响应头 / opaque 响应
# ---------------------------------------------------------------------------

@_reg
def test_opaque_response_has_no_readable_headers():
    ctx = rc.context_for_destination("https://a.test",
                                     "https://cdn.b.test/s.js",
                                     rc.DEST_CLASSIC_SCRIPT)
    visible, stripped = rc.filter_response_headers(
        ctx, {"Content-Type": "text/js", "Set-Cookie": "a=b"})
    assert visible == {}, f"opaque 响应不得有可读头：{visible}"
    assert "Set-Cookie" in stripped


@_reg
def test_forbidden_response_headers_filtered_in_cors():
    ctx = rc.context_for_destination("https://a.test",
                                     "https://api.b.test/v1",
                                     rc.DEST_FETCH)
    visible, stripped = rc.filter_response_headers(
        ctx, {"Content-Type": "application/json", "Set-Cookie": "a=b",
              "Access-Control-Allow-Origin": "*"})
    assert "Content-Type" in visible
    assert "Set-Cookie" not in visible and "Set-Cookie" in stripped
    assert "Access-Control-Allow-Origin" not in visible


@_reg
def test_empty_headers_are_safe():
    ctx = _ctx()
    assert rc.filter_response_headers(ctx, {}) == ({}, [])
    assert rc.filter_response_headers(ctx, None) == ({}, [])


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

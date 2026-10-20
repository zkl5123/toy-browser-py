"""R7-S03 第 2 点 — 真实 site-for-cookies 与凭据上下文。

方案原文："所有发送和重定向使用真实 site-for-cookies / credentials，
不使用缺省『同站顶层 GET』"。

修复前的三个具体缺口（本文件逐条锁死）：

1. ``_follow_redirects`` 把**同一个** cookie_context dict 透传到每一
   跳，跨站标记在第 0 跳算完就不变——``a.test`` 的子资源 302 到
   ``b.test``，第二跳仍被当成同站，Lax cookie 泄漏到跨站目标。
2. **凭据模式完全没参与 cookie 发送判定**：``credentials: 'omit'``
   的跨源 fetch、无 ``crossorigin`` 的 classic 脚本照发 cookie。
3. 调用点缺省是"同站 + 顶层 + GET"——默认值替调用方做安全决策，
   JS fetch / XHR 走的也是这个默认值。

本文件锁 ``network/cookie_context.py`` 的契约：schemeful same-site、
未知站点按跨站、凭据门禁、逐跳重算与降级、以及"没有信息就不发"
（``unknown_context``）。不发真实请求，只用合成 URL 与 ``*.test``
保留域；与 jar 的联调用内存 :class:`CookieJar`（不落盘）。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect

from toy_browser.network import cookie_context as cc
from toy_browser.network import cookie_store as cs

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _jar(*set_cookies):
    """造一个只含 SameSite=Lax cookie 的 jar（默认保护的那档）。"""
    jar = cs.CookieJar()
    for sc in set_cookies:
        jar.ingest_response(f"http://a.test/", {"set-cookie": sc})
    return jar


# ---------------------------------------------------------------------------
# 静态层 — 源码契约
# ---------------------------------------------------------------------------

@_reg
def test_static_no_legacy_default_dict():
    """方案第 2 点：源码里不得再有"缺省同站顶层 GET"的 dict 默认值。

    ``cookie_context`` 缺省必须走 :func:`unknown_context`（保守：不发），
    而不是 ``ctx.get("is_top_level", True)`` 之类的宽松默认。
    """
    src = inspect.getsource(cc)
    assert "is_top_level\", True" not in src, \
        "不得保留『缺省顶层导航』默认值"
    # 跨站判定必须真实计算，不从 dict 里吃默认值。
    prop = inspect.getsource(cc.CookieRequestContext.is_cross_site.fget)
    assert ".get(" not in prop, \
        "is_cross_site 必须按 document_site 真实计算，不得取 dict 默认值"
    assert "document_site" in prop and "return True" in prop, \
        "未知站点必须按跨站处理（保守）"
    # 保守默认必须存在且有文档。
    assert "def unknown_context" in src
    assert "没有信息就不发" in src


@_reg
def test_static_for_redirect_recomputes():
    """方案第 2 点：跳转必须**重新求值**，不是沿用旧 context。"""
    src = inspect.getsource(cc.CookieRequestContext.for_redirect)
    assert "replace(" in src, "跳转应产生新 context（不可变替换）"
    assert "redirect_chain" in src, "跳转链必须留痕"
    assert "same_origin" in src, "跳转必须重新判定跨源"


@_reg
def test_static_credentials_gate_in_header_path():
    """凭据门禁必须在取 Cookie 头的路径上（不是只存字段）。"""
    src = inspect.getsource(cc.CookieRequestContext.cookie_header)
    assert "sends_cookies" in src, \
        "cookie_header 必须先过凭据门禁再问 jar"


@_reg
def test_static_module_has_no_socket():
    """纯逻辑层：不得 import socket / 不得发请求。"""
    src = inspect.getsource(cc)
    assert "import socket" not in src
    assert "urlopen" not in src


# ---------------------------------------------------------------------------
# 第 2 点 — site-for-cookies（schemeful same-site）
# ---------------------------------------------------------------------------

@_reg
def test_top_level_navigation_is_same_site_with_itself():
    ctx = cc.for_navigation("https://a.test/page")
    assert ctx.is_top_level and not ctx.is_cross_site
    assert ctx.document_site == "https://a.test"


@_reg
def test_subdomain_subresource_is_same_site():
    ctx = cc.for_subresource("https://cdn.a.test/x.css",
                             "https://a.test/", destination="style")
    assert not ctx.is_cross_site, "子域属同站（registrable domain 相同）"
    assert not ctx.is_top_level


@_reg
def test_different_registrable_domain_is_cross_site():
    ctx = cc.for_subresource("https://b.test/x.css",
                             "https://a.test/", destination="style")
    assert ctx.is_cross_site


@_reg
def test_scheme_differs_is_cross_site():
    """schemeful：https 文档里的 http 子资源算**跨站**（http ≠ https）。"""
    ctx = cc.for_subresource("http://a.test/x.css",
                             "https://a.test/", destination="style")
    assert ctx.is_cross_site, "schemeful same-site：方案不同即跨站"


@_reg
def test_unknown_document_site_treated_as_cross_site():
    """没有顶层归属信息 → 一律按跨站（宁可不发）。"""
    ctx = cc.unknown_context("https://a.test/x")
    assert ctx.is_cross_site is True
    assert ctx.is_cross_origin is True
    assert ctx.sends_cookies is False


@_reg
def test_public_suffix_hosts_are_distinct_sites():
    """.test 是保留公共后缀：a.test 与 b.test 是两个不同站点。"""
    assert cc.site_for_cookies_of("http://a.test/") == "http://a.test"
    assert cc.site_for_cookies_of("http://b.test/") != \
        cc.site_for_cookies_of("http://a.test/")


@_reg
def test_same_origin_requires_scheme_host_and_port():
    assert cc.same_origin("https://a.test/x", "https://a.test/y")
    assert not cc.same_origin("https://a.test:8443/x", "https://a.test/")
    assert not cc.same_origin("http://a.test/x", "https://a.test/")
    assert not cc.same_origin("https://a.test/x", ""), "空源 ≠ 同源"


# ---------------------------------------------------------------------------
# 第 2 点 — credentials 门禁
# ---------------------------------------------------------------------------

@_reg
def test_credentials_omit_never_sends():
    """跨源无凭据 fetch：即使 jar 里有匹配 cookie 也不发。"""
    jar = _jar("lax=1")
    ctx = cc.for_subresource("http://a.test/x", "http://a.test/",
                             destination="fetch",
                             credentials_mode=cc.CRED_OMIT)
    assert ctx.sends_cookies is False
    assert ctx.cookie_header(jar) == "", "omit 模式不得携带 Cookie"


@_reg
def test_credentials_same_origin_blocks_cross_origin():
    jar = _jar("lax=1; Domain=a.test")
    same = cc.for_subresource("http://a.test/x", "http://a.test/",
                              destination="fetch",
                              credentials_mode=cc.CRED_SAME_ORIGIN)
    cross = cc.for_subresource("http://b.test/x", "http://a.test/",
                               destination="fetch",
                               credentials_mode=cc.CRED_SAME_ORIGIN)
    assert same.sends_cookies is True
    assert cross.sends_cookies is False, "same-origin 模式跨源不发"


@_reg
def test_credentials_include_sends_when_host_matches():
    jar = _jar("uc=1")
    ctx = cc.for_subresource("http://a.test/x", "http://a.test/",
                             destination="fetch",
                             credentials_mode=cc.CRED_INCLUDE)
    assert ctx.sends_cookies is True
    assert ctx.cookie_header(jar) == "uc=1"


@_reg
def test_unknown_credentials_mode_downgrades_to_omit():
    """未知凭据模式不得被当成 include（P13 + 保守默认）。"""
    ctx = cc.CookieRequestContext(url="http://a.test/x",
                                  document_origin="http://a.test",
                                  credentials_mode="bogus")
    assert ctx.credentials_mode == cc.CRED_OMIT
    assert ctx.sends_cookies is False


@_reg
def test_cross_site_subresource_drops_lax_cookie():
    """验收：跨站子资源的 Lax cookie 被丢（凭据门禁之前 jar 就过滤）。"""
    jar = _jar("lax=1; Domain=a.test")
    cross = cc.for_subresource("http://b.test/x.css", "http://a.test/",
                               destination="style",
                               credentials_mode=cc.CRED_INCLUDE)
    assert cross.is_cross_site
    assert cross.cookie_header(jar) == "", "跨站子资源不得带 Lax cookie"
    same = cc.for_subresource("http://a.test/x.css", "http://a.test/",
                              destination="style",
                              credentials_mode=cc.CRED_INCLUDE)
    assert same.cookie_header(jar) == "lax=1"


@_reg
def test_cross_site_top_level_get_keeps_lax_cookie():
    """SameSite=Lax 的定义：跨站**顶层 GET** 导航仍然发送。"""
    jar = cs.CookieJar()
    jar.ingest_response("http://b.test/", {"set-cookie": "lax=1"})
    ctx = cc.for_navigation("http://b.test/page")
    assert ctx.is_top_level
    assert ctx.cookie_header(jar) == "lax=1"


@_reg
def test_cross_site_strict_never_sent():
    """Strict：同站（含顶层到自己站）照发，跨站一律不发。"""
    jar = cs.CookieJar()
    jar.ingest_response("http://b.test/",
                        {"set-cookie": "strict=1; SameSite=Strict"})
    # 顶层导航到 b.test 自己：同站 → 发送。
    nav = cc.for_navigation("http://b.test/page")
    assert nav.cookie_header(jar) == "strict=1"
    # a.test 文档里拉 b.test 的子资源：跨站 → 不发。
    sub = cc.for_subresource("http://b.test/x.css", "http://a.test/",
                             destination="style",
                             credentials_mode=cc.CRED_INCLUDE)
    assert sub.is_cross_site
    assert sub.cookie_header(jar) == "", "Strict 跨站一律不发"


# ---------------------------------------------------------------------------
# 第 2 点 — 逐跳重定向重新求值（审计点名的泄漏）
# ---------------------------------------------------------------------------

@_reg
def test_redirect_recomputes_cross_site():
    """a.test 子资源 302 到 b.test：第二跳必须变成跨站。"""
    ctx = cc.for_subresource("http://a.test/x.css", "http://a.test/",
                             destination="style",
                             credentials_mode=cc.CRED_INCLUDE)
    assert not ctx.is_cross_site
    nxt = ctx.for_redirect("http://b.test/y.css")
    assert nxt.url == "http://b.test/y.css"
    assert nxt.is_cross_site, "跳转后必须重新判定为跨站（旧代码不会）"
    assert nxt.document_site == ctx.document_site, \
        "跳转不改变文档归属（站点仍属发起它的文档）"


@_reg
def test_redirect_cross_origin_downgrades_credentials():
    """非导航请求跨源跳转 → 凭据降为 omit。"""
    ctx = cc.for_subresource("http://a.test/x", "http://a.test/",
                             destination="fetch",
                             credentials_mode=cc.CRED_INCLUDE)
    nxt = ctx.for_redirect("http://b.test/y")
    assert nxt.credentials_mode == cc.CRED_OMIT
    assert nxt.sends_cookies is False


@_reg
def test_top_level_redirect_keeps_credentials():
    """顶层导航跨源跳转**不**降级（SameSite=Lax 顶层 GET 语义）。"""
    ctx = cc.for_navigation("http://a.test/login")
    nxt = ctx.for_redirect("http://sso.test/authorize")
    assert nxt.credentials_mode == cc.CRED_INCLUDE
    assert nxt.is_top_level


@_reg
def test_redirect_chain_recorded():
    ctx = cc.for_subresource("http://a.test/1", "http://a.test/",
                             destination="fetch")
    a = ctx.for_redirect("/2")
    b = a.for_redirect("http://a.test/3")
    assert b.redirect_chain == ("http://a.test/1", "http://a.test/2")
    assert b.to_dict()["redirect_hops"] == 2


@_reg
def test_redirect_relative_location_resolved():
    ctx = cc.for_subresource("http://a.test/dir/1.css", "http://a.test/",
                             destination="style")
    nxt = ctx.for_redirect("../other/2.css")
    assert nxt.url == "http://a.test/other/2.css"


@_reg
def test_redirect_missing_location_is_noop():
    ctx = cc.for_navigation("http://a.test/x")
    assert ctx.for_redirect("").url == ctx.url
    assert ctx.for_redirect(None).url == ctx.url  # type: ignore[arg-type]


@_reg
def test_redirect_after_cross_site_drops_lax_cookie():
    """端到端（jar 层）：跳转前同站能带，跳转后跨站不能带。"""
    jar = _jar("lax=1; Domain=a.test")
    ctx = cc.for_subresource("http://a.test/x.css", "http://a.test/",
                             destination="style",
                             credentials_mode=cc.CRED_INCLUDE)
    assert ctx.cookie_header(jar) == "lax=1"
    nxt = ctx.for_redirect("http://b.test/y.css")
    assert nxt.cookie_header(jar) == "", \
        "跳转后的跨站请求不得继续携带 Lax cookie"


# ---------------------------------------------------------------------------
# 兼容层与可观测
# ---------------------------------------------------------------------------

@_reg
def test_from_legacy_dict_preserves_same_site_semantics():
    """旧 dict（net._subresource_cookie_context 的形状）提升后语义不变。"""
    ctx = cc.from_legacy_dict("http://a.test/x.css",
                              {"is_cross_site": False, "method": "GET",
                               "is_top_level": False})
    assert not ctx.is_cross_site and not ctx.is_top_level
    ctx2 = cc.from_legacy_dict("http://a.test/x.css",
                               {"is_cross_site": True, "method": "GET",
                                "is_top_level": False})
    assert ctx2.is_cross_site


@_reg
def test_from_request_context_reuses_one_source():
    from toy_browser.network import request_context as rc
    r = rc.context_for_destination("https://a.test",
                                   "https://b.test/api", rc.DEST_FETCH)
    ctx = cc.from_request_context(r)
    assert ctx.destination == rc.DEST_FETCH
    assert ctx.document_origin == "https://a.test"
    assert ctx.is_cross_site, "跨源 fetch 的 cookie 上下文必须标记跨站"
    assert ctx.credentials_mode == r.credentials


@_reg
def test_describe_is_observable():
    d = cc.for_navigation("https://a.test/p").describe()
    assert "top-level" in d and "send" in d
    d2 = cc.unknown_context("https://a.test/p").describe()
    assert "cross-site" in d2 and "omit" in d2


@_reg
def test_jar_exception_does_not_propagate():
    """P13：jar 抛异常（坏数据 / mock）时退化为不发，不打挂页面。"""

    class _Boom:
        def cookie_header_for(self, *a, **k):
            raise RuntimeError("boom")

    ctx = cc.for_navigation("http://a.test/p")
    assert ctx.cookie_header(_Boom()) == ""
    assert ctx.ingest(_Boom(), {"set-cookie": "x=1"}) == 0


@_reg
def test_frozen_context_cannot_be_mutated():
    ctx = cc.for_navigation("http://a.test/p")
    try:
        ctx.url = "http://evil.test/"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("context 必须不可变（frozen dataclass）")


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e)))
            print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)

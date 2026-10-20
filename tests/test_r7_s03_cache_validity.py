"""R7-S03 第 4 点 — 缓存校验（Vary / 授权响应 / 304 头合并 / 凭据隔离）。

修复前 ``net.CacheStore`` 的四处缺口（本文件逐条锁死）：

1. ``Vary`` 完全没有实现：``Vary: Accept-Encoding`` 的响应会被之后
   任何请求命中；``Vary: *`` 也照存照命中；
2. 带 ``Authorization`` 请求拿到的响应进了共享缓存（RFC 7234 §3.2）；
3. 304 分支直接把 304 的头当响应头返回（注释说要合并，代码没做），
   于是 ``Content-Type`` / ``Content-Security-Policy`` 在 304 之后
   消失——审计验收项"304 后安全响应头仍正确"当前失败；
4. 缓存键只有 URL，不含凭据策略 → 串凭据。

本文件**只测纯逻辑**（``network/cache_validity.py``），不落盘、不发
请求；磁盘接线在 ``test_r7_s03_cache_wiring.py`` 里验。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect

from toy_browser.network import cache_validity as cv

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 静态层 — 源码契约
# ---------------------------------------------------------------------------

@_reg
def test_static_module_is_pure():
    """纯逻辑层：不落盘、不发请求、不读时钟。"""
    src = inspect.getsource(cv)
    for banned in ("import os", "open(", "import socket", "time.time",
                   "urlopen"):
        assert banned not in src, f"纯逻辑层不得出现 {banned}"


@_reg
def test_static_vary_star_never_matches():
    src = inspect.getsource(cv.vary_matches)
    assert '"*"' in src or "'*'" in src
    assert "return False" in src


@_reg
def test_static_304_merge_keeps_stored_headers():
    src = inspect.getsource(cv.merge_304_headers)
    assert "stored[key] = value" in src, "304 头必须覆盖到已存头之上"
    assert "HOP_BY_HOP_HEADERS" in src
    assert "set-cookie" in src, "Set-Cookie 必须多值保留（第 3 点）"


@_reg
def test_static_cache_key_includes_credentials():
    src = inspect.getsource(cv.cache_key)
    assert "cred=" in src, "缓存键必须含凭据模式（不得串凭据策略）"


# ---------------------------------------------------------------------------
# Cache-Control 解析
# ---------------------------------------------------------------------------

@_reg
def test_parse_cache_control_flags_and_args():
    cc = cv.parse_cache_control("no-store, max-age=60, public")
    assert cc["no-store"] is True
    assert cc["max-age"] == "60"
    assert cc["public"] is True


@_reg
def test_parse_cache_control_duplicate_keeps_first():
    cc = cv.parse_cache_control("max-age=10, max-age=999")
    assert cc["max-age"] == "10", "重复指令保留首次（与 CSP 同口径）"


@_reg
def test_parse_cache_control_quoted_and_empty():
    assert cv.parse_cache_control("") == {}
    assert cv.parse_cache_control(None) == {}
    assert cv.parse_cache_control('no-cache="Set-Cookie"')["no-cache"] == \
        "Set-Cookie"


# ---------------------------------------------------------------------------
# Vary（第 4 点）
# ---------------------------------------------------------------------------

@_reg
def test_vary_fields_parsing():
    assert cv.vary_fields({"vary": "Accept-Encoding, Accept"}) == \
        ["accept-encoding", "accept"]
    assert cv.vary_fields({"vary": "*"}) == ["*"]
    assert cv.vary_fields({}) == []
    assert cv.vary_fields({"vary": ["Accept-Encoding"]}) == ["accept-encoding"]


@_reg
def test_vary_matches_same_value():
    assert cv.vary_matches(["accept-encoding"],
                           {"accept-encoding": "gzip"},
                           {"accept-encoding": "gzip"})


@_reg
def test_vary_mismatch_is_not_a_hit():
    """审计的核心：Vary 字段值不同 ⇒ 不得命中。"""
    assert not cv.vary_matches(["accept-encoding"],
                               {"accept-encoding": "gzip"},
                               {"accept-encoding": "br"}), \
        "Vary: Accept-Encoding 下 gzip 的条目不得被 br 请求命中"


@_reg
def test_vary_missing_both_sides_matches():
    assert cv.vary_matches(["accept-language"], {}, {})


@_reg
def test_vary_missing_one_side_mismatches():
    assert not cv.vary_matches(["accept-language"], {"accept-language": "en"}, {})


@_reg
def test_vary_star_never_matches():
    assert not cv.vary_matches(["*"], {"accept-encoding": "gzip"},
                               {"accept-encoding": "gzip"})


@_reg
def test_no_vary_always_matches():
    assert cv.vary_matches([], {"a": "1"}, {"b": "2"})


# ---------------------------------------------------------------------------
# 可存性（第 4 点）
# ---------------------------------------------------------------------------

@_reg
def test_no_store_not_storable():
    d = cv.is_storable(200, {"cache-control": "no-store"})
    assert not d.allowed and "no-store" in d.reason
    assert d.no_store


@_reg
def test_uncacheable_status_rejected():
    for st in (302, 400, 500, 201):
        d = cv.is_storable(st, {})
        assert not d.allowed, f"status {st} 不应默认可缓存"
        assert "not cacheable" in d.reason


@_reg
def test_cacheable_status_allowed():
    for st in (200, 203, 204, 404, 410, 301):
        assert cv.is_storable(st, {}).allowed, f"status {st} 应可缓存"


@_reg
def test_vary_star_not_stored():
    d = cv.is_storable(200, {"vary": "*"})
    assert not d.allowed and "Vary" in d.reason


@_reg
def test_authenticated_response_needs_public():
    """RFC 7234 §3.2：带 Authorization 请求的响应，除非显式
    public / must-revalidate / s-maxage，否则共享缓存不得存。"""
    d = cv.is_storable(200, {}, request_headers={"authorization": "Bearer x"})
    assert not d.allowed and "RFC 7234" in d.reason
    assert cv.is_storable(
        200, {"cache-control": "public"},
        request_headers={"authorization": "Bearer x"}).allowed
    assert cv.is_storable(
        200, {"cache-control": "must-revalidate"},
        request_headers={"authorization": "Bearer x"}).allowed
    assert cv.is_storable(
        200, {"cache-control": "s-maxage=60"},
        request_headers={"authorization": "Bearer x"}).allowed


@_reg
def test_empty_authorization_is_not_authenticated():
    d = cv.is_storable(200, {}, request_headers={"authorization": "   "})
    assert d.allowed, "空 Authorization 不算带凭据请求"


@_reg
def test_no_cache_is_storable_but_must_revalidate():
    d = cv.is_storable(200, {"cache-control": "no-cache"})
    assert d.allowed and d.no_cache
    assert cv.must_revalidate({"cache-control": "no-cache"})


@_reg
def test_must_revalidate_flag():
    d = cv.is_storable(200, {"cache-control": "max-age=60, must-revalidate"})
    assert d.allowed and d.must_revalidate


@_reg
def test_vary_captured_in_decision():
    d = cv.is_storable(200, {"vary": "Accept-Encoding"})
    assert d.vary == ("accept-encoding",)


@_reg
def test_describe_is_observable():
    assert cv.describe_decision(cv.is_storable(200, {})).startswith("cache:store")
    txt = cv.describe_decision(cv.is_storable(200, {"cache-control": "no-store"}))
    assert "cache:skip" in txt and "no-store" in txt


# ---------------------------------------------------------------------------
# 缓存键（不得串 origin / 凭据策略）
# ---------------------------------------------------------------------------

@_reg
def test_cache_key_separates_credentials():
    url = "https://a.test/x"
    k_omit = cv.cache_key(url, "omit")
    k_same = cv.cache_key(url, "same-origin")
    k_incl = cv.cache_key(url, "include")
    assert len({k_omit, k_same, k_incl}) == 3, \
        "不同凭据模式的响应必须落在不同的缓存条目"


@_reg
def test_cache_key_separates_origin():
    assert cv.cache_key("https://a.test/x") != cv.cache_key("https://b.test/x")
    assert cv.cache_key("http://a.test/x") != cv.cache_key("https://a.test/x")
    assert cv.cache_key("https://a.test:8443/x") != \
        cv.cache_key("https://a.test/x"), "端口属于 origin"


@_reg
def test_cache_key_includes_vary_values():
    base = "https://a.test/x"
    k1 = cv.cache_key(base, "", ["accept-encoding"],
                      {"accept-encoding": "gzip"})
    k2 = cv.cache_key(base, "", ["accept-encoding"],
                      {"accept-encoding": "br"})
    assert k1 != k2, "Vary 字段值不同必须落到不同条目"


@_reg
def test_cache_key_is_stable():
    assert cv.cache_key("https://a.test/x", "omit") == \
        cv.cache_key("https://a.test/x", "omit")


# ---------------------------------------------------------------------------
# 304 头合并（第 4 点）
# ---------------------------------------------------------------------------

@_reg
def test_304_keeps_content_type_and_security_headers():
    """审计验收项：304 之后安全响应头仍然正确。"""
    stored = {
        "content-type": "text/html; charset=utf-8",
        "content-security-policy": "default-src 'self'",
        "x-frame-options": "DENY",
        "etag": '"v1"',
        "date": "Mon, 01 Jan 2024 00:00:00 GMT",
    }
    incoming = {
        "etag": '"v2"',
        "date": "Tue, 02 Jan 2024 00:00:00 GMT",
        "cache-control": "max-age=600",
    }
    merged = cv.merge_304_headers(stored, incoming)
    assert merged["content-type"] == "text/html; charset=utf-8", \
        "304 不得丢掉 Content-Type"
    assert merged["content-security-policy"] == "default-src 'self'", \
        "304 不得丢掉 CSP"
    assert merged["x-frame-options"] == "DENY"
    assert merged["etag"] == '"v2"', "304 里的新值必须覆盖旧值"
    assert merged["cache-control"] == "max-age=600"


@_reg
def test_304_skips_hop_by_hop_and_content_length():
    stored = {"content-type": "text/plain", "content-length": "11"}
    merged = cv.merge_304_headers(stored, {"content-length": "0",
                                           "connection": "keep-alive",
                                           "transfer-encoding": "chunked"})
    assert merged["content-length"] == "11", \
        "body 来自缓存，Content-Length 不能被 304 改掉"
    assert "connection" not in merged
    assert "transfer-encoding" not in merged


@_reg
def test_304_merges_set_cookie_as_multivalue():
    """第 3 点：304 里的 Set-Cookie 与旧值都保留（不覆盖、不去重丢行）。"""
    stored = {"set-cookie": ["a=1"]}
    merged = cv.merge_304_headers(stored, {"set-cookie": ["b=2", "c=3"]})
    assert merged["set-cookie"] == ["a=1", "b=2", "c=3"]
    merged2 = cv.merge_304_headers({}, {"set-cookie": "d=4"})
    assert merged2["set-cookie"] == ["d=4"], "单值也归一成列表"


@_reg
def test_304_empty_incoming_returns_copy():
    stored = {"content-type": "text/plain"}
    merged = cv.merge_304_headers(stored, {})
    assert merged == stored
    assert merged is not stored, "必须返回副本（不得就地改已存头）"


@_reg
def test_304_does_not_mutate_stored():
    stored = {"content-type": "text/plain"}
    cv.merge_304_headers(stored, {"etag": '"z"'})
    assert "etag" not in stored, "纯逻辑层不得就地修改输入"


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

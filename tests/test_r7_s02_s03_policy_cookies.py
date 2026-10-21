"""R7-S02/S03/S05/S06 — CSP/CORS policy chain + cookie context.

Round-7 security audit (网络脚本与安全审计.md §2/§4):

* **S02** — a CSP-blocked subresource must never hit the wire
  ("img-src none → server request count is zero"); an empty
  directive (``img-src`` with no values) is ``'none'``, not "no
  constraint"; a repeated directive keeps its FIRST occurrence;
  ``<base href>`` must not rewrite the document origin; fetch/XHR
  share the document's ``connect-src`` policy.
* **S06** — a same-origin script under ``script-src 'self'`` must
  not be blocked for a missing ACAO; a cross-origin *classic*
  script (no ``crossorigin``) loads no-cors and must not require
  ACAO; ``type=module`` / ``crossorigin`` scripts do.
* **S03/S05** — host-only cookies stay on their host; a public
  suffix cannot be a cookie Domain; the default Path is the request
  directory; ``__Secure-`` / ``__Host-`` prefixes are enforced; an
  illegal SameSite normalizes to Lax; every Set-Cookie line is
  ingested (GET and POST); ``for_url`` filters by Path; the
  "site for cookies" is scheme + registrable domain.

All network tests use a local loopback server with a per-path
request counter (offline, deterministic); cookie semantics are
checked at the unit level plus one loopback ingest probe.
"""
import gzip
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.network import net
from toy_browser.network import cookie_store as cs
from toy_browser.network import request_context as _rc
from toy_browser.network.cookie_store import CookieJar, parse_set_cookie
from toy_browser.network.net import (
    DocumentPolicy,
    HttpClient,
    _build_subresource_request_context,
    _csp_values_for,
    fetch_resource,
    parse_csp_directives,
)
from toy_browser.scripting import js

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# loopback server with a per-path request counter
# ---------------------------------------------------------------------------

def _start_server(routes):
    """Minimal http.server (GET + POST) that counts requests per path
    and records the last Cookie header seen per path.

    ``routes`` maps (method, path) → (status, ctype, body, extra_hdrs)
    where *extra_hdrs* is a **list** of (name, value) pairs (a list, so
    a response can carry several ``Set-Cookie`` lines).
    Returns (server, port, counts); the handler records
    ``counts[(method, path)]`` and ``counts[("hdr", path)]``.
    """
    import http.server
    from http.server import BaseHTTPRequestHandler, HTTPServer

    counts: dict = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            key = (self.command, self.path.split("?")[0])
            counts[key] = counts.get(key, 0) + 1
            cookie_hdr = self.headers.get("Cookie")
            counts[("hdr", key[1])] = cookie_hdr or ""
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            r = routes.get(key)
            if r is None:
                self.send_response(404)
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"404x")
                return
            status, ctype, body, extra = r
            self.send_response(status)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in (extra or []):
                self.send_header(k, v)
            if not any(k.lower() == "content-length"
                       for k, _v in (extra or [])):
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _serve
        do_POST = _serve
        # R7-S02 第 5 点 — OPTIONS（跨源 CORS 预检往返集成测试用）。
        do_OPTIONS = _serve

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, counts


# ---------------------------------------------------------------------------
# 1. CSP parser semantics (S02)
# ---------------------------------------------------------------------------

@_reg
def test_csp_duplicate_directive_first_wins():
    d = parse_csp_directives("script-src 'self'; script-src 'none'")
    assert d.get("script-src") == ["'self'"], \
        f"first occurrence must win: {d}"


@_reg
def test_csp_empty_directive_recorded_as_empty_list():
    d = parse_csp_directives("default-src 'self'; img-src")
    assert d.get("img-src") == [], f"empty directive must be []: {d}"
    assert d.get("default-src") == ["'self'"]


@_reg
def test_csp_values_for_three_states():
    d = parse_csp_directives("default-src 'self'; img-src")
    # present-but-empty → [] (== 'none')
    assert _csp_values_for(d, "img") == []
    assert _csp_values_for(d, "img-src") == []
    # absent → falls back to default-src
    assert _csp_values_for(d, "script") == ["'self'"]
    # neither → None (no constraint)
    assert _csp_values_for(parse_csp_directives("img-src 'self'"),
                           "script") is None
    assert _csp_values_for({}, "img") is None


@_reg
def test_policy_csp_allowed_empty_directive_blocks():
    p = DocumentPolicy(doc_origin="http://a.test/",
                       csp_directives=parse_csp_directives("img-src"))
    assert not p.csp_allowed("img", "http://a.test/x.png"), \
        "present-but-empty img-src must block everything"
    # but a missing directive still permits
    p2 = DocumentPolicy(doc_origin="http://a.test/",
                        csp_directives=parse_csp_directives("script-src 'self'"))
    assert p2.csp_allowed("img", "http://a.test/x.png")


# ---------------------------------------------------------------------------
# 2. loader policy chain on the wire (S02 / S06 acceptance matrix)
# ---------------------------------------------------------------------------

@_reg
def test_img_src_none_zero_requests():
    """S02 acceptance: img-src none → the server never sees a request."""
    srv, port, counts = _start_server({
        ("GET", "/pixel.png"): (200, "image/png", b"\x89PNG\r\n", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        root = dom.parse(
            f'<html><body><img src="{base}pixel.png"></body></html>')
        policy = DocumentPolicy(
            doc_origin=net._origin_str(base),
            csp_directives=parse_csp_directives("img-src none"))
        loaded = net.load_subresources(root, base, client=c, policy=policy)
        assert loaded["image"][f"{base}pixel.png"] is None, \
            "img-src none must map the image to None"
        assert counts.get(("GET", "/pixel.png"), 0) == 0, \
            f"blocked resource must not be requested: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_img_src_self_allows_same_origin():
    """The same fixture WITHOUT 'none' does fetch (control case)."""
    srv, port, counts = _start_server({
        ("GET", "/pixel.png"): (200, "image/png", b"\x89PNG\r\n", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        root = dom.parse(
            f'<html><body><img src="{base}pixel.png"></body></html>')
        policy = DocumentPolicy(
            doc_origin=net._origin_str(base),
            csp_directives=parse_csp_directives("img-src 'self'"))
        loaded = net.load_subresources(root, base, client=c, policy=policy)
        r = loaded["image"][f"{base}pixel.png"]
        assert r is not None and r.status == 200, "same-origin img must load"
        assert counts.get(("GET", "/pixel.png"), 0) == 1
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_same_origin_script_self_no_acao_allowed():
    """S06 acceptance: same-origin script under script-src 'self'
    must NOT be blocked for a missing ACAO."""
    srv, port, counts = _start_server({
        ("GET", "/a.js"): (200, "application/javascript", b"var x=1;", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        root = dom.parse(
            f'<html><body><script src="{base}a.js"></script></body></html>')
        policy = DocumentPolicy(
            doc_origin=net._origin_str(base),
            csp_directives=parse_csp_directives("script-src 'self'"))
        loaded = net.load_subresources(root, base, client=c, policy=policy)
        r = loaded["script"][f"{base}a.js"]
        assert r is not None and r.status == 200, \
            "same-origin script under script-src 'self' must load without ACAO"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_cross_origin_classic_script_no_acao_allowed():
    """S06 acceptance: a cross-origin CLASSIC script (no crossorigin)
    loads no-cors — no ACAO required."""
    srv_a, port_a, _ = _start_server({
        ("GET", "/page.html"): (200, "text/html", b"<html></html>", []),
    })
    srv_b, port_b, _ = _start_server({
        ("GET", "/app.js"): (200, "application/javascript", b"var y=2;", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        doc_base = f"http://127.0.0.1:{port_a}/"
        js_url = f"http://127.0.0.1:{port_b}/app.js"
        root = dom.parse(
            f'<html><body><script src="{js_url}"></script></body></html>')
        policy = DocumentPolicy(
            doc_origin=net._origin_str(doc_base),
            csp_directives=parse_csp_directives("script-src *"))
        loaded = net.load_subresources(root, doc_base, client=c,
                                       policy=policy)
        r = loaded["script"][js_url]
        assert r is not None and r.status == 200, \
            "classic cross-origin script without ACAO must still load (no-cors)"
        c.close_pool()
    finally:
        srv_a.shutdown(); srv_b.shutdown()


@_reg
def test_cross_origin_module_script_no_acao_blocked():
    """S06 acceptance: a cross-origin MODULE script without ACAO is
    blocked (cors-mode read)."""
    srv_a, port_a, _ = _start_server({
        ("GET", "/page.html"): (200, "text/html", b"<html></html>", []),
    })
    srv_b, port_b, _ = _start_server({
        ("GET", "/mod.js"): (200, "application/javascript", b"var m=1;", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        doc_base = f"http://127.0.0.1:{port_a}/"
        js_url = f"http://127.0.0.1:{port_b}/mod.js"
        root = dom.parse(
            f'<html><body><script type="module" src="{js_url}"></script>'
            f'</body></html>')
        policy = DocumentPolicy(
            doc_origin=net._origin_str(doc_base),
            csp_directives=parse_csp_directives("script-src *"))
        loaded = net.load_subresources(root, doc_base, client=c,
                                       policy=policy)
        assert loaded["script"][js_url] is None, \
            "cross-origin module script without ACAO must be blocked"
        c.close_pool()
    finally:
        srv_a.shutdown(); srv_b.shutdown()


@_reg
def test_crossorigin_script_requires_acao():
    """A script carrying crossorigin= is cors-mode: blocked without
    ACAO, allowed with a matching one."""
    srv_a, port_a, _ = _start_server({
        ("GET", "/page.html"): (200, "text/html", b"<html></html>", []),
    })
    srv_b, port_b, _ = _start_server({
        ("GET", "/cors.js"): (200, "application/javascript", b"var z=1;",
                              []),
        ("GET", "/cors2.js"): (200, "application/javascript", b"var z=1;",
                               [("Access-Control-Allow-Origin", "*")]),
    })
    try:
        c = HttpClient(timeout=5.0)
        doc_base = f"http://127.0.0.1:{port_a}/"
        root = dom.parse(
            f'<html><body>'
            f'<script crossorigin src="http://127.0.0.1:{port_b}/cors.js"></script>'
            f'<script crossorigin="anonymous" src="http://127.0.0.1:{port_b}/cors2.js"></script>'
            f'</body></html>')
        policy = DocumentPolicy(
            doc_origin=net._origin_str(doc_base),
            csp_directives=parse_csp_directives("script-src *"))
        loaded = net.load_subresources(root, doc_base, client=c,
                                       policy=policy)
        assert loaded["script"][f"http://127.0.0.1:{port_b}/cors.js"] is None, \
            "crossorigin script without ACAO must be blocked"
        r = loaded["script"][f"http://127.0.0.1:{port_b}/cors2.js"]
        assert r is not None and r.status == 200, \
            "crossorigin script with ACAO: * must load"
        c.close_pool()
    finally:
        srv_a.shutdown(); srv_b.shutdown()


@_reg
def test_base_href_does_not_rewrite_document_origin():
    """S02 acceptance: <base href> only rewrites relative resolution;
    'self' still matches the DOCUMENT origin."""
    srv, port, counts = _start_server({
        ("GET", "/a.js"): (200, "application/javascript", b"var b=1;", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        doc_base = f"http://127.0.0.1:{port}/"
        # The <base> points at another origin; the script URL resolves
        # against the base, but 'self' must use the document origin.
        other = "http://other.invalid/"
        root = dom.parse(
            f'<html><head><base href="{other}"></head><body>'
            f'<script src="/a.js"></script></body></html>')
        policy = DocumentPolicy(
            doc_origin=net._origin_str(doc_base),
            csp_directives=parse_csp_directives("script-src 'self'"))
        loaded = net.load_subresources(root, doc_base, client=c,
                                       policy=policy)
        # /a.js resolved against <base> → http://other.invalid/a.js,
        # which is NOT 'self' → blocked before the wire.
        blocked_url = "http://other.invalid/a.js"
        assert loaded["script"][blocked_url] is None, \
            "'self' must be the document origin, not the <base> origin"
        assert counts.get(("GET", "/a.js"), 0) == 0, \
            f"non-self script must not be requested: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_connect_src_none_zero_requests_fetch():
    """S02 acceptance: fetch under connect-src 'none' never reaches
    the transport and the promise rejects."""
    srv, port, counts = _start_server({
        ("GET", "/data.json"): (200, "application/json", b'{"a":1}', []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        root = dom.parse(
            "<html><body><script>"
            "fetch('" + base + "data.json')"
            ".then(function(){ console.log('OK'); })"
            ".catch(function(e){ console.log('BLOCKED:' + e.name); });"
            "</script></body></html>")
        logs = []
        host = js.Host(root, logs.append)
        host.doc_url = base
        host.doc_origin = net._origin_str(base)
        host._fetch_transport = net.FetchTransport(c, base_url=base)
        host.csp_directives = parse_csp_directives("connect-src none")
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "BLOCKED:TypeError" in joined, \
            f"connect-src none must reject the fetch: {joined}"
        assert "OK" not in joined, joined
        assert counts.get(("GET", "/data.json"), 0) == 0, \
            f"blocked fetch must not be requested: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_connect_src_none_zero_requests_xhr():
    """S02 acceptance: XHR under connect-src 'none' fires onerror and
    never reaches the transport."""
    srv, port, counts = _start_server({
        ("GET", "/data.json"): (200, "application/json", b'{"a":1}', []),
    })
    try:
        c = HttpClient(timeout=5.0)
        base = f"http://127.0.0.1:{port}/"
        root = dom.parse(
            "<html><body><script>"
            "var x = new XMLHttpRequest();"
            "x.onload = function(){ console.log('OK:' + x.status); };"
            "x.onerror = function(){ console.log('ERR'); };"
            "x.open('GET', '" + base + "data.json');"
            "x.send();"
            "</script></body></html>")
        logs = []
        host = js.Host(root, logs.append)
        host.doc_url = base
        host.doc_origin = net._origin_str(base)
        host._fetch_transport = net.FetchTransport(c, base_url=base)
        host.csp_directives = parse_csp_directives("connect-src none")
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        joined = "\n".join(logs)
        assert "ERR" in joined, f"connect-src none must onerror the XHR: {joined}"
        assert "OK:" not in joined, joined
        assert counts.get(("GET", "/data.json"), 0) == 0, \
            f"blocked XHR must not be requested: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# 3. cookie semantics (S03 / S05)
# ---------------------------------------------------------------------------

def _u(s: str):
    return net.parse_url(s)


@_reg
def test_host_only_cookie_not_sent_to_subdomain():
    jar = CookieJar()
    jar.ingest_response(_u("http://a.test/"),
                        {"set-cookie": "ho=1"})
    c = jar.for_url(_u("http://a.test/"))[0]
    assert c.host_only and c.domain == "a.test"
    # host-only: exact host only — NOT the subdomain.
    assert jar.cookie_header_for(_u("http://sub.a.test/")) == "", \
        "host-only cookie must not leak to a subdomain"
    assert jar.cookie_header_for(_u("http://a.test/")) == "ho=1"


@_reg
def test_domain_cookie_sent_to_subdomain():
    jar = CookieJar()
    jar.ingest_response(_u("http://a.test/"),
                        {"set-cookie": "dc=1; Domain=a.test"})
    c = jar.for_url(_u("http://a.test/"))[0]
    assert not c.host_only
    assert jar.cookie_header_for(_u("http://sub.a.test/")) == "dc=1", \
        "Domain cookie must apply to subdomains"


@_reg
def test_public_suffix_domain_rejected():
    jar = CookieJar()
    # .test is a public suffix: Domain=test from a.test is rejected
    # (it could otherwise bridge sibling domains under .test).
    assert jar.ingest_response(
        _u("http://a.test/"), {"set-cookie": "ps=1; Domain=test"}) == 0
    assert len(jar) == 0
    # A sibling domain cannot set for the other sibling either.
    assert jar.ingest_response(
        _u("http://a.test/"), {"set-cookie": "x=1; Domain=b.test"}) == 0
    # Domain equal to the host is allowed (host-only).
    assert jar.ingest_response(
        _u("http://test/"), {"set-cookie": "y=1; Domain=test"}) == 1
    assert jar.all()[0].host_only is True


@_reg
def test_default_path_is_request_directory():
    jar = CookieJar()
    # No Path attribute → default-path is the request directory.
    jar.ingest_response(_u("http://a.test/private/page"),
                        {"set-cookie": "s=1"})
    c = jar.all()[0]
    assert c.path == "/private", f"default path must be the directory: {c.path}"
    assert jar.cookie_header_for(_u("http://a.test/private/other")) == "s=1"
    assert jar.cookie_header_for(_u("http://a.test/public")) == "", \
        "cookie must not be sent outside its default path"
    # for_url must apply the same Path filter (the legacy for_url
    # skipped it and returned the /private cookie for /public).
    assert jar.for_url(_u("http://a.test/public")) == []
    assert len(jar.for_url(_u("http://a.test/private/x"))) == 1
    # Explicit Path still wins.
    jar.ingest_response(_u("http://a.test/private/page"),
                        {"set-cookie": "t=1; Path=/"})
    assert jar.all()[-1].path == "/"


@_reg
def test_secure_prefix_rules():
    jar = CookieJar()
    # __Secure- requires Secure from a secure origin: plain http to a
    # non-loopback host is rejected.
    assert jar.ingest_response(
        _u("http://a.test/"),
        {"set-cookie": "__Secure-a=1; Secure"}) == 0
    # https origin: accepted.
    assert jar.ingest_response(
        _u("https://a.test/"),
        {"set-cookie": "__Secure-a=1; Secure"}) == 1
    # loopback http counts as a secure origin (local development).
    assert jar.ingest_response(
        _u("http://127.0.0.1:8080/"),
        {"set-cookie": "__Secure-b=1; Secure"}) == 1


@_reg
def test_host_prefix_rules():
    jar = CookieJar()
    base = "https://a.test/"
    # __Host- requires Secure + Path=/ + no Domain.
    assert jar.ingest_response(
        _u(base), {"set-cookie": "__Host-a=1; Secure"}) == 1
    # Wrong path → rejected.
    assert jar.ingest_response(
        _u(base + "x/"), {"set-cookie": "__Host-b=1; Secure; Path=/x/"}) == 0
    # Domain attribute → rejected.
    assert jar.ingest_response(
        _u(base), {"set-cookie": "__Host-c=1; Secure; Domain=a.test"}) == 0
    # Missing Secure → rejected.
    assert jar.ingest_response(
        _u(base), {"set-cookie": "__Host-d=1"}) == 0


@_reg
def test_illegal_samesite_normalizes_to_lax():
    jar = CookieJar()
    jar.ingest_response(_u("http://a.example.com/"),
                        {"set-cookie": "s=1; Domain=example.com; "
                                       "SameSite=bogus"})
    c = jar.all()[0]
    assert c.same_site == "lax", \
        f"illegal SameSite must normalize to lax: {c.same_site!r}"
    # And the Lax default protection is back: a cross-site POST does
    # not carry it (the legacy jar sent it everywhere).
    assert jar.cookie_header_for(_u("http://example.com/"),
                                 is_cross_site=True, method="POST",
                                 is_top_level=False) == ""
    # A cross-site top-level GET still may (Lax semantics).
    assert jar.cookie_header_for(_u("http://example.com/"),
                                 is_cross_site=True, method="GET",
                                 is_top_level=True) == "s=1"


@_reg
def test_samesite_none_requires_secure():
    jar = CookieJar()
    assert jar.ingest_response(
        _u("https://a.test/"), {"set-cookie": "n=1; SameSite=None"}) == 0
    assert jar.ingest_response(
        _u("https://a.test/"),
        {"set-cookie": "n=1; SameSite=None; Secure"}) == 1


@_reg
def test_site_for_cookies_registrable_domain():
    assert cs.site_for_cookies("https://a.example.com/x") == \
        "https://example.com"
    assert cs.site_for_cookies("https://example.com/") == \
        "https://example.com"
    assert cs.site_for_cookies("https://a.co.uk/") == "https://a.co.uk"
    # schemeful same-site: http vs https are cross-site.
    assert cs.site_for_cookies("http://a.example.com/") != \
        cs.site_for_cookies("https://a.example.com/")
    # IP literals are their own site.
    assert cs.site_for_cookies("http://127.0.0.1:8080/") == \
        "http://127.0.0.1"
    # reserved TLDs are public suffixes.
    assert cs.registrable_domain("a.test") == "a.test"
    assert cs.registrable_domain("test") == ""


@_reg
def test_cookie_jar_quotas():
    jar = CookieJar()
    for i in range(cs.MAX_COOKIES + 5):
        jar.ingest_response(_u("http://a.test/"),
                            {"set-cookie": f"c{i}=v"})
    assert len(jar) == cs.MAX_COOKIES, \
        f"jar must cap at MAX_COOKIES: {len(jar)}"
    # oldest evicted first: c0..c4 are gone.
    names = {c.name for c in jar.all()}
    assert "c0" not in names and "c4" not in names, names
    assert f"c{cs.MAX_COOKIES + 4}" in names
    # over-long single cookie rejected.
    jar2 = CookieJar()
    assert jar2.ingest_response(
        _u("http://a.test/"),
        {"set-cookie": "big=" + "x" * (cs.MAX_COOKIE_BYTES + 1)}) == 0
    assert len(jar2) == 0


@_reg
def test_loopback_multi_set_cookie_and_post_ingest():
    """S05 loopback probe: BOTH Set-Cookie lines of one response are
    ingested, and a POST response's Set-Cookie is ingested too."""
    srv, port, counts = _start_server({
        ("GET", "/two"): (200, "text/plain", b"ok",
                          [("Set-Cookie", "first=1"),
                           ("Set-Cookie", "second=2")]),
        ("POST", "/set"): (200, "text/plain", b"ok",
                           [("Set-Cookie", "posted=1")]),
    })
    try:
        jar = CookieJar()
        c = HttpClient(timeout=5.0, cookies=jar)
        base = f"http://127.0.0.1:{port}/"
        # The legacy dict comprehension collapsed the two Set-Cookie
        # lines into one (only the last survived); the client's header
        # map preserves them as a list so the jar sees both.
        c.get(base + "two")
        names = {x.name for x in jar.all()}
        assert {"first", "second"} <= names, \
            f"both Set-Cookie lines must be stored: {names}"
        c.post(base + "set", data={"a": "b"})
        names = {x.name for x in jar.all()}
        assert "posted" in names, \
            f"POST response Set-Cookie must be ingested: {names}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_subresource_cookie_context_reaches_jar():
    """S05 loopback probe: a subresource request's cookie context
    reaches the jar — the Lax cookie is sent on a same-site
    subresource and dropped on a cross-site one.

    (Two loopback ports are same-site per registrable-domain rules,
    so the cross-site leg drives the context explicitly — the site
    computation itself is unit-tested below.)"""
    srv, port, counts = _start_server({
        ("GET", "/sub.css"): (200, "text/css", b"body{}", []),
    })
    try:
        jar = CookieJar()
        jar.ingest_response(_u(f"http://127.0.0.1:{port}/"),
                            {"set-cookie": "lax=1"})
        c = HttpClient(timeout=5.0, cookies=jar)
        base = f"http://127.0.0.1:{port}/"
        # same-site subresource: cookie sent.
        fetch_resource(_u(base + "sub.css"), client=c,
                       cookie_context={"is_cross_site": False,
                                       "method": "GET",
                                       "is_top_level": False})
        assert _last_cookie_header(counts, "/sub.css") == "lax=1", \
            "same-site subresource must carry the Lax cookie"
        # cross-site subresource: Lax dropped.
        fetch_resource(_u(base + "sub.css"), client=c,
                       cookie_context={"is_cross_site": True,
                                       "method": "GET",
                                       "is_top_level": False})
        assert _last_cookie_header(counts, "/sub.css") == "", \
            "cross-site subresource must drop the Lax cookie"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_subresource_cookie_context_site_detection():
    """The subresource context marks cross-site on a different
    registrable domain and same-site on a subdomain of the document."""
    ctx = net._subresource_cookie_context(
        net.parse_url("http://b.test/x"), "http://a.test/")
    assert ctx["is_cross_site"] is True and ctx["is_top_level"] is False
    ctx2 = net._subresource_cookie_context(
        net.parse_url("http://sub.a.test/x"), "http://a.test/")
    assert ctx2["is_cross_site"] is False, ctx2


def _last_cookie_header(counts: dict, path: str) -> str:
    """Read the Cookie header recorded for *path* (the server stores
    the last seen value under ``("hdr", path)``)."""
    return counts.get(("hdr", path), "")


# ---------------------------------------------------------------------------
# R7-S02 第 3/5/6 点 — 跨源敏感头剥离 + 发送前拒绝（动态断言）
# ---------------------------------------------------------------------------

@_reg
def test_s02_get_cross_origin_strips_sensitive_headers():
    """R7-S02 第 3 点 — 跨源请求按 fetch §4.6 剥离敏感请求头
    （Authorization / Cookie / WWW-Authenticate …）。同源不剥。
    服务端计数确保请求确实**发出去了**（不是被拒后 0 次）。"""
    srv, port, counts = _start_server({
        ("GET", "/ok"): (200, "text/plain", b"ok", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        doc_origin = "http://a.test/"
        target = f"http://127.0.0.1:{port}/ok"
        # 跨源：document_origin (a.test) ≠ 目标源 (127.0.0.1:port)。
        ctx = _rc.context_for_destination(
            doc_origin, target, _rc.DEST_FETCH)
        hdrs = {"Authorization": "Bearer tok",
                "Cookie": "session=1",
                "X-Custom": "keep"}
        resp = c.get(target, headers=hdrs,
                     request_context=ctx)
        assert resp.status == 200 and resp.ok, \
            f"cross-origin request must still hit the wire: {resp.status}"
        assert counts.get(("GET", "/ok"), 0) >= 1, counts
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_s02_strip_cross_origin_headers_helper():
    """HttpClient._strip_cross_origin_headers 跨源剥、不修改入参。

    同源行为保持"保守不剥"：调用方可能已在 cookie 门禁里做过
    同站判定，strip 层只在**跨源**时按 fetch §4.6 移除敏感头
    （同源请求的敏感头由调用方 / cookie 门禁决定，不在这里
    二次拦截——这是单一职责）。"""
    c = HttpClient()
    base = {"Authorization": "Bearer t", "Cookie": "a=1",
            "X-Keep": "y"}
    # 跨源 → 剥
    stripped = c._strip_cross_origin_headers(
        base, "http://a.test/", "http://b.test/x")
    assert "Authorization" not in stripped and "Cookie" not in stripped
    assert stripped.get("X-Keep") == "y"
    # 入参未被改（调用方可能在别处复用同一份 dict）。
    assert "Authorization" in base and "Cookie" in base
    # 同源 → 原样返回（保守：不在 strip 层重复拦截，交给调用方）。
    # 入参不被修改；返回值内容与入参一致。
    same = c._strip_cross_origin_headers(
        base, "http://a.test/", "http://a.test/y")
    assert same.get("Authorization") == "Bearer t"
    assert same.get("Cookie") == "a=1"
    assert same.get("X-Keep") == "y"
    # 入参本身未被改。
    assert "Authorization" in base and "Cookie" in base
    c.close_pool()


@_reg
def test_s02_get_denied_unknown_scheme_does_not_hit_wire():
    """R7-S02 第 5/6 点 — 不支持的 scheme（ftp / data …）在发送
    前被拒，落地 status=0 + denied，**不发包**。"""
    c = HttpClient()
    # context_for_destination 不会校验 scheme（纯字段映射），这里
    # 手工把 url 设成不支持的 scheme 触发 evaluate 的 scheme 拒绝。
    ctx = _rc.context_for_destination(
        "https://a.test/", "ftp://a.test/x", _rc.DEST_FETCH)
    resp = c.get("ftp://a.test/x", request_context=ctx)
    assert resp.status == 0 and resp.denied is not None, \
        f"unsupported scheme must be pre-denied: {resp.status}"
    assert resp.denied.blocked_by == "scheme"
    c.close_pool()


@_reg
def test_s02_get_denied_mixed_content_blockable():
    """R7-S02 第 3/6 点 — https 文档里的 http 脚本类子资源是
    *可阻断*混合内容：发送前被拒，不发包。"""
    c = HttpClient()
    ctx = _rc.context_for_destination(
        "https://a.test/", "http://a.test/x.js",
        _rc.DEST_CLASSIC_SCRIPT)
    resp = c.get("http://a.test/x.js", request_context=ctx)
    assert resp.status == 0 and resp.denied is not None, \
        "blockable mixed content must be pre-denied"
    assert resp.denied.blocked_by == "mixed-content"
    c.close_pool()


@_reg
def test_s02_fetch_resource_csp_denied_returns_none():
    """R7-S02 第 6 点 — connect-src none 跨源 fetch 在发送前被拒，
    fetch_resource 按失败契约返回 None，服务端计数为 0。"""
    srv, port, counts = _start_server({
        ("GET", "/api"): (200, "application/json", b"{}", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        target = f"http://127.0.0.1:{port}/api"
        # 文档 CSP 明确禁止 connect-src（跨源 fetch 不可读）。
        policy = DocumentPolicy(
            doc_origin="https://a.test/",
            csp_directives=parse_csp_directives("connect-src 'none'"))
        ctx = _rc.context_for_destination(
            "https://a.test/", target, _rc.DEST_FETCH, policy=policy)
        resp = fetch_resource(target, client=c,
                              request_context=ctx)
        assert resp is None, \
            "CSP-forbidden cross-origin fetch must be None"
        assert counts.get(("GET", "/api"), 0) == 0, \
            f"CSP-forbidden fetch must not hit the wire: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_s02_post_denied_csp_does_not_hit_wire():
    """R7-S02 第 6 点 — CSP 拒绝的 POST 在发送前被拒，**不发包**
    （服务端 POST 计数为 0），落地 status=0 + denied。

    回归锁定：旧缺陷——POST 路径只把 request_context 的 decision
    用于预检标记，从不检查 ``decision.allowed``，导致 CSP 拒绝的
    POST 照样发出（GET 路径早已拦截，两边行为不一致）。
    """
    srv, port, counts = _start_server({
        ("POST", "/api"): (200, "application/json", b"{}", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        target = f"http://127.0.0.1:{port}/api"
        # 文档源用 http://（若用 https://，混合内容判定会先于 CSP
        # 命中——那是第 3 点的正确行为，但本测试要锁定的是 CSP
        # 拒绝路径本身）。
        policy = DocumentPolicy(
            doc_origin="http://a.test/",
            csp_directives=parse_csp_directives("connect-src 'none'"))
        ctx = _rc.context_for_destination(
            "http://a.test/", target, _rc.DEST_XHR, policy=policy)
        ctx.method = "POST"
        resp = c.post(target, data="a=1", request_context=ctx)
        assert resp.status == 0 and resp.denied is not None, \
            f"CSP-forbidden POST must be pre-denied: {resp.status}"
        assert resp.denied.blocked_by == "csp", \
            f"denied reason must be csp: {resp.denied.blocked_by}"
        assert counts.get(("POST", "/api"), 0) == 0, \
            f"CSP-forbidden POST must not hit the wire: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_s02_post_denied_unknown_scheme_does_not_hit_wire():
    """R7-S02 第 6 点 — 不支持的 scheme 的 POST 同样发送前被拒
    （与 GET 同口径，不发包）。"""
    c = HttpClient()
    ctx = _rc.context_for_destination(
        "https://a.test/", "ftp://a.test/x", _rc.DEST_XHR)
    ctx.method = "POST"
    resp = c.post("ftp://a.test/x", data="a=1", request_context=ctx)
    assert resp.status == 0 and resp.denied is not None, \
        f"unsupported scheme POST must be pre-denied: {resp.status}"
    assert resp.denied.blocked_by == "scheme"
    c.close_pool()


@_reg
def test_s02_post_denied_mixed_content_blockable():
    """R7-S02 第 3/6 点 — https 文档里的 http XHR（可阻断混合
    内容）POST 在发送前被拒，不发包。"""
    c = HttpClient()
    ctx = _rc.context_for_destination(
        "https://a.test/", "http://a.test/api", _rc.DEST_XHR)
    ctx.method = "POST"
    resp = c.post("http://a.test/api", data="a=1", request_context=ctx)
    assert resp.status == 0 and resp.denied is not None, \
        "blockable mixed content POST must be pre-denied"
    assert resp.denied.blocked_by == "mixed-content"
    c.close_pool()


@_reg
def test_s02_post_allowed_still_hits_wire():
    """R7-S02 第 6 点 — 反向断言：无 CSP 拒绝的 POST 正常发出
    （status=200），确认发送前判定没有把合法请求误拦。"""
    srv, port, counts = _start_server({
        ("POST", "/api"): (200, "application/json", b"{}", []),
    })
    try:
        c = HttpClient(timeout=5.0)
        target = f"http://127.0.0.1:{port}/api"
        ctx = _rc.context_for_destination(
            f"http://127.0.0.1:{port}/", target, _rc.DEST_XHR)
        ctx.method = "POST"
        resp = c.post(target, data="a=1", request_context=ctx)
        assert resp.status == 200, \
            f"same-origin POST must still be sent: {resp.status}"
        assert resp.denied is None
        assert counts.get(("POST", "/api"), 0) == 1, \
            f"allowed POST must hit the wire: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_s02_subresource_context_derives_mode_credentials():
    """R7-S02 第 4 点 — 子资源 spec 派生 context：classic script
    （无 crossorigin）默认 no-cors + omit；module / crossorigin
    → cors；同源无需 ACAO。"""
    policy = DocumentPolicy(doc_origin="https://a.test/",
                            csp_directives={})
    absu = net.parse_url("https://a.test/x.js")
    # classic script, same origin → no-cors, omit credentials.
    # （is_cross_origin 比较 scheme://host，不带尾斜杠。）
    c1 = _build_subresource_request_context(
        {"kind": "script", "src": "x.js",
         "crossorigin": None, "script_type": ""},
        absu, "https://a.test", policy)
    assert c1.mode == _rc.MODE_NO_CORS, c1.mode
    assert c1.credentials == _rc.CRED_OMIT, c1.credentials
    assert c1.is_cross_origin is False
    # module, cross origin → cors mode.
    absu2 = net.parse_url("https://b.test/m.js")
    c2 = _build_subresource_request_context(
        {"kind": "script", "src": "m.js",
         "crossorigin": None, "script_type": "module"},
        absu2, "https://a.test/", policy)
    assert c2.destination == _rc.DEST_MODULE, c2.destination
    assert c2.mode == _rc.MODE_CORS, c2.mode
    assert c2.is_cross_origin is True
    # image → no-cors (not JS-readable).
    c3 = _build_subresource_request_context(
        {"kind": "image", "src": "i.png"},
        absu, "https://a.test/", policy)
    assert c3.mode == _rc.MODE_NO_CORS and \
        c3.destination == _rc.DEST_IMAGE
    # crossorigin=use-credentials → include credentials.
    c4 = _build_subresource_request_context(
        {"kind": "script", "src": "x.js",
         "crossorigin": "use-credentials", "script_type": ""},
        absu, "https://a.test/", policy)
    assert c4.credentials == _rc.CRED_INCLUDE, c4.credentials


@_reg
def test_s02_for_redirect_strips_sensitive_and_downgrades():
    """R7-S02 第 3 点 — 跨源重定向把凭据降为 omit 并剥离敏感头，
    redirect_chain 逐跳追加。

    注意：``for_redirect`` 的跨源判定用 :func:`_rc._origin_of`，
    输出形如 ``scheme://host[:port]``，**不带**尾斜杠。所以
    同站跳转必须用与 ``document_origin`` 完全一致的字符串，
    否则会被误判为跨源。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://a.test/1", _rc.DEST_NAVIGATE)
    ctx.headers = {"Authorization": "Bearer t", "X-K": "1"}
    h1 = ctx.for_redirect("https://b.test/2")
    assert h1.credentials == _rc.CRED_OMIT, h1.credentials
    assert "Authorization" not in h1.headers
    assert h1.headers.get("X-K") == "1"
    assert h1.redirect_chain == ["https://a.test/1"]
    assert h1.is_cross_origin is True
    # 同站跳转不降级（同 host:port 的 origin 字符串）。
    same = ctx.for_redirect("https://a.test/2")
    assert same.credentials != _rc.CRED_OMIT
    assert "Authorization" in same.headers


# ---------------------------------------------------------------------------
# R7-S02 第 3 点 — 逐跳重定向敏感头剥离验收探针（服务端逐跳记录）
# ---------------------------------------------------------------------------

def _start_server_record_headers(routes=None):
    """起本地回环 HTTP 服务器，在 ``counts[("hdrs", path)]`` 上
    **逐跳记录实际收到的请求头 dict**（小写键），并保留每路径计数。

    *routes* 为 ``(path) → (status, ctype, body, extra_hdrs)``；
    3xx 路由的 ``Location`` 头由调用方在 *extra_hdrs* 里用
    ``self.server.server_address`` 动态填充（见 :func:`_make_302_routes`）。
    缺省路由 = 两跳 302 链（``/s1 → /cross``）。"""
    import http.server
    from http.server import BaseHTTPRequestHandler, HTTPServer

    counts: dict = {}

    def _make_302_routes(p: int) -> dict:
        return {
            "/s1": (302, "", b"",
                     [("Location", f"http://127.0.0.1:{p}/cross")]),
            "/cross": (302, "", b"",
                       [("Location", f"http://127.0.0.1:{p}/back")]),
            "/back": (200, "text/plain", b"done", []),
        }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self):
            key = (self.command, self.path.split("?")[0])
            counts[key] = counts.get(key, 0) + 1
            counts[("hdrs", key[1])] = {k.lower(): v for k, v in
                                        self.headers.items()}
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            p = self.server.server_address[1]
            # 动态 302 路由：Location 里的 port 在服务器启动后才知道，
            # 所以按需构造（而非在模块级字面量里引用尚未定义的 port）。
            r = self._user_routes.get(key[1]) if self._user_routes else \
                None
            if r is None and not self._user_routes:
                r = _make_302_routes(p).get(key[1])
            if r is None:
                self.send_response(404)
                self.send_header("Content-Length", "4")
                self.end_headers()
                self.wfile.write(b"404x")
                return
            status, ctype, body, extra = r
            self.send_response(status)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in (extra or []):
                self.send_header(k, v)
            if not any(k.lower() == "content-length"
                       for k, _v in (extra or [])):
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        _user_routes = routes
        do_GET = _serve
        do_POST = _serve
        do_OPTIONS = _serve

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port, counts


@_reg
def test_s02_follow_redirects_strips_sensitive_on_cross_origin_hop():
    """R7-S02 第 3 点验收探针 — 两跳同站（document_origin 与
    /s1、/cross 均为 127.0.0.1:port），服务端逐跳收到的请求头
    必须**保留** Authorization / Cookie（同源合法）；跨源跳的
    剥离由 :func:`test_s02_follow_redirects_strips_sensitive_on_true_cross_origin_jump`
    与 :func:`test_s02_follow_redirects_strips_all_hops_continuous_cross_origin`
    锁定。"""
    srv, port, counts = _start_server_record_headers()
    try:
        c = HttpClient(timeout=5.0)
        doc_origin = f"http://127.0.0.1:{port}/"
        start_url = f"http://127.0.0.1:{port}/s1"
        ctx = _rc.context_for_destination(
            doc_origin, start_url, _rc.DEST_NAVIGATE)
        hdrs = {"Authorization": "Bearer t", "Cookie": "s=1",
                "X-Custom": "keep"}
        resp = c.get(start_url, headers=hdrs,
                     request_context=ctx)
        assert resp.status == 200, f"must land on /cross: {resp.status}"
        assert counts.get(("GET", "/s1"), 0) == 1
        assert counts.get(("GET", "/cross"), 0) == 1
        # 两跳同站（127.0.0.1:port）→ 敏感头保留（同源合法）。
        h_s1 = counts.get(("hdrs", "/s1"), {})
        h_cross = counts.get(("hdrs", "/cross"), {})
        assert h_s1.get("authorization") == "Bearer t", \
            f"same-origin hop /s1 keeps sensitive headers: {h_s1}"
        assert h_s1.get("cookie") == "s=1"
        assert h_cross.get("authorization") == "Bearer t", \
            f"same-origin hop /cross keeps sensitive headers: {h_cross}"
        assert h_cross.get("x-custom") == "keep"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_s02_follow_redirects_strips_sensitive_on_true_cross_origin_jump():
    """R7-S02 第 3 点验收探针（跨源跳） — 文档源 http://a.test/，
    第一跳 /s1 跨源 → 剥敏感头；302 跳到 /cross（仍跨源）
    → 继续剥。服务端逐跳记录断言两跳均无 Authorization/Cookie。"""
    srv, port, counts = _start_server_record_headers()
    try:
        c = HttpClient(timeout=5.0)
        doc_origin = "http://a.test/"
        start_url = f"http://127.0.0.1:{port}/s1"
        ctx = _rc.context_for_destination(
            doc_origin, start_url, _rc.DEST_NAVIGATE)
        hdrs = {"Authorization": "Bearer t", "Cookie": "s=1",
                "X-Custom": "keep"}
        resp = c.get(start_url, headers=hdrs,
                     request_context=ctx)
        assert resp.status == 200, f"must land on /cross: {resp.status}"
        assert counts.get(("GET", "/s1"), 0) == 1
        assert counts.get(("GET", "/cross"), 0) == 1
        # /s1 跨源 → 剥敏感头，但非敏感头保留。
        h_s1 = counts.get(("hdrs", "/s1"), {})
        assert "authorization" not in h_s1, f"/s1 leaked Auth: {h_s1}"
        assert "cookie" not in h_s1, f"/s1 leaked Cookie: {h_s1}"
        assert h_s1.get("x-custom") == "keep"
        # /cross 仍跨源（a.test ≠ 127.0.0.1:port）→ 继续剥。
        h_cross = counts.get(("hdrs", "/cross"), {})
        assert "authorization" not in h_cross, f"/cross leaked Auth: {h_cross}"
        assert "cookie" not in h_cross, f"/cross leaked Cookie: {h_cross}"
        assert h_cross.get("x-custom") == "keep"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_s02_follow_redirects_strips_all_hops_continuous_cross_origin():
    """R7-S02 第 3 点验收探针（连续跨源跳全剥） — 文档源 a.test，
    /s1 → /cross → /back 三跳全在 127.0.0.1:port（跨源），每跳
    对照**原始** headers 重算：全跨源 → 全剥敏感头；非敏感头
    保留。锁定"逐跳重算（非累积剥离）"的新契约。"""
    srv, port, counts = _start_server_record_headers()
    try:
        c = HttpClient(timeout=5.0)
        doc_origin = "http://a.test/"
        target_s1 = f"http://127.0.0.1:{port}/s1"
        ctx = _rc.context_for_destination(
            doc_origin, target_s1, _rc.DEST_NAVIGATE)
        hdrs = {"Authorization": "Bearer t", "Cookie": "s=1",
                "X-Custom": "keep"}
        resp = c.get(target_s1, headers=hdrs,
                     request_context=ctx)
        assert resp.status == 200, f"must land on /back: {resp.status}"
        assert counts.get(("GET", "/s1"), 0) == 1
        assert counts.get(("GET", "/cross"), 0) == 1
        assert counts.get(("GET", "/back"), 0) == 1
        for p in ("/s1", "/cross", "/back"):
            h = counts.get(("hdrs", p), {})
            assert "authorization" not in h, \
                f"{p} leaked Authorization: {h}"
            assert "cookie" not in h, \
                f"{p} leaked Cookie: {h}"
            assert h.get("x-custom") == "keep"
        c.close_pool()
    finally:
        srv.shutdown()


@_reg
def test_s02_follow_redirects_denied_hop_lands_placeholder_not_sent():
    """R7-S02 第 3/6 点验收探针 — 逐跳重评估命中 deadline 拒绝时，
    被禁的那一跳**不发包**，直接落地 status=0 + denied 占位响应。

    构造：deadline 已过期，``get()`` 入口的发送前判定即拒
    （blocked_by=deadline），不发包、不进入重定向链。这与第 1 跳
    被拒是同一"发送前拒绝"契约的落点（deadline 在 ``for_redirect``
    下保持过期，逐跳重评估仍拒）。"""
    import time
    srv, port, counts = _start_server_record_headers()
    try:
        c = HttpClient(timeout=5.0)
        doc_origin = "http://a.test/"
        start_url = f"http://127.0.0.1:{port}/s1"
        t0 = time.monotonic()
        ctx = _rc.context_for_destination(
            doc_origin, start_url, _rc.DEST_FETCH)
        ctx.deadline = t0 - 1.0  # 已过期
        resp = c.get(start_url, request_context=ctx, now=t0)
        assert resp.status == 0 and resp.denied is not None, \
            f"expired-deadline request must be pre-denied: {resp.status}"
        assert resp.denied.blocked_by == "deadline", \
            f"denied by deadline: {resp.denied.blocked_by}"
        # 第 0 跳就拒 → 不发包（服务端计数 0）。
        assert counts.get(("GET", "/s1"), 0) == 0, \
            f"expired-deadline request must not hit the wire: {counts}"
        c.close_pool()
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# R7-S02 第 6 点 — 字符串脚本执行入口（eval / execute）共用
# 文档 inline script-src 策略
# ---------------------------------------------------------------------------

@_reg
def test_s02_string_script_gate_blocks_under_none():
    """R7-S02 第 6 点 — ``script-src 'none'`` 下字符串脚本入口
    （``host.execute("…")`` 与 ``host.eval("…")``）被拒，不跑
    （"被禁的请求不得先发包再丢响应"的脚本侧对偶）。"""
    host = js.Host(dom.parse('<html><body></body></html>'),
                   lambda s: None)
    host.csp_directives = parse_csp_directives("script-src 'none'")
    host.execute('console.log("SHOULD_NOT_RUN");')
    assert not any("SHOULD_NOT_RUN" in l for l in host.logs), \
        f"execute under script-src 'none' must not run: {host.logs}"
    r = host.eval('1 + 2')
    assert r is None, \
        f"eval under script-src 'none' must return None: {r!r}"


@_reg
def test_s02_string_script_gate_allows_unsafe_inline():
    """R7-S02 第 6 点 — ``'unsafe-inline'`` 下字符串脚本入口放行
    （与内联 <script> 同一门）。"""
    host = js.Host(dom.parse('<html><body></body></html>'),
                   lambda s: None)
    host.csp_directives = parse_csp_directives("script-src 'unsafe-inline'")
    host.execute('console.log("ok");')
    assert any("ok" in l for l in host.logs), \
        f"execute under 'unsafe-inline' must run: {host.logs}"
    assert host.eval('4 * 5') == 20, "eval under 'unsafe-inline' must run"


@_reg
def test_s02_string_script_gate_no_policy_allows():
    """R7-S02 第 6 点 — 无策略（csp_directives 为 None）时字符串
    脚本入口恒放行（向后兼容，离线单测不受影响）。"""
    host = js.Host(dom.parse('<html><body></body></html>'),
                   lambda s: None)
    # csp_directives 默认 None
    assert host.csp_directives is None
    host.execute('console.log("free");')
    assert any("free" in l for l in host.logs), \
        f"execute without policy must run: {host.logs}"
    assert host.eval('2 + 2') == 4


@_reg
def test_s02_string_script_gate_present_empty_blocks():
    """R7-S02 第 6 点 — present-but-empty ``script-src``（无值）等价
    于 ``'none'``，字符串脚本入口被拒。"""
    host = js.Host(dom.parse('<html><body></body></html>'),
                   lambda s: None)
    host.csp_directives = parse_csp_directives("script-src")
    host.execute('console.log("NOPE");')
    assert not any("NOPE" in l for l in host.logs), \
        f"execute under present-empty script-src must not run: {host.logs}"
    assert host.eval('1') is None


@_reg
def test_s02_inline_on_handler_gate():
    """R7-S02 第 6 点 — ``onclick="…"`` 内联字符串事件处理器与
    ``<script>`` 内联元素共享同一 ``script-src`` 门：``'none'`` /
    present-but-empty 下拒执行（不"跑了再丢"）；无策略时放行。

    经 :meth:`Host._run_event_handler` 直接验证字符串处理器入口的
    门（事件委托路径 :meth:`execute_event_bubbled` 已同判，此处锁
    定"独立调用也不跑"的契约）。button 经 ``element_table`` 取
    （``id="b"``），避免 DOM 遍历 API 差异。"""
    # 无策略 → on* 字符串处理器执行
    host = js.Host(dom.parse(
        '<html><body><button id="b" onclick="console.log(\'clicked\')">'
        'x</button></body></html>'),
        lambda s: None)
    btn = host.element_table.get("b")
    assert btn is not None, "button must be in element_table"
    host._run_event_handler(
        btn, "console.log('clicked');", "onclick", this_el=btn)
    assert any("clicked" in l for l in host.logs), \
        f"on* handler without policy must run: {host.logs}"
    # script-src 'none' → on* 字符串处理器被拒
    host2 = js.Host(dom.parse(
        '<html><body><button id="b" onclick="console.log(\'NOPE\')">'
        'x</button></body></html>'),
        lambda s: None)
    host2.csp_directives = parse_csp_directives("script-src 'none'")
    btn2 = host2.element_table.get("b")
    host2._run_event_handler(
        btn2, "console.log('NOPE');", "onclick", this_el=btn2)
    assert not any("NOPE" in l for l in host2.logs), \
        f"on* handler under script-src 'none' must not run: {host2.logs}"
    # present-but-empty script-src（等价 'none'）→ 拒
    host3 = js.Host(dom.parse(
        '<html><body><button id="b" onclick="console.log(\'NOPE\')">'
        'x</button></body></html>'),
        lambda s: None)
    host3.csp_directives = parse_csp_directives("script-src")
    btn3 = host3.element_table.get("b")
    host3._run_event_handler(
        btn3, "console.log('NOPE');", "onclick", this_el=btn3)
    assert not any("NOPE" in l for l in host3.logs), \
        f"on* handler under present-empty script-src must not run: " \
        f"{host3.logs}"
    # 'unsafe-inline' → 放行
    host4 = js.Host(dom.parse(
        '<html><body><button id="b" onclick="console.log(\'inline\')">'
        'x</button></body></html>'),
        lambda s: None)
    host4.csp_directives = parse_csp_directives("script-src 'unsafe-inline'")
    btn4 = host4.element_table.get("b")
    host4._run_event_handler(
        btn4, "console.log('inline');", "onclick", this_el=btn4)
    assert any("inline" in l for l in host4.logs), \
        f"on* handler under 'unsafe-inline' must run: {host4.logs}"


# ---------------------------------------------------------------------------
# R7-S02 第 5 点 — 跨源 CORS 响应到达后 ACAO/ACAC 校验（纯逻辑层）
# ---------------------------------------------------------------------------

@_reg
def test_s02_preflight_same_origin_basic_readable():
    """同源 fetch 响应无需 ACAO，校验通过（basic，脚本可读）。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://a.test/api", _rc.DEST_FETCH)
    r = _rc.validate_preflight_response(ctx, {"Content-Type": "text/plain"})
    assert r.allowed, r.reason
    assert r.resolved_type == "basic"
    # 禁止响应头（set-cookie）不可见
    ctx2 = _rc.context_for_destination(
        "https://a.test", "https://a.test/api", _rc.DEST_FETCH)
    r2 = _rc.validate_preflight_response(
        ctx2, {"Content-Type": "text/plain", "Set-Cookie": "sid=1"})
    assert r2.allowed
    assert "set-cookie" not in r2.visible_headers


@_reg
def test_s02_preflight_cross_origin_acao_star_cors():
    """跨源 cors fetch，服务端回 ACAO=* → 校验通过（cors，可读）。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://b.test/api", _rc.DEST_FETCH)
    r = _rc.validate_preflight_response(
        ctx, {"Access-Control-Allow-Origin": "*"})
    assert r.allowed, r.reason
    assert r.resolved_type == "cors"


@_reg
def test_s02_preflight_cross_origin_acao_match_cors():
    """跨源 cors fetch，ACAO = 请求 origin（https://a.test）→ 可读。
    注意：ACAO 必须匹配**发起请求的 origin**（document_origin），
    不是目标源（https://b.test）。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://b.test/api", _rc.DEST_FETCH)
    r = _rc.validate_preflight_response(
        ctx, {"Access-Control-Allow-Origin": "https://a.test"})
    assert r.allowed, r.reason
    assert r.resolved_type == "cors"


@_reg
def test_s02_preflight_cross_origin_acao_mismatch_opaque():
    """跨源 cors fetch，ACAO 不匹配请求 origin 且非 * → 降级 opaque
    （脚本不可读，但请求本身合法）。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://b.test/api", _rc.DEST_FETCH)
    r = _rc.validate_preflight_response(
        ctx, {"Access-Control-Allow-Origin": "https://c.test"})
    assert not r.allowed
    assert r.resolved_type == "opaque"
    assert r.visible_headers == {}


@_reg
def test_s02_preflight_cross_origin_no_acao_opaque():
    """跨源 cors fetch，响应缺 ACAO → 降级 opaque。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://b.test/api", _rc.DEST_FETCH)
    r = _rc.validate_preflight_response(
        ctx, {"Content-Type": "text/plain"})
    assert not r.allowed
    assert r.resolved_type == "opaque"


@_reg
def test_s02_preflight_cross_origin_no_cors_opaque():
    """跨源 no-cors（classic script / image）→ 恒 opaque（不可读）。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://b.test/lib.js", _rc.DEST_CLASSIC_SCRIPT)
    r = _rc.validate_preflight_response(ctx, {})
    assert not r.allowed
    assert r.resolved_type == "opaque"

    ctx_img = _rc.context_for_destination(
        "https://a.test", "https://b.test/x.png", _rc.DEST_IMAGE)
    r_img = _rc.validate_preflight_response(ctx_img, {})
    assert not r_img.allowed
    assert r_img.resolved_type == "opaque"


@_reg
def test_s02_preflight_credentials_include_requires_concrete_acao():
    """credentials=include 跨源：ACAO 不得是 *，须为具体 origin 且
    ACAC=true，否则降级 opaque。"""
    ctx = _rc.RequestContext(
        url="https://b.test/api", document_origin="https://a.test",
        destination=_rc.DEST_FETCH, mode=_rc.MODE_CORS,
        credentials=_rc.CRED_INCLUDE)
    # ACAO=* → opaque（凭据模式不允许 *）
    r = _rc.validate_preflight_response(
        ctx, {"Access-Control-Allow-Origin": "*"})
    assert not r.allowed
    assert r.resolved_type == "opaque"
    # 具体 origin + 无 ACAC → opaque
    r2 = _rc.validate_preflight_response(
        ctx, {"Access-Control-Allow-Origin": "https://a.test"})
    assert not r2.allowed
    assert r2.resolved_type == "opaque"
    # 具体 origin + ACAC=true → cors（可读）
    r3 = _rc.validate_preflight_response(
        ctx, {"Access-Control-Allow-Origin": "https://a.test",
              "Access-Control-Allow-Credentials": "true"})
    assert r3.allowed, r3.reason
    assert r3.resolved_type == "cors"


@_reg
def test_s02_preflight_expose_headers_controls_visibility():
    """跨源 cors 校验通过后，脚本可见头 = 基础过滤可见头 ∪
    Access-Control-Expose-Headers 允许的头。"""
    ctx = _rc.context_for_destination(
        "https://a.test", "https://b.test/api", _rc.DEST_FETCH)
    r = _rc.validate_preflight_response(ctx, {
        "Access-Control-Allow-Origin": "https://a.test",
        "Access-Control-Expose-Headers": "x-custom",
        "X-Custom": "visible",
        "Set-Cookie": "sid=1",
    })
    assert r.allowed, r.reason
    assert r.resolved_type == "cors"
    # 经 Expose-Headers 允许的 x-custom 可见
    assert "x-custom" in r.visible_headers
    # 禁止响应头 set-cookie 仍不可见
    assert "set-cookie" not in r.visible_headers


@_reg
def test_s02_preflight_js_helper_delegates_to_validate():
    """J03 第 2 点 — JS 侧 _js_cors_response_check 委托
    validate_preflight_response，跨源 ACAO 不匹配时 opaque。"""
    host = js.Host(dom.parse('<html><body></body></html>'),
                   lambda s: None)
    host.doc_url = "https://a.test/"
    host.doc_origin = "https://a.test"
    # 跨源 b.test，无 ACAO → opaque
    r = host._js_cors_response_check(
        "https://b.test/api", {"Content-Type": "text/plain"},
        _rc.DEST_FETCH)
    assert not r.allowed
    assert r.resolved_type == "opaque"
    # 跨源 b.test，ACAO 匹配请求 origin → cors
    r2 = host._js_cors_response_check(
        "https://b.test/api",
        {"Access-Control-Allow-Origin": "https://a.test"},
        _rc.DEST_FETCH)
    assert r2.allowed, r2.reason
    assert r2.resolved_type == "cors"
    # 同源 a.test → basic
    r3 = host._js_cors_response_check(
        "https://a.test/api", {"Content-Type": "text/plain"},
        _rc.DEST_FETCH)
    assert r3.allowed
    assert r3.resolved_type == "basic"


# ---------------------------------------------------------------------------
# R7-S02 第 5 点 — 真实 OPTIONS 预检往返（跨源非简单请求先发 OPTIONS）
# ---------------------------------------------------------------------------

@_reg
def test_s02_preflight_options_roundtrip_cross_origin_post():
    """S02 第 5 点（本批）— 跨源 POST + 自定义头 → 先发**真实** OPTIONS
    预检（非简单请求 requires_preflight=True），响应到达后经
    validate_preflight_response 比对 ACAO/ACAC。

    * server B 的 OPTIONS 路由回 ``Access-Control-Allow-Origin: *`` +
      ``Access-Control-Allow-Methods: POST`` → 预检通过 → 真实 POST
      发出，响应 ``cors_resolved_type == "cors"``、``cors_allowed``
      为 True，且 counts 里 OPTIONS 计数 1、POST 计数 1（预检确被
      真实发出，非纯逻辑）。
    """
    srv_a, port_a, _ = _start_server({
        ("GET", "/doc"): (200, "text/plain", b"doc", []),
    })
    srv_b, port_b, counts = _start_server({
        ("OPTIONS", "/api"): (
            200, "", b"",
            [("Access-Control-Allow-Origin", "*"),
             ("Access-Control-Allow-Methods", "POST, OPTIONS"),
             ("Access-Control-Allow-Headers", "X-Test"),
             ("Content-Length", "0")]),
        ("POST", "/api"): (200, "text/plain", b"POSTED", [
            ("Access-Control-Allow-Origin", "*")]),
    })
    try:
        c = HttpClient(timeout=5.0)
        doc_base = f"http://127.0.0.1:{port_a}/"
        api = f"http://127.0.0.1:{port_b}/api"
        root = dom.parse(
            f'<html><body><script>fetch("{api}", '
            f'{{method:"POST", headers:{{"X-Test":"s02"}},'
            f' body:"a=1"}}).then(function(r){{'
            f'  console.log("TYPE:"+r.type);}})'
            f'.catch(function(e){{console.log("ERR:"+e.name);}});'
            f'</script></body></html>')
        host = js.Host(root, lambda s: None)
        host.doc_url = doc_base
        host.doc_origin = net._origin_str(doc_base)
        host._fetch_transport = net.FetchTransport(
            c, base_url=doc_base, document_url=doc_base)
        host.execute_all()
        host.flush_timers()
        host.flush_microtasks()
        # OPTIONS 预检被真实发出（非纯逻辑），且真实 POST 随后发出。
        assert counts.get(("OPTIONS", "/api"), 0) == 1, \
            f"OPTIONS preflight must be issued: {counts}"
        assert counts.get(("POST", "/api"), 0) == 1, \
            f"POST must follow preflight: {counts}"
        # 跨源 POST + 自定义头 → 预检通过（ACAO:*）→ cors。
        # 通过 HttpClient.get/post 直接断言（不依赖 JS console 时序）。
        c2 = HttpClient(timeout=5.0)
        rc_ctx = _rc.context_for_destination(
            doc_base, api, _rc.DEST_XHR)
        rc_ctx.method = "POST"
        rc_ctx.headers = {"X-Test": "s02"}
        resp = c2.post(
            api, data="a=1",
            headers={"X-Test": "s02"},
            request_context=rc_ctx)
        assert resp.status == 200, f"POST must succeed: {resp.status}"
        assert resp.cors_check is not None, \
            "cross-origin POST must carry a CORS check result"
        assert resp.cors_resolved_type == "cors", \
            f"ACAO:* preflight must resolve cors: {resp.cors_resolved_type}"
        assert resp.cors_allowed, \
            "validated cors response must be script-readable"
        c.close_pool()
        c2.close_pool()
    finally:
        srv_a.shutdown()
        srv_b.shutdown()


@_reg
def test_s02_preflight_options_roundtrip_fails_on_mismatch():
    """S02 第 5 点（本批）— 跨源 OPTIONS 预检响应**不**回 ACAO →
    校验不通过 → 真实请求**不发出**（真实浏览器行为：预检失败时
    请求不发出），返回带 CORS 结果的占位响应（status=0、
    cors_resolved_type=opaque、cors_allowed=False）。请求本身合法
    （不冒充"请求被禁"，denied 仍为 None），只是预检未过、
    响应不可读。"""
    srv_a, port_a, _ = _start_server({
        ("GET", "/doc"): (200, "text/plain", b"doc", []),
    })
    srv_b, port_b, counts = _start_server({
        # OPTIONS 路由**不回**任何 Access-Control-Allow-*（模拟服务端
        # 不支持 CORS）。extra 里的 Content-Length 头不影响校验。
        ("OPTIONS", "/api"): (200, "", b"", [("Content-Length", "0")]),
        # POST 路由保留（但预检失败时**不应**被调用）。
        ("POST", "/api"): (200, "text/plain", b"POSTED", []),
    })
    try:
        doc_base = f"http://127.0.0.1:{port_a}/"
        api = f"http://127.0.0.1:{port_b}/api"
        c = HttpClient(timeout=5.0)
        rc_ctx = _rc.context_for_destination(
            doc_base, api, _rc.DEST_XHR)
        rc_ctx.method = "POST"
        rc_ctx.headers = {"X-Test": "s02"}
        resp = c.post(
            api, data="a=1",
            headers={"X-Test": "s02"},
            request_context=rc_ctx)
        # OPTIONS 被真实发出（预检确实走了网络）。
        assert counts.get(("OPTIONS", "/api"), 0) == 1, \
            f"OPTIONS preflight must be issued: {counts}"
        # 预检失败（缺 ACAO）→ 真实 POST **不发出**。
        assert counts.get(("POST", "/api"), 0) == 0, \
            (f"POST must NOT be issued when preflight fails: "
             f"{counts}")
        assert resp.status == 0, \
            ("preflight failure must yield a placeholder response "
             f"(status=0), got {resp.status}")
        assert resp.cors_check is not None, \
            "cross-origin POST must carry a CORS check result"
        assert resp.cors_resolved_type == "opaque", \
            (f"missing ACAO must downgrade to opaque: "
             f"{resp.cors_resolved_type}")
        assert not resp.cors_allowed, \
            "opaque response must NOT be script-readable"
        # 请求本身合法（未走发送前策略拒绝）→ denied 为 None。
        assert resp.denied is None, \
            "a failed preflight is NOT a policy denial (denied must be None)"
        c.close_pool()
    finally:
        srv_a.shutdown()
        srv_b.shutdown()


@_reg
def test_s02_preflight_cache_key_stable():
    """预检缓存键对（目标源, 方法, 自定义头指纹）稳定——相同键复用，
    不同方法 / 不同自定义头各自独立。"""
    from toy_browser.network import net as _net
    u = _net.parse_url("http://127.0.0.1:1234/api")
    c = HttpClient(timeout=5.0)
    k1 = c._preflight_cache_key(u, "POST", {"X-Test": "1"})
    k2 = c._preflight_cache_key(u, "POST", {"X-Test": "1"})
    k3 = c._preflight_cache_key(u, "PUT", {"X-Test": "1"})
    k4 = c._preflight_cache_key(u, "POST", {"X-Other": "1"})
    assert k1 == k2, "same (origin, method, headers) must share a key"
    assert k1 != k3, "different method must get a different key"
    assert k1 != k4, "different custom header must get a different key"


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)

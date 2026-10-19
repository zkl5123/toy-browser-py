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
from toy_browser.network.cookie_store import CookieJar, parse_set_cookie
from toy_browser.network.net import (
    DocumentPolicy,
    HttpClient,
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

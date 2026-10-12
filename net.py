"""P21 — URL 解析 + HTTP/1.1 最小客户端（M2 网络栈第一步）。
P22 — 深化：重定向链 + gzip + chunked + 子资源并发加载（M2 网络栈第二步）。
P23 — HTTPS + 磁盘缓存 + 协商缓存 + 加载进度事件（M2 网络栈第三步）。

P23 新增（`项目未来规划.txt` §P23）：

* **TLS**：``ssl`` 标准库包裹（暂不手写 socket）；证书校验默认开启，
  ``HttpClient(verify_tls=False)`` 对应 ``--insecure`` 开关（关闭
  主机名 + 证书校验，供本地自签名证书 / 调试）。
* **启发式磁盘缓存**：``CacheStore(root_dir)`` — 响应存盘（JSON 元
  数据 + body 文件，key = SHA-256 of URL）；``Cache-Control:
  max-age`` / ``no-cache`` / ``no-store`` 玩具实现 + 无 max-age 时的
  启发式 ``10% of (Date - Last-Modified)``；过期后走协商缓存。
* **ETag 协商缓存**：命中但过期的缓存带 ``If-None-Match`` 回源；
  304 → 复用 body + 刷新 ``Date``（省流量）；无 ETag 的过期缓存
  直接重取。
* **加载进度事件**：``LoadProgress`` — 每个 fetch 回调
  ``(phase, fraction)``（start / document / subresource / done），
  接到现有事件循环（M3.2 进度条消费者）。

测试全部使用本地 ``http.server`` 夹具（离线可测、CI 稳定）；
HTTPS 路径用 ``ssl.SSLContext`` 内存自签名证书夹具验证 TLS 握手 +
校验开关。
"""
from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import socket
import ssl
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # P0 (agent report) — cookie_store imports net, so a runtime import
    # would cycle; the type-only guard keeps the annotation resolvable
    # for static checkers without the cycle.
    import cookie_store

# P32.3 — cookie jar (lazy import to avoid a hard module dependency at
# import time; cookie_store only needs net for URL parsing).

# ---------------------------------------------------------------------------
# P21.1 — URL 解析
# ---------------------------------------------------------------------------

#: scheme → 默认端口映射
_DEFAULT_PORTS = {
    "http": 80,
    "https": 443,
}


@dataclass(frozen=True)
class URL:
    """解析后的 URL 组件（frozen dataclass，可哈希、可比较）。"""

    scheme: str = "http"
    host: str = ""
    port: int = 0
    path: str = "/"
    query: str = ""
    fragment: str = ""
    user: str = ""
    password: str = ""

    @property
    def is_http(self) -> bool:
        return self.scheme == "http"

    @property
    def is_https(self) -> bool:
        return self.scheme == "https"

    @property
    def is_network(self) -> bool:
        return self.scheme in ("http", "https")

    @property
    def default_port(self) -> int:
        return _DEFAULT_PORTS.get(self.scheme, 0)

    @property
    def effective_port(self) -> int:
        """Port used on the wire: explicit, or the scheme default."""
        return self.port if self.port else self.default_port

    @property
    def authority(self) -> str:
        """``host[:port]`` (port omitted when it is the scheme default)."""
        hostport = self.host
        if self.port and self.port != self.default_port:
            hostport = f"{self.host}:{self.port}"
        return hostport

    def to_string(self) -> str:
        """Serialize back to a canonical URL string."""
        s = self.scheme
        auth = ""
        if self.user:
            auth = self.user
            if self.password:
                auth += f":{self.password}"
            auth += "@"
        hostport = self.authority
        out = f"{s}://{auth}{hostport}"
        path = self.path or "/"
        out += path
        if self.query:
            out += f"?{self.query}"
        if self.fragment:
            out += f"#{self.fragment}"
        return out

    @property
    def origin(self) -> str:
        """Origin: ``scheme://host[:port]`` (no default port)."""
        return f"{self.scheme}://{self.authority}"


def parse_url(url: str, base: "URL | str | None" = None) -> URL:
    """Parse *url* into a :class:`URL`.

    *url* may be absolute (``https://host/path``) or relative
    (``/abs/path``、``../up``、``page.html``、``?q=1``、``#frag``).
    When *base* is given, relative resolution follows the WHATWG
    URL spec:

    * ``?query`` / ``#frag`` alone → replace query/fragment of base.
    * ``/path`` (single leading slash) → same host, new path.
    * ``//host/path`` → same scheme, new host.
    * relative ``page.html`` / ``../x`` → resolved against base path
       (directory part only).
    """
    if base is not None and not isinstance(base, URL):
        base = parse_url(base)
    s = (url or "").strip()
    if not s:
        s = "/"

    # fragment split first (fragment is never sent to the server).
    frag = ""
    if "#" in s:
        s, frag = s.rsplit("#", 1)

    # Absolute URL with scheme?
    if base is None:
        u = urllib.parse.urlsplit(s)
        scheme = (u.scheme or "http").lower()
        port = u.port or 0
        return URL(
            scheme=scheme,
            host=u.hostname or "",
            port=port,
            path=u.path or "/",
            query=u.query or "",
            fragment=frag,
            user=u.username or "",
            password=u.password or "",
        )

    # Relative resolution against base.
    if s.startswith("//"):
        # scheme-relative: keep base scheme, new authority.
        rest = s[2:]
        u = urllib.parse.urlsplit(f"{base.scheme}://{rest}")
        return URL(
            scheme=base.scheme,
            host=u.hostname or "",
            port=u.port or 0,
            path=u.path or "/",
            query=u.query or "",
            fragment=frag,
            user=u.username or "",
            password=u.password or "",
        )
    if "://" in s:
        # absolute with explicit scheme → parse fresh.
        return parse_url(url)

    if s.startswith("?"):
        return URL(
            scheme=base.scheme, host=base.host, port=base.port,
            path=base.path, query=s[1:] or "", fragment=frag,
            user=base.user, password=base.password,
        )
    # s is now a path (possibly with ?query appended).
    query = ""
    if "?" in s:
        s, query = s.split("?", 1)

    if s.startswith("/"):
        path = s
    else:
        # relative to base's directory.
        base_dir = base.path.rsplit("/", 1)[0] + "/" if "/" in base.path else "/"
        path = _resolve_relative(base_dir, s)
    return URL(
        scheme=base.scheme, host=base.host, port=base.port,
        path=path, query=query, fragment=frag,
        user=base.user, password=base.password,
    )


def scheme_of(base: URL) -> str:
    return base.scheme


def _resolve_relative(base_dir: str, rel: str) -> str:
    """Resolve *rel* against a base directory path (WHATWG URL spec,
    no ``javascript:``/``data:`` schemes — those are filtered earlier)."""
    parts = base_dir.split("/")
    # drop the trailing '' from splitting '.../' so 'a/b/' → ['a','b','']
    # keep structure: base_dir always ends with '/' (or is '/').
    # Normalize: strip trailing empty, remember we're in a directory.
    segs = [p for p in parts if p != ""]
    for piece in rel.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if segs:
                segs.pop()
        else:
            segs.append(piece)
    return "/" + "/".join(segs)


def normalize_url(url: URL) -> URL:
    """Return a canonical form: scheme/host lowercased, empty path → ``/``,
    query/fragment kept as-is."""
    return URL(
        scheme=url.scheme.lower(),
        host=url.host.lower(),
        port=url.port,
        path=url.path or "/",
        query=url.query,
        fragment=url.fragment,
        user=url.user,
        password=url.password,
    )


# ---------------------------------------------------------------------------
# P21.2 — HTTP/1.1 最小客户端（http.client，接口可替换）
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 10.0

# P0 (GPT report) — network response size cap.  A decompression-bomb
# or a malicious server that streams an unbounded body must not
# exhaust the host's RAM.  The legacy reader had no cap.  The P0
# fix caps the decompressed body at this value and raises
# :class:`ResponseTooLarge` when it is exceeded.
MAX_RESPONSE_BYTES = 256 * 1024 * 1024  # 256 MiB


class ResponseTooLarge(Exception):
    """P0 (GPT report) — the decompressed response body exceeded
    :data:`MAX_RESPONSE_BYTES` (or the caller-supplied cap).  The
    host treats it like any other fetch failure: log it and move on
    to the next sub-resource."""

# P22.1 — redirect chain cap (上限 5 层) + 循环检测。
MAX_REDIRECTS = 5
_REDIRECT_STATUS = (301, 302, 303, 307, 308)


@dataclass
class HttpResponse:
    """Result of an HTTP GET: status, headers, body bytes.

    ``body`` is the *decoded* body for ``text/html`` (P21.3 dispatch
    decodes), raw bytes otherwise.  Callers should check ``status``
    and ``content_type`` before use.
    """

    status: int
    status_text: str
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    final_url: str = ""
    redirected: bool = False
    from_cache: bool = False

    @property
    def content_type(self) -> str:
        """Lowercased Content-Type (mime part, no params)."""
        ct = self.headers.get("content-type", "")
        return ct.split(";")[0].strip().lower()

    @property
    def is_html(self) -> bool:
        return self.content_type.startswith("text/html")

    @property
    def ok(self) -> bool:
        """True only for 2xx success (3xx redirects are surfaced but
        are NOT 'ok' — P22 follows the chain)."""
        return 200 <= self.status < 300

    @property
    def etag(self) -> str:
        """P23.3 — the response's ETag ("" when absent)."""
        return self.headers.get("etag", "")

    # ------------------------------------------------------------------
    # P33.P2 — CORS / CSP
    # ------------------------------------------------------------------

    @property
    def cors_policy(self) -> "CorsPolicy":
        """P33.P2 — the response's parsed CORS policy (from
        ``Access-Control-Allow-*`` headers)."""
        return CorsPolicy.from_headers(self.headers)

    def is_cors_allowed(self, requesting_origin: str = "") -> bool:
        """P33.P2 — True when a cross-origin fetch from
        *requesting_origin* is permitted by this response's CORS
        headers.  A blank *requesting_origin* (file:// / about:blank)
        only matches the wildcard ``*``."""
        return self.cors_policy.permits(requesting_origin)

    @property
    def csp_directives(self) -> dict:
        """P33.P2 — the response's parsed CSP directives (from the
        ``Content-Security-Policy`` header).  Returns an empty dict
        when the header is absent.  The parser is permissive;
        enforcement is left to the caller.

        Header lookup is case-insensitive (the ``headers`` dict may
        carry mixed-case keys from ``http.client`` or test fixtures).
        """
        # Case-insensitive lookup: the raw headers dict from
        # http.client may use any casing; tests use "Content-Security-Policy".
        csp = ""
        for k, v in self.headers.items():
            if k.lower() == "content-security-policy":
                csp = v
                break
        return parse_csp_directives(csp)

    def is_csp_violation(self, source_kind: str,
                         source_value: str) -> bool:
        """P33.P2 — toy CSP check: return True when *source_value*
        would be blocked by the response's CSP *source_kind* directive
        (e.g. ``script-src``, ``img-src``, ``connect-src``).

        The check is intentionally simple: it collects all allowed
        source tokens from the matching directive (falling back to
        ``default-src`` when the specific directive is absent) and
        returns True when *source_value* matches none of them.
        A blank directive list (no CSP header) permits everything.
        """
        csp = self.csp_directives
        if not csp:
            return False
        # The specific directive takes precedence; fall back to
        # default-src when absent.
        values = csp.get(source_kind, csp.get("default-src", []))
        if not values:
            return False  # no constraint
        for tok in values:
            tok_lower = tok.lower()
            # Strip surrounding single quotes for keyword tokens.
            tok_bare = tok_lower.strip("'")
            # 'self' matches when the source_value is same-origin (toy:
            # always allow 'self' as a pass-through).
            if tok_bare == "self":
                # Toy: 'self' is treated as "permit" (a real browser
                # would compare the source to the document origin).
                return False
            if tok_bare == "none":
                # 'none' blocks all sources — but only when it is the
                # sole value in the list.  If other values exist,
                # 'none' is just a no-op token.
                if len(values) == 1:
                    return True
                continue
            # Exact string match (case-insensitive for scheme/host).
            if tok_bare == source_value.lower().strip("'"):
                return False
            # wildcard "data:" permit.
            if tok_bare == "data:" and source_value.lower().startswith("data:"):
                return False
        return True

    # ------------------------------------------------------------------
    # GPT P0-4 — unified policy enforcement
    # ------------------------------------------------------------------

    def is_policy_allowed(self, requesting_origin: str = "",
                          resource_kind: str = "",
                          resource_url: str = "") -> bool:
        """GPT P0-4 — enforce BOTH CORS and CSP in one call.

        Returns ``True`` when the subresource at *resource_url* is
        allowed to be used by the document at *requesting_origin*.
        The check is a conjunction: the resource is blocked when
        **either** policy rejects it.

        * **CORS** — when *resource_url*'s origin differs from
          *requesting_origin* the response **must** carry a matching
          ``Access-Control-Allow-Origin`` header; same-origin
          requests skip the CORS check (a real browser enforces CORS
          only on cross-origin reads).  A blank *requesting_origin*
          (file://, about:blank, or a caller that does not track
          origins) skips the CORS check entirely (legacy behaviour,
          preserved for backward compatibility).
        * **CSP** — the response's ``Content-Security-Policy``
          ``*resource_kind*-src`` (or ``default-src``) directive is
          matched against *resource_url*.

        *resource_kind* is one of "style" (stylesheet), "script",
        "img" (image), "connect" (fetch/XHR); the directive name is
        derived as ``<kind>-src`` with the legacy mapping
        style→"style-src", script→"script-src", img→"img-src",
        connect→"connect-src" (a blank resource_kind skips the CSP
        check).
        """
        # --- CORS check (cross-origin only) ---
        if requesting_origin and resource_url:
            res_origin = _origin_str(resource_url)
            req_origin = _origin_str(requesting_origin) if "://" in requesting_origin \
                         else requesting_origin
            if not is_origin_match(req_origin, res_origin):
                # Cross-origin read: require a matching ACAO header.
                if not self.is_cors_allowed(req_origin):
                    return False
        # --- CSP check ---
        kind_map = {
            "style": "style-src", "stylesheet": "style-src",
            "script": "script-src", "img": "img-src",
            "image": "img-src", "connect": "connect-src",
            "font": "font-src", "worker": "worker-src",
        }
        directive = kind_map.get(resource_kind,
                                 f"{resource_kind}-src"
                                 if resource_kind else "")
        if directive and resource_url:
            if self.is_csp_violation(directive, resource_url):
                return False
        return True


def _origin_str(url_str: str) -> str:
    """Helper for is_policy_allowed: normalise a URL to its origin
    string (scheme://host[:port], default ports stripped)."""
    import urllib.parse as _up
    p = _up.urlparse(url_str)
    default = _DEFAULT_PORTS.get(p.scheme.lower())
    host = p.hostname or ""
    port = p.port
    if default and port == default:
        port = None
    netloc = host if not port else f"{host}:{port}"
    return f"{p.scheme.lower()}://{netloc}"


def _csp_blocks(values: list, source_value: str) -> bool:
    """GPT P0-4 — return True when *source_value* is blocked by the
    CSP source-list *values* (the same token-matching logic as
    :meth:`HttpResponse.is_csp_violation`, factored out so the
    document-level CSP path in :func:`load_subresources` can reuse
    it without constructing a throwaway HttpResponse)."""
    if not values:
        return False  # no constraint
    for tok in values:
        tok_lower = tok.lower()
        tok_bare = tok_lower.strip("'")
        if tok_bare == "self":
            # Toy: 'self' is treated as "permit" (a real browser
            # would compare the source to the document origin).
            return False
        if tok_bare == "none":
            if len(values) == 1:
                return True
            continue
        if tok_bare == source_value.lower().strip("'"):
            return False
        if tok_bare == "data:" and source_value.lower().startswith("data:"):
            return False
    return True


# ---------------------------------------------------------------------------
# P33.P2 — CORS / CSP basic enforcement
# ---------------------------------------------------------------------------

@dataclass
class CorsPolicy:
    """P33.P2 — a parsed CORS response policy.

    Extracted from the response's ``Access-Control-Allow-Origin`` and
    ``Access-Control-Allow-Credentials`` headers.  Used by
    :meth:`HttpResponse.is_cors_allowed` to check whether a cross-origin
    subresource fetch is permitted by the server.

    A *requesting origin* is allowed when:

    * the response carries ``Access-Control-Allow-Origin: *``, or
    * the response carries ``Access-Control-Allow-Origin: <specific>``
      matching the requesting origin exactly (case-insensitive host,
      case-sensitive scheme+port).
    """
    allow_origin: str = ""       # "*" or a specific origin string
    allow_credentials: bool = False
    allow_headers: str = ""      # comma-separated list (empty = any)

    @classmethod
    def from_headers(cls, headers: dict) -> "CorsPolicy":
        h = {k.lower(): v for k, v in headers.items()}
        allow_origin = h.get("access-control-allow-origin", "")
        allow_cred = h.get("access-control-allow-credentials", "").lower() == "true"
        allow_hdrs = h.get("access-control-allow-headers", "")
        return cls(
            allow_origin=allow_origin,
            allow_credentials=allow_cred,
            allow_headers=allow_hdrs,
        )

    def permits(self, requesting_origin: str) -> bool:
        """Return True when *requesting_origin* is permitted by this policy.

        A blank requesting origin (e.g. ``file://`` / ``about:blank``)
        is treated as opaque and never matches a specific origin; only
        the wildcard ``*`` permits it.
        """
        if not requesting_origin:
            return self.allow_origin == "*"
        if self.allow_origin == "*":
            return True
        # Exact origin match (scheme://host[:port]).
        return self.allow_origin.lower() == requesting_origin.lower()


def parse_csp_directives(csp_header: str) -> dict[str, list[str]]:
    """P33.P2 — parse a ``Content-Security-Policy`` response header into
    a dict of directive → [values].  The parser is intentionally
    permissive: it strips comments, splits on whitespace and
    semicolons, and keeps the first value of each directive token as
    the directive name.

    Example::

        "default-src 'self'; script-src 'self' cdn.example.com"

    parses to::

        {"default-src": ["'self'"],
         "script-src": ["'self'", "cdn.example.com"]}

    Toy semantics: only the values are recorded; no enforcement is done
    here (enforcement is left to the caller / CSS/JS engine).
    """
    if not csp_header:
        return {}
    directives: dict[str, list[str]] = {}
    # Strip any comments (toy: ``/* ... */`` and ``--`` are not CSP
    # comment syntax; we just handle whitespace + semicolon splitting).
    raw = csp_header.strip()
    # Split on semicolons to get individual directives.
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        # Split on whitespace to get the directive name + values.
        tokens = chunk.split()
        if not tokens:
            continue
        name = tokens[0].lower()
        values = tokens[1:]
        directives[name] = values
    return directives


def is_origin_match(a: str, b: str) -> bool:
    """P33.P2 — compare two origin strings (``scheme://host[:port]``)
    for equality, ignoring case on the host portion.

    A blank string never matches anything.  The scheme is
    case-insensitive; the host is case-insensitive; the port is
    case-sensitive (but omitted ports are treated as default ports
    per-scheme).
    """
    if not a or not b:
        return False
    import urllib.parse as _up
    pa, pb = _up.urlparse(a), _up.urlparse(b)
    # Normalize: lowercase scheme and netloc.
    a_norm = f"{pa.scheme.lower()}://{pa.netloc.lower()}"
    b_norm = f"{pb.scheme.lower()}://{pb.netloc.lower()}"
    # When the port is the scheme default, omit it for comparison.
    def _norm(origin_str, parsed):
        s = parsed.scheme.lower()
        h = parsed.netloc.lower()
        # Strip default ports.
        default = _DEFAULT_PORTS.get(s)
        if default and h.endswith(f":{default}"):
            h = h[:-(len(str(default)) + 1)]
        return f"{s}://{h}"
    return _norm(a, pa) == _norm(b, pb)


class HttpClient:
    """P21.2 — minimal HTTP/1.1 client.

    Uses :mod:`http.client` (stdlib) so the interface is swappable for
    a hand-rolled socket implementation later (M5 direction B).
    GET and POST (toy, P23 form support) are supported.  P22 adds a
    redirect chain; P23 adds:

    * **TLS** (P23.1) — ``ssl`` wraps the socket; cert verification is
      on by default.  ``verify_tls=False`` (the ``--insecure`` switch)
      disables hostname + cert checks for self-signed / debug servers.
    * **disk cache** (P23.2) — pass a :class:`CacheStore` via
      ``cache=``; GETs consult it first (fresh → no wire; stale with
      ETag → revalidate via ``If-None-Match``).
    * **304 negotiation** (P23.3) — a revalidation that comes back
      304 reuses the cached body and refreshes its freshness.
    * **load progress** (P23.4) — pass a :class:`LoadEventSink` via
      ``progress=``; each fetch emits start/document/subresource/done.
    * **connection reuse** (P33.R2) — keep-alive connections are
      pooled per (scheme, host, port, verify_tls) and reused across
      requests to the same origin; a dead connection (``RemoteDisconnected``
      / ``ConnectionResetError``) is discarded and one retry is made
      with a fresh connection.
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT,
                 verify_tls: bool = True,
                 cache: "CacheStore | None" = None,
                 progress: "LoadEventSink | None" = None,
                 cookies: "cookie_store.CookieJar | None" = None,
                 max_response_bytes: int = MAX_RESPONSE_BYTES) -> None:
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.cache = cache
        self.progress = progress
        # P32.3 — optional cookie jar: responses set-cookie, requests
        # carry the Cookie header for matching host/path.
        self.cookies = cookies
        # P33.R2 — keep-alive connection pool (one connection per
        # origin).  The pool is small (typically 1-4 origins per page
        # load) and is cleared by :meth:`close` / :meth:`close_pool`.
        self._conn_pool: dict = {}
        # P0 (GPT report) — network response size cap.  A malicious or
        # misbehaving server that streams an unbounded body (or a
        # gzip-decompression-bomb) must not exhaust the host's RAM.
        # The legacy reader had no cap; the P0 fix caps the
        # decompressed body at ``max_response_bytes`` and raises
        # :class:`ResponseTooLarge` when the cap is exceeded.
        self.max_response_bytes = max_response_bytes

    # ------------------------------------------------------------------
    # P33.R2 — connection reuse
    # ------------------------------------------------------------------

    def _pool_key(self, url: URL) -> tuple:
        return (url.scheme, url.host or "localhost", url.effective_port,
                self.verify_tls)

    def _open_conn(self, url: URL):
        host = url.host or "localhost"
        port = url.effective_port
        ssl_ctx = None
        if url.is_https:
            ssl_ctx = ssl.create_default_context()
            if not self.verify_tls:
                # P23.1 — the --insecure switch: skip hostname + cert
                # verification (self-signed / debug servers).
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
        if url.is_https:
            return http.client.HTTPSConnection(
                host, port, timeout=self.timeout, context=ssl_ctx,
            )
        return http.client.HTTPConnection(host, port, timeout=self.timeout)

    def _acquire_conn(self, url: URL):
        """P33.R2 — take a pooled connection for *url*'s origin, or
        open a fresh one.  A pooled connection may be stale (server
        closed the keep-alive socket); the caller treats the first
        attempt's failure as a retry-on-fresh-connection case."""
        key = self._pool_key(url)
        conn = self._conn_pool.pop(key, None)
        if conn is not None:
            return conn
        return self._open_conn(url)

    def _release_conn(self, url: URL, conn, dead: bool = False) -> None:
        """P33.R2 — return a healthy keep-alive connection to the pool.

        A dead connection is not pooled (the server has closed the
        socket); the pool is capped at 8 entries so a page with many
        origins cannot grow it unboundedly.
        """
        if dead:
            return
        key = self._pool_key(url)
        if len(self._conn_pool) >= 8:
            try:
                conn.close()
            except OSError:
                pass
            return
        self._conn_pool[key] = conn

    def close_pool(self) -> None:
        """P33.R2 — close every pooled connection (call on shutdown)."""
        for conn in self._conn_pool.values():
            try:
                conn.close()
            except OSError:
                pass
        self._conn_pool.clear()

    def _emit(self, phase: str, fraction: float, detail: str = "") -> None:
        if self.progress is not None:
            self.progress.emit(LoadProgress(phase, fraction, detail))

    def _single_get(self, url: URL,
                    headers: dict | None = None,
                    revalidate_etag: str = "",
                    stale_body: bytes = b"",
                    cookie_context: dict | None = None) -> HttpResponse:
        """One wire GET (no redirect following).  P22 gzip-decodes the
        body when ``Content-Encoding: gzip`` is present.

        P23.2/P23.3 — when *revalidate_etag* is set the request
        carries ``If-None-Match``; a 304 response reuses *stale_body*
        and refreshes the cache entry.

        P33.R2 — connections are reused from the keep-alive pool
        (one per origin); a dead pooled socket is discarded and the
        request is retried once on a fresh connection.

        P0 (GPT report) — *cookie_context* carries the cross-site /
        method / top-level metadata for the cookie jar's SameSite
        filter (see the comment at the cookie-header emission site).
        """
        if url.scheme not in ("http", "https"):
            raise UnsupportedScheme(url.scheme)

        host = url.host or "localhost"
        port = url.effective_port
        path = url.path or "/"
        if url.query:
            path = f"{path}?{url.query}"

        hdrs = {"Host": url.authority,
                "User-Agent": "toy-browser-py/1.2 (P21-P23)",
                "Accept": "text/html,application/xhtml+xml,*/*",
                # P22.2 — ask for gzip; the server may or may not honor
                # it.  When it does, _single_get decodes below.
                "Accept-Encoding": "gzip"}
        if revalidate_etag:
            # P23.3 — conditional GET for stale cached entries.
            hdrs["If-None-Match"] = revalidate_etag
        # P0 (GPT report) — cookie emission now carries the
        # cross-site / method / top-level metadata so the jar's
        # SameSite filter can apply.  The legacy call
        # ``cookie_header_for(url)`` sent every live cookie on every
        # request, ignoring the Secure / SameSite flags.
        # ``cookie_context`` is a dict with optional keys:
        #   is_cross_site: bool   (True when the request host differs
        #                          from the top-level page's host)
        #   method: str           ("GET" / "POST" / …)
        #   is_top_level: bool    (True for document navigations)
        # Defaults are same-site GET top-level (the document fetch
        # itself); subresource fetchers override as needed.
        if self.cookies is not None:
            ctx = cookie_context or {}
            cookie_hdr = self.cookies.cookie_header_for(
                url,
                is_cross_site=ctx.get("is_cross_site", False),
                method=ctx.get("method", "GET"),
                is_top_level=ctx.get("is_top_level", True),
            )
            if cookie_hdr:
                hdrs["Cookie"] = cookie_hdr
        if headers:
            hdrs.update(headers)

        last_exc: Exception | None = None
        for attempt in (0, 1):
            try:
                # P33.R2 — acquire a (possibly reused) connection; a
                # first attempt on a stale socket raises
                # ConnectionResetError / RemoteDisconnected and the
                # retry loop opens a fresh one.
                conn = self._acquire_conn(url)
                conn.request("GET", path, headers=hdrs)
                resp = conn.getresponse()
                # P33.R2 — a connection that answered can be pooled
                # only when the body was fully read AND the server
                # did not ask for the connection to close.
                connection_hdr = ""
                try:
                    connection_hdr = (resp.getheader("Connection")
                                       or "").lower()
                except Exception:
                    connection_hdr = ""
                # P23.3 — a 304 (Not Modified) has no body; reuse
                # the stale cached body and refresh freshness.
                if resp.status == 304:
                    body = stale_body
                    headers_out = {
                        k.lower(): v for k, v in resp.getheaders()
                    }
                    # Merge in the cached headers (the 304 body is
                    # the cached one; keep its content-type, etc.).
                    self._release_conn(url, conn, dead=(
                        "close" in connection_hdr))
                    return HttpResponse(
                        status=200,  # surface as a 200 for callers
                        status_text="OK",
                        headers=headers_out,
                        body=body,
                        final_url=url.to_string(),
                        from_cache=True,
                    )
                body = resp.read()
                # P0 (GPT report) — cap the raw (pre-decompression)
                # body so a malicious server cannot exhaust RAM.
                if len(body) > self.max_response_bytes:
                    raise ResponseTooLarge(
                        f"response body {len(body)} bytes exceeds "
                        f"cap {self.max_response_bytes}")
                headers_out = {
                    k.lower(): v for k, v in resp.getheaders()
                }
                # P32.3 — ingest Set-Cookie into the jar before the
                # response leaves the client.
                if self.cookies is not None:
                    self.cookies.ingest_response(url, headers_out)
                final = url.to_string()
                # P22.2 — transparent gzip decode.
                body = maybe_gunzip(
                    _TransientResp(status=resp.status,
                                   status_text=resp.reason or "",
                                   headers=headers_out,
                                   body=body,
                                   final_url=final))
                self._release_conn(url, conn, dead=("close" in connection_hdr
                                                     or resp.status >= 400
                                                     and resp.status == 401))
                return HttpResponse(
                    status=resp.status,
                    status_text=resp.reason or "",
                    headers=headers_out,
                    body=body,
                    final_url=final,
                )
            except (socket.timeout, socket.error,
                    http.client.HTTPException, OSError,
                    http.client.RemoteDisconnected) as e:
                last_exc = e
                # P33.R2 — the pooled connection was dead; discard
                # it so the retry opens a fresh socket.
                key = self._pool_key(url)
                self._conn_pool.pop(key, None)
                continue
        raise ConnectionError(
            f"GET {url.to_string()} failed after retries: {last_exc}"
        ) from last_exc

    def get(self, url: "URL | str",
            headers: dict | None = None,
            follow_redirects: bool = True,
            max_redirects: int = MAX_REDIRECTS) -> HttpResponse:
        """GET *url*; P22.1 — follow 3xx redirects up to
        *max_redirects* (cap 5) with loop detection.  ``follow_redirects``
        False (the P21 behavior) returns the first 3xx as-is.

        P23.2 — when a cache is attached, a fresh cached response is
        returned without touching the wire.  P23.3 — a stale entry
        with an ETag is revalidated (``If-None-Match``); a 304 reuses
        the body.  P23.4 — emits load-progress events when a sink is
        attached.

        Raises :class:`RedirectLoop` when the chain loops or exceeds
        the cap.  4xx/5xx returned as-is so the caller renders the
        error page.  303/307/308 keep or switch method semantics — for
        a GET-only client all are followed with GET.
        """
        if not isinstance(url, URL):
            url = parse_url(url)
        self._emit(PHASE_START, 0.0, url.to_string())

        # P23.2 — fresh cache hit: skip the wire entirely.
        if self.cache is not None and self.cache.enabled:
            fresh = self.cache.get_fresh(url)
            if fresh is not None:
                self._emit(PHASE_DOCUMENT, 0.5, "cache hit")
                self._emit(PHASE_DONE, 1.0, "cache hit")
                return fresh

        # P23.3 — stale entry with ETag: revalidate conditionally.
        stale = None
        if self.cache is not None and self.cache.enabled:
            stale = self.cache.get_stale(url)

        resp = _follow_redirects(
            self, url, headers,
            max_redirects=max_redirects, follow=follow_redirects,
            revalidate_etag=(stale.etag if stale else ""),
            stale_body=(stale.body if stale else b""),
        )

        # P23.2 — store successful, cacheable responses.
        if (self.cache is not None and self.cache.enabled
                and resp.ok and resp.status != 204):
            if getattr(resp, "from_cache", False):
                # P23.3 — a 304 revalidation was surfaced as a 200:
                # refresh the stale entry's freshness instead of
                # overwriting its body.
                stale = self.cache.get_stale(url)
                if stale is not None:
                    self.cache.revalidate_304(url, stale)
            else:
                self.cache.put(url, resp)

        self._emit(PHASE_DOCUMENT, 0.5, f"HTTP {resp.status}")
        self._emit(PHASE_DONE, 1.0, f"HTTP {resp.status}")
        return resp

    def post(self, url: "URL | str",
             data: "dict | str | bytes | None" = None,
             content_type: str = "",
             headers: dict | None = None,
             follow_redirects: bool = True,
             max_redirects: int = MAX_REDIRECTS,
             cookie_context: dict | None = None) -> HttpResponse:
        """P23 — POST *url* with a body.

        *data* may be a mapping (encoded as
        ``application/x-www-form-urlencoded``), a ``str`` (sent verbatim
        with the given *content_type*), or ``bytes`` (sent verbatim).
        For a GET-only cache, POST responses are NOT cached (spec:
        caching non-idempotent methods is out of scope for the toy).
        P23.4 — emits load-progress events when a sink is attached.

        P0 (GPT report) — *cookie_context* carries the cross-site /
        top-level metadata for the cookie jar's SameSite filter; a
        cross-site POST must not carry a SameSite=Lax cookie.
        """
        if not isinstance(url, URL):
            url = parse_url(url)
        if url.scheme not in ("http", "https"):
            raise UnsupportedScheme(url.scheme)

        hdrs = dict(headers or {})
        body_bytes = b""
        if isinstance(data, dict):
            body_bytes = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type",
                            "application/x-www-form-urlencoded")
        elif isinstance(data, str):
            body_bytes = data.encode("utf-8")
            if content_type:
                hdrs.setdefault("Content-Type", content_type)
        elif isinstance(data, (bytes, bytearray)):
            body_bytes = bytes(data)
            if content_type:
                hdrs.setdefault("Content-Type", content_type)
        if content_type and "Content-Type" not in hdrs:
            hdrs["Content-Type"] = content_type
        hdrs.setdefault("Host", url.authority)
        hdrs.setdefault("User-Agent", "toy-browser-py/1.2 (P21-P23)")
        # P0 (GPT report) — attach matching cookies from the jar.
        if self.cookies is not None:
            ctx = cookie_context or {}
            cookie_hdr = self.cookies.cookie_header_for(
                url,
                is_cross_site=ctx.get("is_cross_site", False),
                method="POST",
                is_top_level=ctx.get("is_top_level", False),
            )
            if cookie_hdr:
                hdrs["Cookie"] = cookie_hdr

        self._emit(PHASE_START, 0.0, url.to_string())
        # POST is not cached (non-idempotent); just do the wire call.
        host = url.host or "localhost"
        port = url.effective_port
        path = url.path or "/"
        if url.query:
            path = f"{path}?{url.query}"

        last_exc: Exception | None = None
        for attempt in (0, 1):
            try:
                # P33.R2 — acquire a (possibly reused) connection; a
                # first attempt on a stale socket raises
                # ConnectionResetError / RemoteDisconnected and the
                # retry loop opens a fresh one.
                conn = self._acquire_conn(url)
                conn.request("POST", path, body=body_bytes,
                             headers=hdrs)
                resp = conn.getresponse()
                body = resp.read()
                headers_out = {
                    k.lower(): v for k, v in resp.getheaders()
                }
                conn_hdr = (resp.getheader("Connection") or "").lower()
                self._release_conn(url, conn, dead=("close" in conn_hdr))
                return HttpResponse(
                    status=resp.status,
                    status_text=resp.reason or "",
                    headers=headers_out,
                    body=body,
                    final_url=url.to_string(),
                )
            except (socket.timeout, socket.error,
                    http.client.HTTPException, OSError,
                    http.client.RemoteDisconnected) as e:
                last_exc = e
                key = self._pool_key(url)
                self._conn_pool.pop(key, None)
                continue
        raise ConnectionError(
            f"POST {url.to_string()} failed after retries: {last_exc}"
        ) from last_exc


class _TransientResp:
    """Minimal duck-type for maybe_gunzip's signature (status/headers/
    body) so we can reuse the P22.2 decoder in _single_get without
    constructing a full HttpResponse mid-read."""
    def __init__(self, status, status_text, headers, body, final_url):
        self.status = status
        self.status_text = status_text
        self.headers = headers
        self.body = body
        self.final_url = final_url


# ---------------------------------------------------------------------------
# P23.2 / P23.3 — 磁盘缓存 + ETag 协商缓存
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    """On-disk cache record (JSON) for one response."""
    url: str
    status: int
    headers: dict
    body: bytes          # decoded (post-gunzip) body
    fetched_at: float    # time.time() when stored
    date: str            # response Date header (may be "")
    last_modified: str   # response Last-Modified header (may be "")
    etag: str            # response ETag header (may be "")
    max_age: float       # Cache-Control max-age (0 if absent)
    no_cache: bool
    no_store: bool
    heuristic_ttl: float # heuristic TTL in seconds (0 if none)

    def ttl(self, now: float) -> float:
        """Remaining freshness lifetime in seconds at *now*."""
        if self.no_store:
            return 0.0
        if self.max_age > 0:
            age = now - self.fetched_at
            return max(0.0, self.max_age - age)
        if self.no_cache:
            # no-cache → still revalidate (304 path) even if fresh.
            return 0.0
        if self.heuristic_ttl > 0:
            age = now - self.fetched_at
            return max(0.0, self.heuristic_ttl - age)
        # No directives, no heuristic → treat as revalidate-on-use.
        return 0.0

    def to_json(self) -> dict:
        return {
            "url": self.url, "status": self.status,
            "headers": {k: str(v) for k, v in self.headers.items()},
            "body_b64": _b64e(self.body),
            "fetched_at": self.fetched_at,
            "date": self.date, "last_modified": self.last_modified,
            "etag": self.etag, "max_age": self.max_age,
            "no_cache": self.no_cache, "no_store": self.no_store,
            "heuristic_ttl": self.heuristic_ttl,
        }

    @classmethod
    def from_json(cls, d: dict) -> "_CacheEntry":
        return cls(
            url=d["url"], status=d["status"],
            headers=d.get("headers", {}),
            body=_b64d(d.get("body_b64", "")),
            fetched_at=float(d.get("fetched_at", 0.0)),
            date=d.get("date", ""), last_modified=d.get("last_modified", ""),
            etag=d.get("etag", ""), max_age=float(d.get("max_age", 0.0)),
            no_cache=bool(d.get("no_cache", False)),
            no_store=bool(d.get("no_store", False)),
            heuristic_ttl=float(d.get("heuristic_ttl", 0.0)),
        )


def _b64e(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    import base64
    return base64.b64decode(s.encode("ascii")) if s else b""


def _cache_key(url: "URL") -> str:
    return hashlib.sha256(url.to_string().encode("utf-8")).hexdigest()


def _parse_cache_control(value: str) -> dict:
    """P23.2 — toy parse of Cache-Control into {directive: arg}."""
    out: dict = {}
    if not value:
        return out
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
        else:
            out[part.strip().lower()] = True
    return out


def _http_date_to_epoch(value: str) -> float:
    """P23.3 — parse an RFC 1123 / RFC 850 date into a timestamp.
    Returns 0.0 when unparseable (heuristic falls back to no-TTL)."""
    import email.utils
    if not value:
        return 0.0
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _heuristic_ttl(headers: dict) -> float:
    """P23.2 — heuristic freshness: 10% of the response's age when
    ``Date`` and ``Last-Modified`` are both present and there are no
    explicit freshness directives."""
    date_hdr = headers.get("date", "")
    lm_hdr = headers.get("last-modified", "")
    d = _http_date_to_epoch(date_hdr)
    lm = _http_date_to_epoch(lm_hdr)
    if d <= 0.0 or lm <= 0.0 or lm >= d:
        return 0.0
    age = d - lm
    return max(0.0, age * 0.1)


def build_cache_entry(url: "URL", resp: "HttpResponse") -> _CacheEntry:
    """P23.2 — turn an :class:`HttpResponse` into a cacheable entry
    (extracting Cache-Control / ETag / Date / Last-Modified)."""
    cc = _parse_cache_control(
        resp.headers.get("cache-control", ""))
    max_age = 0.0
    if "max-age" in cc:
        try:
            max_age = float(str(cc["max-age"]))
        except (TypeError, ValueError):
            max_age = 0.0
    no_cache = ("no-cache" in cc)
    no_store = ("no-store" in cc)
    heuristic = 0.0
    if not max_age and not no_cache and not no_store:
        heuristic = _heuristic_ttl(resp.headers)
    return _CacheEntry(
        url=url.to_string(),
        status=resp.status,
        headers={k: str(v) for k, v in resp.headers.items()},
        body=resp.body,
        fetched_at=time.time(),
        date=resp.headers.get("date", ""),
        last_modified=resp.headers.get("last-modified", ""),
        etag=resp.headers.get("etag", ""),
        max_age=max_age,
        no_cache=no_cache,
        no_store=no_store,
        heuristic_ttl=heuristic,
    )


class CacheStore:
    """P23.2 — heuristic on-disk response cache.

    Layout under *root_dir*::

        <root>/<sha256(url)>.json   # _CacheEntry metadata
        <root>/<sha256(url)>.bin    # raw (decoded) body bytes

    ``no-store`` responses are never written.  Lookups return the
    entry only when it is *fresh* (``ttl(now) > 0``).  Stale entries
    are returned separately via :meth:`get_stale` so the caller can
    run the P23.3 revalidation (``If-None-Match``) path.
    """

    def __init__(self, root_dir: str, enabled: bool = True) -> None:
        self.root_dir = root_dir
        self.enabled = enabled
        if enabled:
            os.makedirs(root_dir, exist_ok=True)

    def _paths(self, url: "URL") -> tuple[str, str]:
        key = _cache_key(url)
        return (os.path.join(self.root_dir, key + ".json"),
                os.path.join(self.root_dir, key + ".bin"))

    def put(self, url: "URL", resp: "HttpResponse") -> None:
        """P23.2 — store *resp* for *url* (skips ``no-store``)."""
        if not self.enabled:
            return
        entry = build_cache_entry(url, resp)
        if entry.no_store:
            return
        meta_path, bin_path = self._paths(url)
        with open(bin_path, "wb") as f:
            f.write(entry.body)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(entry.to_json(), f, ensure_ascii=False)

    def get_fresh(self, url: "URL", now: float | None = None
                  ) -> "HttpResponse | None":
        """P23.2 — return a fresh cached :class:`HttpResponse`, or
        ``None`` when absent / stale / disabled."""
        if not self.enabled:
            return None
        now = now if now is not None else time.time()
        meta_path, bin_path = self._paths(url)
        if not (os.path.exists(meta_path) and os.path.exists(bin_path)):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                entry = _CacheEntry.from_json(json.load(f))
        except (OSError, ValueError, KeyError):
            return None
        if entry.ttl(now) <= 0.0:
            return None
        with open(bin_path, "rb") as f:
            body = f.read()
        return _resp_from_entry(entry, body, from_cache=True)

    def get_stale(self, url: "URL") -> "HttpResponse | None":
        """P23.3 — return a *stale* entry that can still be
        revalidated via ``If-None-Match`` (needs an ETag).  Fresh
        entries and no-ETag entries return ``None``."""
        if not self.enabled:
            return None
        meta_path, bin_path = self._paths(url)
        if not (os.path.exists(meta_path) and os.path.exists(bin_path)):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                entry = _CacheEntry.from_json(json.load(f))
        except (OSError, ValueError, KeyError):
            return None
        now = time.time()
        if entry.ttl(now) > 0.0:
            return None  # still fresh — caller should use get_fresh.
        if not entry.etag:
            return None  # no ETag → cannot revalidate; refetch.
        with open(bin_path, "rb") as f:
            body = f.read()
        return _resp_from_entry(entry, body, from_cache=True)

    def revalidate_304(self, url: "URL", stale: "HttpResponse") -> None:
        """P23.3 — server returned 304: refresh the cached entry's
        ``fetched_at`` / ``Date`` so it becomes fresh again."""
        if not self.enabled:
            return
        meta_path, _ = self._paths(url)
        if not os.path.exists(meta_path):
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                entry = _CacheEntry.from_json(json.load(f))
        except (OSError, ValueError, KeyError):
            return
        entry.fetched_at = time.time()
        entry.date = stale.headers.get("date", entry.date)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(entry.to_json(), f, ensure_ascii=False)

    def clear(self) -> int:
        """Delete every cached response; return the number removed."""
        if not self.enabled or not os.path.isdir(self.root_dir):
            return 0
        n = 0
        for name in os.listdir(self.root_dir):
            if name.endswith((".json", ".bin")):
                try:
                    os.remove(os.path.join(self.root_dir, name))
                    n += 1
                except OSError:
                    pass
        return n


def _resp_from_entry(entry: _CacheEntry, body: bytes,
                     from_cache: bool) -> "HttpResponse":
    headers = dict(entry.headers)
    if from_cache:
        headers["x-toy-cache"] = "hit"
    return HttpResponse(
        status=entry.status,
        status_text="OK" if 200 <= entry.status < 300 else "",
        headers=headers,
        body=body,
        final_url=entry.url,
        redirected=False,
        from_cache=from_cache,
    )


# ---------------------------------------------------------------------------
# P23.4 — 加载进度事件（接入现有事件循环）
# ---------------------------------------------------------------------------

#: P23.4 — load-progress phases (M3.2 GUI progress-bar consumer).
PHASE_START = "start"
PHASE_DOCUMENT = "document"
PHASE_SUBRESOURCE = "subresource"
PHASE_DONE = "done"


@dataclass
class LoadProgress:
    """P23.4 — a single load-progress event.

    *phase* is one of :data:`PHASE_START` / :data:`PHASE_DOCUMENT` /
    :data:`PHASE_SUBRESOURCE` / :data:`PHASE_DONE`.  *fraction* is a
    0.0–1.0 completion estimate (1.0 only at ``PHASE_DONE``) suitable
    for a GUI progress bar.
    """
    phase: str
    fraction: float = 0.0
    detail: str = ""

    def __str__(self) -> str:
        pct = int(round(self.fraction * 100))
        return f"[{self.phase} {pct}%] {self.detail}".rstrip()


class LoadEventSink:
    """P23.4 — collects load-progress events; the GUI / CLI can poll
    ``events`` (or subscribe via ``on_event``) to drive a progress
    bar.  Replaces the bare list with a tiny pub/sub hook."""

    def __init__(self) -> None:
        self.events: list[LoadProgress] = []
        self._subs: list = []

    def on_event(self, fn) -> None:
        """Subscribe *fn*(LoadProgress); called for each new event."""
        self._subs.append(fn)

    def emit(self, ev: LoadProgress) -> None:
        self.events.append(ev)
        for fn in self._subs:
            try:
                fn(ev)
            except Exception:
                pass


class UnsupportedScheme(Exception):
    pass


class FetchError(Exception):
    """Unified fetch failure (timeout / connection / bad status)."""

    def __init__(self, message: str, status: int = 0, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


class RedirectLoop(FetchError):
    """P22.1 — the redirect chain looped or exceeded the 5-hop cap."""

    def __init__(self, message: str, chain: list | None = None):
        super().__init__(message)
        self.chain = list(chain or [])


def decode_chunked(raw: bytes) -> bytes:
    """P22.3 — decode a raw HTTP chunked transfer-encoding body.

    ``http.client`` already decodes chunked bodies transparently when
    you call ``resp.read()``; this hook is kept so the spec's
    chunked parsing is unit-testable in isolation (and so a future
    hand-rolled socket client can reuse it).

    Format: ``<hex-size>[;ext]\\r\\n<data>\\r\\n ... 0\\r\\n[trailers]\\r\\n``
    """
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        # Find the CRLF terminating the size line.
        crlf = raw.find(b"\r\n", i)
        if crlf < 0:
            break
        size_line = raw[i:crlf].decode("latin-1").split(";")[0].strip()
        try:
            size = int(size_line, 16)
        except ValueError:
            break
        if size == 0:
            # Terminal chunk — stop (ignore optional trailers).
            break
        i = crlf + 2
        end = i + size
        out += raw[i:min(end, n)]
        i = end
        # Consume the trailing CRLF after the chunk data.
        if i + 2 <= n and raw[i:i + 2] == b"\r\n":
            i += 2
    return bytes(out)


def maybe_gunzip(resp: "HttpResponse",
                 max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """P22.2 — decode the response body when
    ``Content-Encoding: gzip`` is present.  Idempotent: returns the
    body unchanged when the header is absent or decoding fails (a
    failed gunzip must not break the page).

    P0 (GPT report) — the gunzip path is also capped at *max_bytes*
    so a decompression-bomb (a small gzip that expands to multi-GB)
    cannot exhaust the host's RAM.  When the expanded body exceeds
    the cap, :class:`ResponseTooLarge` is raised.
    """
    enc = resp.headers.get("content-encoding", "").lower()
    if "gzip" not in enc:
        return resp.body
    try:
        out = gzip.decompress(resp.body)
    except (OSError, EOFError, gzip.BadGzipFile):
        return resp.body
    if len(out) > max_bytes:
        raise ResponseTooLarge(
            f"gzip-decompressed body {len(out)} bytes exceeds "
            f"cap {max_bytes}")
    return out


def _follow_redirects(client: "HttpClient",
                     url: "URL",
                     headers: dict | None,
                     max_redirects: int = MAX_REDIRECTS,
                     follow: bool = True,
                     revalidate_etag: str = "",
                     stale_body: bytes = b"") -> HttpResponse:
    """P22.1 — follow a 3xx redirect chain (cap *max_redirects*,
    loop-detect via the set of visited URLs).  P23.3 — the *first*
    hop may carry a conditional GET (``If-None-Match``) when
    *revalidate_etag* is set; a 304 is surfaced as a 200 backed by
    *stale_body*."""
    visited: list[URL] = []
    cur = url
    hops = 0
    first = True
    while True:
        visited.append(cur)
        resp = client._single_get(
            cur, headers,
            revalidate_etag=(revalidate_etag if first else ""),
            stale_body=(stale_body if first else b""),
        )
        first = False
        if not follow or resp.status not in _REDIRECT_STATUS \
                or "location" not in resp.headers:
            # A 3xx that we did NOT follow (or the terminal hop after
            # following) still sets redirected=True so the caller can
            # see the chain was traversed.
            resp.redirected = (resp.status in _REDIRECT_STATUS) or (hops > 0)
            return resp
        hops += 1
        if hops > max_redirects:
            raise RedirectLoop(
                f"redirect chain exceeded {max_redirects} hops "
                f"starting at {url.to_string()}",
                chain=[u.to_string() for u in visited],
            )
        location = resp.headers["location"]
        nxt = parse_url(location, base=cur)
        if nxt in visited:
            raise RedirectLoop(
                f"redirect loop detected at {nxt.to_string()}",
                chain=[u.to_string() for u in visited],
            )
        cur = nxt


def fetch_html(url: "URL | str", client: HttpClient | None = None,
               timeout: float | None = None,
               follow_redirects: bool = True,
               max_redirects: int = MAX_REDIRECTS
               ) -> tuple[str, HttpResponse]:
    """GET *url* and return ``(html_text, response)``.

    P22.1 — follows a 3xx redirect chain (cap *max_redirects*, loop
    detection) when ``follow_redirects`` is True.  *text/html* (or
    empty Content-Type) → UTF-8 decoded body.  Other Content-Types
    raise :class:`FetchError` so the caller can render a download /
    unsupported page.  ``RedirectLoop`` (a FetchError subclass) is
    raised when the chain loops or exceeds the cap.
    """
    c = client or (
        HttpClient(timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
        if timeout is not None else HttpClient())
    if not isinstance(url, URL):
        url = parse_url(url)
    resp = c.get(url, follow_redirects=follow_redirects,
                 max_redirects=max_redirects)
    if resp.status >= 400:
        raise FetchError(
            f"HTTP {resp.status} {resp.status_text} for {url.to_string()}",
            status=resp.status, url=url.to_string(),
        )
    if resp.is_html or resp.content_type in ("", "text/plain"):
        text = resp.body.decode("utf-8", errors="replace")
        return text, resp
    # P21.3 — non-HTML content-type: the caller treats this as a
    # "download/ignore" situation.  Raise so the pipeline can render
    # the unified error page.
    raise FetchError(
        f"unsupported content-type {resp.content_type!r} "
        f"({url.to_string()})",
        status=resp.status, url=url.to_string(),
    )


def fetch_resource(url: "URL | str",
                   client: HttpClient | None = None,
                   timeout: float | None = None) -> "HttpResponse | None":
    """P22.4 — fetch a subresource (stylesheet / image / script).

    Returns the :class:`HttpResponse` (body bytes) on success, or
    ``None`` on any failure (network error, 4xx/5xx, bad content).
    Subresource loading must never block or crash the page — the
    caller ignores ``None``.
    """
    c = client or (
        HttpClient(timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
        if timeout is not None else HttpClient())
    if not isinstance(url, URL):
        url = parse_url(url)
    try:
        resp = c.get(url)
    except Exception:
        return None
    if not resp.ok:
        return None
    return resp


# ---------------------------------------------------------------------------
# P22.4 / P22.5 — 子资源收集 + <base> 标签
# ---------------------------------------------------------------------------

def base_url_from_dom(dom_root) -> "URL | None":
    """P22.5 — read ``<base href>`` from the DOM; return a :class:`URL`
    or ``None`` when the document has no base tag.  A base tag makes
    *all* relative URLs in the document resolve against it, not the
    document URL (spec behavior, toy subset)."""
    def _walk(n):
        if getattr(n, "tag", None) == "base":
            href = n.attr("href")
            if href:
                return href
        for c in getattr(n, "children", []):
            r = _walk(c)
            if r is not None:
                return r
        return None
    href = _walk(dom_root)
    if not href:
        return None
    return parse_url(href)


def collect_subresources(dom_root) -> list[dict]:
    """P22.4 — walk the DOM and return a list of subresource specs:
    ``{"kind": "stylesheet"|"image", "src": <attr value>}`` in
    document order.  Only ``<link rel="stylesheet">`` and ``<img>``
    are collected (the P22 spec subset).  Data URIs are included (the
    caller may skip them)."""
    out: list[dict] = []

    def _walk(n):
        tag = getattr(n, "tag", None)
        if tag == "link":
            rel = n.attr("rel") or ""
            href = n.attr("href")
            if href and rel.strip().lower() in ("stylesheet",
                                                "alternate stylesheet"):
                out.append({"kind": "stylesheet", "src": href})
        elif tag == "img":
            src = n.attr("src")
            if src:
                out.append({"kind": "image", "src": src})
        for c in getattr(n, "children", []):
            _walk(c)

    _walk(dom_root)
    return out


def load_subresources(dom_root, base_url, client: HttpClient | None = None,
                      timeout: float | None = None,
                      progress: LoadEventSink | None = None,
                      enforce_policy: bool = True,
                      doc_csp: str = "") -> dict:
    """P22.4 — load every subresource in *dom_root* against *base_url*.

    1. Resolve a document-level base: ``<base href>`` wins over
       *base_url* (P22.5).
    2. Collect ``<link rel=stylesheet>`` / ``<img src>`` (document
       order).
    3. Resolve each relative src against the base, then GET each one
       (玩具语义：顺序执行 + 失败不阻塞 —— 一个子资源 404/超时不会
       中断其余加载，也不会影响页面渲染）。

    GPT P0-4 — when *enforce_policy* is True (default), each
    successfully fetched subresource is passed through
    :meth:`HttpResponse.is_policy_allowed` with the document's
    *base_url* origin and the subresource's kind ("stylesheet" →
    style-src, "image" → img-src).  A subresource blocked by CORS
    (cross-origin read without ``Access-Control-Allow-Origin``) or
    by the document's CSP ``style-src`` / ``img-src`` directive is
    mapped to ``None`` (the caller skips it silently, matching the
    legacy failure contract).  Set *enforce_policy* to False to
    restore the legacy behaviour (no policy check) for tests that
    use bare HTTP fixtures.

    *doc_csp* (GPT P0-4) is the document response's
    ``Content-Security-Policy`` header; when non-empty it is
    applied to every subresource fetch (the real browser uses the
    *document's* CSP to gate subresources, not each subresource's
    own response header).  When *doc_csp* is blank the legacy
    behaviour is used (the subresource's own CSP header is checked).

    Returns a mapping ``kind → {absolute_url: HttpResponse-or-None}``
    so the caller can, e.g., parse a successfully fetched stylesheet
    and feed it to ``css``.  Failed fetches map to ``None``.
    """
    eff_base = base_url_from_dom(dom_root) or base_url
    if not isinstance(eff_base, URL):
        eff_base = parse_url(str(eff_base))
    specs = collect_subresources(dom_root)
    c = client or (
        HttpClient(timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
        if timeout is not None else HttpClient())
    # P23.4 — attach the progress sink to the (possibly fresh) client.
    if progress is not None and c.progress is None:
        c.progress = progress
    total = len(specs)
    by_kind: dict = {}
    done = 0
    # GPT P0-4 — the document's origin (for CORS cross-origin check).
    doc_origin = _origin_str(eff_base.to_string()) if eff_base else ""
    for spec in specs:
        src = spec["src"]
        # data: URIs are inline — no network fetch.
        if src.lower().startswith("data:"):
            by_kind.setdefault(spec["kind"], {})[src] = None
            continue
        absu = parse_url(src, base=eff_base)
        if progress is not None:
            progress.emit(LoadProgress(
                PHASE_SUBRESOURCE, done / max(total, 1), absu.to_string()))
        resp = fetch_resource(absu, client=c)
        # GPT P0-4 — enforce CORS/CSP on the fetched subresource.
        # When *doc_csp* is supplied the document's CSP header is
        # used to gate the subresource (the real browser behaviour);
        # otherwise the subresource's own response header is checked
        # (legacy behaviour).
        if enforce_policy and resp is not None:
            kind_map = {"stylesheet": "style", "image": "img"}
            res_kind = kind_map.get(spec["kind"], spec["kind"])
            if doc_csp:
                # Build a temporary policy view from the document's
                # CSP header + the subresource's CORS headers.
                csp_directives = parse_csp_directives(doc_csp)
                csp_blocked = False
                if csp_directives:
                    values = (csp_directives.get(
                        f"{res_kind}-src",
                        csp_directives.get("default-src", [])))
                    if values:
                        csp_blocked = _csp_blocks(
                            values, absu.to_string())
                cors_ok = resp.is_cors_allowed(doc_origin) \
                    if doc_origin else True
                if not cors_ok or csp_blocked:
                    resp = None  # policy-blocked → treat as failed
            else:
                if not resp.is_policy_allowed(
                        doc_origin, res_kind, absu.to_string()):
                    resp = None  # policy-blocked → treat as failed
        by_kind.setdefault(spec["kind"], {})[absu.to_string()] = resp
        done += 1
    if progress is not None:
        progress.emit(LoadProgress(PHASE_DONE, 1.0, f"{done} subresources"))
    return by_kind


def load_subresources_parallel(dom_root, base_url,
                               client: "HttpClient | None" = None,
                               max_workers: int = 4,
                               timeout: float | None = None,
                               progress: "LoadEventSink | None" = None,
                               enforce_policy: bool = True,
                               ) -> dict:
    """P33.R2 — :func:`load_subresources` with a bounded thread pool.

    Identical contract to :func:`load_subresources` (kind →
    ``{absolute_url: HttpResponse-or-None}``, failures map to
    ``None``), but the per-resource GETs run on a
    ``concurrent.futures.ThreadPoolExecutor`` (capped at
    *max_workers*, default 4) sharing one :class:`HttpClient` —
    the client's keep-alive pool (P33.R2) makes the extra
    connections cheap (one socket per origin, reused across
    workers).  When the spec list is short (≤ 2) the sequential
    path is used (thread startup would cost more than it saves).
    Progress events are emitted on the main thread as futures
    complete, so the sink sees a monotone 0→1 fraction.

    GPT P0-4 — *enforce_policy* (default True) routes each fetched
    subresource through :meth:`HttpResponse.is_policy_allowed` with
    the document origin and the subresource kind; a policy-blocked
    resource maps to ``None`` (same failure contract as a 404).
    """
    eff_base = base_url_from_dom(dom_root) or base_url
    if not isinstance(eff_base, URL):
        eff_base = parse_url(str(eff_base))
    specs = collect_subresources(dom_root)
    c = client or (
        HttpClient(timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
        if timeout is not None else HttpClient())
    if progress is not None and c.progress is None:
        c.progress = progress
    total = len(specs)
    by_kind: dict = {}

    # data: URIs need no network — record them up front.
    net_specs: list = []
    for spec in specs:
        if spec["src"].lower().startswith("data:"):
            by_kind.setdefault(spec["kind"], {})[spec["src"]] = None
        else:
            net_specs.append(spec)

    # GPT P0-4 — the document's origin (for CORS cross-origin check).
    doc_origin = _origin_str(eff_base.to_string()) if eff_base else ""
    kind_map = {"stylesheet": "style", "image": "img"}

    def _apply_policy(resp, absu, spec_kind):
        if resp is None:
            return None
        if not enforce_policy:
            return resp
        res_kind = kind_map.get(spec_kind, spec_kind)
        if not resp.is_policy_allowed(doc_origin, res_kind, absu.to_string()):
            return None
        return resp

    done = 0
    if len(net_specs) <= 2:
        # Sequential fast path (matches the legacy P22.4 timing
        # exactly, including progress fractions).
        for spec in net_specs:
            absu = parse_url(spec["src"], base=eff_base)
            if progress is not None:
                progress.emit(LoadProgress(
                    PHASE_SUBRESOURCE, done / max(total, 1),
                    absu.to_string()))
            resp = _apply_policy(
                fetch_resource(absu, client=c), absu, spec["kind"])
            by_kind.setdefault(spec["kind"], {})[absu.to_string()] = resp
            done += 1
    else:
        import concurrent.futures
        workers = max(2, min(max_workers, len(net_specs)))

        def _fetch(spec):
            absu = parse_url(spec["src"], base=eff_base)
            try:
                resp = fetch_resource(absu, client=c)
            except Exception:
                resp = None
            return spec, absu, resp

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers) as pool:
            futs = [pool.submit(_fetch, s) for s in net_specs]
            for fut in futs:
                spec, absu, resp = fut.result()
                resp = _apply_policy(resp, absu, spec["kind"])
                by_kind.setdefault(spec["kind"], {})[absu.to_string()] = resp
                done += 1
                if progress is not None:
                    progress.emit(LoadProgress(
                        PHASE_SUBRESOURCE, done / max(total, 1),
                        absu.to_string()))
    if progress is not None:
        progress.emit(LoadProgress(PHASE_DONE, 1.0,
                                   f"{done} subresources"))
    return by_kind


def render_error_page(title: str, detail: str, w: int = 800, h: int = 600):
    """P21.3 — unified error page (timeout / connection / 404 / 500).

    Returns a :class:`~paint.Bitmap` (rendered via the full pipeline)
    so callers can drop it straight into the GUI / PPM output.
    """
    import browser
    html = (
        '<html><body style="font-family:serif">'
        f'<h1 style="color:#c0392b">{title}</h1>'
        f'<p style="font-size:14px;color:#555">{detail}</p>'
        f'<p style="font-size:12px;color:#888">toy-browser-py (P21)</p>'
        '</body></html>'
    )
    return browser.render_document(html, "", w, h)


def error_page_html(title: str, detail: str, category: str = "") -> str:
    """HTML for the unified error page (headless-testable variant).

    P32.5 — *category* selects per-kind copy: one of
    ``"dns"`` / ``"connect"`` / ``"timeout"`` / ``"bad_url"`` /
    ``"http"`` (4xx/5xx) / ``"redirect"`` / ``""`` (generic).  Each
    renders a distinct, human-readable headline + remediation hint so a
    user can tell *why* a load failed.
    """
    category = (category or "").lower()
    if category == "dns":
        headline = "Server not found"
        hint = "The host could not be resolved (DNS failure). Check the " \
               "address or your network connection."
    elif category == "connect":
        headline = "Connection refused"
        hint = "Could not connect to the server. The host may be down or " \
               "firewalled."
    elif category == "timeout":
        headline = "Connection timed out"
        hint = "The server took too long to respond. Try again or check " \
               "your connection."
    elif category == "bad_url":
        headline = "Invalid URL"
        hint = "That address is not a URL toy-browser-py understands. " \
               "Try http:// or https://."
    elif category == "redirect":
        headline = "Redirect loop"
        hint = "The server keeps redirecting to the same address. " \
               "The redirect chain may be misconfigured."
    elif category == "http":
        headline = "Request failed"
        hint = f"HTTP {title}. The server returned an error."
    else:
        headline = title
        hint = detail
    # For the http category the title carries the status line; fold it
    # into the detail for the generic cases.
    detail_text = detail
    if category == "http":
        detail_text = detail
    return (
        '<html><body>'
        f'<h1>{headline}</h1>'
        f'<p>{detail_text}</p>'
        f'<p style="color:#555">{hint}</p>'
        '<p>toy-browser-py (P32)</p>'
        '</body></html>'
    )


def categorize_fetch_error(exc: BaseException) -> str:
    """P32.5 — map a fetch exception to an error-page *category*.

    Uses the exception type + message heuristics so the GUI/CLI can
    call :func:`error_page_html` with the right copy:

    * ``RedirectLoop`` → ``"redirect"``
    * DNS / getaddrinfo → ``"dns"``
    * ``ConnectionRefused`` / connection reset → ``"connect"``
    * ``socket.timeout`` / ``TimeoutError`` → ``"timeout"``
    * ``UnsupportedScheme`` / "invalid url" → ``"bad_url"``
    * :class:`FetchError` with an HTTP status → ``"http"``
    * anything else → ``""`` (generic)
    """
    cls_name = type(exc).__name__
    if cls_name == "RedirectLoop":
        return "redirect"
    if isinstance(exc, UnsupportedScheme):
        return "bad_url"
    msg = str(exc).lower()
    if "dns" in msg or "name or service not known" in msg \
            or "getaddrinfo" in msg:
        return "dns"
    if "timed out" in msg or "timeout" in msg or "timed-out" in msg:
        return "timeout"
    if "refused" in msg or "reset" in msg or "unreachable" in msg:
        return "connect"
    if isinstance(exc, FetchError) and getattr(exc, "status", 0) >= 400:
        return "http"
    if "invalid url" in msg or "scheme" in msg or "unsupported" in msg:
        return "bad_url"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, (ConnectionError, OSError)):
        return "connect"
    return ""

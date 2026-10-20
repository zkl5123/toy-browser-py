"""P21 — URL 解析 + HTTP/1.1 最小客户端（M2 网络栈第一步）。
P22 — 深化：重定向链 + gzip + chunked + 子资源并发加载（M2 网络栈第二步）。
P23 — HTTPS + 磁盘缓存 + 协商缓存 + 加载进度事件（M2 网络栈第三步）。

P23 新增（`docs/项目未来规划.txt` §P23）：

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
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # P0 (agent report) — cookie_store imports net *at function level*
    # (lines 111/275/321), so a module-level ``import cookie_store`` in
    # net.py would be safe at import time, but the string annotation
    # under ``from __future__ import annotations`` keeps it resolvable
    # for static checkers without a runtime import at all.
    from toy_browser.network import cookie_store

# R7-S03 第 2 点 — cookie 上下文（真实 site-for-cookies / credentials）。
# 真实（非 TYPE_CHECKING）导入：cookie_context 只依赖 request_context
# 与 cookie_store（后者在函数级 import net），与 net 无环。
from toy_browser.network import cookie_context as _cookie_ctx
# R7-S03 第 4 点 — 缓存校验（Vary / 授权响应 / 304 头合并 / 凭据隔离）。
from toy_browser.network import cache_validity as _cv
# R7-S04 第 3 点 — deadline / 取消 / 页级聚合预算（纯逻辑决策层）。
from toy_browser.network import request_budget as rb
# R7-S02 — RequestContext 贯穿所有请求（发送前判定 + 逐跳重定向重评 +
# 跨源敏感头剥离）。request_context 只依赖 cookie_context/cookie_store，
# 与 net 无环，可模块级真实导入。
from toy_browser.network import request_context as _rc

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

# R7-S04 — chunk size for the bounded response reader.  Every
# ``read(amt)`` call asks for at most this many bytes, so a hostile
# server cannot make the reader allocate an unbounded intermediate
# buffer in one call, and the accumulated body never exceeds
# ``max_bytes + 1`` before the cap check fires.
_READ_CHUNK = 64 * 1024


def _read_response_bounded(resp, max_bytes: int) -> bytes:
    """R7-S04 — read the whole response body under a *pre-allocation*
    byte cap (shared by the GET and POST paths).

    The legacy code called ``resp.read()`` with no argument, which
    buffers the entire body in RAM *before* any size check could run
    (the S04 audit probe ``read_before_cap`` proved a 129-byte reply
    was fully allocated before ``ResponseTooLarge`` fired).  This
    reader instead pulls at most
    ``min(_READ_CHUNK, max_bytes + 1 - len(out))`` bytes per call, so:

    * no single ``read(amt)`` ever asks for an unbounded amount,
    * the accumulated buffer never exceeds ``max_bytes + 1`` bytes,
    * a body larger than *max_bytes* raises :class:`ResponseTooLarge`
      **before** the oversized buffer is returned to the caller.

    The caller must treat the connection as dead when this raises
    (bytes remain unread, so the socket cannot be pooled).
    """
    out = bytearray()
    while True:
        want = min(_READ_CHUNK, max_bytes + 1 - len(out))
        if want <= 0:
            break  # len(out) == max_bytes + 1 → over cap
        chunk = resp.read(want)
        if not chunk:
            break  # EOF — body fully consumed
        out += chunk
        if len(out) > max_bytes:
            break
    if len(out) > max_bytes:
        raise ResponseTooLarge(
            f"response body exceeds cap {max_bytes} bytes")
    return bytes(out)

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
    # R7-S02 第 3/6 点 — 发送前被策略拦截（CSP / scheme / 混合内容 /
    # 未知 destination / same-origin-mode-却跨源）时，response 以
    # status=0 + ``denied`` 标记落地；``denied`` 是纯逻辑判定
    # (:class:`request_context.RequestDecision`)，``denied_headers``
    # 是"若放行本会剥离敏感头后实际发出去"的请求头（跨源剥离的
    # 证据）。两者都默认 ``None``，正常路径不填。
    denied: object = None          # request_context.RequestDecision
    denied_headers: dict | None = None

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
                         source_value: str,
                         doc_origin: str = "") -> bool:
        """P33.P2 — toy CSP check: return True when *source_value*
        would be blocked by the response's CSP *source_kind* directive
        (e.g. ``script-src``, ``img-src``, ``connect-src``).

        The check is intentionally simple: it collects all allowed
        source tokens from the matching directive (falling back to
        ``default-src`` when the specific directive is absent) and
        returns True when *source_value* matches none of them.
        A blank directive list (no CSP header) permits everything.

        P0 (retest report) — *doc_origin* (optional) makes ``'self'``
        match the document origin instead of unconditionally permitting;
        when blank the legacy pass-through is preserved.
        """
        csp = self.csp_directives
        if not csp:
            return False
        # The specific directive takes precedence; fall back to
        # default-src when absent (R7-S02: a present-but-empty
        # directive is 'none', not "no constraint").
        values = _csp_values_for(csp, source_kind)
        if values is None:
            return False  # no constraint
        return _csp_blocks(values, source_value, doc_origin)

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
          matched against *resource_url*.  P0 (retest report): the
          ``'self'`` keyword is matched against *requesting_origin*
          when it is a real origin (contains "://"), so that a
          cross-origin resource with ``ACAO: *`` is still blocked by
          ``style-src 'self'``.

        *resource_kind* is one of "style" (stylesheet), "script",
        "img" (image), "connect" (fetch/XHR); the directive name is
        derived as ``<kind>-src` with the legacy mapping
        style→"style-src", script→"script-src", img→"img-src",
        connect→"connect-src" (a blank resource_kind skips the CSP
        check).
        """
        # --- CORS check (cross-origin, cors-mode resources only) ---
        # P1 (assessment report) — a stylesheet / image subresource
        # loads in ``no-cors`` mode in browsers: a cross-origin
        # response without ``Access-Control-Allow-Origin`` is still
        # usable for rendering.  Only ``script`` / ``connect`` kinds
        # are cors-mode (their JS read paths need the ACAO header).
        _CORS_MODE_KINDS = {"script", "connect"}
        _res_kind_norm = resource_kind.lower()
        _needs_cors = _res_kind_norm in _CORS_MODE_KINDS
        if _needs_cors and requesting_origin and resource_url:
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
            # P0 (retest report) — pass the document origin so that
            # 'self' matches the document origin, not unconditionally.
            doc_origin = requesting_origin if "://" in requesting_origin else ""
            if self.is_csp_violation(directive, resource_url, doc_origin):
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


def _csp_blocks(values: "list | None", source_value: str,
                doc_origin: str = "") -> bool:
    """GPT P0-4 — return True when *source_value* is blocked by the
    CSP source-list *values* (the same token-matching logic as
    :meth:`HttpResponse.is_csp_violation`, factored out so the
    document-level CSP path in :func:`load_subresources` can reuse
    it without constructing a throwaway HttpResponse).

    P0 (retest report) — ``'self'`` now matches the document origin:
    a source is permitted by ``'self'`` only when its origin equals
    *doc_origin*.  When *doc_origin* is blank the legacy behaviour is
    preserved (``'self'`` is a pass-through) so existing fixtures that
    do not track origins are unaffected.

    R7-S02 — *values* distinguishes three states: ``None`` (no
    directive at all → no constraint, nothing is blocked), a
    non-empty list (token matching below), and ``[]`` — a directive
    present with no values, which per CSP is equivalent to
    ``'none'`` and blocks everything.
    """
    if values is None:
        return False  # no constraint
    if not values:
        return True  # present-but-empty directive == 'none'
    for tok in values:
        tok_lower = tok.lower()
        tok_bare = tok_lower.strip("'")
        if tok_bare == "*":
            # R7-S02 — the wildcard permits any http(s) URL (it does
            # not cover data: / blob: schemes).
            if source_value.lower().startswith(("http://", "https://")):
                return False
            continue
        if tok_bare == "self":
            if not doc_origin:
                # Legacy: no origin tracking → treat 'self' as permit.
                return False
            src_origin = _origin_str(source_value)
            if is_origin_match(doc_origin, src_origin):
                return False
            # 'self' did not match; fall through to the remaining
            # tokens (a mixed list may still permit via another entry).
            continue
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

    R7-S02 — two spec corrections:

    * a directive present with **no values** (``img-src`` alone) is
      recorded as an empty list, which the enforcement helpers treat
      as ``'none'`` (block everything) — it is NOT the same as a
      missing directive (which falls back to ``default-src``);
    * when a directive is **repeated**, the first occurrence wins
      (CSP3 §4.2.3: "if the directive is repeated, ignore all but the
      first").
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
        # R7-S02 — first occurrence of a directive wins.
        if name not in directives:
            directives[name] = values
    return directives


def _csp_values_for(csp_directives: dict,
                    kind: str) -> "list | None":
    """R7-S02 — the source list governing *kind* (a resource kind like
    ``"img"`` or a full directive name like ``"img-src"``).

    Distinguishes the three CSP states the legacy ``.get(...,
    default)`` chain collapsed into one:

    * neither ``<kind>-src`` nor ``default-src`` present → ``None``
      (no constraint — everything is permitted);
    * the directive present with values → that list;
    * the directive present with **no values** → ``[]``, which per
      CSP is equivalent to ``'none'`` (block everything).

    *kind* is normalized so both ``"img"`` and ``"img-src"`` work.
    """
    if not csp_directives:
        return None
    key = kind if kind.endswith("-src") else f"{kind}-src"
    if key in csp_directives:
        return csp_directives[key]
    if "default-src" in csp_directives:
        return csp_directives["default-src"]
    return None


# ---------------------------------------------------------------------------
# R4 stage 0 — unified document policy context (P0-1 of the 4th
# assessment report: GUI/CLI/refresh/history must share ONE policy
# context, so the document's CSP gates subresources identically in both
# entry points)
# ---------------------------------------------------------------------------

@dataclass
class DocumentPolicy:
    """R4 stage 0 — the document-level policy context.

    Built ONCE from the document's actual response headers
    (``Content-Security-Policy`` + its origin, per real-browser
    semantics: the *document's* CSP gates subresources, not each
    subresource's own header) and shared by the CLI render pipeline,
    the GUI page loader, refresh, history back/forward and subresource
    loading — so no path can silently fall back to a blank policy.

    * ``doc_origin`` — the document's origin string
      (``scheme://host[:port]``, default ports stripped), used to
      expand CSP ``'self'`` tokens against the document origin.
    * ``csp_directives`` — the parsed directive → [values] map from
      the ``Content-Security-Policy`` response header (``{}`` when
      the header is absent).
    * ``cors`` — the response's parsed CORS policy
      (``Access-Control-Allow-*`` headers); a cross-origin subresource
      fetch is additionally permitted only when this policy permits
      the requesting origin.
    * ``enforce`` — master switch (default True); when False the
      context still carries the parsed values but callers skip
      enforcement (legacy behaviour for bare fixtures).

    The raw ``Content-Security-Policy`` header string is deliberately
    NOT a field: a single ``HttpResponse`` produces this context, and
    re-parsing the raw string at the call site is exactly the legacy
    divergence the 4th report flagged.

    R4 stage 2 — the legacy *doc_csp* strings of
    :func:`load_subresources` / :func:`load_subresources_parallel`
    are still accepted for unmigrated callers; when both *policy* and
    *doc_csp* are supplied the context wins.  Nonce- and hash-token
    handling lives in the JS engine's inline-script gate
    (:meth:`js.Host._csp_inline_allowed`), not here: this context
    gates *URL sources* against the document's source-list directives.
    """

    doc_origin: str = ""
    csp_directives: dict = field(default_factory=dict)
    cors: "CorsPolicy | None" = None
    enforce: bool = True

    @classmethod
    def from_response(cls, resp: "HttpResponse",
                      doc_origin: str = "") -> "DocumentPolicy":
        """Build the context from a real :class:`HttpResponse`.

        ``doc_origin`` may be blank for opaque sources (``file://`` /
        ``about:``) — the origin is normalised to a scheme-only string
        so ``'self'`` matching stays well-defined.
        """
        origin = doc_origin or cls._origin_from_response(resp)
        directives = dict(resp.csp_directives)  # case-insensitive parse
        cors = resp.cors_policy
        return cls(doc_origin=origin, csp_directives=directives, cors=cors)

    @staticmethod
    def _origin_from_response(resp: "HttpResponse") -> str:
        src = getattr(resp, "final_url", "") or ""
        if src:
            return _origin_str(src)
        ct = getattr(resp, "content_type", "") or ""
        return f"{ct}://" if ct else "about:"

    def csp_header(self) -> str:
        """Re-serialise the parsed directives to the exact header
        string the legacy ``doc_csp=`` parameters of
        :func:`load_subresources` / ``execute_scripts_in_order``
        expect (``; `` between directives, single space between
        tokens).  A no-op round-trip through
        :func:`parse_csp_directives` for a well-formed header."""
        parts: list[str] = []
        for name, values in self.csp_directives.items():
            parts.append(" ".join([name, *values]))
        return "; ".join(parts)

    def csp_allowed(self, source_kind: str, source_value: str) -> bool:
        """Return True when *source_value* is permitted by this
        document policy's CSP *source_kind* directive (falling back to
        ``default-src``), evaluated against :attr:`doc_origin`.
        A blank directive map (no CSP header) permits everything.

        R7-S02 — a directive present with **no values** (``img-src``
        alone) is equivalent to ``'none'`` and blocks everything; it
        is not the same as a missing directive (which falls back to
        ``default-src``).  A bare ``'none'`` token list likewise
        blocks unconditionally.  ``'nonce-…'`` / hash tokens are left
        to the JS-engine's inline gate (R4 stage 2); they never match
        a URL here.
        """
        if not self.csp_directives:
            return True
        values = _csp_values_for(self.csp_directives, source_kind)
        if values is None:
            return True   # no constraint
        if not values:
            return False  # present-but-empty directive == 'none'
        if [v.strip("'").lower() for v in values] == ["none"]:
            return False  # script-src 'none' et al.
        return not _csp_blocks(values, source_value, self.doc_origin)


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


# ---------------------------------------------------------------------------
# R4 stage 2 — CSP nonce / hash token matching (CSP3 §4.1.3, §3.1)
# ---------------------------------------------------------------------------

def csp_nonce_allowed(values: list, element_nonce: str = "") -> bool:
    """R4 stage 2 — return True when an *inline* script/style source is
    permitted by the source-list *values* for a document element that
    carries ``nonce=<element_nonce>``.

    Real-browser semantics (CSP3):

    * ``'self'`` / URL / ``data:`` tokens are URL-source tokens and
      never match an inline source — only a nonce (or hash) or
      ``'unsafe-inline'`` does.  They are ignored here.
    * A ``'nonce-<value>'`` token matches when ``<value>`` equals
      *element_nonce* EXACTLY (case-sensitive; token values are the
      base64 string as it appeared in the header, with any surrounding
      quotes stripped).  An element without a nonce never matches a
      nonce-gated list (an implicit nonce is never generated).
    * ``'unsafe-inline'`` matches any inline source (it may also be
      combined with a nonce in the same list, in which case either
      token permitting is enough — handled by the caller's per-token
      loop; this helper returns True on the first match).
    * ``'none'`` is a block token: when it is the only token it blocks
      unconditionally (handled by the caller / :meth:`DocumentPolicy.
      csp_allowed`).
    """
    if not values:
        return True  # no constraint
    for tok in values:
        bare = tok.strip().strip("'")
        low = bare.lower()
        if low == "unsafe-inline":
            return True
        if low.startswith("nonce-"):
            nonce_val = bare[len("nonce-"):]
            # Case-sensitive exact match (CSP3 §4.1.3).
            if element_nonce and nonce_val == element_nonce:
                return True
            continue
        # URL / 'self' / data: tokens do not match inline sources.
    return False


def csp_hash_allowed(values: list, element_hash: str = "") -> bool:
    """R4 stage 2 — True when the element's source hash
    ``'sha256-…'`` matches a hash token in *values* (exact,
    case-sensitive).  Symmetric to :func:`csp_nonce_allowed` for hash
    sources; an element without a hash never matches a hash-gated
    list."""
    if not values:
        return True
    for tok in values:
        bare = tok.strip().strip("'")
        low = bare.lower()
        if low.startswith("sha256-") or low.startswith("sha384-") or low.startswith("sha512-"):
            if element_hash and bare == element_hash:
                return True
            continue
        if low == "unsafe-inline":
            return True
    return False


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
                 max_response_bytes: int = MAX_RESPONSE_BYTES,
                 deadline: "rb.Deadline | None" = None,
                 page_budget: "rb.PageBudget | None" = None) -> None:
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
        # R7-S03 第 2 点 — 该 client 正在服务的顶层文档 URL。设置后，
        # 未显式给 cookie_context 的请求会按"顶层文档 vs 子资源"真实
        # 判定，而不是退回"同站顶层 GET"缺省值。空串 = 未知（保守）。
        self.top_level_url = ""
        # R7-S04 第 3 点 — deadline / 取消 / 页级聚合预算（纯逻辑决策层
        # request_budget；连接关闭与池清理仍由调用方执行）。
        self.deadline = deadline
        self.page_budget = page_budget
        self._cancel_scope = rb.CancellationScope()

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

    @staticmethod
    def _headers_from_response(resp) -> dict:
        """R7-S05 — lowercase the response headers, preserving EVERY
        ``Set-Cookie`` value.

        The legacy ``{k.lower(): v for k, v in resp.getheaders()}``
        dict comprehension collapsed a multi-line ``Set-Cookie``
        response to its LAST value (the audit's
        ``cookie_scope_validation`` probe: two Set-Cookie lines left
        only ``second`` in the jar).  Duplicated Set-Cookie headers
        are collected into a list under ``"set-cookie"`` — the shape
        :meth:`CookieJar.ingest_response` already understands.
        """
        out: dict = {}
        set_cookies: list = []
        for k, v in resp.getheaders():
            lk = k.lower()
            if lk == "set-cookie":
                set_cookies.append(v)
            else:
                out[lk] = v
        if set_cookies:
            out["set-cookie"] = set_cookies
        return out

    def close_pool(self) -> None:
        """P33.R2 — close every pooled connection (call on shutdown)."""
        for conn in self._conn_pool.values():
            try:
                conn.close()
            except OSError:
                pass
        self._conn_pool.clear()

    def _cookie_ctx_for(self, url: "URL", method: str = "GET",
                        cookie_context=None) -> "_cookie_ctx.CookieRequestContext":
        """R7-S03 第 2 点 — 把调用点给的东西收敛成类型化上下文。

        * 已经是 :class:`CookieRequestContext` → 原样用；
        * 旧 dict（``_subresource_cookie_context`` 的形状）→ 提升；
        * ``None`` → **不再**是"同站顶层 GET"缺省值：顶层文档请求按
          导航上下文（站点取自请求自身），其余交给
          :func:`cookie_context.unknown_context`（保守：不发）。
          绑定了 ``self.top_level_url`` 且与请求不同 URL 时按子资源
          上下文（站点/源取顶层文档的）。
        """
        us = url.to_string() if isinstance(url, URL) else str(url or "")
        if isinstance(cookie_context, _cookie_ctx.CookieRequestContext):
            return cookie_context
        if isinstance(cookie_context, dict):
            return _cookie_ctx.from_legacy_dict(us, cookie_context)
        if cookie_context is None:
            top = getattr(self, "top_level_url", "") or ""
            if top and top != us:
                return _cookie_ctx.for_subresource(us, top, method=method)
            return _cookie_ctx.for_navigation(us, method=method)
        return _cookie_ctx.unknown_context(us, method=method)

    def _emit(self, phase: str, fraction: float, detail: str = "") -> None:
        if self.progress is not None:
            self.progress.emit(LoadProgress(phase, fraction, detail))

    # ------------------------------------------------------------------
    # R7-S04 第 3 点 — deadline / 取消 / 页级预算的门禁
    # ------------------------------------------------------------------

    def cancel(self, reason: str = "cancelled") -> None:
        """请求方（GUI / fetch 宿主）在此处取消整个 client 的在途
        请求。粘性：取消后所有后续请求在门禁处直接抛
        :class:`rb.RequestCancelled`，连接不再复用。"""
        self._cancel_scope.cancel(reason)

    def _admit_request(self, raw_bytes: int = 0, decoded_bytes: int = 0) -> None:
        """一次请求发出**前**的门禁（共同 finally 的入口检查）：

        1. 取消粘性检查（已取消 → 直接抛，连接不动）；
        2. deadline 过期检查（wall-clock，预算耗尽）；
        3. 页级聚合预算检查（``page_budget`` 已耗尽 → 拒绝剩余请求；
           ``would_exceed(raw, decoded)`` 预判这一笔会不会超，也拒）。

        三个检查都**不**做任何 I/O——连接清理由调用方在 except /
        finally 里做，本方法只是"是否放行"的判定。
        """
        self._cancel_scope.check()
        if self.deadline is not None:
            import time as _t
            if self.deadline.expired(_t.monotonic()):
                self._cancel_scope.cancel("deadline exceeded")
                self._cancel_scope.check()  # 转为 RequestCancelled
        if self.page_budget is not None:
            if self.page_budget.exhausted:
                raise ResponseTooLarge(
                    "page aggregate budget exhausted "
                    f"(raw={self.page_budget.raw_used} "
                    f"decoded={self.page_budget.decoded_used})")

    def _charge_page_budget(self, raw_bytes: int, decoded_bytes: int) -> None:
        """一次成功响应之后记账（先成功后记账，失败不计）。"""
        if self.page_budget is not None:
            self.page_budget.charge(raw_bytes, decoded_bytes)

    # ------------------------------------------------------------------
    # R7-S02 — 第 3 点：发送前检查 CSP / connect-src / scheme / 混合内容；
    # 逐跳重定向重新评估，跨源敏感头按规范剥离（不重复造轮子，纯逻辑
    # 决策层，真实发包仍走 _single_get）。
    # ------------------------------------------------------------------

    def _admit_request_context(self, ctx: "_rc.RequestContext",
                               now: float | None = None) -> "_rc.RequestDecision":
        """用 :func:`_rc.evaluate_request` 做一次**发送前**判定。

        * 不允许（deadline / 混合内容阻断 / 未知 destination /
          不支持的 scheme / same-origin-mode-却跨源 / CSP 拒绝）→
          直接返回 ``allowed=False`` 的判定，调用方**不发包**（第 6 点：
          被禁止的请求不得先发包再丢响应）。
        * 允许 → 返回带 ``requires_preflight`` / ``requires_acao`` /
          ``opaque_response`` 标记的判定；这些标记由**响应到达时**的复核
          （:func:`filter_response_headers` / ACAO 比对）落实，不在发送前
          阻塞（否则会把预检/ACAO 的合法流程也挡死）。
        """
        return _rc.evaluate_request(ctx, now=now)

    def _strip_cross_origin_headers(self, headers: dict | None,
                                    document_origin: str = "",
                                    target: "URL | str" = "") -> dict:
        """R7-S02 第 3 点 — 跨源请求 / 跨源重定向按 fetch §4.6 剥离
        敏感请求头（Authorization / Proxy-Authorization / Cookie /
        Cookie2 / WWW-Authenticate / Proxy-Authenticate）。

        * 同源（含源解析失败最保守地不剥离，避免误伤调用方显式传的头）
          → 原样返回。
        * 跨源 → 返回一个新 dict，剔除敏感头，**不修改入参**（调用方
          可能在后续其它目的地下复用同一份 headers）。
        """
        base = dict(headers or {})
        if not target or not base:
            return base
        t = target.to_string() if isinstance(target, URL) else str(target)
        ro = _rc._origin_of(t)
        # document_origin 可能是调用方给的"origin 字符串"（可能带尾斜杠），
        # 统一走 _rc._origin_of 归一化再比。任一源解析不出来 → 最保守
        # 视为跨源，剥离（宁可少发，不多发）。
        do = _rc._origin_of(document_origin) if document_origin else ""
        if not ro or not do or ro.lower() != do.lower():
            return {k: v for k, v in base.items()
                    if str(k).lower() not in _rc.SENSITIVE_REQUEST_HEADERS}
        return base

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
        # R7-S03 第 2 点 — cookie 发送改由**类型化上下文**决定：
        # 跨站（schemeful same-site，逐跳重算）、顶层标记（只有
        # destination=navigate 才算）与 credentials 模式（omit /
        # same-origin / include）三者共同决定发不发。旧代码在这里吃
        # dict 缺省值（"同站 + 顶层 + GET"），且凭据模式从未参与。
        ctx = self._cookie_ctx_for(url, "GET", cookie_context)
        if self.cookies is not None:
            cookie_hdr = ctx.cookie_header(self.cookies)
            if cookie_hdr:
                hdrs["Cookie"] = cookie_hdr
        if headers:
            hdrs.update(headers)

        last_exc: Exception | None = None
        for attempt in (0, 1):
            conn = None
            try:
                # R7-S04 第 3 点 — 共同 finally 的入口门禁：
                # 取消粘性 / deadline / 页级预算，任一失败则
                # 连接不动（尚未 acquire）。
                self._admit_request()
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
                    headers_out = self._headers_from_response(resp)
                    # R7-S04 第 3 点 — 缓存命中也记账（原始=已存 body，
                    # 解压=同，因为 304 复用缓存 body）。
                    self._charge_page_budget(len(body), len(body))
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
                # R7-S04 — bounded chunked read: the cap fires while
                # reading, before the oversized buffer ever exists
                # (the legacy read()-then-check allocated everything
                # first — the S04 read_before_cap probe).
                body = _read_response_bounded(
                    resp, self.max_response_bytes)
                _raw_bytes = len(body)  # 原始字节（记账用）
                # R7-S05 — keep every Set-Cookie line (the legacy dict
                # comprehension dropped all but the last).
                headers_out = self._headers_from_response(resp)
                # P32.3 — ingest Set-Cookie into the jar before the
                # response leaves the client.
                # R7-S03 第 3 点 — GET / 跳转每一跳都走同一个入库
                # 入口（多值 Set-Cookie 全部保留）。
                if self.cookies is not None:
                    ctx.ingest(self.cookies, headers_out)
                final = url.to_string()
                # P22.2 — transparent gzip decode (R7-S04: bounded
                # incremental decode against the instance cap, not a
                # full decompress followed by a size check).
                body = maybe_gunzip(
                    _TransientResp(status=resp.status,
                                   status_text=resp.reason or "",
                                   headers=headers_out,
                                   body=body,
                                   final_url=final),
                    self.max_response_bytes)
                # R7-S04 第 3 点 — 记账：原始字节（已读）+ 解压字节
                # （maybe_gunzip 输出）。失败（ResponseTooLarge）不记。
                self._charge_page_budget(_raw_bytes, len(body))
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
            except ResponseTooLarge:
                # R7-S04 — the body exceeded the cap mid-read (or the
                # bounded gunzip rejected a decompression-bomb): the
                # connection still has unread bytes, so it is closed
                # and must NOT return to the keep-alive pool.
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
                raise
            except rb.RequestCancelled:
                # R7-S04 第 3 点 — 取消是粘性终态：连接关闭、不入池，
                # 且不再重试（直接 re-raise 结束循环）。
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
                raise
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
            max_redirects: int = MAX_REDIRECTS,
            cookie_context: dict | None = None,
            request_context: "_rc.RequestContext | None" = None,
            now: float | None = None) -> HttpResponse:
        """GET *url*; P22.1 — follow 3xx redirects up to
        *max_redirects* (cap 5) with loop detection.  ``follow_redirects``
        False (the P21 behavior) returns the first 3xx as-is.

        P23.2 — when a cache is attached, a fresh cached response is
        returned without touching the wire.  P23.3 — a stale entry
        with an ETag is revalidated (``If-None-Match``); a 304 reuses
        the body.  P23.4 — emits load-progress events when a sink is
        attached.

        R7-S05 — *cookie_context* carries the real request metadata
        (cross-site / method / top-level) for the cookie jar's
        SameSite filter; the legacy default was "same-site top-level
        GET" for every caller, which leaked Lax cookies onto
        cross-site subresource requests.  Subresource loaders pass
        their own context; document navigations keep the legacy
        default when it is omitted.

        Raises :class:`RedirectLoop` when the chain loops or exceeds
        the cap.  4xx/5xx returned as-is so the caller renders the
        error page.  303/307/308 keep or switch method semantics — for
        a GET-only client all are followed with GET.
        """
        if not isinstance(url, URL):
            url = parse_url(url)

        # R7-S02 第 3 点 — 提供 request_context 时做一次**发送前**判定
        # （deadline / 混合内容阻断 / 未知 destination / 不支持的 scheme /
        # same-origin-mode-却跨源 / CSP 拒绝）。被禁的请求**不发包**，
        # 返回一个带原因的占位响应（ok=False、status=0），调用方按
        # 失败处理——绝不"先发再丢"（方案第 6 点）。
        _rc_ctx = request_context
        if _rc_ctx is not None:
            decision = self._admit_request_context(_rc_ctx, now=now)
            if not decision.allowed:
                _doc_origin = _rc_ctx.document_origin or ""
                _stripped = self._strip_cross_origin_headers(
                    headers, _doc_origin, url)
                return HttpResponse(
                    status=0, status_text="denied",
                    headers={}, body=b"",
                    final_url=url.to_string(),
                    denied=decision,
                    denied_headers=_stripped,
                )
            # 允许 → 跨源剥敏感头（fetch §4.6），再走缓存与重定向链。
            _doc_origin = _rc_ctx.document_origin or ""
            headers = self._strip_cross_origin_headers(
                headers, _doc_origin, url)

        self._emit(PHASE_START, 0.0, url.to_string())

        # R7-S03 第 4 点 — 缓存键要带凭据模式与请求头（Vary 匹配 +
        # 授权响应判定），所以先把真实上下文算出来。
        _ctx = self._cookie_ctx_for(url, "GET", cookie_context)
        _cred = _ctx.credentials_mode
        # 请求头键归一化为小写：Vary 字段名是小写，比对与入键都靠它。
        _req_hdrs = {str(k).lower(): v for k, v in (headers or {}).items()}

        # P23.2 — fresh cache hit: skip the wire entirely.
        if self.cache is not None and self.cache.enabled:
            fresh = self.cache.get_fresh(url, credentials_mode=_cred,
                                         request_headers=_req_hdrs)
            if fresh is not None:
                self._emit(PHASE_DOCUMENT, 0.5, "cache hit")
                self._emit(PHASE_DONE, 1.0, "cache hit")
                return fresh

        # P23.3 — stale entry with ETag: revalidate conditionally.
        stale = None
        if self.cache is not None and self.cache.enabled:
            stale = self.cache.get_stale(url, credentials_mode=_cred,
                                         request_headers=_req_hdrs)

        resp = _follow_redirects(
            self, url, headers,
            max_redirects=max_redirects, follow=follow_redirects,
            revalidate_etag=(stale.etag if stale else ""),
            stale_body=(stale.body if stale else b""),
            cookie_context=_ctx,
            request_context=_rc_ctx,
            now=now,
        )

        # P23.2 — store successful, cacheable responses.
        if (self.cache is not None and self.cache.enabled
                and resp.ok and resp.status != 204):
            if getattr(resp, "from_cache", False):
                # P23.3 — a 304 revalidation was surfaced as a 200:
                # refresh the stale entry's freshness instead of
                # overwriting its body.
                # R7-S03 第 4 点 — 304 的头必须**合并到已存头之上**：
                # 304 只带变化的头，Content-Type / CSP 等仍在旧条目里，
                # 直接用 304 的头会把它们丢掉（审计验收项"304 后安全
                # 响应头仍正确"）。
                if stale is not None:
                    resp.headers = _cv.merge_304_headers(
                        stale.headers, resp.headers)
                    self.cache.revalidate_304(
                        url, stale, headers_304=resp.headers,
                        credentials_mode=_cred,
                        request_headers=_req_hdrs)
            else:
                self.cache.put(url, resp, credentials_mode=_cred,
                               request_headers=_req_hdrs)

        self._emit(PHASE_DOCUMENT, 0.5, f"HTTP {resp.status}")
        self._emit(PHASE_DONE, 1.0, f"HTTP {resp.status}")
        return resp

    def post(self, url: "URL | str",
             data: "dict | str | bytes | None" = None,
             content_type: str = "",
             headers: dict | None = None,
             follow_redirects: bool = True,
             max_redirects: int = MAX_REDIRECTS,
             cookie_context: dict | None = None,
             method: str = "POST") -> HttpResponse:
        """P23 — POST *url* with a body.

        *data* may be a mapping (encoded as
        ``application/x-www-form-urlencoded``), a ``str`` (sent verbatim
        with the given *content_type*), or ``bytes`` (sent verbatim).
        For a GET-only cache, POST responses are NOT cached (spec:
        caching non-idempotent methods is out of scope for the toy).
        P23.4 — emits load-progress events when a sink is attached.

        R7-J03 — *method* lets a caller issue another verb (PUT /
        DELETE / PATCH …) through the same body-carrying path; the
        default stays "POST" for every legacy caller.

        P0 (GPT report) — *cookie_context* carries the cross-site /
        top-level metadata for the cookie jar's SameSite filter; a
        cross-site POST must not carry a SameSite=Lax cookie.
        """
        if not isinstance(url, URL):
            url = parse_url(url)
        if url.scheme not in ("http", "https"):
            raise UnsupportedScheme(url.scheme)
        m = (method or "POST").upper()

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
        # R7-S03 第 2 点 — 同 GET：走类型化上下文（凭据门禁 + 真实
        # 跨站/顶层判定），不再吃 dict 缺省值。
        ctx = self._cookie_ctx_for(url, m, cookie_context)
        if self.cookies is not None:
            cookie_hdr = ctx.cookie_header(self.cookies)
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
            conn = None
            try:
                # P33.R2 — acquire a (possibly reused) connection; a
                # first attempt on a stale socket raises
                # ConnectionResetError / RemoteDisconnected and the
                # retry loop opens a fresh one.
                # R7-S04 第 3 点 — 共同 finally 入口门禁（同 GET）。
                self._admit_request()
                conn = self._acquire_conn(url)
                conn.request(m, path, body=body_bytes,
                             headers=hdrs)
                resp = conn.getresponse()
                # R7-S04 — bounded chunked read.  The legacy POST path
                # called resp.read() with no cap at all (the S04
                # response_limits_and_cookie_ingest probe returned a
                # 4096-byte body under a 128-byte cap).
                body = _read_response_bounded(
                    resp, self.max_response_bytes)
                _raw_bytes = len(body)  # 原始字节（记账用）
                # R7-S05 — keep every Set-Cookie line (the legacy dict
                # comprehension dropped all but the last).
                headers_out = self._headers_from_response(resp)
                # R7-S05 — POST responses are ingested too (the legacy
                # POST path never touched the cookie jar).
                # R7-S03 第 3 点 — 与 GET / 跳转共用同一入库入口。
                if self.cookies is not None:
                    ctx.ingest(self.cookies, headers_out)
                conn_hdr = (resp.getheader("Connection") or "").lower()
                self._charge_page_budget(_raw_bytes, len(body))
                self._release_conn(url, conn, dead=("close" in conn_hdr))
                return HttpResponse(
                    status=resp.status,
                    status_text=resp.reason or "",
                    headers=headers_out,
                    body=body,
                    final_url=url.to_string(),
                )
            except ResponseTooLarge:
                # R7-S04 — the POST body exceeded the cap mid-read:
                # close the connection, keep it out of the pool.
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
                raise
            except rb.RequestCancelled:
                # R7-S04 第 3 点 — 取消是粘性终态：连接关闭、不入池，
                # 且不再重试（直接 re-raise 结束循环）。
                if conn is not None:
                    try:
                        conn.close()
                    except OSError:
                        pass
                raise
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
    # R7-S03 第 4 点 — Vary 字段、凭据模式，以及"存这个条目时请求
    # 带的 Vary 字段值"（命中时做 Vary 匹配 + 凭据隔离）。
    vary: list = field(default_factory=list)
    credentials_mode: str = ""
    request_headers: dict = field(default_factory=dict)

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
        # R7-S03 第 3 点 — 多值头（set-cookie 列表）按原样落盘，不再
        # 被 ``str(v)`` 压成 "['a=1']" 这种无法解析的字符串。
        headers_out: dict = {}
        for k, v in (self.headers or {}).items():
            if isinstance(v, (list, tuple)):
                headers_out[k] = [str(x) for x in v]
            else:
                headers_out[k] = str(v)
        return {
            "url": self.url, "status": self.status,
            "headers": headers_out,
            "body_b64": _b64e(self.body),
            "fetched_at": self.fetched_at,
            "date": self.date, "last_modified": self.last_modified,
            "etag": self.etag, "max_age": self.max_age,
            "no_cache": self.no_cache, "no_store": self.no_store,
            "heuristic_ttl": self.heuristic_ttl,
            # R7-S03 第 4 点
            "vary": list(self.vary or []),
            "credentials_mode": self.credentials_mode or "",
            "request_headers": dict(self.request_headers or {}),
        }

    @classmethod
    def from_json(cls, d: dict) -> "_CacheEntry":
        # 旧条目（没有 R7-S03 字段）也能读：缺省即"无 Vary / 未知凭据"。
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
            vary=list(d.get("vary") or []),
            credentials_mode=str(d.get("credentials_mode") or ""),
            request_headers=dict(d.get("request_headers") or {}),
        )


def _b64e(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    import base64
    return base64.b64decode(s.encode("ascii")) if s else b""


def _cache_key(url: "URL", credentials_mode: str = "",
               vary=None, request_headers: "dict | None" = None) -> str:
    """R7-S03 第 4 点 — 缓存键 = URL + 凭据模式 + Vary 字段值。

    URL 自带 origin（不串 origin）；凭据模式参与键（不串凭据策略）；
    Vary 字段值参与键（不同 Accept-Encoding 落不同条目）。
    """
    return _cv.cache_key(url.to_string() if isinstance(url, URL) else str(url),
                         credentials_mode, vary, request_headers)


#: R7-S03 第 4 点 — 缓存凭据桶的**探测顺序**（仅在调用方未声明
#: 凭据模式时使用；已声明的调用方永远走自己的桶）。
_CREDENTIAL_BUCKETS = ("", _cookie_ctx.CRED_INCLUDE,
                       _cookie_ctx.CRED_SAME_ORIGIN, _cookie_ctx.CRED_OMIT)


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


def build_cache_entry(url: "URL", resp: "HttpResponse",
                      credentials_mode: str = "",
                      request_headers: "dict | None" = None,
                      ) -> "_CacheEntry | None":
    """P23.2 — turn an :class:`HttpResponse` into a cacheable entry
    (extracting Cache-Control / ETag / Date / Last-Modified).

    R7-S03 第 4 点 — 先过 :func:`cache_validity.is_storable`：``no-store``、
    不可缓存状态码、``Vary: *``、以及"带 ``Authorization`` 请求拿到的
    响应"（RFC 7234 §3.2）一律**不入库**，返回 ``None`` 并给出 reason
    （不静默放行）。Vary 字段、凭据模式与 Vary 字段值一并入条目。
    """
    decision = _cv.is_storable(resp.status, resp.headers,
                               request_headers, credentials_mode)
    if not decision.allowed:
        return None
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
    # R7-S03 第 3 点 — 多值头（set-cookie）原样保留，不压成字符串。
    headers_out: dict = {}
    for k, v in (resp.headers or {}).items():
        if isinstance(v, (list, tuple)):
            headers_out[k] = [str(x) for x in v]
        else:
            headers_out[k] = str(v)
    vary = list(decision.vary)
    req_hdrs = {}
    for name in vary:
        if name == "*":
            continue
        # 请求头的键大小写不敏感（调用点已归一化，这里再兜一层）。
        val = None
        for k, v in (request_headers or {}).items():
            if str(k).lower() == name:
                val = v
                break
        if val is not None:
            req_hdrs[name] = val
    return _CacheEntry(
        url=url.to_string(),
        status=resp.status,
        headers=headers_out,
        body=resp.body,
        fetched_at=time.time(),
        date=resp.headers.get("date", ""),
        last_modified=resp.headers.get("last-modified", ""),
        etag=resp.headers.get("etag", ""),
        max_age=max_age,
        no_cache=no_cache,
        no_store=no_store,
        heuristic_ttl=heuristic,
        vary=vary,
        credentials_mode=credentials_mode or "",
        request_headers=req_hdrs,
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

    def _paths_for(self, url: "URL", credentials_mode: str) -> tuple[str, str]:
        """**（严格）**按凭据模式定位条目：不做任何回退。"""
        key = _cache_key(url, credentials_mode)
        return (os.path.join(self.root_dir, key + ".json"),
                os.path.join(self.root_dir, key + ".bin"))

    def _paths(self, url: "URL", credentials_mode: str = "",
               vary=None, request_headers: "dict | None" = None
               ) -> tuple[str, str]:
        """R7-S03 第 4 点 — 键含凭据模式（不串凭据 / 不串 origin）。

        凭据模式**已声明**时（``omit`` / ``same-origin`` / ``include``）
        严格按该桶定位，绝不跨桶——这就是"不得串凭据策略"。
        未声明（``""``，直接调用 :class:`CacheStore` 的遗留用法，例如
        测试里"看看刚才那次请求存进去没有"）时按顺序探测已知桶；
        这类调用方本来就没有凭据概念，探测只影响它自己。

        已知限制（如实记录）：每个 (URL, 凭据模式) 只保留**一个**槽
        位；``Vary`` 不同的多个变体会互相覆盖，但**绝不会错配**——
        命中前 :func:`cache_validity.vary_matches` 会比对已存请求头，
        不匹配即视为 miss 并回源。多变体并存需要变体索引，本轮未做。
        """
        if credentials_mode:
            return self._paths_for(url, credentials_mode)
        for bucket in _CREDENTIAL_BUCKETS:
            meta, binp = self._paths_for(url, bucket)
            if os.path.exists(meta) and os.path.exists(binp):
                return meta, binp
        return self._paths_for(url, "")
        return (os.path.join(self.root_dir, key + ".json"),
                os.path.join(self.root_dir, key + ".bin"))

    def put(self, url: "URL", resp: "HttpResponse",
            credentials_mode: str = "",
            request_headers: "dict | None" = None) -> bool:
        """P23.2 — store *resp* for *url*.

        R7-S03 第 4 点 — 是否可存由 :func:`cache_validity.is_storable`
        决定（``no-store`` / 状态码 / ``Vary: *`` / 带 Authorization
        的响应），被拒时返回 ``False`` 且**不落盘**。
        """
        if not self.enabled:
            return False
        entry = build_cache_entry(url, resp, credentials_mode,
                                  request_headers)
        if entry is None or entry.no_store:
            return False
        meta_path, bin_path = self._paths(url, credentials_mode)
        try:
            with open(bin_path, "wb") as f:
                f.write(entry.body)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(entry.to_json(), f, ensure_ascii=False)
        except OSError:
            return False
        return True

    def _load(self, url: "URL", credentials_mode: str = "",
              request_headers: "dict | None" = None,
              ) -> "tuple[_CacheEntry, bytes] | None":
        """读出条目（含 Vary 匹配）；不匹配 / 缺失 / 坏档 → ``None``。"""
        path, bin_path = self._paths(url, credentials_mode)
        if not (os.path.exists(path) and os.path.exists(bin_path)):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = _CacheEntry.from_json(json.load(f))
        except (OSError, ValueError, KeyError):
            return None
        # R7-S03 第 4 点 — Vary 匹配：字段值不同 ⇒ 不是命中。
        if not _cv.vary_matches(entry.vary, entry.request_headers,
                                request_headers or {}):
            return None
        try:
            with open(bin_path, "rb") as f:
                body = f.read()
        except OSError:
            return None
        return entry, body

    def get_fresh(self, url: "URL", now: float | None = None,
                  credentials_mode: str = "",
                  request_headers: "dict | None" = None,
                  ) -> "HttpResponse | None":
        """P23.2 — return a fresh cached :class:`HttpResponse`, or
        ``None`` when absent / stale / disabled / Vary 不匹配。"""
        if not self.enabled:
            return None
        now = now if now is not None else time.time()
        loaded = self._load(url, credentials_mode, request_headers)
        if loaded is None:
            return None
        entry, body = loaded
        if entry.ttl(now) <= 0.0:
            return None
        return _resp_from_entry(entry, body, from_cache=True)

    def get_stale(self, url: "URL", credentials_mode: str = "",
                  request_headers: "dict | None" = None,
                  ) -> "HttpResponse | None":
        """P23.3 — return a *stale* entry that can still be
        revalidated via ``If-None-Match`` (needs an ETag).  Fresh
        entries and no-ETag entries return ``None``."""
        if not self.enabled:
            return None
        loaded = self._load(url, credentials_mode, request_headers)
        if loaded is None:
            return None
        entry, body = loaded
        now = time.time()
        if entry.ttl(now) > 0.0:
            return None  # still fresh — caller should use get_fresh.
        if not entry.etag:
            return None  # no ETag → cannot revalidate; refetch.
        return _resp_from_entry(entry, body, from_cache=True)

    def revalidate_304(self, url: "URL", stale: "HttpResponse",
                       headers_304: "dict | None" = None,
                       credentials_mode: str = "",
                       request_headers: "dict | None" = None) -> None:
        """P23.3 — server returned 304: refresh the cached entry's
        ``fetched_at`` / ``Date`` so it becomes fresh again.

        R7-S03 第 4 点 — 同时用 :func:`cache_validity.merge_304_headers`
        把 304 带来的头**更新到已存头之上**（而不是替换），这样 304
        之后 ``Content-Type`` / CSP 这类只在原始 200 里出现的安全头
        仍然正确。
        """
        if not self.enabled:
            return
        meta_path, _ = self._paths(url, credentials_mode)
        if not os.path.exists(meta_path):
            return
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                entry = _CacheEntry.from_json(json.load(f))
        except (OSError, ValueError, KeyError):
            return
        entry.fetched_at = time.time()
        merged = _cv.merge_304_headers(entry.headers, headers_304 or {})
        entry.headers = merged
        entry.date = merged.get("date", entry.date)
        entry.etag = merged.get("etag", entry.etag)
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(entry.to_json(), f, ensure_ascii=False)
        except OSError:
            return

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


class FetchTransport:
    """R4 stage 3 — a JS-side network transport adapter.

    Wraps a shared :class:`HttpClient` (typically the same keep-alive
    client the URL pipeline already uses for document + subresource
    loads, so connections are pooled across fetch/XHR and the rest of
    the page) and exposes the single-method surface the toy JS engine
    consumes: ``fetch(url) -> HttpResponse | None``.

    * ``None`` is returned when the request fails at the network
      layer (DNS / connect / timeout) so the caller can distinguish
      "failed" from an HTTP error status carried on a real
      :class:`HttpResponse`.
    * An HTTP 4xx/5xx is NOT a failure of the transport: it returns
      the :class:`HttpResponse` (with ``ok=False``) so the engine can
      mirror the spec's "failed fetch" semantics on ``r.ok`` / status.

    This is the object ``browser.py`` / ``gui.py`` inject into
    ``js.Host._fetch_transport`` so that in-page ``fetch()`` /
    ``XMLHttpRequest`` reach the same real service the rest of the
    page does (the report's ``fetch_real`` / ``xhr_real`` cases).
    """

    def __init__(self, client: "HttpClient | None", base_url: str = "",
                 document_url: str = "",
                 credentials_mode: str = "") -> None:
        # R4 stage 3 — *client* may be None when the GUI's load_url was
        # called without a shared HttpClient (the legacy path created a
        # throwaway client inside fetch_html and discarded it).  Build
        # a fresh one so the JS transport always has a working client.
        self._client = client or HttpClient()
        # R4 stage 3 — the document base URL (absolute) so a relative
        # fetch/XHR target ("/data.json") resolves against the page's
        # own origin exactly like the browser's URL resolver does.
        self._base_url = base_url
        # R7-S03 第 2 点 — 发起 fetch/XHR 的**文档** URL 与凭据模式。
        # 没有它们时 in-page fetch 走的是 HttpClient 的"顶层导航"
        # 缺省，等于把 Lax cookie 无条件带给任意跨站目标（审计点名的
        # 缺口）。默认凭据 ``same-origin``（fetch 规范默认）：跨源
        # fetch 默认**不带** cookie。
        self._document_url = document_url or base_url
        self._credentials_mode = (credentials_mode
                                  or _cookie_ctx.CRED_SAME_ORIGIN)

    def fetch(self, url: str, method: str = "GET",
              body: "str | bytes | None" = None,
              headers: "dict | None" = None) -> "HttpResponse | None":
        """R7-J03 — the JS-facing transport used by in-page ``fetch()`` /
        ``XMLHttpRequest``.

        *method* / *body* / *headers* let the engine issue real non-GET
        requests (XHR ``open('POST', …)`` + ``send(body)``): a non-GET
        method routes through :meth:`HttpClient.post` with the given
        body and headers.  ``fetch(url)`` (the legacy 1-arg form) is
        unchanged — a plain GET.
        """
        if not isinstance(url, URL):
            # Relative resolution needs the document base; an empty
            # base makes a relative path unresolvable → fail the fetch
            # (None) rather than guess.
            base = self._base_url or None
            url = parse_url(url, base=base) if base else parse_url(url)
        m = (method or "GET").upper()
        # R7-S03 第 2 点 — in-page fetch/XHR 按"子资源"判定：站点与
        # 源取自文档，凭据模式按规范默认（same-origin）。跨源 fetch
        # 因此不再无条件携带 cookie。
        ctx = _cookie_ctx.for_subresource(
            url.to_string() if isinstance(url, URL) else str(url),
            self._document_url,
            destination=(_cookie_ctx.DEST_FETCH if m in ("GET", "HEAD")
                         else _cookie_ctx.DEST_XHR),
            method=m,
            credentials_mode=self._credentials_mode,
        )
        try:
            # follow_redirects=True mirrors the spec's default fetch
            # behaviour (a 3xx chain yields the final resource, not a
            # 3xx body).  Network-layer errors → None; HTTP error
            # statuses are returned as real responses (the engine
            # rejects the promise / fires onerror on !ok).
            if m in ("GET", "HEAD"):
                return self._client.get(url, headers=dict(headers or {}) or None,
                                        follow_redirects=True,
                                        cookie_context=ctx)
            return self._client.post(
                url, data=body if body is not None else "",
                headers=dict(headers or {}),
                follow_redirects=True, method=m, cookie_context=ctx)
        except (OSError, socket.timeout, socket.error,
                http.client.HTTPException):
            return None
        except FetchError:
            # RedirectLoop etc. — treat as a failed fetch (None).
            return None


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

    R7-S04 — the decode is *incremental and bounded*.  The legacy
    implementation ran ``gzip.decompress(body)`` to completion and
    only then compared the result against the cap, so a
    decompression-bomb had already exhausted RAM by the time the
    check fired.  This version feeds the compressed bytes through
    :func:`gunzip_bounded`, which limits the output per call and
    raises :class:`ResponseTooLarge` as soon as the expansion would
    exceed *max_bytes*.
    """
    enc = resp.headers.get("content-encoding", "").lower()
    if "gzip" not in enc:
        return resp.body
    return gunzip_bounded(resp.body, max_bytes)


def gunzip_bounded(body: bytes,
                   max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """R7-S04 — bounded incremental gzip decode (concatenated members
    supported, like :func:`gzip.decompress`).

    Raises :class:`ResponseTooLarge` when the decoded output would
    exceed *max_bytes* — the check fires *while* decompressing, not
    after the whole expansion is allocated.  Undecodable input (not
    gzip / truncated stream) returns *body* unchanged, preserving the
    lenient P22.2 contract.
    """
    out = bytearray()
    data = bytes(body)
    try:
        while data:
            d = zlib.decompressobj(31)  # 31 = gzip header + trailer
            want = max_bytes + 1 - len(out)
            if want <= 0:
                raise ResponseTooLarge(
                    f"gzip-decompressed body exceeds cap {max_bytes} "
                    f"bytes")
            out += d.decompress(data, want)
            if len(out) > max_bytes or d.unconsumed_tail:
                # Output limit reached with compressed input still
                # pending → the full expansion exceeds the cap.
                raise ResponseTooLarge(
                    f"gzip-decompressed body exceeds cap {max_bytes} "
                    f"bytes")
            if not d.eof:
                # Truncated member — same lenient contract as P22.2
                # (gzip.decompress would raise → raw body returned).
                return body
            data = d.unused_data  # next concatenated member, or b""
    except (zlib.error, EOFError):
        # Not valid gzip — return the raw body unchanged (P22.2).
        return body
    return bytes(out)


def _follow_redirects(client: "HttpClient",
                     url: "URL",
                     headers: dict | None,
                     max_redirects: int = MAX_REDIRECTS,
                     follow: bool = True,
                     revalidate_etag: str = "",
                     stale_body: bytes = b"",
                     cookie_context: dict | None = None,
                     request_context: "_rc.RequestContext | None" = None,
                     now: float | None = None) -> HttpResponse:
    """P22.1 — follow a 3xx redirect chain (cap *max_redirects*,
    loop-detect via the set of visited URLs).  P23.3 — the *first*
    hop may carry a conditional GET (``If-None-Match``) when
    *revalidate_etag* is set; a 304 is surfaced as a 200 backed by
    *stale_body*.

    R7-S05 — *cookie_context* is threaded to every hop.

    R7-S03 第 2 点 — 透传的不再**是同一个对象**：每一跳都用
    :meth:`CookieRequestContext.for_redirect` **重新求值**（跨站标记
    按新 URL 的站点重算，非导航请求的跨源跳转把凭据降为 ``omit``）。
    旧代码把第 0 跳算出的 dict 原样带到最后一跳——``a.test`` 文档里
    的子资源 302 到 ``b.test`` 之后仍被当成同站，Lax cookie 泄漏。

    R7-S02 第 3 点 — *request_context*（可选）：每一跳都用
    :meth:`RequestContext.for_redirect` **重新求值**（跨源跳转把凭据
    降为 ``omit`` 并剥离敏感头，redirect_chain 逐跳追加）。**逐跳先
    判后做**：``for_redirect`` 之后立刻做
    :meth:`HttpClient._admit_request_context`，被禁的那一跳**不发包**，
    直接落地带 ``denied`` 标记的占位响应。第 0 跳不做（由调用方
    在 :meth:`HttpClient.get` 入口已经判过）。"""
    visited: list[URL] = []
    cur = url
    hops = 0
    first = True
    cur_ctx = client._cookie_ctx_for(url, "GET", cookie_context)
    cur_rc = request_context          # 第 0 跳的 context（若提供）
    cur_hdrs = headers                # 第 0 跳的请求头（若跨源已被调用方剥过）
    while True:
        visited.append(cur)
        resp = client._single_get(
            cur, cur_hdrs,
            revalidate_etag=(revalidate_etag if first else ""),
            stale_body=(stale_body if first else b""),
            cookie_context=cur_ctx,
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
        # R7-S03 第 2 点 — 下一跳重新求值（而不是沿用上一跳的判定）。
        cur_ctx = cur_ctx.for_redirect(nxt.to_string())
        # R7-S02 第 3 点 — 下一跳重新评估 + 跨源敏感头剥离（纯逻辑，
        # 未发包）。被禁的那一跳直接落地占位响应，不继续发包。
        if cur_rc is not None:
            cur_rc = cur_rc.for_redirect(nxt.to_string())
            decision = client._admit_request_context(cur_rc, now=now)
            if not decision.allowed:
                _doc_origin = cur_rc.document_origin or ""
                _stripped = client._strip_cross_origin_headers(
                    cur_hdrs, _doc_origin, nxt)
                _denied = HttpResponse(
                    status=0, status_text="denied",
                    headers={}, body=b"",
                    final_url=nxt.to_string(),
                    denied=decision,
                    denied_headers=_stripped,
                )
                _denied.redirected = True
                return _denied
            # 允许 → 跨源剥敏感头，继续下一跳。
            _doc_origin = cur_rc.document_origin or ""
            cur_hdrs = client._strip_cross_origin_headers(
                cur_hdrs, _doc_origin, nxt)
        else:
            # 无 request_context → 保持旧语义（headers 原样透传）。
            cur_hdrs = headers
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
                   timeout: float | None = None,
                   cookie_context: dict | None = None,
                   request_context: "_rc.RequestContext | None" = None,
                   now: float | None = None,
                   ) -> "HttpResponse | None":
    """P22.4 — fetch a subresource (stylesheet / image / script).

    Returns the :class:`HttpResponse` (body bytes) on success, or
    ``None`` on any failure (network error, 4xx/5xx, bad content,
    or a pre-flight policy denial under R7-S02).  Subresource
    loading must never block or crash the page — the caller ignores
    ``None``.

    R7-S05 — *cookie_context* carries the request's cross-site /
    method / top-level metadata to the cookie jar (subresources are
    not top-level navigations; a cross-origin subresource must not
    carry a Lax cookie).

    R7-S02 第 3/6 点 — *request_context* (可选)：发送前用
    :func:`_rc.evaluate_request` 判定一次（deadline / 混合内容阻断 /
    未知 destination / 不支持的 scheme / same-origin-mode-却跨源 /
    CSP 拒绝），被禁的请求**不发包**，直接返回 ``None``（子资源
    失败契约与 404 相同）。跨源敏感头按 :func:`fetch §4.6` 剥离
    （经 :meth:`HttpClient.get` 在发送前统一处理）。
    """
    c = client or (
        HttpClient(timeout=timeout if timeout is not None else DEFAULT_TIMEOUT)
        if timeout is not None else HttpClient())
    if not isinstance(url, URL):
        url = parse_url(url)
    try:
        resp = c.get(
            url, cookie_context=cookie_context,
            request_context=request_context, now=now)
    except Exception:
        return None
    # R7-S02 第 6 点 — 被策略拦截的请求落地为 status=0 + denied，
    # 按子资源失败契约（与 404 相同）返回 None。
    if resp.denied is not None:
        return None
    if not resp.ok:
        return None
    return resp


def _subresource_cookie_context(absu: URL, doc_origin: str) -> dict:
    """R7-S05 — the cookie context for a subresource request: not a
    top-level navigation, and cross-site when the resource's
    registrable site differs from the document's (computed on
    scheme + registrable domain, not a raw host comparison)."""
    from toy_browser.network import cookie_store as _cs
    is_cross_site = True
    if doc_origin:
        try:
            doc_site = _cs.site_for_cookies(doc_origin)
            res_site = _cs.site_for_cookies(absu.to_string())
            is_cross_site = (doc_site != res_site)
        except Exception:
            is_cross_site = not is_origin_match(
                doc_origin, _origin_str(absu.to_string()))
    return {"is_cross_site": is_cross_site,
            "method": "GET",
            "is_top_level": False}


#: R7-S02 — spec kind → request_context.destination 映射（script 需
#: 依 crossorigin / type 细分为 classic-script / module，其余按 kind）。
_SPEC_KIND_TO_DESTINATION = {
    "image": _rc.DEST_IMAGE,
    "stylesheet": _rc.DEST_STYLE,
    "script": _rc.DEST_CLASSIC_SCRIPT,   # 被 _build_subresource_request_context 细化
    "font": _rc.DEST_FONT,
    "connect": _rc.DEST_FETCH,
}


def _build_subresource_request_context(spec: dict, absu: "URL",
                                       doc_origin: str,
                                       policy: "DocumentPolicy | None"
                                       ) -> "_rc.RequestContext":
    """R7-S02 第 3/4 点 — 为子资源 spec 构建发送前判定用的 context。

    * destination：script 按 ``type=module`` / ``crossorigin`` 细分为
      module / classic-script；其余按 spec kind 映射。
    * mode / credentials：按 :func:`_rc.default_mode_for` /
      :func:`_rc.default_credentials_for` 默认值；未知 destination
      保持空串（evaluate 时明确拒绝，不偷换成放行）。
    * policy：透传给 CSP / connect-src 判定。
    """
    kind = spec.get("kind", "")
    if kind == "script":
        if spec.get("script_type") == "module":
            dest = _rc.DEST_MODULE
        elif spec.get("crossorigin") is not None:
            dest = _rc.DEST_CLASSIC_SCRIPT
        else:
            dest = _rc.DEST_CLASSIC_SCRIPT
    else:
        dest = _SPEC_KIND_TO_DESTINATION.get(kind, _rc.DEST_FETCH)
    ctx = _rc.context_for_destination(
        doc_origin, absu.to_string(), dest,
        crossorigin=spec.get("crossorigin"),
        policy=policy,
        base_url=absu.to_string(),
    )
    return ctx


def _subresource_cookie_ctx(absu: "URL", doc_origin: str,
                            destination: str = "",
                            credentials_mode: str = "") -> "_cookie_ctx.CookieRequestContext":
    """R7-S03 第 2 点 — 子资源请求的**类型化** cookie 上下文。

    与 :func:`_subresource_cookie_context`（旧 dict 形状，保留给既有
    调用点与测试）的区别：带 destination 与 credentials 模式，因此
    凭据门禁真正生效（例如无 ``crossorigin`` 的 classic 脚本默认
    ``omit``，不再无条件带 cookie）。
    """
    return _cookie_ctx.for_subresource(
        absu.to_string() if isinstance(absu, URL) else str(absu),
        doc_origin, destination=destination, method="GET",
        credentials_mode=credentials_mode
        or _cookie_ctx.CRED_SAME_ORIGIN)


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
    ``{"kind": "stylesheet"|"image"|"script", "src": <attr value>}``
    in document order.  Only ``<link rel="stylesheet">``, ``<img>``,
    and ``<script src>`` are collected (the P22 spec subset).  Data
    URIs are included (the caller may skip them).

    P0 (retest report) — ``<script src>`` is now collected so the
    URL pipeline can download external scripts and feed them to the
    JS engine (the legacy pipeline only collected stylesheets and
    images; external scripts were silently ignored).

    R7-S06 — each spec also carries the attributes that decide the
    fetch mode: ``crossorigin`` (``""`` for a bare attribute,
    ``"anonymous"`` / ``"use-credentials"`` for explicit values,
    absent → ``None``) and ``script_type`` (the ``type`` attribute).
    A classic script without ``crossorigin`` loads in ``no-cors``
    mode; ``type=module`` or any ``crossorigin`` value makes it a
    cors-mode fetch (see :func:`_subresource_cors_mode`).
    """
    out: list[dict] = []

    def _walk(n):
        tag = getattr(n, "tag", None)
        if tag == "link":
            rel = n.attr("rel") or ""
            href = n.attr("href")
            if href and rel.strip().lower() in ("stylesheet",
                                                "alternate stylesheet"):
                out.append({"kind": "stylesheet", "src": href,
                            "crossorigin": n.attr("crossorigin")})
        elif tag == "img":
            src = n.attr("src")
            if src:
                out.append({"kind": "image", "src": src,
                            "crossorigin": n.attr("crossorigin")})
        elif tag == "script":
            # P0 (retest report) — external script: <script src>
            # (inline <script> with no src has no subresource).
            src = n.attr("src")
            if src:
                out.append({"kind": "script", "src": src,
                            "crossorigin": n.attr("crossorigin"),
                            "script_type": (n.attr("type") or "").lower()})
        for c in getattr(n, "children", []):
            _walk(c)

    _walk(dom_root)
    return out


def _subresource_cors_mode(spec: dict) -> bool:
    """R7-S06 — True when the subresource *spec* is fetched in CORS
    mode (its response is read by JS, so a cross-origin response must
    carry a matching ``Access-Control-Allow-Origin``).

    * a **classic script** (``type`` absent / ``classic`` /
      ``text/javascript``) **without** a ``crossorigin`` attribute
      loads in ``no-cors`` mode — the spec default — and executes
      without needing ACAO (the audit's ``classic_cross_origin_script``
      case: a third-party classic script must not be blocked for a
      missing ACAO);
    * ``type=module`` scripts and any element carrying
      ``crossorigin`` (bare = anonymous, or explicit
      ``anonymous`` / ``use-credentials``) are cors-mode;
    * stylesheets and images are no-cors subresources.
    """
    if spec.get("kind") != "script":
        return False
    if spec.get("script_type") == "module":
        return True
    return spec.get("crossorigin") is not None


def load_subresources(dom_root, base_url, client: HttpClient | None = None,
                      timeout: float | None = None,
                      progress: LoadEventSink | None = None,
                      enforce_policy: bool = True,
                      doc_csp: str = "",
                      policy: DocumentPolicy | None = None) -> dict:
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

    R4 stage 0 — *policy* is the preferred unified
    :class:`DocumentPolicy` context (built once from the document's
    actual response headers via
    :meth:`DocumentPolicy.from_response` and shared by the CLI,
    GUI, refresh and history paths).  When supplied, the context's
    ``csp_directives`` / ``doc_origin`` / ``cors`` govern every
    subresource fetch, and its ``enforce`` flag is authoritative —
    the legacy *enforce_policy* / *doc_csp* arguments are read only
    as a fallback for callers that have not migrated yet.  A legacy
    *doc_csp* string (non-blank, no *policy*) still builds an
    equivalent view, so existing fixtures keep working.

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
    # R4 stage 0 — unify on the DocumentPolicy context when supplied.
    if policy is None and doc_csp:
        # R7-S02 — the policy's origin is the DOCUMENT url; a
        # <base href> only rewrites relative resolution below, never
        # the origin used for 'self' / CORS / SameSite decisions.
        _legacy_origin = (_origin_str(base_url.to_string())
                          if isinstance(base_url, URL)
                          else _origin_str(str(base_url)))
        policy = DocumentPolicy(doc_origin=_legacy_origin,
                                csp_directives=parse_csp_directives(doc_csp))
    if policy is not None:
        doc_csp = policy.csp_header()
        enforce_policy = enforce_policy and policy.enforce
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
    # R7-S02 — computed from the DOCUMENT url (*base_url*), NOT from
    # the <base href>-resolved effective base: <base> must not be able
    # to rewrite the origin that 'self' / CORS / SameSite are
    # evaluated against.
    doc_origin = (_origin_str(base_url.to_string())
                  if isinstance(base_url, URL)
                  else _origin_str(str(base_url)))
    kind_map = {"stylesheet": "style", "image": "img",
                "script": "script"}
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
        # R7-S02 — pre-request CSP gate: a subresource blocked by the
        # document's CSP must never hit the wire (the audit's
        # "img-src none → server request count is zero" probe).  The
        # post-fetch check below stays as defence in depth.
        if (policy is not None and policy.enforce
                and not policy.csp_allowed(
                    kind_map.get(spec["kind"], spec["kind"]),
                    absu.to_string())):
            by_kind.setdefault(spec["kind"], {})[absu.to_string()] = None
            done += 1
            continue
        # R7-S02 第 3/4 点 — 每个 spec 派生自己的 request_context：
        # destination / mode / credentials 按 spec 属性算，发送前做
        # 一次完整判定（scheme / 混合内容 / CSP / 预检标记）。
        _rc_ctx = _build_subresource_request_context(
            spec, absu, doc_origin, policy)
        # R7-S05 — subresource requests carry their real cookie
        # context (subresource = not top-level; cross-site when the
        # registrable site differs from the document's).
        # R7-S03 第 2 点 — 改成类型化上下文并带上 destination，
        # 让凭据门禁真正生效。
        _sub_ctx = _subresource_cookie_ctx(
            absu, doc_origin, kind_map.get(spec["kind"], spec["kind"]))
        resp = fetch_resource(
            absu, client=c, cookie_context=_sub_ctx,
            request_context=_rc_ctx)
        # GPT P0-4 — enforce CORS/CSP on the fetched subresource.
        # When *doc_csp* is supplied the document's CSP header is
        # used to gate the subresource (the real browser behaviour);
        # otherwise the subresource's own response header is checked
        # (legacy behaviour).
        # P1 (assessment report) — per-resource-kind CORS mode:
        # * stylesheets (``<link rel=stylesheet>``) and images are
        #   loaded as *no-cors* subresources in browsers; they do NOT
        #   need ``Access-Control-Allow-Origin`` to apply (the green-
        #   background cross-origin CSS case in the report).
        # * classic scripts (no ``crossorigin``, no ``type=module``)
        #   are *no-cors* too (R7-S06 — the spec default; only
        #   module / crossorigin scripts are cors-mode).
        # * images are *no-cors* (like stylesheets).
        if enforce_policy and resp is not None:
            res_kind = kind_map.get(spec["kind"], spec["kind"])
            if doc_csp:
                # Defence in depth: the pre-request gate above already
                # blocked CSP-forbidden subresources before the wire;
                # re-check the fetched URL so a caller that skips the
                # gate still gets the same answer.
                csp_directives = parse_csp_directives(doc_csp)
                values = _csp_values_for(csp_directives, res_kind)
                csp_blocked = _csp_blocks(
                    values, absu.to_string(), doc_origin)
                # R7-S06 — CORS read check: only for cors-mode
                # resources, and only when the resource is actually
                # cross-origin (same-origin never requires ACAO —
                # the audit's csp_self_same_origin_script case).
                needs_cors = _subresource_cors_mode(spec)
                cors_ok = True
                if (needs_cors and doc_origin
                        and not is_origin_match(
                            doc_origin, _origin_str(absu.to_string()))):
                    cors_ok = resp.is_cors_allowed(doc_origin)
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
                               doc_csp: str = "",
                               policy: DocumentPolicy | None = None,
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

    R4 stage 0 — *policy* (a unified :class:`DocumentPolicy` context
    built from the document's response headers) governs enforcement
    exactly as in :func:`load_subresources`; the legacy
    *enforce_policy* / *doc_csp* arguments remain as fallback for
    unmigrated callers.

    GPT P0-4 — *enforce_policy* (default True) routes each fetched
    subresource through :meth:`HttpResponse.is_policy_allowed` with
    the document origin and the subresource kind; a policy-blocked
    resource maps to ``None`` (same failure contract as a 404).
    """
    # R4 stage 0 — unify on the DocumentPolicy context when supplied.
    if policy is None and doc_csp:
        # R7-S02 — the policy's origin is the DOCUMENT url; <base>
        # only rewrites relative resolution below.
        _legacy_origin = (_origin_str(base_url.to_string())
                          if isinstance(base_url, URL)
                          else _origin_str(str(base_url)))
        policy = DocumentPolicy(doc_origin=_legacy_origin,
                                csp_directives=parse_csp_directives(doc_csp))
    if policy is not None:
        doc_csp = policy.csp_header()
        enforce_policy = enforce_policy and policy.enforce
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
    # R7-S02 — from the DOCUMENT url (*base_url*), never from the
    # <base href>-resolved effective base.
    doc_origin = (_origin_str(base_url.to_string())
                  if isinstance(base_url, URL)
                  else _origin_str(str(base_url)))
    kind_map = {"stylesheet": "style", "image": "img",
                "script": "script"}

    def _csp_pre_blocked(absu, spec) -> bool:
        """R7-S02 — pre-request CSP gate (never send a blocked
        request)."""
        if policy is None or not policy.enforce:
            return False
        return not policy.csp_allowed(
            kind_map.get(spec["kind"], spec["kind"]),
            absu.to_string())

    def _apply_policy(resp, absu, spec):
        if resp is None:
            return None
        if not enforce_policy:
            return resp
        spec_kind = spec["kind"]
        res_kind = kind_map.get(spec_kind, spec_kind)
        if doc_csp:
            # Defence in depth: the pre-request gate already blocked
            # CSP-forbidden subresources before the wire.
            csp_directives = parse_csp_directives(doc_csp)
            values = _csp_values_for(csp_directives, res_kind)
            csp_blocked = _csp_blocks(
                values, absu.to_string(), doc_origin)
            # R7-S06 — cors-mode resources only, and only when the
            # resource is actually cross-origin (same-origin never
            # requires ACAO).
            needs_cors = _subresource_cors_mode(spec)
            cors_ok = True
            if (needs_cors and doc_origin
                    and not is_origin_match(
                        doc_origin, _origin_str(absu.to_string()))):
                cors_ok = resp.is_cors_allowed(doc_origin)
            if not cors_ok or csp_blocked:
                return None
            return resp
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
            if _csp_pre_blocked(absu, spec):
                by_kind.setdefault(spec["kind"], {})[absu.to_string()] = None
                done += 1
                continue
            # R7-S02 第 3/4 点 — 每个 spec 派生 request_context，做
            # 发送前判定（scheme / 混合内容 / CSP / 预检标记）。
            _rc_ctx = _build_subresource_request_context(
                spec, absu, doc_origin, policy)
            resp = _apply_policy(
                fetch_resource(absu, client=c,
                               cookie_context=_subresource_cookie_ctx(
                                   absu, doc_origin, spec["kind"]),
                               request_context=_rc_ctx),
                absu, spec)
            by_kind.setdefault(spec["kind"], {})[absu.to_string()] = resp
            done += 1
    else:
        import concurrent.futures
        workers = max(2, min(max_workers, len(net_specs)))

        def _fetch(spec):
            absu = parse_url(spec["src"], base=eff_base)
            if _csp_pre_blocked(absu, spec):
                return spec, absu, None
            _rc_ctx = _build_subresource_request_context(
                spec, absu, doc_origin, policy)
            try:
                resp = fetch_resource(
                    absu, client=c,
                    cookie_context=_subresource_cookie_ctx(
                        absu, doc_origin, spec["kind"]),
                    request_context=_rc_ctx)
            except Exception:
                resp = None
            return spec, absu, resp

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=workers) as pool:
            futs = [pool.submit(_fetch, s) for s in net_specs]
            for fut in futs:
                spec, absu, resp = fut.result()
                resp = _apply_policy(resp, absu, spec)
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
    from toy_browser.app import browser
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

"""P21 — URL 解析 + HTTP/1.1 最小客户端（M2 网络栈第一步）。
P22 — 深化：重定向链 + gzip + chunked + 子资源并发加载（M2 网络栈第二步）。

P22 新增（`项目未来规划.txt` §P22）：

* **重定向链**：``fetch_html`` / ``HttpClient.get`` 可自动跟随 301/302/
  303/307/308（上限 5 层，循环检测：已见 URL 集 + 超过上限抛
  ``RedirectLoop``）。
* **Content-Encoding: gzip** 解压（``gzip`` 标准库；body 自动解码）。
* **Transfer-Encoding: chunked** 解析（``http.client`` 已透明处理；
  这里保留对原始 chunked 流的单元测试钩子 ``decode_chunked``）。
* **子资源并发加载**：``load_subresources(dom_root, base_url)`` 收集
  ``<link rel=stylesheet>`` / ``<img src>``，基于文档 base 解析相对
  URL，并发 GET（线程池，玩具语义：顺序 + 失败不阻塞），失败静默。
* **``<base>`` 标签玩具**：``base_url_from_dom(dom_root)`` 读
  ``<base href>`` 覆盖默认 base。

测试全部使用本地 ``http.server`` 夹具（离线可测、CI 稳定）。
"""
from __future__ import annotations

import gzip
import http.client
import socket
import ssl
import urllib.parse
from dataclasses import dataclass, field

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


class HttpClient:
    """P21.2 — minimal HTTP/1.1 client.

    Uses :mod:`http.client` (stdlib) so the interface is swappable for
    a hand-rolled socket implementation later (M5 direction B).
    Only GET is supported.  Redirects are NOT followed automatically —
    P22 adds a redirect chain; here the client surfaces the 30x
    response and lets the caller decide.
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT,
                 verify_tls: bool = True) -> None:
        self.timeout = timeout
        self.verify_tls = verify_tls

    def _single_get(self, url: URL,
                    headers: dict | None = None) -> HttpResponse:
        """One wire GET (no redirect following).  P22 gzip-decodes the
        body when ``Content-Encoding: gzip`` is present."""
        if url.scheme not in ("http", "https"):
            raise UnsupportedScheme(url.scheme)

        host = url.host or "localhost"
        port = url.effective_port
        path = url.path or "/"
        if url.query:
            path = f"{path}?{url.query}"

        hdrs = {"Host": url.authority,
                "User-Agent": "toy-browser-py/1.1 (P21-P22)",
                "Accept": "text/html,application/xhtml+xml,*/*",
                # P22.2 — ask for gzip; the server may or may not honor
                # it.  When it does, _single_get decodes below.
                "Accept-Encoding": "gzip"}
        if headers:
            hdrs.update(headers)

        ssl_ctx = None
        if url.is_https:
            ssl_ctx = ssl.create_default_context()
            if not self.verify_tls:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        last_exc: Exception | None = None
        for attempt in (0, 1):  # one retry on transient network errors
            try:
                if url.is_https:
                    conn = http.client.HTTPSConnection(
                        host, port, timeout=self.timeout,
                        context=ssl_ctx,
                    )
                else:
                    conn = http.client.HTTPConnection(
                        host, port, timeout=self.timeout,
                    )
                try:
                    conn.request("GET", path, headers=hdrs)
                    resp = conn.getresponse()
                    body = resp.read()
                    headers_out = {
                        k.lower(): v for k, v in resp.getheaders()
                    }
                    final = url.to_string()
                    # P22.2 — transparent gzip decode.
                    body = maybe_gunzip(
                        _TransientResp(status=resp.status,
                                       status_text=resp.reason or "",
                                       headers=headers_out,
                                       body=body,
                                       final_url=final))
                    return HttpResponse(
                        status=resp.status,
                        status_text=resp.reason or "",
                        headers=headers_out,
                        body=body,
                        final_url=final,
                    )
                finally:
                    conn.close()
            except (socket.timeout, socket.error,
                    http.client.HTTPException, OSError) as e:
                last_exc = e
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

        Raises :class:`RedirectLoop` when the chain loops or exceeds
        the cap.  4xx/5xx returned as-is so the caller renders the
        error page.  303/307/308 keep or switch method semantics — for
        a GET-only client all are followed with GET.
        """
        if not isinstance(url, URL):
            url = parse_url(url)
        return _follow_redirects(self, url, headers,
                                 max_redirects=max_redirects,
                                 follow=follow_redirects)


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


def maybe_gunzip(resp: "HttpResponse") -> bytes:
    """P22.2 — decode the response body when
    ``Content-Encoding: gzip`` is present.  Idempotent: returns the
    body unchanged when the header is absent or decoding fails (a
    failed gunzip must not break the page)."""
    enc = resp.headers.get("content-encoding", "").lower()
    if "gzip" not in enc:
        return resp.body
    try:
        return gzip.decompress(resp.body)
    except (OSError, EOFError, gzip.BadGzipFile):
        return resp.body


def _follow_redirects(client: "HttpClient",
                     url: "URL",
                     headers: dict | None,
                     max_redirects: int = MAX_REDIRECTS,
                     follow: bool = True) -> HttpResponse:
    """P22.1 — follow a 3xx redirect chain (cap *max_redirects*,
    loop-detect via the set of visited URLs)."""
    visited: list[URL] = []
    cur = url
    hops = 0
    while True:
        visited.append(cur)
        resp = client._single_get(cur, headers)
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
    c = client or (HttpClient(timeout=timeout) if timeout else HttpClient())
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
    c = client or (HttpClient(timeout=timeout) if timeout else HttpClient())
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
                      timeout: float | None = None) -> dict:
    """P22.4 — load every subresource in *dom_root* against *base_url*.

    1. Resolve a document-level base: ``<base href>`` wins over
       *base_url* (P22.5).
    2. Collect ``<link rel=stylesheet>`` / ``<img src>`` (document
       order).
    3. Resolve each relative src against the base, then GET each one
       (玩具语义：顺序执行 + 失败不阻塞 —— 一个子资源 404/超时不会
       中断其余加载，也不会影响页面渲染）。

    Returns a mapping ``kind → {absolute_url: HttpResponse-or-None}``
    so the caller can, e.g., parse a successfully fetched stylesheet
    and feed it to ``css``.  Failed fetches map to ``None``.
    """
    eff_base = base_url_from_dom(dom_root) or base_url
    if not isinstance(eff_base, URL):
        eff_base = parse_url(str(eff_base))
    specs = collect_subresources(dom_root)
    c = client or (HttpClient(timeout=timeout) if timeout else HttpClient())
    by_kind: dict = {}
    for spec in specs:
        src = spec["src"]
        # data: URIs are inline — no network fetch.
        if src.lower().startswith("data:"):
            by_kind.setdefault(spec["kind"], {})[src] = None
            continue
        absu = parse_url(src, base=eff_base)
        resp = fetch_resource(absu, client=c)
        by_kind.setdefault(spec["kind"], {})[absu.to_string()] = resp
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


def error_page_html(title: str, detail: str) -> str:
    """HTML for the unified error page (headless-testable variant)."""
    return (
        '<html><body>'
        f'<h1>{title}</h1><p>{detail}</p>'
        '<p>toy-browser-py (P21)</p>'
        '</body></html>'
    )

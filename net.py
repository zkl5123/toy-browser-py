"""P21 — URL 解析 + HTTP/1.1 最小客户端（M2 网络栈第一步）。

设计目标（`项目未来规划.txt` §P21）：

* **URL 解析**：scheme / host / port / path / query / fragment 全量
  解析，相对 URL 按 ``urljoin`` 规范解析，URL 规范化（端口默认值、
  空 path → ``/``、scheme/host 小写化）。
* **HTTP/1.1 最小客户端**：``http.client`` 实现（标准库，接口设计
  成可替换，为以后手写 socket 留口子），GET 请求 + ``Host`` 头 +
  状态码分派（200 / 301 / 404 / 500）。
* **Content-Type 分派**：``text/html`` → 渲染管线；其他类型 → 下载
  / 忽略（返回原始字节供调用方处理）。
* **超时 / 连接错误 → 统一错误页**：渲染层消费
  :class:`PageError` 生成统一错误页 HTML。

测试全部使用本地 ``http.server`` 夹具（离线可测、CI 稳定）。
"""
from __future__ import annotations

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

    def get(self, url: "URL | str",
            headers: dict | None = None) -> HttpResponse:
        """GET *url*; return the (single) :class:`HttpResponse`.

        3xx responses are returned as-is (``redirected`` stays False;
        P22 follows them).  4xx/5xx also returned as-is so the caller
        can render the error page.
        """
        if not isinstance(url, URL):
            url = parse_url(url)
        if url.scheme not in ("http", "https"):
            raise UnsupportedScheme(url.scheme)

        host = url.host or "localhost"
        port = url.effective_port
        path = url.path or "/"
        if url.query:
            path = f"{path}?{url.query}"

        hdrs = {"Host": url.authority,
                "User-Agent": "toy-browser-py/1.0 (P21)",
                "Accept": "text/html,application/xhtml+xml,*/*"}
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
                    if "location" in headers_out and \
                            resp.status in (301, 302, 303, 307, 308):
                        # Surface the redirect; P22 will follow it.
                        return HttpResponse(
                            status=resp.status,
                            status_text=resp.reason or "",
                            headers=headers_out,
                            body=body,
                            final_url=final,
                            redirected=False,
                        )
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


# ---------------------------------------------------------------------------
# P21.3 — Content-Type 分派 + 统一错误页
# ---------------------------------------------------------------------------

class UnsupportedScheme(Exception):
    pass


class FetchError(Exception):
    """Unified fetch failure (timeout / connection / bad status)."""

    def __init__(self, message: str, status: int = 0, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url


def fetch_html(url: "URL | str", client: HttpClient | None = None,
               timeout: float | None = None) -> tuple[str, HttpResponse]:
    """GET *url* and return ``(html_text, response)``.

    *text/html* (or empty Content-Type) → UTF-8 decoded body.
    Other Content-Types raise :class:`FetchError` with the status so
    the caller can render an appropriate page (download / unsupported).
    """
    c = client or (HttpClient(timeout=timeout) if timeout else HttpClient())
    if not isinstance(url, URL):
        url = parse_url(url)
    resp = c.get(url)
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

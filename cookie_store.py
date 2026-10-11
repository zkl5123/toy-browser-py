"""P32.3 — Cookie toy: parse ``Set-Cookie`` responses, store per origin,
and emit a request ``Cookie`` header.

Deliberately *no security boundary* (per the P32 plan): this is a
learning aid that round-trips cookies between the browser and its own
requests.  It does NOT enforce SameSite / Secure / HttpOnly / path
matching for security — it honours ``domain`` / ``path`` / ``max-age`` /
``expires`` just enough to behave like a real jar on the happy path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


class Cookie:
    """One cookie in the jar."""
    __slots__ = ("name", "value", "domain", "path", "expires")

    def __init__(self, name: str, value: str,
                 domain: str = "", path: str = "/",
                 expires: float | None = None) -> None:
        self.name = name
        self.value = value
        self.domain = domain
        self.path = path or "/"
        self.expires = expires  # epoch seconds; None = session cookie

    @property
    def expired(self) -> bool:
        if self.expires is None:
            return False
        return datetime.now(timezone.utc).timestamp() > self.expires

    def __repr__(self) -> str:
        return (f"Cookie({self.name!r}={self.value!r}, domain={self.domain!r}, "
                f"path={self.path!r}, expires={self.expires})")


def parse_set_cookie(header: str, base_url: "net.URL | str") -> Optional[Cookie]:
    """P32.3 — parse one ``Set-Cookie`` header value into a :class:`Cookie`.

    Handles ``name=value`` plus the attributes ``Domain`` / ``Path`` /
    ``Max-Age`` / ``Expires``.  Returns ``None`` when the header has no
    usable ``name=value`` pair (malformed → drop silently, per the toy).
    """
    if not header:
        return None
    import net as _net
    base = base_url if isinstance(base_url, _net.URL) else _net.parse_url(base_url)

    # Split on the first '='; the left side is the cookie name.
    eq = header.find("=")
    if eq <= 0:
        return None
    name = header[:eq].strip()
    if not name:
        return None
    rest = header[eq + 1:]
    parts = [p.strip() for p in rest.split(";")]
    value = parts.pop(0) if parts else ""

    domain = base.host or ""
    path = "/"
    expires: float | None = None

    for attr in parts:
        if not attr:
            continue
        low = attr.lower()
        if low.startswith("domain="):
            domain = attr.split("=", 1)[1].strip().lstrip(".")
        elif low.startswith("path="):
            path = attr.split("=", 1)[1].strip() or "/"
        elif low.startswith("max-age="):
            try:
                delta = float(attr.split("=", 1)[1])
                if delta <= 0:
                    # max-age <= 0 means "delete now" → treat as expired.
                    expires = 0.0
                else:
                    expires = datetime.now(timezone.utc).timestamp() + delta
            except ValueError:
                expires = None
        elif low.startswith("expires="):
            raw = attr.split("=", 1)[1].strip()
            expires = _parse_http_date(raw)
    return Cookie(name, value, domain, path, expires)


def _parse_http_date(raw: str) -> Optional[float]:
    """Parse a rough HTTP date; returns epoch seconds or ``None``."""
    if not raw:
        return None
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


class CookieJar:
    """P32.3 — an in-memory cookie jar keyed by (domain, path, name).

    Not persisted to disk in this milestone (the P32 plan scopes cookies
    to "store + send back" without a security model); wire it through the
    :class:`net.HttpClient` per session.
    """

    def __init__(self) -> None:
        self._cookies: list[Cookie] = []

    # -- ingestion -----------------------------------------------------
    def ingest_response(self, url: "net.URL | str", headers: dict) -> int:
        """Consume every ``set-cookie`` header from *headers* (a lowercase
        dict as produced by :mod:`net`).  Returns the number of cookies
        stored/refreshed.

        ``http.client`` collapses multiple ``Set-Cookie`` lines into a
        single comma-joined value when keyed case-insensitively; both a
        plain string and a list under ``"set-cookie"`` are accepted.
        """
        raw = headers.get("set-cookie")
        if raw is None:
            return 0
        raw_values = raw if isinstance(raw, list) else [raw]
        stored = 0
        for value in raw_values:
            if not isinstance(value, str):
                continue
            c = parse_set_cookie(value, url)
            if c is None:
                continue
            self._upsert(c)
            stored += 1
        return stored

    def _upsert(self, c: Cookie) -> None:
        for i, existing in enumerate(self._cookies):
            if (existing.name == c.name
                    and existing.domain == c.domain
                    and existing.path == c.path):
                self._cookies[i] = c
                return
        self._cookies.append(c)

    # -- emission ------------------------------------------------------
    def cookie_header_for(self, url: "net.URL | str") -> str:
        """Build the ``Cookie`` request header value for *url*.

        Matches by suffix on the host (a cookie set for ``.example.com``
        applies to ``example.com`` and subdomains) and by path
        prefix.  Expired / session cookies are included as-is.
        """
        import net as _net
        base = url if isinstance(url, _net.URL) else _net.parse_url(url)
        host = (base.host or "").lower()
        parts: list[str] = []
        for c in self._cookies:
            if c.expired:
                continue
            cd = (c.domain or "").lower()
            if not cd:
                continue
            if host != cd and not host.endswith("." + cd):
                continue
            path = base.path or "/"
            if not (path == c.path or path.startswith(c.path.rstrip("/") + "/")
                    or c.path == "/"):
                continue
            parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    # -- introspection -------------------------------------------------
    def for_url(self, url: "net.URL | str") -> list[Cookie]:
        """All live cookies that would be sent to *url* (mirror of
        :meth:`cookie_header_for`, returned as :class:`Cookie` objects)."""
        import net as _net
        base = url if isinstance(url, _net.URL) else _net.parse_url(url)
        host = (base.host or "").lower()
        out: list[Cookie] = []
        for c in self._cookies:
            if c.expired:
                continue
            cd = (c.domain or "").lower()
            if cd and (host == cd or host.endswith("." + cd)):
                out.append(c)
            elif not cd:
                out.append(c)
        return out

    def clear(self) -> None:
        self._cookies = []

    def __len__(self) -> int:
        return len(self._cookies)

    def all(self) -> list[Cookie]:
        return [c for c in self._cookies]

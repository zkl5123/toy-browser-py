"""P32.3 — Cookie toy: parse ``Set-Cookie`` responses, store per origin,
and emit a request ``Cookie`` header.

P0 (GPT report) — the legacy implementation deliberately had *no
security boundary* (per the P32 plan's "store + send back" scope).  A
"truly usable browser" must at least enforce:

* **Secure** — a cookie with the ``Secure`` attribute is only sent
  over ``https://`` (or loopback hosts used for local development).
* **HttpOnly** — a cookie with the ``HttpOnly`` attribute is never
  exposed to the JS engine's ``document.cookie`` API; it is only sent
  in the HTTP request header.
* **SameSite** — ``Strict`` / ``Lax`` / ``None`` control whether the
  cookie is sent on cross-site requests.  ``Lax`` (the default when
  the attribute is absent, matching Chromium's behaviour) is sent on
  top-level GET navigations but not on cross-site POST / sub-resource
  requests; ``Strict`` is only sent on same-site requests; ``None``
  is always sent (and *requires* ``Secure`` per the spec — we enforce
  that).
* **Domain validation** — a ``Set-Cookie`` whose ``Domain`` attribute
  does not suffix-match the request host is rejected (a cookie from
  ``a.com`` cannot set a cookie for ``b.com``).

A full RFC 6265bis implementation (PublicSuffixList, ``Domain``
suffix-match rules, scriptable same-site partitioning) is out of
scope for the toy engine; the flag plumbing above is the minimum a
usable browser needs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

__all__ = [
    "Cookie", "CookieJar", "parse_set_cookie",
]


class Cookie:
    """One cookie in the jar."""
    __slots__ = (
        "name", "value", "domain", "path", "expires",
        # P0 (GPT report) — security flags.
        "secure", "http_only", "same_site",
    )

    def __init__(self, name: str, value: str,
                 domain: str = "", path: str = "/",
                 expires: float | None = None,
                 secure: bool = False,
                 http_only: bool = False,
                 same_site: str = "lax") -> None:
        self.name = name
        self.value = value
        self.domain = domain
        self.path = path or "/"
        self.expires = expires  # epoch seconds; None = session cookie
        # P0 — security flags (default: secure=False, http_only=False,
        # same_site="lax" matching Chromium's default for new cookies).
        self.secure = secure
        self.http_only = http_only
        self.same_site = (same_site or "lax").lower()

    @property
    def expired(self) -> bool:
        if self.expires is None:
            return False
        return datetime.now(timezone.utc).timestamp() > self.expires

    def __repr__(self) -> str:
        return (f"Cookie({self.name!r}={self.value!r}, "
                f"domain={self.domain!r}, path={self.path!r}, "
                f"expires={self.expires}, "
                f"secure={self.secure}, http_only={self.http_only}, "
                f"same_site={self.same_site!r})")


def _domain_matches(cookie_domain: str, host: str) -> bool:
    """P0 (GPT report) — RFC 6265 §5.1.3 domain-match: *cookie_domain*
    is a suffix of *host* AND (the last character of *host* before the
    suffix is ``.`` OR the two are equal).  A cookie set for
    ``example.com`` matches ``example.com`` and ``sub.example.com``;
    a cookie set for ``b.com`` does NOT match ``a.com`` (the legacy
    ``host.endswith(cd)`` check had this bug: ``"a.com".endswith("com")``
    → True)."""
    cd = (cookie_domain or "").lower().lstrip(".")
    h = (host or "").lower()
    if not cd or not h:
        return False
    if h == cd:
        return True
    return h.endswith("." + cd)


def parse_set_cookie(header: str, base_url: "net.URL | str") -> Optional[Cookie]:
    """P32.3 — parse one ``Set-Cookie`` header value into a :class:`Cookie`.

    Handles ``name=value`` plus the attributes ``Domain`` / ``Path`` /
    ``Max-Age`` / ``Expires`` / ``Secure`` / ``HttpOnly`` / ``SameSite``.
    Returns ``None`` when the header has no usable ``name=value`` pair
    (malformed → drop silently, per the toy).

    P0 (GPT report) — the ``Domain`` attribute is validated against the
    request host: a cookie from ``a.com`` cannot set a cookie for
    ``b.com`` (suffix-match + dot-boundary check).  ``SameSite=None``
    without ``Secure`` is rejected (the spec requires the two to be
    paired); the cookie is dropped rather than silently downgraded.
    """
    if not header:
        return None
    import net as _net
    base = base_url if isinstance(base_url, _net.URL) else _net.parse_url(base_url)
    host = (base.host or "").lower()

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

    domain = host  # default: the request host
    path = "/"
    expires: float | None = None
    secure = False
    http_only = False
    same_site = "lax"  # Chromium's default for new cookies

    for attr in parts:
        if not attr:
            continue
        low = attr.lower()
        if low.startswith("domain="):
            domain = attr.split("=", 1)[1].strip().lstrip(".").lower()
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
        # P0 (GPT report) — security attribute flags.
        elif low == "secure":
            secure = True
        elif low == "httponly":
            http_only = True
        elif low.startswith("samesite="):
            same_site = attr.split("=", 1)[1].strip().lower() or "lax"

    # P0 — SameSite=None requires Secure (spec §4.1.4).  A SameSite=None
    # cookie without Secure is rejected rather than silently downgraded.
    if same_site == "none" and not secure:
        return None

    # P0 — Domain suffix-match validation: the Domain attribute must
    # suffix-match the request host (RFC 6265 §5.1.3).  A cookie from
    # a.com cannot set a cookie for b.com.
    if domain and host and not _domain_matches(domain, host):
        return None

    return Cookie(name, value, domain, path, expires,
                 secure=secure, http_only=http_only, same_site=same_site)


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
    def ingest_response(self, url: "net.URL | str",
                        headers: dict) -> int:
        """Consume every ``set-cookie`` header from *headers* (a lowercase
        dict as produced by :mod:`net`).  Returns the number of cookies
        stored/refreshed.

        ``http.client`` collapses multiple ``Set-Cookie`` lines into a
        single comma-joined value when keyed case-insensitively; both a
        plain string and a list under ``"set-cookie"`` are accepted.

        P0 (GPT report) — the request *scheme* is now passed to
        :func:`parse_set_cookie` indirectly via the URL so that the
        ``Secure`` / ``SameSite=None`` validation can use it.
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
    def cookie_header_for(self, url: "net.URL | str",
                          is_cross_site: bool = False,
                          method: str = "GET",
                          is_top_level: bool = True) -> str:
        """Build the ``Cookie`` request header value for *url*.

        P0 (GPT report) — the legacy implementation matched only on
        host suffix + path prefix and sent every live cookie on every
        request.  The P0 fix adds the three security filters:

        * **Secure** — a ``Cookie.secure`` cookie is only sent when the
          request scheme is ``https`` (or the host is loopback, for
          local development).
        * **SameSite** — ``is_cross_site`` / ``method`` /
          ``is_top_level`` mirror the request-flow metadata that the
          :class:`net.HttpClient` now carries per request:

          - ``same_site == "strict"``: only sent on same-site
            requests (``is_cross_site`` must be False).
          - ``same_site == "lax"``: sent on same-site requests, and
            on cross-site *top-level GET* navigations only (not on
            cross-site POST or sub-resource requests).
          - ``same_site == "none"``: always sent (cross-site OK).

        * **HttpOnly** does NOT affect the header emission (it is
          always sent in the HTTP request); it only affects the JS
          engine's ``document.cookie`` API (see the JS host's
          ``document.cookie`` getter, which checks this flag).

        Matches by suffix on the host (a cookie set for
        ``.example.com`` applies to ``example.com`` and subdomains)
        and by path prefix.  Expired cookies are skipped.
        """
        import net as _net
        base = url if isinstance(url, _net.URL) else _net.parse_url(url)
        host = (base.host or "").lower()
        scheme = (base.scheme or "").lower()
        is_https = scheme == "https"
        is_loopback = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
        parts: list[str] = []
        for c in self._cookies:
            if c.expired:
                continue
            cd = (c.domain or "").lower()
            if not cd:
                continue
            if not _domain_matches(cd, host):
                continue
            path = base.path or "/"
            if not (path == c.path or path.startswith(c.path.rstrip("/") + "/")
                    or c.path == "/"):
                continue
            # P0 — Secure filter: a Secure cookie is only sent over
            # https:// (or loopback for local development).
            if c.secure and not (is_https or is_loopback):
                continue
            # P0 — SameSite filter.
            if c.same_site == "strict" and is_cross_site:
                continue
            if c.same_site == "lax" and is_cross_site:
                # Lax: cross-site top-level GET only.
                if not (is_top_level and method.upper() == "GET"):
                    continue
            # "none": always sent (Secure was already enforced above).
            parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    # -- introspection -------------------------------------------------
    def for_url(self, url: "net.URL | str",
                is_cross_site: bool = False,
                method: str = "GET",
                is_top_level: bool = True) -> list[Cookie]:
        """All live cookies that would be sent to *url* (mirror of
        :meth:`cookie_header_for`, returned as :class:`Cookie` objects).

        P0 (GPT report) — accepts the same cross-site / method /
        top-level parameters as :meth:`cookie_header_for` and applies
        the Secure + SameSite filters identically.
        """
        import net as _net
        base = url if isinstance(url, _net.URL) else _net.parse_url(url)
        host = (base.host or "").lower()
        scheme = (base.scheme or "").lower()
        is_https = scheme == "https"
        is_loopback = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
        out: list[Cookie] = []
        for c in self._cookies:
            if c.expired:
                continue
            cd = (c.domain or "").lower()
            if not cd:
                continue
            if not _domain_matches(cd, host):
                continue
            # P0 — Secure filter.
            if c.secure and not (is_https or is_loopback):
                continue
            # P0 — SameSite filter.
            if c.same_site == "strict" and is_cross_site:
                continue
            if c.same_site == "lax" and is_cross_site:
                if not (is_top_level and method.upper() == "GET"):
                    continue
            out.append(c)
        return out

    def clear(self) -> None:
        self._cookies = []

    def __len__(self) -> int:
        return len(self._cookies)

    def all(self) -> list[Cookie]:
        return [c for c in self._cookies]

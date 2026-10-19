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

R7-S03/S05 (网络脚本与安全审计.md §4) — the round-7 audit found the
remaining scope bugs and this module now also implements:

* **host-only** — a cookie set without a ``Domain`` attribute is
  host-only (``host_only=True``) and must NOT leak to subdomains
  (the legacy jar sent host-only cookies to subdomains).
* **Public-suffix validation** — a controlled mini-PSL (reserved
  special-use domains + common two-label suffixes) rejects
  ``Domain=test`` from ``a.test`` (a public suffix cannot be a
  cookie's registrable domain; the audit's sibling-domain leak).
* **Default path** — a ``Set-Cookie`` without ``Path`` defaults to
  the request URI's directory (RFC 6265 §5.1.4), not ``/``.
* **Prefix rules** — ``__Secure-`` requires ``Secure`` from a secure
  origin; ``__Host-`` additionally requires ``Path=/`` and no
  ``Domain`` (host-only).
* **SameSite normalization** — an illegal ``SameSite`` value falls
  back to the ``Lax`` default instead of being stored verbatim (a
  stored ``SameSite=bogus`` silently matched none of the filters and
  was sent everywhere — the audit's "default protection failed").
* **Site for cookies** — :func:`site_for_cookies` computes
  ``scheme://registrable-domain`` (schemeful same-site) so callers
  can decide cross-site without a raw host comparison.
* **Quotas** — the jar caps the cookie count and the per-cookie size
  (evicting the oldest entry when full).

A full RFC 6265bis implementation (a complete PublicSuffixList with
update strategy, scriptable same-site partitioning) is out of scope
for the toy engine; the mini-PSL above is a *controlled subset* and
the flag plumbing is the minimum a usable browser needs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

__all__ = [
    "Cookie", "CookieJar", "parse_set_cookie",
    "registrable_domain", "site_for_cookies",
]

# R7-S05 — jar quotas (Chromium: 180 cookies per domain, 3000 total;
# the toy keeps one global cap well below that).
MAX_COOKIES = 300
MAX_COOKIE_BYTES = 4096

# R7-S05 — hosts treated as secure origins for local development
# (a Secure cookie may be set from a loopback http:// origin).
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

# R7-S05 — a *controlled subset* of the Public Suffix List: the
# IANA special-use / reserved domains (always public suffixes) plus
# common two-label public suffixes.  Unknown suffixes fall back to
# "last label is the public suffix", which is correct for every
# ordinary TLD.  Updating this set is the documented PSL update
# strategy for the toy; it is deliberately NOT fetched from the
# network (no runtime dependency, no surprise).
_MINI_PSL = frozenset({
    # IANA special-use domains (RFC 6761 / RFC 6762).
    "test", "example", "invalid", "localhost", "local",
    # Common two-label public suffixes.
    "co.uk", "ac.uk", "org.uk", "gov.uk", "sch.uk", "me.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "id.au",
    "com.br", "net.br", "gov.br", "org.br",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
    "co.in", "net.in", "org.in", "gov.in", "ac.in",
    "co.za", "org.za", "com.tr", "gov.tr",
})


class Cookie:
    """One cookie in the jar."""
    __slots__ = (
        "name", "value", "domain", "path", "expires",
        # P0 (GPT report) — security flags.
        "secure", "http_only", "same_site",
        # R7-S05 — host-only marker (no Domain attribute was set) and
        # the creation time (epoch seconds) used for oldest-first
        # eviction when the jar is over quota.
        "host_only", "creation_time",
    )

    def __init__(self, name: str, value: str,
                 domain: str = "", path: str = "/",
                 expires: float | None = None,
                 secure: bool = False,
                 http_only: bool = False,
                 same_site: str = "lax",
                 host_only: bool = True,
                 creation_time: "float | None" = None) -> None:
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
        # R7-S05 — host-only cookies apply to the exact host only
        # (never to subdomains); Domain cookies (host_only=False)
        # apply to the domain and its subdomains.
        self.host_only = host_only
        self.creation_time = (creation_time if creation_time is not None
                              else datetime.now(timezone.utc).timestamp())

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
                f"same_site={self.same_site!r}, "
                f"host_only={self.host_only})")


def _is_ip_literal(host: str) -> bool:
    """R7-S05 — True when *host* is an IPv4 / IPv6 literal (IPs have
    no registrable domain: the host itself is the site)."""
    h = (host or "").strip("[]")
    if not h:
        return False
    if ":" in h:
        return True  # IPv6
    parts = h.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


def _is_public_suffix(domain: str) -> bool:
    """R7-S05 — True when *domain* is a public suffix (cannot be the
    registrable domain of a cookie).  A single label is always a
    public suffix; known two-label suffixes come from the controlled
    :data:`_MINI_PSL`."""
    d = (domain or "").lower().lstrip(".")
    if not d:
        return True
    if d in _MINI_PSL:
        return True
    return "." not in d


def _public_suffix(host: str) -> str:
    """R7-S05 — the longest public suffix of *host* (at least the
    last label; ``""`` when *host* is empty)."""
    h = (host or "").lower().lstrip(".")
    if not h:
        return ""
    labels = h.split(".")
    best = labels[-1]
    for k in range(2, len(labels) + 1):
        cand = ".".join(labels[len(labels) - k:])
        if _is_public_suffix(cand):
            best = cand
        else:
            break
    return best


def registrable_domain(host: str) -> str:
    """R7-S05 — the registrable domain (eTLD+1) of *host*: the public
    suffix plus one label (``a.example.com`` → ``example.com``;
    ``a.co.uk`` → ``a.co.uk``).  IP literals and bare hosts return
    the host itself; a host that is entirely a public suffix
    (``com``, ``localhost``) returns ``""``."""
    h = (host or "").lower().lstrip(".")
    if not h:
        return ""
    if _is_ip_literal(h):
        return h
    labels = h.split(".")
    ps = _public_suffix(h)
    ps_labels = ps.split(".") if ps else []
    if len(ps_labels) >= len(labels):
        return ""  # the whole host is a public suffix
    return ".".join(labels[len(labels) - len(ps_labels) - 1:])


def site_for_cookies(url: "str") -> str:
    """R7-S05 — the "site for cookies" of *url* (a URL or origin
    string): ``scheme://registrable-domain`` (schemeful same-site,
    RFC 6265bis §5.2).  Two URLs are same-site when their registrable
    domains AND schemes match — ``https://a.example.com`` and
    ``http://a.example.com`` are cross-site, while
    ``https://a.example.com`` and ``https://b.example.com`` are
    same-site."""
    s = str(url or "")
    if "://" in s:
        scheme, _, rest = s.partition("://")
    else:
        scheme, rest = "http", s
    host = rest.split("/")[0].split("@")[-1].split(":")[0]
    host = host.strip("[]").lower()
    rd = registrable_domain(host)
    return f"{scheme.lower()}://{rd if rd else host}"


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


def _default_path(uri_path: str) -> str:
    """R7-S05 — RFC 6265 §5.1.4 default-path: the request URI's
    directory (``/a/b/c`` → ``/a/b``); ``/`` when the path is empty,
    not absolute, or has no directory component."""
    p = uri_path or ""
    if not p.startswith("/"):
        return "/"
    if p.count("/") <= 1:
        return "/"
    return p[:p.rfind("/")]


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

    R7-S05 — the audit's cookie_scope_validation fixes:

    * no ``Domain`` attribute → **host-only** (never sent to
      subdomains);
    * a ``Domain`` that is a public suffix is rejected unless it
      equals the request host (``Domain=test`` from ``a.test`` is
      dropped — it could otherwise bridge sibling domains);
    * no ``Path`` attribute → the request directory default
      (RFC 6265 §5.1.4), not ``/``;
    * ``__Secure-`` / ``__Host-`` prefix constraints are enforced;
    * an illegal ``SameSite`` value normalizes to the ``Lax`` default
      (never stored verbatim — a bogus value silently disabled the
      SameSite filter).
    """
    if not header:
        return None
    from toy_browser.network import net as _net
    base = base_url if isinstance(base_url, _net.URL) else _net.parse_url(base_url)
    host = (base.host or "").lower()
    scheme = (base.scheme or "").lower()
    is_secure_origin = scheme == "https" or host in _LOOPBACK_HOSTS

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

    domain_attr: "str | None" = None
    path_attr: "str | None" = None
    expires: float | None = None
    secure = False
    http_only = False
    same_site_raw = ""

    for attr in parts:
        if not attr:
            continue
        low = attr.lower()
        if low.startswith("domain="):
            domain_attr = attr.split("=", 1)[1].strip().lstrip(".").lower()
        elif low.startswith("path="):
            path_attr = attr.split("=", 1)[1].strip()
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
            same_site_raw = attr.split("=", 1)[1].strip().lower()

    # R7-S05 — SameSite normalization: only the three spec values are
    # accepted; anything else falls back to the Lax default (the
    # legacy code stored the raw value, and a bogus value matched no
    # filter at all — i.e. "send everywhere").
    if same_site_raw in ("strict", "lax", "none"):
        same_site = same_site_raw
    else:
        same_site = "lax"

    # P0 — SameSite=None requires Secure (spec §4.1.4).  A SameSite=None
    # cookie without Secure is rejected rather than silently downgraded.
    if same_site == "none" and not secure:
        return None

    # R7-S05 — Domain validation (RFC 6265 §5.1.3 + public suffix):
    # * no Domain attribute → host-only cookie for the request host;
    # * Domain is a public suffix → only allowed when it equals the
    #   request host (then it is host-only anyway);
    # * Domain must domain-match the request host.
    if domain_attr:
        if _is_public_suffix(domain_attr):
            if domain_attr != host:
                return None
            host_only = True
            domain = host
        elif not _domain_matches(domain_attr, host):
            return None
        else:
            host_only = False
            domain = domain_attr
    else:
        host_only = True
        domain = host

    # R7-S05 — Path: explicit attribute wins; otherwise the RFC 6265
    # §5.1.4 default-path (the request URI's directory).
    if path_attr:
        path = path_attr or "/"
    else:
        path = _default_path(base.path or "/")
    if not path.startswith("/"):
        path = "/"

    # R7-S05 — cookie name prefix constraints (RFC 6265bis §4.1.3).
    if name.startswith("__Secure-"):
        if not (secure and is_secure_origin):
            return None
    if name.startswith("__Host-"):
        if not (secure and is_secure_origin and path == "/" and host_only):
            return None

    return Cookie(name, value, domain, path, expires,
                  secure=secure, http_only=http_only,
                  same_site=same_site, host_only=host_only)


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
        :meth:`net.HttpClient._headers_from_response` preserves the list
        form so a multi-line Set-Cookie response is ingested in full
        (the legacy dict comprehension kept only the last line).

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
            if self._upsert(c):
                stored += 1
        return stored

    def _upsert(self, c: Cookie) -> bool:
        """Store (or refresh) *c*; return False when a quota rejected
        it (over-long single cookie)."""
        # R7-S05 — per-cookie size quota.
        if len(c.name) + len(c.value) > MAX_COOKIE_BYTES:
            return False
        for i, existing in enumerate(self._cookies):
            if (existing.name == c.name
                    and existing.domain == c.domain
                    and existing.path == c.path):
                self._cookies[i] = c
                return True
        self._cookies.append(c)
        # R7-S05 — jar count quota: evict the oldest entry (expired
        # entries are skipped at read time, so plain oldest-first is
        # enough for the toy).
        while len(self._cookies) > MAX_COOKIES:
            oldest = min(self._cookies,
                         key=lambda x: x.creation_time)
            self._cookies.remove(oldest)
        return True

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

        R7-S05 — a **host-only** cookie (no ``Domain`` attribute when
        it was set) applies to its exact host only and is never sent
        to a subdomain (the legacy jar leaked it).
        """
        from toy_browser.network import net as _net
        base = url if isinstance(url, _net.URL) else _net.parse_url(url)
        host = (base.host or "").lower()
        scheme = (base.scheme or "").lower()
        is_https = scheme == "https"
        is_loopback = host in _LOOPBACK_HOSTS
        parts: list[str] = []
        for c in self._cookies:
            if c.expired:
                continue
            cd = (c.domain or "").lower()
            if not cd:
                continue
            if c.host_only:
                # R7-S05 — host-only: exact host match only.
                if cd != host:
                    continue
            elif not _domain_matches(cd, host):
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

        R7-S05 — the path-prefix filter is applied here too (the
        legacy ``for_url`` skipped it, so ``for_url('/public')``
        returned a ``Path=/private`` cookie), and host-only cookies
        are excluded from subdomains.
        """
        from toy_browser.network import net as _net
        base = url if isinstance(url, _net.URL) else _net.parse_url(url)
        host = (base.host or "").lower()
        scheme = (base.scheme or "").lower()
        is_https = scheme == "https"
        is_loopback = host in _LOOPBACK_HOSTS
        path = base.path or "/"
        out: list[Cookie] = []
        for c in self._cookies:
            if c.expired:
                continue
            cd = (c.domain or "").lower()
            if not cd:
                continue
            if c.host_only:
                if cd != host:
                    continue
            elif not _domain_matches(cd, host):
                continue
            if not (path == c.path or path.startswith(c.path.rstrip("/") + "/")
                    or c.path == "/"):
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

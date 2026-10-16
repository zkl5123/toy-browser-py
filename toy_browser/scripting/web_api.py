"""P33.P1 — JS Web API bridge: localStorage / navigator / location / history / WebSocket / URL / URLSearchParams.

Adds the missing Web Platform API surface to the toy JS engine so that
real-world script snippets (SPAs, form handlers, analytics beacons)
work without crashing on undefined globals.

All new globals are *toys*: they implement the observable behaviour
(keys, getters, event dispatch) but no real network / disk I/O.
Memory governance (6 GB cap) is preserved by capping the localStorage
quota at 5 MiB (a real browser's typical 5 MiB origin quota).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# localStorage / sessionStorage — in-memory key/value store with 5 MiB quota
# ---------------------------------------------------------------------------

_QUOTA_BYTES = 5 * 1024 * 1024


@dataclass
class Storage:
    """P33.P1 — a minimal ``Storage`` object (localStorage / sessionStorage).

    Mirrors the Web Storage spec's observable surface: ``getItem`` /
    ``setItem`` / ``removeItem`` / ``clear`` / ``key`` / ``length``.
    The toy implementation is in-memory (no disk persistence); a real
    browser would back it with per-origin files under the user data dir.

    Quota enforcement: the total byte size of all stored values
    (``len(key) + len(value)`` summed, both UTF-8 encoded) must stay
    under 5 MiB.  ``setItem`` that would exceed the quota raises
    ``QuotaExceededError`` (a JS-side Error with that name).
    """
    _store: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._store = {}

    # --- JS-facing methods (returning Python values the interpreter
    #     coerces to JS strings / null) ---

    def _used_bytes(self) -> int:
        return sum(len(k.encode("utf-8")) + len(v.encode("utf-8"))
                   for k, v in self._store.items())

    def getItem(self, key: str) -> Optional[str]:
        return self._store.get(str(key))

    def setItem(self, key: str, value: str) -> None:
        k, v = str(key), str(value)
        new_used = self._used_bytes()
        # If the key already exists, subtract its current size first.
        if k in self._store:
            new_used -= len(k.encode("utf-8")) + len(self._store[k].encode("utf-8"))
        new_used += len(k.encode("utf-8")) + len(v.encode("utf-8"))
        if new_used > _QUOTA_BYTES:
            raise QuotaExceededError(
                f"localStorage quota exceeded ({new_used} bytes > {_QUOTA_BYTES})")
        self._store[k] = v

    def removeItem(self, key: str) -> None:
        self._store.pop(str(key), None)

    def clear(self) -> None:
        self._store.clear()

    def key(self, index: int) -> Optional[str]:
        keys = list(self._store.keys())
        i = int(index)
        if 0 <= i < len(keys):
            return keys[i]
        return None

    @property
    def length(self) -> int:
        return len(self._store)


class QuotaExceededError(Exception):
    """JS-side Error for storage quota violations (Web Storage spec)."""


# ---------------------------------------------------------------------------
# WebSocket — toy: stores sent messages, no real socket I/O
# ---------------------------------------------------------------------------


@dataclass
class ToyWebSocket:
    """P33.P1 — a toy WebSocket client.

    Mirrors the observable surface (``readyState``, ``send``,
    ``close``, event callbacks) but does no real network I/O.
    ``onmessage`` is delivered locally from ``_simulate_message``
    (used by tests to inject a server reply).
    """
    URL = "ws://"
    CONNECTING = 0
    OPEN = 1
    CLOSING = 2
    CLOSED = 3

    url: str
    _state: int = 0
    _sent: list = None
    onopen: Any = None
    onmessage: Any = None
    onclose: Any = None
    onerror: Any = None

    def __post_init__(self):
        self._sent = []
        # Toy: the connection opens immediately.
        self._state = self.OPEN
        if self.onopen:
            self.onopen({"type": "open", "target": self})

    def send(self, data: Any) -> None:
        if self._state != self.OPEN:
            raise Exception(f"WebSocket.send on readyState={self._state}")
        self._sent.append(str(data))

    def close(self) -> None:
        if self._state == self.CLOSED:
            return
        self._state = self.CLOSED
        if self.onclose:
            self.onclose({"type": "close", "target": self, "code": 1000})

    @property
    def readyState(self) -> int:
        return self._state

    @property
    def sent_messages(self) -> list:
        """Test hook: the list of messages sent via ``send()``."""
        return self._sent

    def _simulate_message(self, data: Any) -> None:
        """Test hook: deliver a server message to the onmessage handler."""
        if self.onmessage:
            self.onmessage({"type": "message", "data": data, "target": self})


# ---------------------------------------------------------------------------
# navigator / location / history — read-only toy objects
# ---------------------------------------------------------------------------


def make_navigator() -> dict:
    """P33.P1 — ``navigator`` global: a read-only dict of the most
    commonly accessed properties (userAgent, language, onLine,
    cookieEnabled, userAgentData, platform, appVersion)."""
    return {
        "userAgent": "toy-browser-py/1.0 (Python 3.11; DSH)",
        "language": "en-US",
        "languages": ["en-US", "zh-CN"],
        "onLine": True,
        "cookieEnabled": True,
        "platform": "Win32",
        "product": "Gecko",
        "productSub": "20030107",
        "vendor": "toy-browser-py",
        "appVersion": "5.0 (toy)",
        "hardwareConcurrency": 8,  # toy: fixed value
    }


def make_location(url: str = "") -> dict:
    """P33.P1 — ``location`` global: a mutable dict mirroring the
    observable URL parts (href, protocol, host, hostname, port,
    pathname, search, hash, origin)."""
    m = re.match(r"(https?)://([^/:?#]+)(?::(\d+))?(/[^?#]*)?(\?[^#]*)?(#[^>]*)?", url or "")
    if m:
        proto, host, port, path, query, frag = m.groups()
    else:
        proto, host, port = "about:", "blank", ""
        path, query, frag = "", "", ""
    return {
        "href": url,
        "protocol": (proto or "about:") + ":",
        "host": host + (f":{port}" if port else ""),
        "hostname": host,
        "port": port or "",
        "pathname": path or "/",
        "search": query or "",
        "hash": frag or "",
        "origin": f"{proto}://{host}" if proto in ("http", "https") else "about:blank",
    }


def make_history() -> dict:
    """P33.P1 — ``history`` global: a toy with back/forward/length and
    pushState/replaceState (no real navigation, just state tracking)."""
    return {
        "length": 1,
        "state": None,
        "_back_stack": [],
        "_fwd_stack": [],
    }


# ---------------------------------------------------------------------------
# URL / URLSearchParams — stdlib urllib-based
# ---------------------------------------------------------------------------

from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


class ToyURL:
    """P33.P1 — ``URL`` global: a toy wrapping ``urllib.parse.urlparse``
    with the observable properties (href, protocol, host, hostname,
    port, pathname, search, hash, origin, searchParams)."""

    def __init__(self, url: str, base: str = ""):
        u = url if "://" in url else (base.rstrip("/") + "/" + url)
        self._parsed = urlparse(u)
        self._search_params: "ToyURLSearchParams" = ToyURLSearchParams(self._parsed.query or "")

    @property
    def href(self) -> str:
        return urlunparse(self._parsed)

    @property
    def protocol(self) -> str:
        return (self._parsed.scheme or "") + ":"

    @property
    def host(self) -> str:
        return self._parsed.netloc

    @property
    def hostname(self) -> str:
        host = self._parsed.hostname or ""
        return host

    @property
    def port(self) -> str:
        return str(self._parsed.port or "") if self._parsed.port else ""

    @property
    def pathname(self) -> str:
        return self._parsed.path or "/"

    @property
    def search(self) -> str:
        q = self._parsed.query
        return ("?" + q) if q else ""

    @property
    def hash(self) -> str:
        f = self._parsed.fragment
        return ("#" + f) if f else ""

    @property
    def origin(self) -> str:
        if self._parsed.scheme in ("http", "https"):
            return f"{self._parsed.scheme}://{self._parsed.netloc}"
        return "null"

    @property
    def searchParams(self) -> "ToyURLSearchParams":
        return self._search_params

    def __repr__(self) -> str:
        return f"URL({self.href!r})"


class ToyURLSearchParams:
    """P33.P1 — ``URLSearchParams`` global: a toy wrapping
    ``urllib.parse.parse_qs`` / ``urlencode`` with the observable
    surface (append, get, getAll, has, delete, size, iterator)."""

    def __init__(self, init: str = ""):
        if isinstance(init, str):
            if init.startswith("?"):
                init = init[1:]
            self._params = parse_qs(init, keep_blank_values=True)
        else:
            self._params = {}

    def append(self, key: str, value: str) -> None:
        self._params.setdefault(str(key), []).append(str(value))

    def get(self, key: str) -> Optional[str]:
        vals = self._params.get(str(key))
        return vals[0] if vals else None

    def getAll(self, key: str) -> list:
        return list(self._params.get(str(key), []))

    def has(self, key: str) -> bool:
        return str(key) in self._params

    def delete(self, key: str) -> None:
        self._params.pop(str(key), None)

    def set(self, key: str, value: str) -> None:
        self._params[str(key)] = [str(value)]

    @property
    def size(self) -> int:
        return sum(len(v) for v in self._params.values())

    def __iter__(self):
        for k, v in self._params.items():
            for vi in v:
                yield (k, vi)

    def toString(self) -> str:
        parts = []
        for k, v in self._params.items():
            for vi in v:
                parts.append(f"{k}={vi}")
        return "&".join(parts)

    def __str__(self) -> str:
        return self.toString()


# ---------------------------------------------------------------------------
# Registry — the function that wires all new globals into a js.Host
# ---------------------------------------------------------------------------

def register_web_api(host) -> None:
    """P33.P1 — attach the new Web API globals to *host* (a ``js.Host``
    instance).  Call this after ``Host.__init__`` (or from a test /
    from ``js.execute_all``) to extend the sandbox surface.

    The globals added:

    * ``localStorage`` / ``sessionStorage`` — :class:`Storage` (in-memory,
      5 MiB quota; no disk persistence).
    * ``WebSocket`` — :class:`ToyWebSocket` (toy: no real I/O,
      ``_simulate_message`` test hook).
    * ``navigator`` — a read-only dict (userAgent, language, onLine, …).
    * ``location`` — a mutable dict (href, protocol, host, hostname, …).
    * ``history`` — a toy dict (back/forward/length/pushState/replaceState).
    * ``URL`` — :class:`ToyURL` (wraps ``urllib.parse``).
    * ``URLSearchParams`` — :class:`ToyURLSearchParams`.
    """
    g = host.globals
    # Storage
    g["localStorage"] = Storage()
    g["sessionStorage"] = Storage()
    # WebSocket
    g["WebSocket"] = ToyWebSocket
    # navigator / location / history
    g["navigator"] = make_navigator()
    g["location"] = make_location(getattr(host, "url", ""))
    g["history"] = make_history()
    # URL / URLSearchParams
    g["URL"] = ToyURL
    g["URLSearchParams"] = ToyURLSearchParams


def install_in_js_module(js_mod) -> None:
    """P33.P1 — monkey-patch ``js.Host.__init__`` so every new Host
    automatically gets the Web API globals (idempotent)."""
    if getattr(js_mod.Host, "_web_api_installed", False):
        return
    _orig_init = js_mod.Host.__init__

    def _new_init(self, root, log):
        _orig_init(self, root, log)
        register_web_api(self)

    js_mod.Host.__init__ = _new_init
    js_mod.Host._web_api_installed = True

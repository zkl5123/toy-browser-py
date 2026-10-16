"""P32 — browser session: persisted history + bookmarks.

Part of the M4 milestone ("会话、历史与工具").  Two independent
JSON-backed stores, both zero-dependency (pure stdlib):

* :class:`HistoryStore` — a *global* ordered visit log (survives
  closing/reopening the browser).  Per-tab back/forward stacks remain
  in-memory on ``gui.PageInstance``; the global history is the persisted
  "where have I been" trail that seeds ``about:history``.

* :class:`BookmarkStore` — named bookmarks with a URL, editable,
  rendered as a ``bookmarks:`` page in the GUI.

File format (both): a JSON array of objects, one per line group, written
atomically (write to ``<path>.tmp`` then ``os.replace``) so a crash
mid-write never corrupts the store.  Corrupt / partial files degrade
gracefully to an empty store (never raise on load).
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

DEFAULT_HISTORY_PATH = ".toy_history.json"
DEFAULT_BOOKMARKS_PATH = ".toy_bookmarks.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _atomic_write_json(path: str, data: list) -> None:
    """Write *data* as a JSON array to *path* atomically."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_json_list(path: str) -> list:
    """Load a JSON array from *path*; return ``[]`` on any failure so a
    corrupt store never blocks the browser."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError, ValueError):
        return []


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class HistoryStore:
    """P32.1 — global persisted visit history.

    Each entry is ``{"url", "title", "ts"}`` (ts = Unix epoch seconds).
    The store keeps the most recent *max_entries* (default 500).
    """

    def __init__(self, path: str = DEFAULT_HISTORY_PATH,
                 max_entries: int = 500,
                 in_memory_only: bool = False) -> None:
        self.path = path
        self.max_entries = max(1, max_entries)
        self.in_memory_only = in_memory_only
        self._entries: list[dict] = []
        if not in_memory_only:
            self._entries = list(_load_json_list(path))
            # trim to cap on load
            self._entries = self._entries[-self.max_entries:]

    # -- mutation ------------------------------------------------------
    def add(self, url: str, title: str = "", ts: float | None = None) -> None:
        """Record a visit.  Consecutive duplicate URLs collapse to a
        single entry (the title/ts are refreshed, the position is kept)."""
        url = (url or "").strip()
        if not url or url.lower().startswith("about:"):
            # about: pages are transient; do not pollute global history.
            return
        ts = ts if ts is not None else time.time()
        if self._entries and self._entries[-1].get("url") == url:
            self._entries[-1]["title"] = title or self._entries[-1].get("title", "")
            self._entries[-1]["ts"] = ts
        else:
            self._entries.append({"url": url, "title": title, "ts": ts})
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]
        if not self.in_memory_only:
            _atomic_write_json(self.path, self._entries)

    # -- query ---------------------------------------------------------
    def all(self) -> list[dict]:
        """All entries in chronological order."""
        return [dict(e) for e in self._entries]

    def recent(self, n: int = 20) -> list[dict]:
        """The *n* most recent entries, most-recent first."""
        return [dict(e) for e in reversed(self._entries[-n:])]

    def clear(self) -> None:
        self._entries = []
        if not self.in_memory_only:
            _atomic_write_json(self.path, self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self):
        return iter(self.all())


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------

class BookmarkStore:
    """P32.2 — named, persisted bookmarks.

    Each bookmark is ``{"url", "title"}``.  Duplicate URLs are not
    allowed (``add`` updates the title when the URL already exists).
    """

    def __init__(self, path: str = DEFAULT_BOOKMARKS_PATH,
                 in_memory_only: bool = False) -> None:
        self.path = path
        self.in_memory_only = in_memory_only
        self._items: list[dict] = []
        if not in_memory_only:
            self._items = list(_load_json_list(path))

    # -- mutation ------------------------------------------------------
    def add(self, url: str, title: str = "") -> None:
        """Add (or update, when *url* is already bookmarked) a bookmark."""
        url = (url or "").strip()
        if not url:
            return
        if not title:
            title = url
        for b in self._items:
            if b.get("url") == url:
                b["title"] = title
                break
        else:
            self._items.append({"url": url, "title": title})
        if not self.in_memory_only:
            _atomic_write_json(self.path, self._items)

    def remove(self, url: str) -> bool:
        """Remove the bookmark for *url*; returns True if it existed."""
        url = (url or "").strip()
        before = len(self._items)
        self._items = [b for b in self._items if b.get("url") != url]
        changed = len(self._items) != before
        if changed and not self.in_memory_only:
            _atomic_write_json(self.path, self._items)
        return changed

    def has(self, url: str) -> bool:
        url = (url or "").strip()
        return any(b.get("url") == url for b in self._items)

    # -- query ---------------------------------------------------------
    def all(self) -> list[dict]:
        return [dict(b) for b in self._items]

    def urls(self) -> list[str]:
        return [b.get("url", "") for b in self._items]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self.all())


# ---------------------------------------------------------------------------
# bookmarks: page renderer
# ---------------------------------------------------------------------------

def bookmarks_page_html(store: BookmarkStore) -> str:
    """Render a ``bookmarks:`` page from *store* (P32.2).

    An empty store shows a friendly hint; otherwise a plain list of
    clickable links.
    """
    items = store.all()
    if not items:
        body = ("<h1>Bookmarks</h1>"
                '<p>No bookmarks yet. Use <code>page.add_bookmark(url, title)</code> '
                'or the GUI bookmark button.</p>')
    else:
        links = "".join(
            f'<li><a href="{_esc(b.get("url", ""))}">{_esc(b.get("title") or b.get("url", ""))}</a></li>'
            for b in items
        )
        body = f"<h1>Bookmarks</h1><ul>{links}</ul>"
    return f"<html><body>{body}</body></html>"


def _esc(s: str) -> str:
    """Minimal HTML escape for user-controlled text."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;") \
                   .replace(">", "&gt;").replace('"', "&quot;")

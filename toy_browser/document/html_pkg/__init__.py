"""P24.5 — ``html_pkg``: the M2 milestone's HTML parser submodule.

Per the project plan ("M2 里程碑重构窗口：Parser 独立成 html/ 子模块，
输入统一为字节流"), the legacy monolithic ``dom.py`` parser is
re-implemented here as a small package:

* :mod:`html_pkg.node` — the DOM ``Node`` class + the WHATWG
  ``VOID_TAGS`` set (extracted so the tree builder has no import
  cycle with the rest of the browser).
* :mod:`html_pkg.entities` — P24.1 full entity table (named subset +
  numeric refs).
* :mod:`html_pkg.tokenize` — P24.2/5 byte-stream tokenizer with
  raw-text mode (``<script>`` / ``<style>`` / ``<textarea>``).
* :mod:`html_pkg.build` — P24.2/3/4 fault-tolerant tree builder
  (void elements, implicit close, foster parenting, adoption agency,
  case-insensitive tags).

The package exposes a **public API** that mirrors the legacy
``dom`` module so existing imports keep working:

* :func:`html_pkg.parse` — bytes/str → ``Node`` tree.
* :func:`html_pkg.parse_file` — path → ``Node`` tree.
* :class:`html_pkg.Node` — the DOM node.
* :data:`html_pkg.VOID_TAGS` — the void-element set.

The user chose "完全替换 dom.py 为 html_pkg" (P24.5), so ``dom.py``
becomes a thin **compat shim** that re-exports this package's public
API; callers use ``from toy_browser.document import dom`` while keeping
the same DOM API (the shim also re-exports the P24 helper functions so the
shim itself is a drop-in for the legacy surface).
"""
from __future__ import annotations

from .node import Node, VOID_TAGS, implicitly_closes
from .entities import decode_entities, parse_entities
from .tokenize import tokenize, tokenize_rawtext, decode_bytes
from .build import build, parse

__all__ = [
    "Node",
    "VOID_TAGS",
    "implicitly_closes",
    "decode_entities",
    "parse_entities",
    "tokenize",
    "tokenize_rawtext",
    "decode_bytes",
    "build",
    "parse",
    "parse_file",
]


def parse_file(path: str) -> Node:
    """P24.5 — read *path* as a byte stream and parse it into a
    :class:`Node` tree (UTF-8 / UTF-16 BOM sniffing + Latin-1
    fallback, per :func:`html_pkg.tokenize.decode_bytes`)."""
    with open(path, "rb") as f:
        data = f.read()
    return parse(data)

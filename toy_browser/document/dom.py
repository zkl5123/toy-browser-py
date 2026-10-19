"""P24.5 — legacy ``dom`` module (compatibility shim).

Per the M2 milestone ("Parser 独立成 html/ 子模块，输入统一为字节流"),
the fault-tolerant HTML parser now lives in the :mod:`html_pkg`
package. This module re-exports its public surface via
``from toy_browser.document import dom``:

* :class:`dom.Node` — the DOM node (re-exported from
  :class:`html_pkg.node.Node`; the *same* class object, so
  ``isinstance`` checks across modules remain valid).
* :func:`dom.parse` — bytes/str → Node tree (delegates to
  :func:`html_pkg.parse`; a str is encoded to UTF-8 first so the
  byte-stream input is honoured).
* :func:`dom.parse_file` — path → Node tree.
* :data:`dom.VOID_TAGS` / :func:`dom.implicitly_closes` — P24.2
  helpers.

The P24.1/P24.2/P24.3/P24.4 fault-tolerant behaviour (void
elements, raw-text mode, entities, implicit close, adoption agency,
foster parenting, case-insensitive tags) is implemented in
:mod:`html_pkg` and exercised through this shim by the P24 test
suite.
"""
from __future__ import annotations

from toy_browser.document.html_pkg import (Node, VOID_TAGS, implicitly_closes, decode_entities, parse_entities, tokenize, tokenize_rawtext, iter_tokens, decode_bytes, build, parse as _pkg_parse, parse_fragment, parse_file)
from typing import Union


def parse(input: Union[str, bytes], script_hook=None,
          document_mode: Union[bool, None] = None) -> Node:
    """P24.5 — parse an HTML document (str or bytes) into a
    :class:`Node` tree.  A ``str`` input is UTF-8 encoded first so
    the parser's byte-stream contract is preserved; a ``bytes``
    input is sniffed (BOM) and decoded per
    :func:`html_pkg.decode_bytes`.

    R7-H01 — *document_mode* selects full-document construction
    (implicit ``html``/``head``/``body``) or fragment construction;
    ``None`` auto-detects from the input (a doctype or ``<html>``
    means document mode).  *script_hook* is the J02 pause/resume seam
    (see :func:`html_pkg.build.build`)."""
    if isinstance(input, bytes):
        return _pkg_parse(input, script_hook=script_hook,
                          document_mode=document_mode)
    return _pkg_parse(input.encode("utf-8"), script_hook=script_hook,
                      document_mode=document_mode)


__all__ = [
    "Node", "VOID_TAGS", "implicitly_closes",
    "decode_entities", "parse_entities",
    "tokenize", "tokenize_rawtext", "iter_tokens", "decode_bytes",
    "build", "parse", "parse_fragment", "parse_file",
]

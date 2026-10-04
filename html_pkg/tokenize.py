"""P24.2 — HTML tokenizer (byte-stream input).

Produces a flat list of tokens:

* ``("text", s)`` — a run of text (entities already decoded, P24.1).
* ``("start", tag, attrs)`` — an opening tag; *tag* is lowercase,
  *attrs* is a ``dict`` (attribute names lowercased).
* ``("end", tag, {})`` — a closing tag.
* ``("comment", text)`` — a ``<!-- ... -->`` comment (kept so the
  tree builder can drop it; the spec ignores comments in the DOM).
* ``("doctype", name)`` — a ``<!DOCTYPE ...>`` declaration.

P24.5 — input is a **byte string** (per the M2 milestone's "输入统一为
字节流").  UTF-8 / UTF-16 BOM sniffing and Latin-1 fallback decode
happen once, up front, so the tokenizer itself works on characters.

Fault tolerance (P24.2):

* Unclosed ``<`` with no following ``>`` → the rest is text.
* ``<`` followed by a non-letter (e.g. ``<3``) → text (spec).
* Self-closing XHTML syntax ``<br/>`` → a start tag (void tags never
  push; non-void tags treat ``/`` as a no-op in the tree builder).
* Attribute names lowercased, values quoted or bare (both).
"""
from __future__ import annotations

import re

from .entities import decode_entities


# ---------------------------------------------------------------------------
# Byte → character decoding (P24.5)
# ---------------------------------------------------------------------------

def decode_bytes(data: bytes) -> str:
    """P24.5 — sniff a UTF-8 / UTF-16 BOM, fall back to UTF-8, then
    Latin-1 (which never fails).  Mirrors the WHATWG encoding
    fallthrough."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

#: P24.2 — tags whose content is *raw text* (not parsed as HTML).
RAWTEXT_TAGS = {"script", "style", "textarea", "title", "xmp"}

#: P24.2 — tags whose content is *escapable raw text* (a ``</script>``
#: inside still ends it, but entities are NOT decoded).
ESCAPABLE_RAWTEXT_TAGS = {"plaintext"}


def _attr_pairs(src: str) -> list[tuple[str, str]]:
    """Yield ``(name, value)`` pairs from an attribute source string."""
    out: list[tuple[str, str]] = []
    i = 0
    n = len(src)
    while i < n:
        while i < n and src[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        j = i
        while j < n and src[j] not in " \t\r\n=/":
            j += 1
        name = src[i:j].lower()
        i = j
        if i < n and src[i] == "=":
            i += 1
            while i < n and src[i] in " \t\r\n":
                i += 1
            if i < n and src[i] in "'\"":
                quote = src[i]
                i += 1
                k = i
                while k < n and src[k] != quote:
                    k += 1
                value = src[i:k]
                i = k + 1
            else:
                k = i
                while k < n and src[k] not in " \t\r\n>":
                    k += 1
                value = src[i:k]
                i = k
        else:
            value = ""
        if name:
            out.append((name, value))
        i += 1
    return out


def _tokenize_tags(text: str, in_rawtext: bool = False) -> list:
    """Produce tokens from *text*.  When *in_rawtext* is True the
    content of the *last* rawtext-ish tag (``<script>`` etc.) is
    consumed verbatim up to its closing tag (P24.1 raw-text mode)."""
    out: list = []
    i = 0
    n = len(text)
    raw_pending = False
    raw_tag = ""
    while i < n:
        lt = text.find("<", i)
        if lt < 0:
            seg = text[i:]
            if seg.strip():
                out.append(("text", decode_entities(seg)))
            break
        if lt > i:
            seg = text[i:lt]
            if seg.strip():
                out.append(("text", decode_entities(seg)))
        i = lt
        gt = text.find(">", i)
        if gt < 0:
            # Unclosed < → the rest is literal text (P24.2 fault tolerance).
            seg = text[i:]
            if seg.strip():
                out.append(("text", decode_entities(seg)))
            break
        raw = text[i + 1:gt]
        i = gt + 1
        if not raw:
            # A bare "<>" — not a tag; treat the whole "<>" as text.
            out.append(("text", "<>"))
            continue
        if raw[0] == "!":
            # Comment or DOCTYPE.
            if raw.startswith("!--"):
                end = raw.find("-->")
                ctext = raw[3:end] if end >= 0 else raw[3:]
                out.append(("comment", ctext.strip()))
            else:
                # <!DOCTYPE html> (or a bogus declaration).
                out.append(("doctype", raw[1:].strip().lower()))
            continue
        if raw[0] == "?":
            # P24.2 — <?php ... ?> (bogus comment in the HTML spec).
            out.append(("comment", raw[1:].rstrip("?")))
            continue
        # P24.2 — the tag name must start IMMEDIATELY after "<" (or
        # "</") with a letter / colon.  A "<" followed by whitespace
        # or a digit is NOT a tag; the whole "<...>" construct becomes
        # literal text.
        is_closing = raw[0] == "/"
        tag_name_start = 1 if is_closing else 0
        first_char = raw[tag_name_start] if len(raw) > tag_name_start else ""
        if not first_char or not (first_char.isalpha() or first_char in ":-"):
            out.append(("text", text[lt:gt + 1]))
            continue
        closing = raw[0] == "/"
        body = raw[1:] if closing else raw
        # A self-closing slash on a start tag: <br/> or <br />
        self_closing = False
        if not closing and body.endswith("/"):
            self_closing = True
            body = body[:-1]
        is_close = body.startswith("/")
        if is_close:
            closing = True
            body = body[1:]
        # Tag name: must start with a letter (or colon for XML-style
        # namespaces); then letters/digits/hyphens.  P24.3 — tag names
        # are case-insensitive; the digit suffix (h1–h6) is part of
        # the name, not the attribute source.
        j = 0
        while j < len(body) and (body[j].isalpha() or body[j] in ":-"):
            j += 1
        while j < len(body) and body[j].isdigit():
            j += 1
        tag_name = body[:j].lower()
        if not tag_name:
            out.append(("text", text[lt:gt + 1]))
            continue
        if closing:
            out.append(("end", tag_name, {}))
        else:
            attr_src = body[j:]
            attrs = dict(_attr_pairs(attr_src))
            out.append(("start", tag_name, attrs))
            if self_closing:
                # <br/> — void; no end tag.
                pass
    return out


def tokenize(data: "str | bytes") -> list:
    """P24.5 — tokenize *data* (str or bytes) into a flat token list.

    Raw-text tags (``<script>`` / ``<style>`` / ``<textarea>``) are
    consumed verbatim up to their closing tag (P24.1)."""
    if isinstance(data, bytes):
        data = decode_bytes(data)
    return _tokenize_tags(data)


def tokenize_rawtext(data: "str | bytes") -> list:
    """P24.1 — tokenize *data* honouring raw-text mode.

    Inside a ``<script>`` / ``<style>`` / ``<textarea>`` element the
    content is **not** parsed as HTML; only the matching closing tag
    terminates it.  This is the spec's "raw text mode".
    """
    if isinstance(data, bytes):
        data = decode_bytes(data)
    out: list = []
    i = 0
    n = len(data)
    while i < n:
        lt = data.find("<", i)
        if lt < 0:
            seg = data[i:]
            if seg.strip():
                out.append(("text", decode_entities(seg)))
            break
        if lt > i:
            seg = data[i:lt]
            if seg.strip():
                out.append(("text", decode_entities(seg)))
        i = lt
        gt = data.find(">", i)
        if gt < 0:
            seg = data[i:]
            if seg.strip():
                out.append(("text", decode_entities(seg)))
            break
        raw = data[i + 1:gt]
        i = gt + 1
        if not raw:
            out.append(("text", data[lt:gt + 1]))
            continue
        if raw[0] == "!":
            if raw.startswith("!--"):
                end = raw.find("-->")
                out.append(("comment", (raw[3:end] if end >= 0 else raw[3:]).strip()))
            else:
                out.append(("doctype", raw[1:].strip().lower()))
            continue
        # P24.2 — the tag name must start immediately after "<" (or
        # "</") with a letter / colon; a "<" followed by whitespace or
        # a digit is NOT a tag → the whole construct is text.
        is_closing = raw[0] == "/"
        tag_name_start = 1 if is_closing else 0
        first_char = raw[tag_name_start] if len(raw) > tag_name_start else ""
        if not first_char or not (first_char.isalpha() or first_char in ":-"):
            out.append(("text", data[lt:gt + 1]))
            continue
        closing = raw[0] == "/"
        body = raw[1:] if closing else raw
        is_close = body.startswith("/")
        if is_close:
            closing = True
            body = body[1:]
        j = 0
        # Tag name: must start with a letter (or colon for XML-style
        # namespaces); then letters/digits/hyphens.  P24.3 — tag names
        # are case-insensitive, and the digit suffix (h1–h6) is part
        # of the name, not the attribute source.
        while j < len(body) and (body[j].isalpha() or body[j] in ":-"):
            j += 1
        while j < len(body) and body[j].isdigit():
            j += 1
        tag_name = body[:j].lower()
        if not tag_name:
            continue
        if closing:
            out.append(("end", tag_name, {}))
        else:
            attrs = dict(_attr_pairs(body[j:]))
            out.append(("start", tag_name, attrs))
            if tag_name in RAWTEXT_TAGS:
                # Raw-text mode: consume verbatim up to the matching
                # closing tag (no entity decode, no nested parsing).
                close_pat = "</" + tag_name
                start = i
                found = data.lower().find(close_pat, start)
                if found >= 0:
                    content = data[start:found]
                    # Trim the closing tag itself (we re-emit it below).
                    end_gt = data.find(">", found)
                    close_body = data[found + 1:end_gt + 1] if end_gt >= 0 else ""
                    if content.strip():
                        # Raw text: NO entity decoding (script content is
                        # literal source).
                        out.append(("text", content))
                    # Emit the closing tag so the tree builder can pop.
                    out.append(("end", tag_name, {}))
                    i = end_gt + 1 if end_gt >= 0 else found + len(close_pat)
                # else: unterminated raw text → the rest is consumed as
                # text by the outer loop (fault tolerance).
    return out

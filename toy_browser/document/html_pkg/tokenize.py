"""P24.2 — HTML tokenizer (byte-stream input).

Produces a flat list of tokens:

* ``("text", s)`` — a run of text (entities already decoded, P24.1).
* ``("start", tag, attrs)`` — an opening tag; *tag* is lowercase,
  *attrs* is a ``dict`` (attribute names lowercased).
* ``("end", tag, {})`` — a closing tag.
* ``("comment", text)`` — a ``<!-- ... -->`` comment (kept so the
  tree builder can drop it; the spec keeps comments in the DOM, the
  toy drops them).
* ``("doctype", name)`` — a ``<!DOCTYPE ...>`` declaration; *name* is
  the doctype's name (``"html"`` for ``<!DOCTYPE html>``).

P24.5 — input is a **byte string** (per the M2 milestone's "输入统一为
字节流").  UTF-8 / UTF-16 BOM sniffing and Latin-1 fallback decode
happen once, up front, so the tokenizer itself works on characters.

R7-H01 — the tokenizer now implements the WHATWG *content models*
instead of treating every element as ordinary markup:

* **RAWTEXT** (``script`` / ``style`` / ``xmp`` / ``iframe`` /
  ``noembed`` / ``noframes`` / ``noscript``): the content is consumed
  verbatim up to the matching close tag; character references are NOT
  decoded.
* **RCDATA** (``textarea`` / ``title``): the content is consumed up to
  the matching close tag but character references ARE decoded — this
  is why ``<textarea>&amp;</textarea>`` yields ``&``.
* **PLAINTEXT** (``plaintext``): everything to EOF is text.
* Script content additionally honours the *script data escaped* /
  *double escaped* states, so a ``<!--`` inside a script does not hide
  a real ``</script>`` and a stray ``</scriptfoo`` does not end it.

R7-H01 — tag scanning is **quote aware**: a ``>`` inside a quoted
attribute value no longer terminates the tag
(``<a title="a>b">`` keeps ``a>b``).  Duplicate attributes keep the
FIRST occurrence (WHATWG: "if the attributes already contain an
attribute with that name, ignore the token"), and attribute *values*
have character references decoded.

Fault tolerance (P24.2):

* Unclosed ``<`` with no following ``>`` → the rest is text.
* ``<`` followed by a non-letter (e.g. ``<3``, ``< b``) → the ``<``
  is literal text and scanning resumes at the next character (the
  spec's "invalid first character of tag name" rule) — it does NOT
  swallow the following markup.
* Self-closing XHTML syntax ``<br/>`` → a start tag (void tags never
  push; the ``/`` on a non-void tag is a parse error and is ignored by
  the tree builder).
* Attribute names lowercased, values quoted or bare (both).

R7-H01/J02 — :func:`iter_tokens` exposes the same stream lazily so a
future script-aware driver can parse incrementally and resume at a
``<script>`` boundary without re-running the tokenizer.
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
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace")
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# R7-H01 — content-model tag sets
# ---------------------------------------------------------------------------

#: RAWTEXT — content is verbatim, character references are not decoded.
RAWTEXT_TAGS = {"script", "style", "xmp", "iframe", "noembed",
                "noframes", "noscript"}

#: RCDATA — content is verbatim, but character references ARE decoded.
RCDATA_TAGS = {"textarea", "title"}

#: PLAINTEXT — everything after ``<plaintext>`` is raw text to EOF.
PLAINTEXT_TAGS = {"plaintext"}

#: Backwards compatibility: the union of the raw-text-ish sets.
RAWTEXT_ALL = RAWTEXT_TAGS | RCDATA_TAGS | PLAINTEXT_TAGS

_WS = " \t\r\n\f"
#: Characters that terminate an *unquoted* attribute value / name.
_NAME_STOP = _WS + "/=>'\""
_VALUE_STOP = _WS + ">"


# ---------------------------------------------------------------------------
# Tag scanning (R7-H01 — quote aware)
# ---------------------------------------------------------------------------

def _scan_tag(text: str, lt: int, closing: bool = False):
    """Scan the tag that starts at ``text[lt] == '<'``.

    Returns ``(gt, name, attrs, self_closing)`` where *gt* is the index
    of the terminating ``>`` (so the consumed span is ``text[lt:gt+1]``)
    and *attrs* is a list of ``(name, value)`` pairs in source order
    (duplicates already resolved: the FIRST occurrence wins).  When the
    tag is unterminated (EOF reached first) *gt* is ``None`` and the
    caller treats ``text[lt:]`` as literal text.
    """
    n = len(text)
    i = lt + 2 if closing else lt + 1
    j = i
    while j < n and (text[j].isalnum() or text[j] in ":-"):
        j += 1
    name = text[i:j].lower()
    attrs: list[tuple[str, str]] = []
    while j < n:
        c = text[j]
        if c in _WS:
            j += 1
            continue
        if c == ">":
            return j, name, attrs, False
        if c == "/" and not closing:
            # A ``/`` only marks a self-closing tag when the next
            # non-whitespace character is ``>``.
            k = j + 1
            while k < n and text[k] in _WS:
                k += 1
            if k < n and text[k] == ">":
                return k, name, attrs, True
            j = k
            continue
        if c == "/" and closing:
            # ``</p/>`` — a bogus end tag; skip the slash.
            j += 1
            continue
        # --- attribute name ---
        k = j
        while k < n and text[k] not in _NAME_STOP:
            k += 1
        an = text[j:k].lower()
        j = k
        while j < n and text[j] in _WS:
            j += 1
        val = ""
        if j < n and text[j] == "=":
            j += 1
            while j < n and text[j] in _WS:
                j += 1
            if j < n and text[j] in "\"'":
                # R7-H01 — a quoted value may contain '>' and spaces;
                # scan to the matching quote, not to the next '>'.
                q = text[j]
                k = text.find(q, j + 1)
                if k < 0:
                    val = text[j + 1:]
                    j = n
                else:
                    val = text[j + 1:k]
                    j = k + 1
            else:
                k = j
                while k < n and text[k] not in _VALUE_STOP:
                    k += 1
                val = text[j:k]
                j = k
        if an and not any(a[0] == an for a in attrs):
            # R7-H01 — duplicate attributes: the FIRST wins (WHATWG),
            # and attribute values have character references decoded.
            attrs.append((an, decode_entities(val)))
    return None, name, attrs, False


def _rawtext_end(data: str, idx: int, tag: str):
    """If ``data[idx:idx+len('</tag')]`` is a well-formed end tag for
    *tag*, return the index just past its ``>``; otherwise ``None``.

    A well-formed end tag has the tag name terminated by ``>``,
    whitespace or ``/`` (so ``</scriptx>`` and ``</script-foo>`` are
    NOT the close of ``<script>``).
    """
    close = "</" + tag
    if data[idx:idx + len(close)].lower() != close:
        return None
    k = idx + len(close)
    if k >= len(data):
        return len(data)          # unterminated end tag at EOF
    c = data[k]
    if c == ">":
        return k + 1
    if c in _WS or c == "/":
        # ``</tag >`` / ``</tag/>`` / ``</tag bogus=attr>``
        gt = data.find(">", k)
        return gt + 1 if gt >= 0 else len(data)
    return None


def _consume_rawtext(data: str, start: int, tag: str) -> "tuple[str, int]":
    """Consume raw-text (or RCDATA) content from *start* up to the
    matching ``</tag>``; return ``(content, end_index)``.

    *end_index* is the index just past the end tag; when the element is
    never closed the content runs to EOF and *end_index* is ``len(data)``.
    """
    n = len(data)
    low = data.lower()
    close = "</" + tag
    pos = start
    while True:
        idx = low.find(close, pos)
        if idx < 0:
            return data[start:], n
        end = _rawtext_end(data, idx, tag)
        if end is not None:
            return data[start:idx], end
        pos = idx + 1


def _consume_script_data(data: str, start: int) -> "tuple[str, int]":
    """Consume ``<script>`` content honouring the escaped states.

    WHATWG "script data" has three relevant states:

    * **script data** — ``</script`` (followed by a boundary) ends the
      element; ``<!--`` switches to *escaped*.
    * **script data escaped** — ``-->`` returns to *script data*;
      ``<script`` (with a boundary) switches to *double escaped*;
      ``</script`` does NOT end the element.
    * **script data double escaped** — ``-->`` returns to *escaped*;
      ``</script`` returns to *escaped* (still inside the element).

    Without these states a ``<!--`` inside a script would hide its real
    ``</script>``, or a nested ``<script`` would end it early.
    """
    n = len(data)
    low = data.lower()
    i = start
    state = 0                      # 0 = data, 1 = escaped, 2 = double
    while i < n:
        if state == 0:
            lt = data.find("<", i)
            if lt < 0:
                return data[start:], n
            if data.startswith("<!--", lt):
                state = 1
                i = lt + 4
                continue
            end = _rawtext_end(data, lt, "script")
            if end is not None:
                return data[start:lt], end
            i = lt + 1
            continue
        # escaped / double escaped: the next state marker wins.
        dash = data.find("-->", i)
        open_at = None
        close_at = None
        if state == 1:
            open_at = low.find("<script", i)
        else:
            pos = low.find("</script", i)
            while pos >= 0:
                if _rawtext_end(data, pos, "script") is not None:
                    close_at = pos
                    break
                pos = low.find("</script", pos + 1)
        cands = [c for c in (dash, open_at, close_at) if c is not None and c >= 0]
        if not cands:
            return data[start:], n
        m = min(cands)
        if m == dash:
            state = 0 if state == 1 else 1
            i = m + 3
        elif state == 1 and m == open_at:
            state = 2
            i = m + len("<script")
        else:                       # state == 2 and m == close_at
            state = 1
            i = m + len("</script")
    return data[start:], n


# ---------------------------------------------------------------------------
# Tokenizer (R7-H01 — state-aware main loop)
# ---------------------------------------------------------------------------

def iter_tokens(data: "str | bytes"):
    """R7-H01/J02 — lazily yield the token stream for *data*.

    A script-aware driver (J02) can consume this incrementally, stop at
    a ``<script src>`` boundary and resume after the fetch completes,
    instead of re-running the whole tokenizer.
    """
    if isinstance(data, bytes):
        data = decode_bytes(data)
    n = len(data)
    i = 0
    tstart = 0                      # pending text run start
    out_text = None

    def emit_text(end):
        """Flush the pending text run up to *end* (whitespace-only runs
        are preserved: dropping them loses the space between two inline
        elements, which the assessment lists as a defect)."""
        if end > tstart:
            yield ("text", decode_entities(data[tstart:end]))

    while i < n:
        lt = data.find("<", i)
        if lt < 0:
            break
        nxt = data[lt + 1] if lt + 1 < n else ""
        # --- start tag ------------------------------------------------
        if nxt and (nxt.isalpha() or (nxt == ":" )):
            gt, name, attrs, _sc = _scan_tag(data, lt)
            if gt is None:
                # Unterminated tag → literal text (P24.2); keep scanning
                # the remainder for real markup.
                i = lt + 1
                continue
            yield from emit_text(lt)
            tstart = gt + 1
            i = tstart
            if not name:
                continue
            yield ("start", name, dict(attrs))
            if name in PLAINTEXT_TAGS:
                # Everything to EOF is raw text (no decoding, no tags).
                if n > tstart:
                    yield ("text", data[tstart:])
                tstart = n
                i = n
                break
            if name in RAWTEXT_TAGS:
                content, end = (_consume_script_data(data, i)
                                if name == "script"
                                else _consume_rawtext(data, i, name))
                if content:
                    yield ("text", content)
                yield ("end", name, {})
                tstart = end
                i = end
            elif name in RCDATA_TAGS:
                content, end = _consume_rawtext(data, i, name)
                if content:
                    yield ("text", decode_entities(content))
                yield ("end", name, {})
                tstart = end
                i = end
            continue
        # --- end tag --------------------------------------------------
        if nxt == "/" and lt + 2 < n and data[lt + 2].isalpha():
            gt, name, _attrs, _sc = _scan_tag(data, lt, closing=True)
            if gt is None:
                i = lt + 1
                continue
            yield from emit_text(lt)
            yield ("end", name, {})
            tstart = gt + 1
            i = tstart
            continue
        # --- comment --------------------------------------------------
        if data.startswith("<!--", lt):
            end = data.find("-->", lt + 4)
            if end >= 0:
                ctext = data[lt + 4:end]
                end += 3
            else:
                ctext = data[lt + 4:]
                end = n
            yield from emit_text(lt)
            yield ("comment", ctext.strip())
            tstart = end
            i = end
            continue
        # --- CDATA (bogus comment in HTML) -----------------------------
        if data.startswith("<![CDATA[", lt):
            end = data.find("]]>", lt + 9)
            if end >= 0:
                ctext = data[lt + 9:end]
                end += 3
            else:
                ctext = data[lt + 9:]
                end = n
            yield from emit_text(lt)
            yield ("comment", ctext.strip())
            tstart = end
            i = end
            continue
        # --- doctype / bogus declaration -------------------------------
        if nxt == "!":
            gt = data.find(">", lt)
            if gt < 0:
                i = lt + 1
                continue
            yield from emit_text(lt)
            body = data[lt + 2:gt]
            bl = body.lower()
            if bl.startswith("doctype"):
                # <!DOCTYPE html> → name "html"; "" for a bare <!DOCTYPE>
                yield ("doctype", body[7:].strip().lower())
            else:
                yield ("comment", body.strip())
            tstart = gt + 1
            i = tstart
            continue
        # --- bogus comment <?...> --------------------------------------
        if nxt == "?":
            gt = data.find(">", lt)
            if gt < 0:
                i = lt + 1
                continue
            yield from emit_text(lt)
            yield ("comment", data[lt + 1:gt].rstrip("?").strip())
            tstart = gt + 1
            i = tstart
            continue
        # --- '<' is literal text --------------------------------------
        #   P24.2 — a '<' not followed by a tag opener / markup opener is
        #   literal text.  The spec consumes only the '<'; resuming at the
        #   next character keeps the following markup (e.g. "a < b</p>")
        #   intact instead of swallowing it into the text run.
        i = lt + 1
    yield from emit_text(n)


def tokenize_rawtext(data: "str | bytes") -> list:
    """P24.1 — tokenize *data* honouring the raw-text / RCDATA states.

    Inside ``<script>`` / ``<style>`` / ``<textarea>`` / ``<title>`` the
    content is **not** parsed as HTML; only the matching closing tag
    terminates it (and only ``textarea`` / ``title`` decode character
    references).
    """
    return list(iter_tokens(data))


def tokenize(data: "str | bytes") -> list:
    """P24.2 — tokenize *data* (str or bytes) into a flat token list.

    R7-H01 — this is now the same scanner as :func:`tokenize_rawtext`;
    keeping two divergent scanners was a source of bugs (the legacy
    ``tokenize`` did not implement raw-text mode at all).  The name is
    kept because :mod:`dom` re-exports it.
    """
    return list(iter_tokens(data))

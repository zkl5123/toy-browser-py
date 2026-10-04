"""P24.1 — HTML entity decoding (WHATWG subset + numeric refs).

The P24 plan requires the full HTML entity table (``&amp;`` ``&nbsp;``
``&#x1F600;`` etc).  This module provides:

* ``decode_entities(text)`` — decodes named entities (via a curated
  WHATWG subset) AND numeric references (``&#38;`` / ``&#x26;``) in a
  single pass.  Unrecognised ``&name`` sequences are left verbatim
  (spec behaviour for the toy subset).
* ``parse_entities(text)`` — low-level iterator yielding
  ``(position, length, decoded)`` for each entity found.

The named-entity table is a curated subset of WHATWG's
``entities.json`` covering the named entities most likely to appear in
real-world documents (the P24 "经典容错用例" set).  The numeric
reference path handles the rest, so a missing named entry never
breaks decoding — it just falls through to a literal ``&name``.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# P24.1 — WHATWG named-entity table (curated subset)
# ---------------------------------------------------------------------------

_NAMED_ENTITIES: dict[str, str] = {
    # -- Core 6 (the ubiquitous ones) --
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
    "nbsp": "\u00A0", "shy": "\u00AD",
    # -- Currencies / math / symbols --
    "cent": "\u00A2", "pound": "\u00A3", "yen": "\u00A5", "euro": "\u20AC",
    "curren": "\u00A4", "brvbar": "\u00A6", "sect": "\u00A7",
    "plusmn": "\u00B1", "laquo": "\u00AB", "raquo": "\u00BB",
    "deg": "\u00B0", "times": "\u00D7", "divide": "\u00F7",
    "frac14": "\u00BC", "frac12": "\u00BD", "frac34": "\u00BE",
    "copy": "\u00A9", "reg": "\u00AE", "trade": "\u2122",
    "hellip": "\u2026", "middot": "\u00B7", "bull": "\u2022",
    "dagger": "\u2020", "Dagger": "\u2021",
    # -- Accented letters (Latin-1 supplement) --
    "iexcl": "\u00A1", "iquest": "\u00BF",
    "agrave": "\u00E0", "aacute": "\u00E1", "acirc": "\u00E2",
    "atilde": "\u00E3", "auml": "\u00E4", "aring": "\u00E5",
    "aelig": "\u00E6", "ccedil": "\u00E7",
    "egrave": "\u00E8", "eacute": "\u00E9", "ecirc": "\u00EA",
    "euml": "\u00EB", "igrave": "\u00EC", "iacute": "\u00ED",
    "icirc": "\u00EE", "iuml": "\u00EF", "eth": "\u00F0",
    "ntilde": "\u00F1", "ograve": "\u00F2", "oacute": "\u00F3",
    "ocirc": "\u00F4", "otilde": "\u00F5", "ouml": "\u00F6",
    "oslash": "\u00F8", "ugrave": "\u00F9", "uacute": "\u00FA",
    "ucirc": "\u00FB", "uuml": "\u00FC", "yacute": "\u00FD",
    "thorn": "\u00FE", "yuml": "\u00FF",
    # -- Arrows / symbols / suits --
    "larr": "\u2190", "uarr": "\u2191", "rarr": "\u2192", "darr": "\u2193",
    "harr": "\u2194", "crarr": "\u21B5",
    "loz": "\u25CA", "spades": "\u2660", "clubs": "\u2663",
    "hearts": "\u2665", "diams": "\u2666",
    # -- Greek (common) --
    "alpha": "\u03B1", "beta": "\u03B2", "gamma": "\u03B3",
    "delta": "\u03B4", "epsilon": "\u03B5", "zeta": "\u03B6",
    "eta": "\u03B7", "theta": "\u03B8", "iota": "\u03B9",
    "kappa": "\u03BA", "lambda": "\u03BB", "mu": "\u03BC",
    "nu": "\u03BD", "xi": "\u03BE", "pi": "\u03C0",
    "rho": "\u03C1", "sigma": "\u03C3", "tau": "\u03C4",
    "phi": "\u03C6", "chi": "\u03C7", "psi": "\u03C8",
    "omega": "\u03C9",
    "Gamma": "\u0393", "Delta": "\u0394", "Theta": "\u0398",
    "Lambda": "\u039B", "Xi": "\u039E", "Pi": "\u03A0",
    "Sigma": "\u03A3", "Phi": "\u03A6", "Psi": "\u03A8",
    "Omega": "\u03A9",
    # -- Mathematical operators / relations --
    "le": "\u2264", "ge": "\u2265", "ne": "\u2260", "equiv": "\u2261",
    "asymp": "\u2248", "sim": "\u223C", "approx": "\u2248",
    "prop": "\u221D", "infin": "\u221E", "sum": "\u2211",
    "prod": "\u220F", "int": "\u222B", "forall": "\u2200",
    "exists": "\u2203", "emptyset": "\u2205", "isin": "\u2208",
    "notin": "\u2209", "subset": "\u2282", "supset": "\u2283",
    "cup": "\u222A", "cap": "\u2229", "ang": "\u2220",
    "perp": "\u22A5", "sdot": "\u2219",
    # -- Subscripts / superscripts --
    "sup1": "\u00B9", "sup2": "\u00B2", "sup3": "\u00B3",
    "sub1": "\u2081", "sub2": "\u2082", "sub3": "\u2083",
    # -- Dashes / quotes / misc punctuation --
    "ndash": "\u2013", "mdash": "\u2014",
    "lsquo": "\u2018", "rsquo": "\u2019",
    "ldquo": "\u201C", "rdquo": "\u201D",
    "lnot": "\u00AC", "macr": "\u00AF", "ordf": "\u00AA",
    "ordm": "\u00BA", "para": "\u00B6",
    "times": "\u00D7", "divide": "\u00F7", "deg": "\u00B0",
    "plus": "+", "minus": "-",
}

# ---------------------------------------------------------------------------
# P24.1 — numeric + named entity scanner
# ---------------------------------------------------------------------------

# Matches:  &name;  OR  &#123;  OR  &#xABC;
_ENTITY_RE = re.compile(
    r"&"
    r"(?:"
    r"(?P<named>[A-Za-z][A-Za-z0-9]{0,20}?);|"
    r"(?P<dec>#[0-9]{1,7});|"
    r"(?P<hex>#[Xx][0-9A-Fa-f]{1,6});"
    r")"
)


def _safe_chr(code: int) -> str:
    """U+0000 and surrogate ranges are invalid in HTML text; map them
    to U+FFFD (spec §tokenization)."""
    if code == 0 or 0xD800 <= code <= 0xDFFF:
        return "\uFFFD"
    try:
        return chr(code)
    except (ValueError, OverflowError):
        return "\uFFFD"


def parse_entities(text: str):
    """P24.1 — yield ``(position, length, decoded)`` for each entity
    in *text*.  The caller can splice them into the decoded string.
    Unrecognised ``&...`` (no ``;``) yields nothing (literal)."""
    for m in _ENTITY_RE.finditer(text):
        start, whole = m.start(), m.group(0)
        length = len(whole)
        if m.group("named") is not None:
            name = m.group("named")
            decoded = _NAMED_ENTITIES.get(name)
            if decoded is None:
                # Not in the curated table → leave literal (&name;).
                continue
            yield start, length, decoded
        elif m.group("dec") is not None:
            code = int(m.group("dec")[1:], 10)
            if code <= 0x10FFFF:
                yield start, length, _safe_chr(code)
        elif m.group("hex") is not None:
            code = int(m.group("hex")[2:], 16)  # [2:] skips "#X"/"#x"
            if code <= 0x10FFFF:
                yield start, length, _safe_chr(code)


def decode_entities(text: str) -> str:
    """P24.1 — decode all HTML entities (named + numeric) in *text*.

    Single-pass, left-to-right; entities do not nest.  Text with no
    ``&`` short-circuits (no regex work)."""
    if "&" not in text:
        return text
    out = []
    last = 0
    for pos, length, decoded in parse_entities(text):
        out.append(text[last:pos])
        out.append(decoded)
        last = pos + length
    out.append(text[last:])
    return "".join(out)

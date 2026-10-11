"""Stage 2 — CSS parsing, selectors, cascade, and styled-tree build.

Pure standard library. Supported:
- type / class / id / descendant selectors
- inline style="" attributes
- specificity cascade (id > class > type)
- properties: color, background, font-size, line-height, display,
  width, height, margin, padding, border-width, border-color, text-align
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from dom import Node
from paint import Color
import font as _css_font_mod
import text as _css_text_mod

DEFAULT_FONT_PX = 16.0


# --- Value types -----------------------------------------------------------

@dataclass
class Length:
    value: float
    unit: str  # "px" | "em"


@dataclass
class CalcExpr:
    """P33.P1 — calc() expression: a list of (value, unit) terms joined by
    + / −.  ``unit`` is "px" | "em" | "%" | "" (unitless).

    A single-term expression with mixed units is still valid (e.g.
    ``calc(100% - 20px)`` → [(100.0, "%"), (-20.0, "px")]).
    Resolution requires a reference size (parent content width/height)
    for "%" terms; see :func:`resolve_length`.
    """
    terms: tuple  # tuple of (float, str) pairs
    unit_hint: str = ""  # dominant unit when all terms agree ("" when mixed)


@dataclass
class VarExpr:
    """P33.P1 — var(--name, fallback).  Resolved at style-application
    time against the node's inherited custom-property map."""
    name: str
    fallback: object = None  # a Value or None


@dataclass
class Keyword:
    value: str
    important: bool = False  # P25.2 — `!important` flag


@dataclass
class ImportantDecl:
    """P25.2 — wraps a Value that carries `!important`."""
    value: object
    prop: str = ""
    important: bool = True


# Value = Length | Keyword | Color | ImportantDecl
Value = object


def parse_color(text: str) -> Color | None:
    t = text.strip().lower()
    NAMED = {
        "red": (255, 0, 0),
        "blue": (0, 0, 255),
        "green": (0, 128, 0),
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "gray": (128, 128, 128),
        "grey": (128, 128, 128),
    }
    if t in NAMED:
        r, g, b = NAMED[t]
        return Color(r, g, b)
    if t.startswith("#") and len(t) >= 4:
        hexs = t[1:]
        try:
            if len(hexs) in (3, 4):
                r = int(hexs[0] * 2, 16)
                g = int(hexs[1] * 2, 16)
                b = int(hexs[2] * 2, 16)
                a = int(hexs[3] * 2, 16) if len(hexs) == 4 else 255
                return Color(r, g, b, a)
            if len(hexs) in (6, 8):
                r = int(hexs[0:2], 16)
                g = int(hexs[2:4], 16)
                b = int(hexs[4:6], 16)
                a = int(hexs[6:8], 16) if len(hexs) == 8 else 255
                return Color(r, g, b, a)
        except ValueError:
            return None
    return None


def parse_declaration_pairs(src: str) -> dict[str, Value]:
    """Parse 'color: red; font-size: 14px;' into a property map.

    P25.2 — supports ``!important`` suffix: ``color: red !important``
    wraps the value in :class:`ImportantDecl`."""
    out: dict[str, Value] = {}
    for decl in src.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not prop or not val:
            continue
        # P25.2 — detect `!important` suffix.
        important = False
        if val.lower().endswith("!important"):
            important = True
            val = val[: -len("!important")].strip()
        v = parse_value(val)
        out[prop] = ImportantDecl(v, prop, important) if important else v
    return out


def parse_value(val: str) -> Value:
    v = val.strip()
    lc = v.lower()
    if lc.startswith("#"):
        c = parse_color(v)
        if c is not None:
            return c
    c = parse_color(v)
    if c is not None:
        return c
    # P33.P1 — var(--name, fallback): custom property reference.
    if lc.startswith("var(") and lc.endswith(")"):
        inner = lc[4:-1]
        # Split on the first top-level comma (fallback may itself contain
        # a nested var(); toy: naive split on first comma).
        depth = 0
        split_at = -1
        for i, ch in enumerate(inner):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                split_at = i
                break
        if split_at >= 0:
            name = inner[:split_at].strip()
            fb_raw = inner[split_at + 1:].strip()
            fb = parse_value(fb_raw) if fb_raw else None
        else:
            name = inner.strip()
            fb = None
        return VarExpr(name, fb)
    # P12.3 — calc() toy: evaluate simple binary expressions like
    # "calc(100px - 20px)" or "calc(10px + 5%)".
    # P33.P1 — calc(100% - 20px): mixed %/px now yields CalcExpr.
    if lc.startswith("calc(") and lc.endswith(")"):
        return parse_calc(lc[5:-1])
    # P5.1 — multi-token values (box-shadow, transform, grid-template-*)
    # are kept as a raw Keyword so layout/paint can regex-parse them.
    if " " in v:
        return Keyword(v)
    # Length with unit
    for unit in ("px", "em", "%", "pt"):
        if lc.endswith(unit):
            num = v[: -len(unit)]
            try:
                return Length(float(num), unit)
            except ValueError:
                break
    # Bare number (unitless, e.g. line-height: 1.5)
    try:
        return Keyword(str(float(v)))
    except ValueError:
        pass
    return Keyword(v)


# P34.1 — precompiled regex for parse_calc (the P33 implementation
# re-compiled the pattern string on every call — a hot path when a
# document uses calc() in many rules).
_CALC_TOKEN_RE = __import__("re").compile(
    r"([\d.]+)(px|em|%|pt)?\s*(?=[+-]|$)|([+-])"
)


def parse_calc(expr: str) -> Value:
    """P12.3 / P33.P1 — evaluate a calc() expression.

    Supports multi-term ``+`` / ``-`` between operands, each being a
    number with optional unit (``px``, ``em``, ``%``, ``pt``) or a bare
    number.  Returns a :class:`CalcExpr` when the result mixes units
    (e.g. ``calc(100% - 20px)`` → ``[(100.0, "%"), (-20.0, "px")]``),
    a :class:`Length` when all terms share one unit, a unitless
    :class:`Keyword` when no unit, or a raw :class:`Keyword` fallback
    for unsupported shapes.
    """
    import re as _re
    expr = expr.strip()
    # Tokenize: number+unit pairs and binary + / - operators.
    # P34.1 — use the module-level precompiled pattern.
    token_re = _CALC_TOKEN_RE
    # Simpler approach: split on + and - operators while respecting
    # signed numbers.  Toy: no nested calc() inside calc().
    # Walk the string building (value, sign, unit) terms.
    terms: list = []  # list of (float, str, int)  [value, unit, sign]
    i = 0
    n = len(expr)
    while i < n:
        # Skip whitespace
        while i < n and expr[i].isspace():
            i += 1
        if i >= n:
            break
        ch = expr[i]
        if ch in "+-":
            # Binary operator.  Determine sign for the next term.
            # The first term's sign: leading '-' is part of the number;
            # a sign followed by whitespace or another sign is binary.
            # Toy heuristic: if terms already exist, the sign is binary
            # (applies to the upcoming term).  If no terms yet, it's
            # part of the number (leading minus).
            sign = 1 if ch == "+" else -1
            i += 1
            # P33.P1 — skip whitespace after the operator before reading
            # the next number (calc(100px - 20px) has a space after -).
            while i < n and expr[i].isspace():
                i += 1
            m2 = _re.match(r"([\d.]+)(px|em|%|pt)?", expr[i:])
            if m2:
                val = float(m2.group(1))
                unit = m2.group(2) or ""
                terms.append((val * sign, unit, sign))
                i += m2.end()
            else:
                return Keyword(f"calc({expr})")
        else:
            # Start of a number (first term).
            m2 = _re.match(r"(-?[\d.]+)(px|em|%|pt)?", expr[i:])
            if m2:
                val = float(m2.group(1))
                unit = m2.group(2) or ""
                terms.append((val, unit, 1))
                i += m2.end()
            else:
                return Keyword(f"calc({expr})")
    if not terms:
        return Keyword(f"calc({expr})")
    # Collect distinct units (ignore the empty string for terms without units).
    units = set(t[1] for t in terms if t[1])
    if len(units) == 0:
        # All terms unitless → Keyword.
        total = sum(t[0] for t in terms)
        return Keyword(str(total))
    if len(units) == 1:
        # All terms share the same unit → Length.
        u = next(iter(units))
        total = sum(t[0] for t in terms)
        return Length(total, u)
    # Multi-unit — try legacy eager em→px conversion (1em = 16px, P12.3).
    # em + px → Length in px; % + px/em/pt → CalcExpr (deferred, P33.P1).
    has_pct = any(t[1] == "%" for t in terms)
    if not has_pct:
        # All absolute units — convert em→px and sum.
        total_px = 0.0
        for tv, tu, _sign in terms:
            if tu == "em":
                total_px += tv * 16.0
            elif tu == "pt":
                total_px += tv * (96.0 / 72.0)  # 1pt ≈ 1.333px
            else:  # px or ""
                total_px += tv
        return Length(total_px, "px")
    # Mixed % + absolute → CalcExpr for deferred resolution against ref size.
    calc_terms = tuple((t[0], t[1]) for t in terms)
    non_pct = set(t[1] for t in terms if t[1] and t[1] != "%")
    hint = next(iter(non_pct)) if len(non_pct) == 1 else ""
    return CalcExpr(calc_terms, hint)


# --- Selectors --------------------------------------------------------------

#: P34.1 — precompiled regex for :nth-child(An+B).  The P19 implementation
# re-ran ``re.match`` on a fresh pattern string on every call (~3.5 M
# pattern compilations in the 10k-node benchmark); precompile once.
_NTH_CHILD_RE = __import__("re").compile(r"([+-]?\d*)n\s*([+-]\s*\d+)?\s*$")


@dataclass(frozen=True)
class Selector:
    """A compound selector.

    ``parts`` holds the simple-selector strings from leftmost to rightmost
    (e.g. ``("div", "p")`` for ``div p``, ``(".a", "b")`` for ``.a > b``).
    ``combinator`` is ``" "`` (descendant, default) or ``">"`` (child).
    ``specificity`` is a ``(inline, id, cls, type)`` 4-tuple.
    ``pseudo`` is an optional pseudo-class (":hover", ":focus") for the
    rightmost part.  Pseudo-classes are matched against the current
    interaction state, not the DOM tree.
    """
    parts: tuple[str, ...]
    combinator: str
    specificity: tuple[int, int, int, int]  # P25.1 — (inline, id, cls, type)
    pseudo: str = ""  # ":hover" / ":focus" / ""
    selector_text: str = ""  # P32.4 — raw source text (DevTools provenance)

    def matches(self, node: Node, interaction_state: dict | None = None) -> bool:
        """Match this selector against a DOM node.

        ``interaction_state`` is an optional dict with keys ``"hover"`` and
        ``"focus"`` holding the currently hovered/focused element id (or tag).
        Pseudo-class ``:hover`` matches when the node's id (or tag) equals
        the hover target; ``:focus`` likewise for focus.

        Child-position pseudo-classes (:first-child, :last-child, :nth-child)
        are matched against the DOM structure directly.

        Descendant (default): the rightmost part must match ``node`` and
        every leftward part must match SOME ancestor (any distance).

        Child (">"): the rightmost part must match ``node``; the remaining
        parts must match the IMMEDIATE ancestor chain in order — no skipping.
        """
        # P8.1 — pseudo-class check: skip :hover/:focus when no interaction
        # state is provided (the rule can never match in headless mode).
        if self.pseudo in (":hover", ":focus") and interaction_state is None:
            return False
        # P8.1 / P10.1 — pseudo-class check on the rightmost part.
        if self.pseudo:
            if self.pseudo == ":hover":
                if interaction_state.get("hover") != node.attr("id"):
                    return False
            elif self.pseudo == ":focus":
                if interaction_state.get("focus") != node.attr("id"):
                    return False
            # P10.1 — child-position pseudo-classes (DOM-structure based).
            elif self.pseudo == ":first-child":
                if not self._is_first_child(node):
                    return False
            elif self.pseudo == ":last-child":
                if not self._is_last_child(node):
                    return False
            elif self.pseudo.startswith(":nth-child"):
                idx = self._child_index(node)
                if idx is None:
                    return False
                if not self._nth_matches(self.pseudo, idx):
                    return False
        if not self._simple_matches(self.parts[-1], node):
            return False
        if len(self.parts) == 1:
            return True

        if self.combinator == ">":
            # Walk the immediate ancestor chain (no skipping allowed).
            ancestor = node.parent
            for part in reversed(self.parts[:-1]):
                if ancestor is None:
                    return False
                if not self._simple_matches(part, ancestor):
                    return False
                ancestor = ancestor.parent
            return True

        # Descendant: every leftward part must match some ancestor.
        return self._ancestor_chain(self.parts[:-1], node.parent)

    def _ancestor_chain(self, left_parts: tuple[str, ...], start: Node | None) -> bool:
        """Walk ``start``'s ancestors matching each left part (right-to-left)."""
        idx = len(left_parts) - 1
        ancestor = start
        while idx >= 0 and ancestor is not None:
            if self._simple_matches(left_parts[idx], ancestor):
                idx -= 1
            ancestor = ancestor.parent
        return idx < 0

    @staticmethod
    def _simple_matches(simple: str, node: Node) -> bool:
        """P32 fix — one *simple selector* token: may combine a tag,
        classes, and an id.  Supported compound forms:

        * ``tag``           — element type
        * ``.class``        — single class
        * ``.a.b``          — multi-class (all must be present)
        * ``#id``           — id
        * ``tag.class``     — tag + class (e.g. ``p.x``)
        * ``tag.id``        — tag + id   (e.g. ``div#box``)
        * ``tag.id.class``  — full compound

        The tag portion (leading identifier without ``.``/``#``) is
        optional; when present it must match the node's tag.  Each
        ``.`` / ``#`` segment after the tag is matched independently.
        """
        simple = simple.strip()
        if not simple:
            return False

        # Split into segments: a leading bare tag name, then ``.class``
        # and ``#id`` segments.  ``p.x`` → ("p", ".x"); ``div#box`` →
        # ("div", "#box"); ``.a.b`` → ("", ".a.b").
        import re as _re
        m = _re.match(
            r"^(?P<tag>[a-zA-Z][a-zA-Z0-9-]*)?(?P<tail>(?:[#.][\w-]+)*)$",
            simple,
        )
        if m is None:
            # Unknown compound — fall back to legacy single-part check.
            if simple.startswith("#"):
                return node.attr("id") == simple[1:]
            if simple.startswith("."):
                return node.has_class(simple[1:])
            return node.tag_name() == simple
        tag_part = m.group("tag") or ""
        tail = m.group("tail") or ""

        # 1) tag portion (when present).
        if tag_part:
            if node.tag_name() != tag_part:
                return False

        # 2) class / id segments in the tail.
        for seg in _re.findall(r"[#.][\w-]+", tail):
            if seg.startswith("#"):
                if node.attr("id") != seg[1:]:
                    return False
            else:  # class
                if not node.has_class(seg[1:]):
                    return False
        return True

    # --- P10.1 — child-position pseudo-class helpers -------------------------

    @staticmethod
    def _child_index(node: Node) -> int | None:
        """Return the 1-based child position of ``node`` among its
        element-sibling parents, or ``None`` if the parent has no
        element children (top-level document node).

        P34.1 — memoized per-node (attribute ``__child_index``): the
        index is structural and stable for the lifetime of the tree, so
        a second match against the same node reuses the first scan
        instead of re-walking all siblings.
        """
        parent = node.parent
        if parent is None:
            return None
        memo = getattr(node, "__child_index", None)
        if memo is not None:
            return memo
        idx = 0
        for c in parent.children:
            if c.is_element():
                idx += 1
                if c is node:
                    return idx
        return None

    @classmethod
    def _is_first_child(cls, node: Node) -> bool:
        return cls._child_index(node) == 1

    @classmethod
    def _is_last_child(cls, node: Node) -> bool:
        idx = cls._child_index(node)
        if idx is None:
            return False
        # Count total element children of the parent.
        parent = node.parent
        if parent is None:
            return False
        total = sum(1 for c in parent.children if c.is_element())
        return idx == total

    @staticmethod
    def _nth_matches(pseudo: str, idx: int) -> bool:
        """Evaluate an ``:nth-child(...)`` expression against a 1-based
        child index ``idx``.

        Supported forms:
          - ``:nth-child(N)``            → idx == N
          - ``:nth-child(even)``         → idx % 2 == 0
          - ``:nth-child(odd)``         → idx % 2 == 1
          - ``:nth-child(An+B)``        → (idx - B) % A == 0 and (idx - B) >= 0
        """
        inner = pseudo[len(":nth-child("):-1].strip()
        if inner == "even":
            return idx % 2 == 0
        if inner == "odd":
            return idx % 2 == 1
        m = _NTH_CHILD_RE.match(inner)
        if m:
            a_str = m.group(1).replace("n", "").strip()
            a = int(a_str) if a_str else 0
            b_str = m.group(2).replace(" ", "") if m.group(2) else "0"
            b = int(b_str) if b_str else 0
            if a == 0:
                # An + B with A=0 → idx == B
                return idx == b
            # (idx - B) must be a non-negative multiple of A
            diff = idx - b
            if diff < 0:
                return False
            return diff % a == 0
        # Bare number (no n) — already caught by the regex above.
        try:
            return idx == int(inner)
        except ValueError:
            return False

    @classmethod
    def parse(cls, text: str) -> "Selector":
        """Parse a single selector string.

        Supports:
          - simple:  ``tag``, ``.class``, ``#id``, ``.a.b``
          - descendant:  ``a b``, ``div p``
          - child:  ``a > b``, ``div > p``
          - P8.1 pseudo-class: ``a:hover``, ``.btn:focus`` (rightmost part only)
        """
        text = text.strip()
        # Tokenize: split on spaces, but keep ".a.b" as one token.
        # Simple approach: split on whitespace; tokens starting with "."
        # that contain no spaces are multi-class selectors.
        tokens = text.split()
        if not tokens:
            raise ValueError(f"empty selector: {text!r}")

        parts: list[str] = []
        combinator = " "
        pseudo = ""
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok == ">":
                combinator = ">"
                i += 1
                continue
            # P8.1 / P10.1 — strip a trailing pseudo-class from the rightmost token.
            # ":hover" / ":focus" / ":first-child" / ":last-child" /
            # ":nth-child(N)" / ":nth-child(2n+1)" are matched at
            # interaction / DOM-structure time, not parse time.
            if ":" in tok:
                base, p = tok.rsplit(":", 1)
                p = p.strip()
                if base:  # avoid empty base from a leading ":"
                    parts.append(base)
                    if p in ("hover", "focus"):
                        pseudo = ":" + p
                    elif p in ("first-child", "last-child"):
                        pseudo = ":" + p
                    elif p.startswith("nth-child"):
                        # ":nth-child(N)" — keep the full pseudo with its arg.
                        pseudo = ":" + p
                    i += 1
                    continue
                else:
                    # Standalone pseudo-class token like ":hover" (no base)
                    # — treat as a whole part and record the pseudo.
                    parts.append(tok)
                    if p in ("hover", "focus", "first-child", "last-child"):
                        pseudo = ":" + p
                    elif p.startswith("nth-child"):
                        pseudo = ":" + p
                    i += 1
                    continue
            parts.append(tok)
            i += 1

        if not parts:
            raise ValueError(f"selector has no simple parts: {text!r}")

        # P25.1 — Compute 4-tuple specificity: (inline, id, cls, type).
        # Inline = 0 here (inline styles are handled separately in the
        # cascade); we only compute the selector part.
        spec_a = spec_b = spec_c = 0
        for p in parts:
            p = p.strip()
            if p.startswith("#"):
                spec_a += 1  # id
            elif p.startswith("."):
                # Count classes/attrs: ".a" → 1, ".a.b" → 2
                body = p[1:]
                cls_count = body.count(".") + 1
                spec_b += cls_count
            else:
                spec_c += 1  # element type
        # P8.1 / P10.1 — any pseudo-class adds one class-level point.
        if pseudo:
            spec_b += 1
        # P25.1 — 4-tuple: (inline=0, id, cls, type)
        return cls(tuple(parts), combinator, (0, spec_a, spec_b, spec_c), pseudo,
                   selector_text=text)

    def specificity_sum(self) -> tuple[int, int, int, int]:
        return self.specificity


def parse_selector_list(text: str) -> list[Selector]:
    """Parse a comma-separated CSS selector list."""
    text = text.strip()
    if not text:
        return []
    out: list[Selector] = []
    for sel in text.split(","):
        sel = sel.strip()
        if sel:
            out.append(Selector.parse(sel))
    return out


# --- @keyframes + animation (P4.1) ---------------------------------------------


@dataclass
class Keyframe:
    """A single keyframe: offset in [0,1] and a property map."""
    offset: float
    declarations: dict[str, Value]


@dataclass
class Animation:
    """An @keyframes animation: name, frame list, duration, iteration-count."""
    name: str
    frames: list[Keyframe]  # sorted by offset
    duration: float = 1.0  # in seconds
    iterations: float = 1.0  # inf → loop


#: P34.1 — memo: css_text → parsed Rule list (see ``parse_rulesheet``).
_rulesheet_memo: dict = {}
#: P34.1 — memo: css_text → @keyframes Animation map (see parse_keyframes).
_keyframes_memo: dict = {}
#: P34.1 — memo: css_text → @media MediaQuery list (see parse_media_queries).
_media_queries_memo: dict = {}
#: P34.1 — memo: css_text → @supports SupportsRule list (see parse_supports_blocks).
_supports_blocks_memo: dict = {}
#: P34.1 — memo: css_text → @font-face FontFace list (see parse_font_faces).
_font_faces_memo: dict = {}

# P34.5 — candidate-rule cache: node identity → list of matching Rule
# objects.  Keyed on (tag, id-attr, class-attr, hover-id, focus-id);
# the vast majority of nodes in a page share a small number of
# distinct identities (e.g. "div.c0" / "p" / "li"), so the
# per-selector match walk runs once per identity instead of once per
# node.  Bounded at 4096 distinct identities; cleared together with
# the rulesheet memo (clear_caches) so a new CSS document starts clean.
_candidate_rules_cache: dict = {}
_CANDIDATE_RULES_CACHE_MAX = 4096


# P34.5 — module flag set by apply(): True when the current ruleset
# contains at least one "#id" selector, in which case the
# candidate-rule cache identity key includes the element id so id-only
# rules still resolve per-node; False for the common tag/class-driven
# ruleset, where including the id would 100 %-miss a cache on a
# document whose elements all carry unique ids.
_identity_includes_id = False

# P34.5 — module flag set by apply(): True when the current ruleset
# contains at least one *compound* selector (a multi-part selector with
# a descendant or child combinator, e.g. "div p" or "div > p").  Such
# selectors match based on the node's *ancestor chain*, not on the
# node's own identity alone, so a node-identity cache is insufficient
# — two <p> nodes with the same (tag, class) identity can sit under
# different ancestors and therefore match different rule sets.  When
# this flag is True, the candidate-rule cache is bypassed and the
# legacy per-node match walk runs on every node (correctness first);
# when False (all selectors are single-part: tag / .class / #id), the
# cache is a pure function of the node's own identity and the legacy
# walk is replaced by a single dict hit.  apply() sets this flag by
# scanning the ruleset.
_identity_ancestor_sensitive = False


def clear_caches() -> None:
    """P34.5 — reset all module-level CSS caches.

    Call between independent document renders (e.g. a navigation in
    the GUI) so the candidate-rule cache does not survive across pages
    with different selector sets.  The legacy behaviour (no cache) is
    the safe default: stale matches are never produced.
    """
    _candidate_rules_cache.clear()


def parse_keyframes(css_text: str) -> dict[str, Animation]:
    """P34.1 — memoized @keyframes parse (callers read the map only)."""
    cached = _keyframes_memo.get(css_text)
    if cached is not None:
        return cached
    out = _parse_keyframes_impl(css_text)
    if len(_keyframes_memo) < 256:
        _keyframes_memo[css_text] = out
    return out


def _parse_keyframes_impl(css_text: str) -> dict[str, Animation]:
    """Extract all @keyframes blocks from CSS text.

    Returns {animation_name: Animation}.  Each @keyframes block contains
    ``from { … }``, ``to { … }``, and/or ``NN% { … }`` keyframe selectors.
    The name is taken from ``@keyframes <name> { … }``.
    """
    out: dict[str, Animation] = {}
    i = 0
    n = len(css_text)
    while i < n:
        # Look for "@keyframes" at current position.
        kf_idx = css_text.find("@keyframes", i)
        if kf_idx < 0:
            break
        # Find the opening brace of the @keyframes block.
        brace = css_text.find("{", kf_idx)
        if brace < 0:
            break
        # Animation name is between "@keyframes" and "{".
        name_text = css_text[kf_idx + len("@keyframes"):brace].strip()
        # Find matching close brace.
        depth = 0
        j = brace
        while j < n:
            if css_text[j] == "{":
                depth += 1
            elif css_text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block_body = css_text[brace + 1 : j]
        i = j + 1

        frames: list[Keyframe] = []
        # P12.1 — scan for `name Xs;` / `name Xms;` duration declarations
        # that appear inside the @keyframes block.
        import re as _re
        dur_m = _re.search(r"duration\s*:\s*([\d.]+)\s*(s|ms)", block_body)
        iterations_m = _re.search(r"iteration-count\s*:\s*([\d.inf]+)", block_body)
        anim = Animation(name=name_text, frames=frames)
        if dur_m:
            val = float(dur_m.group(1))
            anim.duration = val if dur_m.group(2) == "s" else val / 1000.0
        if iterations_m:
            raw = iterations_m.group(1)
            anim.iterations = float("inf") if raw == "infinite" else float(raw)
        # Parse inner keyframe selectors: "from", "to", or "NN%".
        k = 0
        m = len(block_body)
        while k < m:
            sel_brace = block_body.find("{", k)
            if sel_brace < 0:
                break
            sel_text = block_body[k:sel_brace].strip()
            end_brace = block_body.find("}", sel_brace)
            if end_brace < 0:
                break
            decl_src = block_body[sel_brace + 1 : end_brace]
            k = end_brace + 1

            # Determine offset.
            sel_lower = sel_text.lower()
            if sel_lower == "from":
                offset = 0.0
            elif sel_lower == "to":
                offset = 1.0
            else:
                # Percentage like "50%"
                try:
                    offset = float(sel_text.rstrip("%").strip()) / 100.0
                    offset = max(0.0, min(1.0, offset))
                except ValueError:
                    continue

            decls = parse_declaration_pairs(decl_src)
            if decls:
                frames.append(Keyframe(offset, decls))

        frames.sort(key=lambda f: f.offset)
        if frames:
            anim.frames = frames
            out[name_text] = anim

    return out


# P12.1 — multi-property @keyframes + AnimationHandle (Web Animations toy)

@dataclass
class KeyframeTrack:
    """A single property's keyframe samples across the whole animation.

    P12.1 — instead of applying one property's interpolated value per frame
    (P4.1), a KeyframeTrack collects every ``Keyframe`` that declares the
    property, plus a reference to the Animation for the duration.
    """
    property: str
    samples: list            # list of (offset, value) sorted by offset
    duration: float = 1.0   # seconds


class AnimationHandle:
    """P12.1 — a minimal Web Animations-style handle.

    Mirrors ``element.animate(keyframes, options)``: stores a per-property
    keyframe track set and exposes ``currentTime`` / ``duration`` /
    ``playState``.  ``frame_overrides(t)`` returns a dict of
    ``{property: value}`` at normalized time ``t in [0, 1]`` for every
    tracked property.
    """

    def __init__(self, duration: float = 1.0, iterations: float = 1.0):
        self.duration = duration
        self.tracks: list[KeyframeTrack] = []
        self.play_state = "running"
        self.current_time = 0.0
        self.iterations = iterations

    def add_track(self, prop: str, samples: list, duration: float = 1.0):
        """Register one property's keyframe samples.

        ``samples`` is ``[(offset, value), …]``; missing values at a given
        offset fall back to the first lower-offset sample (step semantics).
        """
        self.tracks.append(KeyframeTrack(prop, samples, duration))

    def frame_overrides(self, t: float) -> dict:
        """Interpolate every tracked property at normalized time ``t``.

        Returns a dict ``{property: value}``.  For each track, find the two
        bracketing samples and lerp (via ``transition.lerp_value``).
        """
        import transition as trans_mod
        out: dict = {}
        for track in self.tracks:
            smp = track.samples
            if not smp:
                continue
            if t <= smp[0][0]:
                out[track.property] = smp[0][1]
                continue
            if t >= smp[-1][0]:
                out[track.property] = smp[-1][1]
                continue
            for i in range(len(smp) - 1):
                (lo_off, lo_val), (hi_off, hi_val) = smp[i], smp[i + 1]
                if lo_off <= t <= hi_off:
                    span = hi_off - lo_off
                    local_t = (t - lo_off) / span if span > 0 else 0.0
                    out[track.property] = trans_mod.lerp_value(lo_val, hi_val, local_t)
                    break
            else:
                out[track.property] = smp[-1][1]
        return out

    @property
    def playState(self) -> str:
        return self.play_state

    @property
    def currentTime(self) -> float:
        return self.current_time

    def update(self, t: float):
        """P12.1 — advance the handle to normalized time ``t``."""
        self.current_time = max(0.0, min(1.0, t))


def build_keyframe_handle(anim: "Animation") -> "AnimationHandle":
    """P12.1 — turn a parsed @keyframes Animation into an AnimationHandle.

    Groups keyframe declarations by property so that multi-property
    animations (e.g. simultaneous background + opacity + transform) can
    be driven by a single ``frame_overrides`` call.
    """
    handle = AnimationHandle(duration=anim.duration, iterations=anim.iterations)
    by_prop: dict[str, list] = {}
    for kf in anim.frames:
        for prop, val in kf.declarations.items():
            by_prop.setdefault(prop, []).append((kf.offset, val))
    for prop, samples in by_prop.items():
        samples.sort(key=lambda x: x[0])
        handle.add_track(prop, samples, anim.duration)
    return handle


def apply_animation_frame(
    styled_root: "StyledNode",
    element_id: str,
    anim: Animation,
    t: float,
) -> None:
    """P4.1 — apply an animation frame to the target element's StyledNode.

    P12.1 — multi-property: instead of single-keyframe lookup, builds an
    AnimationHandle and applies all tracked properties at once.
    """
    target = _find_styled_by_id(styled_root, element_id)
    if target is None:
        return
    handle = build_keyframe_handle(anim)
    overrides = handle.frame_overrides(t)
    for prop, val in overrides.items():
        target.props[prop] = val


def _find_styled_by_id(sn: "StyledNode", element_id: str) -> "StyledNode | None":
    if sn.dom and getattr(sn.dom, "attr", None) and sn.dom.attr("id") == element_id:
        return sn
    for c in sn.children:
        r = _find_styled_by_id(c, element_id)
        if r:
            return r
    return None


def resolve_animation_property(target: "StyledNode", prop: str) -> object:
    """Resolve an animation-declared property value for the target element."""
    return target.props.get(prop)


# --- Rule parsing --------------------------------------------------------------

@dataclass
class Rule:
    selectors: list[Selector]
    declarations: dict[str, Value]
    specificity: tuple[int, int, int, int]
    source: str = "author"  # P25.2 — "author" or "ua"
    important: bool = False  # P25.2 — True if any decl is !important
    selector_text: str = ""  # P32.4 — raw selector text (DevTools provenance)


#: P34.1 — memo: css_text → parsed Rule list (see ``parse_rulesheet``).
_rulesheet_memo: dict = {}


def parse_rulesheet(css_text: str) -> list[Rule]:
    """P34.1 — memoized rulesheet parse.

    Identical author CSS text is parsed once and the shared ``Rule``
    list is returned.  Callers that *extend* the list (``css.apply``
    appends @media/@supports rules) must use :func:`parse_rulesheet_nocache`
    so they do not poison the memo entry.
    """
    cached = _rulesheet_memo.get(css_text)
    if cached is not None:
        return cached
    rules = _parse_rulesheet_nocache_impl(css_text)
    # Cap the memo: unbounded growth on hostile input (every distinct
    # string a key) would leak memory.  256 distinct stylesheets is far
    # above any realistic page.
    if len(_rulesheet_memo) < 256:
        _rulesheet_memo[css_text] = rules
    return rules


def parse_rulesheet_nocache(css_text: str) -> list[Rule]:
    """P34.1 — parse a *fresh* rulesheet list, bypassing the memo."""
    return _parse_rulesheet_nocache_impl(css_text)


def _parse_rulesheet_nocache_impl(css_text: str) -> list[Rule]:
    rules: list[Rule] = []
    # A rule is "selector { declarations }"
    i = 0
    n = len(css_text)
    while i < n:
        # P9.1 — skip @media / @keyframes / @supports blocks (handled separately).
        at_next = css_text.find("@", i)
        brace = css_text.find("{", i)
        if at_next >= 0 and at_next < brace:
            # Find the end of the at-rule block.
            j = brace
            depth = 0
            while j < n:
                if css_text[j] == "{":
                    depth += 1
                elif css_text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1 if j < n else n
            continue
        if brace < 0:
            break
        selector_text = css_text[i:brace].strip()
        end = css_text.find("}", brace)
        if end < 0:
            break
        decl_src = css_text[brace + 1 : end]
        i = end + 1
        sels = parse_selector_list(selector_text)
        if not sels:
            continue
        max_spec = max((s.specificity_sum() for s in sels), key=sum, default=(0, 0, 0, 0))
        # P25.2 — detect if any declaration in this rule is !important.
        rule_important = any(
            isinstance(v, ImportantDecl) and v.important
            for v in parse_declaration_pairs(decl_src).values()
        )
        # Re-parse declarations (parse_declaration_pairs returns a fresh
        # dict each call; the double-parse is cheap for the toy).
        decls = parse_declaration_pairs(decl_src)
        rules.append(Rule(sels, decls, max_spec, "author", rule_important,
                          selector_text=selector_text))
    return rules


# --- P9.1 — @media queries ---------------------------------------------------------


@dataclass
class MediaQuery:
    """A parsed @media block: condition string + inner rules.

    The condition is the raw text after ``@media`` (e.g. ``"min-width: 600px"``).
    Rules are the normal ``Rule`` list parsed from the block body.
    """
    condition: str
    rules: list[Rule]


def parse_media_queries(css_text: str) -> list[MediaQuery]:
    """P34.1 — memoized @media extract.

    Extract all @media blocks from CSS text.  Each block's inner rules
    are parsed with the standard ``parse_rulesheet``.  The condition is
    the raw text between ``@media`` and the opening brace.

    Only ``min-width`` / ``max-width`` / ``min-height`` / ``max-height``
    are supported; unknown conditions are stored verbatim for future use.
    """
    cached = _media_queries_memo.get(css_text)
    if cached is not None:
        return cached
    out = _parse_media_queries_impl(css_text)
    if len(_media_queries_memo) < 256:
        _media_queries_memo[css_text] = out
    return out


def _parse_media_queries_impl(css_text: str) -> list[MediaQuery]:
    out: list[MediaQuery] = []
    i = 0
    n = len(css_text)
    while i < n:
        idx = css_text.find("@media", i)
        if idx < 0:
            break
        # Find the opening brace of the @media block.
        brace = css_text.find("{", idx)
        if brace < 0:
            break
        condition = css_text[idx + len("@media"):brace].strip()
        # Find the matching close brace (handle nested braces).
        depth = 0
        j = brace
        while j < n:
            if css_text[j] == "{":
                depth += 1
            elif css_text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = css_text[brace + 1 : j]
        i = j + 1
        inner_rules = parse_rulesheet(body)
        out.append(MediaQuery(condition=condition, rules=inner_rules))
    return out


def media_matches(condition: str, viewport_w: float, viewport_h: float) -> bool:
    """Evaluate a media condition against a viewport size.

    Supported forms:
      - ``min-width: Npx``  →  viewport_w >= N
      - ``max-width: Npx``  →  viewport_w <= N
      - ``min-height: Npx`` →  viewport_h >= N
      - ``max-height: Npx`` →  viewport_h <= N
      - combinations joined by ``and`` (all must match)
      - ``all`` or empty condition → always True

    Unknown conditions return True (toy: don't exclude on unrecognized text).
    """
    import re as _re

    # P9.1 / P12.3 — strip the parenthesized group, then split on "and".
    # Compound forms: "min-width: N and max-height: M", and exact
    # "width: N" / "height: M" constraints.
    cond = condition.strip()
    if cond.startswith("(") and cond.endswith(")"):
        cond = cond[1:-1].strip()
    cond = cond.lower()
    if not cond or cond == "all" or cond == "screen":
        return True

    # P12.3 — split on "and" for compound conditions.
    parts = [p.strip() for p in cond.split("and") if p.strip()]
    for part in parts:
        # P12.3 — exact "width: Npx" / "height: Npx" form.
        m_eq = _re.match(r"(width|height)\s*:\s*([\d.]+)\s*px", part)
        if m_eq:
            axis, val = m_eq.group(1), float(m_eq.group(2))
            actual = viewport_w if axis == "width" else viewport_h
            if actual != val:
                return False
            continue
        m = _re.match(r"(min|max)-(width|height)\s*:\s*([\d.]+)\s*px", part)
        if m:
            op, axis, val = m.group(1), m.group(2), float(m.group(3))
            actual = viewport_w if axis == "width" else viewport_h
            if op == "min":
                if actual < val:
                    return False
            else:
                if actual > val:
                    return False
        # Unknown part → toy: don't block.
    return True


# --- P11.3 — @supports queries ------------------------------------------------------


@dataclass
class SupportsRule:
    """A parsed @supports block: condition + inner rules.

    The condition is the raw text after ``@supports`` (e.g.
    ``"display: grid"`` or ``"(position: sticky)"``).  In this toy engine
    every @supports block is treated as *always supported* — the inner
    rules are simply included in the cascade.  A future version could
    match the condition against a capability table.
    """
    condition: str
    rules: list[Rule]


def parse_supports_blocks(css_text: str) -> list[SupportsRule]:
    """P34.1 — memoized @supports extract."""
    cached = _supports_blocks_memo.get(css_text)
    if cached is not None:
        return cached
    out = _parse_supports_blocks_impl(css_text)
    if len(_supports_blocks_memo) < 256:
        _supports_blocks_memo[css_text] = out
    return out


def _parse_supports_blocks_impl(css_text: str) -> list[SupportsRule]:
    """Extract all @supports blocks from CSS text (same scan style as @media)."""
    out: list[SupportsRule] = []
    i = 0
    n = len(css_text)
    while i < n:
        idx = css_text.find("@supports", i)
        if idx < 0:
            break
        brace = css_text.find("{", idx)
        if brace < 0:
            break
        condition = css_text[idx + len("@supports"):brace].strip()
        depth = 0
        j = brace
        while j < n:
            if css_text[j] == "{":
                depth += 1
            elif css_text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = css_text[brace + 1 : j]
        i = j + 1
        inner_rules = parse_rulesheet(body)
        out.append(SupportsRule(condition=condition, rules=inner_rules))
    return out


def supports_matches(condition: str) -> bool:
    """P11.3 — toy: every @supports condition is considered supported."""
    return True


# --- P33.P3 — @font-face ----------------------------------------------------------

@dataclass
class FontFace:
    """P33.P3 — a parsed @font-face rule.

    Attributes:
        family: the ``font-family`` value (e.g. "MyFont").
        src: the raw ``src`` value (e.g. ``"local('Arial')"``).
        weight: the ``font-weight`` keyword or integer (default 400).
        style: the ``font-style`` keyword (default "normal").
        unicode_range: the ``unicode-range`` value (default "U+0-FFFF").
    """
    family: str
    src: str = ""
    weight: str = "400"
    style: str = "normal"
    unicode_range: str = "U+0-FFFF"

    @property
    def is_local(self) -> bool:
        """True when the src uses a local() reference (no network fetch)."""
        return self.src.startswith("local(") if self.src else False

    @property
    def url(self) -> str:
        """Extract the URL from a url(...) src expression, or "" when
        the src is local() / empty."""
        if not self.src:
            return ""
        m = re.search(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", self.src)
        return m.group(1).strip() if m else ""


def parse_font_faces(css_text: str) -> list[FontFace]:
    """P34.1 — memoized @font-face extract."""
    cached = _font_faces_memo.get(css_text)
    if cached is not None:
        return cached
    out = _parse_font_faces_impl(css_text)
    if len(_font_faces_memo) < 256:
        _font_faces_memo[css_text] = out
    return out


def _parse_font_faces_impl(css_text: str) -> list[FontFace]:
    """P33.P3 — extract all @font-face blocks from *css_text*.

    The parser scans for ``@font-face`` followed by a declaration
    block.  Each block's declarations are parsed into a :class:`FontFace`.
    Unrecognized declarations are ignored.

    Returns a list of :class:`FontFace` objects in document order.
    """
    out: list[FontFace] = []
    i = 0
    n = len(css_text)
    while i < n:
        idx = css_text.find("@font-face", i)
        if idx < 0:
            break
        brace = css_text.find("{", idx)
        if brace < 0:
            break
        depth = 0
        j = brace
        while j < n:
            if css_text[j] == "{":
                depth += 1
            elif css_text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        inner = css_text[brace + 1: j] if j < n else ""
        family = ""
        src = ""
        weight = "400"
        style = "normal"
        unicode_range = "U+0-FFFF"
        for decl in inner.split(";"):
            if ":" not in decl:
                continue
            prop, _, val = decl.partition(":")
            prop = prop.strip().lower()
            val = val.strip()
            if prop == "font-family":
                family = val.strip("'\"")
            elif prop == "src":
                src = val
            elif prop == "font-weight":
                weight = val
            elif prop == "font-style":
                style = val
            elif prop == "unicode-range":
                unicode_range = val
        if family:
            out.append(FontFace(
                family=family, src=src, weight=weight,
                style=style, unicode_range=unicode_range,
            ))
        i = (j + 1) if j < n else n
    return out


# --- Property resolution ---------------------------------------------------------

def resolve_length(value: Value, parent_font_px: float,
                   ref_px: float | None = None) -> float | None:
    """P26.3 / P33.P1 — resolve a CSS Value to a pixel length.

    ``ref_px`` is the reference size for "%" terms (e.g. the containing
    block's content width).  When omitted, "%" resolves against
    ``parent_font_px`` (legacy behaviour).  P33.P1 — :class:`CalcExpr`
    (mixed ``%`` + ``px``) and :class:`VarExpr` (custom property
    reference) are resolved here.
    """
    if isinstance(value, Length):
        if value.unit == "px":
            return value.value
        if value.unit == "em":
            return value.value * parent_font_px
        if value.unit == "%":
            base = ref_px if ref_px is not None else parent_font_px
            return value.value * base / 100.0
        return value.value
    if isinstance(value, CalcExpr):
        # P33.P1 — mixed-unit calc (e.g. calc(100% - 20px)).
        # Each % term uses ref_px; px/em terms are absolute.
        base = ref_px if ref_px is not None else parent_font_px
        total = 0.0
        for tv, tu in value.terms:
            if tu == "%":
                total += tv * base / 100.0
            elif tu == "em":
                total += tv * parent_font_px
            else:
                total += tv  # px or unitless
        return total
    if isinstance(value, VarExpr):
        # P33.P1 — var(--name, fallback).  Resolution requires the node's
        # custom-property map; resolve_length is a module-level function
        # without node context, so we return None here.  The actual
        # resolution happens in StyledNode.prop_* via _resolve_var().
        return None
    if isinstance(value, Keyword):
        try:
            return float(value.value)
        except ValueError:
            return None
    return None


@dataclass
class EdgeList:
    """Shorthand edges: top/right/bottom/left (the 2-value form is vert/horiz)."""
    top: float
    right: float
    bottom: float
    left: float

    @classmethod
    def all(cls, v: float) -> "EdgeList":
        return cls(v, v, v, v)


def parse_shorthand(src: str, parent_font_px: float) -> "EdgeList | None":
    """Parse margin/padding/border-width shorthands into EdgeList.

    1 value -> all sides; 2 -> top/bottom, left/right; 3 -> top, horiz, bottom;
    4 -> top, right, bottom, left.
    """
    parts = [p.strip() for p in src.split() if p.strip()]
    if not parts:
        return None
    vals = []
    for p in parts:
        r = parse_value(p)
        n = resolve_length(r, parent_font_px) if r else None
        vals.append(n if n is not None else 0.0)
    if len(vals) == 1:
        return EdgeList.all(vals[0])
    if len(vals) == 2:
        return EdgeList(vals[0], vals[1], vals[0], vals[1])
    if len(vals) == 3:
        return EdgeList(vals[0], vals[1], vals[2], vals[1])
    return EdgeList(vals[0], vals[1], vals[2], vals[3])


# Property name normalisation (dashed CSS names used in rules/inline style).
EDGE_PROPS = {"margin", "padding", "border-width"}


def parse_declaration_pairs(src: str, parent_font_px: float = DEFAULT_FONT_PX) -> dict[str, Value]:
    """Parse 'color: red; font-size: 14px;' into a property map.

    P25.2 — supports ``!important`` suffix: ``color: red !important``
    wraps the value in :class:`ImportantDecl`."""
    out: dict[str, Value] = {}
    for decl in src.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not prop or not val:
            continue
        # P25.2 — detect `!important` suffix.
        important = False
        if val.lower().endswith("!important"):
            important = True
            val = val[: -len("!important")].strip()
        if prop in EDGE_PROPS:
            e = parse_shorthand(val, parent_font_px)
            if e is not None:
                out[prop] = ImportantDecl(e, prop, important) if important else e
            continue
        v = parse_value(val)
        out[prop] = ImportantDecl(v, prop, important) if important else v
    return out


# --- StyledNode -------------------------------------------------------------------

# P25.3 — CSS inheritance groups.
# Inheriting properties: value propagates to children automatically.
# Non-inheriting: children get the property's *initial value* unless
# set explicitly.
INHERITED_PROPS = {
    "color", "font-size", "font-family", "font-weight",
    "line-height", "text-align", "text-transform",
    "overflow-wrap", "letter-spacing", "word-spacing",
}
# P25.3 — initial values for non-inheriting properties.
INITIAL_VALUES: dict[str, Value] = {
    "margin": Length(0, "px"),
    "margin-top": Length(0, "px"), "margin-right": Length(0, "px"),
    "margin-bottom": Length(0, "px"), "margin-left": Length(0, "px"),
    "padding": Length(0, "px"),
    "padding-top": Length(0, "px"), "padding-right": Length(0, "px"),
    "padding-bottom": Length(0, "px"), "padding-left": Length(0, "px"),
    "border-width": Length(0, "px"),
    "border-style": Keyword("none"),
    "border-color": Keyword("currentcolor"),
    "display": Keyword("block"),
    "width": Keyword("auto"),
    "height": Keyword("auto"),
    "overflow": Keyword("visible"),
    "opacity": Keyword("1"),
    "position": Keyword("static"),
    "float": Keyword("none"),
    "clear": Keyword("none"),
    "z-index": Keyword("auto"),
    "text-indent": Length(0, "px"),
    "white-space": Keyword("normal"),
    "word-break": Keyword("normal"),
    "background": Keyword("none"),
}


def _resolve_cascade_value(prop: str, raw: Value,
                           parent_props: dict[str, Value],
                           parent_font_px: float) -> Value:
    """P25.3 — resolve inherit / initial / unset keywords on *raw*."""
    # If raw is a regular Keyword/Length/Color, return as-is.
    if isinstance(raw, ImportantDecl):
        inner = raw.value
    else:
        inner = raw
    if isinstance(inner, Keyword):
        v = inner.value.lower()
        if v == "inherit":
            # P25.3 — take the value from the parent's computed props.
            return parent_props.get(prop, INITIAL_VALUES.get(prop, Keyword(v)))
        if v == "initial":
            return INITIAL_VALUES.get(prop, Keyword("initial"))
        if v == "unset":
            # unset = inherit if inheriting, else initial.
            if prop in INHERITED_PROPS:
                return parent_props.get(prop, Keyword("unset"))
            return INITIAL_VALUES.get(prop, Keyword("unset"))
    return raw


# P25.4 — shorthand → longhand expansion table.
SHORTHAND_EXPANSION: dict[str, list[str]] = {
    "margin": ["margin-top", "margin-right", "margin-bottom", "margin-left"],
    "padding": ["padding-top", "padding-right", "padding-bottom", "padding-left"],
    "border-width": ["border-top-width", "border-right-width",
                     "border-bottom-width", "border-left-width"],
}
# P34.5 — frozenset of the shorthand keys so expand_shorthands() can
# exit in O(1) when none of them is present (the 10k-node benchmark
# has 21k StyledNodes and only a small fraction declare margin/padding
# shorthands; the legacy code paid 3 dict `in` checks per node
# regardless).  Built once at module load.
_SHORTHAND_KEYS = frozenset(SHORTHAND_EXPANSION)


def expand_shorthands(decls: dict[str, Value]) -> dict[str, Value]:
    """P25.4 — expand shorthand properties into their longhand components.

    ``margin: 10px`` → ``margin-top/right/bottom/left: 10px``.
    The original shorthand key is kept in the result (layout still reads
    it via ``sn.props.get("margin")``); the longhands are added alongside
    so that a child's ``inherit`` for ``margin-top`` finds a value.

    P34.5 — short-circuit when no shorthand key is present: a node with
    only color / font-size / background declarations (the 90 %+ case in
    a typical document) returns the input dict unchanged instead of
    copying it.  When a shorthand is present, the legacy copy + per-key
    expansion runs (a new dict is still returned, so the caller's input
    is never mutated).
    """
    if not (_SHORTHAND_KEYS & decls.keys()):
        return decls
    out = dict(decls)
    for sh, lh in SHORTHAND_EXPANSION.items():
        if sh in decls:
            val = decls[sh]
            for k in lh:
                out[k] = val
    return out


class StyledNode:
    def __init__(self, dom_node: Node, parent_font_px: float = DEFAULT_FONT_PX,
                 parent_height_px: float | None = None,
                 parent_width_px: float | None = None,
                 parent_custom_props: "dict | None" = None):
        self.dom = dom_node
        self.parent_font_px = parent_font_px
        # P26.3 — the containing block's resolved content height, used to
        # resolve percentage *heights* (a child's height: 50% is 50% of
        # the parent's content height, not of the font size).
        self.parent_height_px = parent_height_px
        # P33.P1 — containing block's resolved content *width*, used to
        # resolve percentage / calc-mixed %/px *widths*.
        self.parent_width_px = parent_width_px
        # P33.P1 — custom property (--*) inheritance chain.
        # parent_custom_props is the parent's map (immutable view);
        # custom_props is this node's own declarations.
        self.parent_custom_props = parent_custom_props if parent_custom_props is not None else {}
        self.custom_props: dict[str, Value] = {}
        self.props: dict[str, Value] = {}
        self.children: list["StyledNode"] = []

    # P33.P1 — custom property access (inheritance-aware).
    def get_custom_prop(self, name: str) -> Value | None:
        """P33.P1 — resolve a custom property (--name) with inheritance."""
        if name in self.custom_props:
            return self.custom_props[name]
        # Inherit from parent's merged map.
        return self.parent_custom_props.get(name)

    def _resolve_var(self, value: Value, seen: frozenset | None = None) -> Value | None:
        """P33.P1 — resolve var() references in *value*.

        Walks the node's custom property chain (own → inherited) to
        substitute :class:`VarExpr` nodes.  Cycles are detected via
        ``seen`` (frozenset of var names already in the expansion
        stack).  Falls back to the expression's own fallback value,
        or ``None`` when neither a custom property nor a fallback
        exists.
        """
        if not isinstance(value, VarExpr):
            return value
        if seen is None:
            seen = frozenset()
        if value.name in seen:
            # Cycle — use fallback if present, else give up.
            return self._resolve_var(value.fallback, seen) if value.fallback is not None else None
        new_seen = seen | {value.name}
        raw = self.get_custom_prop(value.name)
        if raw is not None:
            # Unwrap ImportantDecl wrappers stored as the raw value.
            if isinstance(raw, ImportantDecl):
                raw = raw.value
            # The stored value may itself be a var() — recurse.
            return self._resolve_var(raw, new_seen)
        if value.fallback is not None:
            return self._resolve_var(value.fallback, new_seen)
        return None

    def resolve_value(self, value: Value, ref_px: float | None = None) -> Value | None:
        """P33.P1 — fully resolve var()/calc() in *value* to a concrete
        Value (Length/Keyword/Color/CalcExpr), then return it.
        Callers that need a pixel length pass ``ref_px`` so that
        :class:`CalcExpr` can be evaluated inline."""
        v = self._resolve_var(value)
        if v is None:
            return None
        if isinstance(v, CalcExpr):
            # Evaluate the calc now that var() is resolved; keep mixed
            # %/px as CalcExpr for the caller's ref_px-aware resolution.
            return v
        return v

    # Convenience accessors
    def _resolve_with_var(self, v: Value, ref_px: float | None = None) -> float | None:
        """P33.P1 — resolve a Value that may contain var() / calc() to a
        pixel length, using ``ref_px`` for % terms when available."""
        rv = self._resolve_var(v)
        if rv is None:
            return None
        if ref_px is not None:
            r = resolve_length(rv, self.parent_font_px, ref_px=ref_px)
            if r is not None:
                return r
        return resolve_length(rv, self.parent_font_px)

    def prop_font_px(self) -> float:
        v = self.props.get("font-size")
        if v is not None:
            r = self._resolve_with_var(v)
            if r is not None:
                return r
        return self.parent_font_px

    def font_family(self) -> str:
        """P18 — CSS font-family (raw value, e.g. 'serif'/'monospace'/'arial')."""
        v = self.props.get("font-family")
        if v is not None:
            return str(v).strip()
        return "monospace"

    # P33.R5 — per-node font metrics cache: the line-height / char-advance
    # lookups below run 5000+ times per 1000-node layout; memoize the
    # resolved TTF face + line height on the node itself.
    _fm_cache: dict = {}

    def line_height_px(self) -> float:
        v = self.props.get("line-height")
        if v is not None:
            rv = self._resolve_var(v)
            r = resolve_length(rv, self.parent_font_px) if rv is not None else None
            if r:
                return r
        # P18 — when a real TTF font is registered, the natural line
        # height comes from the font's ascent/descent/lineGap metrics
        # (e.g. Arial 16px → ~18.4px), not from the legacy bitmap
        # scale (font_px).  This makes the half-leading model visible
        # even without an explicit line-height property.
        # P33.R5 — per-node memo: avoid re-resolving the TTF face on
        # every call (the font registry is process-stable).
        key = (self.parent_font_px, self.font_family())
        hit = self._fm_cache.get(key)
        if hit is not None:
            return hit
        try:
            real_fm = _css_font_mod.get_real_metrics(self.font_family())
            if real_fm is not None and real_fm.units_per_em > 0:
                lh = _css_text_mod.line_height_px(self.prop_font_px(), self.font_family())
            else:
                lh = self.prop_font_px()
        except Exception:
            lh = self.prop_font_px()
        self._fm_cache[key] = lh
        return lh

    def height_px(self) -> float | None:
        v = self.props.get("height")
        if v is None:
            return None
        # P33.P1 — resolve var() references first.
        rv = self._resolve_var(v)
        if rv is None:
            return None
        # P26.3 — percentage heights resolve against the containing block's
        # content height when known (parent_height_px is not None);
        # otherwise fall back to the parent font size (legacy behaviour).
        ref = self.parent_height_px if self.parent_height_px is not None else self.parent_font_px
        r = resolve_length(rv, self.parent_font_px, ref_px=ref)
        if r is not None:
            return r
        return resolve_length(rv, self.parent_font_px)

    def width_px(self) -> float | None:
        v = self.props.get("width")
        if v is None:
            return None
        # P33.P1 — resolve var() references first.
        rv = self._resolve_var(v)
        if rv is None:
            return None
        # P33.P1 — CalcExpr with % terms resolves against the containing
        # block's content width (parent_width_px) when known.
        ref = self.parent_width_px if self.parent_width_px is not None else self.parent_font_px
        r = resolve_length(rv, self.parent_font_px, ref_px=ref)
        if r is not None:
            return r
        return resolve_length(rv, self.parent_font_px)

    def is_display_none(self) -> bool:
        v = self.props.get("display")
        return isinstance(v, Keyword) and v.value.lower() == "none"

    def display(self) -> str:
        """Return the display keyword (e.g. 'block', 'flex', 'none')."""
        return self.get_keyword("display", "block")

    def overflow(self) -> str:
        return self.get_keyword("overflow", "visible")

    def opacity(self) -> float:
        v = self.props.get("opacity")
        if v is not None:
            r = resolve_length(v, self.parent_font_px)
            if r is not None:
                return max(0.0, min(1.0, r))
        return 1.0

    def text_align(self) -> str:
        v = self.props.get("text-align")
        if isinstance(v, Keyword):
            return v.value.lower()
        return "left"

    def color(self) -> Color:
        v = self.props.get("color")
        if v is not None:
            rv = self._resolve_var(v)
            if isinstance(rv, Color):
                return rv
        return Color(0, 0, 0)

    def background(self) -> Color | None:
        v = self.props.get("background")
        if v is not None:
            rv = self._resolve_var(v)
            if isinstance(rv, Color):
                return rv
        return None

    def get_number(self, prop: str, default: float) -> float:
        v = self.props.get(prop)
        if v is None:
            return default
        rv = self._resolve_var(v)
        r = resolve_length(rv, self.parent_font_px) if rv is not None else None
        return r if r is not None else default

    def get_color(self, prop: str, default: Color) -> Color:
        v = self.props.get(prop)
        if v is not None:
            rv = self._resolve_var(v)
            if isinstance(rv, Color):
                return rv
        return default

    def get_keyword(self, prop: str, default: str) -> str:
        v = self.props.get(prop)
        if isinstance(v, Keyword):
            return v.value
        return default

    def placeholder_caption(self, cap_dom: Node) -> "StyledNode":
        """Build a synthetic styled node (used for iframe caption text)."""
        sn = StyledNode(cap_dom, self.parent_font_px)
        # Inherit font size and color from the parent.
        sn.props["font-size"] = self.props.get("font-size")
        sn.props["color"] = self.props.get("color")
        return sn


def apply(
    dom_root: Node,
    css_text: str,
    interaction_state: dict | None = None,
    media_rules: list[MediaQuery] | None = None,
    viewport_w: float = 800.0,
    viewport_h: float = 600.0,
    supports_rules: list[SupportsRule] | None = None,
) -> StyledNode:
    """Build the styled tree. Returns the styled root (synthetic document).

    ``interaction_state`` (P8.1) is an optional dict with keys ``"hover"``
    and ``"focus"`` holding the currently hovered/focused element id.
    When provided, ``:hover`` / ``:focus`` pseudo-class rules are matched
    against it; otherwise pseudo-class rules are skipped.

    ``media_rules`` (P9.1) is an optional list of ``MediaQuery`` objects.
    When provided, inner rules of media queries whose condition matches the
    current viewport size are appended to the rule list before cascading.

    ``supports_rules`` (P11.3) is an optional list of ``SupportsRule`` objects.
    In this toy engine every @supports block is treated as supported, so all
    inner rules are appended to the cascade.
    """
    # P34.1 — parse into a *caller-owned* list: apply() extends it with
    # @media / @supports rules, so the shared rulesheet memo (which
    # stores the untouched author-CSS list) must not be mutated here.
    rules = parse_rulesheet_nocache(css_text)
    # P9.1 — append @media rules that match the current viewport.
    if media_rules:
        for mq in media_rules:
            if media_matches(mq.condition, viewport_w, viewport_h):
                rules.extend(mq.rules)
    # P11.3 — append @supports rules (toy: always supported).
    if supports_rules:
        for sr in supports_rules:
            if supports_matches(sr.condition):
                rules.extend(sr.rules)
    # P34.1 — per-tag rule index: pre-compute which rules may match each
    # tag so _apply_subtree skips the O(rules) walk when a node's tag
    # is not referenced.  See _build_tag_rule_index.
    tag_rules = _build_tag_rule_index(rules)
    # P34.5 — the candidate-rule cache is scoped to one apply() call:
    # selector match results depend on the ruleset, so a different
    # document / stylesheet must never inherit a stale match list.
    # clear_caches() is called *before* the _apply_subtree walk so
    # the cache fills for this apply and is dropped for the next.
    # P34.5 — ``id`` is part of the identity key *only when the ruleset
    # contains an ``#id`` selector*: an id-keyed cache sees 100 %
    # miss rate on a document whose elements all carry unique ids
    # (~9600 distinct ids in the 10k-node benchmark), so tag/class
    # identity alone is the right granularity for the common
    # tag/class-driven ruleset; when "#id" rules are present, the
    # first node of each (tag, class, id) tuple gets its own cache
    # entry so id-only rules still cascade to the right node.
    # Interaction state (hover/focus id) is always part of the key —
    # :hover/:focus rules match a specific node, not its siblings.
    global _identity_includes_id, _identity_ancestor_sensitive
    _identity_includes_id = any(
        part.startswith("#") for r in rules for s in r.selectors for part in s.parts
    )
    # P34.5 — a compound selector (multi-part: "div p", "div > p")
    # matches on the ancestor chain, not on the node's own identity.
    # Detect it once here; when present the candidate-rule cache is
    # bypassed in _apply_subtree (the legacy per-node walk is correct
    # for all rules; the cache would silently produce wrong matches
    # for the compound ones).  Simple single-part selectors (tag /
    # .class / #id) remain cached.
    _identity_ancestor_sensitive = any(
        len(s.parts) > 1 for r in rules for s in r.selectors
    )
    clear_caches()
    root_sn = StyledNode(dom_root)
    # P33.P1 — extract custom properties (--*) declared on :root / html.
    _collect_custom_props(dom_root, root_sn)
    root_sn.children = [
        _apply_subtree(c, rules, interaction_state,
                       parent_custom_props=root_sn.custom_props,
                       tag_rules=tag_rules)
        for c in dom_root.children
    ]
    return root_sn


def _collect_custom_props(node: Node, sn: StyledNode) -> None:
    """P33.P1 — pull --custom properties from inline style="" and
    cascade them into ``sn.custom_props``.

    Walks the node's own ``style=""`` attribute; custom properties set
    in rulesheets are not yet parsed (toy limitation).
    """
    inline = node.attr("style")
    if not inline:
        return
    for decl in inline.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if prop.startswith("--") and val:
            sn.custom_props[prop] = parse_value(val)


def _unimportant(v: Value) -> Value:
    """P25.2 — unwrap an ImportantDecl; return the raw value otherwise."""
    if isinstance(v, ImportantDecl):
        return v.value
    return v


def _cascade_key(r: Rule) -> tuple:
    """P25.1/2 — sort key: source (author > ua), !important flag, 4-tuple
    specificity (summed), rule order (stable)."""
    spec_sum = sum(r.specificity)
    return (r.source == "author", r.important, spec_sum)


def _build_tag_rule_index(rules: list[Rule]) -> dict[str, list[int]]:
    """P34.1 — index rules by the tag each *rightmost* simple part may
    match, so ``_apply_subtree`` can fetch candidate rules in O(1)
    instead of scanning the whole rule list per node.

    Returns ``{tag: [rule indices]}``; rules whose rightmost part has no
    tag constraint (``.class`` / ``#id`` / ``.a.b``) or an interaction
    pseudo (``:hover`` / ``:focus``) are registered under the special
    key ``"*"`` which every node is checked against (cheap, constant
    per node, and empty for most pages that have no such rules).
    """
    index: dict[str, list[int]] = {}
    for i, r in enumerate(rules):
        for s in r.selectors:
            if s.pseudo in (":hover", ":focus"):
                index.setdefault("*", []).append(i)
                continue
            parts = s.parts
            if not parts:
                index.setdefault("*", []).append(i)
                continue
            last = parts[-1].strip()
            tag_part = last.split(".", 1)[0].split("#", 1)[0]
            if tag_part:
                index.setdefault(tag_part, []).append(i)
            else:
                index.setdefault("*", []).append(i)
    # Deduplicate (a rule with two selectors may register under two tags).
    for tag, lst in index.items():
        index[tag] = sorted(set(lst))
    return index


def _apply_subtree(node: Node, rules: list[Rule], interaction_state: dict | None = None,
                   parent_props: dict[str, Value] | None = None,
                   parent_custom_props: dict[str, Value] | None = None,
                   tag_rules: dict[str, list[int]] | None = None) -> StyledNode:
    parent_font = DEFAULT_FONT_PX
    sn = StyledNode(node, parent_font,
                    parent_custom_props=parent_custom_props)
    # P34.1 — O(1) candidate lookup via the per-tag rule index (built
    # once by ``_build_tag_rule_index``); the legacy O(len(rules)) scan
    # is used when no index is supplied.
    # P34.5 — cache the per-node candidate-rule *identity* instead of
    # re-running ``s.matches()`` over every candidate selector for the
    # 21k+ nodes of a 10k-node document: most nodes in a page share
    # exactly the same (tag, id, class, pseudo-interaction) identity,
    # so the match result for one node applies verbatim to all others
    # with the same identity.  The cache is keyed on the identity
    # tuple and bounded at 4096 entries; the legacy per-node walk runs
    # only when the cache is full *or* when the node has no candidates
    # to begin with.
    if tag_rules is not None:
        tag = (node.tag or "").lower() if node.is_element() else ""
        cand_idxs = list(tag_rules.get(tag, ()))
        wildcard_idxs = tag_rules.get("*")
        # P34.5 — the candidate-rule cache is correct only when every
        # matching rule's result is a pure function of the node's own
        # (tag, class, [id]) identity.  A compound selector
        # (descendant / child combinator, e.g. "div p" / "div > p")
        # matches on the *ancestor chain*, so two nodes with the same
        # identity can match different rule sets; in that case the
        # legacy per-node walk must run for every node (correctness
        # first).  Interaction state (hover/focus id) is always part
        # of the key — :hover/:focus rules match a specific node, not
        # its siblings.
        if not _identity_ancestor_sensitive:
            class_attr = node.attr("class") or ""
            hover_id = (interaction_state or {}).get("hover", "")
            focus_id = (interaction_state or {}).get("focus", "")
            # P34.5 — include the element id in the key only when the
            # ruleset has "#id" selectors; otherwise the id is
            # irrelevant to matching and would 100 %-miss a cache on a
            # document with unique ids.
            if _identity_includes_id:
                id_attr = node.attr("id")
                ident_key = (tag, class_attr, id_attr, hover_id, focus_id)
            else:
                ident_key = (tag, class_attr, hover_id, focus_id)
            cached = _candidate_rules_cache.get(ident_key)
            if cached is not None:
                matching = list(cached)  # a fresh list; matches are shared
            else:
                if wildcard_idxs:
                    cand_idxs.extend(wildcard_idxs)
                if not cand_idxs:
                    matching: list[Rule] = []
                else:
                    cand_idxs = sorted(set(cand_idxs))
                    matching = [rules[i] for i in cand_idxs
                                if any(s.matches(node, interaction_state) for s in rules[i].selectors)]
                if len(_candidate_rules_cache) < _CANDIDATE_RULES_CACHE_MAX:
                    _candidate_rules_cache[ident_key] = matching
        else:
            # P34.5 — compound selectors present: the match result
            # depends on the ancestor chain, so the node-identity
            # cache is bypassed and the legacy per-node walk runs.
            if wildcard_idxs:
                cand_idxs.extend(wildcard_idxs)
            if not cand_idxs:
                matching: list[Rule] = []
            else:
                cand_idxs = sorted(set(cand_idxs))
                matching = [rules[i] for i in cand_idxs
                            if any(s.matches(node, interaction_state) for s in rules[i].selectors)]
    else:
        # P25 — Cascade: collect matching rules, sort by (source, important,
        # specificity, order).  Later rules with equal key win (CSS source
        # order rule).
        matching = [
            r for r in rules
            if any(s.matches(node, interaction_state) for s in r.selectors)
        ]
    matching.sort(key=_cascade_key)
    # P25.2 — apply in two passes:
    #   1. normal declarations (sorted by cascade key; later wins on ties).
    #   2. !important declarations (they always beat normal regardless of
    #      source/specificity; within the important layer, author beats UA).
    # The inline pass is split so that:
    #   - inline normal declarations are applied *before* author !important
    #     (so author !important can overwrite them), but
    #   - inline !important declarations are applied *after* author !important
    #     (so inline !important wins within the important layer).
    # Pass 1 — normal (author rules + inline normal, in source order).
    for r in matching:
        for k, v in r.declarations.items():
            if isinstance(v, ImportantDecl) and v.important:
                continue
            sn.props[k] = _unimportant(v)
    # Pass 1.5 — inline *normal* declarations (override author normal,
    # but are still below author !important).
    # P34.5 — the legacy code called node.attr("style") *twice* per
    # node (once here, once for the custom-property pass below); a
    # 10k-node document paid 43k+ attr lookups for the same string.
    # Read it once and reuse for both passes.
    inline = node.attr("style")
    inline_normal: dict[str, Value] = {}
    inline_important: dict[str, Value] = {}
    if inline:
        inline_props = parse_declaration_pairs(inline, parent_font)
        for k, v in inline_props.items():
            if isinstance(v, ImportantDecl) and v.important:
                inline_important[k] = _unimportant(v)
            else:
                inline_normal[k] = _unimportant(v)
        for k, v in inline_normal.items():
            sn.props[k] = v
    # Pass 2 — author !important declarations.
    for r in matching:
        for k, v in r.declarations.items():
            if isinstance(v, ImportantDecl) and v.important:
                sn.props[k] = _unimportant(v)
    # Pass 3 — inline !important declarations (top of the important layer).
    for k, v in inline_important.items():
        sn.props[k] = v
    # P33.P1 — custom property declarations (--*) from inline style.
    # These must NOT go into ``sn.props`` (the layout engine's property
    # read path doesn't know about them); they go into
    # ``sn.custom_props`` for ``_resolve_var`` lookups.
    # P34.5 — reuse the ``inline`` string read once above (the legacy
    # code called node.attr("style") a second time here for the same
    # value).
    inline_cp = inline
    if inline_cp:
        for decl in inline_cp.split(";"):
            if ":" not in decl:
                continue
            cp, _, cv = decl.partition(":")
            cp = cp.strip().lower()
            cv = cv.strip()
            if cp.startswith("--") and cv:
                sn.custom_props[cp] = parse_value(cv)
    # P25.3 — resolve inherit/initial/unset keywords.
    pp = parent_props or {}
    for k, v in list(sn.props.items()):
        resolved = _resolve_cascade_value(k, v, pp, parent_font)
        if resolved is not v:
            sn.props[k] = resolved
    # P25.4 — expand shorthands into longhands.
    sn.props = expand_shorthands(sn.props)
    # P33.P1 — merged custom property map for children (own + inherited).
    merged_cp = dict(sn.parent_custom_props)
    merged_cp.update(sn.custom_props)
    # Compute the font this node gives to its children.
    child_font = sn.prop_font_px()
    # P25.5 — em/% computed-value chain: resolve the child's parent font.
    # The child's parent_font_px is the *current* node's resolved font-size,
    # which is what em/% reference in the child's context.
    inherited_color = sn.props.get("color", Color(0, 0, 0))
    # P34.6 — pre-compute which of the 8 inheritable properties the
    # parent actually carries.  The legacy loop did 8 individual dict
    # checks per child (21k children × 8 = 170k lookups on a
    # 10k-node document) even when the parent carried none of them;
    # the candidate dict holds only the keys that are present, so
    # the per-child loop typically iterates 0-2 entries instead of 8.
    _inherit_candidates: dict = {}
    for _k in ("line-height", "text-align", "text-transform",
               "font-family", "overflow-wrap", "font-weight",
               "letter-spacing", "word-spacing"):
        _v = sn.props.get(_k)
        if _v is not None:
            _inherit_candidates[_k] = _v
    for c in node.children:
        sub = _apply_subtree(c, rules, interaction_state, sn.props,
                             parent_custom_props=merged_cp,
                             tag_rules=tag_rules)
        sub.parent_font_px = child_font
        # P25.3 — inherit font-size when not explicitly set.
        if "font-size" not in sub.props:
            sub.props["font-size"] = Length(child_font, "px")
        # Inherit color ONLY when the child neither matched a rule that set
        # color nor has an inline color.  The set of colors the child
        # *actually* carries comes from its own rule/inline matches; we use
        # the parent's color as the default.
        if "color" not in sub.props:
            sub.props["color"] = inherited_color
        # P18/P19/P25.3 — inherit the 8 text properties from the parent
        # P34.6 — iterate only the keys the parent actually carries
        # (the pre-computed _inherit_candidates dict) instead of the
        # legacy 8-way per-child dict checks.  Child-owned values
        # still win (in sub.props unchanged), preserving behavior.
        _sub_props = sub.props
        for _k, _v in _inherit_candidates.items():
            if _k not in _sub_props:
                _sub_props[_k] = _v
        sn.children.append(sub)
    return sn

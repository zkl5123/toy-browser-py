"""Stage 2 — CSS parsing, selectors, cascade, and styled-tree build.

Pure standard library. Supported:
- type / class / id / descendant selectors
- inline style="" attributes
- specificity cascade (id > class > type)
- properties: color, background, font-size, line-height, display,
  width, height, margin, padding, border-width, border-color, text-align
"""
from __future__ import annotations

from dataclasses import dataclass, field
from dom import Node
from paint import Color

DEFAULT_FONT_PX = 16.0


# --- Value types -----------------------------------------------------------

@dataclass
class Length:
    value: float
    unit: str  # "px" | "em"


@dataclass
class Keyword:
    value: str


# Value = Length | Keyword | Color
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
    """Parse 'color: red; font-size: 14px;' into a property map."""
    out: dict[str, Value] = {}
    for decl in src.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not prop or not val:
            continue
        out[prop] = parse_value(val)
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
    # P12.3 — calc() toy: evaluate simple binary expressions like
    # "calc(100px - 20px)" or "calc(10px + 5%)".
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


def parse_calc(expr: str) -> Value:
    """P12.3 — evaluate a simple calc() expression.

    Supports binary ``+`` / ``-`` between two operands, each being a
    number with optional unit (``px``, ``em``, ``%``, ``pt``) or a bare
    number.  Returns a :class:`Length` (or unitless :class:`Keyword`).
    Unknown shapes fall back to a raw :class:`Keyword`.
    """
    import re as _re
    expr = expr.strip()
    m = _re.fullmatch(
        r"([\d.]+)(\w+)\s*([+-])\s*([\d.]+)(\w*)",
        expr,
    )
    if m:
        a, ua, op, b, ub = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        va, vb = float(a), float(b)
        # P12.3 — supported units: px, em, %, pt, and none.
        supported = {"px", "em", "%", "pt"}
        if ua not in supported and ua:
            return Keyword(f"calc({expr})")
        if ub not in supported and ub:
            return Keyword(f"calc({expr})")
        if ua and ub and ua != ub:
            # Mismatched units — toy: convert em to px (1em = 16px).
            if ua == "em":
                va = va * 16.0
                ua = "px"
            if ub == "em":
                vb = vb * 16.0
                ub = "px"
            if ua != ub:
                # P12.3 — unsupported unit mix: carry the raw calc() text so
                # downstream code can recognize it (toy fallback).
                return Keyword(f"calc({expr})")
        if op == "-":
            result = va - vb
        else:
            result = va + vb
        if ua or ub:
            return Length(result, ua or ub)
        return Keyword(str(result))
    # Bare number.
    try:
        return Length(float(expr), "px") if "." in expr else Keyword(str(float(expr)))
    except ValueError:
        return Keyword(f"calc({expr})")


# --- Selectors --------------------------------------------------------------

@dataclass(frozen=True)
class Selector:
    """A compound selector.

    ``parts`` holds the simple-selector strings from leftmost to rightmost
    (e.g. ``("div", "p")`` for ``div p``, ``(".a", "b")`` for ``.a > b``).
    ``combinator`` is ``" "`` (descendant, default) or ``">"`` (child).
    ``specificity`` is a ``(id, cls, type)`` tuple.
    ``pseudo`` is an optional pseudo-class (":hover", ":focus") for the
    rightmost part.  Pseudo-classes are matched against the current
    interaction state, not the DOM tree.
    """
    parts: tuple[str, ...]
    combinator: str
    specificity: tuple[int, int, int]
    pseudo: str = ""  # ":hover" / ":focus" / ""

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
        simple = simple.strip()
        if simple.startswith("#"):
            return node.attr("id") == simple[1:]
        if simple.startswith("."):
            body = simple[1:]
            # Multi-class: ".a.b" → both classes required.
            if "." in body:
                classes = [c for c in body.split(".") if c]
                if not classes:
                    return False
                return all(node.has_class(c) for c in classes)
            return node.has_class(body)
        # Type selector (tag name).
        return node.tag_name() == simple

    # --- P10.1 — child-position pseudo-class helpers -------------------------

    @staticmethod
    def _child_index(node: Node) -> int | None:
        """Return the 1-based child position of ``node`` among its
        element-sibling parents, or ``None`` if the parent has no
        element children (top-level document node)."""
        parent = node.parent
        if parent is None:
            return None
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
        import re as _re

        inner = pseudo[len(":nth-child("):-1].strip()
        if inner == "even":
            return idx % 2 == 0
        if inner == "odd":
            return idx % 2 == 1
        m = _re.match(r"([+-]?\d*)n\s*([+-]\s*\d+)?\s*$", inner)
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

        # Compute specificity: (ids, classes, types).
        spec_a = spec_b = spec_c = 0
        for p in parts:
            p = p.strip()
            if p.startswith("#"):
                spec_a += 1
            elif p.startswith("."):
                # Count classes: ".a" → 1, ".a.b" → 2, ".a.b.c" → 3
                body = p[1:]
                cls_count = body.count(".") + 1
                spec_b += cls_count
            else:
                spec_c += 1
        # P8.1 / P10.1 — any pseudo-class adds one class-level point.
        if pseudo:
            spec_b += 1

        return cls(tuple(parts), combinator, (spec_a, spec_b, spec_c), pseudo)

    def specificity_sum(self) -> tuple[int, int, int]:
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


def parse_keyframes(css_text: str) -> dict[str, Animation]:
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
    specificity: tuple[int, int, int]


def parse_rulesheet(css_text: str) -> list[Rule]:
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
        max_spec = max((s.specificity_sum() for s in sels), key=sum, default=(0, 0, 0))
        rules.append(Rule(sels, parse_declaration_pairs(decl_src), max_spec))
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
    """Extract all @media blocks from CSS text.

    Returns a list of ``MediaQuery`` objects.  Each block's inner rules are
    parsed with the standard ``parse_rulesheet``.  The condition is the raw
    text between ``@media`` and the opening brace.

    Only ``min-width`` / ``max-width`` / ``min-height`` / ``max-height``
    are supported; unknown conditions are stored verbatim for future use.
    """
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


# --- Property resolution ---------------------------------------------------------

def resolve_length(value: Value, parent_font_px: float) -> float | None:
    if isinstance(value, Length):
        if value.unit == "px":
            return value.value
        if value.unit == "em":
            return value.value * parent_font_px
        if value.unit == "%":
            return value.value * parent_font_px  # toy: % relative to font
        return value.value
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
    """Parse 'color: red; font-size: 14px;' into a property map."""
    out: dict[str, Value] = {}
    for decl in src.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        val = val.strip()
        if not prop or not val:
            continue
        if prop in EDGE_PROPS:
            e = parse_shorthand(val, parent_font_px)
            if e is not None:
                out[prop] = e
            continue
        out[prop] = parse_value(val)
    return out


# --- StyledNode -------------------------------------------------------------------

INHERITED_PROPS = {"color", "font-size", "font-family", "line-height", "text-align", "text-transform", "overflow-wrap"}


class StyledNode:
    def __init__(self, dom_node: Node, parent_font_px: float = DEFAULT_FONT_PX):
        self.dom = dom_node
        self.parent_font_px = parent_font_px
        self.props: dict[str, Value] = {}
        self.children: list[StyledNode] = []

    # Convenience accessors
    def prop_font_px(self) -> float:
        v = self.props.get("font-size")
        return resolve_length(v, self.parent_font_px) if v else self.parent_font_px

    def font_family(self) -> str:
        """P18 — CSS font-family (raw value, e.g. 'serif'/'monospace'/'arial')."""
        v = self.props.get("font-family")
        if v is not None:
            return str(v).strip()
        return "monospace"

    def line_height_px(self) -> float:
        v = self.props.get("line-height")
        if v is not None:
            r = resolve_length(v, self.parent_font_px)
            if r:
                return r
        # P18 — when a real TTF font is registered, the natural line
        # height comes from the font's ascent/descent/lineGap metrics
        # (e.g. Arial 16px → ~18.4px), not from the legacy bitmap
        # scale (font_px).  This makes the half-leading model visible
        # even without an explicit line-height property.
        try:
            import font as _fmod
            import text as _tmod
            real_fm = _fmod.get_real_metrics(self.font_family())
            if real_fm is not None and real_fm.units_per_em > 0:
                return _tmod.line_height_px(self.prop_font_px(), self.font_family())
        except Exception:
            pass
        return self.prop_font_px()

    def height_px(self) -> float | None:
        v = self.props.get("height")
        if v is None:
            return None
        return resolve_length(v, self.parent_font_px)

    def width_px(self) -> float | None:
        v = self.props.get("width")
        if v is None:
            return None
        return resolve_length(v, self.parent_font_px)

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
        if isinstance(v, Color):
            return v
        return Color(0, 0, 0)

    def background(self) -> Color | None:
        v = self.props.get("background")
        if isinstance(v, Color):
            return v
        return None

    def get_number(self, prop: str, default: float) -> float:
        v = self.props.get(prop)
        if v is None:
            return default
        r = resolve_length(v, self.parent_font_px)
        return r if r is not None else default

    def get_color(self, prop: str, default: Color) -> Color:
        v = self.props.get(prop)
        if isinstance(v, Color):
            return v
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
    rules = parse_rulesheet(css_text)
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
    root_sn = StyledNode(dom_root)
    root_sn.children = [
        _apply_subtree(c, rules, interaction_state) for c in dom_root.children
    ]
    return root_sn


def _apply_subtree(node: Node, rules: list[Rule], interaction_state: dict | None = None) -> StyledNode:
    parent_font = DEFAULT_FONT_PX
    sn = StyledNode(node, parent_font)
    # Cascade: collect matching rules.
    matching: list[Rule] = [
        r for r in rules
        if any(s.matches(node, interaction_state) for s in r.selectors)
    ]
    matching.sort(key=lambda r: sum(r.specificity))
    for r in matching:
        for k, v in r.declarations.items():
            sn.props[k] = v
    # Inline style="" beats everything.
    inline = node.attr("style")
    if inline:
        inline_props = parse_declaration_pairs(inline, parent_font)
        for k, v in inline_props.items():
            sn.props[k] = v
    # Compute the font this node gives to its children.
    child_font = sn.prop_font_px()
    # The color this node passes to children (its own resolved color).
    inherited_color = sn.props.get("color", Color(0, 0, 0))
    for c in node.children:
        sub = _apply_subtree(c, rules, interaction_state)
        sub.parent_font_px = child_font
        # Inherit font-size when not explicitly set.
        if "font-size" not in sub.props:
            sub.props["font-size"] = Length(child_font, "px")
        # Inherit color ONLY when the child neither matched a rule that set
        # color nor has an inline color.  The set of colors the child
        # *actually* carries comes from its own rule/inline matches; we use
        # the parent's color as the default.
        if "color" not in sub.props:
            sub.props["color"] = inherited_color
        # P18/P19 — inherit line-height / text-align / text-transform /
        # font-family / overflow-wrap from parent when not explicitly set.
        for inherited in ("line-height", "text-align", "text-transform",
                          "font-family", "overflow-wrap"):
            if inherited not in sub.props and inherited in sn.props:
                sub.props[inherited] = sn.props[inherited]
        sn.children.append(sub)
    return sn

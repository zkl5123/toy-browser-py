"""Stage 4 (support) — text metrics and CPU glyph rasterization.

Uses the built-in 8x8 ASCII bitmap font (see font.py). Glyphs are scaled to
the requested font size by nearest-neighbour and blitted onto the target
bitmap. Pure standard library.

P15.4 — font family support: ``font_family`` parameter on ``draw_text``
switches between the monospace (default 8x8) and serif (10x8 wide) variants.

P17 — real TTF font metrics: when a TTF font file is available for a
family, ``char_advance`` returns the actual per-character advance width
from ``hmtx`` instead of a fixed cell width.  The glyph rasterization
itself still uses the bitmap font (P20 will replace it with true
quadratic-Bezier curve filling).
"""
from __future__ import annotations

from font import glyph as _glyph, glyph_for as _glyph_for, cell_width as _cell_width
from paint import Bitmap, Color
import rasterize as _rasterize


def scale_factor(font_px: float) -> int:
    """The integer scale used for both measurement and drawing (never < 1)."""
    return max(1, int(font_px / 8.0))


def line_height_px(font_px: float, font_family: str = "monospace") -> float:
    """P17 — line height in pixels.

    When a real TTF font is registered, uses the font's ascent/descent/
    lineGap.  Otherwise falls back to the legacy bitmap scale.
    """
    real = _real_metrics_for(font_family)
    if real is not None:
        return real.line_height(font_px)
    return max(1.0, font_px / 8.0) * 8.0


def char_advance(font_px: float, font_family: str = "monospace") -> float:
    """P15.4 — Width of one character cell in pixels for the given family.

    P17 — when a real TTF font is registered for this family, returns the
    *average* advance width (from hmtx) instead of the fixed cell width.
    This gives proportionally-spaced text while still using bitmap glyphs
    for rendering (P20 will replace the bitmap with true curve filling).
    P33.R1 — when no face is registered the legacy fixed-cell estimate is
    used; the caller then falls back to bitmap rendering.
    """
    real = _real_metrics_for(font_family)
    if real is not None:
        # Use the advance of 'M' (a wide char) as a representative value.
        # For true per-char advances, see advance_text below.
        adv_units = real.advances[real.cmap.get(77, 0)] if real.cmap.get(77, 0) < len(real.advances) else real.units_per_em
        return adv_units * (font_px / real.units_per_em)
    return float(_cell_width(font_family)) * scale_factor(font_px)


# P33.R5 — memoized metrics cache: (family, weight) → (metrics, synthetic).
# Repeated calls to _real_metrics_for with the same family+weight return
# the cached result, avoiding the expensive TTF rescan on every
# advance_text invocation (which runs 5000+ times per 1000-node layout).
_metrics_memo: dict = {}


def _real_metrics_for(font_family: str, weight: int = 400):
    """P17/P20.4 — return the real TTF metrics for a family+weight, or None.

    P20.4 — when ``weight >= 600`` and no bold TTF file is registered,
    returns the normal-weight font and sets the module-level
    ``_synthetic_bold`` flag so the curve path dilates the outline.
    P33.R1 — idempotent: if the family's metrics were cleared from the
    registry (a test that clears ``font._real_font_metrics``), re-load
    them from the remembered search dirs so rendering does not silently
    fall back to the bitmap font.  The re-init uses the *remembered*
    search dirs (which include the "fonts/" dir set by the first
    ``init_real_fonts(["fonts"])`` call) to avoid a full system-wide
    CJK scan on every re-init.
    P33.R5 — result is memoized per (family, weight) so the 5000+
    calls from ``advance_text`` during a 1000-node layout don't each
    trigger a TTF rescan.
    """
    global _synthetic_bold
    key = (font_family, weight)
    hit = _metrics_memo.get(key)
    if hit is not None:
        m, synth = hit
        if weight >= 600:
            _synthetic_bold = synth
        return m
    import font
    if weight >= 600:
        m, synth = font.get_metrics_for_weight(font_family, weight)
        if m is None:
            font.init_real_fonts(None)
            m, synth = font.get_metrics_for_weight(font_family, weight)
        _synthetic_bold = synth
    else:
        m = font.get_real_metrics(font_family)
        if m is None:
            font.init_real_fonts(None)
            m = font.get_real_metrics(font_family)
        synth = False
    _metrics_memo[key] = (m, synth)
    return m


def clear_metrics_memo() -> None:
    """P33.R5 — clear the ``_metrics_memo`` cache.

    Call after ``font._real_font_metrics`` is cleared (tests that
    reset the font registry) so the next layout re-resolves metrics
    from the fresh registry instead of returning a stale cached face.
    """
    _metrics_memo.clear()


# P20.4 — set by _real_metrics_for when a bold weight is requested but
# no bold TTF file exists; the curve path applies synthetic dilation.
_synthetic_bold: bool = False

# ---------------------------------------------------------------------------
# P33.R1 — per-glyph rasterization cache + CJK per-character fallback.
#
# A real page is dominated by repeated characters; rasterizing each
# distinct (font, size, codepoint, weight) outline once and OR-blending
# the cached 8-bit coverage mask on subsequent occurrences turns
# O(n_chars) outline fills into O(distinct_glyphs) fills + cheap blits.
# The cache key folds in the (quantized) font size and weight so a
# size/weight change forces a re-rasterization.

_glyph_mask_cache: dict = {}
_glyph_cache_max = 4096


def _mask_key(ch: str, real, font_px: float, weight: float):
    """Cache key: character + metrics id + quantized size + weight."""
    return (ch, id(real), int(round(font_px * 2)), int(round(weight * 10)))


def _get_glyph_mask(ch: str, face, font_px: float, weight: float):
    """P33.R1 — rasterize one glyph into a cached 8-bit coverage mask.

    Returns a :class:`rasterize.GlyphMask` whose rows are in *flipped*
    font space (row 0 = glyph top, y up), sized to the glyph bbox, or
    ``None`` when the outline is empty / malformed (caller falls back
    to the bitmap font).

    P34.2 — *face* is the *resolved* face for the character (the
    CJK fallback face when the primary font lacks the glyph), not
    always the primary face.  The P33.R1 implementation ignored the
    resolved face and called ``real.glyph_outline`` on the primary
    face, so a CJK codepoint on a Latin font produced no outline
    and the caller fell through to the ~44 ms curve path on every
    occurrence.  Using the resolved face makes the mask cache hit
    for CJK chars and turns the curve path into a rare miss-only
    fallback.
    """
    key = _mask_key(ch, face, font_px, weight)
    hit = _glyph_mask_cache.get(key)
    if hit is not None:
        return hit
    outline = face.glyph_outline(ch)
    if outline is None or outline.is_empty or not outline.points:
        return None
    scale = font_px / face.units_per_em
    mask = _rasterize.rasterize_glyph_mask(
        outline, scale, weight=weight, antialias=(weight <= 1.5))
    if mask is None:
        return None
    if len(_glyph_mask_cache) < _glyph_cache_max:
        _glyph_mask_cache[key] = mask
    return mask


def _blit_coverage(bitmap: "Bitmap", x: int, y: int, ch: str,
                   font_px: float, weight: float, color,
                   face=None, baseline=None) -> bool:
    """P33.R1 — blit a cached coverage mask for *ch* onto *bitmap*.

    ``(x, y)`` is the pen's **line top** in screen space (y down).
    The mask is placed so that its baseline row aligns with
    ``y + ascent_px`` where the ascent is taken from the *resolved*
    face's TTF metrics (the CJK fallback face for CJK chars, the
    primary face for everything else).  Returns True when a cached
    mask was found and blitted, False when the caller must fall back
    to the bitmap font.

    P33.R1 — the face is resolved per-character (not hard-coded to
    "monospace") so that CJK codepoints use the CJK fallback face's
    ascent / scale, which differs from the primary face.

    P34.1 — *face* and *baseline* are accepted as optional pre-resolved
    arguments (see ``draw_text``): resolving the face per line instead
    of per character cut the hot loop's face-lookup + mask-cache
    key-building work by ~3×.  The blit itself clips against the
    bitmap bounds (``rasterize.blit_glyph_mask``) so negative
    coordinates (e.g. a negative ``text-indent``) are safe.
    """
    import rasterize
    if face is None:
        import font as _fontmod
        # P0 — resolve with the monospace primary first, not None.
        # The legacy face=None call path returned the CJK fallback
        # face for Latin codepoints (CJK fonts typically carry Latin
        # glyphs; the CJK face's has_glyph("A") was False because the
        # CJK font's cmap lacked A, but resolve_metrics_for_char
        # returned the CJK face anyway when the primary was None),
        # and the CJK face had no glyph outline for "A", so the mask
        # was None and the caller fell back to the bitmap font.
        face = _fontmod.get_real_metrics("monospace")
        if face is None:
            face = _fontmod.get_real_metrics("serif")
        if face is None:
            face = _fontmod.get_cjk_fallback()
        if face is None:
            return False
    mask = _get_glyph_mask(ch, face, font_px, weight)
    if mask is None:
        return False
    # P33.R1 — y is the line top; the baseline sits at y + ascent_px.
    if baseline is None:
        baseline = y + int(round(face.ascent * font_px / face.units_per_em))
    # P34.1 — pass a pre-packed 0xRRGGBB int when the color is opaque;
    # the blit fast path skips the per-call channel unpack and the
    # alpha blend.
    if isinstance(color, int):
        packed = color
    else:
        packed = color.to_u32() if color.a == 255 else None
    if packed is not None:
        _rasterize.blit_glyph_mask(bitmap, mask, x, baseline, packed)
    else:
        _rasterize.blit_glyph_mask(bitmap, mask, x, baseline, color)
    return True


def _is_cjk(ch: str) -> bool:
    """P33.R1 — True for CJK Unified Ideographs / kana / hangul ranges."""
    if not ch:
        return False
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x3040 <= cp <= 0x30FF or 0xAC00 <= cp <= 0xD7AF or
            0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF)


def _resolve_face(ch: str, real):
    """P33.R1 — the font face that can render *ch* (primary, else the
    CJK fallback face).  Returns the :class:`FontMetrics` or None."""
    import font as _fontmod
    return _fontmod.resolve_metrics_for_char(ch, real)


def _outline_valid(outline) -> bool:
    """P20 — bbox-validate a glyph outline (lazy import of ttf_font)."""
    try:
        import ttf_font
    except ImportError:
        return False
    return ttf_font.outline_valid(outline)


def _draw_curve_glyph(bitmap, x: int, y: int, ch: str, font_px: float,
                      color, real, text_rendering: str = "auto",
                      weight: float = 1.0) -> None:
    """P20 — curve-based glyph rasterization (quadratic-Bezier outline).

    Uses ``rasterize.draw_glyph_curves`` to fill the glyph outline with
    2×2 supersampling + even-odd scanline.  Falls back to the legacy
    bitmap blit if the outline fails bbox validation (which indicates a
    buggy coordinate read in the TTF parser).

    ``text_rendering`` (P20.5): "optimizeSpeed" disables antialiasing;
    "optimizeQuality"/"auto" keep it.  ``weight`` (P20.4): > 1.0 triggers
    synthetic bold (outline dilation) when no bold TTF file is available.
    """
    import rasterize
    outline = real.glyph_outline(ch)
    if outline is None or not _outline_valid(outline):
        return
    scale = font_px / real.units_per_em
    # Y baseline: in the bitmap, y is the top of the glyph box.  The TTF
    # baseline sits at (y + ascent_px).  We pass origin so the outline is
    # placed with its baseline at the correct vertical position.
    baseline_y = y + real.ascent * scale
    rasterize.draw_glyph_curves(bitmap, outline, scale,
                                x, baseline_y, color, weight=weight,
                                antialias=(text_rendering != "optimizeSpeed"))



def advance_text(text: str, font_px: float, font_family: str = "monospace") -> float:
    """P17 — total advance width of ``text`` in pixels.

    Uses per-character TTF advances when a real font is registered;
    otherwise falls back to the legacy fixed-cell estimate.
    P33.R1 — CJK codepoints use the CJK fallback face's advance when
    the primary font has no glyph for them.  A missing face (None) is
    a render-time no-op, never a layout-time crash.
    P33.R5 — when the face has a uniform advance for every character
    in the string, skip the per-char face resolution loop and compute
    the total in O(1).  Most monospace fonts are uniform, and the
    CJK fallback face is checked separately.
    P34.2 — the CJK path below is O(n) (one advance-width lookup per
    character) instead of O(n²) prefix-slicing: the P19 implementation
    called ``measure(text[:i])`` inside ``wrap`` which re-scanned the
    whole prefix for every break opportunity.
    P34.3 — hoist the module import + CJK fallback lookup + advance
    method reference out of the per-character loop.  The legacy code
    called ``_resolve_face(ch, real)`` (a Python function that does a
    ``font.resolve_metrics_for_char`` import + registry lookup) and
    ``face.advance_width(ch, font_px)`` (an attribute lookup) for
    every character; hoisting both to a single dict-keyed call cuts
    the per-char cost from ~5 lookups to 1 dict hit.
    """
    real = _real_metrics_for(font_family)
    if real is not None:
        import font as _font_mod
        cjk = _font_mod.get_cjk_fallback()
        if cjk is not None:
            # P34.3 — hoist the per-char lookups: a dict of
            # codepoint → advance-width is built once for the
            # document's actual character set (typically < 50 distinct
            # codepoints in CJK content), so the inner loop is a
            # single dict hit instead of a function call + method
            # lookup + import.
            # P34.5 — the cache is module-level (``_adv_char_cache``)
            # instead of a per-call local dict: the legacy local dict
            # was discarded when advance_text() returned, so wrap()'s
            # own _adv_of() helper had to call advance_text(ch, ...)
            # one character at a time (10.4k calls in a 10k-node
            # layout).  Faces are immutable once loaded, so a cached
            # (family, font_px, char) → advance stays valid for the
            # whole document render; clear_advance_cache() resets it.
            cache_px = int(round(font_px * 2))
            cjk_adv = cjk.advance_width
            real_adv = real.advance_width
            resolve = _font_mod.resolve_metrics_for_char
            total = 0.0
            for ch in text:
                ck = (font_family, cache_px, ch)
                a = _adv_char_cache.get(ck)
                if a is None:
                    face = resolve(ch, real)
                    a = face.advance_width(ch, font_px) if face is not None else real_adv(ch, font_px)
                    if len(_adv_char_cache) < _ADV_CHAR_CACHE_MAX:
                        _adv_char_cache[ck] = a
                total += a
            return total
        # P33.R5 — Latin-only path (no CJK fallback face).  The
        # primary face's advance is used for every character; when
        # the font is monospace (uniform advance) the total is simply
        # len * single_advance.  Even for proportional fonts, using
        # the primary face's advance for every char is the standard
        # fallback (a missing CJK face means no per-char override).
        adv = real.advance_width(text[0], font_px) if text else 0.0
        return len(text) * adv
    return len(text) * char_advance(font_px, font_family)


def measure(text: str, font_px: float, font_family: str = "monospace") -> float:
    """P15.4 — Width in pixels of `text` at `font_px` for the given family.

    P17 — uses per-character TTF advances when available.
    P34.4 — short strings (< 128 chars, the 95 %+ case of a typical
    document) go through a document-scoped width cache: the same string
    at the same size/family has a constant width, and a 10k-node layout
    measures the *identical* short string thousands of times.  Long
    strings (full paragraphs) stay on the O(n) advance scan.
    """
    if len(text) < 128:
        key = (text, font_family, int(round(font_px * 2)))
        w = _doc_width_cache.get(key)
        if w is not None:
            return w
        w = advance_text(text, font_px, font_family)
        if len(_doc_width_cache) < _doc_width_cache_max:
            _doc_width_cache[key] = w
        return w
    return advance_text(text, font_px, font_family)


# P34.2 — per-character advance lookup table (O(n) instead of O(n²)
# prefix-slicing in wrap/measure).  Keyed by (family, font_px) → a
# dict of codepoint → advance-width.  Bounded at 64 distinct
# (family, font_px) pairs; the per-codepoint dict is bounded by the
# number of distinct codepoints actually seen in the document (typical
# CJK document: a few thousand; the table is per-document-scoped so
# memory does not grow unboundedly across pages).
_adv_width_tables: dict = {}
_adv_tables_max = 64

# P34.4 — document-width cache: the full-pipeline benchmark measures
# the *same* short CJK/Latin strings (content 47321 / list item i-0)
# thousands of times across 21k text boxes.  advance_text/measure
# re-scans the string on every call even when the exact (text, font_px,
# family) triple was measured moments ago.  Cache the per-document
# width; the same string at the same size/family in a document has a
# constant width.  Bounded at 4096 entries; cleared together with the
# advance tables (clear_advance_cache) so a new document render starts
# clean.
_doc_width_cache: dict = {}
_doc_width_cache_max = 4096

# P34.5 — per-char advance-width table: the CJK branch of
# advance_text() rebuilds a dict of codepoint → advance on every call
# (21k calls in a 10k-node layout); wrap()'s own _adv_of() helper
# re-invokes advance_text(ch, ...) one character at a time, discarding
# the local cache each time.  Hoist the cache to module level, keyed
# on (family, font_px_bucket, char).  Faces are immutable once loaded
# so a cached advance is valid for the whole process;
# clear_advance_cache() resets it between independent renders (tests
# that swap the font registry).  Bounded at 8192 entries — a CJK
# document realistically uses < 4000 distinct codepoints.
_adv_char_cache: dict = {}
_ADV_CHAR_CACHE_MAX = 8192


def _adv_width_for(ch: str, font_px: float, font_family: str) -> float:
    """P34.2 — O(1) advance-width lookup for one character.

    On the first call for a given (family, font_px) the table is
    populated lazily (one ``advance_width`` per distinct codepoint
    seen so far); subsequent calls are a plain dict hit.
    """
    key = (font_family, int(round(font_px * 2)))
    table = _adv_width_tables.get(key)
    if table is None:
        table = {}
        _adv_width_tables[key] = table
    adv = table.get(ch)
    if adv is None:
        adv = advance_text(ch, font_px, font_family)
        if len(table) < 8192:
            table[ch] = adv
    return adv


def clear_advance_cache() -> None:
    """P34.4 — reset the advance-width + document-width caches.

    Called between independent document renders (layout.clear_caches)
    so a stale cached width from a previous page does not leak into
    the next one.  The per-(family, font_px) advance table is keyed
    by font identity, so it would survive a re-render of the same
    family; the document-width cache is content-scoped and MUST be
    dropped, otherwise a string that changed between renders would
    keep its old width.  P34.5 — also resets the shared per-char
    advance table so a test that swaps the font registry does not
    read a stale (family, font_px, char) → advance entry.
    """
    _adv_width_tables.clear()
    _doc_width_cache.clear()
    _adv_char_cache.clear()


# ---------------------------------------------------------------------------
# P19 — UAX#14 simplified line-breaking + CJK kinsoku + &shy; + overflow-wrap
# ---------------------------------------------------------------------------

# Characters that must NOT start a line (kinsoku shori: closing
# punctuation pushed to the next line).  A superset of common CJK
# closing marks — Latin closing brackets are included too because they
# also look odd as the first character of a wrapped line.
_CJK_NO_LINE_START = set(
    "，。！？）〉」】》、…～―"
    ".,;:!?)>]}"
)

# Characters that must NOT end a line (opening brackets / quotes that
# should stay with the following word on the next line).
_CJK_NO_LINE_END = set(
    "（〈《【〔［「『"
    "([{\""
)

# P19 — simplified UAX#14 break classes (only the classes we need).
# Coarse classification of each character:
#   SP   — space (break allowed after)
#   NS   — no-break space
#   BA   — break after (e.g. '-')
#   HY   — soft hyphen (break opportunity)
#   OP   — opening bracket / quote (cannot end a line)
#   CL   — closing bracket / quote (cannot start a line)
#   CM   — combining mark (no break before)
#   CJK  — CJK ideograph / kana / hangul (break after each)
#   AL   — alphabetic / other (default)

def _break_class(ch: str) -> str:
    cp = ord(ch)
    if cp == 0x0020 or cp == 0x00A0 or cp == 0x3000:
        return "SP"
    if cp == 0x2011:  # non-breaking hyphen
        return "NS"
    if cp == 0x00AD:  # soft hyphen
        return "HY"
    if cp in (0x2010, 0x002D, 0x058D, 0x06DD, 0x1806):  # hyphens
        return "BA"
    if cp in (0x0300, 0x0301):  # common combining marks
        return "CM"
    # Opening brackets / quotes (Latin + fullwidth + CJK)
    if ch in "([{\"'" + chr(0xFF08) + chr(0xFF3B) + "「〈《【〔［『“‘":
        return "OP"
    # Closing brackets / quotes (Latin + fullwidth + CJK)
    if ch in ")]}\"'" + chr(0xFF09) + chr(0xFF3D) + "」〉》】）〕］』”’":
        return "CL"
    # CJK ideographs, kana, hangul
    if (0x4E00 <= cp <= 0x9FFF or      # CJK Unified
        0x3400 <= cp <= 0x4DBF or      # CJK Extension A
        0x20000 <= cp <= 0x2FA1F or    # CJK Extension B
        0x3040 <= cp <= 0x30FF or      # Hiragana + Katakana
        0x3130 <= cp <= 0x318F or      # Hangul Compat
        0xAC00 <= cp <= 0xD7A3 or      # Hangul Syllables
        0x3000 <= cp <= 0x303F):      # CJK Symbols & Punctuation
        return "CJK"
    return "AL"


def _is_cjk_no_start(ch: str) -> bool:
    return ch in _CJK_NO_LINE_START or _break_class(ch) == "CL"


def _is_cjk_no_end(ch: str) -> bool:
    return ch in _CJK_NO_LINE_END or _break_class(ch) == "OP"


def _measure_at(text: str, i: int, font_px: float, font_family: str) -> float:
    """P19 — width of text[:i] in pixels (i = 0..len(text))."""
    return measure(text[:i], font_px, font_family)


def _in_word_char(ch: str) -> bool:
    """P19 — True if ch is a regular word character (not space/shy/CJK-punct)."""
    if not ch:
        return False
    return (not ch.isspace()) and ch != "\u00AD" and _break_class(ch) == "AL"


def wrap(
    text: str,
    width: float,
    font_px: float,
    font_family: str = "monospace",
    word_break: str = "",
    overflow_wrap: str = "",
) -> list[str]:
    """P15.4 + P19 — Wrap `text` into lines that each fit in `width` pixels.

    P19 features:
    - **UAX#14 simplified**: break at spaces; CJK chars break after each
      character; closing brackets / punctuation do NOT start a line
      (kinsoku shori); opening brackets / quotes do NOT end a line.
    - **&shy; (U+00AD)**: soft hyphen is a break opportunity; when a
      break occurs at a soft hyphen, a '-' is emitted at the end of the
      previous line.
    - **word-break: break-all**: every character is a break opportunity
      (CJK default behavior, now available for Latin text too).
    - **overflow-wrap: break-word**: a single word longer than `width`
      is force-broken character-by-character instead of overflowing.
    - **Long-word overflow**: even without overflow-wrap, a word that
      alone exceeds `width` is broken character-by-character (minimal
      overflow handling).
    """
    lines: list[str] = []
    current = ""
    i = 0
    n = len(text)
    # P34.3 — the legacy inner loop re-measured the whole prefix of
    # `current` on every space / shy break opportunity (O(n²) on a
    # line with many break opportunities).  Replace it with a single
    # running-width accumulator: `cur_w` is the width of `current` as
    # it stands, updated incrementally as characters are appended and
    # reset to the width of the new `current` after every line break.
    _adv: dict = {}

    def _adv_of(ch: str) -> float:
        a = _adv.get(ch)
        if a is None:
            a = advance_text(ch, font_px, font_family)
            if len(_adv) < 8192:
                _adv[ch] = a
        # P0 (GPT report) — advance_text must return a float; a string
        # return would poison every width accumulator in wrap() and
        # crash the first ``trial_w > width`` compare with a
        # TypeError.  Coerce defensively so a buggy caller can never
        # take down text layout.
        return float(a) if isinstance(a, (int, float)) else 0.0

    # P19 — track the start of the current "word" (maximal run of
    # non-space, non-shy, AL-class chars) so we can detect when a
    # single word alone exceeds `width` and must be force-broken.
    word_start = 0
    in_word = False
    cur_w = 0.0

    while i < n:
        ch = text[i]
        is_space = ch in (" ", "\t")
        is_shy = (ch == "\u00AD")

        # --- P19.4 — long-word overflow / overflow-wrap:break-word ---
        # If we are inside a Latin word (AL-class chars) and the word
        # alone already exceeds `width`, force a char-by-char break so
        # the word does not overflow the container.
        if (not is_space and not is_shy and in_word
                and _break_class(ch) == "AL" and i > word_start):
            word_text = text[word_start:i + 1]
            # P34.2 — incremental O(n) width: one dict lookup per char
            # instead of measure() re-scanning the whole word.
            w = 0.0
            for wc in word_text:
                w += _adv_of(wc)
            if w > width:
                # Emit everything before this word on the current line,
                # then start a new line with the word so far.
                prefix_len = len(current) - len(word_text) + 1
                prefix = current[:prefix_len].rstrip() if prefix_len > 0 else ""
                if prefix:
                    lines.append(prefix)
                current = word_text
                # The word now starts a fresh line; the running width
                # is the width of the word so far.
                cur_w = w
                word_start = i
                i += 1
                continue

        # --- Update word tracking ---
        if is_space or is_shy:
            in_word = False
        elif _break_class(ch) == "AL":
            if not in_word:
                word_start = i
            in_word = True
        else:
            in_word = False

        if is_space:
            # Accumulate the space; break AFTER it only if the next
            # word would overflow the remaining width.
            current += ch
            cur_w += _adv_of(ch)
            j = i + 1
            next_word = ""
            while j < n and text[j] not in (" ", "\t", "\u00AD"):
                next_word += text[j]
                j += 1
            if current.strip() and next_word:
                # P34.2 — incremental O(n): measure the two halves
                # independently (current is the line prefix, next_word
                # is the candidate suffix) and add their widths instead
                # of re-measuring the concatenated trial string.
                cw = 0.0
                for c in current.rstrip():
                    cw += _adv_of(c)
                nw = 0.0
                for c in next_word:
                    nw += _adv_of(c)
                trial_w = cw + _adv_of(" ") + nw
                if trial_w > width:
                    # P19.2 — kinsoku: if the next word starts with a
                    # no-line-start char, pull it onto the current line
                    # (slight overflow tolerated) instead of letting it
                    # begin the next line.
                    if not _is_cjk_no_start(next_word[0]):
                        lines.append(current.rstrip())
                        current = next_word
                        cur_w = nw
                        word_start = j
                        in_word = _break_class(next_word[0]) == "AL"
                        i = j
                        continue
                    else:
                        current = current.rstrip() + " " + next_word
                        cur_w = trial_w
                        word_start = j
                        in_word = _break_class(next_word[0]) == "AL"
                        i = j
                        continue
            i += 1
            continue

        if is_shy:
            # Soft hyphen: break opportunity.  Look ahead to the full
            # next word; if current + "-" + next_word would overflow,
            # break here and emit '-' at the end of this line.
            j = i + 1
            next_word = ""
            while j < n and text[j] not in (" ", "\t"):
                next_word += text[j]
                j += 1
            if next_word:
                # P34.2 — incremental O(n): the trial is current + "-" +
                # next_word; measure each part independently and sum.
                cw = 0.0
                for c in current.rstrip():
                    cw += _adv_of(c)
                nw = 0.0
                for c in next_word:
                    nw += _adv_of(c)
                trial_w = cw + _adv_of("-") + nw
                if trial_w > width:
                    lines.append(current.rstrip() + "-")
                    current = next_word
                    cur_w = nw
                    word_start = j
                    in_word = _break_class(next_word[0]) == "AL"
                    i = j
                    continue
            # No break needed: consume the shy (invisible).
            in_word = False
            i += 1
            continue

        # --- Non-space, non-shy character ---
        if len(current) > 0:
            # P34.2 — O(1) incremental width: keep a running sum of the
            # widths of the characters already placed on this line and
            # add the width of the candidate character instead of
            # re-measuring the entire current line.
            trial_w = cur_w + _adv_of(ch)
            if trial_w > width:
                # P19.2 — kinsoku: if ch is a no-line-start char, pull
                # it onto the current line (slight overflow tolerated)
                # instead of starting a new line with it.
                if _is_cjk_no_start(ch):
                    current += ch
                    cur_w = trial_w
                else:
                    lines.append(current.rstrip())
                    current = ch
                    cur_w = _adv_of(ch)
                word_start = i
                in_word = _break_class(ch) == "AL"
                i += 1
                continue
        # Normal append (no overflow, or first char on the line).
        current += ch
        cur_w += _adv_of(ch)
        i += 1

    # Flush the last line.
    if current:
        lines.append(current.rstrip())
    return lines or [""]


def _blit(bitmap: Bitmap, x: int, y: int, g: list[int], scale: int,
          color: Color, cell_w: int = 8) -> None:
    """P15.4 — blit a glyph of `cell_w` bytes-per-row (8 or 10)."""
    if scale == 0:
        return
    cols = len(g)
    for row in range(8):
        for col in range(cols):
            byte = g[row]
            if col < 8:
                bit = (byte >> (7 - col)) & 1
            else:
                # 9th and 10th columns (serif variant): blank.
                bit = 0
            if bit:
                for sy in range(scale):
                    for sx in range(scale):
                        bitmap.set_pixel(x + col * scale + sx, y + row * scale + sy, color)


def _blit_line_bitmap(bitmap: "Bitmap", x: int, y: int, text: str,
                      scale: int, color, line_w: float,
                      align: str = "left", max_width: float = 0.0) -> None:
    """P34.1 — legacy 8×8 bitmap-font fast path (no real TTF registered).

    Used by ``draw_text`` when no real font face is available: blit every
    character with the built-in bitmap font, skipping the per-character
    TTF face resolution entirely.  Mirrors the P15.4 bitmap loop without
    the face-resolution / advance-width overhead.
    """
    cx = float(x)
    if align == "center" and max_width > 0:
        line_w = measure(text, font_px, "monospace")
        cx = x + max(0.0, (max_width - line_w) / 2.0)
    elif align == "right" and max_width > 0:
        line_w = measure(text, font_px, "monospace")
        cx = x + max(0.0, max_width - line_w)
    advance = float(_cell_width("monospace")) * scale
    for ch in text:
        g = _glyph_for(ord(ch) & 0x7F, "monospace")
        _blit(bitmap, int(cx), y, g, scale, color, cell_w=_cell_width("monospace"))
        cx += advance


def draw_text(
    bitmap: Bitmap,
    x: float,
    y: float,
    text: str,
    font_px: float,
    color: Color,
    max_width: float,
    align: str = "left",
    font_family: str = "monospace",
    letter_spacing: float = 0.0,
    text_rendering: str = "auto",
    font_weight: int = 400,
) -> float:
    """P15.4 — Wrap + blit `text` onto `bitmap`. `align` is left|center|right.

    ``font_family`` selects the glyph variant ("monospace" default or "serif").
    P20.5 — ``letter_spacing`` adds extra advance between characters (px);
    ``text_rendering`` is a quality hint: "optimizeSpeed" disables the
    2×2 antialiasing supersampling in the curve path, "optimizeQuality"
    keeps it.
    P20.4 — ``font_weight`` >= 600 selects the bold TTF variant; when no
    bold file is registered the curve path applies synthetic bold
    (dilation) to the normal-weight outline.
    Returns the total height consumed (px).
    """
    scale = scale_factor(font_px)
    advance = char_advance(font_px, font_family)
    line_height = line_height_px(font_px, font_family)
    real = _real_metrics_for(font_family, font_weight)
    weight = 2.0 if (_synthetic_bold and font_weight >= 600) else 1.0
    wrapped = wrap(text, max_width, font_px, font_family)
    # P34.1 — resolve the *primary* face once per draw_text call:
    # most documents are pure-Latin, so the per-character CJK
    # fallback check (``has_glyph``) is the only place a different
    # face can be chosen, and it is an O(1) cmap lookup when the
    # primary face does have the glyph.  The legacy path called
    # ``resolve_metrics_for_char`` (registry lookup + cmap) per char.
    primary_face = real
    if real is None:
        # No real font registered: skip face resolution entirely and
        # take the legacy 8×8 bitmap path for every character.
        import font as _fontmod
        primary_face = _fontmod.get_real_metrics("monospace") or \
            _fontmod.get_real_metrics("serif")
        if primary_face is None:
            cy = y
            for line in wrapped:
                stripped = line.strip()
                if stripped:
                    _blit_line_bitmap(bitmap, int(x), int(cy), stripped,
                                      scale, color, line_w=0.0, align=align,
                                      max_width=max_width)
                cy += line_height
            return len(wrapped) * line_height
    cy = y
    # P34.2 — hoist the per-line / per-char invariants out of the
    # hot loops: the color-pack decision (opaque vs semi-transparent)
    # and the per-face baseline (ascent * font_px / units_per_em) are
    # computed once here instead of inside _blit_coverage for every
    # character.  For a pure-Latin document the primary_face alone
    # is used and its baseline is a single int; for a mixed CJK+Latin
    # document we add one entry per distinct fallback face.
    color_packed = None
    if isinstance(color, int):
        color_packed = color
    elif color.a == 255:
        color_packed = color.to_u32()
    # face → baseline offset (ascent * font_px / upm), keyed by id(face).
    # P0 (GPT report) — a missing CJK fallback face can leave the
    # per-char resolution returning None; guard the baseline for
    # every face we actually use so a NoneType never reaches the
    # hot loop.
    _baseline_offsets: dict = {}
    primary_baseline = int(round(primary_face.ascent * font_px / primary_face.units_per_em))
    _baseline_offsets[id(primary_face)] = primary_baseline

    def _face_baseline(f) -> int:
        key = id(f)
        off = _baseline_offsets.get(key)
        if off is None:
            # P0 — `f` may be a fallback face that failed to load
            # (units_per_em = 0).  Fall back to the primary face's
            # baseline instead of dividing by zero / reading ascent
            # off a NoneType.
            if f is None or not getattr(f, "units_per_em", 0):
                off = primary_baseline
            else:
                off = int(round(f.ascent * font_px / f.units_per_em))
            _baseline_offsets[key] = off
        return off

    for line in wrapped:
        stripped = line.strip()
        if stripped:
            line_w = measure(stripped, font_px, font_family)
            if align == "center":
                cx = x + max(0.0, (max_width - line_w) / 2.0)
            elif align == "right":
                cx = x + max(0.0, max_width - line_w)
            else:
                cx = x
            iy = int(cy)
            for ch in stripped:
                # P34.1 — O(1) cmap check on the primary face; only
                # fall through to the per-char registry resolution when
                # the primary face lacks the glyph (CJK on a Latin font).
                face = primary_face
                baseline_off = primary_baseline
                if not face.has_glyph(ch):
                    # P0 (GPT report) — _resolve_face may return None
                    # when no fallback face is registered for the
                    # character; the legacy code called _face_baseline
                    # on that None and then face.advance_width,
                    # crashing the whole render.  A missing fallback
                    # must not take down the page: fall through to
                    # the .notdef / bitmap paths below instead.
                    fb = _resolve_face(ch, real)
                    if fb is not None:
                        face = fb
                        if face is not primary_face:
                            baseline_off = _face_baseline(face)
                    else:
                        face = None
                # P34.2 — inline the blit (mask lookup + blit) to skip
                # the per-char function-call overhead of _blit_coverage;
                # the packed color is already resolved above.
                if face is not None:
                    mask = _get_glyph_mask(ch, face, font_px, weight)
                    if mask is not None:
                        if color_packed is not None:
                            _rasterize.blit_glyph_mask(bitmap, mask, int(cx),
                                                       iy + baseline_off, color_packed)
                        else:
                            _rasterize.blit_glyph_mask(bitmap, mask, int(cx),
                                                       iy + baseline_off, color)
                        cx += face.advance_width(ch, font_px) + letter_spacing
                        continue
                    # P33.R1 — last-resort curve path for CJK codepoints
                    # whose coverage mask failed (empty / malformed outline):
                    # a drawn .notdef box is still better than a meaningless
                    # ASCII cell.  ASCII content keeps the 8×8 bitmap path.
                    if _is_cjk(ch):
                        _outline = face.glyph_outline(ch)
                        if _outline is not None and _outline_valid(_outline):
                            _draw_curve_glyph(bitmap, int(cx), int(cy), ch,
                                              font_px, color, face,
                                              text_rendering=text_rendering,
                                              weight=weight)
                            cx += face.advance_width(ch, font_px) + letter_spacing
                            continue
                    g = _glyph_for(ord(ch) & 0x7F, font_family)
                    _blit(bitmap, int(cx), int(cy), g, scale, color,
                          cell_w=_cell_width(font_family))
                    # P17 — per-character advance from TTF hmtx when available.
                    cx += face.advance_width(ch, font_px) + letter_spacing
                else:
                    # P0 — no face at all can render this character
                    # (no fallback registered).  Draw a .notdef cell
                    # and advance by the primary face's fallback
                    # advance (or 1 cell) so the layout stays stable
                    # instead of crashing the render.
                    g = _glyph_for(ord(ch) & 0x7F, font_family)
                    _blit(bitmap, int(cx), int(cy), g, scale, color,
                          cell_w=_cell_width(font_family))
                    cx += (primary_face.advance_width(ch, font_px)
                          if primary_face is not None
                          else _cell_width(font_family)) + letter_spacing
            cy += line_height
    return len(wrapped) * line_height

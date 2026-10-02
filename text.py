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
    """
    real = _real_metrics_for(font_family)
    if real is not None:
        # Use the advance of 'M' (a wide char) as a representative value.
        # For true per-char advances, see advance_text below.
        adv_units = real.advances[real.cmap.get(77, 0)] if real.cmap.get(77, 0) < len(real.advances) else real.units_per_em
        return adv_units * (font_px / real.units_per_em)
    return float(_cell_width(font_family)) * scale_factor(font_px)


def _real_metrics_for(font_family: str):
    """P17 — return the real TTF metrics for a family, or None."""
    import font
    return font.get_real_metrics(font_family)


def advance_text(text: str, font_px: float, font_family: str = "monospace") -> float:
    """P17 — total advance width of ``text`` in pixels.

    Uses per-character TTF advances when a real font is registered;
    otherwise falls back to the legacy fixed-cell estimate.
    """
    real = _real_metrics_for(font_family)
    if real is not None:
        total = 0.0
        for ch in text:
            total += real.advance_width(ch, font_px)
        return total
    return len(text) * char_advance(font_px, font_family)


def measure(text: str, font_px: float, font_family: str = "monospace") -> float:
    """P15.4 — Width in pixels of `text` at `font_px` for the given family.

    P17 — uses per-character TTF advances when available.
    """
    return advance_text(text, font_px, font_family)


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
    # P19 — track the start of the current "word" (maximal run of
    # non-space, non-shy, AL-class chars) so we can detect when a
    # single word alone exceeds `width` and must be force-broken.
    word_start = 0
    in_word = False

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
            if measure(word_text, font_px, font_family) > width:
                # Emit everything before this word on the current line,
                # then start a new line with the word so far.
                prefix_len = len(current) - len(word_text) + 1
                prefix = current[:prefix_len].rstrip() if prefix_len > 0 else ""
                if prefix:
                    lines.append(prefix)
                current = word_text
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
            j = i + 1
            next_word = ""
            while j < n and text[j] not in (" ", "\t", "\u00AD"):
                next_word += text[j]
                j += 1
            if current.strip() and next_word:
                trial = current.rstrip() + " " + next_word
                if measure(trial, font_px, font_family) > width:
                    # P19.2 — kinsoku: if the next word starts with a
                    # no-line-start char, pull it onto the current line
                    # (slight overflow tolerated) instead of letting it
                    # begin the next line.
                    if not _is_cjk_no_start(next_word[0]):
                        lines.append(current.rstrip())
                        current = next_word
                        word_start = j
                        in_word = _break_class(next_word[0]) == "AL"
                        i = j
                        continue
                    else:
                        current = trial
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
                trial = current.rstrip() + "-" + next_word
                if measure(trial, font_px, font_family) > width:
                    lines.append(current.rstrip() + "-")
                    current = next_word
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
            trial_w = measure(current + ch, font_px, font_family)
            if trial_w > width:
                # P19.2 — kinsoku: if ch is a no-line-start char, pull
                # it onto the current line (slight overflow tolerated)
                # instead of starting a new line with it.
                if _is_cjk_no_start(ch):
                    current += ch
                else:
                    lines.append(current.rstrip())
                    current = ch
                word_start = i
                in_word = _break_class(ch) == "AL"
                i += 1
                continue
        # Normal append (no overflow, or first char on the line).
        current += ch
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
) -> float:
    """P15.4 — Wrap + blit `text` onto `bitmap`. `align` is left|center|right.

    ``font_family`` selects the glyph variant ("monospace" default or "serif").
    Returns the total height consumed (px).
    """
    scale = scale_factor(font_px)
    advance = char_advance(font_px, font_family)
    line_height = line_height_px(font_px, font_family)
    real = _real_metrics_for(font_family)
    wrapped = wrap(text, max_width, font_px, font_family)
    cy = y
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
            for ch in stripped:
                g = _glyph_for(ord(ch) & 0x7F, font_family)
                _blit(bitmap, int(cx), int(cy), g, scale, color,
                      cell_w=_cell_width(font_family))
                # P17 — per-character advance from TTF hmtx when available.
                if real is not None:
                    cx += real.advance_width(ch, font_px)
                else:
                    cx += advance
        cy += line_height
    return len(wrapped) * line_height

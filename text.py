"""Stage 4 (support) — text metrics and CPU glyph rasterization.

Uses the built-in 8x8 ASCII bitmap font (see font.py). Glyphs are scaled to
the requested font size by nearest-neighbour and blitted onto the target
bitmap. Pure standard library.

P15.4 — font family support: ``font_family`` parameter on ``draw_text``
switches between the monospace (default 8x8) and serif (10x8 wide) variants.
"""
from __future__ import annotations

from font import glyph as _glyph, glyph_for as _glyph_for, cell_width as _cell_width
from paint import Bitmap, Color


def scale_factor(font_px: float) -> int:
    """The integer scale used for both measurement and drawing (never < 1)."""
    return max(1, int(font_px / 8.0))


def char_advance(font_px: float, font_family: str = "monospace") -> float:
    """P15.4 — Width of one character cell in pixels for the given family."""
    return float(_cell_width(font_family)) * scale_factor(font_px)


def measure(text: str, font_px: float, font_family: str = "monospace") -> float:
    """P15.4 — Width in pixels of `text` at `font_px` for the given family."""
    return len(text) * char_advance(font_px, font_family)


def wrap(text: str, width: float, font_px: float, font_family: str = "monospace") -> list[str]:
    """P15.4 — Wrap `text` into lines that each fit in `width` pixels."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = word if not current else current + " " + word
        if measure(trial, font_px, font_family) > width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
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
    line_height = max(1.0, font_px / 8.0) * 8.0
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
                cx += advance
        cy += line_height
    return len(wrapped) * line_height

"""P17 — TTF/OTF font file parser (pure stdlib).

Parses the minimum set of sfnt tables needed to render text:
- ``head``: unitsPerEm, bounding box
- ``hhea``: ascent / descent / lineGap
- ``hmtx``: advance width + left side bearing (per glyph)
- ``cmap``: character → glyph-id mapping (platform 3, encoding 1)
- ``loca``: glyph location offsets
- ``glyf``: glyph outline (quadratic Bezier) — extracted for P20 rasterization
- ``maxp``: numGlyphs
- ``OS/2``: weight / class for font-family matching

What P17 *does* replace: character-advance estimation —
``advance_width(ch)`` now returns the real ``hmtx`` value scaled to the
requested font size, instead of a fixed 8px-per-char.  Real glyph
rasterization is deferred to P20; P17 stores outlines and metrics.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GlyphHeader:
    """Parsed glyph header (first 10 bytes of a glyf record)."""
    num_contours: int      # >0 = simple glyph, <0 = composite, 0 = empty
    x_min: int = 0
    y_min: int = 0
    x_max: int = 0
    y_max: int = 0
    raw_offset: int = 0    # byte offset within the glyf table


@dataclass
class GlyphOutline:
    """P17.2 — extracted glyph outline (quadratic Bezier + lines).

    ``points`` is a list of ``(x, y, on_curve)`` tuples in font-unit
    coordinates.  For composite glyphs the outlines of all components
    are merged into a single point list (transformed to the parent
    coordinate system).  ``is_empty`` is True for glyphs with no
    contours (e.g. space).
    """
    points: list = field(default_factory=list)
    is_empty: bool = False
    is_composite: bool = False


@dataclass
class FontMetrics:
    """P17 — real font metrics read from TTF tables."""
    units_per_em: int = 1000
    ascent: int = 0
    descent: int = 0        # negative (below baseline)
    line_gap: int = 0
    num_glyphs: int = 0
    # advance widths in font units (hmtx)
    advances: list = field(default_factory=list)
    left_side_bearings: list = field(default_factory=list)
    # character → glyph id
    cmap: dict = field(default_factory=dict)
    # P17 — OS/2 weight class (400 = normal, 700 = bold)
    weight_class: int = 400
    # P17.2 — raw glyf/loca table data for outline extraction
    _data: bytes = b""
    _tables: dict = field(default_factory=dict)

    def advance_width(self, ch: str, font_px: float) -> float:
        """P17 — advance width in pixels for a character at ``font_px``.

        Uses the real ``hmtx`` value scaled from units-per-EM to pixels.
        Falls back to a monospace estimate if the char is missing from
        the font.
        """
        if not self.advances or self.units_per_em == 0:
            return 8.0 * max(1, int(font_px / 8.0))  # legacy fallback
        code = ord(ch) if ch else 0
        gid = self.cmap.get(code, 0)
        if gid < 0 or gid >= len(self.advances):
            # .notdef / missing glyph: use a wide fallback cell.
            return int(font_px)
        units = self.advances[gid]
        # units-per-EM → pixels at the requested font size.
        return float(units) * (font_px / self.units_per_em)

    def line_height(self, font_px: float) -> float:
        """P17 — line height in pixels (ascent - descent + lineGap)."""
        if self.units_per_em == 0:
            return max(1.0, font_px)
        scale = font_px / self.units_per_em
        return (self.ascent - self.descent + self.line_gap) * scale

    def glyph_outline(self, ch: str) -> Optional[GlyphOutline]:
        """P17.2 — extract the glyph outline for a character.

        Returns a :class:`GlyphOutline` with quadratic-Bezier points in
        font-unit coordinates, or ``None`` if the character is not in
        the font.  Composite glyphs are resolved to their component
        outlines (transformed to the parent coordinate system).
        """
        if not self._data or not self._tables:
            return None
        code = ord(ch) if ch else 0
        gid = self.cmap.get(code, 0)
        if gid == 0 or gid >= self.num_glyphs:
            return None
        out = _extract_glyph(self, gid)
        # P17.3 — an empty glyph (space, ligature placeholder, etc.)
        # may be a composite with many degenerate points; treat it as
        # "empty" when all points cluster within a 1-unit box.
        if out is not None and out.points:
            xs = [p[0] for p in out.points]
            ys = [p[1] for p in out.points]
            if (max(xs) - min(xs)) < 2 and (max(ys) - min(ys)) < 2:
                out.is_empty = True
        return out

    def has_glyph(self, ch: str) -> bool:
        """P17 — True if the font has a non-empty glyph for ``ch``."""
        code = ord(ch) if ch else 0
        return code in self.cmap and self.cmap[code] != 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class TTFError(Exception):
    """Raised on malformed or unsupported TTF/OTF files."""


def _read_table(data: bytes, tables: dict, tag: str) -> bytes:
    if tag not in tables:
        return b""
    off, length = tables[tag]
    return data[off:off + length]


def _parse_cmap(cmap_bytes: bytes) -> dict:
    """P17 — parse the cmap table (format 4 subtables preferred).

    Subtable offsets in the cmap table are *relative to the start of the
    cmap table itself*, not the file.  ``cmap_bytes`` must therefore be
    the full cmap table content (from the table directory).
    """
    result: dict = {}
    if len(cmap_bytes) < 4:
        return result
    num_subtables = struct.unpack(">H", cmap_bytes[2:4])[0]
    # Prefer platform 3 / encoding 1 (Windows Unicode BMP, format 4).
    preferred_rel = None
    so = 4
    for i in range(num_subtables):
        if so + 8 > len(cmap_bytes):
            break
        platform, encoding, rel_off = struct.unpack(">HHI", cmap_bytes[so:so + 8])
        so += 8
        if (platform, encoding) == (3, 1):
            preferred_rel = rel_off
            break
    if preferred_rel is None:
        # Fall back to platform 0 / encoding 3 (Unicode, format 4).
        so = 4
        for i in range(num_subtables):
            if so + 8 > len(cmap_bytes):
                break
            platform, encoding, rel_off = struct.unpack(">HHI", cmap_bytes[so:so + 8])
            so += 8
            if (platform, encoding) == (0, 3):
                preferred_rel = rel_off
                break
    if preferred_rel is None:
        return result
    sub = cmap_bytes[preferred_rel:]
    if len(sub) < 4:
        return result
    fmt, length = struct.unpack(">HH", sub[:4])
    sub_len = length if length > 0 else len(sub)
    if fmt == 4:
        # Format 4 (segmented).
        seg_count_x2 = struct.unpack(">H", sub[6:8])[0]
        seg_count = seg_count_x2 // 2
        end_codes_off = 14
        start_codes_off = end_codes_off + seg_count_x2 * 2
        id_delta_off = start_codes_off + seg_count * 2
        id_range_off = id_delta_off + seg_count * 2
        # P17 — endCode[0] is always 0xFFFF (spec: reserved).  We parse it
        # but never match codepoint 0xFFFF.
        end_codes = struct.unpack(f">{seg_count}H", sub[end_codes_off:end_codes_off + seg_count * 2])
        start_codes = struct.unpack(f">{seg_count}H", sub[start_codes_off:start_codes_off + seg_count * 2])
        id_deltas = struct.unpack(f">{seg_count}h", sub[id_delta_off:id_delta_off + seg_count * 2])
        id_ranges = struct.unpack(f">{seg_count}H", sub[id_range_off:id_range_off + seg_count * 2])
        for i in range(seg_count):
            start_c = start_codes[i]
            end_c = end_codes[i]
            delta = id_deltas[i]
            rng = id_ranges[i]
            if rng == 0:
                for c in range(start_c, end_c + 1):
                    if c == 0xFFFF:
                        continue
                    gid = (c + delta) & 0xFFFF
                    if gid != 0:
                        result[c] = gid
            else:
                # P17 — TTF spec: idRangeOffset[i] is an offset *from the
                # current position in the idRangeOffset array* (i.e. from
                # byte ``id_range_off + i*2``) to the start of the glyph-ID
                # array for this segment.  The first entry of that array
                # corresponds to the segment's startCode.
                base = id_range_off + i * 2 + rng
                for c in range(start_c, end_c + 1):
                    if c == 0xFFFF:
                        continue
                    idx = base + (c - start_c) * 2
                    if idx + 2 > sub_len:
                        break
                    gid = struct.unpack(">H", sub[idx:idx + 2])[0]
                    if gid != 0:
                        result[c] = gid
    elif fmt == 12:
        # Format 12 (32-bit code points).  Rare in TTF, present in some OTF.
        num_groups = struct.unpack(">I", sub[12:16])[0]
        goff = 16
        for g in range(num_groups):
            if goff + 12 > len(sub):
                break
            start_c, end_c, start_g = struct.unpack(">III", sub[goff:goff + 12])
            goff += 12
            for c in range(start_c, end_c + 1):
                result[c] = start_g + (c - start_c)
    elif fmt == 14:
        # Format 14 (Unicode variation sequences).  Skip for toy.
        pass
    return result


def load_ttf(path: str) -> FontMetrics:
    """P17 — parse a TTF/OTF file into a :class:`FontMetrics`.

    Raises :class:`TTFError` on unreadable / malformed files so callers
    can fall back to the built-in bitmap font.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise TTFError(f"cannot read font file {path!r}: {e}")

    if len(data) < 6:
        raise TTFError(f"font file too small ({len(data)} bytes)")
    sfnt_version, num_tables = struct.unpack(">IH", data[:6])
    # sfnt 0x00010000 = TTF, 'OTTO' = CFF/OTF, 'true' = old Mac TTF.
    if sfnt_version not in (0x00010000, 0x54544F46, 0x74727565, 0x4F54544F):
        # Accept 'OTTO' (CFF-based OTF) too — we only read head/hhea/hmtx/cmap,
        # which are present in both.
        pass

    # Table directory.
    tables: dict = {}
    off = 12
    for i in range(num_tables):
        if off + 16 > len(data):
            break
        tag_bytes, _check, toff, tlen = struct.unpack(">4sIII", data[off:off + 16])
        tag = tag_bytes.decode("latin-1")
        tables[tag] = (toff, tlen)
        off += 16

    required = ("head", "hhea", "hmtx", "maxp")
    for tag in required:
        if tag not in tables:
            raise TTFError(f"font missing required table {tag!r}")

    # head
    head = _read_table(data, tables, "head")
    if len(head) < 20:
        raise TTFError("head table too short")
    units_per_em = struct.unpack(">H", head[18:20])[0]
    if units_per_em == 0:
        units_per_em = 1000
    # P17 — flagTable bit 0 = MAC / low bit (0 = TrueType, 1 = CFF).
    # We only support TrueType glyf-based fonts; CFF/OTF raises.
    flags = struct.unpack(">H", head[8:10])[0]
    is_cff = bool(flags & 0x0001)

    # hhea
    hhea = _read_table(data, tables, "hhea")
    ascent = descent = line_gap = 0
    num_h_metrics = 0
    if len(hhea) >= 36:
        ascent, descent = struct.unpack(">hh", hhea[4:8])
        line_gap = struct.unpack(">h", hhea[8:10])[0]
        num_h_metrics = struct.unpack(">H", hhea[34:36])[0]

    # maxp → numGlyphs
    maxp = _read_table(data, tables, "maxp")
    if len(maxp) < 6:
        raise TTFError("maxp table too short")
    num_glyphs = struct.unpack(">H", maxp[4:6])[0]

    # hmtx
    hmtx = _read_table(data, tables, "hmtx")
    advances: list = []
    lsbs: list = []
    count = num_h_metrics if num_h_metrics > 0 else num_glyphs
    count = min(count, num_glyphs)
    for i in range(count):
        if i * 4 + 4 > len(hmtx):
            break
        adv, lsb = struct.unpack(">Hh", hmtx[i * 4:i * 4 + 4])
        advances.append(adv)
        lsbs.append(lsb)
    # Glyphs beyond numHMetrics share the last advance width (TTF spec).
    if num_glyphs > len(advances):
        if advances:
            last_adv = advances[-1]
        else:
            last_adv = units_per_em
        advances.extend([last_adv] * (num_glyphs - len(advances)))
        lsbs.extend([0] * (num_glyphs - len(lsbs)))

    # cmap
    cmap_bytes = _read_table(data, tables, "cmap")
    cmap = _parse_cmap(cmap_bytes)

    # P17 — read OS/2 weight class for font-weight matching.
    os2 = _read_table(data, tables, "OS/2")
    weight_class = 400
    if len(os2) >= 10:
        weight_class = struct.unpack(">H", os2[4:6])[0]

    # P17 — CFF/OTF fonts (flagTable bit 0 set) are not supported for
    # glyph rasterization; raise so the caller falls back to bitmap.
    if is_cff:
        raise TTFError("CFF/OTF font: only TrueType glyf fonts supported")

    return FontMetrics(
        units_per_em=units_per_em,
        ascent=ascent,
        descent=descent,
        line_gap=line_gap,
        num_glyphs=num_glyphs,
        advances=advances,
        left_side_bearings=lsbs,
        cmap=cmap,
        weight_class=weight_class,
        _data=data,
        _tables=tables,
    )


# ---------------------------------------------------------------------------
# P17.2 — glyf / loca outline extraction
# ---------------------------------------------------------------------------

def _read_loca(fm: FontMetrics, i: int) -> int:
    """Read the loca entry for glyph index ``i`` (byte offset into glyf)."""
    data = fm._data
    tables = fm._tables
    if "loca" not in tables:
        return 0
    loca_off, loca_len = tables["loca"]
    # Determine short vs long loca.
    num_glyphs = fm.num_glyphs
    loca_is_short = (loca_len // (num_glyphs + 1)) < 4
    if loca_is_short:
        v = struct.unpack(">H", data[loca_off + i * 2:loca_off + i * 2 + 2])[0]
        return v * 2
    return struct.unpack(">I", data[loca_off + i * 4:loca_off + i * 4 + 4])[0]


def _read_glyf(fm: FontMetrics, gid: int) -> bytes:
    """Read the raw glyf bytes for glyph index ``gid``."""
    data = fm._data
    tables = fm._tables
    if "glyf" not in tables or "loca" not in tables:
        return b""
    glyf_off, glyf_len = tables["glyf"]
    start = _read_loca(fm, gid)
    end = _read_loca(fm, gid + 1) if gid + 1 <= fm.num_glyphs else start
    if start >= end:
        return b""
    return data[glyf_off + start:glyf_off + end]


def _parse_simple_glyph(raw: bytes) -> list:
    """P17.2 — parse a simple glyf record into a list of ``(x, y, on_curve)``.

    Coordinates are in font units (y-up).  The TTF coordinate encoding:
    - xShort / yShort: signed 8-bit delta (bit 7 = sign).
    - xLong / yLong: unsigned 16-bit value; the 0x8000 bit means negative.
    Each point's delta is relative to the previous point's coordinate.
    """
    if len(raw) < 10:
        return []
    num_contours = struct.unpack(">h", raw[:2])[0]
    if num_contours <= 0:
        return []
    # P17.2 — simple-glyph layout (TTF spec):
    #   offset 0:  numberOfContours
    #   offset 2:  xMin
    #   offset 4:  yMin
    #   offset 6:  xMax
    #   offset 8:  yMax
    #   offset 10: endPtsOfContours[num_contours]  (uint16 each)
    #   ...        instructionCount, flags, coords
    # The total point count is NOT stored; it equals max(endPtsOfContours)+1.
    end_pts_off = 10
    end_pts_raw = raw[end_pts_off:end_pts_off + num_contours * 2]
    end_pts = struct.unpack(f">{num_contours}H", end_pts_raw)
    n = max(end_pts) + 1 if num_contours > 0 else 0
    # End points of contours (n points total, num_contours end indices).
    end_pts = struct.unpack(f">{num_contours}H", raw[12:12 + num_contours * 2])
    # Flags (with 0x40 repetition and 0x00 byte-repeat).
    flag_off = 12 + num_contours * 2
    flags: list = []
    i = flag_off
    while len(flags) < n:
        if i >= len(raw):
            break
        f = raw[i]
        i += 1
        if f == 0x40:
            # P17.2 — 0x40: next byte is a count of repetitions of f (including itself).
            if i >= len(raw):
                break
            rep = raw[i]
            i += 1
            flags.extend([0x40] * rep)
        elif f == 0:
            # 0x00: next byte is a count of repetitions of the previous flag.
            if i >= len(raw):
                break
            rep = raw[i]
            i += 1
            prev = flags[-1] if flags else 0
            flags.extend([prev] * rep)
        else:
            flags.append(f)
    # P17.2 — TTF spec: for xLong / yLong, if the value has the 0x8000
    # bit set, subtract it from the running coordinate (the value is
    # *signed* in two's-complement 16-bit form).
    #     xv = unsigned16;  if xv >= 0x8000: x -= (xv - 0x10000)
    #     else:             x -= xv
    # For xShort / yShort: signed 8-bit delta, add directly.
    pts: list = []
    x = 0
    y = 0
    for k in range(n):
        if k >= len(flags):
            break
        flag = flags[k]
        on_curve = bool(flag & 0x02)
        # x coordinate.
        if flag & 0x01:  # xShort: signed 8-bit delta.
            if i >= len(raw):
                break
            dx = raw[i]
            if dx >= 0x80:
                dx -= 0x100
            x += dx
            i += 1
        else:  # xLong: 16-bit; 0x8000 bit = negative.
            if i + 2 > len(raw):
                break
            xv = struct.unpack(">H", raw[i:i + 2])[0]
            if xv >= 0x8000:
                # Two's complement: subtract (xv - 0x10000) → x += 0x10000 - xv
                x += 0x10000 - xv
            else:
                x -= xv
            i += 2
        # y coordinate.
        if flag & 0x10:  # yShort: signed 8-bit delta.
            if i >= len(raw):
                break
            dy = raw[i]
            if dy >= 0x80:
                dy -= 0x100
            y += dy
            i += 1
        else:  # yLong: 16-bit; 0x8000 bit = negative.
            if i + 2 > len(raw):
                break
            yv = struct.unpack(">H", raw[i:i + 2])[0]
            if yv >= 0x8000:
                y += 0x10000 - yv
            else:
                y -= yv
            i += 2
        pts.append((x, y, on_curve))
    return pts


def _parse_composite_glyph(raw: bytes, fm: FontMetrics, depth: int = 0) -> list:
    """P17.2 — parse a composite glyf record, recursively extracting
    component outlines (transformed to the parent coordinate system).

    Returns a flat list of ``(x, y, on_curve)`` in font units.
    """
    if depth > 10:
        return []
    if len(raw) < 4:
        return []
    num_components = struct.unpack(">H", raw[:2])[0]
    off = 4
    all_pts: list = []
    for _ in range(num_components):
        if off + 4 > len(raw):
            break
        flags = struct.unpack(">H", raw[off:off + 2])[0]
        comp_gid = struct.unpack(">H", raw[off + 2:off + 4])[0]
        off += 4
        x = y = 0.0
        scale_x = scale_y = 1.0
        scale_xy = 0.0
        # P17.2 — component translation.
        if flags & 0x0001:
            if off + 4 > len(raw):
                break
            x = struct.unpack(">h", raw[off:off + 2])[0]
            y = struct.unpack(">h", raw[off + 2:off + 4])[0]
            off += 4
        else:
            if off + 4 > len(raw):
                break
            x = struct.unpack(">h", raw[off:off + 2])[0] / 16.0
            y = struct.unpack(">h", raw[off + 2:off + 4])[0] / 16.0
            off += 4
        # P17.2 — component scale.
        if flags & 0x0008:
            if off + 4 > len(raw):
                break
            scale_x = struct.unpack(">h", raw[off:off + 2])[0] / 16384.0
            scale_y = struct.unpack(">h", raw[off + 2:off + 4])[0] / 16384.0
            off += 4
        elif flags & 0x0040:
            if off + 4 > len(raw):
                break
            scale_x = struct.unpack(">h", raw[off:off + 2])[0] / 16384.0
            scale_y = struct.unpack(">h", raw[off + 2:off + 4])[0] / 16384.0
            off += 4
        elif flags & 0x0080:
            if off + 8 > len(raw):
                break
            mxx = struct.unpack(">h", raw[off:off + 2])[0] / 16384.0
            myx = struct.unpack(">h", raw[off + 2:off + 4])[0] / 16384.0
            mxy = struct.unpack(">h", raw[off + 4:off + 6])[0] / 16384.0
            myy = struct.unpack(">h", raw[off + 6:off + 8])[0] / 16384.0
            scale_x = mxx
            scale_y = myy
            scale_xy = mxy
            off += 8
        # P17.2 — skip additional words if flagged.
        if (flags & 0x0100) and off + 2 <= len(raw):
            off += 2  # additional words (ignore)
        # Read the component glyph's outline.
        comp_raw = _read_glyf(fm, comp_gid)
        if not comp_raw:
            continue
        comp_num_contours = struct.unpack(">h", comp_raw[:2])[0] if len(comp_raw) >= 2 else 0
        if comp_num_contours == 0:
            continue
        if comp_num_contours > 0:
            comp_pts = _parse_simple_glyph(comp_raw)
        else:
            comp_pts = _parse_composite_glyph(comp_raw, fm, depth + 1) if depth < 10 else []
        # Transform to parent coordinate system.
        for px, py, oc in comp_pts:
            tx = px * scale_x + py * scale_xy + x
            ty = px * scale_xy + py * scale_y + y
            all_pts.append((int(tx), int(ty), oc))
    return all_pts


def _extract_glyph(fm: FontMetrics, gid: int, _depth: int = 0) -> Optional[GlyphOutline]:
    """P17.2 — extract the outline for glyph index ``gid``."""
    if gid < 0 or gid >= fm.num_glyphs:
        return None
    if _depth > 10:
        return GlyphOutline(points=[], is_empty=True)
    raw = _read_glyf(fm, gid)
    if not raw:
        return GlyphOutline(points=[], is_empty=True)
    num_contours = struct.unpack(">h", raw[:2])[0]
    if num_contours == 0:
        return GlyphOutline(points=[], is_empty=True)
    if num_contours > 0:
        pts = _parse_simple_glyph(raw)
        return GlyphOutline(points=pts, is_empty=not pts, is_composite=False)
    # Composite glyph.
    pts = _parse_composite_glyph(raw, fm, _depth)
    return GlyphOutline(points=pts, is_empty=not pts, is_composite=True)


# ---------------------------------------------------------------------------
# P17.4 — @font-face upgrade: real font file loading + font-weight switching
# ---------------------------------------------------------------------------

_font_cache: dict = {}

# Font-weight → family suffix convention for the toy engine.
_WEIGHT_SUFFIX = {
    400: "",          # normal
    500: "",
    600: "",
    700: "bold",     # arial.ttf → arialbd.ttf
}

# Font-family → file base name (toy: a small built-in registry).
_FAMILY_FILES = {
    "serif": "times",
    "monospace": "cour",
    "sans-serif": "arial",
    "arial": "arial",
    "times": "times",
    "times new roman": "times",
    "courier": "cour",
    "courier new": "cour",
    "georgia": "georgia",
    "verdana": "verdana",
    "tahoma": "tahoma",
}

# P17 — fallback families: when the primary font file is missing, try these
# in order (the test suite ships arial.ttf / arialbd.ttf in fonts/).
_FAMILY_FALLBACKS = {
    "serif": ["times", "georgia", "arial"],
    "monospace": ["cour", "arial"],
}


def _find_font_file(family: str, bold: bool, search_dirs: list) -> Optional[str]:
    """P17.4 — locate a TTF file on disk for a font family + weight.

    Searches each directory for ``<base>.ttf`` (normal) or
    ``<base>bd.ttf`` / ``<base>b.ttf`` (bold).  When the primary base
    name is missing, falls back through :data:`_FAMILY_FALLBACKS`
    (e.g. serif → times → georgia → arial).  Returns the first match,
    or ``None`` if not found.
    """
    import os
    base = _FAMILY_FILES.get(family, family.lower().split(" ")[0])
    # Build an ordered candidate list: primary base, then fallbacks.
    bases = [base] + _FAMILY_FALLBACKS.get(family, [])
    seen = set()
    ordered = []
    for b in bases:
        if b not in seen:
            seen.add(b)
            ordered.append(b)
    suffix = "bd" if bold else ""
    for b in ordered:
        candidates = [f"{b}{suffix}.ttf", f"{b}{suffix}.otf", f"{b}.ttf", f"{b}.otf"]
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            for cand in candidates:
                p = os.path.join(d, cand)
                if os.path.isfile(p):
                    return p
    return None


def load_font_for_family(
    family: str,
    font_px: float,
    bold: bool = False,
    search_dirs: Optional[list] = None,
) -> Optional[FontMetrics]:
    """P17.4 — load a real TTF/OTF for a CSS ``font-family`` + weight.

    Returns a :class:`FontMetrics` on success, or ``None`` when no font
    file is available (caller falls back to the built-in 8×8 bitmap).
    Results are cached by ``(path, bold)``.
    """
    import os
    dirs = search_dirs if search_dirs is not None else []
    # Also look next to the script and in the current directory.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search = list(dirs) + [script_dir, script_dir + "/fonts", "."]

    key = None
    # First check cache by resolved path.
    path = _find_font_file(family, bold, search)
    if path is None:
        return None
    key = (path, bold)
    if key in _font_cache:
        return _font_cache[key]
    try:
        fm = load_ttf(path)
    except TTFError:
        return None
    _font_cache[key] = fm
    return fm

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

    ``points`` is a flat list of ``(x, y, on_curve)`` tuples in font-unit
    coordinates (legacy view, kept for compatibility).  ``contours`` is
    the same data grouped by TTF contour — a list of lists, where each
    inner list is one closed contour (TTF winding: the even-odd rule
    fills a glyph's holes correctly when each contour is filled
    separately, and overlapping regions cancel).  For composite glyphs
    the outlines of all components are merged into their respective
    contour groups (transformed to the parent coordinate system).
    ``is_empty`` is True for glyphs with no contours (e.g. space).
    ``bbox`` is the glyph's bounding box ``(xMin, yMin, xMax, yMax)``
    in font units, used by P20 to validate parsed coordinates (outlines
    whose points fall outside the bbox are rejected and the caller
    falls back to bitmap rendering).
    """
    points: list = field(default_factory=list)
    contours: list = field(default_factory=list)  # T01 — list of (x,y,on_curve) lists
    is_empty: bool = False
    is_composite: bool = False
    bbox: tuple = (0, 0, 0, 0)

    def flat(self) -> list:
        """T01 — return the legacy flat point list (all contours
        concatenated, in the same order ``contours`` would produce)."""
        if not self.contours:
            return list(self.points)
        out: list = []
        for c in self.contours:
            out.extend(c)
        return out


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
    # P33.R1 — per-glyph outline cache (gid -> GlyphOutline).
    # Unbounded dicts are forbidden by the project's resource
    # governance; the cache is capped at 4096 entries (the advance
    # array cap) so a CJK face with 28k glyphs does not hold the
    # outline of every character in RAM.  The cache is keyed by
    # glyph id (not character) so the same glyph is never parsed
    # twice even if multiple characters map to it.
    _outline_cache: dict = field(default_factory=dict)
    _outline_cache_max: int = 4096

    def advance_width(self, ch: str, font_px: float) -> float:
        """P17 — advance width in pixels for a character at ``font_px``.

        Uses the real ``hmtx`` value scaled from units-per-EM to pixels.
        P33.R1 — clamps the result to a sensible range: a CJK font with
        units_per_em=256 reports full-width advances of 256 (i.e. 1em);
        the clamp keeps pathological values (corrupt hmtx / huge unit
        counts) from making the layout blow up.  Falls back to a
        monospace estimate if the char is missing from the font.
        """
        if not self.advances or self.units_per_em == 0:
            return 8.0 * max(1, int(font_px / 8.0))  # legacy fallback
        code = ord(ch) if ch else 0
        gid = self.cmap.get(code, 0)
        # P33.R1 — the advance array is capped at 4096 entries; glyphs
        # beyond the cap reuse the last metric (the common case is CJK
        # where most glyphs share the same advance).
        if gid >= len(self.advances):
            gid = len(self.advances) - 1
        if gid < 0:
            return int(font_px)
        units = self.advances[gid]
        # units-per-EM → pixels at the requested font size.
        px = float(units) * (font_px / self.units_per_em)
        # P33.R1 — clamp to [0, 3*font_px] so a corrupt hmtx or an
        # oversized units_per_em cannot push the advance past 3em.
        return min(px, 3.0 * font_px)

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

        P33.R1 — results are cached per-glyph-id in
        ``_outline_cache`` (capped at 4096 entries) so a document
        that repeats a character does not re-read the glyf record.
        """
        if not self._data or not self._tables:
            return None
        code = ord(ch) if ch else 0
        gid = self.cmap.get(code, 0)
        if gid == 0 or gid >= self.num_glyphs:
            return None
        cached = self._outline_cache.get(gid)
        if cached is not None:
            return cached
        out = _extract_glyph(self, gid)
        # P17.3 — an empty glyph (space, ligature placeholder, etc.)
        # may be a composite with many degenerate points; treat it as
        # "empty" when all points cluster within a 1-unit box.
        if out is not None and out.points:
            pts = out.flat()
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                if (max(xs) - min(xs)) < 2 and (max(ys) - min(ys)) < 2:
                    out.is_empty = True
        if out is not None and len(self._outline_cache) < self._outline_cache_max:
            self._outline_cache[gid] = out
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
    # Collect candidate subtable offsets: prefer platform 3 / encoding 1
    # (Windows Unicode BMP, format 4), then platform 0 / encoding 3, then
    # any subtable.  A candidate that parses to zero mappings (e.g. a
    # zero-segCount padding table some fonts ship) is skipped so a
    # sibling subtable with the real mapping can be used.
    #
    # P33.R1 — Windows CJK fonts (simsun / simhei / msyh) sometimes carry
    # a malformed cmap header (numSubtables reads as a huge value) yet the
    # first few registry records are still valid.  Cap the record walk at
    # the table size so we never index past the end, and trust only
    # records whose platform/encoding are plausible TTF values (0,1,3,4,5
    # / 0,1,3,4,10).
    candidates: list = []
    max_records = max(0, (len(cmap_bytes) - 4) // 8)
    so = 4
    rels: list = []
    for i in range(min(num_subtables, max_records)):
        if so + 8 > len(cmap_bytes):
            break
        platform, encoding, rel_off = struct.unpack(">HHI", cmap_bytes[so:so + 8])
        so += 8
        if (platform in (0, 1, 3, 4, 5)
                and encoding in (0, 1, 3, 4, 10)
                and rel_off < len(cmap_bytes)):
            rels.append((platform, encoding, rel_off))
    for plat, enc, rel in rels:
        if (plat, enc) == (3, 1):
            candidates.append(rel)
    for plat, enc, rel in rels:
        if (plat, enc) == (0, 3) and rel not in candidates:
            candidates.append(rel)
    for plat, enc, rel in rels:
        if (plat, enc) not in ((3, 1), (0, 3)) and rel not in candidates:
            candidates.append(rel)

    for preferred_rel in candidates:
        sub = cmap_bytes[preferred_rel:]
        if len(sub) < 4:
            continue
        parsed = _parse_cmap_subtable(sub)
        if parsed:
            result.update(parsed)
            return result

    # P33.R1 — fallback for corrupt-registry fonts: scan the whole table
    # for subtable magics (format 4 / format 12) and try each hit.
    for preferred_rel in _scan_cmap_subtables(cmap_bytes):
        sub = cmap_bytes[preferred_rel:]
        if len(sub) < 4:
            continue
        parsed = _parse_cmap_subtable(sub)
        if parsed:
            result.update(parsed)
            return result
    return result


def _scan_cmap_subtables(cmap_bytes: bytes) -> list:
    """P33.R1 — find candidate subtable start offsets by scanning for the
    format-4 / format-12 magics, for fonts whose cmap registry header is
    corrupt.  Returns offsets in descending subtable-length order (the
    real CJK mapping is usually the largest subtable)."""
    hits: list = []
    n = len(cmap_bytes)
    for i in range(n - 8):
        fmt = struct.unpack(">H", cmap_bytes[i:i + 2])[0]
        if fmt not in (4, 12):
            continue
        length = struct.unpack(">H", cmap_bytes[i + 2:i + 4])[0]
        if fmt == 4:
            segx2 = struct.unpack(">H", cmap_bytes[i + 6:i + 8])[0]
            if not (4 <= segx2 <= 4096 and segx2 % 2 == 0):
                continue
        if not (4 <= length <= max(n - i, 4096)):
            continue
        hits.append((i, length))
    hits.sort(key=lambda t: -t[1])
    out: list = []
    last = None
    for pos, length in hits:
        if last is not None and abs(pos - last) < 32:
            continue
        out.append(pos)
        last = pos
        if len(out) >= 8:
            break
    return out


def _parse_cmap_subtable(sub: bytes) -> dict:
    """P33.R1 — parse one cmap subtable into ``{codepoint: glyph_id}``.

    Empty / malformed subtables return ``{}`` so the candidate loop in
    :func:`_parse_cmap` can fall through to the next subtable.
    """
    result: dict = {}
    if len(sub) < 4:
        return result
    fmt, length = struct.unpack(">HH", sub[:4])
    sub_len = length if length > 0 else len(sub)
    if fmt == 4:
        # Format 4 (segmented).  Subtable layout (all uint16, big-endian):
        #   0  format
        #   2  length
        #   4  language
        #   6  segCountX2
        #   8  searchRange
        #   10 entrySelector
        #   12 rangeShift
        #   14 endCode[segCount]
        #   14 + segCount*2 : reservedPad (1 uint16)
        #   16 + segCount*2 : startCode[segCount]
        #   16 + segCount*4 : idDelta[segCount]
        #   16 + segCount*6 : idRangeOffset[segCount]
        seg_count_x2 = struct.unpack(">H", sub[6:8])[0]
        if seg_count_x2 == 0:
            return result
        seg_count = seg_count_x2 // 2
        # TTF cmap format 4 subtable layout (all offsets relative to the
        # subtable start; segCount = seg_count_x2 // 2):
        #   0  format (2)
        #   2  length (2)
        #   4  language (2)
        #   6  segCountX2 (2)
        #   8  searchRange (2)
        #   10 entrySelector (2)
        #   12 rangeShift (2)
        #   14 endCode[segCount]            -> 14 + segCount*2
        #   14 + segCount*2 : reservedPad   -> +2
        #   16 + segCount*2 : startCode[segCount]
        #   16 + segCount*4 : idDelta[segCount]
        #   16 + segCount*6 : idRangeOffset[segCount]
        end_codes_off = 14
        start_codes_off = 16 + seg_count * 2
        id_delta_off = 16 + seg_count * 4
        id_range_off = 16 + seg_count * 6
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
            # P34.7 — bound the per-group scan: a corrupt group (e.g.
            # start_c=0, end_c=0x10FFFF) would materialize a 1 M-entry
            # dict.  A legitimate CJK group covers at most ~20k code-
            # points; cap the per-group span at 50k so a corrupt
            # subtable cannot OOM the whole process.
            if end_c - start_c > 50000:
                continue
            for c in range(start_c, end_c + 1):
                result[c] = start_g + (c - start_c)
    elif fmt == 14:
        # Format 14 (Unicode variation sequences).  Skip for toy.
        pass
    return result


def _load_one_ttf(data: bytes, limits=None) -> FontMetrics:
    """P33.R1 — parse a *single* TrueType font image (the sfnt record)
    out of ``data``.  Shared by :func:`load_ttf` (whole file) and
    :func:`load_ttc` (one face of a collection).

    R7-S04 第 4 点 — 可选 *limits*（:class:`PageResourceLimits`）：读
    ``num_tables`` 后、构建表目录**之前**做 ``admit(FONT_COMPONENTS,
    num_tables)`` 分配前防护，超限抛 :class:`TTFError`（调用方按加载
    失败回退位图字体，不崩）。记账挂在**传入的** *limits* 上（跨
    face / 跨文档累加），*limits* 为 None 时退化为仅用默认上限做
    单字体判定（不跨调用串账，向后兼容）。
    """
    if len(data) < 6:
        raise TTFError(f"font file too small ({len(data)} bytes)")
    sfnt_version, num_tables = struct.unpack(">IH", data[:6])
    # R7-S04 第 4 点 — 字体表/组件独立限额（分配前防护）。
    try:
        from toy_browser.network.page_limits import (
            PageResourceLimits as _PL, PageDimension as _PD,
        )
        _lim = limits if limits is not None else _PL()
        if not _lim.admit(_PD.FONT_COMPONENTS, num_tables):
            raise TTFError(f"font table count {num_tables} exceeds "
                           f"{_lim.limit(_PD.FONT_COMPONENTS)} limit")
        _lim.charge(_PD.FONT_COMPONENTS, num_tables)
    except ImportError:
        pass  # page_limits 不可用（测试裁剪）时跳过限额，不破坏既有路径
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
    # P33.R1 — numGlyphs lives in the low 16 bits of head[4:6] on the
    # TrueType head table, but we read it from maxp below (authoritative).

    # hhea
    hhea = _read_table(data, tables, "hhea")
    if len(hhea) < 36:
        raise TTFError("hhea table too short")
    ascent = struct.unpack(">h", hhea[4:6])[0]
    descent = struct.unpack(">h", hhea[6:8])[0]
    line_gap = struct.unpack(">h", hhea[8:10])[0]
    num_h_metrics = struct.unpack(">H", hhea[34:36])[0]

    # maxp → numGlyphs (version 0.5 has only numGlyphs; 1.0 has more).
    maxp = _read_table(data, tables, "maxp")
    if len(maxp) < 6:
        raise TTFError("maxp table too short")
    num_glyphs = struct.unpack(">H", maxp[4:6])[0]
    if num_glyphs < 1:
        # Fallback: the head table's numGlyphs (head[4:6]).
        if len(head) >= 6:
            num_glyphs = struct.unpack(">H", head[4:6])[0]
    if num_glyphs < 1:
        raise TTFError("font reports zero glyphs")

    # hmtx — glyph metrics: advance width + left side bearing.
    # The table stores numHMetrics pairs; the remaining glyphs share the
    # LAST advance width (TTF spec).  We materialize the full array so
    # callers can index by glyph id.
    #
    # P33.R1 — resource guard: CJK fonts (simhei / msyh) can have
    # numGlyphs ≈ 65k while hmtx holds only a few hundred metrics.
    # Materializing the full array would be 65k × 2 lists — fine, but
    # only do it when the requested glyph range actually needs it.
    # For the toy, cap the materialized advance array at 4096 entries;
    # glyphs beyond the cap reuse the last metric (the common case is
    # CJK where most glyphs share the same advance).
    hmtx = _read_table(data, tables, "hmtx")
    num_h_metrics = struct.unpack(">H", hhea[34:36])[0]
    # P33.R1 — clamp numHMetrics to the actual table size (some fonts
    # report a numHMetrics that exceeds the hmtx table length).
    hmtx_pairs = len(hmtx) // 4
    if num_h_metrics > hmtx_pairs:
        num_h_metrics = hmtx_pairs
    # P33.R1 — cap the materialized advance array to keep memory bounded.
    _ADV_CAP = 4096
    cap = min(num_glyphs, _ADV_CAP)
    advances: list = []
    lsbs: list = []
    for i in range(min(num_h_metrics, cap)):
        off = i * 4
        if off + 4 > len(hmtx):
            break
        adv, lsb = struct.unpack(">Hh", hmtx[off:off + 4])
        advances.append(adv)
        lsbs.append(lsb)
    # Pad up to the cap with the last advance width (or units_per_em).
    if len(advances) < cap:
        last_adv = advances[-1] if advances else units_per_em
        advances.extend([last_adv] * (cap - len(advances)))
        lsbs.extend([0] * (cap - len(lsbs)))

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


def load_ttf(path: str, limits=None) -> FontMetrics:
    """P17 — parse a TTF/OTF file into a :class:`FontMetrics`.

    Raises :class:`TTFError` on unreadable / malformed files so callers
    can fall back to the built-in bitmap font.

    R7-S04 第 4 点 — 可选 *limits* 透传给 :func:`_load_one_ttf`（字体
    表/组件限额记账挂到传入的页面限额实例，跨调用累加）。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise TTFError(f"cannot read font file {path!r}: {e}")
    return _load_one_ttf(data, limits)


# ---------------------------------------------------------------------------
# P33.R1 — TTC (TrueType Collection) support: Windows ships CJK fonts
# (simsun.ttc / msyh.ttc / simhei families) as *collections* containing
# several faces.  The TTC header:
#   magic 'ttcf' (4), version (4), nFonts (4), offsets[n] (4 each).
# Each offset points at an independent sfnt record inside the file.
# ---------------------------------------------------------------------------

def is_ttc(path: str) -> bool:
    """True when *path* is a TrueType Collection file ('ttcf' magic)."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"ttcf"
    except OSError:
        return False


def ttc_face_count(path: str) -> int:
    """Number of faces in a TTC, or 0 when the file is not a valid TTC."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return 0
    if len(head) < 12 or head[:4] != b"ttcf":
        return 0
    return struct.unpack(">L", head[8:12])[0]


def load_ttc(path: str, face: int = 0, limits=None) -> FontMetrics:
    """P33.R1 — load the *face*-th font of a TrueType Collection.

    Falls back to the first face when an index out of range is given, so
    callers can probe a collection with face=0 safely.  Raises
    :class:`TTFError` for non-TTC inputs or unreadable files.

    R7-S04 第 4 点 — 可选 *limits* 透传给 :func:`_load_one_ttf`（跨
    face 累加记账）。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise TTFError(f"cannot read font file {path!r}: {e}")
    if len(data) < 12 or data[:4] != b"ttcf":
        raise TTFError(f"{path!r} is not a TrueType collection")
    n_fonts = struct.unpack(">L", data[8:12])[0]
    if n_fonts < 1:
        raise TTFError("TTC reports zero faces")
    face = max(0, min(face, n_fonts - 1))
    off = struct.unpack(">L", data[12 + face * 4:16 + face * 4])[0]
    if off >= len(data):
        raise TTFError(f"TTC face {face} offset out of range")
    return _load_one_ttf(data[off:], limits)


def load_face_best_cjk(path: str) -> FontMetrics:
    """P33.R1 — pick the most CJK-capable face of a collection.

    Scans every face (cheap cmap count) and returns the face whose cmap
    has the most CJK Unified Ideographs (U+4E00..U+9FFF).  This lets a
    caller register a single .ttc entry that actually renders Chinese
    even when the first face is Latin-only.  Faces whose cmap parses to
    zero CJK codepoints are rejected so a Latin-only collection still
    yields a usable CJK face when one exists.
    """
    n = ttc_face_count(path)
    if n == 0:
        raise TTFError(f"{path!r} is not a TrueType collection")
    best_fm: Optional[FontMetrics] = None
    best_score = 0
    for i in range(n):
        try:
            fm = load_ttc(path, i)
        except (TTFError, MemoryError, OSError, struct.error,
                ValueError, IndexError):
            continue
        # P33.R1 — perf: bound the CJK scan so a giant cmap does not make
        # face-selection O(cmap) (the cmap dict can have 60k+ entries).
        score = 0
        for c in fm.cmap:
            if 0x4E00 <= c <= 0x9FFF:
                score += 1
                if score >= 500:  # good enough — stop counting
                    break
        if score > best_score:
            best_score = score
            best_fm = fm
    if best_fm is None:
        raise TTFError("no usable CJK face in collection")
    return best_fm


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
    """Read the raw glyf bytes for glyph index ``gid``.

    P33.R1 — every returned slice is clamped to the actual glyf table
    length, so a corrupt loca table (out-of-range offsets, or a face
    slice in a TTC that extends past the next face) can never read into
    a neighbouring face or off the end of the buffer.
    """
    data = fm._data
    tables = fm._tables
    if "glyf" not in tables or "loca" not in tables:
        return b""
    glyf_off, glyf_len = tables["glyf"]
    glyf_end = glyf_off + glyf_len
    start = _read_loca(fm, gid)
    end = _read_loca(fm, gid + 1) if gid + 1 <= fm.num_glyphs else start
    if start >= end or start >= glyf_len:
        return b""
    end = min(end, glyf_len)
    return data[glyf_off + start:glyf_off + end]


def _parse_simple_glyph(raw: bytes) -> list:
    """P17.2 — parse a simple glyf record into a list of contours.

    T01 — returns ``list[list[(x, y, on_curve)]]`` (one inner list per
    TTF contour), NOT a flat point list.  The caller
    (:func:`_extract_glyph`) stores the grouped form on
    :class:`GlyphOutline.contours`; the legacy flat view
    (:class:`GlyphOutline.points` / :meth:`GlyphOutline.flat`) is
    reassembled from the groups so P20 test contracts that assert on
    the flat list keep working.

    Coordinates are in font units (y-up).  The TTF coordinate encoding:
    - xShort / yShort: signed 8-bit delta (bit 7 = sign).
    - xLong / yLong: unsigned 16-bit value; the 0x8000 bit means negative.
    Each point's delta is relative to the previous point's coordinate.

    P33.R1 — bounds-checked: a truncated / malformed glyf record used to
    crash struct.unpack with "requires a buffer of N bytes"; now every
    struct access is guarded so a broken component yields an empty
    outline (the caller falls back to bitmap) instead of a traceback.
    """
    if len(raw) < 10:
        return []
    num_contours = struct.unpack(">h", raw[:2])[0]
    if num_contours <= 0:
        return []
    # P20 — simple-glyph layout (TTF spec):
    #   offset 0:  numberOfContours (int16)
    #   offset 2:  xMin, yMin, xMax, yMax (4 x int16)
    #   offset 10: endPtsOfContours[num_contours] (uint16 each)
    #   then:      instructionCount (uint16)  @ offset 10 + num_contours*2
    #   then:      flags (1 byte per point, with 0x40/0x00 repeat)
    #                @ offset 12 + num_contours*2
    #   then:      coordinate deltas (xShort/yShort 8-bit, xLong/yLong 16-bit)
    #   then:      hinting program (instructionCount bytes) — NOT parsed.
    # The total point count n = max(endPtsOfContours) + 1.
    end_pts_off = 10
    end_pts_len = end_pts_off + num_contours * 2
    if end_pts_len > len(raw):
        return []
    end_pts = struct.unpack(f">{num_contours}H",
                            raw[end_pts_off:end_pts_len])
    n = max(end_pts) + 1 if num_contours > 0 else 0
    # instructionCount sits right after endPtsOfContours.
    inst_off = 10 + num_contours * 2
    if inst_off + 2 > len(raw):
        return []
    # P17.2 — the TTF spec puts the hinting program BETWEEN
    # instructionCount and the point flags.  instCount can be large
    # (650 bytes in the shipped arial.ttf fixture); skip it before
    # reading the flags, otherwise the flags section is read from
    # inside the instruction stream and every coordinate is garbage.
    instruction_count = struct.unpack(">H", raw[inst_off:inst_off + 2])[0]
    # P33.R1 — clamp instCount to the record: a corrupt value must not
    # push the flags section past the end of the glyf record.
    if inst_off + 2 + instruction_count > len(raw):
        instruction_count = max(0, len(raw) - inst_off - 2)
    # Flags start right after the hinting program.
    flag_off = inst_off + 2 + instruction_count
    if flag_off >= len(raw):
        return []
    flags: list = []
    i = flag_off
    while len(flags) < n:
        if i >= len(raw):
            break
        f = raw[i]
        i += 1
        flags.append(f)
        # P33.R1 — FreeType repeat semantics: a tag byte with bit 0x08
        # (REPEAT_FLAG) set is followed by a count byte; the tag is
        # emitted count more times.  0x00 and 0x40 are the two values
        # that actually use the repeat form in real fonts (0x40 =
        # overlap, 0x00 = off-curve with no vectors), but any tag with
        # the 0x08 bit is a repeat marker.
        if f & 0x08:
            if i >= len(raw):
                break
            rep = raw[i]
            i += 1
            flags.extend([f] * rep)
    # P20 / P33.R1 — TTF glyf point tags (FreeType ttgload.c, verified
    # against the shipped simhei / arial outlines):
    #   0x01  on-curve point
    #   0x02  x is a 1-byte (short) vector — otherwise 2-byte (long)
    #   0x04  y is a 1-byte (short) vector — otherwise 2-byte (long)
    #   0x08  REPEAT_FLAG — this byte + the next are a repeat pair
    #   0x10  x-positive / same-x (meaning depends on 0x02)
    #   0x20  y-positive / same-y (meaning depends on 0x04)
    #   0x40  overlap
    # Repeat encoding: a tag byte with the REPEAT_FLAG (0x08) bit set
    # is followed by a count byte; the tag is emitted count times.
    # 0x00/0x40 as the *previous* tag are handled the same way.
    #
    # Coordinate reading (two passes, x then y — NOT interleaved):
    #   short: 1 byte, unsigned magnitude; 0x10/0x20 bit selects the
    #          sign (clear = negative).
    #   long:   2 bytes, signed 16-bit big-endian.
    #   same:   no bytes consumed (coordinate repeats the previous one).
    pts: list = []
    x = 0
    y = 0
    # P33.R1 — two passes (x then y, NOT interleaved).  The x pass
    # records each point's absolute x in a list; the y pass pairs it
    # with the running y to emit the final (x, y, on_curve) triples.
    x_vals: list = []
    # --- x pass ---
    for k in range(n):
        if k >= len(flags):
            break
        f = flags[k]
        if f & 0x02:  # xShortVector
            if i >= len(raw):
                break
            delta = raw[i]
            i += 1
            if not (f & 0x10):  # xPositive not set → negative
                delta = -delta
            x += delta
        elif not (f & 0x10):  # long vector (same-x means 0x10 set)
            if i + 2 > len(raw):
                break
            delta = struct.unpack(">h", raw[i:i + 2])[0]
            i += 2
            x += delta
        # else: SAME_X (0x10 with long vector) — x unchanged.
        x_vals.append(x)
    # --- y pass ---
    all_pts: list = []
    for k in range(n):
        if k >= len(flags):
            break
        f = flags[k]
        if f & 0x04:  # yShortVector
            if i >= len(raw):
                break
            delta = raw[i]
            i += 1
            if not (f & 0x20):  # yPositive not set → negative
                delta = -delta
            y += delta
        elif not (f & 0x20):  # long vector (same-y means 0x20 set)
            if i + 2 > len(raw):
                break
            delta = struct.unpack(">h", raw[i:i + 2])[0]
            i += 2
            y += delta
        # else: SAME_Y (0x20 with long vector) — y unchanged.
        px = x_vals[k] if k < len(x_vals) else x
        all_pts.append((px, y, bool(flags[k] & 0x01)))
    # T01 — regroup the flat point list by contour boundaries
    # (endPtsOfContours holds the global index of each contour's last
    # point).  Each contour is emitted as its own sub-list so the
    # rasterizer can apply the even-odd rule contour-by-contour.
    contours: list = []
    start = 0
    for end in end_pts:
        contours.append(all_pts[start:end + 1])
        start = end + 1
    return contours


# P33.R1 — cycle guard for composite glyph parsing: the set of glyph ids
# currently on the recursion stack.  A composite glyph whose component
# chain loops back onto an ancestor is skipped instead of recursing
# forever.  Lives at module scope so every recursive call shares it.
_COMPOSITE_CYCLE_GUARD: set = set()


def _parse_composite_glyph(raw: bytes, fm: FontMetrics, depth: int = 0) -> list:
    """P17.2 — parse a composite glyf record, recursively extracting
    component outlines (transformed to the parent coordinate system).

    T01 — returns a list of contours (``list[list[(x, y, on_curve)]]``),
    one inner list per TTF contour, matching :func:`_parse_simple_glyph`.
    Each component's contours are transformed and appended to the
    result as separate contour groups.
    """
    if depth > 10:
        return []
    if len(raw) < 4:
        return []
    num_components = struct.unpack(">H", raw[:2])[0]
    off = 4
    contours: list = []
    # P33.R1 — self-reference / cycle guard: some fonts ship composite
    # glyphs whose component chain loops back onto an ancestor.  Track the
    # glyph ids already on the stack; when a cycle is detected the
    # remaining components are skipped instead of recursing forever.
    # A corrupt record (num_components absurdly large, or no room for
    # even one 4-byte component header) yields no points.
    if num_components > 128:
        return contours
    for _ in range(num_components):
        if off + 4 > len(raw):
            break
        flags = struct.unpack(">H", raw[off:off + 2])[0]
        comp_gid = struct.unpack(">H", raw[off + 2:off + 4])[0]
        off += 4
        # P33.R1 — a component that is the parent glyph itself (or an
        # ancestor) is a malformed cycle; skip it.
        if comp_gid in _COMPOSITE_CYCLE_GUARD:
            continue
        x = y = 0.0
        scale_x = scale_y = 1.0
        scale_xy = 0.0
        # P33.R1 — component transform (FreeType ttgload.c, verified
        # against the OpenJDK fork):
        #   0x0002 ARGS_ARE_XY_VALUES — x/y are int8 (signed bytes)
        #   0x0001 ARGS_ARE_WORDS     — x/y are int16 (unambiguates)
        #   0x0008 WE_HAVE_A_SCALE    — one int16, 16.16 (x=y)
        #   0x0040 WE_HAVE_AN_XY_SCALE— two int16, 16.16
        #   0x0080 WE_HAVE_A_2X2      — four int16, 16.16
        #   0x0100 WE_HAVE_INSTR      — int16 count + instruction bytes
        if flags & 0x0002:
            if flags & 0x0001:
                # Two int16 words.
                if off + 4 > len(raw):
                    break
                x = struct.unpack(">h", raw[off:off + 2])[0]
                y = struct.unpack(">h", raw[off + 2:off + 4])[0]
                off += 4
            else:
                # Two signed int8 bytes (2.14 fixed-point per spec).
                if off + 2 > len(raw):
                    break
                xb, yb = raw[off], raw[off + 1]
                x = (xb - 0x100) / 16.0 if xb >= 0x80 else float(xb)
                y = (yb - 0x100) / 16.0 if yb >= 0x80 else float(yb)
                off += 2
        # Scale.
        if flags & 0x0008:
            # ONE int16 word, 16.16 fixed-point (x = y).
            if off + 2 > len(raw):
                break
            s = struct.unpack(">h", raw[off:off + 2])[0]
            scale_x = scale_y = s / 65536.0
            off += 2
        elif flags & 0x0040:
            # Two int16 words, 16.16.
            if off + 4 > len(raw):
                break
            scale_x = struct.unpack(">h", raw[off:off + 2])[0] / 65536.0
            scale_y = struct.unpack(">h", raw[off + 2:off + 4])[0] / 65536.0
            off += 4
        elif flags & 0x0080:
            # 2x2 matrix, four int16 words, 16.16.
            if off + 8 > len(raw):
                break
            scale_x = struct.unpack(">h", raw[off:off + 2])[0] / 65536.0
            mxy = struct.unpack(">h", raw[off + 2:off + 4])[0] / 65536.0
            _myx = struct.unpack(">h", raw[off + 4:off + 6])[0] / 65536.0
            scale_y = struct.unpack(">h", raw[off + 6:off + 8])[0] / 65536.0
            scale_xy = mxy
            off += 8
        # P33.R1 — WE_HAVE_INSTR: int16 instruction count + bytes.
        if flags & 0x0100:
            if off + 2 > len(raw):
                break
            n_ins = struct.unpack(">H", raw[off:off + 2])[0]
            off += 2 + n_ins
        # Read the component glyph's outline.
        comp_raw = _read_glyf(fm, comp_gid)
        if not comp_raw:
            continue
        comp_num_contours = struct.unpack(">h", comp_raw[:2])[0] if len(comp_raw) >= 2 else 0
        if comp_num_contours == 0:
            continue
        # P33.R1 — register the component on the cycle guard, then
        # parse; unregistered afterward.
        if comp_num_contours > 0:
            comp_pts = _parse_simple_glyph(comp_raw)
        else:
            _COMPOSITE_CYCLE_GUARD.add(comp_gid)
            try:
                comp_pts = _parse_composite_glyph(comp_raw, fm, depth + 1) \
                    if depth < 10 else []
            finally:
                _COMPOSITE_CYCLE_GUARD.discard(comp_gid)
        # Transform to parent coordinate system.
        # T01 — comp_pts is now a list of contours (each a list of
        # (x, y, on_curve) tuples).  Transform every contour to the
        # parent coordinate system and append them individually.
        for cpts in comp_pts:
            transformed = []
            for px, py, oc in cpts:
                tx = px * scale_x + py * scale_xy + x
                ty = px * scale_xy + py * scale_y + y
                transformed.append((int(tx), int(ty), oc))
            contours.append(transformed)
    return contours


def _extract_glyph(fm: FontMetrics, gid: int, _depth: int = 0) -> Optional[GlyphOutline]:
    """P17.2 — extract the outline for glyph index ``gid``.

    P20 — attaches the glyph's bbox (``xMin, yMin, xMax, yMax`` in font
    units) to the :class:`GlyphOutline` so callers can validate parsed
    coordinates (outlines with points outside the bbox are broken and
    should be rejected).

    T01 — the parser now returns contours (``list[list[(x, y, on_curve)]]``);
    both the legacy flat ``points`` view and the new ``contours`` view are
    populated on the :class:`GlyphOutline`.
    """
    if gid < 0 or gid >= fm.num_glyphs:
        return None
    if _depth > 10:
        return GlyphOutline(points=[], contours=[], is_empty=True)
    raw = _read_glyf(fm, gid)
    if not raw:
        return GlyphOutline(points=[], contours=[], is_empty=True)
    num_contours = struct.unpack(">h", raw[:2])[0]
    if num_contours == 0:
        return GlyphOutline(points=[], contours=[], is_empty=True)
    # P20 — read the glyf bbox for validation.
    bbox = (0, 0, 0, 0)
    if len(raw) >= 10:
        bbox = struct.unpack(">hhhh", raw[2:10])
    if num_contours > 0:
        contours = _parse_simple_glyph(raw)
        flat = [p for c in contours for p in c]
        return GlyphOutline(points=flat, contours=contours,
                           is_empty=not flat,
                           is_composite=False, bbox=bbox)
    # P33.R1 — composite glyphs have num_contours < 0.  A huge negative
    # value (e.g. -18931 = 0xB743) means the record was misread as a
    # composite (the loca offsets or the glyf table are truncated on
    # disk); treat it as empty rather than recursing into garbage.
    if num_contours < -256:
        return GlyphOutline(points=[], contours=[], is_empty=True,
                            is_composite=True, bbox=bbox)
    # Composite glyph.
    # P33.R1 — push this glyph's id onto the cycle guard, parse, pop.
    _COMPOSITE_CYCLE_GUARD.add(gid)
    try:
        contours = _parse_composite_glyph(raw, fm, _depth)
    finally:
        _COMPOSITE_CYCLE_GUARD.discard(gid)
    flat = [p for c in contours for p in c]
    return GlyphOutline(points=flat, contours=contours,
                        is_empty=not flat,
                        is_composite=True, bbox=bbox)


def outline_valid(outline) -> bool:
    """P20 — True if the outline's parsed points fall within its bbox.

    A small tolerance (±1 font unit) is allowed for composite glyphs
    whose transforms can push vertices slightly outside the bbox.
    Outlines that fail this check were parsed with a buggy coordinate
    read and must be rejected (the caller falls back to bitmap).
    """
    if outline is None or outline.is_empty or not outline.points:
        return False
    x_min, y_min, x_max, y_max = outline.bbox
    if x_min == 0 and y_min == 0 and x_max == 0 and y_max == 0:
        # No bbox recorded — accept (empty-glyph case handled above).
        return True
    tol = 1
    for x, y, _oc in outline.flat():
        if x < x_min - tol or x > x_max + tol:
            return False
        if y < y_min - tol or y > y_max + tol:
            return False
    return True


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

# P33.R1 — CJK font files (TrueType Collections on Windows; .ttf on other
# platforms).  The fallback chain appends these so a missing CJK glyph in
# the primary font can be rendered by the CJK face.
# Order = priority.  simhei is preferred: it is a plain .ttf with clean
# glyf outlines that the P20 rasterizer can read.  The .ttc collections
# (simsun / msyh) are kept as a fallback for hosts without simhei, but
# their CJK faces are often CFF-based or carry malformed glyf records,
# so they rank lower on purpose.
# P0 (GLM report) — CJK font discovery by *file-name literal match*
# was too narrow: the GLM probe on a clean Linux host found
# ``NotoSerifSC-Black.ttf`` in the system font dir, but the legacy
# matcher only recognised the bare bases ``cjk`` / ``noto`` /
# ``wqy`` (plus a handful of Windows-only names).  The result was
# ``font._cjk_fallback = None`` → any CJK character in the page
# crashed the whole render pipeline with ``AttributeError: 'NoneType'
# object has no attribute 'ascent'``.  The P0 fix widens the match to
# the *real-world* CJK family names so a host that has a CJK font
# (Noto CJK, Droid Sans Fallback, AR PL, Source Han, WenQuanYi,
# Unfonts, TakaoGothic, etc.) gets a working fallback face.
_CJK_FONT_FILES = {
    # Windows: the classic Microsoft CJK faces.
    "win": [
        "simhei", "simkai", "mingliu", "simsun", "msyh",
        "msyhbd", "msyhbd", "deng", "dena",
    ],
    # Linux / macOS / generic: a superset of the real-world CJK
    # families the GLM probe would find on a clean distro.  The
    # matcher lower-cases the file base and checks whether the base
    # contains one of these tokens (not an exact match), so
    # ``NotoSerifSC-Regular.ttf`` → base ``notoserifsc-regular``
    # matches the ``noto`` token, and ``DroidSansFallback.ttf`` →
    # ``droidsansfallback`` matches the ``droid`` token.
    "generic": [
        # Noto CJK (Debian / Ubuntu / Fedora)
        "noto", "notosanssc", "notosanscjk", "notoserifsc",
        "notoserifcjk", "notosansmono", "notosanstc", "notoseriftc",
        # Droid Sans Fallback (older Android / some distros)
        "droid", "droidsansfallback", "droidfallback",
        # WQY / WenQuanYi (Debian)
        "wqy", "wenquanyi", "wqymicrohei", "wqyzenhei",
        # AR PL (older RPM distros)
        "arphic", "arpl",
        # Source Han (older Adobe)
        "sourcehan",
        # Unfonts (Japanese, some BSD)
        "unfonts",
        # TakaoGothic (Japanese Windows legacy)
        "takao",
        # MS Gothic / MS Mincho (Windows JP)
        "msgothic", "msmincho",
        # Batang / Gulim (Windows KR)
        "batang", "gulim",
        # SimSun / SimHei (Windows CN, also on macOS / Linux via Wine)
        "simsun", "simhei", "simsun",
        # CJK generic
        "cjk",
        # macOS system fonts
        "pingfang", "hiragino", "songti", "heiti", "kaiti",
    ],
}


def _cjk_base_match(base: str) -> bool:
    """P0 (GLM report) — True when *base* (lower-cased file stem
    without the extension) contains any of the known CJK tokens.

    The legacy ``find_cjk_font`` only matched ``f"{b}.ttf"`` /
    ``f"{b}.ttc"`` for an exact base *b* from the table; the P0 fix
    matches the *token* inside the stem so a file like
    ``NotoSerifSC-Black.ttf`` (stem ``notoserifsc-black``) is found.
    """
    import re
    stem = re.sub(r"(?:bd|bold|black|light|thin|medium|regular|semi)$",
                  "", base.lower().replace("-", "").replace("_", ""))
    for tok in ("noto", "droid", "wqy", "wenquanyi", "arphic", "arpl",
                "sourcehan", "unfonts", "takao", "msgothic", "msmincho",
                "batang", "gulim", "simsun", "simhei", "cjk",
                "pingfang", "hiragino", "songti", "heiti", "kaiti",
                "msyh", "deng", "dena"):
        if tok in stem:
            return True
    return False


def _font_candidates(family: str, bold: bool, search_dirs: list) -> list:
    """P33.R1 — the ordered candidate TTF/TTC paths for *family*.

    Each entry of the list is a path that exists on disk; the caller
    tries them in order until one parses.  When the primary base name
    is missing, :data:`_FAMILY_FALLBACKS` fills the gap (e.g.
    monospace → cour → arial).  Search-dir order is preserved so an
    explicit dir (the test suite's "fonts/") wins over a system dir.
    """
    import os
    base = _FAMILY_FILES.get(family, family.lower().split(" ")[0])
    bases = [base] + _FAMILY_FALLBACKS.get(family, [])
    seen_b = set()
    ordered = []
    for b in bases:
        if b not in seen_b:
            seen_b.add(b)
            ordered.append(b)
    suffix = "bd" if bold else ""
    out: list = []
    seen_p: set = set()
    for b in ordered:
        for cand in (f"{b}{suffix}.ttf", f"{b}{suffix}.otf",
                     f"{b}{suffix}.ttc", f"{b}.ttf", f"{b}.otf", f"{b}.ttc"):
            for d in search_dirs:
                if not os.path.isdir(d):
                    continue
                p = os.path.join(d, cand)
                if os.path.isfile(p) and p not in seen_p:
                    seen_p.add(p)
                    out.append(p)
    return out


def _find_font_file(family: str, bold: bool, search_dirs: list) -> Optional[str]:
    """P17.4 — locate a TTF file on disk for a font family + weight.

    Returns the first candidate on disk (see :func:`_font_candidates`)
    or ``None``.  Callers that need the full candidate chain use
    :func:`_font_candidates` directly.
    """
    cands = _font_candidates(family, bold, search_dirs)
    return cands[0] if cands else None


# P33.R1 — resource governance: cap the size of font files we are
# willing to hold in RAM.  A system CJK face can be 10-20 MB; that is
# fine on a desktop but too much for the test-suite / CI.  Callers pass
# a byte budget (default 12 MiB, which fits simhei.ttf at 9.7 MB but
# rejects the 17-20 MB simsun / msyh collections) and oversized fonts
# are skipped, so a low-memory host falls back to the bitmap font
# instead of OOM-ing.
_MAX_FONT_BYTES = 12 * 1024 * 1024


def _load_font_path(path: str, max_bytes: int) -> Optional[FontMetrics]:
    """P33.R1 — parse one TTF/TTC path, respecting the byte budget.

    Returns the :class:`FontMetrics` or ``None`` (file too big,
    malformed, or no usable face).  Results are cached by
    ``(path, bold)`` — a failed parse is *not* cached so a later
    re-init can retry.
    """
    import os
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return None
        # P33.R1 — memory governor: refuse to parse a face when the
        # process heap is already near the 6 GB cap (the parse of a
        # 10 MB TTF expands to ~30 MB of Python objects).
        from toy_browser.common import mem_guard
        if not mem_guard.can_load_face(size):
            return None
    except OSError:
        return None
    # P33.R1 — cache key includes the path; bold is folded into the
    # path choice (bd suffix) so the same key is used here.
    key = (path, os.path.basename(path).endswith(("bd.ttf", "b.ttf", "bd.ttc")))
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    try:
        if is_ttc(path):
            fm = load_face_best_cjk(path)
        else:
            fm = load_ttf(path)
    except TTFError:
        return None
    _font_cache[key] = fm
    return fm


def load_font_for_family(
    family: str,
    font_px: float,
    bold: bool = False,
    search_dirs: Optional[list] = None,
    max_bytes: int = _MAX_FONT_BYTES,
) -> Optional[FontMetrics]:
    """P17.4 — load a real TTF/OTF for a CSS ``font-family`` + weight.

    P33.R1 — also accepts TrueType Collections (``.ttc``): when a
    candidate file is a collection, the CJK-capable face is selected
    (:func:`load_face_best_cjk`).  The candidate chain is walked in
    order until one parses; a file that fails (malformed, CFF face,
    oversized) is skipped, so a system dir with an unparseable
    ``cour.ttf`` falls through to the bundled ``arial.ttf``.
    Returns a :class:`FontMetrics` on success, or ``None`` when no
    font file is available (caller falls back to the built-in 8×8
    bitmap).
    """
    import os
    dirs = search_dirs if search_dirs is not None else []
    # Also look next to the script and in the current directory.
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    search = list(dirs) + [script_dir, script_dir + "/fonts", "."]
    for path in _font_candidates(family, bold, search):
        fm = _load_font_path(path, max_bytes)
        if fm is not None:
            return fm
    return None


def _cjk_stem_match(stem: str) -> bool:
    """P0 (GLM report) — True when the lower-cased file stem (without
    extension) looks like a CJK font file.  The legacy matcher only
    tried exact base names from :data:`_CJK_FONT_FILES`, so
    ``NotoSerifSC-Black.ttf`` was never found on a clean Linux host.
    The P0 fix matches the *token* inside the stem: any of the known
    CJK family tokens appearing in the stem qualifies.
    """
    s = stem.lower().replace("-", "").replace("_", "").replace(".", "")
    for tok in ("noto", "droid", "wqy", "wenquanyi", "arphic", "arpl",
                 "sourcehan", "unfonts", "takao", "msgothic", "msmincho",
                 "batang", "gulim", "simsun", "simhei", "cjk",
                 "pingfang", "hiragino", "songti", "heiti", "kaiti",
                 "msyh", "deng", "dena"):
        if tok in s:
            return True
    return False


def find_cjk_font(search_dirs: Optional[list] = None,
                  weight_class: int = 400,
                  max_bytes: int = _MAX_FONT_BYTES) -> Optional[FontMetrics]:
    """P33.R1 — find a CJK-capable font on the system.

    P0 (GLM report) — the legacy implementation only tried exact
    file names from :data:`_CJK_FONT_FILES` (``simhei.ttf``,
    ``noto.ttf``, ``wqy.ttf``, …), so a clean Linux host that had
    ``NotoSerifSC-Black.ttf`` in its font dir got
    ``_cjk_fallback = None`` and any CJK character in the page
    crashed the whole render pipeline.  The P0 fix scans every
    ``*.ttf`` / ``*.ttc`` in the search dirs and matches the stem
    against the CJK token table (``_cjk_stem_match``), so any CJK
    family the host actually has is picked up.

    For collections the most CJK-capable face is selected.  Returns
    the first usable :class:`FontMetrics`, or ``None``.

    P33.R1 resource governance — files larger than *max_bytes* are
    skipped so a low-memory host does not hold a 17 MB collection in
    RAM; the caller then falls back to the bitmap font.

    P34.8 — candidate order + load budget: the legacy alphabetical
    order put ``msgothic.ttc`` (8.8 MB, a 3-face TTC whose cmap
    parse takes minutes on this host) *before* ``simhei.ttf`` (9.5 MB
    .ttf, ~20 000 CJK glyphs, parses in <1 s), so the scan stalled
    on the collection for the whole parse budget.  The P34.8 order
    tries the known-good *standalone* CJK .ttf families first
    (simhei, msyh, Deng, simsun, Noto, WQY, …), and only falls back
    to the alphabetical scan (including the slow collections) if none
    of them are present.  A hard wall-clock budget (``_CJK_LOAD_BUDGET_S``)
    is also enforced across all load attempts so a single corrupt /
    slow face can never stall the scan past the budget.
    """
    import os
    import time as _time
    if search_dirs is None:
        search_dirs = _system_font_dirs()
    # P34.8 — known-good *standalone* CJK .ttf families, best coverage
    # first.  These are tried before any .ttc collection because the
    # collections (msgothic / msyhl / simsun) can take minutes to parse
    # every face on a slow host while a single .ttf with 20k+ CJK
    # glyphs parses in under a second.
    _PREFERRED_STEMS = [
        "simhei", "msyh", "deng", "dengb", "dengl", "msyhbd", "msyhl",
        "simsun", "notosanssc", "notosanscjk", "notosansmono",
        "notoserifsc", "notoserifcjk", "notoseriftc", "notosanstc",
        "wqy", "wqymicrohei", "wqyzenhei", "wenquanyi",
        "sourcehan", "unfonts", "takao", "batang", "gulim",
        "msmincho", "pingfang", "hiragino", "songti", "heiti", "kaiti",
        "cjk", "droid", "arphic", "arpl",
    ]
    best: Optional[FontMetrics] = None
    best_cjk_count = -1
    # P34.7 — limit the number of candidate files we attempt to load.
    # A full system font dir can have 500+ files; loading each one is
    # O(file-size) in RAM + CPU.  We only need to find *one* complete
    # CJK font, so cap the number of load attempts at 16 (the first 16
    # CJK-matching files in sorted order are almost always enough —
    # simhei.ttf / msyh.ttc / NotoSansSC-VF all sort early on Windows
    # and Linux).  If none of the first 16 have CJK coverage we fall
    # back to the best of the 16 (which may be None → bitmap fallback).
    _load_attempts = 0
    _MAX_LOAD_ATTEMPTS = 16
    # P34.8 — hard wall-clock budget for the whole CJK load scan.
    # A single face's cmap parse can stall for minutes (corrupt or
    # very large TTC); the scan must not exceed this budget, after
    # which the best-so-far (or None → bitmap fallback) is returned.
    _CJK_LOAD_BUDGET_S = 120.0
    _t_start = _time.monotonic()
    # Build a name → path map for the first search dir (the common
    # case: Windows has all fonts in one dir).
    _by_name: dict[str, str] = {}
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        try:
            for name in os.listdir(d):
                _by_name.setdefault(name.lower(), os.path.join(d, name))
        except OSError:
            continue

    def _try_load(path: str) -> Optional[FontMetrics]:
        """P34.8 — load one candidate, honouring the budget + caps."""
        nonlocal _load_attempts
        if _load_attempts >= _MAX_LOAD_ATTEMPTS:
            return None
        # P34.8 — budget check: if the scan has already run long,
        # stop trying new files and return the best-so-far.
        if _time.monotonic() - _t_start > _CJK_LOAD_BUDGET_S:
            return None
        try:
            if os.path.getsize(path) > max_bytes:
                return None
            _load_attempts += 1
            if is_ttc(path):
                m = load_face_best_cjk(path)
            else:
                m = load_ttf(path)
        except (TTFError, MemoryError, OSError, struct.error,
                ValueError, IndexError):
            # P34.7 — a MemoryError in _parse_cmap_subtable (some
            # TTC faces carry a corrupt format-12 subtable whose
            # group ranges explode) or a struct.error (corrupt
            # format-4 subtable with a too-small buffer) must not
            # take down the whole CJK scan; skip the face and try
            # the next candidate.
            return None
        if m is None:
            return None
        # P34.7 — pick the candidate with the most CJK Unified
        # Ideographs coverage (U+4E00–U+9FFF) so a sparse font
        # (e.g. HYZhongHeiTi with ~70 CJK glyphs) does not shadow a
        # complete one (simhei with ~20 000).  A font that has
        # *no* CJK-ideograph coverage is rejected outright.
        # Early exit at 500 CJK codepoints (same threshold as
        # load_face_best_cjk) — enough to discriminate a complete
        # font from a sparse one, without an O(cmap) scan on the
        # 9 MB simhei (60k+ cmap entries).
        cjk_count = 0
        for k in m.cmap:
            if 0x4E00 <= k <= 0x9FFF:
                cjk_count += 1
                if cjk_count >= 500:
                    break
        if cjk_count == 0:
            return None
        nonlocal best, best_cjk_count
        if cjk_count > best_cjk_count:
            best_cjk_count = cjk_count
            best = m
        return best

    # P34.8 — pass 1: try the preferred standalone .ttf families in
    # known-coverage order.  On a Windows host with simhei.ttf
    # present this pass finds a 20k-glyph CJK face in <1 s and the
    # early-exit fires, so the slow msgothic/msyhl collections are
    # never even attempted.
    for stem in _PREFERRED_STEMS:
        if best is not None and best_cjk_count >= 500:
            break  # early exit: complete CJK font found
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            cand = os.path.join(d, f"{stem}.ttf")
            if os.path.isfile(cand):
                _try_load(cand)
                break
    # P34.8 — pass 2: the legacy alphabetical scan (covers the
    # preferred families that are absent, plus any host-specific
    # CJK file the token table matches).  Budget + attempt caps
    # still apply.
    if not (best is not None and best_cjk_count >= 500):
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            try:
                entries = os.listdir(d)
            except OSError:
                continue
            for name in sorted(entries):
                if _load_attempts >= _MAX_LOAD_ATTEMPTS:
                    break
                if best is not None and best_cjk_count >= 500:
                    break
                low = name.lower()
                if not (low.endswith(".ttf") or low.endswith(".ttc")):
                    continue
                # P0 — match the stem against the CJK token table.
                stem = low.rsplit(".", 1)[0]
                if not _cjk_stem_match(stem):
                    continue
                p = os.path.join(d, name)
                _try_load(p)
                if best is not None and best_cjk_count >= 500:
                    break
            if best is not None and best_cjk_count >= 500:
                break
    return best


def _system_font_dirs() -> list:
    """P33.R1 — the most likely OS font directories, best first.

    Zero-dependency: plain paths, no registry/DBUS reads.  Absent paths
    are kept; callers skip them (the scan handles non-existent dirs).
    """
    import os
    import platform
    out: list = []
    sysname = platform.system()
    if sysname == "Windows":
        out.append(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    elif sysname == "Darwin":
        out += ["/System/Library/Fonts", "/Library/Fonts",
                os.path.expanduser("~/Library/Fonts")]
    else:
        out += ["/usr/share/fonts", "/usr/local/share/fonts",
                os.path.expanduser("~/.fonts")]
    return out


def _font_files_in(dirs: list) -> list:
    """All TTF/OTF/TTC files under *dirs* (flat scan, no recursion)."""
    import os
    out: list = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.lower().endswith((".ttf", ".otf", ".ttc")):
                out.append(os.path.join(d, name))
    return out


# ---------------------------------------------------------------------------
# P20.4 — font fallback chain: specified family → system-fonts dir scan →
# built-in bitmap.  A CSS ``font-family`` value may name a family that has
# no bundled TTF (e.g. "verdana" when only arial.ttf is shipped).  The chain:
#   1. Try the exact family (with the weight suffix).
#   2. Try the family's registered fallbacks (e.g. monospace → cour → arial).
#   3. Scan the search dirs for *any* TTF whose family name matches, or,
#      failing that, any TTF at all (last resort).
#   4. None → caller falls back to the built-in 8×8 bitmap.
# A ``weight_class`` hook selects the closest available weight.
# ---------------------------------------------------------------------------

def match_weight(available, requested: int = 400) -> Optional[int]:
    """P20.4 — pick the closest available weight_class to ``requested``.

    ``available`` is a mapping of family-name → :class:`FontMetrics`
    (each carries ``weight_class``).  Returns the chosen weight_class, or
    ``None`` if ``available`` is empty.  The weight closest to
    ``requested`` wins; ties prefer the heavier weight.
    """
    if not available:
        return None
    best = None
    best_dist = None
    for m in available.values():
        w = getattr(m, "weight_class", 400)
        d = abs(w - requested)
        if best_dist is None or d < best_dist or (d == best_dist and best is not None and w > best):
            best_dist = d
            best = w
    return best


def find_font_for_family(family: str, weight_class: int = 400,
                         search_dirs: Optional[list] = None,
                         want_bold: bool = False) -> Optional[FontMetrics]:
    """P20.4 — resolve a CSS font-family + weight to a loaded FontMetrics.

    Walks the fallback chain (exact family → registered fallbacks → any
    available TTF) and returns the first :class:`FontMetrics` whose
    ``weight_class`` is the closest to ``weight_class``.  ``want_bold``
    biases the search toward the bold variant when available.
    """
    import os
    import glob
    import re as _re
    dirs = list(search_dirs) if search_dirs else []
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dirs = dirs + [script_dir + "/fonts", script_dir, "."]

    # 1. Exact family (with weight suffix when bold is requested).
    bold = want_bold or weight_class >= 600
    m = load_font_for_family(family, 0.0, bold, dirs)
    if m is not None:
        return m
    # 2. Registered fallbacks (exact weight).
    bases = _FAMILY_FALLBACKS.get(family, [])
    for fb in bases:
        m = load_font_for_family(fb, 0.0, bold, dirs)
        if m is not None:
            return m
    # 3. Scan search dirs for any TTF whose family name matches.
    fam_norm = family.lower()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "*.ttf"))) + \
                sorted(glob.glob(os.path.join(d, "*.otf"))):
            base = _re.sub(r"(bd|b)$", "", os.path.basename(p)).rsplit(".", 1)[0]
            if base in (_FAMILY_FILES.get(family, fam_norm)) or base in fam_norm or fam_norm in base:
                m = load_font_for_family(base, 0.0, bold, [d])
                if m is not None:
                    return m
    # 4. Last resort: any available TTF (normal weight preferred).
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "*.ttf"))) + \
                sorted(glob.glob(os.path.join(d, "*.otf"))):
            m = load_font_for_family(os.path.basename(p).rsplit(".", 1)[0],
                                    0.0, bold, [d])
            if m is not None:
                return m
    return None


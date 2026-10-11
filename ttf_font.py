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
    contours (e.g. space).  ``bbox`` is the glyph's bounding box
    ``(xMin, yMin, xMax, yMax)`` in font units, used by P20 to
    validate parsed coordinates (outlines whose points fall outside the
    bbox are rejected and the caller falls back to bitmap rendering).
    """
    points: list = field(default_factory=list)
    is_empty: bool = False
    is_composite: bool = False
    bbox: tuple = (0, 0, 0, 0)


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
            xs = [p[0] for p in out.points]
            ys = [p[1] for p in out.points]
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
            for c in range(start_c, end_c + 1):
                result[c] = start_g + (c - start_c)
    elif fmt == 14:
        # Format 14 (Unicode variation sequences).  Skip for toy.
        pass
    return result


def _load_one_ttf(data: bytes) -> FontMetrics:
    """P33.R1 — parse a *single* TrueType font image (the sfnt record)
    out of ``data``.  Shared by :func:`load_ttf` (whole file) and
    :func:`load_ttc` (one face of a collection)."""
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
    return _load_one_ttf(data)


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


def load_ttc(path: str, face: int = 0) -> FontMetrics:
    """P33.R1 — load the *face*-th font of a TrueType Collection.

    Falls back to the first face when an index out of range is given, so
    callers can probe a collection with face=0 safely.  Raises
    :class:`TTFError` for non-TTC inputs or unreadable files.
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
    return _load_one_ttf(data[off:])


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
        except TTFError:
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
    """P17.2 — parse a simple glyf record into a list of ``(x, y, on_curve)``.

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
        pts.append((px, y, bool(flags[k] & 0x01)))
    return pts


# P33.R1 — cycle guard for composite glyph parsing: the set of glyph ids
# currently on the recursion stack.  A composite glyph whose component
# chain loops back onto an ancestor is skipped instead of recursing
# forever.  Lives at module scope so every recursive call shares it.
_COMPOSITE_CYCLE_GUARD: set = set()


def _parse_composite_glyph(raw: bytes, fm: FontMetrics, depth: int = 0) -> list:
    """P17.2 — parse a composite glyf record, recursively extracting
    component outlines (transformed to the parent coordinate system).

    Returns a flat list of ``(x, y, on_curve)`` in font units.

    P33.R1 — self-reference / cycle guard: some fonts ship composite
    glyphs whose component chain loops back onto an ancestor.  Track the
    glyph ids already on the stack; when a cycle is detected the
    remaining components are skipped instead of recursing forever.
    """
    if depth > 10:
        return []
    if len(raw) < 4:
        return []
    num_components = struct.unpack(">H", raw[:2])[0]
    off = 4
    all_pts: list = []
    # P33.R1 — self-reference / cycle guard: some fonts ship composite
    # glyphs whose component chain loops back onto an ancestor.  Track the
    # glyph ids already on the stack; when a cycle is detected the
    # remaining components are skipped instead of recursing forever.
    # A corrupt record (num_components absurdly large, or no room for
    # even one 4-byte component header) yields no points.
    if num_components > 128:
        return all_pts
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
        for px, py, oc in comp_pts:
            tx = px * scale_x + py * scale_xy + x
            ty = px * scale_xy + py * scale_y + y
            all_pts.append((int(tx), int(ty), oc))
    return all_pts


def _extract_glyph(fm: FontMetrics, gid: int, _depth: int = 0) -> Optional[GlyphOutline]:
    """P17.2 — extract the outline for glyph index ``gid``.

    P20 — attaches the glyph's bbox (``xMin, yMin, xMax, yMax`` in font
    units) to the :class:`GlyphOutline` so callers can validate parsed
    coordinates (outlines with points outside the bbox are broken and
    should be rejected).
    """
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
    # P20 — read the glyf bbox for validation.
    bbox = (0, 0, 0, 0)
    if len(raw) >= 10:
        bbox = struct.unpack(">hhhh", raw[2:10])
    if num_contours > 0:
        pts = _parse_simple_glyph(raw)
        return GlyphOutline(points=pts, is_empty=not pts,
                           is_composite=False, bbox=bbox)
    # P33.R1 — composite glyphs have num_contours < 0.  A huge negative
    # value (e.g. -18931 = 0xB743) means the record was misread as a
    # composite (the loca offsets or the glyf table are truncated on
    # disk); treat it as empty rather than recursing into garbage.
    if num_contours < -256:
        return GlyphOutline(points=[], is_empty=True, is_composite=True,
                            bbox=bbox)
    # Composite glyph.
    # P33.R1 — push this glyph's id onto the cycle guard, parse, pop.
    _COMPOSITE_CYCLE_GUARD.add(gid)
    try:
        pts = _parse_composite_glyph(raw, fm, _depth)
    finally:
        _COMPOSITE_CYCLE_GUARD.discard(gid)
    return GlyphOutline(points=pts, is_empty=not pts,
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
    for x, y, _oc in outline.points:
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
_CJK_FONT_FILES = {
    "win": ["simhei", "simkai", "mingliu", "simsun", "msyh"],
    "generic": ["cjk", "noto", "wqy"],
}


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
        import mem_guard
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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search = list(dirs) + [script_dir, script_dir + "/fonts", "."]
    for path in _font_candidates(family, bold, search):
        fm = _load_font_path(path, max_bytes)
        if fm is not None:
            return fm
    return None


def find_cjk_font(search_dirs: Optional[list] = None,
                  weight_class: int = 400,
                  max_bytes: int = _MAX_FONT_BYTES) -> Optional[FontMetrics]:
    """P33.R1 — find a CJK-capable font on the system.

    Scans *search_dirs* (default: the OS font directory when
    ``None``) for the known CJK font file bases (:data:`_CJK_FONT_FILES`),
    handling both ``.ttf`` and ``.ttc``.  For collections the most
    CJK-capable face is selected.  Returns the first usable
    :class:`FontMetrics`, or ``None``.

    P33.R1 resource governance — files larger than *max_bytes* are
    skipped so a low-memory host does not hold a 17 MB collection in
    RAM; the caller then falls back to the bitmap font.
    """
    import os
    import platform
    if search_dirs is None:
        search_dirs = _system_font_dirs()
    bases = list(_CJK_FONT_FILES.get("win" if platform.system() == "Windows"
                                      else "generic", []))
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for b in bases:
            for cand in (f"{b}.ttf", f"{b}.ttc", f"{b}bd.ttf", f"{b}bd.ttc"):
                p = os.path.join(d, cand)
                if not os.path.isfile(p):
                    continue
                try:
                    if os.path.getsize(p) > max_bytes:
                        continue
                    if is_ttc(p):
                        m = load_face_best_cjk(p)
                    else:
                        m = load_ttf(p)
                except TTFError:
                    continue
                if m is not None:
                    return m
    return None


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
    script_dir = os.path.dirname(os.path.abspath(__file__))
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


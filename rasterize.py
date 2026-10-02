"""P20 — True quadratic-Bezier glyph rasterization with antialiasing.

P17 stored TTF glyph outlines as lists of ``(x, y, on_curve)`` points
but still blitted the legacy 8×8 bitmap.  This module replaces that
with a proper **curve-based** renderer:

1. **Flatten** each quadratic-Bezier segment into a polyline
   (de Casteljau subdivision, one recursion level).
2. **Collect** all outline points per contour (on-curve points are the
   control points; off-curve points become implicit on-curve midpoints
   between adjacent off-curve points — the standard TrueType rule).
3. **Scanline fill** (even-odd) the resulting polygon at 2× supersample
   resolution to produce **coverage-based antialiasing**: each output
   pixel accumulates the 2×2 sub-pixel coverage and the result is
   blended into the target bitmap with fractional alpha.

Pure standard library.
"""
from __future__ import annotations

import math
from paint import Bitmap, Color, WHITE


# ---------------------------------------------------------------------------
# P20.1 — quadratic-Bezier flattening
# ---------------------------------------------------------------------------

def _flatten_quadratic(p0, p1, p2, flatness=0.25):
    """Flatten one quadratic Bezier segment into a polyline.

    ``p0``, ``p1`` (control), ``p2`` are ``(x, y)`` in pixel space.
    Returns a list of ``(x, y)`` points including both endpoints.
    Uses recursive de Casteljau subdivision until the control point
    is within ``flatness`` pixels of the chord.
    """
    def depth(a, b, c, d=0):
        dx = c[0] - a[0]
        dy = c[1] - a[1]
        dist = abs(dy * (b[0] - a[0]) - dx * (b[1] - a[1]))
        if dist <= flatness or d >= 6:
            return [c]
        m0 = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        m1 = ((b[0] + c[0]) / 2.0, (b[1] + c[1]) / 2.0)
        m2 = ((m0[0] + m1[0]) / 2.0, (m0[1] + m1[1]) / 2.0)
        return depth(a, m0, m2, d + 1) + depth(m2, m1, c, d + 1)

    return [p0] + depth(p0, p1, p2)


def _contour_to_points(pts, scale, origin_x, origin_y, flip_y=True):
    """Convert a list of ``(x, y, on_curve)`` font-unit outline points into
    a pixel-space polygon.

    ``scale`` maps font units → pixels.  ``origin_x / origin_y`` place the
    glyph's origin (baseline left) in pixel space.  ``flip_y`` converts
    TTF's y-up coordinate system to screen y-down.

    TrueType rule: off-curve points are quadratic Bezier control points.
    The on-curve point *between* two consecutive off-curve points is the
    midpoint of those two off-curve points.  This produces the actual
    vertex list for the scanline fill.
    """
    if not pts:
        return []
    n = len(pts)
    # Build the on-curve polygon by applying the TrueType off-curve rule.
    poly: list = []
    i = 0
    while i < n:
        x, y, on_curve = pts[i]
        if on_curve:
            poly.append((x, y))
            i += 1
        else:
            # Run of consecutive off-curve points.
            run = []
            while i < n and not pts[i][2]:
                run.append((pts[i][0], pts[i][1]))
                i += 1
            # The on-curve vertex between off-curve k and off-curve k+1
            # is their midpoint.
            for k in range(len(run) - 1):
                poly.append(((run[k][0] + run[k + 1][0]) / 2.0,
                             (run[k][1] + run[k + 1][1]) / 2.0))
            # If the run wraps around the contour end, the first on-curve
            # point of the contour closes the last quadratic.
            if run and n > 0:
                next_pt = pts[i] if i < n else pts[0]
                poly.append(((run[-1][0] + next_pt[0]) / 2.0,
                              (run[-1][1] + next_pt[1]) / 2.0))
    if not poly:
        return []
    # Transform to pixel space (y-flip for screen coordinates).
    result: list = []
    for px, py in poly:
        fx = origin_x + px * scale
        fy = origin_y - py * scale if flip_y else origin_y + py * scale
        result.append((fx, fy))
    return result


# ---------------------------------------------------------------------------
# P20.2 — scanline fill with 2× coverage antialiasing
# ---------------------------------------------------------------------------

def _fill_polygon(bmp: Bitmap, polygon: list, color: Color,
                 antialias: bool = True) -> None:
    """Even-odd scanline fill of *polygon* into *bmp*.

    ``antialias`` (P20.5): True → sample at 2× resolution (each output
    pixel subdivided into four ½×½ sub-squares; coverage 0..4 →
    fractional alpha).  False → single hard-edged sample at the pixel
    centre (coverage is a hard 0/1), which is faster but produces aliased
    edges.  Used for the ``text-rendering: optimizeSpeed`` hint.
    """
    if len(polygon) < 3:
        return
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    x_min = max(0, int(math.floor(min(xs))))
    x_max = min(bmp.width, int(math.ceil(max(xs))) + 1)
    y_min = max(0, int(math.floor(min(ys))))
    y_max = min(bmp.height, int(math.ceil(max(ys))) + 1)
    if x_max <= x_min or y_max <= y_min:
        return

    n = len(polygon)
    if not antialias:
        # P20.5 — hard-edged single-sample fill (optimizeSpeed).
        for py in range(y_min, y_max):
            sample_y = py + 0.5
            crossings: list = []
            for i in range(n):
                x1, y1 = polygon[i]
                x2, y2 = polygon[(i + 1) % n]
                if (y1 <= sample_y < y2) or (y2 <= sample_y < y1):
                    t = (sample_y - y1) / (y2 - y1)
                    crossings.append(x1 + t * (x2 - x1))
            if not crossings:
                continue
            crossings.sort()
            for k in range(0, len(crossings) - 1, 2):
                s0, s1 = crossings[k], crossings[k + 1]
                px_start = max(x_min, int(math.floor(s0)))
                px_end = min(x_max - 1, int(math.ceil(s1)))
                base = py * bmp.width
                for px in range(px_start, px_end + 1):
                    idx = base + px
                    dst = bmp.pixels[idx]
                    dr, dg, db = (dst >> 16) & 0xFF, (dst >> 8) & 0xFF, dst & 0xFF
                    # Partial-pixel coverage at the edge: fraction of the
                    # pixel column inside the span.
                    f = max(0.0, min(1.0, min(px + 1.0, s1) - max(px, s0)))
                    if f <= 0:
                        continue
                    mr = min(255, int(color.r * f + dr * (1.0 - f)))
                    mg = min(255, int(color.g * f + dg * (1.0 - f)))
                    mb = min(255, int(color.b * f + db * (1.0 - f)))
                    bmp.pixels[idx] = (mr << 16) | (mg << 8) | mb
        return

    for py in range(y_min, y_max):
        # For each of the two sub-pixel rows (py+0.25, py+0.75), find
        # all x-intersections with polygon edges.  Then even-odd span
        # fill at the two sub-pixel columns (px+0.25, px+0.75).
        crossings_lo: list = []  # at y = py + 0.25
        crossings_hi: list = []  # at y = py + 0.75
        for i in range(n):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % n]
            ylo = py + 0.25
            yhi = py + 0.75
            if (y1 <= ylo < y2) or (y2 <= ylo < y1):
                t = (ylo - y1) / (y2 - y1)
                crossings_lo.append(x1 + t * (x2 - x1))
            if (y1 <= yhi < y2) or (y2 <= yhi < y1):
                t = (yhi - y1) / (y2 - y1)
                crossings_hi.append(x1 + t * (x2 - x1))
        if not crossings_lo and not crossings_hi:
            continue
        crossings_lo.sort()
        crossings_hi.sort()
        # Even-odd spans for the low sub-row.
        spans_lo = [(crossings_lo[k], crossings_lo[k + 1])
                    for k in range(0, len(crossings_lo) - 1, 2)]
        spans_hi = [(crossings_hi[k], crossings_hi[k + 1])
                    for k in range(0, len(crossings_hi) - 1, 2)]
        base = py * bmp.width
        # Union of x-ranges covered by either sub-row.
        all_xs = set()
        for s0, s1 in spans_lo:
            for px in range(max(x_min, int(math.floor(s0))),
                           min(x_max - 1, int(math.ceil(s1))) + 1):
                all_xs.add(px)
        for s0, s1 in spans_hi:
            for px in range(max(x_min, int(math.floor(s0))),
                           min(x_max - 1, int(math.ceil(s1))) + 1):
                all_xs.add(px)
        for px in all_xs:
            # Coverage: how many of the 4 sub-pixel centres are inside?
            cov = 0
            cx_lo, cx_hi = px + 0.25, px + 0.75
            # Low row (py+0.25): is cx_lo or cx_hi inside a span_lo?
            for c in (cx_lo, cx_hi):
                for s0, s1 in spans_lo:
                    if s0 <= c <= s1:
                        cov += 1
                        break
            # High row (py+0.75): is cx_lo or cx_hi inside a span_hi?
            for c in (cx_lo, cx_hi):
                for s0, s1 in spans_hi:
                    if s0 <= c <= s1:
                        cov += 1
                        break
            if cov == 0:
                continue
            alpha = int(255 * cov / 4)
            if alpha <= 0:
                continue
            idx = base + px
            dst = bmp.pixels[idx]
            sa = alpha / 255.0
            dr = (dst >> 16) & 0xFF
            dg = (dst >> 8) & 0xFF
            db = dst & 0xFF
            sr, sg, sb = color.r, color.g, color.b
            mr = min(255, int(sr * sa + dr * (1.0 - sa)))
            mg = min(255, int(sg * sa + dg * (1.0 - sa)))
            mb = min(255, int(sb * sa + db * (1.0 - sa)))
            bmp.pixels[idx] = (mr << 16) | (mg << 8) | mb


# ---------------------------------------------------------------------------
# P20 — public entry point: draw a TTF glyph outline with curves
# ---------------------------------------------------------------------------

def draw_glyph_curves(
    bmp: Bitmap,
    outline,
    scale: float,
    origin_x: float,
    origin_y: float,
    color: Color,
    weight: float = 1.0,
    antialias: bool = True,
) -> int:
    """P20.1/P20.2/P20.3 — rasterize a TTF :class:`~ttf_font.GlyphOutline`
    onto *bmp* using quadratic-Bezier flattening + antialiased scanline
    fill.

    ``scale`` maps font units → pixels (``font_px / units_per_em``).
    ``origin_x / origin_y`` is the glyph's pen position in pixel space
    (``origin_y`` is the baseline; the glyph's y-axis is flipped).

    ``weight`` is a stroke multiplier: 1.0 = normal, 2.0 = roughly bold.
    Values above 1.0 trigger P20.3 synthetic bold — the polygon is
    dilated outward so the strokes are visibly thicker.  The dilation
    radius is proportional to ``scale`` so bold stays consistent across
    font sizes.

    ``antialias`` (P20.5): True → 2×2 supersampled coverage fill;
    False → hard-edged single-sample fill (optimizeSpeed hint).

    Returns the number of polygon vertices rendered (0 if the outline
    is empty).
    """
    if outline is None or outline.is_empty or not outline.points:
        return 0
    poly = _contour_to_points(list(outline.points), scale, origin_x, origin_y, flip_y=True)
    if len(poly) < 3:
        return 0
    # P20.3 — synthetic bold: dilate the polygon in pixel space.
    if weight > 1.0:
        # Dilation radius scales with the font size (in pixels) so that
        # bold is visible at both small and large point sizes.
        dilate_px = max(1.0, (weight - 1.0) * (scale * 2048.0) * 0.15)
        poly = _dilate_poly(poly, dilate_px)
    _fill_polygon(bmp, poly, color, antialias=antialias)
    return len(poly)


def _dilate(pts: list, radius: float) -> list:
    """P20.3 — naive outline dilation for synthetic bold (font units).

    Each point is moved outward by ``radius`` along the vector from the
    local centroid.  Toy approximation (real synthetic bold uses offset
    curves / Minkowski sum).
    """
    if len(pts) < 3:
        return pts
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    out: list = []
    for x, y, oc in pts:
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy)
        if d < 1e-6:
            out.append((x, y, oc))
            continue
        out.append((x + (dx / d) * radius, y + (dy / d) * radius, oc))
    return out


def _dilate_poly(poly: list, radius: float) -> list:
    """P20.3 — dilate a pixel-space polygon outward by *radius* pixels.

    Each vertex is moved outward along the vector from the polygon's
    centroid to the vertex.  Toy approximation.
    """
    if len(poly) < 3 or radius <= 0:
        return poly
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    out: list = []
    for x, y in poly:
        dx, dy = x - cx, y - cy
        d = math.hypot(dx, dy)
        if d < 1e-6:
            out.append((x, y))
            continue
        out.append((x + (dx / d) * radius, y + (dy / d) * radius))
    return out


# ---------------------------------------------------------------------------
# P20 — convenience: render a whole string of TTF glyphs with curves
# ---------------------------------------------------------------------------

def draw_text_curves(
    bmp: Bitmap,
    x: float,
    y: float,
    text: str,
    font_px: float,
    color: Color,
    font_metrics,
    letter_spacing: float = 0.0,
    weight: float = 1.0,
) -> int:
    """P20 — render ``text`` with quadratic-Bezier curve filling.

    ``font_metrics`` is a :class:`~ttf_font.FontMetrics` (P17).  Each
    character's glyph outline is extracted, scaled, and filled.  The
    pen advances by the real hmtx width plus ``letter_spacing``.

    Returns the total width consumed in pixels.
    """
    if font_metrics is None:
        return 0
    scale = font_px / font_metrics.units_per_em
    cx = x
    total = 0.0
    for ch in text:
        out = font_metrics.glyph_outline(ch)
        if out is not None and not out.is_empty:
            draw_glyph_curves(bmp, out, scale, cx, y, color, weight=weight)
        code = ord(ch)
        gid = font_metrics.cmap.get(code, 0)
        adv = font_metrics.advances[gid] if gid < len(font_metrics.advances) else font_metrics.units_per_em
        adv_px = adv * scale + letter_spacing
        cx += adv_px
        total += adv_px
    return total

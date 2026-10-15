"""Stage 3 — box model + single-pass block/inline layout + hit testing.

Pure standard library. Produces a tree of LayoutBox that paint_engine.py
walks to emit display commands.
"""
from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import Optional

from css import (DEFAULT_FONT_PX, EdgeList, Keyword, Length, StyledNode,
                 parse_color, resolve_length as _resolve_length,
                 CalcExpr, VarExpr, ImportantDecl)
import css as css_mod
from dom import Node
from paint import Color
import text as text_mod
import font


# P33.R5 — module-level caches and constants (hot-path shortcuts).
from paint import Color as _Color
_DEFAULT_BORDER_COLOR = _Color(0, 0, 0)
_DEFAULT_TEXT_COLOR = _Color(0, 0, 0)
# P34.1 — child-index cache with an address-reuse guard.  The key is
# the *parent node object* (a weakref.ref, so the table itself never
# keeps dead DOM trees alive; ``Node`` gained a ``__weakref__`` slot
# for this).  A plain id()-keyed dict would be poisoned when CPython's
# allocator hands the same address to a freshly created node of a later
# document; the live-parent check (``entry[0]() is parent_dom``) makes
# the mapping correct across re-renders.  The table also bounds itself
# (LRU drop of the oldest entries at ``_CHILD_INDEX_CACHE_MAX``).
# ``clear_caches()`` still resets the whole table between independent
# document renders.
_child_index_cache: dict = {}  # id(parent) → (weakref(parent), {id(child) → idx})
_CHILD_INDEX_CACHE_MAX = 4096

# P34.4 — half-leading line box cache: a document uses a handful of
# distinct (family, font_px) pairs (body text 14px, h1 16px, caption
# 12px …).  The legacy code re-resolved the TTF metrics + ascent /
# descent / half-leading on every text box (~3180 times in a 3k-node
# layout).  Cache the computed (half_lead, ascent_px, descent_px) per
# (family, font_px, line_h); the same string triple in a document
# always produces the same line box.  Bounded at 64 entries (a
# document realistically uses far fewer).  Reset in clear_caches.
_half_lead_cache: dict = {}
_HALF_LEAD_CACHE_MAX = 64


def _child_index_for(node, parent_dom) -> int:
    """P34.1 — O(1) child index with an address-reuse guard.

    The weakref guard makes the cache safe across re-renders: when the
    parent node is collected and a *new* node reuses its address, the
    stored weakref is dead (or points to a different object) and the
    mapping is rebuilt instead of returning a stale index.
    """
    _idparent = id(parent_dom)
    entry = _child_index_cache.get(_idparent)
    if entry is not None and entry[0]() is parent_dom:
        return entry[1].get(id(node), 0)
    idx = 0
    mapping = {}
    for c in parent_dom.children:
        if c.node_type == "element":
            idx += 1
            mapping[id(c)] = idx
    if len(_child_index_cache) >= _CHILD_INDEX_CACHE_MAX:
        # Drop the 1/8 oldest entries (dicts preserve insertion order).
        for k in list(_child_index_cache)[:_CHILD_INDEX_CACHE_MAX // 8]:
            del _child_index_cache[k]
    _child_index_cache[_idparent] = (weakref.ref(parent_dom), mapping)
    return mapping.get(id(node), 0)

# P33.R5 — frozenset of CSS properties that, when present, force the
# slow path of build_box.  Checked once per call via set intersection.
# P33.P1 — line-height is added so that divs with an explicit line-height
# go through the slow path (which reads sn.line_height_px() correctly);
# the fast path hardcodes line_height_px = my_font * 1.2.
_SPECIAL_PROPS = frozenset((
    "display", "position", "overflow", "overflow-x", "overflow-y",
    "box-shadow", "transform", "z-index", "float",
    "flex-grow", "flex-shrink", "flex-basis", "order",
    "align-self", "opacity", "border-radius", "filter",
    "letter-spacing", "word-spacing", "text-transform",
    "text-indent", "text-rendering", "font-weight",
    "line-height",
    "box-decoration-break", "-webkit-appearance",
    "text-justify", "word-break", "hyphens", "overflow-wrap",
    "line-clamp", "-webkit-line-clamp", "text-overflow", "vertical-align",
    "min-width", "max-width", "min-height", "max-height",
    "outline", "outline-width", "clip-path", "background-image",
    "text-shadow", "background",
    # P0 (agent report) — box-sizing changes how explicit width/height are
    # resolved (border-box subtracts padding + border), so a box carrying
    # `box-sizing: border-box` must NOT take the fast path's content-box
    # assumptions.  The `border`/`padding`/`margin` shorthands (and their
    # longhand forms) likewise break the fast path's zero-edge default.
    "box-sizing", "border", "border-width", "border-color", "border-style",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
))
# P33.P1 — fast path additionally requires a small props map: a div with
# 5+ properties is not a "plain block" and should go through the slow
# path (which reads all of them).  This avoids the frozenset intersection
# on the hot path for the 10k-node benchmark case (width/height/color/
# font-size = 4 props → fast path).
_FAST_PATH_MAX_PROPS = 4
_FAST_PATH_EXCLUDED_TAGS = frozenset((
    "img", "iframe", "input", "select", "textarea", "button",
    "canvas", "svg", "video", "audio",
    "td", "th", "tr", "table", "span", "a", "em", "strong",
    "b", "i", "u", "mark", "code", "small", "sub", "sup",
))


_child_index_cache: dict = {}  # id(parent) → (weakref(parent), {id(child) → idx})
_CHILD_INDEX_CACHE_MAX = 4096


def clear_caches() -> None:
    """P33.R5 — clear the module-level performance caches.

    Call between independent document renders so stale child-index
    mappings (keyed by ``id()``) don't produce wrong results when
    the same ``id()`` is reused for a different node.

    P34.8 — the advance-width + document-width caches are *content-
    scoped* and MUST be dropped between renders so a string that
    changed does not keep its old width.  The per-(family, px, char)
    advance memo, however, is *face-scoped*: faces are immutable once
    loaded, so a cached advance for ("monospace", 14, "a") stays valid
    across documents.  Keeping it across renders is what makes the
    warm-cache path fast; the legacy clear_caches dropped it too,
    forcing every layout to re-pay the CJK face resolution + TTF
    advance computation on the first character of every string.
    """
    _child_index_cache.clear()
    _half_lead_cache.clear()
    text_mod.clear_advance_cache()
    text_mod.clear_metrics_memo()
    # P34.8 — the face-scoped advance memo is intentionally NOT cleared
    # here: it is keyed on (family, px-bucket, char) and faces are
    # immutable, so re-using it across independent renders is safe and
    # is the main win of the warm-cache path.  Only a test that swaps
    # the font registry calls text_mod.clear_advance_cache() directly.
    css_mod.clear_caches()


class BoxType:
    BLOCK = "block"
    TEXT = "text"
    INLINE = "inline"


def is_inline_tag(tag: str) -> bool:
    return tag in (
        "span", "a", "em", "strong", "b", "i", "u", "mark",
        "code", "small", "sub", "sup",
    )


@dataclass
class Rect:
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0


@dataclass
class EdgeSizes:
    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0


@dataclass
class LayoutBox:
    box_type: str
    rect: Rect = field(default_factory=Rect)
    padding: EdgeSizes = field(default_factory=EdgeSizes)
    border: EdgeSizes = field(default_factory=EdgeSizes)
    margin: EdgeSizes = field(default_factory=EdgeSizes)
    text_content: Optional[str] = None
    text_color: object = None
    background: object = None
    border_color: object = None
    font_px: float = DEFAULT_FONT_PX
    text_align: str = "left"
    explicit_height: Optional[float] = None
    explicit_width: Optional[float] = None
    # P0 (agent report) — box-sizing: "border-box" makes explicit
    # width/height the border-box size (content = explicit - padding - border).
    box_sizing: str = ""
    is_display_none: bool = False
    line_height_px: float = DEFAULT_FONT_PX
    children: list = field(default_factory=list)
    parent: Optional["LayoutBox"] = None
    dom: Optional[Node] = None
    # P1.4 — Flexbox
    is_flex: bool = False
    flex_direction: str = "row"  # "row" | "column" (+"-reverse")
    flex_wrap: str = "nowrap"  # P27 — "nowrap" | "wrap" | "wrap-reverse"
    justify_content: str = "start"  # start|center|end|space-between|space-around|space-evenly
    align_items: str = "stretch"  # stretch|start|center|end|baseline(toy: start)
    align_content: str = "stretch"  # P27 — multi-line cross-axis: stretch|start|center|end|space-between|space-around
    # P3.2 — flex-grow weight (for distributing leftover space)
    flex_grow: float = 0.0
    # P27 — flex sizing trio + order
    flex_shrink: float = 1.0
    flex_basis: float | None = None  # None = auto (use content/explicit size)
    flex_order: int = 0
    align_self: str = ""  # P27 — per-item cross-axis override; "" = inherit align-items
    # P0 (retest report) — flex gap (column-gap on the main axis for row,
    # row-gap for column); the `gap` shorthand sets both.
    flex_gap: float = 0.0
    # P4.3 — CSS Grid
    is_grid: bool = False
    grid_columns: int = 0  # number of column tracks
    grid_rows: int = 0  # number of row tracks (0 = auto)
    # P0 (retest report) — grid track fractions + gap (1fr 2fr →
    # [(1.0,'fr'),(2.0,'fr')]); grid_gap = column-gap, grid_row_gap =
    # row-gap (cross-axis).
    grid_cols_frac: list = field(default_factory=list)
    grid_rows_frac: list = field(default_factory=list)
    grid_gap: float = 0.0
    grid_row_gap: float = 0.0
    # P5.1 — CSS visual effects
    border_radius: float = 0.0  # px corner radius
    shadow_offset_x: float = 0.0  # box-shadow x
    shadow_offset_y: float = 0.0  # box-shadow y
    shadow_radius: float = 0.0  # box-shadow blur radius
    shadow_color: object = None  # box-shadow Color
    shadow_enabled: bool = False  # whether box-shadow is active
    transform_tx: float = 0.0  # transform: translate(x px)
    transform_ty: float = 0.0  # transform: translate(y px)
    # P5.2 — CSS float (left/right)
    is_float: bool = False  # float: left | right
    float_direction: str = ""  # "left" | "right"
    # P6.1 — position: absolute (top/left offset relative to parent)
    is_absolute: bool = False
    abs_top: float = 0.0
    abs_left: float = 0.0
    # P9.2 — full-direction absolute offsets
    abs_right: float = 0.0
    abs_bottom: float = 0.0
    # P8.2 — position: relative (offset relative to flow position)
    is_relative: bool = False
    rel_top: float = 0.0
    rel_left: float = 0.0
    # P9.2 — full-direction relative offsets
    rel_right: float = 0.0
    rel_bottom: float = 0.0
    # P7.4 — position: sticky + full-direction offsets
    is_sticky: bool = False
    sticky_top: float = 0.0
    sticky_right: float = 0.0
    sticky_bottom: float = 0.0
    sticky_left: float = 0.0
    # P7.1 — z-index (stacking order)
    z_index: int = 0
    # P7.2 — display: table
    is_table: bool = False
    is_table_row: bool = False
    is_table_cell: bool = False
    border_collapse: bool = False
    text_overflow: str = ""  # "ellipsis" or ""
    line_clamp: int = 0  # max lines (0 = unlimited)
    # P1.1 — opacity
    opacity: float = 1.0
    overflow_hidden: bool = False
    # P14.1 — scroll container: overflow:auto / overflow:scroll
    overflow_x: str = ""   # "hidden" | "auto" | "scroll" | "" (visible)
    overflow_y: str = ""
    scroll_left: float = 0.0
    scroll_top: float = 0.0
    # P3.1 — embedded image pixel data (list of 0xRRGGBB u32, row-major)
    img_pixels: Optional[list] = None
    img_pixel_w: int = 0
    img_pixel_h: int = 0

    # P8.4 — text properties: letter-spacing, word-spacing, text-transform
    letter_spacing: float = 0.0  # px per character
    word_spacing: float = 0.0  # px per word
    text_transform: str = ""  # "uppercase" / "lowercase" / "capitalize" / ""
    # P20.5 — text-rendering quality hint ("auto" | "optimizeSpeed" |
    # "optimizeQuality").  The toy renderer maps this to whether the
    # curve rasterizer uses 2×2 antialiasing (quality) or skips it (speed).
    text_rendering: str = "auto"
    # P18 — line box model: font ascent/descent in px + baseline y.
    font_ascent_px: float = 0.0
    font_descent_px: float = 0.0
    baseline_y: float = 0.0
    vertical_align: str = ""  # "baseline" | "top" | "middle" | ""

    # P9.4 — clip-path: inset() + background linear-gradient toy
    clip_inset_top: float = 0.0  # clip-path: inset(T R B L)
    clip_inset_right: float = 0.0
    clip_inset_bottom: float = 0.0
    clip_inset_left: float = 0.0
    clip_enabled: bool = False
    gradient_c1: object = None  # linear-gradient start Color
    gradient_c2: object = None  # linear-gradient end Color
    gradient_enabled: bool = False
    gradient_angle: str = "to bottom"  # "to bottom" / "to right"
    # P9.4 — text-shadow
    text_shadow_enabled: bool = False
    text_shadow_offset_x: float = 0.0
    text_shadow_offset_y: float = 0.0
    text_shadow_radius: float = 0.0
    text_shadow_color: object = None

    # P10.3 — CSS outline
    outline_width: float = 0.0
    outline_color: object = None
    bg_image_set: bool = False
    # P10.1 — 1-based element-sibling index among the parent's children.
    child_index: int = 0
    # P13.3 — CSS filter: blur(Npx) / brightness(N) / contrast(N)
    filter_blur: float = 0.0       # blur radius in px (0 = no blur)
    filter_brightness: float = 1.0 # multiplier (1.0 = no change)
    filter_contrast: float = 1.0   # multiplier (1.0 = no change)
    filter_enabled: bool = False
    # P14.3 — CSS text-indent (first-line horizontal shift in px)
    text_indent: float = 0.0
    # P18 — line box model: font ascent/descent in px + baseline y.
    font_ascent_px: float = 0.0
    font_descent_px: float = 0.0
    baseline_y: float = 0.0
    vertical_align: str = ""  # "baseline" | "top" | "middle" | ""
    # P15.3 — CSS box-decoration-break / -webkit-appearance (toy: stored)
    box_decoration_break: str = ""   # "clone" | "slice" | ""
    webkit_appearance: str = ""      # "none" | "button" | "auto" | ""
    # P15.4 — CSS font-family (toy: "monospace" or "serif")
    font_family: str = "monospace"
    # P20.4 — CSS font-weight (numeric 100–900 or "normal"/"bold").
    # The toy stores the raw value; >= 600 selects the bold TTF variant.
    font_weight: int = 400
    # P16.3 — CSS text-justify / word-break / hyphens (toy: stored)
    text_justify: str = ""    # "none" | "inter-word" | "new" | "auto" | ""
    word_break: str = ""      # "normal" | "break-all" | "keep-all" | ""
    overflow_wrap: str = ""   # "normal" | "break-word" | ""
    hyphens: str = ""        # "none" | "auto" | "manual" | ""
    # P16.4 — CSS scroll-snap-type (toy: stored)
    scroll_snap_type: str = ""  # "none" | "x mandatory" | "y proximity" | ...
    # P26.4 — CSS min/max width/height constraints (toy: stored, clamped at finalize).
    min_width: float = 0.0
    max_width: float = 0.0  # 0 = none
    min_height: float = 0.0
    max_height: float = 0.0  # 0 = none

    # P28 — Form controls: type/checked/selected/value/state fields.
    # ``control_type`` is the DOM ``type`` attribute (lowered) for
    # <input> / <button>; "" for other elements.
    control_type: str = ""
    control_checked: bool = False
    control_value: str = ""
    control_options: list = field(default_factory=list)  # <select> option values
    control_option_index: int = 0
    control_file_name: str = ""
    control_focus: bool = False
    control_tabindex: int = 0
    control_required: bool = False

    IFRAME_PLACEHOLDER_H = 120.0
    IMG_DEFAULT_W = 32.0
    IMG_DEFAULT_H = 32.0
    # P33.R5 — dirty flag for incremental re-layout.
    dirty: bool = False

    def outer_height(self) -> float:
        return (
            self.rect.h
            + self.padding.top
            + self.padding.bottom
            + self.border.top
            + self.border.bottom
            + self.margin.top
            + self.margin.bottom
        )

    def border_rect(self) -> "Rect":
        """P2 (assessment report) — the box's *border-box* geometry,
        matching a real browser's ``getBoundingClientRect()``.

        The internal ``self.rect`` is recorded at the *content-box*
        origin: the toy's single-pass layout places content at
        ``x + margin_l + padding_l`` (the legacy offset drops the
        border, a known approximation) and the width is the content
        width.  ``getBoundingClientRect()`` reports the border-box
        instead: the origin is the border-box top-left corner
        (margin-box top-left shifted in by the margin — which the
        internal origin already is, plus one padding back from the
        content edge) and the size spans border + padding + content.
        The internal content-box ``self.rect`` stays untouched (235
        consumers — paint, hit-test, tests — read it in content
        coordinates); only this reporting seam converts.
        """
        return Rect(
            self.rect.x - self.padding.left,
            self.rect.y - self.padding.top,
            self.rect.w
            + self.padding.left + self.padding.right
            + self.border.left + self.border.right,
            self.rect.h
            + self.padding.top + self.padding.bottom
            + self.border.top + self.border.bottom,
        )


def shorthand_edges(value, font_px: float) -> EdgeSizes:
    """Resolve a margin/padding/border-width value into EdgeSizes."""
    from css import EdgeList, resolve_length

    if value is None:
        return EdgeSizes()
    if isinstance(value, EdgeList):
        return EdgeSizes(value.top, value.right, value.bottom, value.left)
    r = resolve_length(value, font_px)
    if r is None:
        return EdgeSizes()
    return EdgeSizes(r, r, r, r)


def _decode_data_uri(src: str):
    """P3.1 — decode a data: URI into (pixels, w, h) or None.

    Supports:
      data:image/png;base64,<b64>   — decode via a minimal PNG reader
      data:image/octet-stream;base64,<b64>  — raw RGB bytes (w×h×3)
      data:application/octet-stream;base64,<b64>  — raw RGB

    The pixel buffer is a flat list of 0x00RRGGBB u32 (row-major, same as
    paint.Bitmap.pixels).
    """
    import base64

    # Split "data:<mime>;base64,<payload>"
    if not src.startswith("data:"):
        return None
    header, _, b64 = src.partition(";base64,")
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None

    mime = header[5:].lower()  # strip "data:"
    if "png" in mime:
        return _decode_png_min(raw)
    # Raw RGB / octet-stream: interpret as w×h×3.
    if "octet" in mime or "jpeg" in mime or "jpg" in mime:
        # For raw RGB we need a hint: if the byte count is divisible by 3
        # and forms a plausible square/rect, treat as packed RGB.
        n = len(raw)
        if n % 3 == 0:
            # Heuristic: assume it's a small thumbnail.  Try to guess w.
            import math
            side = int(math.isqrt(n // 3))
            if side > 0 and side * side * 3 == n:
                w = side
                h = side
            else:
                # Fall back to a 1×n/3 strip.
                w = n // 3
                h = 1
            pixels = []
            for i in range(0, n, 3):
                pixels.append((raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2])
            return (pixels, w, h)
    return None


def _decode_png_min(data: bytes):
    """P3.1 — minimal PNG decoder (8-bit RGB, no interlace, no palette).

    Returns (pixels, w, h) or None.  Uses zlib for IDAT.  Skips unknown
    chunks.  Handles filter types 0–4 (None/Sub/Up/Average/Paeth).
    """
    import struct
    import zlib

    if len(data) < 8 or data[:4] != b"\x89PNG":
        return None
    # Scan chunks.
    pos = 8
    idat = b""
    width = height = 0
    bit_depth = 0
    color_type = 0
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", payload[:10])
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
        pos += 12 + length

    if width == 0 or height == 0 or color_type != 2 or bit_depth != 8:
        return None
    raw = zlib.decompress(idat)
    stride = width * 3
    # Undo PNG row filters.
    pixels_prev = bytearray(stride)
    pixels_cur = bytearray(stride)
    decoded = bytearray()
    offset = 0
    for y in range(height):
        ftype = raw[offset]
        offset += 1
        row = bytearray(raw[offset:offset + stride])
        offset += stride
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub
            for i in range(3, stride):
                row[i] = (row[i] + row[i - 3]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + pixels_prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = row[i - 3] if i >= 3 else 0
                row[i] = (row[i] + ((left + pixels_prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = row[i - 3] if i >= 3 else 0
                b = pixels_prev[i]
                c = pixels_prev[i - 3] if i >= 3 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                row[i] = (row[i] + pr) & 0xFF
        decoded.extend(row)
        pixels_prev[:] = row
    # Convert RGB triplets to 0xRRGGBB u32 list.
    out = []
    for i in range(0, len(decoded), 3):
        out.append((decoded[i] << 16) | (decoded[i + 1] << 8) | decoded[i + 2])
    return (out, width, height)


def _build_children(bx: LayoutBox, sn, my_font: float, parent_font_family: str, explicit_h: Optional[float], available_w: float | None = None) -> None:
    """P33.R5 — build the child boxes for *bx* (shared by the fast and
    slow paths of ``build_box``).  Extracted so the fast path can
    skip the ~50 property lookups while still producing the full
    layout tree.
    P33.P1 — propagate the parent's content width (and height) to each
    child's StyledNode so that ``width: calc(100% - 20px)`` and
    ``height: calc(100% - 20px)`` resolve against the containing
    block's actual dimensions (CSS2.1 §10.6.5).
    P34.6c — leaf fast path: a box with zero children (the common case
    for the 10k-node benchmark where ~50 % of boxes are leaves) pays
    no iteration cost at all; the legacy code still entered the
    function body, computed parent_w/parent_h, and looped over an
    empty sn.children.
    """
    if not sn.children:
        return
    # P34.8 — most boxes in a typical document have no display:none /
    # script / style children, so the per-child ctag lower() + membership
    # test is wasted work.  Branch on a cheap flag computed once per
    # call (the "common" path skips the per-child work entirely).
    _has_special = False
    for csn in sn.children:
        ct = (csn.dom.tag or "").lower()
        if ct in ("script", "style") or csn.is_display_none():
            _has_special = True
            break
    # P33.P1 — parent content dimensions for %/calc resolution.
    parent_w = bx.explicit_width if bx.explicit_width is not None else (available_w or 0.0)
    parent_h = bx.explicit_height
    if _has_special:
        for csn in sn.children:
            cdom = csn.dom
            ctag = (cdom.tag or "").lower()
            if ctag in ("script", "style"):
                continue
            # P33.P1 — set containing-block dimensions on the child's StyledNode.
            csn.parent_width_px = parent_w
            if parent_h is not None:
                csn.parent_height_px = parent_h
            cb = build_box(csn, my_font, parent_font_family,
                           explicit_h if explicit_h is not None else None)
            csn.__box = cb  # P0 (agent report) — back-link for flex sizing
            cb.parent = bx
            if csn.is_display_none():
                cb.rect = Rect()
            # P15.4 — inherit font_family from parent when child has none.
            # P6.2 — propagate line-clamp / text-overflow to text children.
            if cb.box_type == BoxType.TEXT:
                cb.line_clamp = bx.line_clamp
                cb.text_overflow = bx.text_overflow
            bx.children.append(cb)
    else:
        # P34.8 — the fast path: no script / style / display:none children,
        # so the per-child ctag lower() + membership test is skipped.
        _append = bx.children.append
        _build_box = build_box
        _box_rect = Rect
        for csn in sn.children:
            csn.parent_width_px = parent_w
            if parent_h is not None:
                csn.parent_height_px = parent_h
            cb = _build_box(csn, my_font, parent_font_family,
                            explicit_h if explicit_h is not None else None)
            cb.parent = bx
            if cb.box_type == BoxType.TEXT:
                cb.line_clamp = bx.line_clamp
                cb.text_overflow = bx.text_overflow
            _append(cb)


def _edges_for(sn, prop, my_font):
    """Resolve a margin/padding/border value into EdgeSizes.

    P25.4 — after shorthand expansion, the shorthand key may hold an
    EdgeList (or a single value), while the longhand keys hold per-side
    values.  Per CSS2.1, longhands override the shorthand's per-side
    default.  This helper builds the final EdgeSizes from both sources.
    P33.R5 — module-level imports of EdgeList / resolve_length (the
    function used to re-import them on every call; 6000+ calls per
    1000-node layout).
    """
    base = EdgeSizes()
    # 1. Shorthand key (margin / padding / border-width).
    sv = sn.props.get(prop)
    if sv is not None:
        if isinstance(sv, EdgeList):
            base = EdgeSizes(sv.top, sv.right, sv.bottom, sv.left)
        else:
            r = _resolve_length(sv, my_font)
            if r is not None:
                base = EdgeSizes(r, r, r, r)
    # 2. Longhand overrides (margin-top, margin-right, …).
    for i, suffix in enumerate(("-top", "-right", "-bottom", "-left")):
        lv = sn.props.get(prop + suffix)
        if lv is not None:
            r = _resolve_length(lv, my_font)
            if r is not None:
                v = getattr(base, ("top", "right", "bottom", "left")[i])
                if i == 0:
                    base.top = r
                elif i == 1:
                    base.right = r
                elif i == 2:
                    base.bottom = r
                else:
                    base.left = r
    return base


def build_box(sn: StyledNode, parent_font_px: float,
              parent_font_family: str = "", parent_height_px: float | None = None,
              available_w: float | None = None) -> LayoutBox:
    node = sn.dom
    # P0 (agent report) — keep the StyledNode reachable from the box so
    # layout-time passes (e.g. flex basis capping) can re-derive explicit
    # width/height that the fast path below zeroes out.  Each child box
    # back-links to its child StyledNode via ``csn.__box``.
    # P33.R5 — cache the is_element result (hot path: called 2000+ times
    # per 1000-node layout).  Node.node_type is a plain string; comparing
    # it directly is faster than the method call.
    is_element = (node.node_type == "element")
    # P34.7 — the font-size is a parse-time invariant; its resolved px
    # value is memoized on the StyledNode (css.prop_font_px), so this
    # call is a plain attribute read after the first build_box for a
    # node.
    my_font = sn.prop_font_px()
    # P34.7 — lowercase tag + cheap precompute of the *common* flags the
    # slow path reads (is_form_tag / img / iframe / …).  The legacy code
    # recomputed the tag string compare in six separate `if` blocks;
    # one lower() + one set of membership tests is cheaper.
    tag = (node.tag or "").lower()

    # P34.7 — child index in O(1) with an address-reuse guard (the
    # legacy _child_index_for already memoizes the per-parent id→index
    # map, so the per-child cost after the first child of a parent is a
    # single dict hit).
    child_index = 0
    parent_dom = node.parent
    if is_element and parent_dom is not None:
        child_index = _child_index_for(node, parent_dom)

    # P33.R5 — text content: collect direct text-node children.
    # P34.8 — the legacy code scanned node.children on every element
    # build_box call (2000+ per 1000-node layout) even when the element
    # had no text-node children (the common case for divs / blocks).
    # A len==0 short-circuit + a type-guarded scan cuts the cost of the
    # "no text" path from O(children) to O(1).
    if is_element:
        _kids = node.children
        if not _kids:
            text_str = ""
        else:
            parts: list[str] = []
            _append = parts.append
            for c in _kids:
                if c.node_type != "element":
                    _append(c.text or "")
            text_str = "".join(parts) if parts else ""
    else:
        text_str = node.text or ""

    # P34.3 — text-node fast path: a text node (node_type != "element")
    # with a small props dict and no special properties skips the ~100
    # property reads + regex calls of the slow path.  The slow path's
    # text-box construction is dominated by the form-control / iframe /
    # img / caption blocks that never apply to a plain text node, plus
    # ~40 sn.props.get() lookups for CSS that a text node rarely carries.
    # This is the single biggest layout-stage cost on the 10k-node
    # benchmark (text nodes ≈ 50 % of all build_box calls).
    if not is_element:
        _prop = sn.props
        if len(_prop) <= _FAST_PATH_MAX_PROPS and not (_SPECIAL_PROPS & _prop.keys()):
            _col = _DEFAULT_TEXT_COLOR
            _col_raw = _prop.get("color")
            if _col_raw is not None:
                if isinstance(_col_raw, Color):
                    _col = _col_raw
                else:
                    _rv = sn._resolve_var(_col_raw)
                    if _rv is not None and isinstance(_rv, Color):
                        _col = _rv
            _bg = None
            _bg_raw = _prop.get("background")
            if _bg_raw is not None:
                if isinstance(_bg_raw, Color):
                    _bg = _bg_raw
                else:
                    _rv = sn._resolve_var(_bg_raw)
                    if _rv is not None and isinstance(_rv, Color):
                        _bg = _rv
            _bxBLOCK = LayoutBox(
                BoxType.TEXT,
                Rect(),
                EdgeSizes(), EdgeSizes(), EdgeSizes(),
                text_content=text_str,
                text_color=_col,
                background=_bg,
                border_color=_DEFAULT_BORDER_COLOR,
                font_px=my_font,
                text_align="left",
                explicit_width=None,
                explicit_height=None,
                line_height_px=my_font * 1.2,
                dom=None,
                child_index=child_index,
                font_family=parent_font_family or "monospace",
            )
            return _bxBLOCK

    background = sn.background()
    color = sn.color()
    border_color = sn.get_color("border-color", _DEFAULT_BORDER_COLOR)
    text_align = sn.text_align()

    # P28 — default border for form controls: even without a CSS
    # border-width, checkbox/radio/select/text-input/file/textarea/button
    # boxes carry a 1px border so the renderer can draw their outline.
    is_form_tag = tag in ("input", "select", "textarea", "button")
    _force_form_border = None
    if is_form_tag and "border-width" not in sn.props:
        _force_form_border = EdgeSizes(top=1.0, right=1.0, bottom=1.0, left=1.0)

    # P33.R5 — <input> / <textarea> / <img> / <canvas> / <svg> / <video> / <audio>:
    # pull width/height from DOM attributes.  For the common case (no dom
    # attrs, no form tag) skip the entire block.
    dom_w: Optional[float] = None
    dom_h: Optional[float] = None
    if tag in ("img", "input", "textarea", "canvas", "svg", "video", "audio"):
        for attr, target in (("width", "w"), ("height", "h")):
            raw = node.attr(attr)
            if raw:
                try:
                    val = float(raw)
                except ValueError:
                    continue
                if target == "w":
                    dom_w = val
                else:
                    dom_h = val
        # P28 — <textarea> HTML default: 4 rows × 20 cols.
        if tag == "textarea":
            cols_raw = node.attr("cols")
            rows_raw = node.attr("rows")
            if dom_w is None:
                try:
                    dom_w = int(cols_raw) * 8.0 if cols_raw else 160.0
                except ValueError:
                    dom_w = 160.0
            if dom_h is None:
                try:
                    dom_h = int(rows_raw) * 16.0 if rows_raw else 4 * 16.0
                except ValueError:
                    dom_h = 4 * 16.0
        # P33.P2 — <canvas> HTML default: 300×150 (HTML spec).
        if tag == "canvas":
            if dom_w is None:
                dom_w = 300.0
            if dom_h is None:
                dom_h = 150.0
        # P33.P3 — <svg> / <video> / <audio> placeholder default:
        # 300×150 (matches the browser's intrinsic size for these elements).
        if tag in ("svg", "video", "audio"):
            if dom_w is None:
                dom_w = 300.0
            if dom_h is None:
                dom_h = 150.0

    # P3.1 — embedded base64 image: decode data URI into pixel buffer.
    img_pixels: Optional[list] = None
    img_pixel_w = 0
    img_pixel_h = 0
    if tag == "img":
        src = node.attr("src") or ""
        if src.startswith("data:"):
            decoded = _decode_data_uri(src)
            if decoded is not None:
                img_pixels, img_pixel_w, img_pixel_h = decoded
                # Use intrinsic size unless overridden.
                if dom_w is None:
                    dom_w = float(img_pixel_w)
                if dom_h is None:
                    dom_h = float(img_pixel_h)

    # P26.3 — propagate the containing block's content height so that
    # percentage *heights* on this box resolve against the parent's
    # actual height (CSS2.1 §10.6.5).  ``parent_height_px`` is the
    # parent box's content height when known (None at the viewport root).
    if parent_height_px is not None:
        sn.parent_height_px = parent_height_px
    # P33.P1 — propagate the containing block's content width so that
    # percentage / calc-mixed %/px *widths* resolve against the parent's
    # actual width.  ``sn.parent_width_px`` is set by _build_children
    # before build_box is called (CSS apply-time resolution is
    # unavailable; layout-time resolution is correct here).
    explicit_w = sn.width_px()
    explicit_h = sn.height_px()
    if explicit_w is None and dom_w is not None:
        explicit_w = dom_w
    if explicit_h is None and dom_h is not None:
        explicit_h = dom_h

    # P33.R5 — fast path: for a plain block element with no special CSS
    # properties, skip the ~50 property lookups and use defaults.
    # Uses module-level _SPECIAL_PROPS frozenset + tag exclusion set.
    # P33.P1 — also require the props map to be small (≤ _FAST_PATH_MAX_PROPS
    # keys) to avoid the frozenset intersection on the hot path for
    # 10k-node documents where most divs carry 4 properties
    # (width/height/color/font-size).
    _prop = sn.props
    if (is_element
            and not is_form_tag
            and tag not in _FAST_PATH_EXCLUDED_TAGS
            and len(_prop) <= _FAST_PATH_MAX_PROPS
            and not (_SPECIAL_PROPS & _prop.keys())):
        # P33.P1 — super-fast path: no special properties, small props
        # map.  Read width/height/color/font directly from _prop dict
        # (no method calls on sn, no _resolve_var, no _edges_for for
        # the 3 common empty edge cases).
        margin = EdgeSizes()
        padding = EdgeSizes()
        border = EdgeSizes()
        # P33.P1 — resolve width/height from the raw props dict
        # (skips sn.width_px() / sn.height_px() method calls which
        # go through _resolve_var + resolve_length + getattr).
        explicit_w = None
        explicit_h = None
        wv = _prop.get("width")
        if wv is not None:
            if isinstance(wv, Length):
                if wv.unit == "px":
                    explicit_w = wv.value
                elif wv.unit == "em":
                    explicit_w = wv.value * my_font
                elif wv.unit == "%":
                    ref = sn.parent_width_px if sn.parent_width_px is not None else my_font
                    explicit_w = wv.value * ref / 100.0
                else:
                    explicit_w = wv.value
            elif isinstance(wv, CalcExpr):
                ref = sn.parent_width_px if sn.parent_width_px is not None else my_font
                total = 0.0
                for tv, tu in wv.terms:
                    if tu == "%":
                        total += tv * ref / 100.0
                    elif tu == "em":
                        total += tv * my_font
                    else:
                        total += tv
                explicit_w = total
            elif isinstance(wv, VarExpr):
                rv = sn._resolve_var(wv)
                if rv is not None:
                    explicit_w = _resolve_length(rv, my_font, ref_px=sn.parent_width_px)
            elif isinstance(wv, Keyword):
                try:
                    explicit_w = float(wv.value)
                except ValueError:
                    pass
        hv = _prop.get("height")
        if hv is not None:
            if isinstance(hv, Length):
                if hv.unit == "px":
                    explicit_h = hv.value
                elif hv.unit == "em":
                    explicit_h = hv.value * my_font
                elif hv.unit == "%":
                    ref = sn.parent_height_px if sn.parent_height_px is not None else my_font
                    explicit_h = hv.value * ref / 100.0
                else:
                    explicit_h = hv.value
            elif isinstance(hv, CalcExpr):
                ref = sn.parent_height_px if sn.parent_height_px is not None else my_font
                total = 0.0
                for tv, tu in hv.terms:
                    if tu == "%":
                        total += tv * ref / 100.0
                    elif tu == "em":
                        total += tv * my_font
                    else:
                        total += tv
                explicit_h = total
            elif isinstance(hv, VarExpr):
                rv = sn._resolve_var(hv)
                if rv is not None:
                    explicit_h = _resolve_length(rv, my_font, ref_px=sn.parent_height_px)
            elif isinstance(hv, Keyword):
                try:
                    explicit_h = float(hv.value)
                except ValueError:
                    pass
        # P33.P1 — resolve color/background/font directly from _prop dict.
        bg_raw = _prop.get("background")
        bg_color = None
        if bg_raw is not None:
            if isinstance(bg_raw, Color):
                bg_color = bg_raw
            else:
                rv = sn._resolve_var(bg_raw)
                if rv is not None and isinstance(rv, Color):
                    bg_color = rv
        col_raw = _prop.get("color")
        col_val = _DEFAULT_TEXT_COLOR
        if col_raw is not None:
            if isinstance(col_raw, Color):
                col_val = col_raw
            else:
                rv = sn._resolve_var(col_raw)
                if rv is not None and isinstance(rv, Color):
                    col_val = rv
        ta = "left"
        ta_raw = _prop.get("text-align")
        if ta_raw is not None:
            if isinstance(ta_raw, Keyword):
                ta = ta_raw.value.lower()
        ff_raw = _prop.get("font-family")
        font_family = parent_font_family or "monospace"
        if ff_raw is not None:
            ff_str = getattr(ff_raw, "value", ff_raw)
            font_family = font.family_from_css(str(ff_str))
            bx = LayoutBox(
                BoxType.BLOCK,
                Rect(),
                padding, border, margin,
                text_content=text_str or None,
                text_color=col_val,
                background=bg_color,
                border_color=_DEFAULT_BORDER_COLOR,
                font_px=my_font,
                text_align=ta,
                explicit_width=explicit_w,
                explicit_height=explicit_h,
                line_height_px=my_font * 1.2,  # P33.P1 — fast-path default line height
                children=[],
                dom=node,
                child_index=child_index,
                font_family=font_family,
            )
            bx.dom_sn = sn  # P0 (agent report) — back-link for flex sizing
            _build_children(bx, sn, my_font, parent_font_family, explicit_h, available_w)
            return bx
    # P1.4 — Flexbox: read display:flex / flex-direction / justify-content / align-items.
    is_flex = False
    flex_direction = "row"
    flex_wrap = "nowrap"
    justify_content = "start"
    align_items = "stretch"
    align_content = "stretch"
    disp = sn.display()
    if disp == "flex":
        is_flex = True
        flex_direction = sn.get_keyword("flex-direction", "row")
        justify_content = sn.get_keyword("justify-content", "start")
        align_items = sn.get_keyword("align-items", "stretch")
    # P27 — read flex-wrap / align-content on the flex container.
    flex_wrap = sn.get_keyword("flex-wrap", "nowrap")
    align_content = sn.get_keyword("align-content", "stretch")
    # P0 (retest report) — flex gap: the `gap` / `column-gap` / `row-gap`
    # shorthands.  `gap: 10px` parses to Length(10.0,"px"); `gap: 10px 20px`
    # parses to Keyword("10px 20px").  For a row container the main-axis
    # gap is column-gap; for column it is row-gap.  Read via sn.props
    # directly (get_number handles Length; get_keyword handles the
    # multi-token Keyword form).
    flex_gap = 0.0
    _fd = flex_direction
    _def_gap = sn.get_number("gap", -1.0)
    if _def_gap >= 0.0:
        # `gap: 10px` (single value) → both axes = 10px.
        flex_gap = _def_gap
    else:
        # `gap: 10px 20px` (multi-token Keyword) → split.
        _gap_kw = sn.get_keyword("gap", "")
        if _gap_kw:
            _gp = _gap_kw.split()
            if len(_gp) >= 2:
                flex_gap = float(_gp[1].strip()[:-2]) if _gp[1].strip().endswith("px") else float(_gp[1] or 0)
            else:
                flex_gap = float(_gap_kw.strip()[:-2]) if _gap_kw.strip().endswith("px") else 0.0
        else:
            flex_gap = 0.0
    if _fd == "column":
        # row-gap beats the `gap` shorthand for the main (vertical) axis.
        _rg = sn.get_number("row-gap", -1.0)
        if _rg >= 0.0:
            flex_gap = _rg
        else:
            _rg_kw = sn.get_keyword("row-gap", "")
            if _rg_kw and _rg_kw.strip().endswith("px"):
                flex_gap = float(_rg_kw.strip()[:-2])
    else:
        _cg = sn.get_number("column-gap", -1.0)
        if _cg >= 0.0:
            flex_gap = _cg
        else:
            _cg_kw = sn.get_keyword("column-gap", "")
            if _cg_kw and _cg_kw.strip().endswith("px"):
                flex_gap = float(_cg_kw.strip()[:-2])
    # P4.3 — CSS Grid: read display:grid / grid-template-columns / grid-template-rows.
    is_grid = False
    grid_columns = 0
    grid_rows = 0
    grid_cols_frac: list = []
    grid_rows_frac: list = []
    grid_gap = 0.0
    grid_row_gap = 0.0
    if disp == "grid":
        is_grid = True
        gtc = sn.get_keyword("grid-template-columns", "")
        if gtc:
            grid_columns = len(gtc.split())
            for _tok in gtc.split():
                _tok = _tok.strip()
                if _tok.endswith("fr"):
                    try:
                        grid_cols_frac.append((float(_tok[:-2]), "fr"))
                    except ValueError:
                        grid_cols_frac.append((1.0, "fr"))
                elif _tok.endswith("px"):
                    try:
                        grid_cols_frac.append((float(_tok[:-2]), "px"))
                    except ValueError:
                        grid_cols_frac.append((1.0, "px"))
                else:
                    grid_cols_frac.append((1.0, "fr"))
        gtr = sn.get_keyword("grid-template-rows", "")
        if gtr:
            grid_rows = len(gtr.split())
            for _tok in gtr.split():
                _tok = _tok.strip()
                if _tok.endswith("fr"):
                    try:
                        grid_rows_frac.append((float(_tok[:-2]), "fr"))
                    except ValueError:
                        grid_rows_frac.append((1.0, "fr"))
                elif _tok.endswith("px"):
                    try:
                        grid_rows_frac.append((float(_tok[:-2]), "px"))
                    except ValueError:
                        grid_rows_frac.append((1.0, "px"))
                else:
                    grid_rows_frac.append((1.0, "fr"))
        # P0 (retest report) — grid gap.
        def _px_of(raw: str) -> float:
            raw = raw.strip()
            if raw.endswith("px"):
                try:
                    return float(raw[:-2])
                except ValueError:
                    return 0.0
            try:
                return float(raw)
            except ValueError:
                return 0.0
        _cg_num = sn.get_number("column-gap", -1.0)
        if _cg_num >= 0.0:
            grid_gap = _cg_num
        else:
            _gap_raw = sn.get_keyword("column-gap", "")
            grid_gap = _px_of(_gap_raw) if _gap_raw else 0.0
            if grid_gap == 0.0:
                _gap_raw = sn.get_keyword("row-gap", "")
                grid_gap = _px_of(_gap_raw) if _gap_raw else 0.0
            if grid_gap == 0.0:
                _gap_num = sn.get_number("gap", -1.0)
                if _gap_num >= 0.0:
                    grid_gap = _gap_num
                else:
                    _gap_raw = sn.get_keyword("gap", "")
                    if _gap_raw:
                        _gp = _gap_raw.split()
                        grid_gap = _px_of(_gp[0]) if _gp else 0.0
        _rg_num = sn.get_number("row-gap", -1.0)
        if _rg_num >= 0.0:
            grid_row_gap = _rg_num
        else:
            _rgap_raw = sn.get_keyword("row-gap", "")
            grid_row_gap = _px_of(_rgap_raw) if _rgap_raw else grid_gap
            if grid_row_gap == 0.0:
                _gap_num = sn.get_number("gap", -1.0)
                if _gap_num >= 0.0:
                    grid_row_gap = _gap_num
                else:
                    _gap_raw = sn.get_keyword("gap", "")
                    if _gap_raw:
                        _gp = _gap_raw.split()
                        if len(_gp) >= 2:
                            grid_row_gap = _px_of(_gp[1])
                        else:
                            grid_row_gap = _px_of(_gap_raw)

    # P7.2 — display:table / table-row / table-cell + border-collapse.
    # P0 (retest report) — honour UA-style `display: table`/`table-row`/
    # `table-cell` (used by browser._default_css), not just tag names:
    # the toy DOM parser keeps tag names lowercase, but documents that
    # style table rows/cells with display only (no tag) must still
    # route through _layout_table.
    # P7.2 — display:table / table-row / table-cell + border-collapse.
    # P0 (retest report) — fall back to the tag name when the computed
    # display is a plain block: the UA stylesheet (browser._default_css)
    # supplies ``display: table``/``table-row``/``table-cell`` on the
    # render_document path, but the bare css.apply route (layout probes
    # and unit tests) leaves table tags with the default block display,
    # so the tag-name check keeps _layout_table reachable in both cases.
    is_table = disp in ("table", "inline-table") or tag == "table"
    is_table_row = tag == "tr" or disp in ("table-row",)
    is_table_cell = tag in ("td", "th") or disp in ("table-cell",)
    border_collapse = sn.get_keyword("border-collapse", "") == "collapse"
    # P27 — flex shrink / basis (read even on non-flex items; used when
    # the parent is flex).  flex_basis None means "auto": fall back to
    # the item's explicit/main-axis size or its content size.
    flex_shrink = sn.get_number("flex-shrink", 1.0)
    flex_basis = sn.props.get("flex-basis")
    if isinstance(flex_basis, Length):
        if flex_basis.unit == "px":
            flex_basis = flex_basis.value
        elif flex_basis.unit == "em":
            flex_basis = flex_basis.value * my_font
        elif flex_basis.unit == "%":
            # P27 — percentage flex-basis is stored raw; _layout_flex
            # resolves it against the container's main-axis size.
            flex_basis = flex_basis.value / 100.0
        else:
            flex_basis = flex_basis.value
    elif isinstance(flex_basis, Keyword):
        flex_basis = None  # "auto"
    else:
        flex_basis = None
    flex_order = int(sn.get_number("order", 0.0))
    align_self = sn.get_keyword("align-self", "")

    # P3.2 — flex-grow (on the element itself, used when parent is flex).
    # When a flex child has flex-grow > 0, its CSS width is a *basis* for
    # the grow distribution, not a hard constraint.
    flex_grow = sn.get_number("flex-grow", 0.0)
    if flex_grow > 0:
        explicit_w = None

    # P1.1 — opacity (0.0–1.0) and overflow.
    opacity = sn.opacity()
    overflow_hidden = sn.overflow() == "hidden"
    # P14.1 — scroll container: overflow-x / overflow-y (toy: single "overflow"
    # applies to both axes when x/y are not specified separately).
    overflow_x = ""
    overflow_y = ""
    ov_x = sn.get_keyword("overflow-x", "")
    ov_y = sn.get_keyword("overflow-y", "")
    ov_single = sn.overflow()
    if ov_x in ("hidden", "auto", "scroll"):
        overflow_x = ov_x
    if ov_y in ("hidden", "auto", "scroll"):
        overflow_y = ov_y
    if not overflow_x and ov_single in ("hidden", "auto", "scroll"):
        overflow_x = ov_single
    if not overflow_y and ov_single in ("hidden", "auto", "scroll"):
        overflow_y = ov_single
    is_scroll_container = ("auto" in (overflow_x, overflow_y) or "scroll" in (overflow_x, overflow_y))
    # P14.1 — scrollLeft / scrollTop inline style values (toy: manual scroll offset).
    scroll_left = sn.get_number("scroll-left", 0.0)
    scroll_top = sn.get_number("scroll-top", 0.0)

    # P5.1 — border-radius / box-shadow / transform:translate.
    import re as _re

    border_radius = 0.0
    shadow_offset_x = 0.0
    shadow_offset_y = 0.0
    shadow_radius = 0.0
    shadow_color = None
    shadow_enabled = False
    transform_tx = 0.0
    transform_ty = 0.0

    br = sn.props.get("border-radius")
    if br is not None:
        # Length(value=20.0, unit='px') → use .value directly.
        # Keyword("20px") → regex-match.
        if isinstance(br, Length):
            border_radius = br.value
        else:
            m = _re.match(r"([\d.]+)\s*px", str(getattr(br, "value", br)))
            if m:
                border_radius = float(m.group(1))

    bs = sn.props.get("box-shadow")
    if bs is not None:
        # box-shadow: 5px 5px 10px black — parse_value keeps the whole multi-
        # token value as a Keyword (the raw CSS string).
        raw = getattr(bs, "value", None)
        if raw is None:
            raw = str(bs)
        parts = raw.split()
        if len(parts) >= 2:
            def _num(tok: str) -> float:
                m2 = _re.match(r"(-?[\d.]+)", tok)
                return float(m2.group(1)) if m2 else 0.0
            shadow_offset_x = _num(parts[0])
            shadow_offset_y = _num(parts[1])
            if len(parts) >= 3:
                shadow_radius = _num(parts[2])
            if len(parts) >= 4:
                sc = parse_color(parts[3])
                if sc is not None:
                    shadow_color = sc
            shadow_enabled = True

    tf = sn.props.get("transform")
    if tf is not None:
        raw = getattr(tf, "value", None)
        if raw is None:
            raw = str(tf)
        m = _re.search(r"translate\s*\(\s*(-?[\d.]+)px\s*,\s*(-?[\d.]+)px\s*\)", raw)
        if m:
            transform_tx = float(m.group(1))
            transform_ty = float(m.group(2))

    # P5.2 — float: left | right
    is_float = False
    float_direction = ""
    fl = sn.props.get("float")
    if fl is not None:
        fl_kw = sn.get_keyword("float", "")
        if fl_kw in ("left", "right"):
            is_float = True
            float_direction = fl_kw

    # P6.1 — position: absolute with top/left offset.
    # P9.2 — full-direction: top/left/right/bottom for absolute & relative.
    is_absolute = False
    abs_top = 0.0
    abs_left = 0.0
    abs_right = 0.0
    abs_bottom = 0.0
    # P8.2 — position: relative with top/left offset (offset from flow position).
    is_relative = False
    rel_top = 0.0
    rel_left = 0.0
    rel_right = 0.0
    rel_bottom = 0.0
    pos = sn.get_keyword("position", "")
    if pos == "absolute":
        is_absolute = True
        for prop_name in ("top", "left", "right", "bottom"):
            pv = sn.props.get(prop_name)
            if pv is None:
                continue
            if isinstance(pv, Length):
                v = pv.value
            else:
                m2 = _re.match(r"(-?[\d.]+)\s*px", str(getattr(pv, "value", pv)))
                v = float(m2.group(1)) if m2 else 0.0
            if prop_name == "top":
                abs_top = v
            elif prop_name == "left":
                abs_left = v
            elif prop_name == "right":
                abs_right = v
            else:
                abs_bottom = v
    elif pos == "relative":
        is_relative = True
        for prop_name in ("top", "left", "right", "bottom"):
            pv = sn.props.get(prop_name)
            if pv is None:
                continue
            if isinstance(pv, Length):
                v = pv.value
            else:
                m2 = _re.match(r"(-?[\d.]+)\s*px", str(getattr(pv, "value", pv)))
                v = float(m2.group(1)) if m2 else 0.0
            if prop_name == "top":
                rel_top = v
            elif prop_name == "left":
                rel_left = v
            elif prop_name == "right":
                rel_right = v
            else:
                rel_bottom = v

    # P7.4 — position: sticky + full-direction offsets.
    is_sticky = False
    sticky_top = 0.0
    sticky_right = 0.0
    sticky_bottom = 0.0
    sticky_left = 0.0
    if pos == "sticky":
        is_sticky = True
        for prop_name in ("top", "right", "bottom", "left"):
            pv = sn.props.get(prop_name)
            if pv is None:
                continue
            if isinstance(pv, Length):
                v = pv.value
            else:
                m2 = _re.match(r"(-?[\d.]+)\s*px", str(getattr(pv, "value", pv)))
                v = float(m2.group(1)) if m2 else 0.0
            if prop_name == "top":
                sticky_top = v
            elif prop_name == "right":
                sticky_right = v
            elif prop_name == "bottom":
                sticky_bottom = v
            else:
                sticky_left = v

    # P7.1 — z-index (stacking order for positioned/floated elements).
    z_index = 0
    zidx = sn.props.get("z-index")
    if zidx is not None:
        try:
            if isinstance(zidx, Keyword):
                z_index = int(float(zidx.value))
            elif isinstance(zidx, Length):
                z_index = int(zidx.value)
            else:
                z_index = int(float(str(zidx)))
        except (ValueError, TypeError):
            z_index = 0

    # P26.4 — CSS2.1 min/max constraints (toy: px only).
    # ``get_number`` resolves % against the parent font size; for
    # percentage min/max the layout finalize pass re-resolves against
    # the actual containing width (layout_block, P26.4 clamp).
    min_width = sn.get_number("min-width", 0.0)
    max_width = sn.get_number("max-width", 0.0)
    min_height = sn.get_number("min-height", 0.0)
    max_height = sn.get_number("max-height", 0.0)

    # P28 — form control state fields (from DOM attributes).
    # ``type`` on <input>/<button>; ``checked``/``selected``/``value``/
    # ``name``/``required``/``tabindex``/``formaction`` read directly
    # from the DOM node.  <select> stores its <option> value list +
    # the index of the selected option.
    control_type = ""
    control_checked = False
    control_value = ""
    control_options: list = []
    control_option_index = 0
    control_file_name = ""
    control_focus = node.attr("focus") is not None
    control_tabindex = 0
    control_required = False
    if is_element:
        for attr, target in (("type", "type"), ("value", "value"),
                             ("checked", "checked"), ("name", "name"),
                             ("tabindex", "tabindex"), ("required", "required"),
                             ("formaction", "formaction")):
            raw = node.attr(attr)
            # Boolean attributes (checked/required) are present as "" —
            # use `is not None` so the empty-string case still counts.
            if target in ("checked", "required"):
                if raw is None:
                    continue
                if target == "checked":
                    control_checked = True
                else:
                    control_required = True
                continue
            if raw:
                if target == "type":
                    control_type = raw.lower()
                elif target == "value":
                    control_value = raw
                elif target == "tabindex":
                    try:
                        control_tabindex = int(raw)
                    except ValueError:
                        control_tabindex = 0
        if tag == "input" and control_type == "file":
            # P28 — file input shows the local file name (toy: from
            # a hypothetical "value" attr or default placeholder).
            control_file_name = control_value or ""
        if tag == "select":
            # P28 — collect <option> values; the selected option is the
            # first with the "selected" attribute, else index 0.
            for opt in node.children:
                if opt.is_element() and (opt.tag or "").lower() == "option":
                    control_options.append(opt.attr("value") or (opt.text or ""))
                    if opt.attr("selected") is not None:
                        control_option_index = len(control_options) - 1
            if control_value == "" and control_options:
                control_value = control_options[control_option_index]
        # P28 — checkbox / radio: <input type="checkbox"> without a
        # value attribute defaults to "on" per the HTML spec.
        if tag == "input" and control_type in ("checkbox", "radio") and control_value == "":
            control_value = "on"

    # P6.2 — text-overflow: ellipsis + line-clamp (max visible lines).
    text_overflow = ""
    line_clamp = 0
    to = sn.get_keyword("text-overflow", "")
    if to == "ellipsis":
        text_overflow = "ellipsis"
    lc = sn.props.get("line-clamp")
    if lc is None:
        lc = sn.props.get("-webkit-line-clamp")
    if lc is not None:
        try:
            if isinstance(lc, Keyword):
                line_clamp = int(float(lc.value))
            else:
                line_clamp = int(float(str(lc)))
        except (ValueError, TypeError):
            line_clamp = 0

    # P8.4 — letter-spacing / word-spacing / text-transform.
    letter_spacing = 0.0
    ls = sn.props.get("letter-spacing")
    if ls is not None:
        if isinstance(ls, Length):
            letter_spacing = ls.value
        else:
            m2 = _re.match(r"(-?[\d.]+)\s*px", str(getattr(ls, "value", ls)))
            letter_spacing = float(m2.group(1)) if m2 else 0.0

    word_spacing = 0.0
    ws = sn.props.get("word-spacing")
    if ws is not None:
        if isinstance(ws, Length):
            word_spacing = ws.value
        else:
            m2 = _re.match(r"(-?[\d.]+)\s*px", str(getattr(ws, "value", ws)))
            word_spacing = float(m2.group(1)) if m2 else 0.0

    text_transform = sn.get_keyword("text-transform", "")
    if text_transform not in ("uppercase", "lowercase", "capitalize"):
        text_transform = ""

    # P20.5 — text-rendering quality hint ("auto" | "optimizeSpeed" |
    # "optimizeQuality").  The CSS parser stores the raw value as a
    # Keyword (lowercase); normalize case and accept anything else as
    # "auto".
    text_rendering = sn.get_keyword("text-rendering", "auto")
    text_rendering = text_rendering.strip()
    if text_rendering.lower() == "optimizespeed":
        text_rendering = "optimizeSpeed"
    elif text_rendering.lower() == "optimizequality":
        text_rendering = "optimizeQuality"
    elif text_rendering.lower() == "auto" or text_rendering == "":
        text_rendering = "auto"
    else:
        text_rendering = "auto"

    # P9.4 — clip-path: inset(T R B L) + background linear-gradient toy.
    import re as _re2

    def _raw_str(v) -> str:
        """Extract the raw string from a Value object (Keyword/Length/Color/str)."""
        if isinstance(v, str):
            return v
        val = getattr(v, "value", v)
        # Length has .value (number) and .unit (string) → "4px".
        unit = getattr(v, "unit", "")
        return f"{val}{unit}"

    def _inset_num(tok: str) -> float:
        """Parse a clip-path inset value like '10px' or '5' → float pixels."""
        m = _re2.match(r"(-?[\d.]+)", tok)
        return float(m.group(1)) if m else 0.0

    clip_inset_top = 0.0
    clip_inset_right = 0.0
    clip_inset_bottom = 0.0
    clip_inset_left = 0.0
    clip_enabled = False
    cp = sn.props.get("clip-path")
    if cp is not None:
        raw = _raw_str(cp)
        # P9.4 — parse inset values: support px suffix, 1–4 values.
        m2 = _re2.search(r"inset\s*\(([^)]+)\)", raw)
        if m2:
            parts = [p.strip() for p in m2.group(1).split() if p.strip()]
            if len(parts) >= 1:
                clip_inset_top = _inset_num(parts[0])
            if len(parts) >= 2:
                clip_inset_right = _inset_num(parts[1])
            if len(parts) >= 3:
                clip_inset_bottom = _inset_num(parts[2])
            if len(parts) >= 4:
                clip_inset_left = _inset_num(parts[3])
            clip_enabled = True

    gradient_c1 = None
    gradient_c2 = None
    gradient_enabled = False
    gradient_angle = "to bottom"
    bg_val = sn.props.get("background")
    if bg_val is not None:
        raw = _raw_str(bg_val)
        m2 = _re2.search(r"linear-gradient\s*\(\s*(to\s+(?:bottom|right|top|left))?\s*,\s*(\S+)\s*,\s*(\S+)\s*\)", raw)
        if m2:
            angle = m2.group(1) or "to bottom"
            gradient_angle = angle
            g1 = parse_color(m2.group(2))
            g2 = parse_color(m2.group(3))
            if g1 is not None and g2 is not None:
                gradient_c1 = g1
                gradient_c2 = g2
                gradient_enabled = True

    # P10.3 — CSS outline: width + color, rendered outside the border.
    # P10.3 — CSS outline: width + color, rendered outside the border.
    outline_width = 0.0
    outline_color = None
    # Read "outline" first (shorthand carries width+color); fall back to
    # "outline-width" when only the width was set.
    ol_shorthand = sn.props.get("outline")
    ol_width_prop = sn.props.get("outline-width")
    if ol_shorthand is not None:
        raw = _raw_str(ol_shorthand)
        # "2px solid #ff0000" → width=2, color=#ff0000
        m2 = _re2.search(r"([\d.]+)\s*px\s*(?:solid\s+)?(\S+)", raw)
        if m2:
            outline_width = float(m2.group(1))
            oc = parse_color(m2.group(2))
            if oc is not None:
                outline_color = oc
        else:
            # "4px" alone in the shorthand.
            m2 = _re2.search(r"([\d.]+)\s*px", raw)
            if m2:
                outline_width = float(m2.group(1))
                oc_val = sn.props.get("outline-color")
                if oc_val is not None:
                    oc = parse_color(_raw_str(oc_val))
                    if oc is not None:
                        outline_color = oc
    if ol_width_prop is not None:
        # "outline-width" (always a Length with .value as a number).
        raw = _raw_str(ol_width_prop)
        m2 = _re2.search(r"([\d.]+)\s*px", raw)
        if m2:
            outline_width = float(m2.group(1))
            oc_val = sn.props.get("outline-color")
            if oc_val is not None:
                oc = parse_color(_raw_str(oc_val))
                if oc is not None:
                    outline_color = oc
    # If no color was found, use a default outline color so the outline
    # still renders (toy behaviour: outline-color default is the current
    # text color, here black).
    if outline_width > 0 and outline_color is None:
        outline_color = Color(0, 0, 0)

    # P10.3 — CSS outline (toy): width + color, rendered outside the border.
    # outline: 2px solid #ff0000  OR  outline-width + outline-color
    # P10.3 — CSS background-image toy: solid-color fill is already handled;
    # here we only track a "has_bg_image" flag when the value is not a
    # plain color (e.g. url(...) or a gradient we don't yet parse).
    bg_image_set = False
    bg_img = sn.props.get("background-image")
    if bg_img is not None:
        bg_image_set = True  # toy: flag only; no pixel-level image painting yet.

    # P9.4 — text-shadow: X Y B color.
    text_shadow_enabled = False
    text_shadow_offset_x = 0.0
    text_shadow_offset_y = 0.0
    text_shadow_radius = 0.0
    text_shadow_color = None
    ts = sn.props.get("text-shadow")
    if ts is not None:
        raw = _raw_str(ts)
        m2 = _re2.search(r"(-?[\d.]+)\s*px\s+(-?[\d.]+)\s*px\s*(?:([\d.]+)\s*px\s*)?(\S+)", raw)
        if m2:
            text_shadow_offset_x = float(m2.group(1))
            text_shadow_offset_y = float(m2.group(2))
            if m2.group(3):
                text_shadow_radius = float(m2.group(3))
            tc = parse_color(m2.group(4))
            if tc is not None:
                text_shadow_color = tc
                text_shadow_enabled = True

    # P13.3 — CSS filter: blur(Npx) / brightness(N) / contrast(N)
    filter_blur = 0.0
    filter_brightness = 1.0
    filter_contrast = 1.0
    filter_enabled = False
    flt = sn.props.get("filter")
    if flt is not None:
        raw = _raw_str(flt)
        m_blur = _re2.search(r"blur\s*\(\s*([\d.]+)\s*px\s*\)", raw)
        if m_blur:
            filter_blur = float(m_blur.group(1))
        m_bright = _re2.search(r"brightness\s*\(\s*([\d.]+)\s*%\s*\)", raw)
        if m_bright:
            filter_brightness = float(m_bright.group(1)) / 100.0
        m_contrast = _re2.search(r"contrast\s*\(\s*([\d.]+)\s*%\s*\)", raw)
        if m_contrast:
            filter_contrast = float(m_contrast.group(1)) / 100.0
        if filter_blur > 0 or filter_brightness != 1.0 or filter_contrast != 1.0:
            filter_enabled = True

    # P14.3 — CSS text-indent: first-line horizontal shift (px).
    text_indent = sn.get_number("text-indent", 0.0)
    # P18 — vertical-align (toy: baseline / top / middle).
    vertical_align = sn.get_keyword("vertical-align", "")

    # P15.3 — CSS box-decoration-break / -webkit-appearance (toy: stored).
    box_decoration_break = sn.get_keyword("box-decoration-break", "")
    webkit_appearance = sn.get_keyword("-webkit-appearance", "")

    # P15.4 — CSS font-family: inherit from parent when not explicitly set.
    import font as _font
    ff_raw = sn.props.get("font-family")
    if ff_raw is not None:
        # font-family value is a Keyword holding the raw CSS string
        # (e.g. "serif", "monospace", "Times New Roman").
        ff_str = getattr(ff_raw, "value", None)
        if ff_str is None:
            ff_str = str(ff_raw)
        font_family = _font.family_from_css(ff_str)
    elif parent_font_family:
        # Inherit the parent's font family when this node has none of its own.
        font_family = parent_font_family
    else:
        font_family = "monospace"

    # P16.3 — CSS text-justify / word-break / hyphens (toy: stored keywords).
    text_justify = sn.get_keyword("text-justify", "")
    word_break = sn.get_keyword("word-break", "")
    overflow_wrap = sn.get_keyword("overflow-wrap", "")
    hyphens = sn.get_keyword("hyphens", "")

    # P16.4 — CSS scroll-snap-type (toy: stored).
    scroll_snap_type = sn.get_keyword("scroll-snap-type", "")

    # P20.4 — CSS font-weight (numeric 100–900 or "normal"/"bold").
    font_weight = 400
    fw_raw = sn.props.get("font-weight")
    if fw_raw is not None:
        fw_val = str(getattr(fw_raw, "value", fw_raw)).strip().lower()
        if fw_val == "bold":
            font_weight = 700
        elif fw_val == "normal":
            font_weight = 400
        else:
            m_w = _re.match(r"(\d+)", fw_val)
            if m_w:
                font_weight = int(m_w.group(1))

    # P0 (agent report) — box-sizing: border-box makes an explicit
    # width/height the border-box size (subtract padding + border for
    # content).  Sourced from the cascaded `box-sizing` declaration when
    # present (author + UA), else the inline style attribute (the toy
    # CSS cascade keeps longhands in ``sn.props``; ``*{box-sizing:…}``
    # resets usually arrive there).
    _bs_val = sn.props.get("box-sizing")
    if _bs_val is not None:
        box_sizing = str(getattr(_bs_val, "value", _bs_val)).strip().lower()
    else:
        _style_txt = node.attr("style") or "" if is_element else ""
        import re as _re_bs
        _m_bs = _re_bs.search(r"(?:^|;)\s*box-sizing\s*:\s*([^;]+)", _style_txt, _re_bs.IGNORECASE)
        box_sizing = _m_bs.group(1).strip().lower() if _m_bs else ""

    bx = LayoutBox(
        box_type=(
            BoxType.INLINE
            if (is_element and is_inline_tag(node.tag or ""))
            else BoxType.BLOCK
            if is_element
            else BoxType.TEXT
        ),
        padding=_edges_for(sn, "padding", my_font),
        border=_force_form_border if _force_form_border is not None else _edges_for(sn, "border-width", my_font),
        margin=_edges_for(sn, "margin", my_font),
        text_content=text_str,
        text_color=color,
        background=background,
        border_color=border_color,
        font_px=my_font,
        font_family=font_family,
        font_weight=font_weight,
        text_align=text_align,
        explicit_height=explicit_h,
        explicit_width=explicit_w,
        box_sizing=box_sizing,
        is_display_none=sn.is_display_none(),
        line_height_px=sn.line_height_px(),
        dom=node if is_element else None,
        is_flex=is_flex,
        flex_direction=flex_direction,
        flex_wrap=flex_wrap,
        justify_content=justify_content,
        align_items=align_items,
        align_content=align_content,
        flex_grow=flex_grow,
        flex_shrink=flex_shrink,
        flex_basis=flex_basis,
        flex_order=flex_order,
        align_self=align_self,
        flex_gap=flex_gap,
        is_grid=is_grid,
        grid_columns=grid_columns,
        grid_rows=grid_rows,
        grid_cols_frac=grid_cols_frac,
        grid_rows_frac=grid_rows_frac,
        grid_gap=grid_gap,
        grid_row_gap=grid_row_gap,
        border_radius=border_radius,
        shadow_offset_x=shadow_offset_x,
        shadow_offset_y=shadow_offset_y,
        shadow_radius=shadow_radius,
        shadow_color=shadow_color,
        shadow_enabled=shadow_enabled,
        transform_tx=transform_tx,
        transform_ty=transform_ty,
        is_float=is_float,
        float_direction=float_direction,
        is_absolute=is_absolute,
        abs_top=abs_top,
        abs_left=abs_left,
        abs_right=abs_right,
        abs_bottom=abs_bottom,
        is_relative=is_relative,
        rel_top=rel_top,
        rel_left=rel_left,
        rel_right=rel_right,
        rel_bottom=rel_bottom,
        is_sticky=is_sticky,
        sticky_top=sticky_top,
        sticky_right=sticky_right,
        sticky_bottom=sticky_bottom,
        sticky_left=sticky_left,
        z_index=z_index,
        is_table=is_table,
        is_table_row=is_table_row,
        is_table_cell=is_table_cell,
        border_collapse=border_collapse,
        text_overflow=text_overflow,
        line_clamp=line_clamp,
        letter_spacing=letter_spacing,
        word_spacing=word_spacing,
        text_transform=text_transform,
        text_rendering=text_rendering,
        clip_inset_top=clip_inset_top,
        clip_inset_right=clip_inset_right,
        clip_inset_bottom=clip_inset_bottom,
        clip_inset_left=clip_inset_left,
        clip_enabled=clip_enabled,
        gradient_c1=gradient_c1,
        gradient_c2=gradient_c2,
        gradient_enabled=gradient_enabled,
        gradient_angle=gradient_angle,
        text_shadow_enabled=text_shadow_enabled,
        text_shadow_offset_x=text_shadow_offset_x,
        text_shadow_offset_y=text_shadow_offset_y,
        text_shadow_radius=text_shadow_radius,
        text_shadow_color=text_shadow_color,
        outline_width=outline_width,
        outline_color=outline_color,
        bg_image_set=bg_image_set,
        child_index=child_index,
        filter_blur=filter_blur,
        filter_brightness=filter_brightness,
        filter_contrast=filter_contrast,
        filter_enabled=filter_enabled,
        text_indent=text_indent,
        vertical_align=vertical_align,
        box_decoration_break=box_decoration_break,
        webkit_appearance=webkit_appearance,
        text_justify=text_justify,
        word_break=word_break,
        overflow_wrap=overflow_wrap,
        hyphens=hyphens,
        scroll_snap_type=scroll_snap_type,
        opacity=opacity,
        overflow_hidden=overflow_hidden,
        overflow_x=overflow_x,
        overflow_y=overflow_y,
        scroll_left=scroll_left,
        scroll_top=scroll_top,
        img_pixels=img_pixels,
        img_pixel_w=img_pixel_w,
        img_pixel_h=img_pixel_h,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        control_type=control_type,
        control_checked=control_checked,
        control_value=control_value,
        control_options=control_options,
        control_option_index=control_option_index,
        control_file_name=control_file_name,
        control_focus=control_focus,
        control_tabindex=control_tabindex,
        control_required=control_required,
    )
    bx.dom_sn = sn  # P0 (agent report) — back-link for flex sizing

    if is_element:
        if tag == "iframe":
            src = node.attr("src")
            caption = f"iframe: {src}" if src else "iframe (empty)"
            cap_node = Node.text_node(caption)
            cap_sn = sn.placeholder_caption(cap_node)
            cap_box = build_box(cap_sn, my_font, font_family)
            cap_box.box_type = BoxType.TEXT
            cap_box.text_content = caption
            cap_box.parent = bx
            bx.children.append(cap_box)
        elif tag == "img":
            src = node.attr("src") or ""
            alt = node.attr("alt") or ""
            if bx.img_pixels is not None:
                # P3.1 — embedded image: use decoded pixels, shorter caption.
                caption = f"img {bx.img_pixel_w}x{bx.img_pixel_h}"
            elif src:
                caption = f"img: {src}"
            elif alt:
                caption = f"img: {alt}"
            else:
                caption = "img (empty)"
            cap_node = Node.text_node(caption)
            cap_sn = sn.placeholder_caption(cap_node)
            cap_box = build_box(cap_sn, my_font, font_family)
            cap_box.box_type = BoxType.TEXT
            cap_box.text_content = caption
            cap_box.parent = bx
            bx.children.append(cap_box)
        else:
            # Walk the styled children (the authoritative list built by
            # css.apply); the DOM child list may differ for synthetic roots.
            for csn in sn.children:
                cdom = csn.dom
                ctag = (cdom.tag or "").lower()
                if ctag in ("script", "style"):
                    continue
                # P33.P1 — set containing-block dimensions on the child's
                # StyledNode before build_box reads width_px() / height_px().
                csn.parent_width_px = bx.explicit_width
                if bx.explicit_height is not None:
                    csn.parent_height_px = bx.explicit_height
                if csn.is_display_none():
                    # P11.3 — display:none boxes stay in the layout tree
                    # (flagged via is_display_none) so toggle tests can
                    # inspect them; layout_block skips their content.
                    cb = build_box(csn, my_font, font_family, bx.explicit_height if bx.explicit_height is not None else None, bx.explicit_width)
                    cb.parent = bx
                    cb.rect = Rect()
                    bx.children.append(cb)
                    continue
                cb = build_box(csn, my_font, font_family, bx.explicit_height if bx.explicit_height is not None else None, bx.explicit_width)
                cb.parent = bx
                # P15.4 — inherit font_family from parent when child has none.
                # P6.2 — propagate line-clamp / text-overflow to text children.
                if cb.box_type == BoxType.TEXT:
                    cb.line_clamp = bx.line_clamp
                    cb.text_overflow = bx.text_overflow
                bx.children.append(cb)
    return bx


def _styled_child(sn: StyledNode, dom_child: Node) -> Optional[StyledNode]:
    """Find the StyledNode that wraps a given DOM child."""
    for c in sn.children:
        if c.dom is dom_child:
            return c
    return None


# --- layout pass -------------------------------------------------------------------


def layout(styled_root: StyledNode, viewport_w: float, _viewport_h: float = 0.0):
    """Build the layout tree and run the single-pass layout. Returns the root LayoutBox.

    The styled root is the synthetic "document" node; its children are the
    top-level elements. We lay out every child so sibling elements at the
    top level are all reachable from the returned root.

    P26.3 — ``_viewport_h`` is the viewport's content height, used to
    resolve percentage heights on top-level boxes whose containing block
    is the viewport (e.g. ``body { height: 100% }`` → 600px).

    P33.R5 — after the full layout pass, every box's ``dirty`` flag is
    reset to False (the tree is now clean).
    """
    # P33.R5 — clear id()-based caches so a new document render
    # doesn't produce wrong child indices from a reused memory address.
    clear_caches()
    vh = _viewport_h if _viewport_h > 0 else None
    children_sn = list(styled_root.children)
    if not children_sn:
        root = build_box(styled_root, DEFAULT_FONT_PX, "", vh)
        layout_block(root, 0.0, 0.0, viewport_w)
        _mark_all_clean(root)
        return root
    if len(children_sn) == 1:
        root = build_box(children_sn[0], DEFAULT_FONT_PX, "", vh)
        layout_block(root, 0.0, 0.0, viewport_w)
        _mark_all_clean(root)
        return root
    # Multiple top-level children: wrap them in a synthetic block whose
    # styled children are the real top-level siblings.
    import dom as dom_mod
    synth = dom_mod.Node.element("__layout_root__")
    synth_sn = StyledNode(synth, DEFAULT_FONT_PX)
    for cs in children_sn:
        synth_sn.children.append(cs)
    root = build_box(synth_sn, DEFAULT_FONT_PX, "", vh)
    layout_block(root, 0.0, 0.0, viewport_w)
    # P33.R5 — reset all dirty flags after a full layout pass.
    _mark_all_clean(root)
    return root


def _mark_all_clean(root: LayoutBox) -> None:
    """P33.R5 — set dirty=False on every box in the tree (post-layout)."""
    stack = [root]
    while stack:
        bx = stack.pop()
        bx.dirty = False
        stack.extend(reversed(bx.children))


def mark_dirty(bx: LayoutBox) -> None:
    """P33.R5 — mark *bx* and all its descendants as dirty.

    Call this after modifying a box's properties (e.g. after a DOM
    mutation, a CSS change, or a JS-driven style update) so that the
    next ``layout_dirty_only`` pass knows which subtrees to re-lay-out.
    Idempotent: marking an already-dirty box has no effect.
    """
    stack = [bx]
    while stack:
        n = stack.pop()
        n.dirty = True
        stack.extend(reversed(n.children))


def layout_dirty_only(root: LayoutBox) -> bool:
    """P33.R5 — re-run layout on dirty subtrees only.

    Walks the layout tree.  For every box that is ``dirty``, re-runs
    ``layout_block`` on it at its last recorded flow origin, then marks
    the box and its subtree clean.  Clean subtrees are skipped entirely,
    so a document with only a small dirty region pays for re-laying-out
    that region, not the whole tree.

    Returns ``True`` if any box was re-laid-out, ``False`` if the tree
    was already clean.

    Note: this is an *approximate* incremental layout.  It re-runs
    ``layout_block`` on the dirty box at its last recorded flow
    position (rect.x / rect.y minus margins).  This is correct for
    local changes (width, height, position offsets, background, etc.)
    that do not alter the document flow around the box.  For changes
    that insert/remove block siblings the caller must re-run the full
    ``layout()`` pass instead.
    """
    any_dirty = False
    stack = [root]
    _pop = stack.pop
    _append = stack.append
    while stack:
        bx = _pop()
        if bx.dirty:
            any_dirty = True
            # Re-run layout_block on this box at its last flow origin.
            x = bx.rect.x - bx.margin.left
            y = bx.rect.y - bx.margin.top
            containing_w = bx.parent.rect.w if bx.parent is not None else 800.0
            layout_block(bx, x, y, containing_w)
            # P34.8 — mark the box and its *entire* subtree clean in
            # the same pass (no second O(subtree) _mark_all_clean walk).
            # ``mark_dirty`` propagates dirty to every descendant, so a
            # re-layout of *bx* (which re-lays-out its whole subtree via
            # layout_block) makes every descendant clean too.  Clear
            # bx now and push each child onto the clean-marking stack;
            # the child-marking loop below clears them as it unwinds.
            bx.dirty = False
            _clean_stack = bx.children[:]
            _clean_pop = _clean_stack.pop
            while _clean_stack:
                _c = _clean_pop()
                _c.dirty = False
                _clean_stack.extend(_c.children)
        else:
            # Clean box: push its children to find dirty subtrees deeper.
            stack.extend(reversed(bx.children))
    return any_dirty


def _fold_margins(parent: LayoutBox, first_child: LayoutBox | None) -> float:
    """P26.1 — CSS margin collapsing (toy, block-level only).

    P2 (assessment report) — parent-top vs first-child-top collapse
    (CSS2.1 §9.5.1.2): when a parent box's top edge meets its first
    in-flow block child's top edge (no padding/border between them),
    the two top margins collapse to ``max(parent.margin.top,
    child.margin.top)`` and the collapsed amount pokes ABOVE the
    parent's top edge — it is NOT consumed inside the parent's
    content.  This is the amount the parent's flow position advances
    past its own top edge, which ``layout_block`` uses to reposition
    the parent relative to its own parent.  (The child's own top
    margin does NOT advance the content stack: the first child is
    laid out at the parent's content top, see the child pass below.)

    Empty blocks (no in-flow block children) also collapse their top
    and bottom margins to one (CSS2.1 §9.3.3) — the caller clamps to
    0 when the parent has padding/border that prevents collapse.
    """
    if first_child is None:
        # P26.1 — empty block: top+bottom margins collapse to one.
        return max(parent.margin.top, parent.margin.bottom)
    return max(parent.margin.top, first_child.margin.top)


def _next_sibling_gap(prev: LayoutBox, nxt: LayoutBox) -> float:
    """P26.1 — collapsed gap between two adjacent block siblings: the max
    of the previous child's bottom margin and the next child's top margin
    (CSS2.1 §9.5.1).  The caller adds this to the running y offset."""
    return max(prev.margin.bottom, nxt.margin.top)


def _resolve_pct_value(v, ref: float) -> float:
    """P26.3 — resolve a CSS Length with unit '%' against a reference size."""
    from css import Length
    if isinstance(v, Length) and v.unit == "%":
        return v.value * ref / 100.0
    return 0.0


def layout_block(bx: LayoutBox, x: float, y: float, containing_w: float) -> None:
    if bx.is_display_none:
        bx.rect = Rect()
        return

    # P6.1 — position: absolute: place relative to the parent content area
    # using top/left/right/bottom offsets instead of the flow position.
    # P9.2 — full-direction: right/bottom anchor from the parent's edge.
    if bx.is_absolute:
        x = x + bx.abs_left
        y = y + bx.abs_top
        # right/bottom offsets are applied after the box is sized, but in
        # the single-pass toy layout we approximate: shift x/y by the
        # negative of right/bottom from the containing edge.
        if bx.abs_right != 0.0:
            x = x - bx.abs_right  # shift left by right offset
        if bx.abs_bottom != 0.0:
            y = y - bx.abs_bottom  # shift up by bottom offset

    # P7.4 — position: sticky: the box stays at its flow position but is
    # clamped so it never goes above the top offset (or left of the left
    # offset) relative to the nearest scrolling ancestor.  In this static
    # layout we approximate: the box's y is clamped to at least `y + sticky_top`
    # (i.e. the flow y never goes below the sticky threshold), and x is
    # clamped similarly for sticky_left.  True scroll-tracking requires a
    # viewport-aware second pass; this gives the "stuck" position for the
    # common case where flow would scroll past the offset.
    if bx.is_sticky:
        if bx.sticky_top > 0:
            # In static layout, the flow y is already the correct position.
            # We record the offsets; a scroll-aware renderer would clamp here.
            pass
        if bx.sticky_left > 0:
            pass

    # P8.2 — position: relative: the box stays in the document flow but is
    # shifted by its top/left/right/bottom offsets from its normal flow
    # position.  P9.2 — right/bottom shift in the opposite direction.
    if bx.is_relative:
        x = x + bx.rel_left
        y = y + bx.rel_top
        if bx.rel_right != 0.0:
            x = x - bx.rel_right
        if bx.rel_bottom != 0.0:
            y = y - bx.rel_bottom

    padding_l = bx.padding.left
    padding_t = bx.padding.top
    padding_r = bx.padding.right
    padding_b = bx.padding.bottom
    margin_l = bx.margin.left
    margin_r = bx.margin.right
    # P26.5 — CSS2.1 §10.3 simplified auto-width resolution:
    #   w = max(0, containing - margin_l - margin_r - padding - border)
    # P0 (agent report) — box-sizing:border-box: an explicit width is the
    # BORDER-box width, so the content width subtracts padding *and* border;
    # with content-box (default) the explicit width is the content width and
    # the box grows outward by padding/border.
    border_w_total = bx.border.left + bx.border.right
    border_h_total = bx.border.top + bx.border.bottom
    box_border_box = getattr(bx, "box_sizing", "") == "border-box"
    if bx.explicit_width is not None:
        if box_border_box:
            available = max(0.0, bx.explicit_width - padding_l - padding_r
                            - border_w_total)
        else:
            # P2 (assessment report) — content-box semantics: an explicit
            # `width` is the CONTENT width, not the border-box width.
            # The legacy code subtracted padding + border for every box,
            # so a content-box `width:200px;padding:5px;border:2px`
            # (layout_blocks .a) reported a 186px content width instead
            # of 200px, and `width:100px` (layout_blocks .b) was
            # 10px narrow.  The CSS box model (§10.3.1) defines the
            # content width as the `width` value itself; padding and
            # border grow the box outward, and `border_rect()` already
            # expands the rect by padding + border for the browser.
            available = bx.explicit_width
    else:
        available = max(0.0, containing_w - margin_l - margin_r - padding_l - padding_r
                        - (border_w_total if box_border_box else 0.0))
    if bx.explicit_height is not None:
        if box_border_box:
            explicit_content_h = max(0.0, bx.explicit_height - padding_t - padding_b - border_h_total)
        else:
            explicit_content_h = bx.explicit_height
    else:
        explicit_content_h = None

    tag = (bx.dom.tag if bx.dom and bx.dom.is_element() else None) or ""
    is_iframe = tag == "iframe"
    is_img = tag == "img"
    is_input = tag == "input"
    is_textarea = tag == "textarea"

    # P26.4 — CSS2.1 §10.4: min/max width clamp the computed width
    # (also applied to auto-width boxes, per the spec).
    if bx.max_width > 0:
        available = min(available, bx.max_width)
    if bx.min_width > available:
        available = bx.min_width
    # P3.1 — embedded image with explicit size: use those dimensions.
    if is_img and bx.img_pixels is not None:
        if bx.explicit_width is None:
            available = float(bx.img_pixel_w)
        if bx.explicit_height is None:
            bx.explicit_height = float(bx.img_pixel_h)
    # <img> without explicit width falls back to the 32×32 placeholder.
    if is_img and bx.explicit_width is None:
        available = LayoutBox.IMG_DEFAULT_W
    if is_img and bx.explicit_height is None:
        bx.explicit_height = LayoutBox.IMG_DEFAULT_H
    # <img> with explicit_width but no explicit height: match width (square).
    if is_img and bx.explicit_width is not None and bx.explicit_height is None:
        bx.explicit_height = bx.explicit_width
    # P28 — control-specific default sizing (runs BEFORE the generic
    # <input> fallback so checkbox/radio/file get their own boxes).
    if is_input and bx.control_type in ("checkbox", "radio"):
        if bx.explicit_width is None:
            available = 16.0
        if bx.explicit_height is None:
            bx.explicit_height = 16.0
    elif is_input and bx.control_type == "file":
        if bx.explicit_width is None:
            available = 220.0
        if bx.explicit_height is None:
            bx.explicit_height = 24.0
    elif is_input and bx.control_type in ("button", "submit", "reset", "image"):
        if bx.explicit_width is None:
            available = 80.0
        if bx.explicit_height is None:
            bx.explicit_height = 28.0
    # P28 — <textarea> gets a fixed placeholder box (4 rows × 20 cols
    # default, HTML §4.10.6).
    if is_textarea and bx.explicit_width is None:
        available = 160.0
    if is_textarea and bx.explicit_height is None:
        bx.explicit_height = 64.0
    # P28 — <select> gets a fixed combobox box (width = max option
    # width or 120px, height = 24px).
    if tag == "select" and bx.explicit_width is None:
        available = 120.0
    if tag == "select" and bx.explicit_height is None:
        bx.explicit_height = 24.0
    # <input> (text/password/number/etc.) gets a fixed placeholder box —
    # only for the default text-like types (checkbox/radio/file/button are
    # handled above with their own sizes).
    if is_input and bx.control_type not in ("checkbox", "radio", "file",
                                             "button", "submit", "reset", "image"):
        if bx.explicit_width is None:
            available = 160.0
        if bx.explicit_height is None:
            bx.explicit_height = 20.0
    # P33.R2-2 (layout_blocks D1/D2) — CSS2.1 §9.5.1.2 first-child
    # top-margin collapse.  The caller passes ``y`` as the top of this
    # box's *margin box* (the flow position in the parent's content).
    #
    # Two regimes:
    #  1. Top-level flow (parent is <body> or <html>): the parent's
    #     own top margin is NOT consumed by the parent (the parent's
    #     border-box top sits at the grandparent's content top), so
    #     the first in-flow block child's top margin punches through:
    #     child.border_top = parent.content_top + max(0, child.mt -
    #     parent.mt).  This is the D1/D2 fix (.a y=10, .b y=80).
    #  2. Regular block parent (div etc., no padding/border, not
    #     flex/grid/table/BFC/abs/rel): the P26.1 baseline model —
    #     the first child sits at the parent's content top with NO
    #     offset (the collapsed margin is absorbed by the parent's
    #     own position, which the grandparent's child loop already
    #     resolved).  Non-first siblings use the collapsed sibling
    #     gap (_next_sibling_gap).
    #  3. Non-collapsible parent (padding/border/flex/grid/table/
    #     BFC/abs/rel): the full top margin is consumed inside the
    #     parent's content.
    _p = bx.parent
    # P33.R2-2 (D1/D2) — the punch-through applies ONLY at the
    # top-level flow (the <body> element): the body's own top margin
    # (UA 8px) is not consumed by the body's child loop, so the
    # first in-flow block child's top margin punches through.  For
    # every other parent the P26.1 baseline model applies: the box's
    # own top margin is never consumed inside its content; the
    # parent's child loop handles it (sibling gaps, first-child
    # no-advance).  The html element's child loop does not advance
    # by the body's margin (the body sits at the html content top),
    # so the html level uses the same baseline model.
    _p_is_body = (
        _p is not None
        and _p.dom is not None
        and _p.dom.tag == "body"
    )
    _collapsible_top = (
        _p_is_body
        and not _p.is_flex and not _p.is_grid and not _p.is_table
        and not _p.is_absolute and not _p.is_relative
        and _p.overflow_hidden in (False, "")
        and _p.padding.top == 0 and _p.border.top == 0
        and not bx.is_float and not bx.is_display_none
        and bx.box_type == BoxType.BLOCK
    )
    if _collapsible_top:
        _is_first_block_child = False
        for _sib in _p.children:
            if _sib.box_type == BoxType.BLOCK and not _sib.is_float and not _sib.is_display_none:
                _is_first_block_child = (_sib is bx)
                break
        if _is_first_block_child:
            # P33.R2-2 (D1) — punch-through at the top-level flow:
            # the body's own top margin (UA 8px) is not consumed by
            # the body's child loop (the body sits at the html
            # content top), so the first in-flow block child's top
            # margin REPLACES it: the child's border-box top is at
            # the body's content top + child.margin.top.  When the
            # body's margin is explicitly 0 (e.g. ``body{margin:0}``),
            # there is no anchor to replace and the P26.1 baseline
            # model applies (the child's margin is not consumed).
            _mtop = bx.margin.top if _p.margin.top > 0.0 else 0.0
        else:
            _mtop = 0.0
    else:
        # P26.1 baseline — the box's own top margin is never consumed
        # inside its content.  The parent's child loop handles it.
        _mtop = 0.0
    content_x0 = x + margin_l + padding_l
    content_y0 = y + padding_t + _mtop

    if bx.box_type == BoxType.TEXT:
        # P34.8 — text box sizing: every text box (2000+ in the 10k
        # benchmark) pays the same handful of attribute reads + the
        # half-leading cache lookup.  The legacy code re-resolved the
        # TTF face on every one of them; the P34.4 half-leading cache
        # already cuts that.  This path is now the single most
        # expensive part of layout_block, so keep it tight: no
        # _fold_margins, no float pre-pass, no margin-collapse.
        text_str = bx.text_content or ""
        # P18 — text-transform (P8.4) applied before wrap/measure.
        if bx.text_transform == "uppercase":
            text_str = text_str.upper()
        elif bx.text_transform == "lowercase":
            text_str = text_str.lower()
        elif bx.text_transform == "capitalize":
            text_str = " ".join(w[:1].upper() + w[1:] for w in text_str.split(" "))
        # P34.7 — skip the measure() call when the text cannot possibly
        # overflow: each char advances at most 3 × font_px (the
        # advance_width clamp), so `len(text_str) * 3 * font_px` is an
        # upper bound on the rendered width.  When that bound ≤ the
        # available width the text fits on one line and the full
        # O(n) measure() call is wasted.  For the 1000-div benchmark
        # ("Content 123" ≈ 11 chars × 3 × 14px = 462 px < 800 px
        # available) this skips ~1000 measure() calls.
        _max_w = len(text_str) * 3.0 * bx.font_px
        if _max_w > available and text_mod.measure(text_str, bx.font_px, bx.font_family) > available:
            wrapped = text_mod.wrap(text_str, max(0.0, available), bx.font_px,
                                    bx.font_family,
                                    word_break=bx.word_break,
                                    overflow_wrap=bx.overflow_wrap)
        else:
            wrapped = [text_str]
        # P6.2 — line-clamp: limit the number of visible lines.
        if bx.line_clamp > 0 and len(wrapped) > bx.line_clamp:
            wrapped = wrapped[: bx.line_clamp]
            text_str = " ".join(wrapped)
            if bx.text_overflow == "ellipsis":
                text_str += "…"
        line_h = bx.line_height_px
        # P34.4 — half-leading line box cache.
        hl_key = (bx.font_family, bx.font_px, line_h)
        hl = _half_lead_cache.get(hl_key)
        if hl is None:
            half_lead = 0.0
            ascent_px = 0.0
            descent_px = 0.0
            try:
                real_fm = font.get_real_metrics(bx.font_family)
                if real_fm is not None and real_fm.units_per_em > 0:
                    scale = bx.font_px / real_fm.units_per_em
                    ascent_px = real_fm.ascent * scale
                    descent_px = -real_fm.descent * scale
                    em_box = ascent_px + descent_px
                    if line_h > em_box:
                        half_lead = (line_h - em_box) / 2.0
            except Exception:
                pass
            if len(_half_lead_cache) < _HALF_LEAD_CACHE_MAX:
                hl = (half_lead, ascent_px, descent_px)
                _half_lead_cache[hl_key] = hl
        else:
            half_lead, ascent_px, descent_px = hl
        bx.rect = Rect(content_x0, content_y0 + half_lead, available,
                       line_h * max(1, len(wrapped)))
        # P18 — record baseline y (top of em box + ascent) for
        # baseline-aligned inline elements.
        bx.font_ascent_px = ascent_px if ascent_px > 0 else (bx.font_px * 0.75)
        bx.font_descent_px = descent_px if descent_px > 0 else (bx.font_px * 0.25)
        bx.baseline_y = bx.rect.y + half_lead + bx.font_ascent_px
        return

    # P1.4 — Flexbox layout: children are laid out in a single row/column.
    if bx.is_flex:
        _layout_flex(bx, content_x0, content_y0, available, containing_w)
        return

    # P4.3 — CSS Grid layout: children fill a grid-template matrix.
    if bx.is_grid:
        _layout_grid(bx, content_x0, content_y0, available, containing_w)
        return

    # P7.2 — display: table: children fill rows; cells in the same row share
    # the row height; column widths are the max cell width per column.
    if bx.is_table:
        _layout_table(bx, content_x0, content_y0, available, containing_w)
        return

    # P33.R2-2 (layout_blocks D1/D2) — the collapsed first-child top
    # margin is already accounted for in ``content_y0`` above
    # (``content_y0 = y + padding_t + margin.top - collapsed``).  The
    # legacy ``y += margin.top - collapsed`` here was dead code: it
    # mutated ``y`` AFTER ``content_y0`` had been computed, so it had
    # no effect on the content origin.  Remove it.

    # P5.2 — CSS float: children with float:left/right are positioned at
    # the specified side of the containing block; subsequent inline content
    # wraps around them.
    # P26.2 — BFC: when overflow != visible the box establishes a
    # block formatting context that contains its floats — the box's height
    # extends to cover them (prevents the "float escapes parent" bug), and
    # floats never overlap the BFC's border box.
    # P34.8 — the float pre-pass + first-block scan below iterate
    # bx.children twice (once for floats, once for the first block).
    # The legacy code ran both passes unconditionally; most blocks in a
    # typical document have no floats, so skip the entire pre-pass when
    # no child is a float (checked once, O(n) but early-terminated).
    child_containing_w = available if bx.explicit_width is not None else containing_w
    _has_float = False
    for cb in bx.children:
        if cb.is_float:
            _has_float = True
            break
    if _has_float:
        float_l = 0.0
        float_r = 0.0
        float_y = 0.0
        float_max_h = 0.0
        for cb in bx.children:
            if not cb.is_float:
                continue
            # P26.2 — BFC: lay the float out at its own coordinate origin
            # (0, 0) relative to the viewport, then reposition it to the
            # BFC's content edge.  This prevents the float from inheriting
            # the BFC's parent coordinates during layout.
            layout_block(cb, 0.0, 0.0, child_containing_w)
            w = cb.rect.w + cb.padding.left + cb.padding.right
            h = cb.outer_height()
            if cb.float_direction == "left":
                cb.rect.x = content_x0
                cb.rect.y = content_y0 + float_y
                float_l = max(float_l, w)
                float_y = max(float_y, h)
                float_max_h = max(float_max_h, cb.rect.y + cb.rect.h)
            else:
                cb.rect.x = content_x0 + available - w
                cb.rect.y = content_y0 + float_y
                float_r = max(float_r, w)
                float_y = max(float_y, h)
                float_max_h = max(float_max_h, cb.rect.y + cb.rect.h)
        # P26.2 — reduce the in-flow content width by the total float width.
        avail = max(0.0, available - float_l - float_r)
        if float_l > 0.0:
            content_x0 = content_x0 + float_l
        # P26.2 — the BFC's *own* rect must not shift with the float offset;
        # only in-flow children use the adjusted content_x0.  Save the
        # original content edge for the box's own geometry.
        bfc_content_x0 = content_x0 - float_l
    else:
        float_l = float_r = float_y = float_max_h = 0.0
        avail = available
        bfc_content_x0 = content_x0

    # Lay out children, accumulating the content height.
    # P26.1 — margin collapsing between adjacent block siblings:
    # the gap between siblings is max(prev_margin_bottom, next_margin_top)
    # instead of sum, per CSS2.1 §9.5.1.
    child_y = 0.0
    prev_block: LayoutBox | None = None
    i = 0
    n = len(bx.children)
    while i < n:
        kind = bx.children[i].box_type
        if bx.children[i].is_display_none:
            # P11.3 — display:none children occupy no space (their rect is
            # already Rect()); just skip them.
            i += 1
            continue
        if kind == BoxType.BLOCK:
            cb = bx.children[i]
            if cb.is_float:
                # Already positioned in the pre-pass above; skip re-layout.
                i += 1
                continue
            # P26.1 — collapsed margin between this block and its previous
            # block sibling: use max(prev.margin-bottom, next.margin-top),
            # not the sum (CSS2.1 §9.5.1).
            if prev_block is not None:
                child_y += _next_sibling_gap(prev_block, cb)
            # P26.5 — children are constrained to the parent's content width,
            # not the viewport: pass the parent's computed available width as
            # the child's containing block width.
            child_containing_w = available if bx.explicit_width is not None else containing_w
            layout_block(cb, content_x0, content_y0 + child_y, child_containing_w)
            # P26.1 — the content stack advances by the box's border-box
            # height; the collapsed margin with the *next* sibling is
            # applied by _next_sibling_gap at the top of the next
            # iteration (CSS2.1 §9.5.1: margins between siblings never
            # add, they collapse to the max).  P33.R2-2 (D2) — the
            # first in-flow block child's border-box top is punched
            # through the parent's top margin by ``_mtop`` (above), so
            # the stack must also advance by that amount; later
            # siblings only advance by the collapsed sibling gap.
            child_y += (
                cb.rect.h
                + cb.padding.top
                + cb.padding.bottom
                + cb.border.top
                + cb.border.bottom
            )
            if (prev_block is None
                    and bx.dom is not None and bx.dom.tag == "body"
                    and bx.margin.top > 0.0
                    and not bx.is_flex and not bx.is_grid and not bx.is_table
                    and not bx.is_absolute and not bx.is_relative
                    and bx.overflow_hidden in (False, "")
                    and bx.padding.top == 0 and bx.border.top == 0):
                # P33.R2-2 (D2) — the first in-flow block child of the
                # <body> element punches through the body's top margin
                # (``_mtop = cb.margin.top`` in the child's own
                # layout_block call above), so the content stack must
                # also advance by that amount; later siblings measure
                # from the first child's actual border-box top.
                child_y += cb.margin.top
            prev_block = cb
            i += 1
        else:
            # P18 — inline-level run: collect children until a block-level
            # box is hit.  Each child is laid out sequentially; the run
            # height is the max of child heights and the container's
            # line_height_px.  P18.2 — an inline element (e.g. <a> or
            # <span>) whose text wraps across lines keeps its style
            # (text_color, background, border) because the child box
            # carries its own StyledNode properties; the layout only
            # positions the box, it does not strip styling.
            # P18.3 — vertical-align: inline images / elements with
            # vertical-align set are shifted relative to the line's
            # baseline (toy: baseline / top / middle).
            cx = content_x0
            run_h = 0.0
            run_y = content_y0 + child_y
            run_top_y = run_y
            run_bottom_y = run_y
            while i < n:
                ck = bx.children[i].box_type
                if ck not in (BoxType.TEXT, BoxType.INLINE):
                    break
                cb = bx.children[i]
                if cb.is_float:
                    i += 1
                    continue
                run_w = max(0.0, avail - (cx - content_x0))
                # P0 (GLM report) — P1-4: an inline box that only
                # contains TEXT children (e.g. <span>short text</span>)
                # should be sized to its text content, not to the full
                # container width.  The legacy implementation laid it
                # out with the full available width (max(run_w, avail)),
                # so every inline element's background/border stretched
                # to the container edge regardless of its text length.
                # The P0 fix: when the box has no explicit width and all
                # its children are TEXT boxes, lay it out with the text
                # content width (sum of child text advances) instead of
                # the container width.
                inline_text_w = None
                if (ck == BoxType.INLINE
                        and cb.explicit_width is None
                        and cb.max_width <= 0
                        and cb.children
                        and all(grandchild.box_type == BoxType.TEXT
                               for grandchild in cb.children)):
                    tw = 0.0
                    for grandchild in cb.children:
                        t = grandchild.text_content or ""
                        tw += text_mod.measure(t, grandchild.font_px)
                    inline_text_w = tw + cb.padding.left + cb.padding.right
                if inline_text_w is not None:
                    layout_block(cb, cx, run_y, inline_text_w)
                else:
                    layout_block(cb, cx, run_y, max(run_w, avail))
                w = cb.rect.w + cb.padding.left + cb.padding.right
                # P18.3 — vertical-align shift (toy: baseline / top / middle).
                # The line box baseline is approximated as run_y + line_height_px * 0.75
                # (legacy bitmap ascent ratio).  For TTF fonts the text box
                # already recorded its own baseline_y; use the parent's
                # line_height as the fallback.
                va = cb.vertical_align
                if va == "top":
                    cb.rect.y = run_top_y
                elif va == "middle":
                    mid = (run_top_y + bx.line_height_px / 2.0)
                    cb.rect.y = mid - cb.rect.h / 2.0
                # "baseline" (default): no shift — the box is already at run_y.
                cx += w
                if ck == BoxType.INLINE:
                    cx += text_mod.char_advance(cb.font_px)
                run_h = max(run_h, cb.rect.h)
                run_top_y = min(run_top_y, cb.rect.y)
                run_bottom_y = max(run_bottom_y, cb.rect.y + cb.rect.h)
                i += 1
            child_y += max(run_h, bx.line_height_px)

    # Finalize this box's geometry.
    visible_children = [c for c in bx.children if not c.is_display_none]
    if visible_children:
        h = child_y
    else:
        h = _element_placeholder_height(bx)
    # P0 (agent report) — border-box: explicit height is the border-box
    # height, so the content height subtracts padding + border.
    if explicit_content_h is not None:
        h = explicit_content_h
    if is_img and bx.explicit_width is None:
        h = LayoutBox.IMG_DEFAULT_H
    if is_iframe and h < LayoutBox.IFRAME_PLACEHOLDER_H:
        grow = LayoutBox.IFRAME_PLACEHOLDER_H - h
        h = LayoutBox.IFRAME_PLACEHOLDER_H
        for c in bx.children:
            if c.dom is None:  # caption text box
                c.rect.y += grow / 2.0
    # P28 — form controls with an explicit height (checkbox/radio/select/
    # textarea/file/button): the placeholder height above must be respected
    # even when the control has no in-flow children.  For empty controls the
    # height was set from the control-specific default above.
    if bx.explicit_height is None and (is_input or is_textarea or tag == "select" or tag == "button"):
        h = _element_placeholder_height(bx)

    # P26.2 — BFC: the box's height extends to contain its floats.
    if float_max_h > 0.0:
        h = max(h, float_max_h - (content_y0 - bx.rect.y))

    # P26.4 — min/max width/height constraints (CSS2.1): clamp the final
    # box size; width is already final (`available`), height is `h`.
    if bx.max_width > 0:
        available = min(available, bx.max_width)
    if bx.min_width > available:
        available = bx.min_width
    if bx.max_height > 0:
        h = min(h, bx.max_height)
    if bx.min_height > h:
        h = bx.min_height

    bx.rect = Rect(bfc_content_x0 if float_l > 0.0 else content_x0, content_y0, available, h)


def _element_placeholder_height(bx: LayoutBox) -> float:
    tag = (bx.dom.tag if bx.dom and bx.dom.is_element() else None) or ""
    if tag == "iframe":
        return LayoutBox.IFRAME_PLACEHOLDER_H
    if tag == "img":
        return LayoutBox.IMG_DEFAULT_H
    return bx.line_height_px


def _styled_node_of(bx: "LayoutBox"):
    """P0 (agent report) — recover the StyledNode that produced *bx*.

    ``build_box`` back-links ``bx.dom_sn`` on both the fast and slow
    paths; the flex sizing pass uses it to re-derive the explicit CSS
    width/height of items whose box-level values the fast path drops."""
    return getattr(bx, "dom_sn", None)


def _layout_flex(
    bx: LayoutBox,
    content_x0: float,
    content_y0: float,
    available: float,
    containing_w: float,
) -> None:
    """P1.4 + P27 — flexbox layout (spec-compliant subset).

    P27 features:
      flex-grow / flex-shrink / flex-basis  (spec §9.7 sizing algorithm)
      flex-wrap: wrap | wrap-reverse (multi-line)
      justify-content: start|center|end|space-between|space-around|space-evenly
      align-items / align-self: stretch|start|center|end|baseline
      align-content: stretch|start|center|end|space-between|space-around
      flex-direction: row|column × -reverse
      order: items sorted by (order, source-order)
      min-width / min-height: auto on flex items (content floor)
    """
    if not bx.children:
        bx.rect = Rect(content_x0, content_y0, available, bx.line_height_px)
        return

    row = "row" in bx.flex_direction
    reverse = bx.flex_direction.endswith("-reverse")
    wrap = bx.flex_wrap != "nowrap"
    wrap_reverse = bx.flex_wrap == "wrap-reverse"
    # P27.5 — reverse directions: the main-axis start edge flips.
    # row-reverse: main axis starts at the right edge; items pack right→left.
    # column-reverse: main axis starts at the bottom edge; items pack bottom→up.
    main_reverse = reverse  # used to flip the main-axis packing direction

    # --- Phase 0: natural sizes (single-line measure at containing width) ---
    visible = [cb for cb in bx.children if not cb.is_display_none]
    if not visible:
        bx.rect = Rect(content_x0, content_y0, available, bx.line_height_px)
        return
    # sort by (order, source index) — stable
    indexed = list(enumerate(visible))
    indexed.sort(key=lambda t: (visible[t[0]].flex_order, t[0]))
    ordered = [cb for _, cb in indexed]

    # Measure each child at natural size
    # P1 (assessment report) — the natural main/cross sizes must include
    # the item's own padding + border on BOTH axes.  The legacy version
    # only added padding on the main axis and ignored the item's border
    # (and cross padding), so items carrying ``padding:5px;border:2px``
    # (flex_padding_border) or a cross-axis margin measured too small:
    # the flex_cross_margin probe in the report expected y=4 (top margin)
    # but the item landed at y=0.  Now:
    #   natural_main  = content + main_padding + main_border
    #   natural_cross = content + cross_padding + cross_border
    # (the margin is handled separately via the margin-box totals in
    # Phase 4; it does NOT belong in the natural size).
    natural_main: list[float] = []
    natural_cross: list[float] = []
    for cb in ordered:
        layout_block(cb, content_x0, content_y0, containing_w)
        if row:
            natural_main.append(
                cb.rect.w
                + cb.padding.left + cb.padding.right
                + cb.border.left + cb.border.right)
            natural_cross.append(
                cb.rect.h
                + cb.padding.top + cb.padding.bottom
                + cb.border.top + cb.border.bottom)
        else:
            natural_main.append(
                cb.rect.h
                + cb.padding.top + cb.padding.bottom
                + cb.border.top + cb.border.bottom)
            natural_cross.append(
                cb.rect.w
                + cb.padding.left + cb.padding.right
                + cb.border.left + cb.border.right)
    n = len(ordered)

    # --- Phase 1: flex basis (spec §9.2.3) ---
    # basis = flex-basis if set and >= 0, else main-axis size (explicit),
    # else max-content size (the natural main-axis size measured at the
    # containing width in Phase 0).
    # P0 (agent report) — a flex item with an explicit cross-axis size
    # (width in row / height in column) that is NOT the main axis must
    # keep it: the legacy code ignored it and the item's cross size fell
    # through to the text line-height.  Record those here so the
    # stretch pass in Phase 5 can honour them.
    cross_explicit: list[float | None] = []
    for cb in ordered:
        if row:
            cross_explicit.append(cb.explicit_height)
        else:
            cross_explicit.append(cb.explicit_width)
    bases = []
    for i, cb in enumerate(ordered):
        if cb.flex_basis is not None and cb.flex_basis >= 0:
            bases.append(cb.flex_basis)
        elif row and cb.explicit_width is not None:
            # P2 (assessment report) — box-sizing semantics for the
            # content flex basis (CSS box-sizing §3 + flexbox §9.2.3):
            #   * content-box (default): an explicit `width` is the
            #     *content* size, so it is the flex basis directly —
            #     padding + border grow the box outward and are NOT
            #     subtracted.  The legacy code subtracted padding here,
            #     so a `width:40px;padding:5px` item measured a 30px
            #     basis, and the justify-content centring + every
            #     main-axis margin-box total built on `sized` shrank by
            #     the item's padding (flex_padding_border c*.x: 81 vs
            #     61 — a 3×5px shift).
            #   * border-box: an explicit `width` is the *border-box*
            #     size, so the content basis subtracts the item's own
            #     padding + border.
            _bb = getattr(cb, "box_sizing", "") == "border-box"
            if _bb:
                _pb = (cb.padding.left + cb.padding.right if row
                       else cb.padding.top + cb.padding.bottom)
                _bd = (cb.border.left + cb.border.right if row
                       else cb.border.top + cb.border.bottom)
            else:
                _pb = 0.0
                _bd = 0.0
            _exp = cb.explicit_width if row else cb.explicit_height
            bases.append(max(0.0, _exp - _pb - _bd))
        else:
            # P27 — auto basis: fall back to the item's natural main size
            # (spec §9.2.3 "max-content size").  For grow-only items with
            # no content the natural size is small, so they still absorb
            # most of the free space through flex-grow.
            bases.append(max(0.0, natural_main[i]))

    # P0 (agent report) — flex items with an explicit main-axis size must
    # not stretch to the available width: cap the flex basis at that size.
    # build_box's fast path zeroes explicit_width for plain blocks, so
    # when the box-level value is missing we re-derive the explicit CSS
    # size from the item's child StyledNode (width_px / height_px),
    # falling back to the Phase-0 natural main size otherwise.
    _styled_children = _styled_node_of(bx)
    _styled_by_dom = {}
    if _styled_children is not None:
        for csn in getattr(_styled_children, "children", []):
            if csn.dom is not None:
                _styled_by_dom[id(csn.dom)] = csn
    _item_explicit = [None] * n
    for i, cb in enumerate(ordered):
        _cap = (cb.explicit_width if row else cb.explicit_height)
        if _cap is not None:
            # P2 (assessment report) — content-box basis capping mirrors
            # the basis above: content-box items cap at the explicit size
            # itself (no subtraction — padding/border grow the box
            # outward); border-box items cap at border-box minus
            # padding + border on the MAIN axis.
            _bb = getattr(cb, "box_sizing", "") == "border-box"
            if _bb:
                _pb = (cb.padding.left + cb.padding.right if row
                       else cb.padding.top + cb.padding.bottom)
                _bd = (cb.border.left + cb.border.right if row
                       else cb.border.top + cb.border.bottom)
            else:
                _pb = 0.0
                _bd = 0.0
            _item_explicit[i] = max(0.0, _cap - _pb - _bd)
        else:
            _csn = _styled_by_dom.get(id(cb.dom)) if cb.dom is not None else None
            if _csn is not None:
                _cap2 = (_csn.width_px() if row else _csn.height_px())
                if _cap2 is not None:
                    _item_explicit[i] = _cap2
        if _item_explicit[i] is None:
            bases[i] = min(bases[i], natural_main[i])
        else:
            bases[i] = min(bases[i], _item_explicit[i])

    # --- Phase 2: flex grow/shrink (spec §9.7 simplified single-pass) ---
    total_basis = sum(bases)
    leftover = available - total_basis
    sized = list(bases)

    if leftover > 0:
        # grow: distribute positive leftover to items with flex-grow > 0
        total_grow = sum(max(0.0, cb.flex_grow) for cb in ordered)
        if total_grow > 0:
            for i, cb in enumerate(ordered):
                if cb.flex_grow > 0:
                    sized[i] += leftover * (cb.flex_grow / total_grow)
    elif leftover < 0:
        # P27.5 - min-width/min-height:auto: flex items cannot shrink
        # below their *content* size (the natural main-axis size from
        # Phase 0, which is the tightest measure available).
        for i, cb in enumerate(ordered):
            floor = natural_main[i] if natural_main[i] > 0 else 0.0
            cb_min = cb.min_width if row else cb.min_height
            floor = max(floor, cb_min if cb_min > 0 else 0.0)
            sized[i] = max(sized[i], floor)
        # P27.3 — when flex-wrap is enabled, spec §9.7.4 says items do
        # NOT shrink to fit the line; they overflow and wrap instead.
        # Skip the shrink pass when wrapping is active.
        if not wrap:
            deficit = available - sum(sized)
            if deficit < 0:
                total_shrink = sum(max(0.0, cb.flex_shrink) for cb in ordered)
                if total_shrink > 0:
                    total_power = sum(max(0.0, cb.flex_shrink) * max(0.0, sized[i])
                                      for i, cb in enumerate(ordered))
                    for i, cb in enumerate(ordered):
                        if cb.flex_shrink > 0:
                            power = cb.flex_shrink * max(0.0, sized[i])
                            shrink_amt = (-deficit) * (power / total_power) if total_power > 0 else 0.0
                            sized[i] -= shrink_amt

    # --- Phase 3: wrap into lines (if flex-wrap) ---
    lines: list[list[int]] = []
    if wrap:
        line: list[int] = []
        line_w = 0.0
        for i, cb in enumerate(ordered):
            w = sized[i]
            if line and line_w + w > available:
                lines.append(line)
                line = []
                line_w = 0.0
            line.append(i)
            line_w += w
        if line:
            lines.append(line)
        if not lines:
            lines = [list(range(n))]
        # P27.3 — wrap-reverse: lines stack from the far cross edge.
        # Reverse the line list so the DOM-first line sits at the far
        # cross-axis edge (bottom for row, left for column).
        if wrap_reverse:
            lines.reverse()
    else:
        lines = [list(range(n))]
        # single line: overflow is handled by the shrink pass; when every
        # item has flex-shrink:0 the overflow is clipped (toy: no overflow
        # property yet), so no renormalisation here.

    # --- Phase 4: place lines ---
    if row:
        # cross axis = vertical; container height = sum of line heights
        line_heights: list[float] = []
        for line in lines:
            lh = max(natural_cross[i] for i in line)
            line_heights.append(lh)
        container_h = sum(line_heights)

        # align-content distributes lines on the cross axis
        cross_size = max(container_h, 0.0)
        if bx.explicit_height is not None:
            # P2 (assessment report) — the flex container's available
            # cross space is its *content* height.  ``bx.explicit_height``
            # is the border-box size when box-sizing:border-box (the
            # layout_flex probe page sets ``*{box-sizing:border-box}``),
            # so the legacy code stretched the line to the full
            # border-box height and centred items 4-5px too low
            # (layout_flex c*.y: 19 vs 15).  Subtract the container's
            # own padding + border (same branch as the ``available``
            # width resolution above).
            cross_avail = (bx.explicit_height
                           - bx.padding.top - bx.padding.bottom
                           - bx.border.top - bx.border.bottom
                           if bx.box_sizing == "border-box"
                           else bx.explicit_height)
        else:
            cross_avail = cross_size

        # distribute lines vertically
        nl = len(lines)
        line_ys: list[float] = []
        for k in range(nl):
            line_ys.append(sum(line_heights[:k]))
        free_space = cross_avail - sum(line_heights)
        # P27.4 — when a single line sits in a taller container with
        # align-content:stretch (default), the line stretches to fill
        # the cross axis so align-items:center places items at the
        # true container centre (spec §4.5).
        if nl == 1 and bx.explicit_height is not None and bx.align_content in ("stretch", ""):
            line_heights[0] = cross_avail
            free_space = 0.0
        if free_space > 0:
            ac = bx.align_content
            # P27.3 — wrap-reverse: lines pack against the far cross edge
            # (bottom for row), so shift all lines down by free_space
            # before applying align-content distribution.
            if wrap_reverse and nl > 1:
                for k in range(nl):
                    line_ys[k] += free_space
            if nl > 1:
                if ac == "center":
                    off = free_space / 2.0
                    for k in range(nl):
                        line_ys[k] += off
                elif ac in ("end", "flex-end"):
                    for k in range(nl):
                        line_ys[k] += free_space
                elif ac == "space-between":
                    step = free_space / (nl - 1)
                    acc = 0.0
                    for k in range(nl):
                        line_ys[k] = acc
                        acc += line_heights[k] + step
                elif ac == "space-around":
                    share = free_space / nl
                    for k in range(nl):
                        line_ys[k] = share / 2.0 + k * (line_heights[k] + share)
            else:  # nl == 1
                # Single line in a taller container: align-content still
                # distributes the free cross-axis space (spec §9.5).
                if ac in ("center", "flex-center"):
                    line_ys[0] += free_space / 2.0
                elif ac in ("end", "flex-end"):
                    line_ys[0] += free_space
                # "start" / "stretch" (default): single line stays at top.
        # else: no free space — lines pack from the start.

        # place items within each line
        for li, line in enumerate(lines):
            line_y = content_y0 + line_ys[li]
            line_h = line_heights[li]
            # P1 (assessment report) — cross-axis margin (row: the item's
            # own top margin pushes it down from the line top; column:
            # the item's left margin pushes it right from the content
            # edge).  This is the missing piece the report's
            # "Flex gap | 4,4,160,24 vs 4,0,160,20  交叉轴 margin 仍缺失"
            # line refers to: the item's cross-axis margin must offset
            # the placement, not just the main-axis advance.
            eff_line_y = line_y
            # P1 (agent report) — flex item margins: compute the *margin-box*
            # main-axis size of every item in this line.  ``sized[i]`` is
            # the content main size; add padding + border + margin to get
            # the margin-box size that real browsers distribute.
            _line_margin_box_total = 0.0
            _mb_main: list[float] = []
            for i in line:
                _cb = ordered[i]
                if row:
                    _mb_main.append(
                        sized[i]
                        + _cb.padding.left + _cb.padding.right
                        + _cb.border.left + _cb.border.right
                        + _cb.margin.left + _cb.margin.right
                    )
                else:
                    _mb_main.append(
                        sized[i]
                        + _cb.padding.top + _cb.padding.bottom
                        + _cb.border.top + _cb.border.bottom
                        + _cb.margin.top + _cb.margin.bottom
                    )
                _line_margin_box_total += _mb_main[-1]
            # P0 (retest report) — the flex `gap` property: fixed space
            # between items (m-1 gaps on the main axis) that does NOT
            # participate in justify-content free-space distribution.
            _m_line = len(line)
            _gap_main = bx.flex_gap if _m_line > 1 else 0.0
            _gap_total = _gap_main * (_m_line - 1)
            free_main = available - _line_margin_box_total - _gap_total
            jc = bx.justify_content
            # P0 (retest report) — compute the block's left edge *x_off* in
            # a forward coordinate system, then mirror for reverse
            # directions (spec: row-reverse packs from the right edge;
            # item 0 in DOM order is the rightmost item).
            x_off = content_x0
            m = _m_line
            step_space = 0.0
            if free_main > 0:
                if jc == "center":
                    x_off = content_x0 + free_main / 2.0
                elif jc in ("end", "flex-end"):
                    x_off = content_x0 + free_main
                elif jc == "space-between":
                    step_space = free_main / (m - 1) if m > 1 else 0.0
                elif jc == "space-around":
                    step_space = free_main / m
                    x_off = content_x0 + step_space / 2.0
                elif jc == "space-evenly":
                    step_space = free_main / (m + 1)
                    x_off = content_x0 + step_space
            # P0 (retest report) — mirror for reverse directions:
            # start(reverse) packs right → block left edge =
            # content_x0 + free_main; center → content_x0 + free_main/2;
            # end(reverse) packs left → content_x0.  (Note: the reverse
            # "start" maps to the forward "end" x_off, and vice versa.)
            _step_main = _gap_main + step_space  # advance between mb left edges
            if reverse:
                # P0 (retest report) — mirror for reverse directions:
                # start(reverse) packs right → block left edge =
                # content_x0 + free_main; center → content_x0 + free_main/2;
                # end(reverse) packs left → content_x0.  (The reverse
                # "start" maps to the forward "end" x_off, and vice versa.)
                if jc == "center":
                    x_off = content_x0 + free_main / 2.0
                elif jc in ("end", "flex-end"):
                    x_off = content_x0
                elif jc in ("space-around", "space-evenly"):
                    # Same distribution, mirrored: the block spans
                    # [content_x0, content_x0 + total]; x_off stays.
                    pass
                else:
                    # start (default) + space-between: pack from the
                    # right edge.
                    x_off = content_x0 + free_main
                # DOM item k's mb-left = x_off + Σ_{j>k}(mb_j + step_main)
                # (items to the left of k in the reverse layout are the
                # DOM items after k).
                _mb_main_list = _mb_main
                _suffix: list[float] = [0.0] * m
                for k in range(m - 1, -1, -1):
                    if k == m - 1:
                        _suffix[k] = 0.0
                    else:
                        _suffix[k] = _suffix[k + 1] + _mb_main_list[k + 1] + _step_main
                item_x = x_off + _suffix[0]
            else:
                item_x = x_off

            for pos, i in enumerate(line):
                cb = ordered[i]
                # P0 (agent report) — main-axis sizing: an explicit main-axis
                # size (width in row / height in column, honouring
                # box-sizing:border-box) must beat the natural content
                # measure, so `width:80px` items do not stretch to the
                # available width.
                w = sized[i]
                _main_exp = (cb.explicit_width if row else cb.explicit_height)
                if _main_exp is not None:
                    # P2 (assessment report) — content-box explicit main
                    # size IS the content size (no subtraction: padding
                    # + border grow the box outward); border-box
                    # explicit size is the border-box size, so subtract
                    # the main-axis padding + border.
                    _bb = getattr(cb, "box_sizing", "") == "border-box"
                    if _bb:
                        _bb_pad = (cb.padding.left + cb.padding.right if row
                                   else cb.padding.top + cb.padding.bottom)
                        _bb_brd = (cb.border.left + cb.border.right if row
                                   else cb.border.top + cb.border.bottom)
                        w = max(0.0, _main_exp - _bb_pad - _bb_brd)
                    else:
                        w = _main_exp
                # P1 (agent report) — flex item margins: item_x is the
                # margin-box left edge; layout_block adds margin_l internally
                # to compute content_x0, so no extra offset is needed here.
                # P27.4 — cross-axis alignment for this item (item height
                # = natural_cross; stretch fills the line height).
                item_align = cb.align_self if cb.align_self else bx.align_items
                # P0 (agent report) — an explicit cross-axis size beats the
                # natural measure (a `height` on the item).
                ch = cross_explicit[i]
                if ch is None:
                    # fall back to the Phase-0 measured cross size, which for
                    # border-box items already includes padding + border.
                    ch = max(natural_cross[i], cb.rect.h)
                # P0 (retest report) — item_x is the margin-box left edge.
                # layout_block adds margin_l + padding_l internally to
                # compute content_x0, so pass item_x directly (the legacy
                # `item_x - w` for reverse was a content-box correction
                # that double-subtracted the item width).
                _content_x = item_x
                # P1 (assessment report) — the item's own cross-axis
                # margin pushes it inward from the line/container edge
                # (row: margin.top pushes down; column: margin.left
                # pushes right).  The legacy version applied the margin
                # only on the main-axis advance, so an item with
                # `margin:4px` in a flex row with `gap:10px` still
                # landed at y=line_y (the report's flex_gap case
                # expected y=4 but got y=0).
                _cross_marg = cb.margin.top if row else cb.margin.left
                _cross_start = (eff_line_y + _cross_marg) if row \
                    else (content_x0 + _cross_marg)
                # P1 (assessment report) — the item's *far* cross-axis
                # margin (row: margin.bottom; column: margin.right).
                # align-items/align-self center/end must position the
                # item's *margin-box* inside the line/container (CSS
                # flexbox §4.5: the cross-size being centred is the
                # margin-box height, not the border-box height).  The
                # legacy code centred on the border-box height, so an
                # item with cross-axis margins landed off-centre by
                # the far margin (layout_flex c*.y: 19 vs 15,
                # flex_padding_border c*.y: 2 vs 4).
                _cross_marg_far = cb.margin.bottom if row else cb.margin.right
                _mb_cross = ch + _cross_marg + _cross_marg_far
                # cross-axis y (row: align within the line; column: x-align)
                if row:
                    if item_align in ("stretch", "") and cross_explicit[i] is None:
                        layout_block(cb, _content_x, _cross_start, w)
                        cb.rect.h = max(0.0, line_h - _cross_marg - cb.margin.bottom)
                    elif item_align in ("start", "flex-start"):
                        layout_block(cb, _content_x, _cross_start, w)
                    elif item_align in ("center", "baseline"):
                        layout_block(cb, _content_x,
                                     _cross_start + max(0.0, (line_h - _mb_cross) / 2.0), w)
                    elif item_align in ("end", "flex-end"):
                        layout_block(cb, _content_x,
                                     _cross_start + max(0.0, line_h - _mb_cross), w)
                    else:
                        layout_block(cb, _content_x, _cross_start, w)
                else:
                    # column: main axis = vertical, cross axis = horizontal.
                    if item_align in ("stretch", "") and cross_explicit[i] is None:
                        layout_block(cb, _cross_start, item_x, w)
                        cb.rect.w = max(0.0, available - _cross_marg - cb.margin.right)
                    elif item_align in ("start", "flex-start"):
                        layout_block(cb, _cross_start, item_x, w)
                    elif item_align in ("center", "baseline"):
                        layout_block(cb,
                                     _cross_start + max(0.0, (available - _mb_cross) / 2.0),
                                     item_x, w)
                    elif item_align in ("end", "flex-end"):
                        layout_block(cb, _cross_start + max(0.0, available - _mb_cross),
                                     item_x, w)
                    else:
                        layout_block(cb, _cross_start, item_x, w)
                # P1 (agent report) — advance to the next item's margin-box
                # left edge: current item's margin-box width (content +
                # padding + border + margin on both sides) + gap + step space.
                _mb_adv = (
                    w
                    + cb.padding.left + cb.padding.right
                    + cb.border.left + cb.border.right
                    + cb.margin.left + cb.margin.right
                ) if row else (
                    w
                    + cb.padding.top + cb.padding.bottom
                    + cb.border.top + cb.border.bottom
                    + cb.margin.top + cb.margin.bottom
                )
                # P0 (retest report) — reverse-direction advance: DOM item
                # k's items-to-the-left are DOM items after k, so the step
                # to the next DOM item subtracts mb_{k+1} (+gap+step), not
                # mb_k.  The legacy `item_x -= mb_current` left a
                # margin-box offset of mb_k - mb_{k+1} between adjacent
                # items whenever the items had unequal margin-box sizes
                # (the column-reverse adjacency test failed exactly that
                # way: b_bottom was 20 px below a.y).
                _mb_adv_next = _mb_adv
                if reverse and pos + 1 < len(line):
                    _nxt = ordered[line[pos + 1]]
                    _mb_adv_next = (
                        sized[line[pos + 1]]
                        + _nxt.padding.left + _nxt.padding.right
                        + _nxt.border.left + _nxt.border.right
                        + _nxt.margin.left + _nxt.margin.right
                    ) if row else (
                        sized[line[pos + 1]]
                        + _nxt.padding.top + _nxt.padding.bottom
                        + _nxt.border.top + _nxt.border.bottom
                        + _nxt.margin.top + _nxt.margin.bottom
                    )
                if reverse:
                    item_x -= _mb_adv_next + _step_main
                else:
                    item_x += _mb_adv + _step_main
        # row direction: finalize container rect
        if bx.explicit_height is not None:
            final_h = bx.explicit_height
        else:
            final_h = max(sum(line_heights), bx.line_height_px)
        bx.rect = Rect(content_x0, content_y0, available, final_h)
    else:
        # column direction: main axis = vertical, cross axis = horizontal.
        # Single line (column wrap is rare); cross-size = container width.
        cross_size = available
        # P2 (assessment report) — the column main space is the container's
        # *content* height.  ``bx.explicit_height`` is the border-box size
        # under box-sizing:border-box (the flex_column probe page sets
        # ``body{margin:0}`` and the container is plain content-box, so it
        # is already content — but border-box containers must subtract
        # their own padding + border, mirroring the row cross_avail fix
        # above).
        if bx.explicit_height is not None:
            main_avail = (bx.explicit_height
                         - bx.padding.top - bx.padding.bottom
                         - bx.border.top - bx.border.bottom
                         if bx.box_sizing == "border-box"
                         else bx.explicit_height)
        else:
            main_avail = sum(sized)
        # P0 (retest report) — margin-box main sizes + flex gap for the
        # column main axis.  ``_mb_col[i]`` is the i-th item's margin-box
        # height (content + padding + border + margin, top and bottom).
        _mb_col: list[float] = []
        _mb_col_total = 0.0
        for i in range(n):
            cb = ordered[i]
            _mb = (sized[i]
                   + cb.padding.top + cb.padding.bottom
                   + cb.border.top + cb.border.bottom
                   + cb.margin.top + cb.margin.bottom)
            _mb_col.append(_mb)
            _mb_col_total += _mb
        _gap_col = bx.flex_gap if n > 1 else 0.0
        _gap_col_total = _gap_col * (n - 1)
        free_main = main_avail - _mb_col_total - _gap_col_total
        jc = bx.justify_content
        # P0 (retest report) — forward column: compute the *first* item's
        # margin-box top (content_y0-relative) per justify-content; the
        # item's margin-box top advances downward by mb + gap + step.
        _y_start = 0.0
        step_space = 0.0
        if free_main > 0:
            if jc == "center":
                _y_start = free_main / 2.0
            elif jc in ("end", "flex-end"):
                _y_start = free_main
            elif jc == "space-between" and n > 1:
                step_space = free_main / (n - 1)
            elif jc == "space-around":
                step_space = free_main / n
                _y_start = step_space / 2.0
            elif jc == "space-evenly":
                step_space = free_main / (n + 1)
                _y_start = step_space
        _step_col = _gap_col + step_space
        # P27.5 — column-reverse: the flex-start edge is the BOTTOM.
        # Items walk UPWARD from the bottom; the DOM-first item sits at
        # the very bottom (its margin-box bottom edge is at the
        # container's content bottom edge).
        place_order = list(range(n))
        if reverse:
            # P0 (retest report) — column-reverse: item 0 (DOM-first)
            # sits at the BOTTOM.  Its margin-box bottom edge is at the
            # content bottom (main_avail); margin-box top =
            # main_avail - mb_0.  Items walk UPWARD: DOM item k's
            # mb-top = main_avail - prefix_mb(k) - k*(gap+step), where
            # prefix_mb(k) = Σ_{i≤k} mb_i.
            _prefix: list[float] = [0.0] * n
            for k in range(n):
                _prefix[k] = (_prefix[k - 1] if k > 0 else 0.0) + _mb_col[k]
            y_off = main_avail - _mb_col[0]
            for pi in place_order:
                cb = ordered[pi]
                h = sized[pi]
                _flow_y = content_y0 + y_off
                item_align = cb.align_self if cb.align_self else bx.align_items
                cw = natural_cross[pi]
                if item_align in ("stretch", "") and cb.explicit_width is None:
                    layout_block(cb, content_x0, _flow_y, cross_size)
                    cb.rect.w = available
                elif item_align == "center":
                    layout_block(cb, content_x0 + max(0.0, (cross_size - cw) / 2.0), _flow_y, cross_size)
                elif item_align in ("end", "flex-end"):
                    layout_block(cb, content_x0 + max(0.0, cross_size - cw), _flow_y, cross_size)
                else:
                    layout_block(cb, content_x0, _flow_y, cross_size)
                # Next item (above): mb-top = current mb-top - mb_next
                # - gap - step (margin-box adjacency in the packed stack).
                if pi + 1 < n:
                    y_off -= _mb_col[pi + 1] + _gap_col + step_space
                else:
                    y_off -= _mb_col[pi] + _gap_col + step_space
        else:
            y_off = _y_start
            for pi in place_order:
                cb = ordered[pi]
                h = sized[pi]
                _flow_y = content_y0 + y_off + cb.margin.top
                item_align = cb.align_self if cb.align_self else bx.align_items
                cw = natural_cross[pi]
                if item_align in ("stretch", "") and cb.explicit_width is None:
                    layout_block(cb, content_x0, _flow_y, cross_size)
                    cb.rect.w = available
                elif item_align == "center":
                    layout_block(cb, content_x0 + max(0.0, (cross_size - cw) / 2.0), _flow_y, cross_size)
                elif item_align in ("end", "flex-end"):
                    layout_block(cb, content_x0 + max(0.0, cross_size - cw), _flow_y, cross_size)
                else:
                    layout_block(cb, content_x0, _flow_y, cross_size)
                y_off += _mb_col[pi] + _step_col
        final_h = main_avail if bx.explicit_height is not None else sum(sized)
        bx.rect = Rect(content_x0, content_y0, cross_size,
                       max(final_h, bx.line_height_px))


def _layout_grid(
    bx: LayoutBox,
    content_x0: float,
    content_y0: float,
    available: float,
    containing_w: float,
) -> None:
    """P4.3 — toy CSS Grid layout.

    Children are placed into a grid-template-columns × auto-rows matrix.
    P0 (retest report) — column tracks honour the 1fr 2fr fraction
    ratios from ``grid-template-columns`` (and the ``gap`` shorthand)
    instead of the legacy equal-width split.  Row height is determined
    by the tallest child in that row.
    P33.R4 — children are positioned at their row's cumulative y offset
    (not all at content_y0), so grid cells in later rows are placed
    below those in earlier rows.
    """
    if not bx.children:
        bx.rect = Rect(content_x0, content_y0, available, bx.line_height_px)
        return

    cols = bx.grid_columns if bx.grid_columns > 0 else 1
    col_gap = bx.grid_gap
    row_gap = bx.grid_row_gap

    # P0 (retest report) — proportional track sizing (1fr 2fr + gap).
    # Each entry in grid_cols_frac is (value, unit): ("fr" → fractional,
    # "px" → fixed).  Free space (available − (cols−1)*col_gap) is
    # distributed: px tracks take their fixed width, fr tracks share the
    # remainder proportionally.  Legacy equal-split when no track list.
    fracs = getattr(bx, "grid_cols_frac", None) or []
    if len(fracs) == cols:
        free = max(0.0, available - (cols - 1) * col_gap)
        # Separate px (fixed) from fr (proportional) tracks.
        px_sum = sum(v for v, u in fracs if u == "px")
        fr_sum = sum(v for v, u in fracs if u == "fr")
        # If all tracks are fr, distribute the full free space.
        # If mixed, px tracks take their value and fr tracks share the rest.
        col_widths: list[float] = []
        if fr_sum > 0 and px_sum > 0:
            # Mixed: px tracks fixed, fr tracks share (free - px_sum).
            fr_free = max(0.0, free - px_sum)
            for v, u in fracs:
                if u == "px":
                    col_widths.append(v)
                else:
                    col_widths.append(fr_free * (v / fr_sum))
        elif fr_sum > 0:
            # All fr: distribute the full free space.
            for v, u in fracs:
                col_widths.append(free * (v / fr_sum))
        else:
            # All px (or no fr): use the px values directly.
            for v, u in fracs:
                col_widths.append(v if u == "px" else free / cols)
        col_widths = [max(0.0, w) for w in col_widths]
        # P2 (assessment report) — when the grid container is
        # ``box-sizing: border-box`` (the layout_grid probe page sets
        # ``*{box-sizing:border-box}``), ``available`` is the
        # *content* width (border-box explicit width − container
        # padding − border).  The CSS grid spec distributes track
        # sizes against the content box, so 1fr 1fr 1fr over a 300px
        # content width yields 100px tracks, not 300/3−2.  The legacy
        # equal split under-reported 98 (it subtracted the cells' own
        # borders from the track), so normalise back: the track is
        # sized off the content width, and the cell's own border-box
        # (content + padding + border) is reported by border_rect().
        if bx.box_sizing == "border-box" and len(fracs) == cols:
            _bb_pad = bx.padding.left + bx.padding.right
            _bb_brd = bx.border.left + bx.border.right
            _bb_content = max(0.0, bx.explicit_width - _bb_pad - _bb_brd) \
                if bx.explicit_width is not None else available
            _free2 = max(0.0, _bb_content - (cols - 1) * col_gap)
            _fr2 = sum(v for v, u in fracs if u == "fr")
            _px2 = sum(v for v, u in fracs if u == "px")
            if _fr2 > 0 and _px2 > 0:
                _frf = max(0.0, _free2 - _px2)
                col_widths = [v if u == "px" else _frf * (v / _fr2)
                              for v, u in fracs]
            elif _fr2 > 0:
                col_widths = [_free2 * (v / _fr2) for v, u in fracs]
            else:
                col_widths = [v if u == "px" else _free2 / cols
                              for v, u in fracs]
            col_widths = [max(0.0, w) for w in col_widths]
    else:
        # Legacy equal-split (no fraction list).
        col_w = (available - (cols - 1) * col_gap) / cols
        col_widths = [col_w] * cols

    # Phase 1: lay out each child at its natural size to get intrinsic dims.
    for cb in bx.children:
        if cb.is_display_none:
            cb.rect = Rect()
            continue
        layout_block(cb, content_x0, content_y0, available / cols)

    # Phase 2: place children into the grid, tracking cumulative row height.
    visible = [cb for cb in bx.children if not cb.is_display_none]
    n = len(visible)
    # P33.R4 — accumulate row heights so each row starts below the previous.
    row_y: dict[int, float] = {}
    row_max_h: dict[int, float] = {}
    for idx, cb in enumerate(visible):
        row = idx // cols
        col = idx % cols
        cx = content_x0
        for c2 in range(col):
            cx += col_widths[c2]
            cx += col_gap
        cy = content_y0 + row_y.get(row, 0.0) + row * row_gap
        cw = col_widths[col] if col < len(col_widths) else 0.0
        layout_block(cb, cx, cy, cw)
        row_h = cb.rect.h + cb.padding.top + cb.padding.bottom
        row_max_h[row] = max(row_max_h.get(row, 0.0), row_h)
    # P0 (agent report) — row track distribution: when the grid has row
    # track fractions (grid-template-rows: 50px 50px / 1fr 2fr), use
    # them; otherwise fall back to the legacy even-split / content-sized.
    row_fracs = getattr(bx, "grid_rows_frac", None) or []
    if len(row_fracs) == len(row_max_h) and any(f > 0 for f, u in row_fracs):
        # P0 (retest report) — proportional row sizing (mirrors columns).
        px_sum_r = sum(v for v, u in row_fracs if u == "px")
        fr_sum_r = sum(v for v, u in row_fracs if u == "fr")
        if bx.explicit_height is not None:
            free_h = max(0.0, bx.explicit_height - (len(row_fracs) - 1) * row_gap)
        else:
            free_h = None  # content-sized rows
        row_h_list: list[float] = []
        for idx_r, (v, u) in enumerate(row_fracs):
            if u == "px":
                row_h_list.append(v)
            elif fr_sum_r > 0 and free_h is not None:
                # fr track: share the remaining space.
                fr_free = max(0.0, free_h - px_sum_r)
                row_h_list.append(fr_free * (v / fr_sum_r))
            else:
                row_h_list.append(row_max_h.get(idx_r, 0.0))
        # Ensure each row is at least its content height.
        for idx_r in range(len(row_h_list)):
            if idx_r in row_max_h:
                row_h_list[idx_r] = max(row_h_list[idx_r], row_max_h[idx_r])
    else:
        row_h_list: list[float] = []
        for row in range(len(row_max_h)):
            if bx.explicit_height is not None:
                n_rows = len(row_max_h)
                row_h_list.append(max(bx.explicit_height / n_rows, row_max_h.get(row, 0.0)))
            else:
                row_h_list.append(row_max_h.get(row, 0.0))
    # P33.R4 — compute cumulative row offsets for the next pass (include
    # row_gap between rows).
    total_h = 0.0
    for row in range(len(row_max_h)):
        row_y[row] = total_h
        total_h += row_h_list[row] + row_gap
    total_h = max(0.0, total_h - row_gap)  # strip trailing gap
    # Phase 3: re-position children at their correct row y-offsets.
    for idx, cb in enumerate(visible):
        row = idx // cols
        col = idx % cols
        cx = content_x0
        for c2 in range(col):
            cx += col_widths[c2]
            cx += col_gap
        cy = content_y0 + row_y.get(row, 0.0) + row * row_gap
        cw = col_widths[col] if col < len(col_widths) else 0.0
        layout_block(cb, cx, cy, cw)
        # P0 (agent report) — stretch cell content into its row track when
        # the container height distributes the space.
        # P2 (assessment report) — under box-sizing:border-box the track
        # size is the *border-box* height, so the cell's content height
        # subtracts its own padding + border (the grid probe page sets
        # ``*{box-sizing:border-box}``, so the legacy content-height
        # subtraction over-reported the cell height by 2px — layout_grid
        # g*.h: 62 vs 60).
        if bx.explicit_height is not None:
            _cb_bd = (cb.border.top + cb.border.bottom
                      if getattr(cb, "box_sizing", "") == "border-box"
                      else 0.0)
            track_h = row_h_list[row] - cb.padding.top - cb.padding.bottom - _cb_bd
            if track_h > cb.rect.h:
                cb.rect.h = track_h
    bx.rect = Rect(content_x0, content_y0, available,
                   bx.explicit_height if bx.explicit_height is not None
                   else max(total_h, bx.line_height_px))


def _layout_table(
    bx: LayoutBox,
    content_x0: float,
    content_y0: float,
    available: float,
    containing_w: float,
) -> None:
    """P7.2 — display: table layout.

    Rows (``tr``) are laid out top-to-bottom.  Each row's cells (``td``/``th``)
    share the row height; column widths are the maximum cell content width per
    column.  With ``border-collapse: collapse``, adjacent cell borders merge
    (border drawn once between neighbours instead of twice).
    """
    if not bx.children:
        bx.rect = Rect(content_x0, content_y0, available, bx.line_height_px)
        return

    # Phase 1 — lay out every row child at the *containing* width (NOT the
    # shrunken auto table width) so cells measure their natural text
    # content without being pre-stretched; Phase 2 below re-lays cells at
    # their final column width.
    for cb in bx.children:
        layout_block(cb, content_x0, content_y0, containing_w)

    # Collect rows and their cell widths.
    rows: list[LayoutBox] = [cb for cb in bx.children if cb.is_table_row or cb.box_type == BoxType.BLOCK]
    if not rows:
        bx.rect = Rect(content_x0, content_y0, available, bx.line_height_px)
        return

    # Determine column count from the widest row.
    n_cols = 0
    for row in rows:
        cells = [c for c in row.children if c.is_table_cell or c.box_type in (BoxType.BLOCK, BoxType.TEXT, BoxType.INLINE)]
        n_cols = max(n_cols, len(cells))
    # P33.R2-2 (layout_table D3) — border-collapse: collapse is
    # declared on the <table> element (CSS2.1 §17.4.3).  The cascade
    # does NOT propagate it into the <tr>/<td> computed styles: a <td>
    # without its own declaration computes `separate`, so the row/cell
    # boxes are built with ``border_collapse=False``.  When the table
    # element itself carries collapse, probe the rows' and cells'
    # computed styles (the cascade may have set it on a descendant)
    # and fall back to the table's own flag.
    if not bx.border_collapse:
        _bc = False
        for _row in rows:
            _rowsn = getattr(_row, "dom_sn", None)
            if _rowsn is not None and _rowsn.get_keyword("border-collapse", "") == "collapse":
                _bc = True
                break
            for _cell in _row.children:
                _celsn = getattr(_cell, "dom_sn", None)
                if _celsn is not None and _celsn.get_keyword("border-collapse", "") == "collapse":
                    _bc = True
                    break
            if _bc:
                break
        if _bc:
            bx.border_collapse = True
    if n_cols == 0:
        # No cells — just stack rows.
        child_y = 0.0
        for row in rows:
            layout_block(row, content_x0, content_y0 + child_y, containing_w)
            child_y += row.outer_height()
        bx.rect = Rect(content_x0, content_y0, available, max(child_y, bx.line_height_px))
        return

    # Column widths: P0 (agent report) — a table without an explicit width
    # auto-sizes to its content: each column is the natural (unwrapped)
    # text width of the widest cell + padding + border, like a real
    # browser's `width:auto` table.  A table with an explicit width keeps
    # the legacy equal-column distribution.
    import text as _text_mod
    col_max: list[float] = [0.0] * n_cols
    # P33.R2-2 (layout_table D3) — in border-collapse mode the shared
    # border between adjacent cells is drawn ONCE, so a cell's
    # contribution to its column's natural width counts only HALF of
    # its left and right border; the table's own outer border edges
    # (half of border-left on the left, half of border-right on the
    # right) are added to the table total once.
    for row in rows:
        cells = [c for c in row.children if c.is_table_cell or c.box_type in (BoxType.BLOCK, BoxType.TEXT, BoxType.INLINE)]
        for ci in range(min(n_cols, len(cells))):
            c = cells[ci]
            # P1 (agent report) — natural width uses the cell's own
            # font family + line-height so TTF vertical metrics are
            # honoured; border-collapse halves the shared cell borders.
            # P0 (retest report) — collect the cell's text ONCE: prefer
            # the aggregated text_content (which already includes TEXT
            # descendants); fall back to the TEXT children's text joined
            # by a space only when text_content is empty (a cell with
            # only inline-element children, no direct text node).
            _fam = c.font_family if hasattr(c, "font_family") else "monospace"
            _t = c.text_content or ""
            if not _t and c.children:
                _parts = [gc.text_content for gc in c.children
                          if gc.box_type == BoxType.TEXT and gc.text_content]
                if _parts:
                    _t = " ".join(_parts)
            _natural = _text_mod.measure(_t, c.font_px, _fam) if _t else 0.0
            # P33.R2-2 (layout_table D3) — in collapse mode the shared
            # border between two adjacent cells is drawn ONCE and is
            # allocated to the gap between columns, NOT to the column
            # content.  Each column's natural width therefore counts
            # text + padding MINUS the halved border pair (the border
            # pixels are re-added at the table level via _outer_bw and
            # the inter-column gaps).  In separate mode the full
            # border pair is added to the column width.
            if bx.border_collapse:
                _bw_lr = -(c.border.left + c.border.right) / 2.0
            else:
                _bw_lr = c.border.left + c.border.right
            _cw = _natural + c.padding.left + c.padding.right + _bw_lr
            col_max[ci] = max(col_max[ci], _cw)
    # P33.R2-2 (layout_table D3) — outer border edges: in collapse mode
    # the table's outer edge is the remaining half of the outermost
    # cell's border pair (each edge column already contributed its INNER
    # half to its column width above); in separate mode the full outer
    # borders are counted.
    _outer_bw = (
        (bx.border.left + bx.border.right) / 2.0
        if bx.border_collapse else
        (bx.border.left + bx.border.right)
    )
    explicit_table_w = bx.explicit_width
    if explicit_table_w is not None:
        # legacy: distribute the explicit width equally across columns
        col_w_list = [explicit_table_w / n_cols] * n_cols
        col_w = explicit_table_w / n_cols
    else:
        # auto: shrink-to-fit columns at their natural content width,
        # never wider than the containing block.  P0 (retest report) —
        # with border-collapse the shared cell borders are halved; the
        # table border-box includes the left/right outer borders once
        # each.  Edge 2x2 single-char table ≈ 70 px, so the legacy
        # "fill the containing block" fallback over-reported 300 px.
        col_w_list = [max(w, 1.0) for w in col_max]
        col_w = max(col_w_list) if col_w_list else 0.0
        available = min(sum(col_w_list) + _outer_bw, containing_w)

    # Phase 2 — position rows and cells.
    child_y = 0.0
    # P1 (agent report) — table auto-width: the table's rect width is the
    # sum of the column widths (shrink-to-fit), NOT the containing block
    # width.  The legacy code set ``available = containing_w`` which made
    # every table fill its parent, so the 2×2 table with single-digit
    # cells reported w=800 instead of w≈70 (Edge layout probe §4).
    _table_total_w = (
        sum(col_w_list) + _outer_bw
        if not explicit_table_w else explicit_table_w
    )
    for row in rows:
        row.cells = [c for c in row.children if c.is_table_cell or c.box_type in (BoxType.BLOCK, BoxType.TEXT, BoxType.INLINE)]
        cells = row.cells
        n_cells = len(cells)
        # Lay out cells horizontally.
        cell_x = content_x0
        row_h = 0.0
        for ci in range(n_cols):
            if ci < n_cells:
                cell = cells[ci]
                cw = col_w_list[ci]
                # Force the cell width.
                cell.explicit_width = cw
                layout_block(cell, cell_x, content_y0 + child_y, cw)
                cell_x += cw
                row_h = max(row_h, cell.outer_height())
            else:
                # Empty cell — just advance.
                cell_x += col_w_list[ci]
        # Finalize row.
        row.explicit_height = row_h if row_h > 0 else bx.line_height_px
        row.rect = Rect(content_x0, content_y0 + child_y, _table_total_w, row_h)
        # Reposition cells with the final row y.
        cx = content_x0
        for ci in range(n_cells):
            cell = cells[ci]
            cell.rect.y = content_y0 + child_y
            cell.rect.x = cx
            cx += col_w_list[ci]
        child_y += row_h

    # P33.R2-2 (layout_table D3) — the table's rect is recorded at the
    # content-box origin.  border_rect() adds the FULL border pair back
    # (legacy approximation documented at L385), so we must store the
    # CONTENT width here.  In separate mode the content width is the
    # table total minus the full outer border pair.  In collapse mode
    # the border pixels (shared between table and outermost cells,
    # drawn once) are NOT part of the content; the total already
    # accounts for them via _outer_bw, so we subtract the full pair
    # to let border_rect() report the correct border-box width.
    if bx.explicit_width is not None:
        _content_w = _table_total_w
    else:
        _content_w = _table_total_w - (bx.border.left + bx.border.right)
    _content_w = max(_content_w, 0.0)
    bx.rect = Rect(content_x0, content_y0, _content_w,
                   max(child_y, bx.line_height_px))


# --- hit testing ---------------------------------------------------------------------


def hit_test(root: LayoutBox, mx: float, my: float) -> Optional[LayoutBox]:
    """Find the deepest layout box whose rect contains (mx, my).

    If the deepest match is a pure text box (dom is None), climb up to the
    enclosing element so a click on text inside a button resolves to the
    button, not the text.
    """

    def contains(bx: LayoutBox) -> bool:
        r = bx.rect
        return r.x <= mx < r.x + r.w and r.y <= my < r.y + r.h

    def find_deepest(bx: LayoutBox) -> Optional[LayoutBox]:
        """Recursively find the deepest box under bx that contains the point."""
        if bx.is_display_none or not contains(bx):
            return None
        # Search children first (deepest wins).
        for c in bx.children:
            result = find_deepest(c)
            if result is not None:
                return result
        # No containing child — this box is the deepest match.
        return bx

    result = find_deepest(root)
    if result is not None and result.dom is None and result.parent is not None:
        result = result.parent
    return result


def hit_test_with_dom(root: LayoutBox, mx: float, my: float) -> Optional[Node]:
    bx = hit_test(root, mx, my)
    return bx.dom if bx else None

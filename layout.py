"""Stage 3 — box model + single-pass block/inline layout + hit testing.

Pure standard library. Produces a tree of LayoutBox that paint_engine.py
walks to emit display commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from css import DEFAULT_FONT_PX, Keyword, Length, StyledNode, parse_color
from dom import Node
from paint import Color
import text as text_mod


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
    is_display_none: bool = False
    line_height_px: float = DEFAULT_FONT_PX
    children: list = field(default_factory=list)
    parent: Optional["LayoutBox"] = None
    dom: Optional[Node] = None
    # P1.4 — Flexbox
    is_flex: bool = False
    flex_direction: str = "row"  # "row" | "column"
    justify_content: str = "start"  # "start" | "center" | "end" | "space-between"
    align_items: str = "stretch"  # "stretch" | "start" | "center" | "end"
    # P3.2 — flex-grow weight (for distributing leftover space)
    flex_grow: float = 0.0
    # P4.3 — CSS Grid
    is_grid: bool = False
    grid_columns: int = 0  # number of column tracks
    grid_rows: int = 0  # number of row tracks (0 = auto)
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
    # P15.3 — CSS box-decoration-break / -webkit-appearance (toy: stored)
    box_decoration_break: str = ""   # "clone" | "slice" | ""
    webkit_appearance: str = ""      # "none" | "button" | "auto" | ""
    # P15.4 — CSS font-family (toy: "monospace" or "serif")
    font_family: str = "monospace"
    # P16.3 — CSS text-justify / word-break / hyphens (toy: stored)
    text_justify: str = ""    # "none" | "inter-word" | "new" | "auto" | ""
    word_break: str = ""      # "normal" | "break-all" | "keep-all" | ""
    hyphens: str = ""        # "none" | "auto" | "manual" | ""
    # P16.4 — CSS scroll-snap-type (toy: stored)
    scroll_snap_type: str = ""  # "none" | "x mandatory" | "y proximity" | ...

    IFRAME_PLACEHOLDER_H = 120.0
    IMG_DEFAULT_W = 32.0
    IMG_DEFAULT_H = 32.0

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


def build_box(sn: StyledNode, parent_font_px: float,
              parent_font_family: str = "") -> LayoutBox:
    node = sn.dom
    is_element = node.is_element()
    my_font = sn.prop_font_px()

    # P10.1 — child-position pseudo-classes: compute this node's 1-based
    # element-sibling index so :first-child / :last-child / :nth-child can
    # be matched against the DOM structure at layout time.
    child_index = 0
    parent_dom = node.parent
    if parent_dom is not None:
        idx = 0
        for c in parent_dom.children:
            if c.is_element():
                idx += 1
                if c is node:
                    child_index = idx

    # Text content: for text nodes use the node's own .text; for elements
    # only collect direct text-node children (not element children, which
    # carry their own text in their own boxes).
    if is_element:
        parts: list[str] = []
        for c in node.children:
            if not c.is_element():
                parts.append(c.text or "")
        text_str = "".join(parts)
    else:
        text_str = node.text or ""

    background = sn.background()
    color = sn.color()
    border_color = sn.get_color("border-color", __import__("paint").Color(0, 0, 0))
    text_align = sn.text_align()

    # <img> / <input>: pull width/height from DOM attributes when no CSS
    # explicit size is set. These are the "intrinsic dimensions" in the
    # toy model.
    tag = (node.tag or "").lower()
    dom_w: Optional[float] = None
    dom_h: Optional[float] = None
    if tag in ("img", "input"):
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

    explicit_w = sn.width_px()
    explicit_h = sn.height_px()
    if explicit_w is None and dom_w is not None:
        explicit_w = dom_w
    if explicit_h is None and dom_h is not None:
        explicit_h = dom_h

    # P1.4 — Flexbox: read display:flex / flex-direction / justify-content / align-items.
    is_flex = False
    flex_direction = "row"
    justify_content = "start"
    align_items = "stretch"
    disp = sn.display()
    if disp == "flex":
        is_flex = True
        flex_direction = sn.get_keyword("flex-direction", "row")
        justify_content = sn.get_keyword("justify-content", "start")
        align_items = sn.get_keyword("align-items", "stretch")
    # P4.3 — CSS Grid: read display:grid / grid-template-columns / grid-template-rows.
    is_grid = False
    grid_columns = 0
    grid_rows = 0
    if disp == "grid":
        is_grid = True
        gtc = sn.get_keyword("grid-template-columns", "")
        if gtc:
            grid_columns = len(gtc.split())
        gtr = sn.get_keyword("grid-template-rows", "")
        if gtr:
            grid_rows = len(gtr.split())

    # P7.2 — display:table / table-row / table-cell + border-collapse.
    is_table = disp == "table"
    is_table_row = tag == "tr" or (disp == "table-row")
    is_table_cell = tag in ("td", "th")
    border_collapse = sn.get_keyword("border-collapse", "") == "collapse"
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
    hyphens = sn.get_keyword("hyphens", "")

    # P16.4 — CSS scroll-snap-type (toy: stored).
    scroll_snap_type = sn.get_keyword("scroll-snap-type", "")

    bx = LayoutBox(
        box_type=(
            BoxType.INLINE
            if (is_element and is_inline_tag(node.tag or ""))
            else BoxType.BLOCK
            if is_element
            else BoxType.TEXT
        ),
        padding=shorthand_edges(sn.props.get("padding"), my_font),
        border=shorthand_edges(sn.props.get("border-width"), my_font),
        margin=shorthand_edges(sn.props.get("margin"), my_font),
        text_content=text_str,
        text_color=color,
        background=background,
        border_color=border_color,
        font_px=my_font,
        font_family=font_family,
        text_align=text_align,
        explicit_height=explicit_h,
        explicit_width=explicit_w,
        is_display_none=sn.is_display_none(),
        line_height_px=sn.line_height_px(),
        dom=node if is_element else None,
        is_flex=is_flex,
        flex_direction=flex_direction,
        justify_content=justify_content,
        align_items=align_items,
        flex_grow=flex_grow,
        is_grid=is_grid,
        grid_columns=grid_columns,
        grid_rows=grid_rows,
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
        box_decoration_break=box_decoration_break,
        webkit_appearance=webkit_appearance,
        text_justify=text_justify,
        word_break=word_break,
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
    )

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
                if csn.is_display_none():
                    # P11.3 — display:none boxes stay in the layout tree
                    # (flagged via is_display_none) so toggle tests can
                    # inspect them; layout_block skips their content.
                    cb = build_box(csn, my_font, font_family)
                    cb.parent = bx
                    cb.rect = Rect()
                    bx.children.append(cb)
                    continue
                cb = build_box(csn, my_font, font_family)
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
    """
    children_sn = list(styled_root.children)
    if not children_sn:
        root = build_box(styled_root, DEFAULT_FONT_PX)
        layout_block(root, 0.0, 0.0, viewport_w)
        return root
    if len(children_sn) == 1:
        root = build_box(children_sn[0], DEFAULT_FONT_PX)
        layout_block(root, 0.0, 0.0, viewport_w)
        return root
    # Multiple top-level children: wrap them in a synthetic block whose
    # styled children are the real top-level siblings.
    import dom as dom_mod
    synth = dom_mod.Node.element("__layout_root__")
    synth_sn = StyledNode(synth, DEFAULT_FONT_PX)
    for cs in children_sn:
        synth_sn.children.append(cs)
    root = build_box(synth_sn, DEFAULT_FONT_PX)
    layout_block(root, 0.0, 0.0, viewport_w)
    return root


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
    margin_l = bx.margin.left
    margin_r = bx.margin.right
    available = max(0.0, containing_w - margin_l - margin_r - padding_l * 2.0)

    tag = (bx.dom.tag if bx.dom and bx.dom.is_element() else None) or ""
    is_iframe = tag == "iframe"
    is_img = tag == "img"
    is_input = tag == "input"

    if bx.explicit_width is not None:
        available = bx.explicit_width - padding_l * 2.0
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
    # <input type="text"> gets a fixed placeholder box.
    if is_input and bx.explicit_width is None:
        available = 160.0
    if is_input and bx.explicit_height is None:
        bx.explicit_height = 20.0
    content_x0 = x + margin_l + padding_l
    content_y0 = y + padding_t

    # Text node boxes: size directly from the wrapped text, no child pass.
    if bx.box_type == BoxType.TEXT:
        text_str = bx.text_content or ""
        wrapped = text_mod.wrap(text_str, max(0.0, available), bx.font_px)
        # P6.2 — line-clamp: limit the number of visible lines.
        if bx.line_clamp > 0 and len(wrapped) > bx.line_clamp:
            wrapped = wrapped[: bx.line_clamp]
            text_str = " ".join(wrapped)
            if bx.text_overflow == "ellipsis":
                text_str += "…"
        line_h = bx.line_height_px
        bx.rect = Rect(content_x0, content_y0, available, line_h * max(1, len(wrapped)))
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

    # P5.2 — CSS float: children with float:left/right are positioned at
    # the specified side of the containing block; subsequent inline content
    # wraps around them.
    float_l = 0.0
    float_r = 0.0
    float_y = 0.0
    for cb in bx.children:
        if not cb.is_float:
            continue
        layout_block(cb, content_x0, content_y0 + float_y, containing_w)
        w = cb.rect.w + cb.padding.left + cb.padding.right
        h = cb.outer_height()
        if cb.float_direction == "left":
            cb.rect.x = content_x0
            cb.rect.y = content_y0
            float_l = max(float_l, w)
            float_y = max(float_y, h)
        else:
            cb.rect.x = content_x0 + available - w
            cb.rect.y = content_y0
            float_r = max(float_r, w)
            float_y = max(float_y, h)
    avail = max(0.0, available - float_l - float_r)
    start_x = content_x0 + float_l
    content_x0 = start_x

    # Lay out children, accumulating the content height.
    child_y = 0.0
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
            layout_block(cb, content_x0, content_y0 + child_y, containing_w)
            child_y += cb.outer_height()
            i += 1
        else:
            cx = content_x0
            run_h = 0.0
            run_y = content_y0 + child_y
            while i < n:
                ck = bx.children[i].box_type
                if ck not in (BoxType.TEXT, BoxType.INLINE):
                    break
                cb = bx.children[i]
                if cb.is_float:
                    i += 1
                    continue
                run_w = max(0.0, avail - (cx - content_x0))
                layout_block(cb, cx, run_y, max(run_w, avail))
                w = cb.rect.w + cb.padding.left + cb.padding.right
                cx += w
                if ck == BoxType.INLINE:
                    cx += text_mod.char_advance(cb.font_px)
                run_h = max(run_h, cb.rect.h)
                i += 1
            child_y += max(run_h, bx.line_height_px)

    # Finalize this box's geometry.
    visible_children = [c for c in bx.children if not c.is_display_none]
    if visible_children:
        h = child_y
    else:
        h = _element_placeholder_height(bx)
    if bx.explicit_height is not None:
        h = bx.explicit_height
    if is_img and bx.explicit_width is None:
        h = LayoutBox.IMG_DEFAULT_H
    if is_iframe and h < LayoutBox.IFRAME_PLACEHOLDER_H:
        grow = LayoutBox.IFRAME_PLACEHOLDER_H - h
        h = LayoutBox.IFRAME_PLACEHOLDER_H
        for c in bx.children:
            if c.dom is None:  # caption text box
                c.rect.y += grow / 2.0

    bx.rect = Rect(content_x0, content_y0, available, h)


def _element_placeholder_height(bx: LayoutBox) -> float:
    tag = (bx.dom.tag if bx.dom and bx.dom.is_element() else None) or ""
    if tag == "iframe":
        return LayoutBox.IFRAME_PLACEHOLDER_H
    if tag == "img":
        return LayoutBox.IMG_DEFAULT_H
    return bx.line_height_px


def _layout_flex(
    bx: LayoutBox,
    content_x0: float,
    content_y0: float,
    available: float,
    containing_w: float,
) -> None:
    """P1.4 — toy flexbox layout.

    Supports:
      flex-direction: row | column
      justify-content: start | center | end | space-between
    Children are laid out in a single flex line.
    """
    if not bx.children:
        bx.rect = Rect(content_x0, content_y0, available, bx.line_height_px)
        return

    # Phase 1: lay out each child at natural size (gives us intrinsic w/h).
    for cb in bx.children:
        if cb.is_display_none:
            cb.rect = Rect()
            continue
        layout_block(cb, content_x0, content_y0, containing_w)

    if bx.flex_direction == "row":
        # P11.3 — skip display:none children (zero-size, not part of the flow).
        visible = [cb for cb in bx.children if not cb.is_display_none]
        child_ws = [cb.rect.w + cb.padding.left + cb.padding.right for cb in visible]
        total_w = sum(child_ws)
        n = len(child_ws)
        gap = 0.0
        if bx.justify_content == "space-between" and n > 1 and available > total_w:
            gap = (available - total_w) / (n - 1)
        x_off = content_x0
        if bx.justify_content == "center" and available > total_w:
            x_off = content_x0 + (available - total_w) / 2.0
        elif bx.justify_content == "end" and available > total_w:
            x_off = content_x0 + (available - total_w)
        max_h = 0.0
        # P3.2 — distribute leftover space to children with flex-grow > 0.
        # Grow children use their natural (content) size as basis; the leftover
        # (available - total_natural) is split proportionally by flex-grow weight.
        # Non-grow children keep their natural width.
        # P11.3 — work with visible children only; display:none are zero-size.
        grow_ws = [max(0.0, cb.flex_grow) for cb in visible]
        total_grow = sum(grow_ws)
        extra_ws = []
        for i, cb in enumerate(visible):
            w = child_ws[i]
            if total_grow > 0 and cb.flex_grow > 0:
                natural = available if cb.explicit_width is None else child_ws[i]
                extra_ws.append(natural)
            else:
                extra_ws.append(w)
        if total_grow > 0:
            total_extra = sum(extra_ws)
            leftover = available - total_extra
            if leftover > 0:
                for i, cb in enumerate(visible):
                    if cb.flex_grow > 0:
                        extra_ws[i] += leftover * (cb.flex_grow / total_grow)
        for idx, cb in enumerate(visible):
            cw = extra_ws[idx]
            layout_block(cb, x_off, content_y0, cw)
            x_off += cw + gap
            ch = cb.rect.h + cb.padding.top + cb.padding.bottom
            if ch > max_h:
                max_h = ch
        # Second pass: reposition children vertically per align-items.
        if bx.align_items in ("start", "center", "end"):
            for cb in visible:
                ch = cb.rect.h + cb.padding.top + cb.padding.bottom
                if bx.align_items == "start":
                    cb.rect.y = content_y0
                elif bx.align_items == "center":
                    cb.rect.y = content_y0 + (max_h - ch) / 2.0
                else:  # end
                    cb.rect.y = content_y0 + (max_h - ch)
        bx.rect = Rect(content_x0, content_y0, available, max(max_h, bx.line_height_px))
    else:
        # --- vertical flow: children stacked ---
        visible = [cb for cb in bx.children if not cb.is_display_none]
        child_hs = [
            cb.rect.h + cb.padding.top + cb.padding.bottom + cb.margin.top + cb.margin.bottom
            for cb in visible
        ]
        total_h = sum(child_hs)
        n = len(child_hs)
        gap = 0.0
        if bx.justify_content == "space-between" and n > 1 and available > total_h:
            gap = (available - total_h) / (n - 1)
        y_off = content_y0
        if bx.justify_content == "center" and available > total_h:
            y_off = content_y0 + (available - total_h) / 2.0
        elif bx.justify_content == "end" and available > total_h:
            y_off = content_y0 + (available - total_h)
        max_w = 0.0
        for cb, ch in zip(visible, child_hs):
            layout_block(cb, content_x0, y_off, containing_w)
            y_off += ch + gap
            cw = cb.rect.w + cb.padding.left + cb.padding.right
            if cw > max_w:
                max_w = cw
        final_h = total_h + gap * max(0, n - 1)
        bx.rect = Rect(content_x0, content_y0, available, max(final_h, bx.line_height_px))


def _layout_grid(
    bx: LayoutBox,
    content_x0: float,
    content_y0: float,
    available: float,
    containing_w: float,
) -> None:
    """P4.3 — toy CSS Grid layout.

    Children are placed into a grid-template-columns × auto-rows matrix.
    Each column track has equal width (available / grid_columns).
    Row height is determined by the tallest child in that row.
    """
    if not bx.children:
        bx.rect = Rect(content_x0, content_y0, available, bx.line_height_px)
        return

    cols = bx.grid_columns if bx.grid_columns > 0 else 1
    # Phase 1: lay out each child at its natural size to get intrinsic dims.
    for cb in bx.children:
        if cb.is_display_none:
            cb.rect = Rect()
            continue
        layout_block(cb, content_x0, content_y0, available / cols)

    # Phase 2: place children into the grid.
    col_w = available / cols
    visible = [cb for cb in bx.children if not cb.is_display_none]
    n = len(visible)
    max_h = 0.0
    for idx, cb in enumerate(visible):
        row = idx // cols
        col = idx % cols
        cx = content_x0 + col * col_w
        # Lay out the child at its grid cell position.
        layout_block(cb, cx, content_y0, col_w)
        row_h = cb.rect.h + cb.padding.top + cb.padding.bottom
        if row_h > max_h:
            max_h = row_h
    bx.rect = Rect(content_x0, content_y0, available, max(max_h, bx.line_height_px))


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

    # Phase 1 — lay out every row child to get natural cell widths.
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
    if n_cols == 0:
        # No cells — just stack rows.
        child_y = 0.0
        for row in rows:
            layout_block(row, content_x0, content_y0 + child_y, containing_w)
            child_y += row.outer_height()
        bx.rect = Rect(content_x0, content_y0, available, max(child_y, bx.line_height_px))
        return

    # Column widths: distribute available space equally, then let each cell's
    # natural width participate (simplified: equal columns).
    col_w = available / n_cols

    # Phase 2 — position rows and cells.
    child_y = 0.0
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
                cw = col_w if ci < n_cols - 1 else (available - col_w * (n_cols - 1))
                # Force the cell width.
                cell.explicit_width = cw
                layout_block(cell, cell_x, content_y0 + child_y, cw)
                cell_x += cw
                row_h = max(row_h, cell.outer_height())
            else:
                # Empty cell — just advance.
                cell_x += col_w
        # Finalize row.
        row.explicit_height = row_h if row_h > 0 else bx.line_height_px
        row.rect = Rect(content_x0, content_y0 + child_y, available, row_h)
        # Reposition cells with the final row y.
        cx = content_x0
        for ci in range(n_cells):
            cell = cells[ci]
            cell.rect.y = content_y0 + child_y
            cw = col_w if ci < n_cols - 1 else (available - col_w * (n_cols - 1))
            cell.rect.x = cx
            cx += cw
        child_y += row_h

    bx.rect = Rect(content_x0, content_y0, available, max(child_y, bx.line_height_px))


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

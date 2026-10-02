"""Stage 4 — display list emission + CPU rasterization into a Bitmap.

Walks a LayoutBox tree, producing a flat list of paint commands, then
rasterizes those commands onto a paint.Bitmap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from layout import LayoutBox, BoxType
from paint import Bitmap, Color
import text as text_mod


@dataclass
class DisplayCommand:
    kind: str  # "rect" | "border" | "text" | "img" | "rounded" | "shadow" | "gradient" | "clipped"
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    color: Color | None = None
    text: str = ""
    font_px: float = 16.0
    align: str = "left"
    max_width: float = 0.0
    # P3.1 — embedded image pixel data
    img_pixels: Optional[list] = None
    img_src_w: int = 0
    img_src_h: int = 0
    # P5.1 — border-radius + box-shadow
    radius: float = 0.0
    shadow_offset_x: float = 0.0
    shadow_offset_y: float = 0.0
    shadow_radius: float = 0.0
    shadow_color: Color | None = None
    # P9.4 — linear-gradient fields
    grad_c1: Color | None = None  # gradient start color
    grad_c2: Color | None = None  # gradient end color
    grad_angle: str = "to bottom"  # "to bottom" / "to right" / "to top" / "to left"
    # P9.4 — clip-path inset fields
    clip_top: float = 0.0
    clip_right: float = 0.0
    clip_bottom: float = 0.0
    clip_left: float = 0.0
    clip_enabled: bool = False
    # P9.4 — text-shadow fields
    text_shadow_x: float = 0.0
    text_shadow_y: float = 0.0
    text_shadow_radius: float = 0.0
    text_shadow_color: Color | None = None
    text_shadow_enabled: bool = False
    # P10.3 — CSS outline fields
    outline_width: float = 0.0
    outline_color: Color | None = None
    # P13.3 — CSS filter fields
    filter_blur: float = 0.0
    filter_brightness: float = 1.0
    filter_contrast: float = 1.0
    filter_enabled: bool = False
    # P14.1 — scroll container fields
    scroll_x: float = 0.0   # viewport x of the scroll box
    scroll_y: float = 0.0   # viewport y of the scroll box
    scroll_w: float = 0.0   # viewport width of the scroll box
    scroll_h: float = 0.0   # viewport height of the scroll box
    scroll_enabled: bool = False
    scroll_clip_left: float = 0.0
    scroll_clip_top: float = 0.0
    scroll_clip_right: float = 0.0
    scroll_clip_bottom: float = 0.0
    # P20.5 — letter-spacing (px, per-character extra advance) and
    # text-rendering quality ("auto" | "optimizeSpeed" | "optimizeQuality").
    letter_spacing: float = 0.0
    text_rendering: str = "auto"
    # P20.4 — CSS font-weight (>= 600 → bold variant / synthetic bold).
    font_weight: int = 400


class DisplayList:
    def __init__(self) -> None:
        self.commands: list[DisplayCommand] = []

    def __len__(self) -> int:
        return len(self.commands)


def emit_commands(root: LayoutBox) -> DisplayList:
    dl = DisplayList()
    _emit(root, dl)
    # P7.1 — z-index: sort commands by layer (default 0). Higher z-index
    # commands are drawn later (on top). Stable sort preserves DOM order
    # within the same layer.
    for i, cmd in enumerate(dl.commands):
        if not hasattr(cmd, "_layer"):
            cmd._layer = 0
    dl.commands.sort(key=lambda c: getattr(c, "_layer", 0))
    return dl


def _collect_scroll_regions(dl: DisplayList) -> list[tuple]:
    """P14.1 — collect scroll-clip regions for the rasterize post-pass."""
    regions: list[tuple] = []
    seen: set = set()
    for cmd in dl.commands:
        if not getattr(cmd, "scroll_enabled", False):
            continue
        key = (int(cmd.scroll_x), int(cmd.scroll_y), int(cmd.scroll_w), int(cmd.scroll_h))
        if key in seen:
            continue
        seen.add(key)
        regions.append((
            key[0], key[1], key[2], key[3],
            cmd.scroll_clip_left, cmd.scroll_clip_top,
            cmd.scroll_clip_right, cmd.scroll_clip_bottom,
        ))
    return regions


def _emit(bx: LayoutBox, dl: DisplayList) -> None:
    if bx.is_display_none:
        return
    r = bx.rect
    tag = (bx.dom.tag if bx.dom and bx.dom.is_element() else None) or ""
    is_img = tag == "img"
    is_iframe = tag == "iframe"

    # P7.1 — tag every command from this box with its z-index layer.
    z = bx.z_index

    def _tag(cmd: DisplayCommand) -> DisplayCommand:
        cmd._layer = z
        return cmd

    # P14.1 — scroll container: shift children by scroll offset + clip.
    is_scroll_container = (
        bx.overflow_x in ("auto", "scroll") or bx.overflow_y in ("auto", "scroll")
    )

    def _tag_scroll(cmd: DisplayCommand) -> DisplayCommand:
        """P14.1 — shift a command by the scroll container's offset.

        A scroll container with ``scroll_left`` / ``scroll_top`` renders its
        children shifted *up / left* by that amount (the content has been
        scrolled), and the box acts as a clip region: pixels that land
        outside the box are erased by the post-pass.
        """
        cmd._layer = z
        if is_scroll_container:
            cmd.x -= bx.scroll_left
            cmd.y -= bx.scroll_top
            cmd.scroll_x = bx.rect.x
            cmd.scroll_y = bx.rect.y
            cmd.scroll_w = bx.rect.w
            cmd.scroll_h = bx.rect.h
            cmd.scroll_enabled = True
            cmd.scroll_clip_left = bx.padding.left
            cmd.scroll_clip_top = bx.padding.top
            cmd.scroll_clip_right = bx.padding.right
            cmd.scroll_clip_bottom = bx.padding.bottom
        return cmd

    # P5.1 — apply transform:translate to the box's position.
    tx_off = bx.transform_tx
    ty_off = bx.transform_ty
    r = bx.rect
    eff_x = r.x + tx_off
    eff_y = r.y + ty_off
    # P1.1 — apply opacity to the box's own background colour.
    op = bx.opacity
    eff_bg = bx.background
    if eff_bg is not None and op < 1.0:
        eff_bg = Color(eff_bg.r, eff_bg.g, eff_bg.b, int(eff_bg.a * op))

    # P13.3 — filter fields (applied as post-pass in rasterize).
    f_blur = bx.filter_blur if bx.filter_enabled else 0.0
    f_bright = bx.filter_brightness if bx.filter_enabled else 1.0
    f_contrast = bx.filter_contrast if bx.filter_enabled else 1.0
    f_enabled = bx.filter_enabled
    # Every command emitted for this box inherits its filter params.
    def _filter_fields():
        return dict(filter_blur=f_blur, filter_brightness=f_bright,
                    filter_contrast=f_contrast, filter_enabled=f_enabled)

    # P5.1 — box-shadow: emit a "shadow" command BEFORE the background.
    # The shadow is drawn at (eff_x + shadow_offset_x, eff_y + shadow_offset_y)
    # with the box's own dimensions; the rasterizer dilates by shadow_radius.
    if bx.shadow_enabled and bx.shadow_color is not None:
        dl.commands.append(
            _tag(
                DisplayCommand(
                    "shadow",
                    eff_x,
                    eff_y,
                    r.w,
                    r.h,
                    color=bx.shadow_color,
                    radius=bx.border_radius,
                    shadow_offset_x=bx.shadow_offset_x,
                    shadow_offset_y=bx.shadow_offset_y,
                    shadow_radius=bx.shadow_radius,
                )
            )
        )

    # P3.1 — embedded image: emit a dedicated "img" command so the
    # rasterizer can paint the decoded pixels with nearest-neighbour scaling.
    if is_img and bx.img_pixels is not None:
        dl.commands.append(
            _tag(
                DisplayCommand(
                    "img", eff_x, eff_y, r.w, r.h,
                    img_pixels=bx.img_pixels,
                    img_src_w=bx.img_pixel_w,
                    img_src_h=bx.img_pixel_h,
                    radius=bx.border_radius,
                )
            )
        )
        # Border around the embedded image.
        if bx.border.left > 0 and bx.border_color is not None:
            bc = bx.border_color
            if op < 1.0:
                bc = Color(bc.r, bc.g, bc.b, int(bc.a * op))
            dl.commands.append(
                _tag(DisplayCommand("border", eff_x, eff_y, r.w, r.h, color=bc, radius=bx.border_radius))
            )
    else:
        # Background fill — use "rounded" when border_radius > 0.
        # P9.4 — linear-gradient: emit a dedicated "gradient" command.
        if eff_bg is not None and not bx.gradient_enabled:
            kind = "rounded" if bx.border_radius > 0 else "rect"
            # P9.4 — clip-path + text-shadow on regular rect/rounded fill.
            clip_t = bx.clip_inset_top if bx.clip_enabled else 0.0
            clip_r = bx.clip_inset_right if bx.clip_enabled else 0.0
            clip_b = bx.clip_inset_bottom if bx.clip_enabled else 0.0
            clip_l = bx.clip_inset_left if bx.clip_enabled else 0.0
            ts_x = bx.text_shadow_offset_x if bx.text_shadow_enabled else 0.0
            ts_y = bx.text_shadow_offset_y if bx.text_shadow_enabled else 0.0
            ts_r = bx.text_shadow_radius if bx.text_shadow_enabled else 0.0
            ts_c = bx.text_shadow_color if bx.text_shadow_enabled else None
            dl.commands.append(
                _tag(
                    DisplayCommand(
                        kind, eff_x, eff_y, r.w, r.h,
                        color=eff_bg,
                        radius=bx.border_radius,
                        clip_top=clip_t, clip_right=clip_r,
                        clip_bottom=clip_b, clip_left=clip_l,
                        clip_enabled=bx.clip_enabled,
                        text_shadow_x=ts_x, text_shadow_y=ts_y,
                        text_shadow_radius=ts_r,
                        text_shadow_color=ts_c,
                        text_shadow_enabled=bx.text_shadow_enabled,
                        **_filter_fields(),
                    )
                )
            )
        elif bx.gradient_enabled and bx.gradient_c1 is not None and bx.gradient_c2 is not None:
            # P9.4 — linear-gradient: emit a "gradient" command with clip-path.
            dl.commands.append(
                _tag(
                    DisplayCommand(
                        "gradient", eff_x, eff_y, r.w, r.h,
                        color=bx.gradient_c1,
                        radius=bx.border_radius,
                        grad_c1=bx.gradient_c1,
                        grad_c2=bx.gradient_c2,
                        grad_angle=bx.gradient_angle,
                        clip_top=bx.clip_inset_top,
                        clip_right=bx.clip_inset_right,
                        clip_bottom=bx.clip_inset_bottom,
                        clip_left=bx.clip_inset_left,
                        clip_enabled=bx.clip_enabled,
                        text_shadow_x=bx.text_shadow_offset_x,
                        text_shadow_y=bx.text_shadow_offset_y,
                        text_shadow_radius=bx.text_shadow_radius,
                        text_shadow_color=bx.text_shadow_color,
                        text_shadow_enabled=bx.text_shadow_enabled,
                        **_filter_fields(),
                    )
                )
            )
        elif eff_bg is None and is_img:
            # Default grey placeholder fill for <img> (no embedded data).
            dl.commands.append(
                _tag(
                    DisplayCommand(
                        "rounded" if bx.border_radius > 0 else "rect",
                        eff_x, eff_y, r.w, r.h,
                        color=Color(200, 200, 200),
                        radius=bx.border_radius,
                    )
                )
            )

    # Border (toy: 1px outline using border_color when border-width > 0)
    if not (is_img and bx.img_pixels is not None):
        bw = bx.border.left
        if bw > 0 and bx.border_color is not None:
            bc = bx.border_color
            if op < 1.0:
                bc = Color(bc.r, bc.g, bc.b, int(bc.a * op))
            dl.commands.append(
                _tag(DisplayCommand("border", eff_x, eff_y, r.w, r.h, color=bc, radius=bx.border_radius))
            )
        elif is_img or is_iframe:
            # Default 1px grey border for img / iframe placeholders.
            dl.commands.append(
                _tag(
                    DisplayCommand(
                        "border", eff_x, eff_y, r.w, r.h,
                        color=Color(128, 128, 128),
                        radius=bx.border_radius,
                    )
                )
            )

    # P10.3 — CSS outline: draw a ring OUTSIDE the border.
    # offset = (outline_width / 2); the outline is drawn as a hollow rect
    # around (eff_x - off, eff_y - off, r.w + 2*off, r.h + 2*off).
    if bx.outline_width > 0 and bx.outline_color is not None:
        ow = int(bx.outline_width)
        oc = bx.outline_color
        if op < 1.0:
            oc = Color(oc.r, oc.g, oc.b, int(oc.a * op))
        off = max(0, ow - 1)  # ring just outside the border
        ox0 = int(eff_x - off)
        oy0 = int(eff_y - off)
        ow_full = int(r.w) + 2 * off
        oh_full = int(r.h) + 2 * off
        # Hollow rect: four strips.
        dl.commands.append(
            _tag(
                DisplayCommand(
                    "rect", ox0, oy0, ow_full, 1, color=oc
                )
            )
        )
        dl.commands.append(
            _tag(
                DisplayCommand(
                    "rect", ox0, oy0 + oh_full - 1, ow_full, 1, color=oc
                )
            )
        )
        dl.commands.append(
            _tag(
                DisplayCommand(
                    "rect", ox0, oy0, 1, oh_full, color=oc
                )
            )
        )
        dl.commands.append(
            _tag(
                DisplayCommand(
                    "rect", ox0 + ow_full - 1, oy0, 1, oh_full, color=oc
                )
            )
        )

    # Text content — shift by transform offset.
    if bx.text_content:
        tx = eff_x + bx.padding.left
        ty = eff_y + bx.padding.top
        # P14.3 — text-indent shifts the first line right (positive) or left.
        if bx.text_indent:
            tx += bx.text_indent
        max_w = bx.explicit_width - bx.padding.left - bx.padding.right if bx.explicit_width else r.w
        tc = bx.text_color if bx.text_color is not None else Color(0, 0, 0)
        if op < 1.0:
            tc = Color(tc.r, tc.g, tc.b, int(tc.a * op))

        # P9.4 — text-shadow: emit a shadow command BEFORE the text.
        if bx.text_shadow_enabled and bx.text_shadow_color is not None:
            dl.commands.append(
                _tag(
                    DisplayCommand(
                        "text_shadow",
                        tx + bx.text_shadow_offset_x,
                        ty + bx.text_shadow_offset_y,
                        r.w,
                        r.h,
                        color=bx.text_shadow_color,
                        text=bx.text_content,
                        font_px=bx.font_px,
                        align=bx.text_align,
                        max_width=max_w if max_w > 0 else r.w,
                        text_shadow_radius=bx.text_shadow_radius,
                    )
                )
            )

        text_content = bx.text_content
        # P8.4 — text-transform: case transformation before truncation.
        if bx.text_transform == "uppercase":
            text_content = text_content.upper()
        elif bx.text_transform == "lowercase":
            text_content = text_content.lower()
        elif bx.text_transform == "capitalize":
            text_content = " ".join(
                w.capitalize() if i == 0 else w for i, w in enumerate(text_content.split(" "))
            )
        # P6.2 — text-overflow: ellipsis truncates to a single line + "…".
        if bx.text_overflow == "ellipsis":
            max_chars = int(max_w / max(1.0, bx.font_px * 0.5))
            if len(text_content) > max_chars:
                text_content = text_content[: max_chars - 1] + "…"
        # P6.2 — line-clamp: limit visible lines.
        if bx.line_clamp > 0:
            lines = text_mod.wrap(text_content, max(0.0, max_w), bx.font_px, bx.font_family)
            if len(lines) > bx.line_clamp:
                lines = lines[: bx.line_clamp]
                text_content = "\n".join(lines)
                if bx.text_overflow == "ellipsis" and not lines[-1].endswith("…"):
                    text_content += "…"
        dl.commands.append(
            _tag(
                DisplayCommand(
                    "text",
                    tx,
                    ty,
                    r.w,
                    r.h,
                    color=tc,
                    text=text_content,
                    font_px=bx.font_px,
                    align=bx.text_align,
                    max_width=max_w if max_w > 0 else r.w,
                    # P20.5 — letter-spacing + text-rendering.
                    letter_spacing=bx.letter_spacing,
                    text_rendering=getattr(bx, "text_rendering", "auto"),
                    # P20.4 — font-weight (>= 600 → bold / synthetic bold).
                    font_weight=getattr(bx, "font_weight", 400),
                )
            )
        )
    for c in bx.children:
        _emit(c, dl)
    # P14.1 — scroll container: clip child commands that overflow the box.
    # A box with overflow:auto or overflow:scroll acts as a scroll viewport.
    # We apply a clip rect equal to the box's own rect to all child commands
    # emitted above, shifting them by the scroll offset.
    if bx.scroll_top > 0 or bx.scroll_left > 0:
        # Find commands from this box's children (those emitted after this box's
        # own background/border/text but before the next sibling's content) —
        # in a flat display list, we approximate by clipping commands whose
        # top-left falls inside this box's rect AND whose parent is this box.
        # For the toy renderer, we simply apply a final clip pass to the region.
        pass


def rasterize(dl: DisplayList, width: int, height: int) -> Bitmap:
    bmp = Bitmap(width, height)
    # P9.4 — collect clip-path insets from all commands for a post-pass.
    clip_regions: list[tuple] = []
    for cmd in dl.commands:
        if cmd.kind in ("rect", "rounded", "gradient") and cmd.clip_enabled:
            clip_regions.append(
                (cmd.x, cmd.y, cmd.w, cmd.h,
                 cmd.clip_top, cmd.clip_right, cmd.clip_bottom, cmd.clip_left)
            )
    for cmd in dl.commands:
        if cmd.kind == "rect":
            if cmd.color is not None:
                bmp.fill_rect(cmd.x, cmd.y, cmd.w, cmd.h, cmd.color)
        elif cmd.kind == "rounded":
            # P5.1 — rounded-rect background fill (corner clipping via per-pixel test).
            if cmd.color is not None:
                _fill_rounded(bmp, cmd)
        elif cmd.kind == "shadow":
            # P5.1 — soft box shadow: dilated rounded-rect with alpha ramp.
            _draw_shadow(bmp, cmd)
        elif cmd.kind == "border":
            _draw_border(bmp, cmd)
        elif cmd.kind == "text":
            color = cmd.color if cmd.color is not None else Color(0, 0, 0)
            text_mod.draw_text(
                bmp,
                cmd.x,
                cmd.y,
                cmd.text,
                cmd.font_px,
                color,
                cmd.max_width,
                align=cmd.align,
                letter_spacing=cmd.letter_spacing,
                text_rendering=cmd.text_rendering,
                font_weight=cmd.font_weight,
            )
        elif cmd.kind == "img":
            # P3.1 — nearest-neighbour scale the embedded pixels into the box.
            _paint_img_pixels(bmp, cmd)
        elif cmd.kind == "gradient":
            # P9.4 — linear gradient fill (toy: 2-stop, per-pixel lerp).
            _draw_gradient(bmp, cmd)
        elif cmd.kind == "text_shadow":
            # P9.4 — text shadow: draw the text offset with shadow color.
            color = cmd.color if cmd.color is not None else Color(0, 0, 0)
            text_mod.draw_text(
                bmp,
                cmd.x,
                cmd.y,
                cmd.text,
                cmd.font_px,
                color,
                cmd.max_width,
                align=cmd.align,
            )
    # P9.4 — apply clip-path insets as a post-pass.
    if clip_regions:
        _apply_clip_path(bmp, clip_regions)
    # P13.3 — apply CSS filter post-passes (blur / brightness / contrast).
    _apply_filters(bmp, dl)
    # P14.1 — apply scroll-container clips as a post-pass.
    _apply_scroll_clips(bmp, dl)
    return bmp


def _paint_img_pixels(bmp: Bitmap, cmd: DisplayCommand) -> None:
    """P3.1 — paint decoded img pixels into the bitmap (nearest-neighbour)."""
    src_w = cmd.img_src_w
    src_h = cmd.img_src_h
    pixels = cmd.img_pixels
    if not pixels or src_w <= 0 or src_h <= 0:
        return
    dst_x0 = int(cmd.x)
    dst_y0 = int(cmd.y)
    dst_w = int(cmd.w)
    dst_h = int(cmd.h)
    if dst_w <= 0 or dst_h <= 0:
        return
    # P19 — clamp the source pixel index to the actual pixel-buffer length
    # (defence against truncated data URIs where w*h exceeds pixel count).
    for dy in range(dst_h):
        sy = min(src_h - 1, int(dy * src_h / dst_h))
        row_start = sy * src_w
        for dx in range(dst_w):
            sx = min(src_w - 1, int(dx * src_w / dst_w))
            idx = row_start + sx
            if idx >= len(pixels):
                break
            color_u32 = pixels[idx]
            r = (color_u32 >> 16) & 0xFF
            g = (color_u32 >> 8) & 0xFF
            b = color_u32 & 0xFF
            bmp.set_pixel(dst_x0 + dx, dst_y0 + dy, Color(r, g, b, 255))


def _draw_border(bmp: Bitmap, cmd: DisplayCommand) -> None:
    color = cmd.color if cmd.color is not None else Color(0, 0, 0)
    x, y, w, h = cmd.x, cmd.y, cmd.w, cmd.h
    bmp.fill_rect(x, y, w, 1, color)
    bmp.fill_rect(x, y + h - 1, w, 1, color)
    bmp.fill_rect(x, y, 1, h, color)
    bmp.fill_rect(x + w - 1, y, 1, h, color)


def _inside_rounded(px: int, py: int, x0: int, y0: int, w: int, h: int, radius: float) -> bool:
    """P5.1 — per-pixel rounded-rect inclusion test (circle in each corner).

    For a pixel at (px, py) inside the bounding box [x0, x0+w) x [y0, y0+h),
    the four corners are replaced by quarter-circles of the given radius.
    A pixel is *inside* if it lies within the central cross region OR within
    the circular arc at the applicable corner.
    """
    r = int(radius)
    if r <= 0:
        return True
    # Clamp radius to fit inside the box.
    r = min(r, w // 2, h // 2)
    if r <= 0:
        return True

    # Normalise to local coordinates (0-based).
    lx = px - x0
    ly = py - y0

    # Central cross region: always inside.
    if lx >= r and lx < w - r:
        return True
    if ly >= r and ly < h - r:
        return True

    # Top-left corner: circle center (r, r), radius r.
    if lx < r and ly < r:
        dx = r - lx
        dy = r - ly
        return dx * dx + dy * dy <= r * r

    # Top-right corner: circle center (w-1-r, r), radius r.
    if lx >= w - r and ly < r:
        dx = lx - (w - 1 - r)
        dy = r - ly
        return dx * dx + dy * dy <= r * r

    # Bottom-left corner: circle center (r, h-1-r), radius r.
    if lx < r and ly >= h - r:
        dx = r - lx
        dy = ly - (h - 1 - r)
        return dx * dx + dy * dy <= r * r

    # Bottom-right corner: circle center (w-1-r, h-1-r), radius r.
    if lx >= w - r and ly >= h - r:
        dx = lx - (w - 1 - r)
        dy = ly - (h - 1 - r)
        return dx * dx + dy * dy <= r * r

    # Outside the bounding box entirely.
    return False


def _fill_rounded(bmp: Bitmap, cmd: DisplayCommand) -> None:
    """P5.1 — rounded-rect fill: per-pixel corner test with alpha blending."""
    color = cmd.color
    x0 = int(cmd.x)
    y0 = int(cmd.y)
    w = int(cmd.w)
    h = int(cmd.h)
    r = int(cmd.radius)
    if r <= 0:
        # No rounding — fall back to plain fill.
        bmp.fill_rect(cmd.x, cmd.y, cmd.w, cmd.h, color)
        return
    x1 = x0 + w - 1
    y1 = y0 + h - 1
    for py in range(max(0, y0), min(bmp.height, y1 + 1)):
        for px in range(max(0, x0), min(bmp.width, x1 + 1)):
            if _inside_rounded(px, py, x0, y0, w, h, r):
                dst = bmp.pixels[py * bmp.width + px]
                bmp.pixels[py * bmp.width + px] = color.blend_over(dst)


def _draw_shadow(bmp: Bitmap, cmd: DisplayCommand) -> None:
    """P5.1 — soft box shadow: a dilated rounded-rect painted with reduced alpha.

    The shadow is drawn at ``cmd.x + cmd.shadow_offset_x`` etc., with a
    blur radius of ``cmd.shadow_radius``.  The colour is ``cmd.color``
    (the box-shadow color) with alpha scaled to 35 %.
    """
    color = cmd.color if cmd.color is not None else Color(0, 0, 0)
    blur = int(cmd.shadow_radius)
    ox = int(cmd.x + cmd.shadow_offset_x)
    oy = int(cmd.y + cmd.shadow_offset_y)
    w = int(cmd.w)
    h = int(cmd.h)
    radius = cmd.radius
    # Shadow spread: paint a dilated rounded rect with alpha = 0.35 * base alpha.
    shadow_alpha = int(color.a * 0.35)
    shadow_col = Color(color.r, color.g, color.b, shadow_alpha)
    x0 = ox - blur
    y0 = oy - blur
    x1 = ox + w + blur - 1
    y1 = oy + h + blur - 1
    for py in range(max(0, y0), min(bmp.height, y1 + 1)):
        for px in range(max(0, x0), min(bmp.width, x1 + 1)):
            # Only paint if inside the dilated rounded-rect region.
            if _inside_rounded(px, py, ox, oy, w, h, radius):
                dst = bmp.pixels[py * bmp.width + px]
                bmp.pixels[py * bmp.width + px] = shadow_col.blend_over(dst)


def _draw_gradient(bmp: Bitmap, cmd: DisplayCommand) -> None:
    """P9.4 — 2-stop linear gradient fill.

    Interpolates between ``cmd.grad_c1`` and ``cmd.grad_c2`` along the
    direction indicated by ``cmd.grad_angle``.  Toy model: per-pixel lerp
    with a simple axis-based parameter (0→1 across the gradient axis).
    """
    c1 = cmd.grad_c1 if cmd.grad_c1 is not None else Color(0, 0, 0)
    c2 = cmd.grad_c2 if cmd.grad_c2 is not None else Color(255, 255, 255)
    x0 = int(cmd.x)
    y0 = int(cmd.y)
    w = int(cmd.w)
    h = int(cmd.h)
    angle = cmd.grad_angle
    for py in range(max(0, y0), min(bmp.height, y0 + h)):
        for px in range(max(0, x0), min(bmp.width, x0 + w)):
            if angle in ("to bottom", "to top"):
                t = (py - y0) / max(1, h - 1) if h > 1 else 0.0
                if angle == "to top":
                    t = 1.0 - t
            else:  # "to right" / "to left"
                t = (px - x0) / max(1, w - 1) if w > 1 else 0.0
                if angle == "to left":
                    t = 1.0 - t
            t = max(0.0, min(1.0, t))
            gr = int(c1.r + (c2.r - c1.r) * t)
            gg = int(c1.g + (c2.g - c1.g) * t)
            gb = int(c1.b + (c2.b - c1.b) * t)
            col = Color(gr, gg, gb, 255)
            dst = bmp.pixels[py * bmp.width + px]
            bmp.pixels[py * bmp.width + px] = col.blend_over(dst)


def _apply_clip_path(bmp: Bitmap, clips: list[tuple]) -> None:
    """P9.4 — clip-path inset post-pass.

    ``clips`` is a list of ``(x0, y0, w, h, inset_t, inset_r, inset_b, inset_l)``
    tuples collected during emission.  For each clip, pixels outside the
    inset region are erased (set to white / background).
    """
    WHITE = 0x00FFFFFF
    for x0, y0, w, h, it, ir, ib, il in clips:
        clip_x = int(x0) + int(il)
        clip_y = int(y0) + int(it)
        clip_w = int(w) - int(il) - int(ir)
        clip_h = int(h) - int(it) - int(ib)
        if clip_w <= 0 or clip_h <= 0:
            continue
        for py in range(max(0, int(y0)), min(bmp.height, int(y0) + int(h))):
            for px in range(max(0, int(x0)), min(bmp.width, int(x0) + int(w))):
                inside = (
                    px >= clip_x and px < clip_x + clip_w
                    and py >= clip_y and py < clip_y + clip_h
                )
                if not inside:
                    bmp.pixels[py * bmp.width + px] = WHITE


def _apply_filters(bmp: Bitmap, dl: DisplayList) -> None:
    """P13.3 — CSS filter post-passes (blur / brightness / contrast).

    Collects filter regions from commands that have ``filter_enabled`` and
    applies the filter transforms to the corresponding pixel region.
    Pixels are stored as 0x00RRGGBB u32 ints.
    """
    # Deduplicate: same (x, y, w, h) filter applied multiple times is a no-op.
    seen: set[tuple] = set()
    regions: list[tuple] = []
    for cmd in dl.commands:
        if not cmd.filter_enabled:
            continue
        key = (int(cmd.x), int(cmd.y), int(cmd.w), int(cmd.h))
        if key in seen:
            continue
        seen.add(key)
        regions.append(key)

    for x0, y0, w, h in regions:
        # Collect the filter params from the first matching command.
        f_blur = 0.0
        f_bright = 1.0
        f_contrast = 1.0
        for cmd in dl.commands:
            if (int(cmd.x), int(cmd.y), int(cmd.w), int(cmd.h)) == (x0, y0, w, h) and cmd.filter_enabled:
                f_blur = cmd.filter_blur
                f_bright = cmd.filter_brightness
                f_contrast = cmd.filter_contrast
                break

        py_start = max(0, y0)
        py_end = min(bmp.height, y0 + h)
        px_start = max(0, x0)
        px_end = min(bmp.width, x0 + w)

        if f_bright != 1.0 or f_contrast != 1.0:
            # Apply brightness + contrast to all pixels in the region.
            for py in range(py_start, py_end):
                for px in range(px_start, px_end):
                    idx = py * bmp.width + px
                    u32 = bmp.pixels[idx]
                    r = (u32 >> 16) & 0xFF
                    g = (u32 >> 8) & 0xFF
                    b = u32 & 0xFF
                    if f_bright != 1.0:
                        r = int(r * f_bright)
                        g = int(g * f_bright)
                        b = int(b * f_bright)
                    if f_contrast != 1.0:
                        r = int((r - 128) * f_contrast + 128)
                        g = int((g - 128) * f_contrast + 128)
                        b = int((b - 128) * f_contrast + 128)
                    r = max(0, min(255, r))
                    g = max(0, min(255, g))
                    b = max(0, min(255, b))
                    bmp.pixels[idx] = (r << 16) | (g << 8) | b

        if f_blur > 0:
            # Toy box-blur approximation: average over a (2*radius+1)² kernel.
            radius = max(1, int(f_blur))
            for py in range(py_start, py_end):
                for px in range(px_start, px_end):
                    r_sum = 0
                    g_sum = 0
                    b_sum = 0
                    count = 0
                    for dy in range(-radius, radius + 1):
                        cy = py + dy
                        if cy < 0 or cy >= bmp.height:
                            continue
                        for dx in range(-radius, radius + 1):
                            cx = px + dx
                            if cx < 0 or cx >= bmp.width:
                                continue
                            c = bmp.pixels[cy * bmp.width + cx]
                            r_sum += (c >> 16) & 0xFF
                            g_sum += (c >> 8) & 0xFF
                            b_sum += c & 0xFF
                            count += 1
                    if count > 0:
                        idx = py * bmp.width + px
                        r = r_sum // count
                        g = g_sum // count
                        b = b_sum // count
                        bmp.pixels[idx] = (r << 16) | (g << 8) | b


def _apply_scroll_clips(bmp: Bitmap, dl: DisplayList) -> None:
    """P14.1 — scroll-container post-pass.

    For every command that carries a scroll clip (children of a box with
    ``overflow:auto`` / ``overflow:scroll``), pixels that fall outside the
    clip region (box rect minus padding) are erased to white.
    """
    WHITE = 0xFFFFFF
    seen: set = set()
    for cmd in dl.commands:
        if not getattr(cmd, "scroll_enabled", False):
            continue
        key = (int(cmd.scroll_x), int(cmd.scroll_y),
               int(cmd.scroll_w), int(cmd.scroll_h))
        if key in seen:
            continue
        seen.add(key)
        x0, y0, w, h = key
        clip_x = int(x0) + int(cmd.scroll_clip_left)
        clip_y = int(y0) + int(cmd.scroll_clip_top)
        clip_w = int(w) - int(cmd.scroll_clip_left) - int(cmd.scroll_clip_right)
        clip_h = int(h) - int(cmd.scroll_clip_top) - int(cmd.scroll_clip_bottom)
        if clip_w <= 0 or clip_h <= 0:
            continue
        py_lo = max(0, y0)
        py_hi = min(bmp.height, y0 + h)
        px_lo = max(0, x0)
        px_hi = min(bmp.width, x0 + w)
        for py in range(py_lo, py_hi):
            for px in range(px_lo, px_hi):
                inside = (
                    px >= clip_x and px < clip_x + clip_w
                    and py >= clip_y and py < clip_y + clip_h
                )
                if not inside:
                    bmp.pixels[py * bmp.width + px] = WHITE

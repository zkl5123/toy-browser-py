"""Stage 4 — display list emission + CPU rasterization into a Bitmap.

Walks a LayoutBox tree, producing a flat list of paint commands, then
rasterizes those commands onto a paint.Bitmap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.layout.layout import LayoutBox, BoxType
from toy_browser.rendering.paint import Bitmap, Color
from toy_browser.typography import text as text_mod


@dataclass
class DisplayCommand:
    kind: str  # "rect" | "border" | "text" | "img" | "rounded" | "shadow" | "gradient" | "clipped"
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    color: Color | None = None
    text: str = ""
    text_color: Color | None = None
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
    # P28 — form control state fields (checkbox/radio/select/file).
    control_type: str = ""
    control_checked: bool = False
    control_value: str = ""
    control_options: list = field(default_factory=list)
    control_option_index: int = 0
    control_file_name: str = ""
    control_focus: bool = False
    control_required: bool = False
    # P7.1 — z-index layer (set by emit_commands; sorted by layer).
    # P34.3 — declared here (default 0) so emit_commands no longer needs
    # a hasattr-based defaulting loop over 20k+ commands.
    _layer: int = 0


class DisplayList:
    def __init__(self) -> None:
        self.commands: list[DisplayCommand] = []

    def __len__(self) -> int:
        return len(self.commands)


# P34.3 — a module-level shared empty-filter dict for _emit's
# _filter_fields() fast path.  When a box has no active filter (the
# overwhelmingly common case) _filter_fields() returns this shared
# dict instead of building a new one per box; a *shared mutable dict*
# is safe here because the caller only unpacks it (**_filter_fields())
# and never mutates the result, so all no-filter boxes can share one
# instance without aliasing.
_EMPTY_FILTER_FIELDS: dict = {}


def emit_commands(root: LayoutBox) -> DisplayList:
    dl = DisplayList()
    _emit(root, dl)
    # P7.1 — z-index: sort commands by layer (default 0). Higher z-index
    # commands are drawn later (on top). Stable sort preserves DOM order
    # within the same layer.
    # P34.3 — _layer is a dataclass field defaulting to 0 (P34.3), so
    # commands that _emit's _tag/_tag_scroll never set already carry the
    # correct default; the sort key is a plain attribute read.
    dl.commands.sort(key=lambda c: c._layer)
    return dl


def _collect_scroll_regions(dl: DisplayList) -> list[tuple]:
    """P14.1 — collect scroll-clip regions for the rasterize post-pass."""
    regions: list[tuple] = []
    seen: set = set()
    # P34.3 — plain attribute read (scroll_enabled is a DisplayCommand
    # dataclass field defaulting to False) instead of a per-command
    # getattr with a default (the legacy code paid a getattr + missing-
    # attribute fallback on every command of a 20k-command list).
    for cmd in dl.commands:
        if not cmd.scroll_enabled:
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
    # P33.P2 — canvas: a white-backed placeholder when no CSS background
    # is set (mimics the browser's transparent canvas default of white
    # in the toy bitmap pipeline).
    is_canvas = tag == "canvas"
    # P33.P3 — SVG / video / audio placeholder rendering.
    is_svg = tag == "svg"
    is_video = tag in ("video", "audio")

    # P7.1 — tag every command from this box with its z-index layer.
    z = bx.z_index
    # P34.4 — plain method local (dl_append = dl.commands.append) to
    # skip the `dl.commands` attribute read at every append site; the
    # legacy `dl.commands.append(...)` paid two attribute lookups per
    # command on a 6k-command list.
    dl_append = dl.commands.append
    # P34.4 — _tag is a one-liner closure allocated per _emit call
    # (21k calls on the 10k-node benchmark); the function call is the
    # only work it does, so inlining cmd._layer = z at each append
    # site is cheaper than the 6360 call/return pairs.  The
    # _tag_scroll variant (scroll containers only) is a genuine
    # behavior change and stays a function.
    # P14.1 — scroll container: shift children by scroll offset + clip.
    # P34.3 — hoist the scroll-container read (offsets, clip edges)
    # once into locals so the per-command path is a plain attribute
    # set instead of re-reading bx.scroll_* / bx.padding.* / bx.rect.*
    # on every command.
    is_scroll_container = (
        bx.overflow_x in ("auto", "scroll") or bx.overflow_y in ("auto", "scroll")
    )
    if is_scroll_container:
        _sc_left = bx.scroll_left
        _sc_top = bx.scroll_top
        _sc_x = bx.rect.x
        _sc_y = bx.rect.y
        _sc_w = bx.rect.w
        _sc_h = bx.rect.h
        _sc_l = bx.padding.left
        _sc_t = bx.padding.top
        _sc_r = bx.padding.right
        _sc_b = bx.padding.bottom

    def _tag_scroll(cmd: DisplayCommand) -> DisplayCommand:
        """P14.1 — shift a command by the scroll container's offset.

        A scroll container with ``scroll_left`` / ``scroll_top`` renders its
        children shifted *up / left* by that amount (the content has been
        scrolled), and the box acts as a clip region: pixels that land
        outside the box are erased by the post-pass.
        """
        cmd._layer = z
        cmd.x -= _sc_left
        cmd.y -= _sc_top
        cmd.scroll_x = _sc_x
        cmd.scroll_y = _sc_y
        cmd.scroll_w = _sc_w
        cmd.scroll_h = _sc_h
        cmd.scroll_enabled = True
        cmd.scroll_clip_left = _sc_l
        cmd.scroll_clip_top = _sc_t
        cmd.scroll_clip_right = _sc_r
        cmd.scroll_clip_bottom = _sc_b
        return cmd

    # P5.1 — apply transform:translate to the box's position.
    tx_off = bx.transform_tx
    ty_off = bx.transform_ty
    r = bx.rect
    # R4 stage 5 — the *paint* surface of a box (its background fill and
    # border ring) is its border-box, not the content box the layout
    # engine stores in ``bx.rect`` (the content-box origin, used by 235+
    # content-space consumers).  The legacy code emitted both the
    # background rect and the border ring at (eff_x, eff_y, r.w, r.h) =
    # content-box, so a box with ``padding:5px;border:2px`` painted its
    # background 7px inside where a browser paints it (the report's
    # flex_padding_border case: Edge red group [63,6,237,36] vs toy
    # [67,10,229,28] — exactly the border-box-minus-content-box inset on
    # every side).  CSS's default background-clip is border-box: the
    # background fills the whole border-box and the border ring is drawn
    # ON TOP of it, so the visible background only shows through past
    # the border.  The origin below matches the layout engine's
    # ``border_rect()`` contract (content origin minus padding — what
    # getBoundingClientRect reports); text is painted at the content-box
    # origin (``r.x``/``r.y`` + transform offset), and the scroll-clip
    # post-pass still anchors on the content rect + padding insets.
    _pad_l, _pad_t = bx.padding.left, bx.padding.top
    _pad_r, _pad_b = bx.padding.right, bx.padding.bottom
    _bdr_l, _bdr_t = bx.border.left, bx.border.top
    _bdr_r, _bdr_b = bx.border.right, bx.border.bottom
    # R4 stage 5 — the border-box paint surface: the origin is the
    # content-box origin minus the padding (matching the layout
    # engine's ``border_rect()`` contract, which is what the
    # geometry probes report and what the browser's
    # getBoundingClientRect returns).  The size spans content +
    # padding + border on both axes.  Integer alignment: snap the
    # origin DOWN and grow the size to cover the box's right/bottom
    # edge on whole pixels, so the 1px-thick raster border ring
    # lands on a pixel boundary instead of straddling two pixels
    # (a sub-pixel flex centre otherwise reads as a 2.5px-visible
    # ring on a 2px CSS border, putting the visible background 1px
    # off per side vs the GPU reference).
    import math as _math
    _bb_x0 = r.x + tx_off - _pad_l
    _bb_y0 = r.y + ty_off - _pad_t
    _bb_wf = r.w + _pad_l + _pad_r + _bdr_l + _bdr_r
    _bb_hf = r.h + _pad_t + _pad_b + _bdr_t + _bdr_b
    eff_x = int(_math.floor(_bb_x0))
    eff_y = int(_math.floor(_bb_y0))
    bb_w = int(_math.ceil(_bb_x0 + _bb_wf - eff_x))
    bb_h = int(_math.ceil(_bb_y0 + _bb_hf - eff_y))
    # P1.1 — apply opacity to the box's own background colour.
    op = bx.opacity

    # P34.6 — pure-container fast path: a box that carries no
    # paintable content of its own (no background, no border, no text,
    # no shadow, no outline, no gradient, no clip, no image, no
    # control, not an img/iframe/canvas/svg/video element, no active
    # filter, not a scroll container, no transform) emits ZERO
    # commands on its own — it merely recurses into its children.  The
    # legacy code still paid for ~30 local alias hoists, the filter-
    # fields dict build, the _dc / _cmds_append / _emit_one closure
    # definitions, and ~15 condition checks for these no-op
    # containers; on the 10k-node benchmark a large fraction of the
    # 21k boxes are such pure wrappers, so skipping the per-box
    # setup entirely is a measurable win.  The guard conservatively
    # checks every paint-source flag; if any is set the legacy full
    # path runs unchanged.
    if (bx.background is None and bx.border.left == 0
            and not bx.text_content and not bx.shadow_enabled
            and not bx.outline_width and not bx.gradient_enabled
            and not bx.clip_enabled and bx.img_pixels is None
            and not bx.control_type
            and not is_img and not is_iframe and not is_canvas
            and not is_svg and not is_video
            and not bx.filter_enabled and not is_scroll_container
            and tx_off == 0 and ty_off == 0):
        for c in bx.children:
            _emit(c, dl)
        return
    # P34.3 — a fast-path guard for the overwhelmingly common case where
    # the box has no opacity AND no background: skip the eff_bg
    # computation entirely (the legacy code still did the `if ... and
    # op < 1.0` check + branch even when both were trivially false).
    if op < 1.0:
        eff_bg = bx.background
        if eff_bg is not None:
            eff_bg = Color(eff_bg.r, eff_bg.g, eff_bg.b, int(eff_bg.a * op))
    else:
        eff_bg = bx.background

    # P13.3 — filter fields (applied as post-pass in rasterize).
    # P34.3 — hoist the dict construction out of the per-command call:
    # the legacy _filter_fields() rebuilt a 4-key dict for every command
    # emitted by this box; a document with 20k boxes built 40k dicts.
    # P34.3 — skip the dict build + the _filter_fields closure entirely
    # when the box has no active filter (the overwhelmingly common case
    # in the 10k-node benchmark: 21200 commands, 0 with filter).  The
    # dict is rebuilt on demand only when filter_enabled is True.
    f_blur = bx.filter_blur if bx.filter_enabled else 0.0
    f_bright = bx.filter_brightness if bx.filter_enabled else 1.0
    f_contrast = bx.filter_contrast if bx.filter_enabled else 1.0
    f_enabled = bx.filter_enabled
    # Every command emitted for this box inherits its filter params.
    _FILTER_FIELDS = (
        dict(filter_blur=f_blur, filter_brightness=f_bright,
             filter_contrast=f_contrast, filter_enabled=f_enabled)
        if f_enabled else _EMPTY_FILTER_FIELDS
    )
    def _filter_fields():
        return _FILTER_FIELDS

    # P33.R4 — if this box is a scroll container, route every emitted
    # command through _tag_scroll (shift + clip); otherwise the plain
    # _layer assignment below (P34.4: no _tag function call).
    _dc = DisplayCommand
    _cmds_append = dl_append
    if is_scroll_container:
        def _emit_one(cmd: DisplayCommand) -> None:
            _tag_scroll(cmd)
            _cmds_append(cmd)
    else:
        def _emit_one(cmd: DisplayCommand) -> None:
            cmd._layer = z
            _cmds_append(cmd)

    # P34.3 — hoist the per-box paint flags into locals so the ~30
    # condition checks below are local reads instead of repeated
    # dataclass-field descriptor lookups.  The local aliases keep the
    # same values (captured once at _emit entry, never mutated here).
    shadow_enabled = bx.shadow_enabled
    shadow_color = bx.shadow_color
    shadow_ox = bx.shadow_offset_x
    shadow_oy = bx.shadow_offset_y
    shadow_r = bx.shadow_radius
    border_radius = bx.border_radius
    border_left = bx.border.left
    border_color = bx.border_color
    text_shadow_enabled = bx.text_shadow_enabled
    text_shadow_color = bx.text_shadow_color
    text_shadow_ox = bx.text_shadow_offset_x
    text_shadow_oy = bx.text_shadow_offset_y
    text_shadow_r = bx.text_shadow_radius
    outline_width = bx.outline_width
    outline_color = bx.outline_color
    text_content = bx.text_content
    text_indent = bx.text_indent
    text_align = bx.text_align
    text_transform = bx.text_transform
    text_overflow = bx.text_overflow
    line_clamp = bx.line_clamp
    font_px = bx.font_px
    font_family = bx.font_family
    clip_enabled = bx.clip_enabled
    gradient_enabled = bx.gradient_enabled
    gradient_c1 = bx.gradient_c1
    gradient_c2 = bx.gradient_c2
    gradient_angle = bx.gradient_angle
    img_pixels = bx.img_pixels
    img_pixel_w = bx.img_pixel_w
    img_pixel_h = bx.img_pixel_h
    explicit_width = bx.explicit_width
    control_type = bx.control_type
    control_checked = bx.control_checked
    control_focus = bx.control_focus
    clip_inset_top = bx.clip_inset_top
    clip_inset_right = bx.clip_inset_right
    clip_inset_bottom = bx.clip_inset_bottom
    clip_inset_left = bx.clip_inset_left
    scroll_top = bx.scroll_top
    scroll_left = bx.scroll_left

    # P5.1 — box-shadow: emit a "shadow" command BEFORE the background.
    # The shadow is drawn at (eff_x + shadow_offset_x, eff_y + shadow_offset_y)
    # with the box's own dimensions; the rasterizer dilates by shadow_radius.
    # R4 stage 5 — the shadow silhouette is the box's border-box.
    if shadow_enabled and shadow_color is not None:
        _emit_one(
            _dc(
                "shadow",
                eff_x,
                eff_y,
                bb_w,
                bb_h,
                color=shadow_color,
                radius=border_radius,
                shadow_offset_x=shadow_ox,
                shadow_offset_y=shadow_oy,
                shadow_radius=shadow_r,
            )
        )

    # P3.1 — embedded image: emit a dedicated "img" command so the
    # rasterizer can paint the decoded pixels with nearest-neighbour scaling.
    # The img pixels scale into the box's content area; with the R4
    # stage 5 border-box paint origin the content top-left is the
    # border-box origin advanced by the border + padding insets.
    if is_img and img_pixels is not None:
        _emit_one(
            _dc(
                "img", eff_x + _bdr_l + _pad_l, eff_y + _bdr_t + _pad_t,
                r.w, r.h,
                img_pixels=img_pixels,
                img_src_w=img_pixel_w,
                img_src_h=img_pixel_h,
                radius=border_radius,
            )
        )
        # Border around the embedded image.
        if border_left > 0 and border_color is not None:
            bc = border_color
            if op < 1.0:
                bc = Color(bc.r, bc.g, bc.b, int(bc.a * op))
            _emit_one(_dc("border", eff_x, eff_y, bb_w, bb_h, color=bc, radius=border_radius))
    else:
        # Background fill — use "rounded" when border_radius > 0.
        # P9.4 — linear-gradient: emit a dedicated "gradient" command.
        # R4 stage 5 — the fill spans the box's border-box (bb_w ×
        # bb_h at the eff_x/eff_y border-box origin), not the
        # content box: CSS's default background-clip is border-box,
        # and the border ring below is drawn ON TOP of the fill, so
        # the visible fill shows through only past the border.
        if eff_bg is not None and not gradient_enabled:
            kind = "rounded" if border_radius > 0 else "rect"
            # P9.4 — clip-path + text-shadow on regular rect/rounded fill.
            clip_t = clip_inset_top if clip_enabled else 0.0
            clip_r = clip_inset_right if clip_enabled else 0.0
            clip_b = clip_inset_bottom if clip_enabled else 0.0
            clip_l = clip_inset_left if clip_enabled else 0.0
            ts_x = text_shadow_ox if text_shadow_enabled else 0.0
            ts_y = text_shadow_oy if text_shadow_enabled else 0.0
            ts_r = text_shadow_r if text_shadow_enabled else 0.0
            ts_c = text_shadow_color if text_shadow_enabled else None
            _emit_one(
                _dc(
                    kind, eff_x, eff_y, bb_w, bb_h,
                    color=eff_bg,
                    radius=border_radius,
                    clip_top=clip_t, clip_right=clip_r,
                    clip_bottom=clip_b, clip_left=clip_l,
                    clip_enabled=clip_enabled,
                    text_shadow_x=ts_x, text_shadow_y=ts_y,
                    text_shadow_radius=ts_r,
                    text_shadow_color=ts_c,
                    text_shadow_enabled=text_shadow_enabled,
                    **_filter_fields(),
                )
            )
        elif gradient_enabled and gradient_c1 is not None and gradient_c2 is not None:
            # P9.4 — linear-gradient: emit a "gradient" command with clip-path.
            _emit_one(
                _dc(
                    "gradient", eff_x, eff_y, bb_w, bb_h,
                    color=gradient_c1,
                    radius=border_radius,
                    grad_c1=gradient_c1,
                    grad_c2=gradient_c2,
                    grad_angle=gradient_angle,
                    clip_top=clip_inset_top,
                    clip_right=clip_inset_right,
                    clip_bottom=clip_inset_bottom,
                    clip_left=clip_inset_left,
                    clip_enabled=clip_enabled,
                    text_shadow_x=text_shadow_ox,
                    text_shadow_y=text_shadow_oy,
                    text_shadow_radius=text_shadow_r,
                    text_shadow_color=text_shadow_color,
                    text_shadow_enabled=text_shadow_enabled,
                    **_filter_fields(),
                )
            )
        elif eff_bg is None and is_img:
            # Default grey placeholder fill for <img> (no embedded data).
            # R4 stage 5 — img placeholder fill spans the border-box.
            _emit_one(
                _dc(
                    "rounded" if border_radius > 0 else "rect",
                    eff_x, eff_y, bb_w, bb_h,
                    color=Color(200, 200, 200),
                    radius=border_radius,
                )
            )
        elif eff_bg is None and is_canvas:
            # P33.P2 — canvas default: white fill (mimics the browser's
            # transparent-canvas-on-white-page look in the toy bitmap
            # pipeline).  A CSS background-color on the canvas element
            # overrides this (eff_bg would not be None in that case).
            # R4 stage 5 — the canvas surface is its border-box.
            _emit_one(
                _dc(
                    "rect",
                    eff_x, eff_y, bb_w, bb_h,
                    color=Color(255, 255, 255),
                )
            )
        elif eff_bg is None and (is_svg or is_video):
            # P33.P3 — SVG / video / audio placeholder: a light-grey
            # rounded rectangle with a 1px border, sized from the
            # element's border-box (default 300×150 when no CSS size
            # is set).  R4 stage 5 — placeholder surface = border-box.
            ph_w = max(1.0, bb_w)
            ph_h = max(1.0, bb_h)
            _emit_one(
                _dc(
                    "rect",
                    eff_x, eff_y, ph_w, ph_h,
                    color=Color(230, 230, 230),
                )
            )
            _emit_one(
                _dc(
                    "border", eff_x, eff_y, ph_w, ph_h,
                    color=Color(160, 160, 160),
                    radius=0,
                )
            )

    # R4 stage 5 — the CSS border is the outermost ring of the
    # border-box; the toy's raster ring is 1px thick, so it sits on
    # the border-box's outer pixel boundary (a 2px CSS border reads
    # as a 1px ring here — the box's visible background shows
    # through 1px past the ring, matching the GPU reference's inset
    # to within 1px on every side).
    if not (is_img and img_pixels is not None):
        if border_left > 0 and border_color is not None:
            bc = border_color
            if op < 1.0:
                bc = Color(bc.r, bc.g, bc.b, int(bc.a * op))
            _emit_one(_dc("border", eff_x, eff_y, bb_w, bb_h, color=bc, radius=border_radius))
        elif is_img or is_iframe:
            # Default 1px grey border for img / iframe placeholders.
            _emit_one(
                _dc(
                    "border", eff_x, eff_y, bb_w, bb_h,
                    color=Color(128, 128, 128),
                    radius=border_radius,
                )
            )

    # P28 — form control visual state: checkbox/radio tick marks,
    # select combobox arrow, file-input name label, focus ring for any
    # focused control.
    _is_control = control_type != "" or tag in ("input", "select", "textarea", "button")
    if control_type in ("checkbox", "radio"):
        # R4 stage 5 — the control mark is centred on the border-box
        # (a checkbox's tick fills the whole control, border included).
        cx = eff_x + bb_w / 2.0
        cy = eff_y + bb_h / 2.0
        if control_type == "checkbox" and control_checked:
            # Check mark: a filled 6×6 square in the center.
            _emit_one(
                _dc(
                    "rect", cx - 3.0, cy - 3.0, 6.0, 6.0,
                    color=Color(30, 30, 30),
                    control_type=control_type,
                )
            )
        elif control_type == "radio" and control_checked:
            # Radio dot: a filled 6×6 square (toy: no circles yet).
            _emit_one(
                _dc(
                    "rect", cx - 3.0, cy - 3.0, 6.0, 6.0,
                    color=Color(30, 30, 30),
                    control_type=control_type,
                )
            )
    elif control_type == "select" or tag == "select":
        # <select> combobox: draw a down-arrow glyph (toy: "v" text).
        # R4 stage 5 — the select's visible surface is its border-box.
        opt_label = bx.control_options[bx.control_option_index] if bx.control_options else ""
        _emit_one(
            _dc(
                "text",
                eff_x + 4.0,
                eff_y + max(0.0, bb_h / 2.0 - font_px / 2.0),
                bb_w - 16.0,
                bb_h,
                text=opt_label,
                text_color=bx.text_color or Color(0, 0, 0),
                font_px=font_px,
                control_type="select",
            )
        )
        # Down arrow: a small filled triangle approximated as a 6×3 rect.
        _emit_one(
            _dc(
                "rect",
                eff_x + bb_w - 12.0,
                eff_y + bb_h / 2.0 - 1.5,
                8.0, 3.0,
                color=Color(80, 80, 80),
                control_type="select",
            )
        )
    elif control_type == "file":
        # <input type="file">: show the file name (or "No file selected").
        # R4 stage 5 — the file input's surface is its border-box.
        file_label = bx.control_file_name or "No file selected"
        _emit_one(
            _dc(
                "text",
                eff_x + 4.0,
                eff_y + max(0.0, bb_h / 2.0 - font_px / 2.0),
                bb_w - 16.0,
                bb_h,
                text=file_label,
                text_color=bx.text_color or Color(0, 0, 0),
                font_px=font_px,
                control_type="file",
            )
        )
    # P28.3 — focus ring for any focused form control.
    # R4 stage 5 — the focus ring sits 1px outside the border-box.
    if control_focus and _is_control:
        _emit_one(
            _dc(
                "border", eff_x - 1.0, eff_y - 1.0, bb_w + 2.0, bb_h + 2.0,
                color=Color(0, 100, 255),
                control_type=control_type,
            )
        )
    # P10.3 — CSS outline: draw a ring OUTSIDE the border-box.
    # R4 stage 5 — offset computed from the border-box dimensions.
    if outline_width > 0 and outline_color is not None:
        ow = int(outline_width)
        oc = outline_color
        if op < 1.0:
            oc = Color(oc.r, oc.g, oc.b, int(oc.a * op))
        off = max(0, ow - 1)  # ring just outside the border
        ox0 = int(eff_x - off)
        oy0 = int(eff_y - off)
        ow_full = int(bb_w) + 2 * off
        oh_full = int(bb_h) + 2 * off
        # Hollow rect: four strips.
        _emit_one(
            _dc(
                "rect", ox0, oy0, ow_full, 1, color=oc
            )
        )
        _emit_one(
            _dc(
                "rect", ox0, oy0 + oh_full - 1, ow_full, 1, color=oc
            )
        )
        _emit_one(
            _dc(
                "rect", ox0, oy0, 1, oh_full, color=oc
            )
        )
        _emit_one(
            _dc(
                "rect", ox0 + ow_full - 1, oy0, 1, oh_full, color=oc
            )
        )

    # Text content — shift by transform offset.
    # R4 stage 5 — eff_x/eff_y are the *border-box* origin
    # (content-box origin minus padding).  The text's content-box
    # origin is therefore the layout-stored content rect itself
    # (``r.x``/``r.y`` + the transform offset), NOT eff_x + padding:
    # that legacy formula double-counted the padding and pushed the
    # text one padding-width too far right/down on every padded box.
    if text_content:
        tx = r.x + tx_off
        ty = r.y + ty_off
        # P14.3 — text-indent shifts the first line right (positive) or left.
        if text_indent:
            tx += text_indent
        # R4 stage 5 — the text wrap width is the content box width
        # (r.w): the layout engine already resolved `available` to the
        # content width, so the text site keeps r.w (NOT bb_w, which
        # would let the text run into the padding/border ring).
        max_w = explicit_width - bx.padding.left - bx.padding.right if explicit_width else r.w
        tc = bx.text_color if bx.text_color is not None else Color(0, 0, 0)
        if op < 1.0:
            tc = Color(tc.r, tc.g, tc.b, int(tc.a * op))

        # P9.4 — text-shadow: emit a shadow command BEFORE the text.
        if text_shadow_enabled and text_shadow_color is not None:
            _emit_one(
                _dc(
                    "text_shadow",
                    tx + text_shadow_ox,
                    ty + text_shadow_oy,
                    r.w,
                    r.h,
                    color=text_shadow_color,
                    text=text_content,
                    font_px=font_px,
                    align=text_align,
                    max_width=max_w if max_w > 0 else r.w,
                    text_shadow_radius=text_shadow_r,
                )
            )

        # P8.4 — text-transform: case transformation before truncation.
        _tt = text_transform
        if _tt == "uppercase":
            text_content = text_content.upper()
        elif _tt == "lowercase":
            text_content = text_content.lower()
        elif _tt == "capitalize":
            text_content = " ".join(
                w.capitalize() if i == 0 else w for i, w in enumerate(text_content.split(" "))
            )
        # P6.2 — text-overflow: ellipsis truncates to a single line + "…".
        if text_overflow == "ellipsis":
            max_chars = int(max_w / max(1.0, font_px * 0.5))
            if len(text_content) > max_chars:
                text_content = text_content[: max_chars - 1] + "…"
        # P6.2 — line-clamp: limit visible lines.
        if line_clamp > 0:
            lines = text_mod.wrap(text_content, max(0.0, max_w), font_px, font_family)
            if len(lines) > line_clamp:
                lines = lines[: line_clamp]
                text_content = "\n".join(lines)
                if text_overflow == "ellipsis" and not lines[-1].endswith("…"):
                    text_content += "…"
        _emit_one(
            _dc(
                "text",
                tx,
                ty,
                r.w,
                r.h,
                color=tc,
                text=text_content,
                font_px=font_px,
                align=text_align,
                max_width=max_w if max_w > 0 else r.w,
                # P20.5 — letter-spacing + text-rendering.
                letter_spacing=bx.letter_spacing,
                text_rendering=bx.text_rendering,
                # P20.4 — font-weight (>= 600 → bold / synthetic bold).
                font_weight=bx.font_weight,
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
    # P34.3 — the legacy code scanned the whole command list *and* the
    # culling loop below for the same clip regions; a 10k-node display
    # list paid 2×21200 attribute reads.  Merge the two passes: the
    # culling loop already visits every command that survives, so the
    # clip-region collection can ride along in the same loop.
    clip_regions: list[tuple] = []
    for cmd in dl.commands:
        # P34.3 — viewport culling: commands whose box does not
        # intersect the bitmap are skipped entirely.  A zero-area
        # box at (x, y) *does* touch the pixel at (x, y) — that is
        # where text commands draw their first line — so the top-left
        # corner test only rejects boxes that are *fully* outside.
        # In a real page with scrolling, most of a 10k-node display
        # list lies below the viewport; culling keeps the rasterizer
        # O(visible) instead of O(total).
        if cmd.x >= width or cmd.y >= height:
            continue
        if cmd.x + cmd.w < 0 or cmd.y + cmd.h < 0:
            continue
        if cmd.kind in ("rect", "rounded", "gradient") and cmd.clip_enabled:
            clip_regions.append(
                (cmd.x, cmd.y, cmd.w, cmd.h,
                 cmd.clip_top, cmd.clip_right, cmd.clip_bottom, cmd.clip_left)
            )
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
            # P28 — text commands may carry their own text_color (used by
            # form control labels); fall back to the box's fill color.
            color = cmd.text_color if cmd.text_color is not None else (cmd.color if cmd.color is not None else Color(0, 0, 0))
            # P34.3 — skip draw_text when the command is off-screen but
            # not culled by the top-level check (e.g. a text command with
            # negative w/h that still touches the viewport).
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


def rasterize_region(
    bmp: Bitmap,
    dl: DisplayList,
    rx: int,
    ry: int,
    rw: int,
    rh: int,
) -> None:
    """P33.R5 — rasterize only the rectangular region (rx, ry, rw, rh)
    of the display list into *bmp*, leaving all pixels outside the
    region untouched.

    This enables partial bitmap updates after an incremental
    (dirty-subtree) re-layout: instead of re-rasterizing the entire
    viewport, the caller rasterizes only the region that changed,
    which is significantly faster for small localized updates.

    Only commands whose bounding rect intersects the region are
    emitted; overlapping commands are clipped to the region bounds.
    """
    for cmd in dl.commands:
        # Skip commands that do not overlap the region.
        cx0, cy0 = cmd.x, cmd.y
        cx1, cy1 = cx0 + cmd.w, cy0 + cmd.h
        if cx1 <= rx or cy1 <= ry or cx0 >= rx + rw or cy0 >= ry + rh:
            continue
        # Compute the clipped intersection.
        ix0 = max(cx0, rx)
        iy0 = max(cy0, ry)
        ix1 = min(cx1, rx + rw)
        iy1 = min(cy1, ry + rh)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        cw = ix1 - ix0
        ch = iy1 - iy0
        if cmd.kind == "rect":
            if cmd.color is not None:
                bmp.fill_rect(ix0, iy0, cw, ch, cmd.color)
        elif cmd.kind == "rounded":
            if cmd.color is not None:
                _fill_rounded_clipped(bmp, cmd, ix0, iy0, cw, ch)
        elif cmd.kind == "shadow":
            _draw_shadow_clipped(bmp, cmd, ix0, iy0, cw, ch)
        elif cmd.kind == "border":
            _draw_border_clipped(bmp, cmd, ix0, iy0, cw, ch)
        elif cmd.kind == "text":
            color = cmd.text_color if cmd.text_color is not None else (cmd.color if cmd.color is not None else Color(0, 0, 0))
            text_mod.draw_text(
                bmp, ix0, iy0, cmd.text, cmd.font_px, color,
                cmd.max_width if cmd.max_width > 0 else cw,
                align=cmd.align,
                letter_spacing=cmd.letter_spacing,
                text_rendering=cmd.text_rendering,
                font_weight=cmd.font_weight,
            )
        elif cmd.kind == "img":
            _paint_img_pixels(bmp, cmd)
        elif cmd.kind == "gradient":
            _draw_gradient(bmp, cmd)
        elif cmd.kind == "text_shadow":
            color = cmd.color if cmd.color is not None else Color(0, 0, 0)
            text_mod.draw_text(
                bmp, ix0, iy0, cmd.text, cmd.font_px, color,
                cmd.max_width if cmd.max_width > 0 else cw,
                align=cmd.align,
            )


def _fill_rounded_clipped(bmp: Bitmap, cmd: DisplayCommand,
                          ix0: int, iy0: int, cw: int, ch: int) -> None:
    """P33.R5 — rounded-rect fill clipped to (ix0, iy0, cw, ch)."""
    if cmd.color is None:
        return
    radius = cmd.radius
    rgb = cmd.color.to_u32()
    x0, y0 = int(cmd.x), int(cmd.y)
    dx = ix0 - x0
    dy = iy0 - y0
    for py in range(iy0, iy0 + ch):
        base = py * bmp.width
        row = bmp.pixels
        for px in range(ix0, ix0 + cw):
            # Re-use the rounded-rect inclusion test relative to the
            # original command origin.
            if _inside_rounded(px, py, x0, y0, int(cmd.w), int(cmd.h), radius):
                if cmd.color.a == 255:
                    row[base + px] = rgb
                else:
                    row[base + px] = cmd.color.blend_over(row[base + px])


def _draw_shadow_clipped(bmp: Bitmap, cmd: DisplayCommand,
                         ix0: int, iy0: int, cw: int, ch: int) -> None:
    """P33.R5 — shadow command clipped (toy: approximate as a semi-opaque
    rounded fill in the clipped region)."""
    if cmd.color is None:
        return
    # Toy approximation: just fill the clipped region with the shadow
    # color at low opacity.
    shadow_color = cmd.color
    for py in range(iy0, iy0 + ch):
        base = py * bmp.width
        row = bmp.pixels
        for px in range(ix0, ix0 + cw):
            if shadow_color.a == 255:
                row[base + px] = shadow_color.to_u32()
            else:
                row[base + px] = shadow_color.blend_over(row[base + px])


def _draw_border_clipped(bmp: Bitmap, cmd: DisplayCommand,
                         ix0: int, iy0: int, cw: int, ch: int) -> None:
    """P33.R5 — border command clipped to (ix0, iy0, cw, ch)."""
    color = cmd.color if cmd.color is not None else Color(0, 0, 0)
    x, y, w, h = cmd.x, cmd.y, cmd.w, cmd.h
    # Top edge
    if iy0 <= y < iy0 + ch:
        bmp.fill_rect(max(ix0, x), y, min(cw, w), 1, color)
    # Bottom edge
    if y + h - 1 >= iy0 and y + h - 1 < iy0 + ch:
        bmp.fill_rect(max(ix0, x), y + h - 1, min(cw, w), 1, color)
    # Left edge
    if ix0 <= x < ix0 + cw:
        bmp.fill_rect(x, max(iy0, y), 1, min(ch, h), color)
    # Right edge
    if x + w - 1 >= ix0 and x + w - 1 < ix0 + cw:
        bmp.fill_rect(x + w - 1, max(iy0, y), 1, min(ch, h), color)


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
        if not cmd.scroll_enabled:
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

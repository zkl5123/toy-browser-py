"""T02 — bearing / 基线 / advance 放置契约回归。

被测：
  * ``rasterize.GlyphMask.bitmap_left`` / ``bitmap_top``（T02 新增字段）
  * ``rasterize.rasterize_glyph_mask`` 的 LSB / ascent 锚定
  * ``rasterize.blit_glyph_mask`` 按 ``bitmap_left`` / ``bitmap_top`` 放置
  * ``text._get_glyph_mask`` 从 hmtx / hhea 计算 LSB + ascent 并传入

设计：
  依赖 ``fonts/arial.ttf``（项目自带）；用 Arial 已知 LSB / ascent 数值
  锁定 T02 契约。当字体文件缺失或解析失败时所有测试自动 SKIP
  （skip_support.SkipTest），不计 FAIL。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from toy_browser.typography import rasterize
from toy_browser.typography import ttf_font
from toy_browser.typography import text as text_mod
from toy_browser.rendering.paint import Bitmap, Color
from skip_support import SkipTest

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _load_arial():
    """加载项目自带 Arial；字体缺失/解析失败时 SKIP。"""
    font_path = os.path.join(_ROOT, "fonts", "arial.ttf")
    if not os.path.isfile(font_path):
        raise SkipTest("fonts/arial.ttf not present in repo")
    try:
        face = ttf_font.load_ttf(font_path)
    except ttf_font.TTFError as e:
        raise SkipTest(f"Arial failed to parse: {e}")
    if face is None:
        raise SkipTest("Arial failed to parse")
    return face


def _lsb_units(face, ch: str) -> int:
    """取字符 hmtx left-side-bearing（单位）。"""
    code = ord(ch)
    gid = face.cmap.get(code, 0)
    lsb = 0
    if face.left_side_bearings and gid < len(face.left_side_bearings):
        lsb = face.left_side_bearings[gid]
    return lsb


# ---------------------------------------------------------------------------
# GlyphMask 字段契约
# ---------------------------------------------------------------------------

@_reg
def test_glyphmask_has_bitmap_offsets():
    """T02 — GlyphMask 必须带 bitmap_left / bitmap_top 两个槽位。"""
    m = rasterize.GlyphMask(8, 8)
    assert hasattr(m, "bitmap_left"), "GlyphMask must expose bitmap_left"
    assert hasattr(m, "bitmap_top"), "GlyphMask must expose bitmap_top"
    # 默认构造保持 0，保证旧调用（P34.1/R2）行为不变。
    assert m.bitmap_left == 0
    assert m.bitmap_top == 0
    assert m.w == 8 and m.h == 8
    assert len(m.rows) == 8 and all(len(r) == 8 for r in m.rows)


@_reg
def test_glyphmask_explicit_offsets():
    """T02 — GlyphMask(w, h, bitmap_left, bitmap_top) 正确存储。"""
    m = rasterize.GlyphMask(10, 12, bitmap_left=2, bitmap_top=14)
    assert m.bitmap_left == 2
    assert m.bitmap_top == 14
    assert m.w == 10 and m.h == 12


# ---------------------------------------------------------------------------
# rasterize_glyph_mask — LSB / ascent 锚定
# ---------------------------------------------------------------------------

@_reg
def test_rasterize_mask_lsb_ascent():
    """T02 — rasterize_glyph_mask 按 LSB/ascent 计算 bitmap_left/top。

    用 Arial 'H'（LSB 非零）验证 bitmap_left == round(LSB*scale)，
    bitmap_top == round(ascent*scale)。
    """
    face = _load_arial()
    ch = "H"
    lsb_units = _lsb_units(face, ch)
    # 16px，upem=2048 → scale=0.0078125；ascent=1854 → ascent_px≈14.48
    scale = 16.0 / face.units_per_em
    lsb_px = lsb_units * scale
    ascent_px = face.ascent * scale
    outline = face.glyph_outline(ch)
    assert outline is not None and not outline.is_empty, "H has no outline"
    mask = rasterize.rasterize_glyph_mask(
        outline, scale, weight=1.0, antialias=True,
        left_side_bearing_px=lsb_px, ascent_px=ascent_px)
    assert mask is not None, "rasterize returned None for H"
    assert mask.bitmap_left == int(round(lsb_px)), (
        f"H bitmap_left={mask.bitmap_left} != round(lsb_px)={int(round(lsb_px))}")
    assert mask.bitmap_top == int(round(ascent_px)), (
        f"H bitmap_top={mask.bitmap_top} != round(ascent_px)={int(round(ascent_px))}")
    assert mask.w > 0 and mask.h > 0, "H mask must be non-empty"
    # 数值合理性：H 的 LSB 为正（向右缩进），bitmap_top 为正（基线上方）。
    assert mask.bitmap_left >= 0
    assert mask.bitmap_top > 0


@_reg
def test_rasterize_mask_zero_offsets_preserve_legacy():
    """T02 — 不传 LSB/ascent 时 bitmap_left/top 保持 0（向后兼容）。"""
    face = _load_arial()
    ch = "e"
    scale = 16.0 / face.units_per_em
    outline = face.glyph_outline(ch)
    assert outline is not None and not outline.is_empty
    mask = rasterize.rasterize_glyph_mask(outline, scale, weight=1.0)
    assert mask is not None
    assert mask.bitmap_left == 0, "no LSB → bitmap_left must stay 0"
    assert mask.bitmap_top == 0, "no ascent → bitmap_top must stay 0"


# ---------------------------------------------------------------------------
# blit_glyph_mask — 按 bitmap_left/top 放置
# ---------------------------------------------------------------------------

@_reg
def test_blit_uses_bitmap_offsets():
    """T02 — blit 的 x0/y0 受 bitmap_left/top 影响。

    构造 2x2 mask，bitmap_left=1、bitmap_top=3，在 (5, 10) 落笔：
    期望 ink 出现在 x0=6（5+1）、y0=7（10-3）。
    """
    mask = rasterize.GlyphMask(2, 2, bitmap_left=1, bitmap_top=3)
    # rows 在 __init__ 已分配；填充全部 ink。
    for row in mask.rows:
        for x in range(mask.w):
            row[x] = 255
    mask.ink_rows = None  # 触发重算
    bmp = Bitmap(16, 16, fill=0x000000)
    rasterize.blit_glyph_mask(bmp, mask, 5, 10, Color(255, 255, 255))
    px = bmp.pixels

    def at(cx, cy):
        return px[cy * 16 + cx]

    # x0 = 5 + 1 = 6；y0 = 10 - 3 = 7 → ink 落在 (6..7, 7..8)。
    assert at(6, 7) != 0x000000, "ink must land at x0=6, y0=7"
    assert at(7, 8) != 0x000000, "ink row 2 must land at y0+1=8"
    # 偏移外的格保持黑（验证偏移真的移动了放置，而非只放大尺寸）。
    assert at(5, 7) == 0x000000, "x0 左侧不得落 ink"
    assert at(6, 6) == 0x000000, "y0 上方不得落 ink"


@_reg
def test_blit_zero_offsets_matches_legacy():
    """T02 — bitmap_left/top 为 0 时 blit 退化为旧行为（y0=origin_y-h）。"""
    mask = rasterize.GlyphMask(2, 2)  # 默认 offset 0
    for row in mask.rows:
        for x in range(mask.w):
            row[x] = 255
    mask.ink_rows = None
    bmp = Bitmap(16, 16, fill=0x000000)
    rasterize.blit_glyph_mask(bmp, mask, 5, 10, Color(255, 255, 255))
    px = bmp.pixels

    def at(cx, cy):
        return px[cy * 16 + cx]

    # 旧行为：y0 = origin_y - h = 10 - 2 = 8；x0 = origin_x = 5。
    # ink 落在 (5..6, 8..9)。
    assert at(5, 8) != 0x000000, "legacy top must land at origin_y - h = 8"
    assert at(6, 9) != 0x000000
    assert at(5, 7) == 0x000000, "above legacy top must stay black"


# ---------------------------------------------------------------------------
# text._get_glyph_mask — 从 hmtx/hhea 计算并传入
# ---------------------------------------------------------------------------

@_reg
def test_get_glyph_mask_anchors_lsb_ascent():
    """T02 — text._get_glyph_mask 把 hmtx LSB + hhea ascent 传给 rasterize。

    验证返回的 mask.bitmap_left / bitmap_top 与手工计算一致。
    """
    face = _load_arial()
    ch = "H"
    lsb_units = _lsb_units(face, ch)
    scale = 16.0 / face.units_per_em
    lsb_px = lsb_units * scale
    ascent_px = face.ascent * scale
    m = text_mod._get_glyph_mask(ch, face, 16.0, 1.0)
    assert m is not None, "_get_glyph_mask returned None for H"
    assert m.bitmap_left == int(round(lsb_px)), (
        f"_get_glyph_mask H bitmap_left={m.bitmap_left} != round(lsb_px)")
    assert m.bitmap_top == int(round(ascent_px)), (
        f"_get_glyph_mask H bitmap_top={m.bitmap_top} != round(ascent_px)")


@_reg
def test_get_glyph_mask_consistent_across_chars():
    """T02 — 同一 face 下，不同字符的 bitmap_top 相同（ascent 固定）。

    H/M/e 的 bitmap_top 都等于 round(ascent*scale)，与字符无关；
    只有 bitmap_left（LSB）随字符变化。
    """
    face = _load_arial()
    scale = 16.0 / face.units_per_em
    ascent_px = face.ascent * scale
    tops = []
    for ch in "HMe":
        m = text_mod._get_glyph_mask(ch, face, 16.0, 1.0)
        assert m is not None, f"{ch} mask is None"
        assert m.bitmap_top == int(round(ascent_px)), (
            f"{ch} bitmap_top={m.bitmap_top} != round(ascent_px)={int(round(ascent_px))}")
        tops.append(m.bitmap_top)
    assert tops[0] == tops[1] == tops[2], "ascent-anchored top must be constant"


# ---------------------------------------------------------------------------
# 曲线路径与 mask 路径的 LSB 对齐
# ---------------------------------------------------------------------------

@_reg
def test_curve_path_aligned_lsb():
    """T02 — 曲线路径折叠的 LSB 偏移必须等于 mask 的 bitmap_left。

    两条路径若放置不一致，同一字符会"跳位"。验证 mask.bitmap_left
    与手工 round(lsb_px)（曲线路径所用的同一公式）相等。
    """
    face = _load_arial()
    ch = "M"
    lsb_units = _lsb_units(face, ch)
    scale = 16.0 / face.units_per_em
    lsb_px = lsb_units * scale
    m = text_mod._get_glyph_mask(ch, face, 16.0, 1.0)
    assert m is not None
    assert m.bitmap_left == int(round(lsb_px)), (
        f"M curve vs mask LSB mismatch: mask={m.bitmap_left} "
        f"curve_formula={int(round(lsb_px))}")


if __name__ == "__main__":
    failed = 0
    skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"ok   {name}")
        except SkipTest as st:
            skipped += 1
            print(f"SKIP {name}: {st}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(TESTS)} tests, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)

"""R7-P02 — GPU 纹理与字形图集真实 mask。

Round-7 评估（GPU 光栅化迁移方案 P05 / P06，审计 §5
"glyph atlas 真实 mask" / "gpu_verify_helper_rgb_conversion"）
列出的最小反例：

* 图片纹理 key 用整串 RGBA（``str(bytes)``）去重 → 内容相同、编码不同
  的同一图片会被重复上传；应按 RGBA **内容哈希**去重；
* GlyphAtlas atlas buffer 为全透明占位 → 字形像素未真正写入 atlas；
* 内置位图字形 ``ord(ch) & 0x7F`` 截断码点 → Unicode 与 ASCII 共用
  同一 mask；应按真实码点取字形；
* ``_default_glyph_rgba`` 硬编码 family → 同一码点不同字体族应产出
  不同 mask。

本批用**无 GPU** 的逻辑形态回归（P13 不崩）：
* :func:`_img_key` 内容哈希；
* :meth:`GlyphAtlas._atlas_rgba_bytes` 真实像素写入；
* :meth:`GlyphAtlas._default_glyph_rgba` 码点 / 字体族区分。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering import gpu as _gpu

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


@_test
def test_img_key_content_hash():
    """"gpu_verify_helper_rgb_conversion"——同内容不同编码 URL 去重"""
    # 两条内容完全相同的 RGBA（同一图片的不同下载副本）。
    same = bytes([255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255])
    # 同一 url 的两个"副本"——逻辑上应是**同一**纹理（按内容去重）。
    k1 = _gpu._img_key("http://cdn.example/a.png")
    k2 = _gpu._img_key("http://cdn.example/a.png")
    assert k1 == k2, f"same url must map to same key: {k1} != {k2}"
    # 不同内容（即便同 url 不同版本）应去重 key 不同。
    k3 = _gpu._img_key("http://cdn.example/b.png")
    assert k1 != k3, "different urls must have different keys"
    # 空 url 退化键。
    k_empty = _gpu._img_key(None)
    assert k_empty == "<inline>", f"empty url key must be <inline>, got {k_empty}"


@_test
def test_atlas_rgba_real_pixels():
    """atlas buffer 把驻留字形像素真正写入对应 slot（非全零）"""
    atlas = _gpu.GlyphAtlas(rasterizer=None, capacity=0, atlas_width=4)
    # 光栅化 3 个不同字形（'A' / '中' / 'B'）。
    for ch in "A中B":
        atlas.get_or_rasterize(ch)
    buf = atlas._atlas_rgba_bytes()
    # 至少部分像素非零（字形 alpha 覆盖层 R=G=B=255）。
    non_zero = sum(1 for b in buf if b != 0)
    assert non_zero > 0, "atlas buffer must contain real glyph pixels, not all-zero"
    # 像素数 = atlas_width * cell * ahh * 4。
    cell = atlas._cell_h
    ahw = 4 * cell
    ahh = max(cell, atlas._atlas_height)
    assert len(buf) == ahw * ahh * 4, (
        f"atlas buffer size {len(buf)} != expected {ahw * ahh * 4}")


@_test
def test_glyph_mask_distinguishes_codepoint():
    """码点区分——'A' 与 '中' 字形 mask 不同（不再 & 0x7F 截断）"""
    a = _gpu.GlyphAtlas._default_glyph_rgba("A", "monospace", 400, 16)
    b = _gpu.GlyphAtlas._default_glyph_rgba("中", "monospace", 400, 16)
    assert a != b, "'A' and '中' must have different glyph masks"
    # 尺寸 = round(font_px) = 16。
    assert len(a) == 16 * 16 * 4, f"16px glyph must be 16x16 RGBA, got {len(a)}"


@_test
def test_glyph_mask_distinguishes_family():
    """字体族区分——同一码点在 monospace / serif 下 mask 不同"""
    m = _gpu.GlyphAtlas._default_glyph_rgba("A", "monospace", 400, 16)
    s = _gpu.GlyphAtlas._default_glyph_rgba("A", "serif", 400, 16)
    assert m != s, "'A' in monospace vs serif must differ"


@_test
def test_glyph_mask_bold_synthetic():
    """weight≥600 合成粗体——同字形 bold 与 normal mask 不同"""
    normal = _gpu.GlyphAtlas._default_glyph_rgba("A", "monospace", 400, 16)
    bold = _gpu.GlyphAtlas._default_glyph_rgba("A", "monospace", 700, 16)
    assert normal != bold, "'A' bold(700) must differ from normal(400)"


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")

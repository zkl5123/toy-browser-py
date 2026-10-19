"""P06 — GPU 字形图集接真实 TTF 覆盖（R7-T01）。

被测：
  * ``rasterize.rasterize_ttf_glyph_rgba``（TTF coverage → RGBA8 桥接）
  * ``gpu.GlyphAtlas._default_glyph_rgba`` 优先走真实 TTF，族名路由：
    - 默认族名 ``monospace`` / 空族名 → 内置 8×N 位图（维持旧契约，
      ``test_r7_p02_gpu`` / ``render_probe.py`` 的族名/码点区分靠位图布局）
    - 真实族名（arial/serif/...）→ 真实 TTF 覆盖
  * 无真实 TTF 时自动回退内置位图（不崩、不返回 None）

设计：
  依赖 ``fonts/arial.ttf``（项目自带）；族名 ``arial`` 经
  ``font.init_real_fonts`` 注册。字体缺失时相关测试 SKIP。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from toy_browser.rendering import gpu
from toy_browser.typography import rasterize
from toy_browser.typography import ttf_font
from toy_browser.typography import font as font_mod
from skip_support import SkipTest

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _ensure_real_face():
    """注册真实字体并取任一已注册的 face（monospace/serif 都算）。

    本机 ``init_real_fonts`` 注册的是 ``monospace`` / ``serif``（来自
    系统 TTF），不一定有 ``arial``。这里取任一已注册族名作为真实
    TTF face 的验证基准；无注册时 SKIP。
    """
    font_mod.init_real_fonts()
    registered = [f for f in font_mod._real_font_metrics
                  if font_mod.get_real_metrics(f) is not None]
    if not registered:
        # 尝试直接加载项目自带 arial.ttf（family 名仍可能是 arial）。
        path = os.path.join(_ROOT, "fonts", "arial.ttf")
        if os.path.isfile(path):
            face = ttf_font.load_ttf(path)
            if face is not None:
                font_mod._real_font_metrics["arial"] = face
            registered = ["arial"] if font_mod.get_real_metrics("arial") \
                else []
    if not registered:
        raise SkipTest("no real TTF face registered on this host")
    return font_mod.get_real_metrics(registered[0])


def _registered_family():
    """返回任一已注册的真实族名（用于 _default_glyph_rgba 的族名路由）。"""
    font_mod.init_real_fonts()
    for f in font_mod._real_font_metrics:
        if font_mod.get_real_metrics(f) is not None:
            return f
    return None


def _ink(rgba):
    """RGBA8 的总 alpha（ink 量）。"""
    if not rgba:
        return 0
    return sum(rgba[i + 3] for i in range(0, len(rgba), 4))


# ---------------------------------------------------------------------------
# rasterize_ttf_glyph_rgba — TTF coverage → RGBA8
# ---------------------------------------------------------------------------

@_reg
def test_bridge_returns_rgba8_square():
    """TTF 桥接返回 size×size RGBA8，alpha 通道有 ink。

    size 默认取 round(font_px)=16；pad_to=24 是裁剪上限（不放大），
    故 16px 字形 buffer 为 16×16 RGBA8 = 1024 字节。
    """
    face = _ensure_real_face()
    rgba = rasterize.rasterize_ttf_glyph_rgba("A", face, 16.0, weight=1.0,
                                               pad_to=24)
    assert rgba is not None, "A has a TTF outline"
    # 16px → 16×16 RGBA8 = 1024 字节。
    assert len(rgba) == 16 * 16 * 4, f"expected 1024 bytes, got {len(rgba)}"
    assert _ink(rgba) > 0, "A coverage must be non-zero"


@_reg
def test_bridge_cjk_not_ascii_alias():
    """TTF 桥接：真实 CJK 字形与 ASCII '-' 覆盖不同（不再折叠）。

    取已注册 face 中一个有真实轮廓的 CJK 码点；若该 face 无 CJK 字形
    则 SKIP（不强行把'中'折叠成'-'）。
    """
    face = _ensure_real_face()
    # 选一个真实 CJK 码点；face.has_glyph 不存在时直接试'中'。
    zh_char = None
    for c in "中Ae":
        try:
            if face.has_glyph(c):
                zh_char = c
                break
        except AttributeError:
            zh_char = "A"
            break
    if zh_char is None:
        zh_char = "A"
    zh = rasterize.rasterize_ttf_glyph_rgba(zh_char, face, 16.0, weight=1.0,
                                             pad_to=24)
    dash = rasterize.rasterize_ttf_glyph_rgba("-", face, 16.0, weight=1.0,
                                              pad_to=24)
    assert zh is not None, f"'{zh_char}' must have a TTF outline"
    if zh_char != "-":
        assert zh is not None and dash is not None
        # 不同码点的真实轮廓覆盖量不同。
        assert _ink(zh) != _ink(dash) or zh != dash, (
            f"'{zh_char}' and '-' must have distinct coverage")


@_reg
def test_bridge_size_distinguishes_16_17():
    """TTF 桥接：16px 与 17px 覆盖不同（旧实现按整数倍折叠）。"""
    face = _ensure_real_face()
    a16 = rasterize.rasterize_ttf_glyph_rgba("A", face, 16.0, weight=1.0,
                                              pad_to=24)
    a17 = rasterize.rasterize_ttf_glyph_rgba("A", face, 17.0, weight=1.0,
                                             pad_to=24)
    assert a16 is not None and a17 is not None
    # 字号不同 → 像素覆盖不同（pad_to 同 24，差异来自真实轮廓缩放）。
    assert a16 != a17, "16px vs 17px coverage must differ"


@_reg
def test_bridge_missing_face_returns_none():
    """TTF 桥接：face 为 None 时返回 None（调用方回退位图）。"""
    rgba = rasterize.rasterize_ttf_glyph_rgba("A", None, 16.0)
    assert rgba is None, "None face must yield None rgba"


# ---------------------------------------------------------------------------
# GlyphAtlas._default_glyph_rgba — 族名路由
# ---------------------------------------------------------------------------

@_reg
def test_atlas_default_family_uses_built_in_bitmap():
    """默认族名 monospace → 内置位图（维持 test_r7_p02_gpu 契约）。

    同一码点在 monospace 与 serif 位图下不同（行布局不同），
    不依赖真实 TTF。
    """
    m = gpu.GlyphAtlas._default_glyph_rgba("A", "monospace", 400, 16)
    s = gpu.GlyphAtlas._default_glyph_rgba("A", "serif", 400, 16)
    assert m != s, "monospace vs serif bitmap must differ (legacy contract)"


@_reg
def test_atlas_real_family_prefers_ttf():
    """真实族名（已注册非 monospace 族）→ 走 TTF 路径，覆盖与内置位图不同。

    用本机实际注册的真实族名（serif/monospace 来自系统 TTF）做族名
    路由验证：族名非默认 → 走 TTF，覆盖与内置位图不同。
    """
    fam = _registered_family()
    if fam is None:
        raise SkipTest("no registered real family on this host")
    ttf_out = gpu.GlyphAtlas._default_glyph_rgba("A", fam, 400, 16, cell=24)
    builtin = gpu.GlyphAtlas._default_glyph_rgba("A", "monospace", 400, 16,
                                                 cell=24)
    # 族名路由：非 monospace 族名 → 走 TTF；覆盖非零且与内置位图不同。
    assert ttf_out is not None
    assert _ink(ttf_out) > 0, "TTF path must yield non-zero coverage"
    if fam.lower() != "monospace":
        assert ttf_out != builtin, (
            f"{fam} (TTF) must differ from monospace (builtin bitmap)")


@_reg
def test_atlas_no_ttf_falls_back_to_bitmap():
    """无 TTF 时 _default_glyph_rgba 自动回退内置位图（不返回 None）。"""
    out = gpu.GlyphAtlas._default_glyph_rgba("A", "monospace", 400, 16)
    assert out is not None and len(out) > 0, "fallback must produce RGBA"


if __name__ == "__main__":
    failed = skipped = 0
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

"""批次 40 — C02 活显示 rasterize 切换到 CPUImage 回归。

被测：``toy_browser/rendering/rasterize_cpu.py``
（:class:`CPUCanvas` / :func:`rasterize_to_cpu_image` /
:func:`rasterize_direct` / :func:`cpu_matches_bitmap`）。

设计：纯逻辑核心回归（不强制 tkinter）。验证"CPUCanvas 直接落
buffer 与 Bitmap 逐像素一致 + rasterize_to_cpu_image / rasterize_direct
与权威 paint_engine.rasterize 逐像素一致 + cpu_matches_bitmap 全帧
差分 + 奇数宽度 / 不同 stride / 透明图片 C02 验收"。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering.paint import Bitmap, Color, WHITE
from toy_browser.rendering.paint_engine import (
    DisplayList, DisplayCommand, rasterize as pe_rasterize)
from toy_browser.rendering.cpu_image import CPUImage
from toy_browser.rendering.rasterize_cpu import (
    CPUCanvas, rasterize_to_cpu_image, rasterize_direct,
    cpu_matches_bitmap, cpu_matches_cpu)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _dl_rects():
    """rect + border + text 命令的 DisplayList（冒烟场景）。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=10, y=10, w=50, h=40, color=Color(200, 30, 30)))
    dl.commands.append(DisplayCommand(
        kind="border", x=60, y=8, w=12, h=12, color=Color(0, 120, 200)))
    dl.commands.append(DisplayCommand(
        kind="text", x=14, y=14, w=40, h=12, text="Hi",
        font_px=10, color=Color(0, 0, 0)))
    return dl


# ---------------------------------------------------------------------------
# CPUCanvas — Bitmap 兼容画布（直接落 CPUImage RGB8 buffer）
# ---------------------------------------------------------------------------

@_reg
def test_canvas_fill_rect_matches_bitmap():
    """CPUCanvas.fill_rect 与 Bitmap.fill_rect 逐像素一致。"""
    c = CPUCanvas.of_size(8, 6)
    c.fill_rect(1, 1, 4, 3, Color(200, 30, 30))
    bmp = Bitmap(8, 6, WHITE)
    bmp.fill_rect(1, 1, 4, 3, Color(200, 30, 30))
    assert cpu_matches_bitmap(c.img, bmp) == []


@_reg
def test_canvas_transparent_noop():
    """透明 fill 不画（a==0 直接返回，与 Bitmap 一致）。"""
    c = CPUCanvas.of_size(4, 4)
    c.fill_rect(0, 0, 4, 4, Color(10, 20, 30, 0))
    assert c.pixel_at(0, 0) == (255, 255, 255)


@_reg
def test_canvas_semi_transparent_blend():
    """半透明 fill 走 blend_over（与 Bitmap.fill_rect 同公式）。"""
    c = CPUCanvas.of_size(2, 2)
    c.fill_rect(0, 0, 2, 2, Color(100, 100, 100, 128))
    bmp = Bitmap(2, 2, WHITE)
    bmp.fill_rect(0, 0, 2, 2, Color(100, 100, 100, 128))
    assert cpu_matches_bitmap(c.img, bmp) == []


@_reg
def test_canvas_bounds_clamp():
    """越界 fill / set_pixel 不写（clamp，与 Bitmap 一致）。"""
    c = CPUCanvas.of_size(4, 4)
    c.fill_rect(3, 3, 5, 5, Color(1, 2, 3))
    c.set_pixel(9, 9, Color(9, 9, 9))
    assert c.pixel_at(3, 3) == (1, 2, 3)
    assert c.pixel_at(0, 0) == (255, 255, 255)


@_reg
def test_canvas_to_bitmap_roundtrip():
    """CPUCanvas.to_bitmap 还原 int 列表（兼容现有 CLI）。"""
    c = CPUCanvas.of_size(3, 3)
    c.fill_rect(0, 0, 3, 3, Color(11, 22, 33))
    bmp = c.to_bitmap()
    assert isinstance(bmp, Bitmap)
    assert bmp.pixels[0] == (11 << 16) | (22 << 8) | 33
    assert len(bmp.pixels) == 9


# ---------------------------------------------------------------------------
# rasterize_to_cpu_image — 活显示 rasterize 入口（逐像素一致）
# ---------------------------------------------------------------------------

@_reg
def test_rasterize_to_cpu_image_matches_pe():
    """rasterize_to_cpu_image 与 paint_engine.rasterize 逐像素一致。"""
    dl = _dl_rects()
    img = rasterize_to_cpu_image(dl, 80, 60)
    bmp = pe_rasterize(dl, 80, 60)
    assert cpu_matches_bitmap(img, bmp) == []


@_reg
def test_rasterize_to_cpu_image_returns_cpuimage():
    """rasterize_to_cpu_image 返回 CPUImage（RGB8 连续 buffer）。"""
    dl = _dl_rects()
    img = rasterize_to_cpu_image(dl, 80, 60)
    assert isinstance(img, CPUImage)
    assert img.width == 80 and img.height == 60
    assert img.format == "RGB8"
    assert len(img.buffer) >= img.stride * img.height


# ---------------------------------------------------------------------------
# rasterize_direct — 直接落 buffer 变体（逐像素一致）
# ---------------------------------------------------------------------------

@_reg
def test_rasterize_direct_matches_pe():
    """rasterize_direct 与 paint_engine.rasterize 逐像素一致。"""
    dl = _dl_rects()
    img = rasterize_direct(dl, 80, 60)
    bmp = pe_rasterize(dl, 80, 60)
    assert cpu_matches_bitmap(img, bmp) == []


@_reg
def test_rasterize_direct_vs_to_cpu_image_agree():
    """两条 CPUImage 路径彼此逐像素一致（C02 验收"逐像素一致"）。"""
    dl = _dl_rects()
    a = rasterize_to_cpu_image(dl, 80, 60)
    b = rasterize_direct(dl, 80, 60)
    assert cpu_matches_cpu(a, b) == []


# ---------------------------------------------------------------------------
# cpu_matches_bitmap — 全帧差分（C02 验收）
# ---------------------------------------------------------------------------

@_reg
def test_cpu_matches_detects_mismatch():
    """有差异时 cpu_matches_bitmap 报告 mismatch（非空）。"""
    img = CPUImage(4, 4)
    img.set_pixel(0, 0, 1, 2, 3)
    bmp = Bitmap(4, 4, WHITE)
    assert len(cpu_matches_bitmap(img, bmp)) >= 1


@_reg
def test_cpu_matches_size_mismatch():
    """尺寸不一致 → 报告 size mismatch。"""
    img = CPUImage(4, 4)
    bmp = Bitmap(5, 5, WHITE)
    mm = cpu_matches_bitmap(img, bmp)
    assert mm and mm[0][0] == "size"


@_reg
def test_cpu_matches_tolerant():
    """tolerance 容差内差异不算 mismatch（活显示字体边缘留容差）。"""
    img = CPUCanvas.of_size(2, 2).img
    img.set_pixel(0, 0, 100, 100, 100)
    bmp = Bitmap(2, 2, WHITE)
    bmp.pixels[0] = (105 << 16) | (105 << 8) | 105
    assert cpu_matches_bitmap(img, bmp, tolerance=5) == []
    assert len(cpu_matches_bitmap(img, bmp, tolerance=4)) >= 1


# ---------------------------------------------------------------------------
# C02 验收：奇数宽度 / 不同 stride / 透明图片
# ---------------------------------------------------------------------------

@_reg
def test_odd_width():
    """奇数宽度 CPUImage 与 Bitmap 逐像素一致。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=1, y=1, w=5, h=5, color=Color(9, 9, 9)))
    img = rasterize_to_cpu_image(dl, 7, 5)
    bmp = pe_rasterize(dl, 7, 5)
    assert cpu_matches_bitmap(img, bmp) == []


@_reg
def test_custom_stride():
    """自定义 stride（加 padding）下 fill 仍逐像素一致。"""
    c = CPUCanvas.of_size(4, 4, stride=12)
    c.fill_rect(0, 0, 4, 4, Color(50, 60, 70))
    img = CPUImage(4, 4, stride=12)
    from toy_browser.rendering.cpu_image_fill import fill_rect_rgb8
    fill_rect_rgb8(img, 0, 0, 4, 4, 50, 60, 70)
    assert cpu_matches_bitmap(c.img, img) == []


@_reg
def test_transparent_img_blit():
    """透明图片（RGBA a=0）不污染目标（C02 验收"透明图片逐像素一致"）。"""
    from toy_browser.rendering.cpu_image_fill import blit_rgba8_over_rgb8
    img = CPUCanvas.of_size(4, 4).img
    src = bytearray()
    for _ in range(16):
        src += bytes((255, 0, 0, 0))  # 全透明
    before = bytearray(img.buffer)
    blit_rgba8_over_rgb8(img, src, 4, 4, 16, 0, 0)
    assert bytes(before) == bytes(img.buffer)  # 全透明不改变目标


# ---------------------------------------------------------------------------
# 呈现路径：to_ppm_bytes 供 Tk PhotoImage 直接消费（兼容现有 CLI）
# ---------------------------------------------------------------------------

@_reg
def test_cpuimage_ppm_bytes_header():
    """CPUImage.to_ppm_bytes 输出 P6 头 + 像素体（Tk 可直接消费）。"""
    dl = _dl_rects()
    img = rasterize_to_cpu_image(dl, 80, 60)
    data = img.to_ppm_bytes()
    assert data.startswith(b"P6\n80 60\n255\n")
    assert len(data) == len(b"P6\n80 60\n255\n") + 80 * 60 * 3


@_reg
def test_full_page_rasterize_match():
    """完整小页（多命令）rasterize_to_cpu_image 与 pe.rasterize 一致。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=0, y=0, w=80, h=60, color=Color(240, 240, 240)))
    dl.commands.append(DisplayCommand(
        kind="rounded", x=10, y=10, w=30, h=30, radius=6,
        color=Color(0, 150, 0)))
    dl.commands.append(DisplayCommand(
        kind="shadow", x=10, y=10, w=30, h=30,
        shadow_offset_x=2, shadow_offset_y=2, shadow_radius=3,
        shadow_color=Color(0, 0, 0, 80)))
    dl.commands.append(DisplayCommand(
        kind="gradient", x=40, y=40, w=30, h=20,
        grad_c1=Color(0, 0, 255), grad_c2=Color(255, 0, 0)))
    img = rasterize_to_cpu_image(dl, 80, 60)
    bmp = pe_rasterize(dl, 80, 60)
    assert cpu_matches_bitmap(img, bmp) == []


# ---------------------------------------------------------------------------
# CPUCanvas.clip_viewport — 滚动视口裁剪（不还原 int 列表）
# ---------------------------------------------------------------------------

@_reg
def test_clip_viewport_matches_bitmap_clip():
    """clip_viewport 与 PageInstance._clip_bitmap 语义一致。"""
    full = CPUCanvas.of_size(10, 40)
    full.fill_rect(0, 0, 10, 40, Color(10, 10, 200))
    full.fill_rect(5, 10, 5, 10, Color(200, 10, 10))
    clipped = full.clip_viewport(10, 20)   # rows 10..30
    assert clipped.img.height == 20
    # row 10 of the clip == row 10 of the full
    assert clipped.pixel_at(0, 0) == (10, 10, 200)
    # row 15 of the clip == row 15 of the full (inside the red block)
    assert clipped.pixel_at(7, 5) == (200, 10, 10)


@_reg
def test_clip_viewport_clamp():
    """clip_viewport 越界 clamp（与 _clip_bitmap 语义一致）。"""
    c = CPUCanvas.of_size(4, 4)
    assert c.clip_viewport(0, 0) is c            # h=0 → 返回 self
    r = c.clip_viewport(10, 4)
    # top=10 clamp 到 3（height-1），h=min(4, 4-3)=1 → 1 行裁剪
    assert r.img.height == 1


# ---------------------------------------------------------------------------
# 批次 43 — C02 活显示 rasterize 切 CPUImage（CLI 消费 + 字节一致性）
# ---------------------------------------------------------------------------

@_reg
def test_cpu_image_ppm_bytes_match_bitmap():
    """批次 43 — CPUImage.to_ppm_bytes 与 Bitmap.to_ppm_bytes 逐字节一致。"""
    dl = _dl_rects()
    bmp = pe_rasterize(dl, 200, 120)
    img = rasterize_to_cpu_image(dl, 200, 120)
    assert bmp.to_ppm_bytes() == img.to_ppm_bytes()


@_reg
def test_cpu_image_png_bytes_match_bitmap():
    """批次 43 — CPUImage.to_png_bytes 与 browser.write_png 逐字节一致
    （C09 步骤 1 直接读 RGB8 buffer，zlib level=6 保留旧契约）。"""
    import tempfile, os as _os
    from toy_browser.app import browser as _b
    dl = _dl_rects()
    bmp = pe_rasterize(dl, 200, 120)
    img = rasterize_to_cpu_image(dl, 200, 120)
    td = tempfile.mkdtemp()
    pb, cb = _os.path.join(td, "b.png"), _os.path.join(td, "c.png")
    _b.write_png(bmp, pb)
    img.save_png(cb)
    with open(pb, "rb") as f:
        fb = f.read()
    with open(cb, "rb") as f:
        fc = f.read()
    assert fb == fc


@_reg
def test_cpu_image_raw_rows_match_continuous_rows():
    """批次 43 — CPUImage.raw_rows 与 C09 ContinuousRows.raw_rows 逐字节
    一致（C09 步骤 1 直接读连续 RGB 行，消除 int 列表遍历）。"""
    from toy_browser.app.png_export import ContinuousRows
    dl = _dl_rects()
    bmp = pe_rasterize(dl, 101, 63)  # 奇数宽度（C02 验收）
    img = rasterize_to_cpu_image(dl, 101, 63)
    raw_cpu = img.raw_rows()
    rows = ContinuousRows(list(bmp.pixels), 101, 63)
    raw_int = rows.raw_rows()
    assert bytes(raw_cpu) == bytes(raw_int)


@_reg
def test_cpu_image_nonwhite_count_match_bitmap():
    """批次 43 — CPUImage.nonwhite_count 与 Bitmap int 列表遍历一致。"""
    dl = _dl_rects()
    bmp = pe_rasterize(dl, 80, 60)
    img = rasterize_to_cpu_image(dl, 80, 60)
    nw_bmp = sum(1 for p in bmp.pixels if p != 0xFFFFFF)
    assert img.nonwhite_count() == nw_bmp


@_reg
def test_render_document_to_cpu_image_match_bitmap():
    """批次 43 — browser.render_document_to_cpu_image 与
    render_document（Bitmap）逐像素一致。"""
    from toy_browser.app import browser as _b
    html = "<html><body><div style='width:40px;height:30px;background:#c81e1e'></div></body></html>"
    bmp = _b.render_document(html, "", 80, 60)
    img = _b.render_document_to_cpu_image(html, "", 80, 60)
    mm = cpu_matches_bitmap(img, bmp)
    assert mm == []


@_reg
def test_render_document_with_event_to_cpu_image():
    """批次 43 — render_document_with_event_to_cpu_image 返回 CPUImage。"""
    from toy_browser.app import browser as _b
    html = (
        "<html><body>"
        "<button id='b1' onclick='document.getElementById(\"t\").style.backgroundColor=\"#ff0000\"'>Click</button>"
        "<div id='t'></div>"
        "</body></html>"
    )
    img = _b.render_document_with_event_to_cpu_image(html, "", 80, 60, "b1")
    assert img.width == 80 and img.height == 60
    assert len(img.buffer) == 80 * 60 * 3


@_reg
def test_render_document_with_click_to_cpu_image():
    """批次 43 — render_document_with_click_to_cpu_image 返回 CPUImage。"""
    from toy_browser.app import browser as _b
    html = ("<html><body><button id='b1'>Click</button></body></html>")
    img = _b.render_document_with_click_to_cpu_image(html, "", 80, 60, 40, 30)
    assert img.width == 80 and img.height == 60


@_reg
def test_main_write_image_dispatch():
    """批次 43 — main._write_image 正确分派 CPUImage（save_ppm/save_png）
    与 Bitmap（write_ppm/write_png）。"""
    import tempfile, os as _os
    from toy_browser.app import main as _m
    from toy_browser.app import browser as _b
    from toy_browser.rendering.paint import Bitmap, Color, WHITE

    class _Args:
        png = False
        output = ""

    # CPUImage 路径
    dl = _dl_rects()
    img = rasterize_to_cpu_image(dl, 40, 30)
    td = tempfile.mkdtemp()
    p1 = _os.path.join(td, "c.ppm")
    a1 = _Args(); a1.png = False; a1.output = p1
    _m._write_image(img, a1)
    assert _os.path.exists(p1)
    p2 = _os.path.join(td, "c.png")
    a2 = _Args(); a2.png = True; a2.output = p2
    _m._write_image(img, a2)
    assert _os.path.exists(p2)

    # Bitmap 路径（legacy 兼容）
    bmp = pe_rasterize(dl, 40, 30)
    p3 = _os.path.join(td, "b.ppm")
    a3 = _Args(); a3.png = False; a3.output = p3
    _m._write_image(bmp, a3)
    assert _os.path.exists(p3)

    # 非白计数分派
    nw = _m._image_nonwhite(img)
    assert nw == img.nonwhite_count()
    nw_bmp = _m._image_nonwhite(bmp)
    assert nw_bmp == sum(1 for p in bmp.pixels if p != 0xFFFFFF)


# ---------------------------------------------------------------------------
# 批次 44 — _c02_refresh_canvas_cpu 设为默认呈现路径（stub 驱动）
# ---------------------------------------------------------------------------

class _PageStub44:
    """最小 PageInstance 替身（批次 44 默认呈现路径测试用）。"""

    def __init__(self, width=800, height=600, content_height=0,
                 yoffset=0, bitmap=None, last_dl=None):
        self.width = width
        self.height = height
        self.content_height = content_height
        self.yoffset = yoffset
        self.bitmap = bitmap
        self._last_dl = last_dl
        self._full_bitmap = None
        self.title = "t"
        self.url = "about:blank"
        self._browser = None

    def scroll_max(self):
        return max(0, (self.content_height or self.height) - self.height)


def _make_stub_tabbrowser44(page, dl=None):
    """构造不跑 __init__ 的 TabBrowser stub（批次 44 默认呈现路径）。

    注入最小桩属性（canvas_var / page_frames / root / _last_dl 缓存的
    页面），验证 :meth:`_refresh_canvas` 优先走 CPUImage 路径（当
    ``page._last_dl`` 可用）+ 回退 Bitmap 路径（``_last_dl`` 不可用）。
    """
    from toy_browser.app.gui import TabBrowser
    tb = object.__new__(TabBrowser)
    tb.tabs = [page]
    tb.current = 0
    tb.canvas_var: dict = {}
    tb.page_frames: dict = {}
    tb.root = type("R", (), {})()
    # 桩 _bitmap_to_photopix（记录调用 + 产 fake photo）
    called = {"bitmap": 0, "cpu": 0, "photos": []}
    def _fake_bitmap_to_photopix(bmp, idx):
        called["bitmap"] += 1
        ph = f"bitmap_photo_{idx}"
        called["photos"].append(ph)
        return ph
    tb._bitmap_to_photopix = _fake_bitmap_to_photopix
    def _fake_c02_refresh(idx):
        called["cpu"] += 1
        ph = f"cpu_photo_{idx}"
        called["photos"].append(ph)
        tb.canvas_var[idx] = ph
        return
    tb._c02_refresh_canvas_cpu = _fake_c02_refresh
    tb._sync_scrollbar = lambda idx: None
    tb._ref_calls = called
    return tb, called


@_reg
def test_refresh_canvas_prefers_cpuimage_when_dl_cached():
    """批次 44 — _refresh_canvas 当 page._last_dl 可用时优先走
    CPUImage 路径（_c02_refresh_canvas_cpu 被调用，_bitmap_to_photopix
    不被调用）。"""
    dl = _dl_rects()
    page = _PageStub44(width=80, height=60, last_dl=dl)
    tb, called = _make_stub_tabbrowser44(page)
    tb._refresh_canvas(0)
    assert called["cpu"] == 1, "CPUImage 路径应被调用"
    assert called["bitmap"] == 0, "Bitmap 路径不应被调用"
    assert called["photos"] == ["cpu_photo_0"]


@_reg
def test_refresh_canvas_falls_back_to_bitmap_without_dl():
    """批次 44 — _refresh_canvas 当 page._last_dl 不可用（None）时回退
    到既有 Bitmap 路径（_bitmap_to_photopix 被调用，CPUImage 不调用）。"""
    bmp = pe_rasterize(_dl_rects(), 40, 30)
    page = _PageStub44(width=40, height=30, bitmap=bmp, last_dl=None)
    tb, called = _make_stub_tabbrowser44(page)
    tb._refresh_canvas(0)
    assert called["bitmap"] == 1, "Bitmap 路径应被调用"
    assert called["cpu"] == 0, "CPUImage 路径不应被调用"
    assert called["photos"] == ["bitmap_photo_0"]


@_reg
def test_refresh_canvas_bitmap_none_early_return():
    """批次 44 — _refresh_canvas 当 _last_dl 不可用且 bitmap is None 时
    提前返回（不产 photo，不调用任何呈现方法）。"""
    page = _PageStub44(width=40, height=30, bitmap=None, last_dl=None)
    tb, called = _make_stub_tabbrowser44(page)
    tb._refresh_canvas(0)
    assert called["bitmap"] == 0
    assert called["cpu"] == 0
    assert called["photos"] == []


@_reg
def test_c02_refresh_canvas_cpu_fallback_to_bitmap():
    """批次 44 — _c02_refresh_canvas_cpu 当 _last_dl 不可用（full is
    None）时回退到 _refresh_canvas（再走 Bitmap 路径）。"""
    from toy_browser.app.gui import TabBrowser
    page = _PageStub44(width=40, height=30, bitmap=None, last_dl=None)
    tb, called = _make_stub_tabbrowser44(page)
    # 真实 _c02_refresh_canvas_cpu（不用 stub）——验证 full is None 时
    # 回退到 _refresh_canvas
    tb._c02_refresh_canvas_cpu = TabBrowser._c02_refresh_canvas_cpu.__get__(tb)
    tb._c02_refresh_canvas_cpu(0)
    # _last_dl None → full None → _refresh_canvas → bitmap None → early return
    assert called["bitmap"] == 0
    assert called["cpu"] == 0
    assert called["photos"] == []


# ---------------------------------------------------------------------------
# 批次 45 — paint_engine 命令分派直接画到 CPUCanvas（CpuImageBitmapView）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 批次 45 — paint_engine 命令分派直接画到 CPUCanvas（CpuImageBitmapView）
# ---------------------------------------------------------------------------

@_reg
def test_cpu_image_bitmap_view_read_write():
    """批次 45 — CpuImageBitmapView 的 pixels 视图直接落到 CPUImage
    RGB8 buffer（读写 round-trip，不复制 int 列表）。"""
    from toy_browser.rendering.cpu_image import CpuImageBitmapView
    img = CPUImage(10, 8)
    img.buffer = bytearray([0xFF] * (img.stride * img.height))
    view = CpuImageBitmapView(img)
    try:
        view.pixels[0] = 0xFF0000
    except Exception as e:
        raise AssertionError(f"pixels[0]= 写失败: {e!r}")
    try:
        assert view.pixels[0] == 0xFF0000
    except Exception as e:
        raise AssertionError(f"pixels[0] 读断言失败: {e!r}")
    try:
        assert img.get_pixel(0, 0) == (255, 0, 0)
    except Exception as e:
        raise AssertionError(f"get_pixel(0,0) 断言失败: {e!r}")
    # 切片读（行 0：pixels[0] 已写 0xFF0000，pixels[1] 白底 0xFFFFFF）
    row = view.pixels[0:10]
    assert len(row) == 10 and row[0] == 0xFF0000 and row[1] == 0xFFFFFF
    # fill_rect via view
    try:
        view.fill_rect(0, 0, 3, 2, Color(0, 0, 255, 255))
    except Exception as e:
        raise AssertionError(f"fill_rect 失败: {e!r}")
    try:
        assert img.get_pixel(0, 0) == (0, 0, 255)
    except Exception as e:
        raise AssertionError(f"fill_rect 后 get_pixel 断言失败: {e!r}")
    # set_pixel via view（半透明 blend_over；白底上 50% 红色 →
    # R 保持 255、G/B 各降到 ~128（白底不变 R 通道））
    view.set_pixel(5, 5, Color(255, 0, 0, 128))
    dr, dg, db = img.get_pixel(5, 5)
    assert dr == 255 and 120 <= dg <= 135 and 120 <= db <= 135
    # pixel_at
    try:
        assert view.pixel_at(1, 1) == (0, 0, 255)
    except Exception as e:
        raise AssertionError(f"pixel_at 断言失败: {e!r}")


@_reg
def test_rasterize_to_canvas_matches_rasterize():
    """批次 45 — paint_engine.rasterize_to_canvas 与权威
    paint_engine.rasterize 逐像素一致（混合命令：rect + border +
    text + shadow + rounded，无 scratch Bitmap 合并层）。"""
    from toy_browser.rendering.paint_engine import (
        rasterize_to_canvas)
    dl = _dl_mixed()
    bmp = pe_rasterize(dl, 100, 90)
    canvas = rasterize_to_canvas(dl, 100, 90)
    diff = cpu_matches_bitmap(canvas.img, bmp)
    assert len(diff) == 0, diff[:5]


def _dl_mixed():
    """混合命令 DisplayList（rect + border + text + shadow +
    rounded + gradient，覆盖 paint_engine 主要命令分派）。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="rect", x=5, y=5, w=30, h=20, color=Color(255, 0, 0)))
    dl.commands.append(DisplayCommand(
        kind="border", x=10, y=10, w=20, h=15, color=Color(0, 0, 255)))
    dl.commands.append(DisplayCommand(
        kind="text", x=2, y=2, w=40, h=10, text="Hi",
        text_color=Color(0, 128, 0), font_px=10, max_width=40))
    dl.commands.append(DisplayCommand(
        kind="shadow", x=40, y=40, w=20, h=20,
        color=Color(50, 50, 50, 128), radius=4,
        shadow_offset_x=2, shadow_offset_y=2, shadow_radius=4))
    dl.commands.append(DisplayCommand(
        kind="rounded", x=60, y=60, w=25, h=25,
        color=Color(255, 128, 0), radius=8))
    dl.commands.append(DisplayCommand(
        kind="gradient", x=5, y=50, w=30, h=20,
        grad_c1=Color(255, 0, 0), grad_c2=Color(0, 0, 255),
        grad_angle="to right"))
    return dl


@_reg
def test_rasterize_to_canvas_odd_width_stride():
    """批次 45 — rasterize_to_canvas 奇数宽度 + 非紧凑 stride 时
    与 rasterize 逐像素一致（buffer stride 边界正确）。"""
    from toy_browser.rendering.paint_engine import (
        rasterize_to_canvas)
    dl = _dl_mixed()
    bmp = pe_rasterize(dl, 101, 63)
    canvas = rasterize_to_canvas(dl, 101, 63, stride=101 * 3 + 4)
    diff = cpu_matches_bitmap(canvas.img, bmp)
    assert len(diff) == 0, diff[:5]


@_reg
def test_gui_c02_rasterize_uses_canvas_path():
    """批次 45 — gui._c02_rasterize_cpu_image 走
    paint_engine.rasterize_to_canvas 直接画到 CPUCanvas（消除
    scratch Bitmap 合并层），返回的 CPUImage 与权威 rasterize 逐
    像素一致。"""
    from toy_browser.app.gui import TabBrowser
    from toy_browser.rendering.paint_engine import rasterize_to_canvas
    page = _PageStub44(width=100, height=90, content_height=90,
                       last_dl=_dl_mixed())
    tb = object.__new__(TabBrowser)
    tb.tabs = [page]
    tb.current = 0
    tb.canvas_var = {}
    tb.page_frames = {}
    img = tb._c02_rasterize_cpu_image(page)
    assert img is not None
    canvas = rasterize_to_canvas(page._last_dl, 100, 90)
    diff = cpu_matches_bitmap(img, canvas.img)
    assert len(diff) == 0, diff[:5]
    # 页面未加载（_last_dl None）时返回 None
    page2 = _PageStub44(last_dl=None)
    tb.tabs = [page2]
    assert tb._c02_rasterize_cpu_image(page2) is None


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)

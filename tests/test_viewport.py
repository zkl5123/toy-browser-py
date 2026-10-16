"""G06 — 视口 / DPI / 缩放坐标契约回归（round5 docs 批次 2）。

覆盖 ViewportState 换算公式 + ResizeCoalescer 合并逻辑：
- css↔ui 往返一致
- zoom 围绕锚点保持文档坐标不动
- 尺寸 / zoom 变化夹紧滚动
- resize 合并：连续 resize 只留最后一次，到点才提交
"""
from __future__ import annotations

from toy_browser.app.viewport import ViewportState, ResizeCoalescer


TESTS = []


def test_g06_css_ui_roundtrip():
    vp = ViewportState(css_width=800, css_height=600,
                       ui_width=800, ui_height=600,
                       device_scale=1.0, page_zoom=1.0)
    ux, uy = vp.css_to_ui(100, 50)
    cx, cy = vp.ui_to_css(ux, uy)
    assert abs(cx - 100) < 1e-9
    assert abs(cy - 50) < 1e-9


def test_g06_scroll_offset_in_ui():
    vp = ViewportState(scroll_x=10, scroll_y=20,
                       device_scale=1.0, page_zoom=1.0)
    ux, uy = vp.css_to_ui(100, 100)
    assert ux == 90 and uy == 80  # 文档坐标减滚动偏移
    cx, cy = vp.ui_to_css(ux, uy)
    assert abs(cx - 100) < 1e-9 and abs(cy - 100) < 1e-9


def test_g06_zoom_scaling():
    vp = ViewportState(device_scale=1.0, page_zoom=2.0,
                       css_width=800, css_height=600,
                       ui_width=1600, ui_height=1200)
    ux, uy = vp.css_to_ui(100, 100)
    assert ux == 200 and uy == 200  # CSS×zoom
    cx, cy = vp.ui_to_css(ux, uy)
    assert abs(cx - 100) < 1e-9 and abs(cy - 100) < 1e-9


def test_g06_device_scale_combined():
    vp = ViewportState(device_scale=1.5, page_zoom=1.0,
                       css_width=800, css_height=600,
                       ui_width=1200, ui_height=900)
    ux, uy = vp.css_to_ui(100, 100)
    assert abs(ux - 150) < 1e-9 and abs(uy - 150) < 1e-9
    cx, cy = vp.ui_to_css(ux, uy)
    assert abs(cx - 100) < 1e-9


def test_g06_zoom_anchor_holds():
    vp = ViewportState(device_scale=1.0, page_zoom=1.0,
                       css_width=800, css_height=600,
                       ui_width=800, ui_height=600,
                       scroll_x=0, scroll_y=0)
    # 围绕文档 (200, 150) 放大到 2.0：该点应保持控件中心 (400,300)
    changed = vp.set_zoom(2.0, anchor_css=(200, 150))
    assert changed
    ux, uy = vp.css_to_ui(200, 150)
    assert abs(ux - 400) < 1e-6 and abs(uy - 300) < 1e-6
    assert vp.css_width == 400  # 800/(1.0*2.0)


def test_g06_set_size_recomputes_css():
    vp = ViewportState(device_scale=1.0, page_zoom=1.0)
    assert vp.set_size(1000, 700)
    assert vp.css_width == 1000 and vp.css_height == 700
    assert not vp.set_size(1000, 700)  # 不变
    # device_scale 变化
    assert vp.set_size(1000, 700, device_scale=2.0)
    assert vp.css_width == 500


def test_g06_clamp_scroll_after_shrink():
    vp = ViewportState(scroll_x=500, scroll_y=800,
                       device_scale=1.0, page_zoom=1.0)
    vp.clamp_scroll(scroll_max_x=300, scroll_max_y=1000)
    assert vp.scroll_x == 300
    assert vp.scroll_y == 800
    vp.clamp_scroll(scroll_max_x=300, scroll_max_y=1000)  # 再夹紧
    assert vp.scroll_x == 300 and vp.scroll_y == 800


def test_g06_coalescer_flush_timing():
    c = ResizeCoalescer(min_interval_ms=16.0)
    # 首个 offer 在 t=0，合并窗口到 t=16
    c.offer(100, 100, now_ms=0.0)
    assert c.flush(now_ms=8.0) is None       # 未到点
    assert c.pending()
    assert c.flush(now_ms=16.0) == (100, 100)  # 到点 → 提交最新
    assert not c.pending()
    # 提交后无待处理
    assert c.flush(now_ms=20.0) is None


def test_g06_coalescer_keeps_latest():
    c = ResizeCoalescer(min_interval_ms=16.0)
    c.offer(100, 100, now_ms=0.0)
    c.offer(120, 110, now_ms=4.0)
    c.offer(140, 130, now_ms=8.0)  # 连续 resize，窗口起点仍是 t=0
    assert c.flush(now_ms=10.0) is None  # 未到 16ms
    assert c.flush(now_ms=16.0) == (140, 130)  # 只留最后一次
    # 新批次：offer 在 t=20，窗口到 t=36
    c.offer(160, 150, now_ms=20.0)
    assert c.flush(now_ms=28.0) is None
    assert c.flush(now_ms=36.0) == (160, 150)


def test_g06_zoom_clamp_range():
    vp = ViewportState(device_scale=1.0, page_zoom=1.0,
                       css_width=800, css_height=600,
                       ui_width=800, ui_height=600)
    vp.set_zoom(0.01)  # 低于下限 → 0.1
    assert vp.page_zoom == 0.1
    vp.set_zoom(9.9)  # 高于上限 → 4.0
    assert vp.page_zoom == 4.0


TESTS.append(("test_g06_css_ui_roundtrip", test_g06_css_ui_roundtrip))
TESTS.append(("test_g06_scroll_offset_in_ui", test_g06_scroll_offset_in_ui))
TESTS.append(("test_g06_zoom_scaling", test_g06_zoom_scaling))
TESTS.append(("test_g06_device_scale_combined",
              test_g06_device_scale_combined))
TESTS.append(("test_g06_zoom_anchor_holds", test_g06_zoom_anchor_holds))
TESTS.append(("test_g06_set_size_recomputes_css",
              test_g06_set_size_recomputes_css))
TESTS.append(("test_g06_clamp_scroll_after_shrink",
              test_g06_clamp_scroll_after_shrink))
TESTS.append(("test_g06_coalescer_flush_timing",
              test_g06_coalescer_flush_timing))
TESTS.append(("test_g06_coalescer_keeps_latest",
              test_g06_coalescer_keeps_latest))
TESTS.append(("test_g06_zoom_clamp_range", test_g06_zoom_clamp_range))

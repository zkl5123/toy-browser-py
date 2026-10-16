"""C07 — 紧凑显示列表与可见区域裁剪（纯逻辑核心）回归
（round5 docs 批次 13）。

覆盖 toy_browser/app/display_list.py：
- CommandStats：按 kind 计数、可见比例、重复资源、summary
- conservative_bounds：原始 rect / 阴影核 / 描边 / 滤镜模糊外扩
- bounds_intersect / viewport_clip：视口外可跳、阴影投进不裁掉
- ResourceTable：资源 ID 化（同一对象不重复登记）
- ir_payload：类型化 IR 按 kind 收窄字段
- count_commands：命令数量
"""
from __future__ import annotations

from toy_browser.app.display_list import (
    CommandStats, conservative_bounds, bounds_intersect,
    viewport_clip, ResourceTable, count_commands, ir_payload,
)


TESTS = []


# ----------------------------------------------------------------------
# CommandStats — 命令类型 / 可见比例 / 重复资源统计（C07 步骤 1）
# ----------------------------------------------------------------------
def test_stats_by_kind_and_visible_ratio():
    s = CommandStats()
    s.record("rect", visible=True)
    s.record("rect", visible=False)
    s.record("text", visible=True)
    s.record("img", visible=True, resource_id=1)
    s.record("img", visible=True, resource_id=1)
    s.record("img", visible=False, resource_id=2)
    assert s.by_kind["rect"] == 2
    assert s.by_kind["text"] == 1
    assert s.by_kind["img"] == 3
    assert s.total == 6
    assert s.visible_count == 4
    assert abs(s.visible_ratio() - 4 / 6) < 1e-9


def test_stats_duplicate_resources():
    s = CommandStats()
    s.record("img", visible=True, resource_id=7)
    s.record("img", visible=True, resource_id=7)   # 第 2 次 → 重复 +1
    s.record("img", visible=True, resource_id=7)   # 第 3 次 → 重复 +1
    s.record("img", visible=True, resource_id=8)   # 新资源，不重复
    assert s.duplicate_resources == 2


def test_stats_summary_and_reset():
    s = CommandStats()
    s.record("rect", visible=True)
    txt = s.summary()
    assert "total=1" in txt and "kinds=rect:1" in txt
    s.reset()
    assert s.total == 0 and s.by_kind == {}


# ----------------------------------------------------------------------
# conservative_bounds — 保守 bounds（C07 步骤 2）
# ----------------------------------------------------------------------
def test_bounds_plain_rect():
    """无阴影 / 描边 / 模糊：bounds = 原始 rect。"""
    assert conservative_bounds(10, 20, 100, 50) == (10, 20, 110, 70)


def test_bounds_shadow_expands():
    """阴影核向外扩展（偏移 + 2×blur）。"""
    b = conservative_bounds(0, 0, 50, 50,
                            shadow_radius=10,
                            shadow_offset_x=5,
                            shadow_offset_y=5)
    # shadow_expand = 2*radius = 20; 阴影盒 = 原始盒整体外扩
    #   min 端：x - 0 - expand → 0 - 0 - 20 = -20
    #   max 端：x + w - 0 + expand → 50 - 0 + 20 = 70
    # （偏移量由调用方把盒移到阴影位置后再传本函数；本函数只按
    #   当前盒外扩 2×blur 覆盖高斯尾部，保守不偏移）
    assert b[0] == -20 and b[1] == -20
    assert b[2] == 70 and b[3] == 70


def test_bounds_outline_expands():
    b = conservative_bounds(0, 0, 50, 50, outline_width=4)
    assert b == (-4, -4, 54, 54)


def test_bounds_filter_blur_expands():
    b = conservative_bounds(0, 0, 50, 50, filter_blur=8)
    assert b == (-8, -8, 58, 58)


def test_bounds_combined():
    b = conservative_bounds(0, 0, 10, 10,
                            shadow_radius=5, outline_width=2,
                            filter_blur=3)
    # 阴影 expand=10: [-10,-10,20,20]; 描边再外扩 2; 滤镜再外扩 3
    assert b[0] < 0 and b[2] > 10


# ----------------------------------------------------------------------
# bounds_intersect / viewport_clip（C07 步骤 4 + 验收"阴影不被裁掉"）
# ----------------------------------------------------------------------
def test_intersect_basic():
    assert bounds_intersect((0, 0, 10, 10), 0, 0, 100, 100) is True
    assert bounds_intersect((200, 200, 210, 210), 0, 0, 100, 100) is False


def test_viewport_clip_outside_skippable():
    """完全在视口外（含保守 bounds）→ 可跳过。"""
    cmd = {"x": 500, "y": 500, "w": 10, "h": 10}
    assert viewport_clip(cmd, 0, 0, 100, 100) is True


def test_viewport_clip_inside_not_skippable():
    """与视口相交 → 不可跳过。"""
    cmd = {"x": 50, "y": 50, "w": 100, "h": 100}
    assert viewport_clip(cmd, 0, 0, 100, 100) is False


def test_viewport_clip_shadow_projects_in():
    """验收"阴影从视口外投进来不被错误裁掉"：原始 rect 在视口外，
    但阴影核投进视口 → 用保守 bounds 判 → 不可跳过。"""
    cmd = {"x": -100, "y": 50, "w": 30, "h": 30,
           "shadow_radius": 50, "shadow_offset_x": 100}
    # 原始 rect [ -100,-70.. ] 在视口外，但阴影 offset_x=100 + 2×blur=100
    # 使阴影盒扩到 x≈200 附近，与视口 [0,0,100,100] 相交
    assert viewport_clip(cmd, 0, 0, 100, 100) is False


# ----------------------------------------------------------------------
# ResourceTable — 资源 ID 化（C07 步骤 3）
# ----------------------------------------------------------------------
def test_resource_table_reuse():
    table = ResourceTable()
    pixels = [[0, 1, 2]]
    rid1 = table.register(pixels)
    rid2 = table.register(pixels)   # 同一对象 → 同 ID
    assert rid1 == rid2
    assert len(table) == 1
    assert table.get(rid1) is pixels


def test_resource_table_distinct():
    table = ResourceTable()
    a = table.register([1])
    b = table.register([2])
    assert a != b
    assert len(table) == 2


# ----------------------------------------------------------------------
# ir_payload — 类型化 IR（C07 交付 "类型化 IR"）
# ----------------------------------------------------------------------
def test_ir_payload_rect():
    cmd = {"x": 1, "y": 2, "w": 3, "h": 4, "color": "C",
           "text": "ignored", "font_px": 99}
    p = ir_payload("rect", cmd)
    assert p["kind"] == "rect"
    assert p["color"] == "C"
    assert "text" not in p        # rect 不携带 text
    assert "font_px" not in p


def test_ir_payload_text():
    cmd = {"x": 0, "y": 0, "text": "hi", "text_color": "T",
           "font_px": 12, "align": "left", "color": "ignored"}
    p = ir_payload("text", cmd)
    assert p["text"] == "hi"
    assert p["font_px"] == 12
    assert "color" not in p       # text 不携带背景色


def test_ir_payload_img_uses_resource_id():
    cmd = {"x": 0, "y": 0, "w": 10, "h": 10, "resource_id": 5,
           "img_pixels": [999], "img_src_w": 10, "img_src_h": 10}
    p = ir_payload("img", cmd)
    assert p["resource_id"] == 5
    assert "img_pixels" not in p   # 资源 ID 化，不复制像素


def test_ir_payload_unknown_passthrough():
    cmd = {"x": 0, "weird": 1}
    p = ir_payload("clipped", cmd)
    assert p["weird"] == 1        # 未知 kind 全字段透传


# ----------------------------------------------------------------------
# count_commands
# ----------------------------------------------------------------------
def test_count_commands():
    assert count_commands([1, 2, 3]) == 3
    assert count_commands([]) == 0


TESTS.append(("test_stats_by_kind_and_visible_ratio",
              test_stats_by_kind_and_visible_ratio))
TESTS.append(("test_stats_duplicate_resources",
              test_stats_duplicate_resources))
TESTS.append(("test_stats_summary_and_reset",
              test_stats_summary_and_reset))
TESTS.append(("test_bounds_plain_rect", test_bounds_plain_rect))
TESTS.append(("test_bounds_shadow_expands", test_bounds_shadow_expands))
TESTS.append(("test_bounds_outline_expands",
              test_bounds_outline_expands))
TESTS.append(("test_bounds_filter_blur_expands",
              test_bounds_filter_blur_expands))
TESTS.append(("test_bounds_combined", test_bounds_combined))
TESTS.append(("test_intersect_basic", test_intersect_basic))
TESTS.append(("test_viewport_clip_outside_skippable",
              test_viewport_clip_outside_skippable))
TESTS.append(("test_viewport_clip_inside_not_skippable",
              test_viewport_clip_inside_not_skippable))
TESTS.append(("test_viewport_clip_shadow_projects_in",
              test_viewport_clip_shadow_projects_in))
TESTS.append(("test_resource_table_reuse",
              test_resource_table_reuse))
TESTS.append(("test_resource_table_distinct",
              test_resource_table_distinct))
TESTS.append(("test_ir_payload_rect", test_ir_payload_rect))
TESTS.append(("test_ir_payload_text", test_ir_payload_text))
TESTS.append(("test_ir_payload_img_uses_resource_id",
              test_ir_payload_img_uses_resource_id))
TESTS.append(("test_ir_payload_unknown_passthrough",
              test_ir_payload_unknown_passthrough))
TESTS.append(("test_count_commands", test_count_commands))

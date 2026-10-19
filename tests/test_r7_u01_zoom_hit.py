"""R7-U01 — zoom / DPR hit-test coordinate inverse transform (G06).

审计实施方案 R7-U01 第 3 点："resize/scroll/zoom/DPR 使用统一坐标变换，
命中测试应用逆变换"；验收："150%/200% 缩放点击位置正确"。

Prior to the fix, :meth:`PageInstance.hit_test_at` used a bare
``doc_y = y + self.yoffset`` — pure vertical scroll, no zoom / device_scale.
At 150%/200% the click at a fixed physical pixel resolved to the wrong CSS
document coordinate.  The fix routes the click through the unified
:class:`ViewportState.logical_to_css` inverse transform
(``(ui / (zoom × dpr)) + scroll``), and stores the viewport state on
``PageInstance._viewport``.

Two layers:
* **静态层**（无 tkinter 也可跑）：核查 gui.py 源码中
  ``hit_test_at`` 经 ``_viewport.logical_to_css`` 转坐标（而非裸
  ``y + yoffset``），且 ``__init__`` 挂载 ``ViewportState``、``set_zoom``
  更新 ``_viewport.page_zoom``。
* **动态层**（完整 Python 环境才有 tkinter）：手工布局根 + patch
  ``layout.hit_test``，验证 150%/200% 缩放命中位置正确、100% 缩放
  退化为旧 ``y + yoffset`` 行为、滚动 + 缩放叠加。

无 tkinter 时动态层静默跳过（静态层已守住契约）。
"""
from __future__ import annotations

import os
import re
import sys
import types


# ---------------------------------------------------------------------------
# 静态层 helper
# ---------------------------------------------------------------------------

def _gui_source() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    gui_path = os.path.join(here, os.pardir, "toy_browser", "app", "gui.py")
    with open(gui_path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_method(src: str, method_name: str) -> str:
    pat = re.compile(r"^\s*def " + re.escape(method_name) + r"\b", re.MULTILINE)
    m = pat.search(src)
    if not m:
        return ""
    lines = src[m.start():].split("\n")
    first = lines[0]
    indent = len(first) - len(first.lstrip())
    out = [first]
    for line in lines[1:]:
        if not line.strip():
            out.append(line)
            continue
        cur = len(line) - len(line.lstrip())
        if cur <= indent and line.lstrip().startswith("def "):
            break
        out.append(line)
    return "\n".join(out)


def _try_import_gui():
    try:
        from toy_browser.app import gui as gui_mod
        return gui_mod
    except Exception:
        return None


def _try_import_viewport():
    try:
        from toy_browser.app.viewport import ViewportState
        return ViewportState
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 静态层
# ---------------------------------------------------------------------------

def test_static_hit_test_uses_viewport_inverse_transform():
    """hit_test_at 经 _viewport.logical_to_css 转坐标（非裸 y+yoffset）。"""
    src = _gui_source()
    body = _extract_method(src, "hit_test_at")
    assert body, "hit_test_at not found"
    assert "logical_to_css" in body, (
        "hit_test_at must route through the unified coordinate inverse "
        "transform (logical_to_css)"
    )
    assert "_viewport" in body, "must read the per-page ViewportState"
    # 旧的裸垂直偏移不应再是直接命中坐标。
    assert "doc_y = y + self.yoffset" not in body, (
        "hit_test_at still uses the bare 'y + yoffset' (no zoom/DPR transform)"
    )


def test_static_init_mounts_viewport_state():
    """__init__ 挂载 ViewportState。"""
    src = _gui_source()
    assert "ViewportState" in src, "PageInstance should mount a ViewportState"
    assert "self._viewport" in src, "PageInstance should hold self._viewport"


def test_static_set_zoom_updates_viewport():
    """set_zoom 更新 _viewport.page_zoom 并夹紧滚动。"""
    src = _gui_source()
    body = _extract_method(src, "set_zoom")
    assert body, "set_zoom not found"
    assert "self._viewport.page_zoom = zoom" in body, (
        "set_zoom must write _viewport.page_zoom"
    )
    assert "scroll_y" in body, "set_zoom should re-clamp the scroll offset"


def test_static_hit_test_has_fallback_without_viewport():
    """无 _viewport（pre-G06 实例）时退化为 y+yoffset，不崩。"""
    src = _gui_source()
    body = _extract_method(src, "hit_test_at")
    assert body, "hit_test_at not found"
    assert "getattr(self, \"_viewport\", None)" in body, (
        "hit_test_at must tolerate a missing _viewport (legacy fallback)"
    )
    assert "self.yoffset" in body, "fallback path must still use the scroll offset"


# ---------------------------------------------------------------------------
# 动态层
# ---------------------------------------------------------------------------

def _bare_page():
    """Bypass __init__; set only the fields hit_test_at touches."""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return None
    ViewportState = _try_import_viewport()
    if ViewportState is None:
        return None
    p = gui_mod.PageInstance.__new__(gui_mod.PageInstance)
    p.session = types.SimpleNamespace(
        dom_tree=None,
        layout_root=types.SimpleNamespace(),  # truthy sentinel
    )
    p.yoffset = 0
    p.content_height = 0
    p.width = 800
    p.height = 600
    p._viewport = ViewportState()
    return p


def _patch_hit_box(captured):
    """patch layout.hit_test to capture (mx, my) and return a fixed box."""
    from toy_browser.layout import layout as _layout_mod

    def _fake_hit(root, mx, my):
        captured["mx"] = mx
        captured["my"] = my
        return types.SimpleNamespace(dom=None, parent=None, rect=None, children=[])

    _layout_mod.hit_test = _fake_hit


def test_dynamic_150_percent_zoom_shifts_hit_coordinate():
    """150% 缩放：点同一物理像素，命中 CSS 坐标应按 1/1.5 缩小。"""
    p = _bare_page()
    if p is None:
        return
    from toy_browser.app.viewport import ViewportState
    p._viewport = ViewportState(page_zoom=1.5)
    # 模拟：物理像素 (0, 150) 在 150% 下 → CSS 文档坐标 (0/1.5, 150/1.5)=(0,100)
    captured: dict = {}
    _patch_hit_box(captured)
    p.hit_test_at(0, 150)
    # logical_to_css: (0/1.5, 150/1.5 + 0) = (0.0, 100.0)
    assert abs(captured["mx"] - 0.0) < 1e-6, f"mx={captured['mx']}"
    assert abs(captured["my"] - 100.0) < 1e-6, f"my={captured['my']}"


def test_dynamic_200_percent_zoom_shifts_hit_coordinate():
    """200% 缩放：物理 (0, 200) → CSS (0, 100)。"""
    p = _bare_page()
    if p is None:
        return
    from toy_browser.app.viewport import ViewportState
    p._viewport = ViewportState(page_zoom=2.0)
    captured: dict = {}
    _patch_hit_box(captured)
    p.hit_test_at(0, 200)
    assert abs(captured["my"] - 100.0) < 1e-6, f"my={captured['my']}"


def test_dynamic_100_percent_degrades_to_legacy_behavior():
    """100% + device_scale=1.0：logical_to_css 退化为 ui + scroll（旧行为）。"""
    p = _bare_page()
    if p is None:
        return
    from toy_browser.app.viewport import ViewportState
    p._viewport = ViewportState(page_zoom=1.0, device_scale=1.0, scroll_y=50)
    captured: dict = {}
    _patch_hit_box(captured)
    p.hit_test_at(10, 30)
    # (10/1.0, 30/1.0 + 50) = (10.0, 80.0)  — 等价旧 doc_y = y + yoffset
    assert abs(captured["mx"] - 10.0) < 1e-6, f"mx={captured['mx']}"
    assert abs(captured["my"] - 80.0) < 1e-6, f"my={captured['my']}"


def test_dynamic_scroll_plus_zoom_combined():
    """滚动 + 缩放叠加：zoom=1.5, scroll_y=100, 物理 (0, 300) →
    CSS (0, 300/1.5 + 100) = (0, 300)。"""
    p = _bare_page()
    if p is None:
        return
    from toy_browser.app.viewport import ViewportState
    p._viewport = ViewportState(page_zoom=1.5, device_scale=1.0, scroll_y=100)
    captured: dict = {}
    _patch_hit_box(captured)
    p.hit_test_at(0, 300)
    assert abs(captured["my"] - 300.0) < 1e-6, f"my={captured['my']}"


def test_dynamic_set_zoom_clamps_scroll_and_updates_viewport():
    """set_zoom 更新 _viewport.page_zoom 并夹紧滚动。"""
    p = _bare_page()
    if p is None:
        return
    p.content_height = 1000
    p.height = 600
    p.yoffset = 400
    p._viewport.scroll_y = 400
    p.set_zoom(2.0)
    assert p._viewport.page_zoom == 2.0
    # scroll_to clamps to [0, content_height - height] = [0, 400]; yoffset=400
    assert p.yoffset == 400
    assert p._viewport.scroll_y == 400


def test_dynamic_missing_viewport_fallback():
    """无 _viewport 时退化为 y + yoffset（pre-G06 实例不崩）。"""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return
    p = gui_mod.PageInstance.__new__(gui_mod.PageInstance)
    p.session = types.SimpleNamespace(dom_tree=None, layout_root=types.SimpleNamespace())
    p.yoffset = 40
    # 不设 _viewport 属性 → getattr(self, "_viewport", None) 走 fallback
    captured: dict = {}
    _patch_hit_box(captured)
    p.hit_test_at(10, 20)
    # fallback: doc_x = x, doc_y = y + yoffset = 20 + 40 = 60
    assert abs(captured["mx"] - 10.0) < 1e-6
    assert abs(captured["my"] - 60.0) < 1e-6


# ---------------------------------------------------------------------------
TESTS = [
    ("test_static_hit_test_uses_viewport_inverse_transform",
     test_static_hit_test_uses_viewport_inverse_transform),
    ("test_static_init_mounts_viewport_state",
     test_static_init_mounts_viewport_state),
    ("test_static_set_zoom_updates_viewport",
     test_static_set_zoom_updates_viewport),
    ("test_static_hit_test_has_fallback_without_viewport",
     test_static_hit_test_has_fallback_without_viewport),
    ("test_dynamic_150_percent_zoom_shifts_hit_coordinate",
     test_dynamic_150_percent_zoom_shifts_hit_coordinate),
    ("test_dynamic_200_percent_zoom_shifts_hit_coordinate",
     test_dynamic_200_percent_zoom_shifts_hit_coordinate),
    ("test_dynamic_100_percent_degrades_to_legacy_behavior",
     test_dynamic_100_percent_degrades_to_legacy_behavior),
    ("test_dynamic_scroll_plus_zoom_combined",
     test_dynamic_scroll_plus_zoom_combined),
    ("test_dynamic_set_zoom_clamps_scroll_and_updates_viewport",
     test_dynamic_set_zoom_clamps_scroll_and_updates_viewport),
    ("test_dynamic_missing_viewport_fallback",
     test_dynamic_missing_viewport_fallback),
]

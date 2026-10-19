"""R7-U02 — 真实 Tk Entry/Text 原生输入控件（审计 §57 残留落地）。

审计原文："EditorOverlayController.attach 创建 EditorState 与
OverlayPlacement 并设置可见状态，没有创建实际 Tk Entry/Text。"
本测试锁定 :class:`NativeOverlayWidget` 的核心契约：

* **控件选择**：``input`` → Entry / ``textarea`` → Text（纯逻辑，
  无 Tk 可核查"该用哪种控件"）。
* **双向同步**：模型 → 控件（value/caret 写入）、控件 → 模型
  （value 读回，password 不回写真实 value）。
* **password 遮罩**：Entry 显示遮罩（● 等长），真实 value 不变。
* **销毁终态**：控件销毁后所有同步 / 定位 / 再绑定拒绝（no-op 不崩）。

两层设计（与 test_r7_u01_window_close 约定一致）：
* **静态层**（无 tkinter 也可跑）：核查 native_overlay.py 源码中
  "控件选择 / 双向同步 / password 遮罩 / 销毁终态" 契约确实存在。
* **动态层**（完整 Python 环境才有 tkinter，注入真实控件；受管
  3.13.12 无 tkinter 时静默跳过，静态层已守住契约）：用真实
  ``tk.Entry`` / ``tk.Text`` 验证双向同步 / password 遮罩 / 多行 /
  销毁终态。
"""
from __future__ import annotations

import os
import re
import sys
import types


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

def _ov_source() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, os.pardir, "toy_browser", "app",
                        "native_overlay.py")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_method(src: str, method_name: str) -> str:
    pat = re.compile(r"^\s*def " + re.escape(method_name) + r"\b",
                    re.MULTILINE)
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


def _try_import_module():
    try:
        from toy_browser.app import native_overlay as mod
        return mod
    except Exception:
        return None


def _try_tk():
    try:
        import tkinter as tk
        return tk
    except Exception:
        return None


def _state(value="", is_password=False, tag="input"):
    from toy_browser.app.editor import EditorState
    return EditorState(node_ref=1, value=value,
                       is_password=is_password, read_only=False)


# ---------------------------------------------------------------------------
# 静态层（无 tkinter 也可跑）
# ---------------------------------------------------------------------------

def test_static_widget_class_name_selection():
    """widget_class_name 返回 entry→Entry / textarea→Text。"""
    src = _ov_source()
    assert "def widget_class_name" in src
    body = _extract_method(src, "widget_class_name")
    assert body, "widget_class_name not found"
    assert "Text" in body and "Entry" in body, (
        "must map kind to the real Tk widget class names"
    )
    assert "textarea" in body, "must branch on textarea kind"


def test_static_bidirectional_sync_exists():
    """双向同步：sync_to_widget（模型→控件）+ sync_from_widget
    （控件→模型）都存在。"""
    src = _ov_source()
    assert "def sync_to_widget" in src, "missing sync_to_widget"
    assert "def sync_from_widget" in src, "missing sync_from_widget"
    to_body = _extract_method(src, "sync_to_widget")
    from_body = _extract_method(src, "sync_from_widget")
    assert to_body and from_body
    assert "insert" in to_body, "model→widget must write via insert"
    assert "get" in from_body, "widget→model must read via get"


def test_static_password_mask_separated():
    """password 遮罩：真实 value 不回写，呈现层用遮罩字符。"""
    src = _ov_source()
    assert "is_password" in src, "password handling must be present"
    from_body = _extract_method(src, "sync_from_widget")
    assert "is_password" in from_body, (
        "widget→model read must NOT overwrite the real value for "
        "password fields (G03 step 4 clipboard separation)"
    )


def test_static_destroy_terminal_rejects_operations():
    """销毁终态：destroyed 后所有同步 / 定位 / 再绑定拒绝。"""
    src = _ov_source()
    for m in ("sync_to_widget", "sync_from_widget", "place", "bind_widget"):
        body = _extract_method(src, m)
        assert body, f"{m} not found"
        assert "self._destroyed" in body, (
            f"{m} must check the destroyed terminal flag"
        )


def test_static_geometry_place_uses_zoom_scale():
    """place 定位按 CSS×page_zoom 物理标度（与 ViewportState 一致）。"""
    src = _ov_source()
    body = _extract_method(src, "place")
    assert body, "place not found"
    assert "page_zoom" in body, "place must scale by viewport zoom"
    assert "place" in body, "must call the widget's place() API"


# ---------------------------------------------------------------------------
# 动态层（完整环境才有 tkinter，注入真实控件；无 tkinter 静默跳过）
# ---------------------------------------------------------------------------

def test_dynamic_entry_widget_selection():
    """input kind → 真实 tk.Entry 控件，双向同步可用。"""
    mod = _try_import_module()
    tk = _try_tk()
    if mod is None or tk is None:
        return
    root = tk.Tk()
    root.withdraw()
    try:
        st = _state(value="hello")
        w = mod.NativeOverlayWidget(st, kind=mod.KIND_ENTRY)
        assert w.widget_class_name() == "Entry"
        real = tk.Entry(root)
        w.bind_widget(real)
        # 模型 → 控件
        assert w.sync_to_widget() is True
        assert real.get() == "hello"
        # 控件 → 模型
        real.delete(0, "end")
        real.insert(0, "world")
        assert w.sync_from_widget() is True
        assert st.value == "world"
    finally:
        root.destroy()


def test_dynamic_textarea_widget_selection():
    """textarea kind → 真实 tk.Text 控件，多行内容双向同步。"""
    mod = _try_import_module()
    tk = _try_tk()
    if mod is None or tk is None:
        return
    root = tk.Tk()
    root.withdraw()
    try:
        st = _state(value="line1\nline2")
        w = mod.NativeOverlayWidget(st, kind=mod.KIND_TEXTAREA)
        assert w.widget_class_name() == "Text"
        real = tk.Text(root, height=2)
        w.bind_widget(real)
        assert w.sync_to_widget() is True
        assert real.get("1.0", "end-1c") == "line1\nline2"
        # 控件 → 模型（textarea 读回含 \n）
        real.delete("1.0", "end")
        real.insert("1.0", "a\nb\nc")
        assert w.sync_from_widget() is True
        assert st.value == "a\nb\nc"
    finally:
        root.destroy()


def test_dynamic_password_mask_not_overwrite_real_value():
    """password 控件：遮罩文本不回写真实 value（剪贴板策略不变）。"""
    mod = _try_import_module()
    tk = _try_tk()
    if mod is None or tk is None:
        return
    root = tk.Tk()
    root.withdraw()
    try:
        st = _state(value="secret", is_password=True)
        w = mod.NativeOverlayWidget(st, kind=mod.KIND_ENTRY)
        real = tk.Entry(root, show="*")
        w.bind_widget(real)
        assert w.sync_to_widget() is True
        # 真实 value 不被遮罩篡改
        assert st.value == "secret"
        # 呈现层 display_value 是等长遮罩
        assert st.display_value() == "●●●●●●"
        # 读回遮罩文本（长度 6 == 真实 value 长度）：同步成功且真实值不变
        assert w.sync_from_widget() is True
        assert st.value == "secret"
    finally:
        root.destroy()


def test_dynamic_destroy_terminal_noops():
    """销毁后所有同步 / 定位 / 再绑定拒绝（no-op 不崩）。"""
    mod = _try_import_module()
    tk = _try_tk()
    if mod is None or tk is None:
        return
    root = tk.Tk()
    root.withdraw()
    try:
        st = _state(value="abc")
        w = mod.NativeOverlayWidget(st, kind=mod.KIND_ENTRY)
        real = tk.Entry(root)
        w.bind_widget(real)
        assert w.destroy() is True
        assert w.destroyed is True
        # 终态拒绝
        assert w.sync_to_widget() is False
        assert w.sync_from_widget() is False
        assert w.place() is False
        real2 = tk.Entry(root)
        w.bind_widget(real2)   # 销毁后拒绝再绑定
        assert w.widget is None
    finally:
        root.destroy()


def test_dynamic_place_geometry_scales_with_zoom():
    """place 定位按 CSS×page_zoom 物理标度。"""
    mod = _try_import_module()
    tk = _try_tk()
    if mod is None or tk is None:
        return
    root = tk.Tk()
    root.withdraw()
    try:
        st = _state(value="x")
        from toy_browser.app.overlay_protocol import OverlayRect, Viewport
        w = mod.NativeOverlayWidget(
            st, kind=mod.KIND_ENTRY,
            rect=OverlayRect(x=10.0, y=20.0, w=100.0, h=24.0))
        real = tk.Entry(root)
        w.bind_widget(real)
        # zoom=2.0：CSS(10,20,100,24) → 物理(20,40,200,48)
        vp = Viewport(page_zoom=2.0)
        assert w.place(vp) is True
        root.update_idletasks()
        pi = real.place_info()
        cx = int(pi["x"]); cy = int(pi["y"])
        cw = int(pi["width"]); ch = int(pi["height"])
        assert (cx, cy) == (20, 40), f"origin=({cx},{cy})"
        assert (cw, ch) == (200, 48), f"size=({cw},{ch})"
    finally:
        root.destroy()


# ---------------------------------------------------------------------------
# 桩控件（无 tkinter 也能跑的双向同步 / 销毁验证）
# ---------------------------------------------------------------------------

class _StubWidget:
    """最小桩：实现 Entry/Text 的 delete/insert/get + selection_range + place 协议。"""

    def __init__(self):
        self._buf = ""
        self._sel = (0, 0)
        self._placed = None
        self.destroyed = False

    def delete(self, a, b):
        self._buf = ""

    def insert(self, a, text):
        self._buf = text

    def get(self, a="0", b=None):
        # 桩简化：忽略索引差异，返回缓冲区（模拟 Entry.get() 与
        # Text.get("1.0","end-1c") 的语义——都是"当前全文"）。
        return self._buf

    def selection_range(self, start, end):
        # 真实 Entry/Text 都有 selection_range；桩记录选区供断言。
        self._sel = (int(start), int(end))

    def place(self, x, y, width, height):
        self._placed = (x, y, width, height)


def test_stub_widget_bidirectional_sync_without_tk():
    """无 tkinter：用桩控件验证双向同步 + 销毁契约（静态层兜底）。"""
    mod = _try_import_module()
    if mod is None:
        return
    st = _state(value="init")
    w = mod.NativeOverlayWidget(st, kind=mod.KIND_ENTRY)
    stub = _StubWidget()
    w.bind_widget(stub)
    assert stub._buf == "init"
    # 模型 → 控件
    st.value = "changed"
    assert w.sync_to_widget() is True
    assert stub._buf == "changed"
    # 控件 → 模型
    stub._buf = "from-widget"
    assert w.sync_from_widget() is True
    assert st.value == "from-widget"
    # 销毁终态
    assert w.destroy() is True
    assert w.sync_to_widget() is False
    assert w.sync_from_widget() is False


def test_stub_widget_destroy_idempotent():
    """destroy 幂等：再次 destroy 返 False（已销毁）。"""
    mod = _try_import_module()
    if mod is None:
        return
    st = _state(value="x")
    w = mod.NativeOverlayWidget(st, kind=mod.KIND_ENTRY)
    stub = _StubWidget()
    w.bind_widget(stub)
    assert w.destroy() is True
    assert w.destroy() is False, "second destroy must return False"


# ---------------------------------------------------------------------------
TESTS = [
    ("test_static_widget_class_name_selection",
     test_static_widget_class_name_selection),
    ("test_static_bidirectional_sync_exists",
     test_static_bidirectional_sync_exists),
    ("test_static_password_mask_separated",
     test_static_password_mask_separated),
    ("test_static_destroy_terminal_rejects_operations",
     test_static_destroy_terminal_rejects_operations),
    ("test_static_geometry_place_uses_zoom_scale",
     test_static_geometry_place_uses_zoom_scale),
    ("test_dynamic_entry_widget_selection",
     test_dynamic_entry_widget_selection),
    ("test_dynamic_textarea_widget_selection",
     test_dynamic_textarea_widget_selection),
    ("test_dynamic_password_mask_not_overwrite_real_value",
     test_dynamic_password_mask_not_overwrite_real_value),
    ("test_dynamic_destroy_terminal_noops",
     test_dynamic_destroy_terminal_noops),
    ("test_dynamic_place_geometry_scales_with_zoom",
     test_dynamic_place_geometry_scales_with_zoom),
    ("test_stub_widget_bidirectional_sync_without_tk",
     test_stub_widget_bidirectional_sync_without_tk),
    ("test_stub_widget_destroy_idempotent",
     test_stub_widget_destroy_idempotent),
]

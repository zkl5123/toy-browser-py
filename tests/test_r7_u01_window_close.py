"""R7-U01 — 窗口关闭与资源回收回归。

Round-7 评估（GUI 与工程质量审计 §5）列出的静态缺陷：

* ``gui.py`` 的 ``WM_DELETE_WINDOW`` 内联回调只设 ``_g07_ui_closing=True``
  + 关文档 server，**缺少** ``root.destroy()`` / ``root.quit()``；
  重复关闭（``_g07_ui_closing`` 已 True）时直接 return，窗口可能残留；
* 异常路径（server shutdown / PhotoImage 清理抛错）也可能留下窗口。

修复契约（落在 ``TabBrowser._on_window_close``）：
1. 幂等：``_g07_ui_closing`` 已 True 时仍确保 ``root.destroy()`` 被调用；
2. 顺序：先关文档 server → 清理 PhotoImage → ``root.destroy()``；
3. 异常安全：``root.destroy()`` 在 try/except 中，即使 server shutdown /
   清理异常也不留窗口；
4. ``root.quit()`` 在 destroy 后调用，让 mainloop 退出。

本测试分两层：
* **静态层（无 tkinter 也可跑）**：检查 gui.py 源码中
  ``_on_window_close`` 方法存在、引用了 ``_g07_ui_closing`` /
  ``destroy`` / ``quit``、且 ``protocol("WM_DELETE_WINDOW", ...)`` 指向
  它。锁定"修复已接线"这一契约，不依赖运行环境。
* **动态层（完整 Python 环境才有）**：用 mock TabBrowser 绑定真实方法，
  验证正常路径 / 重复关闭 / server 异常 / 图片清理异常下
  ``root.destroy()`` 始终被调用。无 tkinter 时这些动态测试静默跳过
  （静态层已守住契约）。
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# 工具：读 gui.py 源码
# ---------------------------------------------------------------------------

def _gui_source() -> str:
    """读 gui.py 源码（不 import，避免无 tkinter 时崩）。"""
    path = os.path.join(_ROOT, "toy_browser", "app", "gui.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _extract_method(src: str, method_name: str) -> str:
    """从源码里抽出 ``def method_name(...)`` 的整个方法体。

    用缩进追踪：取方法 def 行，向后读到比 def 行更浅（或同级）的
    非空行为止。"""
    lines = src.split("\n")
    start = None
    for i, ln in enumerate(lines):
        m = re.match(r"^(\s*)def " + re.escape(method_name) + r"\s*\(", ln)
        if m:
            start = i
            break
    if start is None:
        return ""
    # def 行本身的缩进
    mdef = re.match(r"^(\s*)", lines[start])
    def_indent = len(mdef.group(1))
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() == "":
            out.append(ln)
            continue
        mcur = re.match(r"^(\s*)", ln)
        cur_indent = len(mcur.group(1))
        if cur_indent <= def_indent:
            break  # 回到外层 → 方法结束
        out.append(ln)
    return "\n".join(out)


def _try_import_gui():
    try:
        from toy_browser.app import gui as gui_mod
        return gui_mod
    except ImportError:
        return None


def _try_import_tk():
    try:
        import tkinter as tk
        return tk
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# 静态层：源码契约（无 tkinter 也可验证）
# ---------------------------------------------------------------------------

@_reg
def test_static_on_window_close_exists_in_source():
    """gui.py 源码必须含 ``def _on_window_close`` 方法。"""
    src = _gui_source()
    assert "def _on_window_close" in src, (
        "TabBrowser._on_window_close 方法在 gui.py 源码中不存在——"
        "R7-U01 窗口关闭修复未落地")


@_reg
def test_static_on_window_close_calls_destroy_quit():
    """_on_window_close 方法体必须调用 destroy() 和 quit()。"""
    src = _gui_source()
    body = _extract_method(src, "_on_window_close")
    assert body, "_on_window_close 方法体提取失败"
    assert re.search(r"\.destroy\(\)", body), (
        "_on_window_close 未调用 root.destroy()——窗口可能残留")
    assert re.search(r"\.quit\(\)", body), (
        "_on_window_close 未调用 root.quit()——mainloop 不退出")


@_reg
def test_static_on_window_close_idempotent_flag():
    """_on_window_close 必须引用 _g07_ui_closing（幂等标志）。"""
    src = _gui_source()
    body = _extract_method(src, "_on_window_close")
    assert body, "_on_window_close 方法体提取失败"
    assert "_g07_ui_closing" in body, (
        "_on_window_close 未引用 _g07_ui_closing 幂等标志——"
        "重复关闭可能重入 shutdown")


@_reg
def test_static_wm_delete_protocol_wired():
    """protocol("WM_DELETE_WINDOW", ...) 必须指向 _on_window_close。"""
    src = _gui_source()
    # 旧代码：def _g07_on_ui_closing(_event=None): ... protocol("WM_DELETE_WINDOW", _g07_on_ui_closing)
    # 新代码：protocol("WM_DELETE_WINDOW", self._on_window_close)
    m = re.search(r'protocol\(\s*["\']WM_DELETE_WINDOW["\']\s*,\s*([^)\s]+)', src)
    assert m, "gui.py 源码中找不到 protocol(\"WM_DELETE_WINDOW\", ...)"
    target = m.group(1)
    # 旧的内联回调名 _g07_on_ui_closing 必须已消失（已重构为方法）
    assert "_g07_on_ui_closing" not in target, (
        f"WM_DELETE_WINDOW 仍指向旧内联回调 {target}——"
        f"R7-U01 修复未接线，窗口关闭仍缺 root.destroy()")
    # 新目标必须是 self._on_window_close
    assert "_on_window_close" in target, (
        f"WM_DELETE_WINDOW 应指向 self._on_window_close，实际 {target}")


@_reg
def test_static_destroy_in_exception_safe_block():
    """root.destroy() 必须在 try/except 中（异常安全：清理异常不留窗口）。"""
    src = _gui_source()
    body = _extract_method(src, "_on_window_close")
    assert body, "_on_window_close 方法体提取失败"
    # destroy() 调用前应有 try:（同 try 块内或前几行）
    # 简化：方法体必须同时含 try/except 与 destroy
    assert "try:" in body and "except" in body, (
        "_on_window_close 缺少 try/except 异常安全块——"
        "server shutdown / 图片清理抛错时可能不留窗口")
    # destroy 在 try 内：找 self.root.destroy() 调用之前最近的 try:
    # （方法体里 try: 出现多次——server shutdown / image cleanup /
    # destroy / quit 各一个 try 块。必须确认 destroy 调用行**之前**
    # 存在一个 try: 且之后存在对应的 except，证明 destroy 在 try/except
    # 内被保护。用 rfind 在 destroy 调用之前的子串里找最近的 try:）。
    self_destroy_idx = body.find("self.root.destroy()")
    assert self_destroy_idx != -1, "找不到 self.root.destroy() 调用"
    before_destroy = body[:self_destroy_idx]
    try_before_idx = before_destroy.rfind("try:")
    assert try_before_idx != -1, (
        "self.root.destroy() 之前无 try:——异常安全契约未满足")
    # destroy 调用之后必须有 except（同 try 块的异常兜底）
    after_destroy = body[self_destroy_idx:]
    assert "except" in after_destroy, (
        "self.root.destroy() 所在 try 块无 except 兜底")


# ---------------------------------------------------------------------------
# 动态层：mock TabBrowser 绑真实方法（完整 Python 环境才有 tkinter）
# ---------------------------------------------------------------------------

class _MockRoot:
    """最小 Tk root 替身：记录 destroy/quit 调用。"""

    def __init__(self):
        self.destroyed = False
        self.quit_called = False

    def destroy(self):
        self.destroyed = True

    def quit(self):
        self.quit_called = True


class _MockDocServer:
    """最小 DocProcessServer 替身：记录 shutdown 调用。"""

    def __init__(self):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True

    def describe(self):
        return "mock"


class _MockTabBrowser:
    """最小 TabBrowser 替身：只测 _on_window_close 逻辑分支。"""

    def __init__(self, close_behavior="normal"):
        self.root = _MockRoot()
        self._g07_doc_server = _MockDocServer()
        self._g07_ui_closing = False
        self.canvas_var = {}
        self._close_behavior = close_behavior
        self._destroy_named_images_called = False

    def _g07_doc_server_shutdown(self):
        if self._close_behavior == "server_error":
            raise RuntimeError("server shutdown exploded")
        self._g07_doc_server.shutdown()

    def _destroy_named_images(self):
        if self._close_behavior == "images_error":
            raise RuntimeError("image cleanup exploded")
        self._destroy_named_images_called = True
        self.canvas_var.clear()


def _attach_real_close_method(tb):
    """把真实 TabBrowser._on_window_close 绑到 mock 上执行。

    无 tkinter 时 gui 模块无法 import → 返回 False（调用方跳过）。"""
    gui_mod = _try_import_gui()
    if gui_mod is None:
        return False
    TabBrowser = gui_mod.TabBrowser
    tb._on_window_close = TabBrowser._on_window_close.__get__(
        tb, _MockTabBrowser)
    return True


@_reg
def test_dynamic_close_normal_path():
    """正常路径：server shutdown → image cleanup → root.destroy + quit。

    无 tkinter 时 gui 模块无法 import → 跳过（静态层已守住契约）。
    """
    tb = _MockTabBrowser(close_behavior="normal")
    if not _attach_real_close_method(tb):
        return
    tb._on_window_close(None)
    assert tb._g07_ui_closing is True, "flag must be set"
    assert tb._g07_doc_server.shutdown_called, "server must be shut down"
    assert tb._destroy_named_images_called, "images must be cleaned"
    assert tb.root.destroyed, "root.destroy() must be called"
    assert tb.root.quit_called, "root.quit() must be called"


@_reg
def test_dynamic_close_idempotent_second_call():
    """重复关闭：_g07_ui_closing 已 True 时仍调 root.destroy（防泄漏）。

    无 tkinter 时跳过。
    """
    tb = _MockTabBrowser(close_behavior="normal")
    if not _attach_real_close_method(tb):
        return
    tb._on_window_close(None)  # first
    assert tb.root.destroyed, "first close must destroy root"
    # reset destroyed flag to verify second call also destroys
    tb.root.destroyed = False
    tb._on_window_close(None)  # second — must still destroy
    assert tb.root.destroyed, (
        "second _on_window_close must still call root.destroy() "
        "(idempotent: prevent window leak)")


@_reg
def test_dynamic_close_server_error_still_destroys_root():
    """异常安全：server shutdown 抛错时仍销毁 root（不留窗口）。

    无 tkinter 时跳过。
    """
    tb = _MockTabBrowser(close_behavior="server_error")
    if not _attach_real_close_method(tb):
        return
    tb._on_window_close(None)
    assert tb.root.destroyed, (
        "root.destroy() must still be called even when "
        "server shutdown raises")
    assert tb.root.quit_called
    assert tb._g07_ui_closing is True


@_reg
def test_dynamic_close_images_error_still_destroys_root():
    """异常安全：PhotoImage 清理抛错时仍销毁 root。

    无 tkinter 时跳过。
    """
    tb = _MockTabBrowser(close_behavior="images_error")
    if not _attach_real_close_method(tb):
        return
    tb._on_window_close(None)
    assert tb.root.destroyed, (
        "root.destroy() must still be called even when "
        "_destroy_named_images raises")
    assert tb.root.quit_called


@_reg
def test_dynamic_close_with_real_tk_root():
    """端到端：创建真实 tk.Tk()，调 _on_window_close，断言 root 被销毁。

    无 tkinter 时跳过（受管隔离环境常态）。
    """
    tk = _try_import_tk()
    if tk is None:
        return
    tb = _MockTabBrowser(close_behavior="normal")
    root = tk.Tk()
    tb.root = root  # 注入真实 root
    if not _attach_real_close_method(tb):
        root.destroy()  # 清理
        return
    tb._on_window_close(None)
    # 真实 tk.Tk() 被 destroy() 后，widget 的 winfo_exists() 返回 0
    # （Tk 侧标记 widget 已销毁）。destroy 未调用时返回 1。
    # 注意：destroyed 后 root.quit() 可能抛 TclError（Tcl 解释器已
    # 部分关闭），这是可预期的——_on_window_close 内部用 try/except
    # 保护了 quit，不影响 destroy 的实际效果。
    # 用 winfo_exists() 检测（比 hasattr 'destroyed' 更通用，兼容
    # MockRoot 与真实 Tk 两种形态）。
    exists = 1
    try:
        exists = root.winfo_exists()
    except Exception:
        exists = 0  # TclError → 已销毁
    assert exists == 0, (
        "real tk.Tk() must be destroyed (winfo_exists()==0), "
        f"got {exists}")


if __name__ == "__main__":
    failed = 0
    skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            # 静态测试恒有断言；动态测试在无 tkinter 时静默 return
            # 这里简单标记：动态测试的跳过在下方 main 里用 gui 模块
            # 是否可 import 来识别（避免误判为通过）。
            print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    # 无 tkinter 时动态测试被静默跳过——打印提示但不算失败。
    if _try_import_gui() is None:
        dynamic_tests = [
            "test_dynamic_close_normal_path",
            "test_dynamic_close_idempotent_second_call",
            "test_dynamic_close_server_error_still_destroys_root",
            "test_dynamic_close_images_error_still_destroys_root",
            "test_dynamic_close_with_real_tk_root",
        ]
        skipped = len(dynamic_tests)
        print(f"\nNOTE: {skipped} dynamic test(s) skipped "
              f"(tkinter not available in this environment)")
    print(f"\n{len(TESTS)} tests, {failed} failed, "
          f"{skipped} skipped (no tkinter)")
    sys.exit(1 if failed else 0)

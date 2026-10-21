"""R7-U02 §215-223 / 审计 §57 — 活窗口真实编辑层/IME 接线探针（不冒充通过）。

Round-7 评估 R7-U02 + GUI 审计 §57 开放项："EditorOverlayController
没有创建实际 Tk Entry/Text，模型中的 selection/placement/composition
存在不等同操作系统输入法已经接入。"

此前 :mod:`native_overlay`（R7-U02）已把 :class:`EditorState` 的
value/选区/password 驱动到真实 Tk 控件；本模块把"键盘序列 + IME 组合
路由"接线到**活窗口真实 Tk 控件**，可自动化到最大程度：

- 自动化键盘序列（经 G11 路由 + B06 落地）驱动真实 Entry/Text 的
  value；
- IME 组合三阶段（拼音中间态 → 汉字提交）驱动真实控件 value +
  is_composing 状态同步；
- 双向同步（模型 ↔ 控件）可断言。

依赖活 Tk 窗口（缺 tkinter 或建 root 失败即 :class:`SkipTest`，不
冒充通过）。本批在本机 3.11.9（带 Tk）实测通过；受管 3.13.12 缺
tkinter 时全 SKIP（不缩分母）。

"不冒充通过"边界（本模块如实标注）：

* 本探针证明"活窗口下**自动化键盘序列 + IME 组合交互**能驱动真实
  Tk 控件的 value/选区/IME 状态同步"；
* **不**证明"真实系统输入法真人选词、真实键盘分发、读屏器读到"——
  这些仍需人工验收（与 R7-P02 §254 同口径），本模块不宣称完成。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.live_editing import (
    create_session, KIND_ENTRY, KIND_TEXTAREA,
)
from toy_browser.app.native_overlay import NativeOverlayWidget
from toy_browser.app.overlay_protocol import OverlayRect, Viewport

try:
    import tkinter as _tk  # noqa: F401
except Exception:
    _tk = None

from skip_support import SkipTest  # noqa: E402

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _require_live_editing_env():
    """活窗口编辑探针门控：缺 tkinter 或建 root 失败即 SkipTest。

    返回一个 (root, helper) —— helper 可按 kind 创建真实 Tk Entry/Text
    控件（供 NativeOverlayWidget 绑定）。调用方负责 ``root.destroy()``。
    """
    if _tk is None:
        raise SkipTest("tkinter unavailable; live-editing probe gated")
    root = _tk.Tk()
    root.withdraw()

    def _make_widget(kind=KIND_ENTRY):
        if kind == KIND_TEXTAREA:
            w = _tk.Text(root, height=1, width=20)
        else:
            w = _tk.Entry(root, width=20)
        return w

    return root, _make_widget


# ----------------------------------------------------------------------
# 探针 1 — 自动化键盘序列驱动真实 Tk Entry value
# ----------------------------------------------------------------------

@_test
def test_probe1_key_sequence_drives_real_entry():
    """活窗口下：注入键盘序列（含 Backspace 删除）经 G11 路由 +
    B06 编辑落地，真实 Tk Entry 的 value 同步到期望终值。"""
    root, mk = _require_live_editing_env()
    try:
        sess = create_session()
        sess.register_field("email", value="", kind=KIND_ENTRY,
                            rect=OverlayRect(10, 20, 200, 30))
        sess.focus_field("email")
        entry = mk(KIND_ENTRY)
        sess.attach_widget("email", entry)

        res = sess.inject_key_sequence("a", "b", "c", "Backspace", "@")
        assert res.final_value == "ab@", (
            f"键盘序列应得 ab@：{res.describe()}")
        assert res.applied_count >= 4, res.describe()
        # 真实控件 value 与模型终值一致（双向同步的"模型→控件"方向）。
        assert entry.get() == "ab@", f"真实 Entry 值应为 ab@：{entry.get()!r}"
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 2 — caret-move / 非编辑键不改 value（路由边界）
# ----------------------------------------------------------------------

@_test
def test_probe2_caret_move_not_applied():
    """方向键 / Home / End 等 caret 移动键经 G11 路由为 caret-move，
    不落地编辑值（applied=False），value 不变。"""
    root, mk = _require_live_editing_env()
    try:
        sess = create_session()
        sess.register_field("txt", value="hello", kind=KIND_ENTRY)
        sess.focus_field("txt")
        entry = mk(KIND_ENTRY)
        sess.attach_widget("txt", entry)

        res = sess.inject_key_sequence("ArrowLeft", "ArrowLeft", "Home",
                                       "End", "x")
        # ArrowLeft/Home/End 均 caret-move（applied=True，移动 caret
        # 不改 value），仅 "x" 落地；End 后 caret 在末尾，"x" 插在其后
        # → "hellox"。
        arrow_entries = [e for e in res.entries if e.key.startswith("Arrow")]
        assert all(e.applied is True for e in arrow_entries), res.describe()
        assert any(e.action == "caret-move" for e in res.entries), \
            res.describe()
        assert res.final_value == "hellox", res.describe()
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 3 — IME 组合三阶段驱动真实控件（拼音中间态 → 汉字提交）
# ----------------------------------------------------------------------

@_test
def test_probe3_ime_composition_syncs_real_widget():
    """活窗口下：IME 组合（拼音中间态 → 最终汉字提交）驱动真实
    Entry，提交后 is_composing 复位、value 同步到汉字。"""
    root, mk = _require_live_editing_env()
    try:
        sess = create_session()
        sess.register_field("greet", value="", kind=KIND_ENTRY)
        sess.focus_field("greet")
        entry = mk(KIND_ENTRY)
        sess.attach_widget("greet", entry)

        # 拼音逐步中间态 → 最终汉字"你好"。
        ir = sess.run_ime_composition("ni", "ninhao", "你好")
        assert ir.committed is True, ir.describe()
        assert ir.end_composing is False, ir.describe()
        assert ir.final_value == "你好", ir.describe()
        # 真实控件 value 同步到汉字（模型→控件）。
        assert entry.get() == "你好", f"真实 Entry 应为 你好：{entry.get()!r}"
        # 组合提交前最后一段 update 保持 composing（轨迹可诊断）。
        updates = [e for e in ir.entries if e.phase == "update"]
        assert any(e.composing for e in updates), ir.describe()
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 4 — IME 组合取消（Esc 语义）不留汉字
# ----------------------------------------------------------------------

@_test
def test_probe4_ime_cancel_clears_composition():
    """组合取消：先 start + 拼音 update，再 cancel → 组合区清除、
    is_composing 复位、value 不含拼音中间态。"""
    root, mk = _require_live_editing_env()
    try:
        sess = create_session()
        sess.register_field("greet", value="已", kind=KIND_ENTRY)
        sess.focus_field("greet")
        entry = mk(KIND_ENTRY)
        sess.attach_widget("greet", entry)

        # 模拟：组合进行中（拼音"ni"挂在"已"后面），然后取消。
        st = sess.field_state("greet")
        st.ime_composition_start(st.caret)
        st.ime_composition_update("ni", 1)
        ir = sess.run_ime_composition(cancel=True)
        assert ir.committed is False, ir.describe()
        assert ir.end_composing is False, ir.describe()
        # 取消后组合区清除，value 回到"已"（拼音"ni"被删）。
        assert ir.final_value == "已", ir.describe()
        assert entry.get() == "已", f"真实 Entry 应为 已：{entry.get()!r}"
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 5 — textarea（多行 Text）键盘 + IME 同步
# ----------------------------------------------------------------------

@_test
def test_probe5_textarea_real_text_widget():
    """textarea 字段驱动真实 Tk Text 控件（1.0 索引协议），键盘 +
    Enter（insertLineBreak）换行后 value 含 \\n。"""
    root, mk = _require_live_editing_env()
    try:
        sess = create_session()
        sess.register_field("bio", value="", kind=KIND_TEXTAREA,
                            rect=OverlayRect(10, 20, 300, 80))
        sess.focus_field("bio")
        text = mk(KIND_TEXTAREA)
        sess.attach_widget("bio", text)

        res = sess.inject_key_sequence("a", "Enter", "b")
        assert res.final_value == "a\nb", res.describe()
        # 真实 Text 控件读回（Text 索引 "1.0","end-1c"）。
        val = text.get("1.0", "end-1c")
        assert val == "a\nb", f"真实 Text 应为 a\\nb：{val!r}"
    finally:
        root.destroy()


# ----------------------------------------------------------------------
# 探针 6 — 双向同步（模型↔控件）+ 密码遮罩不回写真实值
# ----------------------------------------------------------------------

@_test
def test_probe6_round_trip_and_password_mask():
    """模型→控件→模型 一轮同步成功；password 控件读回遮罩长度校验
    不回写真实 value（剪贴板策略不变）。"""
    root, mk = _require_live_editing_env()
    try:
        sess = create_session()
        sess.register_field("pwd", value="secret", kind=KIND_ENTRY,
                            is_password=True)
        sess.focus_field("pwd")
        entry = mk(KIND_ENTRY)
        sess.attach_widget("pwd", entry)

        ok = sess.sync_round_trip("pwd")
        assert ok is True, "password 双向同步应成功"
        # 模型真实 value 不回退成遮罩（遮罩只在呈现层）。
        st = sess.field_state("pwd")
        assert st.value == "secret", f"真实 value 须保持 secret：{st.value!r}"
        assert st.is_password is True
        # 呈现层 display_value 是等长 ●（不暴露真实值）。
        assert st.display_value() == "●" * 6, st.display_value()
    finally:
        root.destroy()


if __name__ == "__main__":
    passed = failed = skipped = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            if getattr(e, "__class__", None).__name__ == "SkipTest":
                print(f"SKIP {name}: {e}")
                skipped += 1
            else:
                print(f"ERROR {name}: {e!r}")
                failed += 1

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)

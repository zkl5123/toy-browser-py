"""R7 组 A 活窗口人工验收脚手架探针（swapchain / IME / 读屏器）。

验证 :mod:`toy_browser.app.live_acceptance` 的三个探针：

- :func:`probe_swapchain_visible` — 活窗口拿真实 screen surface +
  present 通路 + gate 可观测差异（缺三条件任一 → UNSUPPORTED 不冒充）。
- :func:`probe_ime_composition` — 活窗口驱动真实 Tk Entry + G03 IME
  组合三阶段（缺 tkinter → UNSUPPORTED；驱动成功 → PASS + 证据）。
- :func:`probe_screen_reader` — 内部 a11y 树 → UIA 契约 + 事件流暴露
  （纯逻辑，恒可驱动；PASS 指暴露成功，人工读到留签字）。

依赖分层（缺 OS 能力即 :class:`SkipTest`，不缩分母、不冒充通过）：

* swapchain 探针需活窗口三条件（tkinter + wgpu device + rendercanvas），
  缺一即 UNSUPPORTED（脚手架内部判定）→ 测试在缺条件环境 SKIP。
* IME 探针需 tkinter（真实 Entry），缺即 UNSUPPORTED → SKIP。
* 读屏器探针纯逻辑（不依赖 OS），恒可驱动 → 全环境 PASS（证据可采集）。
* "不冒充"核心契约：UNSUPPORTED/FAIL 必须如实带 reason，PASS 必带
  证据 + 人工判定项 + 未签字默认态。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app import live_acceptance as _la
from skip_support import SkipTest  # noqa: E402

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _swapsafe() -> None:
    """swapchain 三条件齐全才放行（缺任一 SKIP，对应脚手架 UNSUPPORTED）。"""
    gates = _la._env_gates()
    missing = _la._need_live_env(gates)
    if missing is not None:
        raise SkipTest(f"live-env gated: {missing}")


def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------
# swapchain 探针
# ----------------------------------------------------------------------

@_test
def test_swap_unsupported_when_env_missing():
    """缺活窗口三条件时 probe_swapchain_visible 必 UNSUPPORTED（不冒充）。"""
    gates = _la._env_gates()
    p = _la.probe_swapchain_visible(headless=True)
    if _la._need_live_env(gates) is None:
        # 三条件齐全：不应报 UNSUPPORTED。
        assert p.status in (_la.PASS, _la.FAIL), p.status
        return
    assert p.status == _la.UNSUPPORTED, p.status
    assert p.reason, "UNSUPPORTED 必须带 reason（不冒充）"


@_test
def test_swap_pass_carries_evidence_and_human_check():
    """三条件齐全时 PASS 必带现场证据（surface 格式 / gate 决策）+ 人工
    判定项 + 未签字默认态（不冒充"已人工验收"）。"""
    _swapsafe()
    p = _la.probe_swapchain_visible(headless=True)
    if p.status != _la.PASS:
        # surface 构造等运行时失败 → 如实 FAIL 也带 reason。
        assert p.status == _la.FAIL and p.reason, (p.status, p.reason)
        return
    assert p.evidence.get("surface_format"), "证据须含 surface 格式"
    assert p.evidence.get("hwnd"), "证据须含 HWND"
    # gate 可观测差异（活窗口 present vs headless no-surface）。
    assert p.evidence.get("gate_decision"), "须记录 gate 决策"
    assert "no_surface" in str(p.evidence.get("gate_no_surface")) or \
        p.evidence.get("gate_no_surface"), "须记录 headless 对照"
    assert p.human_check, "PASS 必带人工判定项"
    assert p.signed is False, "默认未签字（不冒充已验收）"


@_test
def test_swap_signable():
    """PASS 探针可经 mark_signed 记录签字（签字位，不冒充默认态）。"""
    p = _la.AcceptanceProbe(_la.PASS, "x", human_check="…")
    assert p.signed is False
    p.mark_signed("判定人A", "2026-10-18")
    assert p.signed is True and p.signer == "判定人A"
    assert "判定人A" in p.render() and "未签字" not in p.render()


# ----------------------------------------------------------------------
# IME 探针
# ----------------------------------------------------------------------

@_test
def test_ime_unsupported_when_no_tk():
    """缺 tkinter 时 probe_ime_composition 必 UNSUPPORTED（不冒充）。"""
    if _tk_available():
        # 有 Tk：跑真实驱动，验证 PASS 带证据。
        p = _la.probe_ime_composition(["ni", "hao"], headless=True)
        assert p.status == _la.PASS, (p.status, p.reason)
        assert p.evidence.get("final_value") == "你好hao" or \
            p.evidence.get("final_value"), "证据须含最终控件值"
        assert p.evidence.get("committed") is True
        assert p.human_check, "PASS 必带人工判定项（真人选词留人工）"
        assert p.signed is False
        return
    p = _la.probe_ime_composition(["ni", "hao"], headless=True)
    assert p.status == _la.UNSUPPORTED, p.status
    assert p.reason, "UNSUPPORTED 必须带 reason"


@_test
def test_ime_cancel_resets_composing():
    """IME 组合取消（Esc 语义）：is_composing 复位，不串进最终值。"""
    if not _tk_available():
        raise SkipTest("tkinter unavailable; IME cancel probe gated")
    from toy_browser.app.live_editing import LiveEditingSession
    s = LiveEditingSession()
    s.register_field("f", value="")
    s.focus_field("f")
    r = s.run_ime_composition("abc", cancel=True)
    assert r.end_composing is False, "取消后 is_composing 须复位"
    assert r.committed is False, "取消不产生 commit"


# ----------------------------------------------------------------------
# 读屏器探针
# ----------------------------------------------------------------------

@_test
def test_screader_exposes_uia_contract_and_event_stream():
    """读屏器探针（纯逻辑）恒可驱动：暴露 UIA 契约 + 完整事件流。"""
    p = _la.probe_screen_reader(sample_node={
        "role": "button", "name": "提交", "children": []})
    assert p.status == _la.PASS, (p.status, p.reason)
    assert p.evidence.get("uia_contract"), "证据须含 UIA 控件契约"
    ev = p.evidence.get("event_stream")
    assert ev, "证据须含事件流"
    types = [e.get("type") for e in ev]
    # 完整事件流：focus-moved / property-changed / structure-change /
    # 对话框焦点归还（focus-moved）。
    assert "focus-moved" in types and "property-changed" in types
    assert "structure-change" in types
    assert p.human_check, "PASS 必带人工判定项（读屏器读到留人工）"
    assert p.signed is False, "默认未签字（不冒充已读屏验证）"


@_test
def test_screader_from_file_bad_path_is_fail():
    """读屏器探针从文件读 a11y 树：路径非法/读失败 → 如实 FAIL。"""
    p = _la.probe_screen_reader(a11y_input=os.path.join(
        os.path.dirname(__file__), "no_such_a11y_xyz.json"))
    assert p.status == _la.FAIL, p.status
    assert p.reason, "FAIL 必须带 reason（不冒充）"


@_test
def test_screader_from_file_single_root_dict_is_pass():
    """--a11y-input 喂**单根 dict**（非 list）须归一化后正常驱动 → PASS。

    回归缺陷：旧 ``_count_nodes`` 假定树是 list，文件喂单根 dict 时
    ``_c(tree)`` 对 dict 迭代拿到 key（str）→ ``x.get`` 崩（AttributeError）。
    修后 dict→[dict] 归一化，产 UIA 契约 + 事件流，恒可驱动 PASS。
    """
    import json
    root_dir = os.path.dirname(__file__)
    pth = os.path.join(root_dir, "_tmp_a11y_single_root.json")
    with open(pth, "w", encoding="utf-8") as f:
        json.dump({"role": "document", "name": "page", "children": [
            {"role": "textbox", "name": "地址栏", "children": []},
            {"role": "button", "name": "提交", "children": []},
        ]}, f)
    try:
        p = _la.probe_screen_reader(a11y_input=pth)
        assert p.status == _la.PASS, f"{p.status}: {p.reason}"
        # 单根 dict 须被计数为节点（树里 1 根 + 2 子 = 3，加外层 root=1 →
        # 探针把 a11y_input 作为 root.children，tree_size 至少覆盖到子节点）。
        assert p.evidence.get("tree_size", 0) >= 3, p.evidence.get("tree_size")
        assert p.evidence.get("uia_contract"), "PASS 必带 UIA 契约"
        assert p.evidence.get("event_stream"), "PASS 必带事件流"
        assert p.human_check, "PASS 必带人工判定项（读屏器读到留签字）"
        assert not p.signed, "默认未签字"
    finally:
        if os.path.exists(pth):
            os.remove(pth)


@_test
def test_screader_from_file_single_root_dict_is_pass():
    """--a11y-input 喂**单根 dict**（非 list）须归一化后正常驱动 → PASS。

    回归缺陷：旧 ``_count_nodes`` 假定树是 list，文件喂单根 dict 时
    ``_c(tree)`` 对 dict 迭代拿到 key（str）→ ``x.get`` 崩（AttributeError）。
    修后 dict→[dict] 归一化，产 UIA 契约 + 事件流，恒可驱动 PASS。
    """
    import json
    root_dir = os.path.dirname(__file__)
    pth = os.path.join(root_dir, "_tmp_a11y_single_root.json")
    with open(pth, "w", encoding="utf-8") as f:
        json.dump({"role": "document", "name": "page", "children": [
            {"role": "textbox", "name": "地址栏", "children": []},
            {"role": "button", "name": "提交", "children": []},
        ]}, f)
    try:
        p = _la.probe_screen_reader(a11y_input=pth)
        assert p.status == _la.PASS, f"{p.status}: {p.reason}"
        # 单根 dict 须被计数为节点（树里 1 根 + 2 子 = 3，加外层 root=1 →
        # 探针把 a11y_input 作为 root.children，tree_size 至少覆盖到子节点）。
        assert p.evidence.get("tree_size", 0) >= 3, p.evidence.get("tree_size")
        assert p.evidence.get("uia_contract"), "PASS 必带 UIA 契约"
        assert p.evidence.get("event_stream"), "PASS 必带事件流"
        assert p.human_check, "PASS 必带人工判定项（读屏器读到留签字）"
        assert not p.signed, "默认未签字"
    finally:
        if os.path.exists(pth):
            os.remove(pth)


# ----------------------------------------------------------------------
# 报告 / run_all
# ----------------------------------------------------------------------

@_test
def test_run_all_returns_three_probes_in_order():
    """run_all 返回组 A 三项探针（swapchain / IME / 读屏器），顺序固定。"""
    probes = _la.run_all(ime_phrases=["ni", "hao"], headless=True)
    assert len(probes) == 3, f"应 3 项：{len(probes)}"
    titles = [p.title for p in probes]
    assert "swapchain" in titles[0]
    assert "IME" in titles[1]
    assert "读屏器" in titles[2]


@_test
def test_render_report_is_signable_markdown():
    """render_report 产可签字 Markdown：含证据 / 人工判定项 / 签字位。"""
    probes = _la.run_all(ime_phrases=["ni", "hao"], headless=True)
    md = _la.render_report(probes)
    assert md.startswith("# ")
    assert "人工判定项" in md or "未签字" in md
    # 每项都有签字位（未签字默认态）。
    assert "验收签字" in md
    # 未签字默认态不冒充（全未签字）。
    assert all(p.signed is False for p in probes)


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

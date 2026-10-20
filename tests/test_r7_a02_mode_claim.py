"""R7-A02 第 6 点 — 文档工作模式 + 能力声明真实性校验回归。

被测：``toy_browser/app/doc_process_mode.py``（新建）
+ ``toy_browser/app/gui.py`` 的 ``_r7_a02_doc_mode`` /
``_r7_a02_mode_describe`` 接线。

round7 实施方案 R7-A02 第 6 点：

> 在工作进程未完全接通前保留显式单进程调试模式，但**默认模式的
> 性能/安全声明必须真实**。

审计 R7-A02 证据（本模块要回答的原话）：

> "现有 worker 存在且 spawn，但默认导航仍同步，worker handle 主要
> 返回 URL/版本状态；**不能据名称宣称已多核渲染**。"

核心判据不是"有没有 worker 这个词"，而是**文档工作（解析 / 样式 /
布局 / 绘制）在哪执行**。本测试锁定三条不变量：

1. **默认模式如实为 ``single_debug``**（默认导航仍同步 + worker
   不做真实文档工作），不得因存在 spawn 而升档。
2. **``worker_ipc`` 下"已多核渲染"声明判为不真实**（管道通了 ≠
   活搬到 worker）——审计点名的越级声明闸门。
3. **显式单进程调试模式可观测、可断言**（主动 opt-in 与被动回退
   可区分）。

两层设计（"不冒充通过"）：

* 静态层——源码契约字符串断言（gui 接线点存在）。
* 动态层——真实调用 detect_doc_work_mode / CapabilityClaim /
  SingleProcessDebugMode / gui 探测方法（gui 用 ``__new__`` 构造，
  不跑 Tk 主循环，仅验证纯逻辑探测契约）。

**不冒充通过**：本模块只做**模式判定与声明校验**。真实多核渲染
（worker 持 DocumentSession 执行真实解析 / 样式 / 布局 / 绘制）
**未接通**，故默认与 worker_ipc 下 multi_core_rendering 恒 False；
测试断言这一"如实为假"，不制造"看起来已多核"的绿灯。

无 OS / wgpu 依赖即可回归（P13 不崩）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.doc_process_mode import (
    MODE_SINGLE_DEBUG, MODE_WORKER_IPC, MODE_WORKER_FULL,
    VALID_DOC_WORK_MODES, _MODE_CAPABILITIES,
    DocWorkMode, detect_doc_work_mode,
    CapabilityClaim, claim_multi_core_rendering,
    CLAIM_MULTI_CORE_RENDERING,
    SingleProcessDebugMode,
)

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read(path):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(base, path), encoding="utf-8").read()


# ----------------------------------------------------------------------
# 静态层
# ----------------------------------------------------------------------

@_test
def test_static_module_contract():
    src = _read(os.path.join("toy_browser", "app", "doc_process_mode.py"))
    for sym in ("MODE_SINGLE_DEBUG", "MODE_WORKER_IPC", "MODE_WORKER_FULL",
                "detect_doc_work_mode", "CapabilityClaim",
                "claim_multi_core_rendering", "SingleProcessDebugMode",
                "is_truthful", "effective_mode"):
        assert sym in src, f"doc_process_mode.py 缺 {sym} 契约"


@_test
def test_static_gui_mode_wiring_contract():
    src = _read(os.path.join("toy_browser", "app", "gui.py"))
    assert "_r7_a02_doc_mode" in src, "gui 缺 _r7_a02_doc_mode 探测"
    assert "_r7_a02_mode_describe" in src, "gui 缺 _r7_a02_mode_describe"
    assert "_r7_a02_debug_mode" in src, "gui 缺显式单进程调试模式字段"
    # 关键：探测须据 worker 真实文档工作状态判定（R7-A02 第 2 点已
    # 接上 worker 真实解析 / 样式 / 布局 / 绘制路径），**不**为"好看"
    # 写死 True 也**不**写死 False——经 has_done_doc_work() 探测后
    # 透传。写死 False 会在 worker 真实做了文档工作后误判 WORKER_IPC
    # （不冒充失败的镜像：把"做了"谎报成"没做"）。
    assert "has_done_doc_work" in src, (
        "gui 探测须经 has_done_doc_work() 据 worker 真实文档工作状态"
        "判定 worker_does_doc_work，不得写死 True/False")
    assert "navigation_is_sync=True" in src, (
        "gui 探测必须如实标记默认导航仍同步")


@_test
def test_static_modes_and_capabilities_defined():
    """三态齐全 + 每态能力表定义（未知能力保守按 False）。"""
    assert VALID_DOC_WORK_MODES == (MODE_SINGLE_DEBUG, MODE_WORKER_IPC,
                                    MODE_WORKER_FULL), "三态应齐全"
    for m in VALID_DOC_WORK_MODES:
        assert m in _MODE_CAPABILITIES, f"{m} 缺能力表"
        assert "multi_core_rendering" in _MODE_CAPABILITIES[m], \
            f"{m} 能力表缺 multi_core_rendering"
    # 只有 WORKER_FULL 才真实具备多核渲染（不夸大）。
    assert _MODE_CAPABILITIES[MODE_SINGLE_DEBUG]["multi_core_rendering"] is False
    assert _MODE_CAPABILITIES[MODE_WORKER_IPC]["multi_core_rendering"] is False
    assert _MODE_CAPABILITIES[MODE_WORKER_FULL]["multi_core_rendering"] is True


# ----------------------------------------------------------------------
# 动态层
# ----------------------------------------------------------------------

@_test
def test_dynamic_default_mode_is_single_debug():
    """不变量 1：默认模式如实为 single_debug（导航同步 + worker 不做活）。"""
    m = detect_doc_work_mode()
    assert m.mode == MODE_SINGLE_DEBUG, f"默认应为 single_debug：{m.mode}"
    assert m.is_single_debug is True, "is_single_debug 应为 True"
    assert m.is_worker_full is False, "默认不得为 worker_full"
    assert m.navigation_is_sync is True, "默认导航同步应如实记录"
    assert m.worker_does_doc_work is False, "worker 做真实文档工作应为 False"
    assert m.reason, "应给出判定依据（可观测）"
    assert m.supports("multi_core_rendering") is False, \
        "single_debug 不得具备多核渲染"


@_test
def test_dynamic_spawned_only_is_worker_ipc_not_multi_core():
    """不变量 2：spawn 了但 worker 只做 IPC → worker_ipc，非多核渲染。"""
    m = detect_doc_work_mode(worker_spawned=True)
    assert m.mode == MODE_WORKER_IPC, f"应为 worker_ipc：{m.mode}"
    assert m.worker_spawned is True, "spawned 应记录为 True"
    assert m.worker_does_doc_work is False, "worker 不做真实文档工作"
    # 审计核心：管道通了 ≠ 多核渲染
    assert m.supports("multi_core_rendering") is False, \
        "worker_ipc 不得具备多核渲染（IPC 通 ≠ 活搬到 worker）"
    assert m.supports("frame_ipc") is True, "worker_ipc 应具备帧 IPC"
    assert m.supports("worker_isolation") is True, "worker_ipc 应有进程隔离"


@_test
def test_dynamic_worker_full_only_when_doc_work_in_worker():
    """只有 worker 真做文档工作（worker_does_doc_work）才升到 full。"""
    m = detect_doc_work_mode(worker_does_doc_work=True, worker_spawned=True)
    assert m.mode == MODE_WORKER_FULL, f"应为 worker_full：{m.mode}"
    assert m.is_worker_full is True
    assert m.supports("multi_core_rendering") is True, \
        "worker_full 才具备多核渲染"
    assert m.supports("ui_nonblocking_navigation") is True


@_test
def test_dynamic_multi_core_claim_untruthful_by_default():
    """不变量 2 核心：默认与 worker_ipc 下"已多核渲染"声明不真实。"""
    for m in (detect_doc_work_mode(),
              detect_doc_work_mode(worker_spawned=True)):
        c = claim_multi_core_rendering(m)
        assert c.is_truthful() is False, (
            f"模式 {m.mode} 下「已多核渲染」声明必须判为不真实")
        reason = c.untruthful_reason()
        assert reason, "不真实时必须给出原因（可诊断）"
        assert "多核渲染" in reason, f"原因应点明多核渲染：{reason}"


@_test
def test_dynamic_multi_core_claim_truthful_only_on_worker_full():
    """worker_full 下"已多核渲染"声明才真实（当前默认不通此路）。"""
    m = detect_doc_work_mode(worker_does_doc_work=True, worker_spawned=True)
    c = claim_multi_core_rendering(m)
    assert c.is_truthful() is True, "worker_full 下声明应真实"
    assert c.untruthful_reason() == "", "真实时原因应为空"
    assert c.name == CLAIM_MULTI_CORE_RENDERING, "声明名应为审计点名词"


@_test
def test_dynamic_claim_unknown_capability_conservative():
    """未知能力 / 未知模式保守按不真实处理（不承认没定义过的声明）。"""
    c1 = CapabilityClaim(name="x", capability="never_defined",
                         mode=MODE_WORKER_FULL)
    assert c1.is_truthful() is False, "未知能力应保守判不真实"
    c2 = CapabilityClaim(name="y", capability="multi_core_rendering",
                         mode="bogus_mode")
    assert c2.is_truthful() is False, "未知模式应保守判不真实"
    assert "未知模式" in c2.untruthful_reason(), "应说明未知模式"


@_test
def test_dynamic_worker_isolation_claim_per_mode():
    """进程隔离声明逐态校验（single_debug 无隔离，worker_* 有）。"""
    c_single = CapabilityClaim(name="iso", capability="worker_isolation",
                               mode=MODE_SINGLE_DEBUG)
    assert c_single.is_truthful() is False, "single_debug 无进程隔离"
    for mode in (MODE_WORKER_IPC, MODE_WORKER_FULL):
        c = CapabilityClaim(name="iso", capability="worker_isolation",
                            mode=mode)
        assert c.is_truthful() is True, f"{mode} 应有进程隔离"


@_test
def test_dynamic_debug_mode_off_by_default():
    """不变量 3：显式单进程调试模式默认关闭（可观测）。"""
    sp = SingleProcessDebugMode()
    assert sp.enabled is False, "默认不应开启"
    assert sp.is_debug_single_process is False, "默认不应处于单进程态"
    assert "off" in sp.describe(), f"诊断应显示 off：{sp.describe()}"


@_test
def test_dynamic_debug_mode_explicit_optin_forces_single():
    """显式开启 → 强制 single_debug（即使探测到 worker_full 也不升档）。"""
    sp = SingleProcessDebugMode()
    sp.enable(reason="step-through layout")
    assert sp.enabled is True and sp.is_debug_single_process is True
    detected = detect_doc_work_mode(worker_does_doc_work=True,
                                    worker_spawned=True)
    assert detected.mode == MODE_WORKER_FULL, "探测本身应是 worker_full"
    eff = sp.effective_mode(detected)
    assert eff.mode == MODE_SINGLE_DEBUG, (
        f"显式调试应强制 single_debug：{eff.mode}")
    assert "显式单进程调试" in eff.reason, f"reason 应标明主动调试：{eff.reason}"
    assert sp.describe().startswith("single-process-debug(active"), \
        f"诊断应显示 active：{sp.describe()}"


@_test
def test_dynamic_auto_fallback_distinguishable_from_debug():
    """被动回退（spawn 失败）与主动调试**可区分**（诊断不混淆）。"""
    sp = SingleProcessDebugMode()
    sp.mark_auto_fallback("spawn failed")
    assert sp.was_auto_fallback is True, "应标记被动回退"
    assert sp.enabled is False, "被动回退不等于主动开启调试"
    assert sp.is_debug_single_process is True, "仍属单进程态"
    desc = sp.describe()
    assert "auto-fallback" in desc, f"诊断应显示 auto-fallback：{desc}"
    assert "single-process-debug(active)" not in desc, "不得冒充主动调试"
    detected = detect_doc_work_mode(worker_does_doc_work=True,
                                    worker_spawned=True)
    eff = sp.effective_mode(detected)
    assert eff.mode == MODE_SINGLE_DEBUG, "回退态应为 single_debug"
    assert "自动回退" in eff.reason, f"reason 应标明回退：{eff.reason}"


@_test
def test_dynamic_debug_mode_disable_restores_detected():
    """关闭显式调试 → 回到按真实接线探测的模式（不残留强制态）。"""
    sp = SingleProcessDebugMode()
    sp.enable(reason="debug")
    sp.disable()
    assert sp.enabled is False, "disable 后应关闭"
    detected = detect_doc_work_mode(worker_spawned=True)
    eff = sp.effective_mode(detected)
    assert eff.mode == detected.mode, (
        f"关闭后应回到探测模式 {detected.mode}：{eff.mode}")


@_test
def test_dynamic_mode_rank_ordering():
    """模式档位有序（single < ipc < full），供声明校验与排序。"""
    s = DocWorkMode(mode=MODE_SINGLE_DEBUG)
    i = DocWorkMode(mode=MODE_WORKER_IPC)
    f = DocWorkMode(mode=MODE_WORKER_FULL)
    assert s.rank < i.rank < f.rank, f"档位应递增：{s.rank},{i.rank},{f.rank}"


@_test
def test_dynamic_mode_describe_contains_evidence():
    """诊断串含判定证据（同步标记 / worker 是否做活 / spawn 状态）。"""
    m = detect_doc_work_mode(worker_spawned=True)
    d = m.describe()
    for frag in ("mode=worker_ipc", "nav_sync=True",
                 "worker_doc_work=False", "spawned=True"):
        assert frag in d, f"诊断串缺 {frag}：{d}"


# ----------------------------------------------------------------------
# gui 接线动态层（需 tkinter；缺失时跳过而非伪装通过）
# ----------------------------------------------------------------------

@_test
def test_dynamic_gui_doc_mode_wiring():
    """gui._r7_a02_doc_mode 探测契约（需 tkinter，缺失则 SKIP）。

    断言 gui 探测如实返回 single_debug / worker_ipc（取决于真实
    spawn 状态），且**永不**返回 worker_full（现有 worker 不做真实
    文档工作）。
    """
    try:
        import tkinter  # noqa: F401
    except ImportError:
        from skip_support import SkipTest
        raise SkipTest("tkinter 不可用，跳过 gui 接线动态断言")
    import types
    from toy_browser.app import gui as _gui
    cls = None
    for nm in dir(_gui):
        o = getattr(_gui, nm)
        if isinstance(o, type) and hasattr(o, "_r7_a02_doc_mode"):
            cls = o
            break
    assert cls is not None, "gui 缺 _r7_a02_doc_mode 所属类"
    obj = cls.__new__(cls)          # 不跑 __init__（不开 Tk 主循环）
    for spawned in (False, True):
        obj._g07_doc_server = types.SimpleNamespace(
            is_spawned=lambda v=spawned: v)
        obj._r7_a02_debug_mode = None
        m = obj._r7_a02_doc_mode()
        assert m is not None, "探测不应返回 None"
        assert m.mode in (MODE_SINGLE_DEBUG, MODE_WORKER_IPC), (
            f"gui 探测不得返回 worker_full（worker 不做真实文档工作）：{m.mode}")
        assert m.worker_does_doc_work is False, "必须如实为 False"
        assert m.navigation_is_sync is True, "默认导航同步必须如实标记"
        expected = MODE_WORKER_IPC if spawned else MODE_SINGLE_DEBUG
        assert m.mode == expected, f"spawned={spawned} 应为 {expected}：{m.mode}"
        d = obj._r7_a02_mode_describe()
        assert d.startswith("r7_a02_mode:"), f"诊断串格式不对：{d}"
        # 关键：诊断串里"已多核渲染"声明必须标 UNTRUTHFUL
        assert "UNTRUTHFUL" in d, (
            f"诊断串必须标记多核渲染声明为不真实：{d}")


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

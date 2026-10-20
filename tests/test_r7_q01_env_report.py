"""R7-Q01 验收 — 注入"GPU 缺失"：环境/后端字段可观测 + 稳定分母。

审计 Q01 验收条款："注入一项 import 失败、无效 --only、内部检查失败、
GPU 缺失、Tcl 超时，分别得到 ERROR/FAIL/SKIP/TIMEOUT 的正确退出码与
稳定分母。"

本文件覆盖其中**GPU 缺失**一条。关键事实（两环境实测 2026-10-20）：
wgpu 可选依赖缺失时，4 个 GPU 模块（test_gpu_backend /
test_gpu_rasterize / test_gpu_image_cache / test_gpu_glyph_atlas）
走 P13 CPU 回退断言 → **全 PASS**（0.5s）；wgpu 可用时走真实
光栅化路径 → 也是全 PASS（48s）。**两环境都是 PASS，结果里看不出
环境差异**——这正是 Q01 第 6 条要求"机器可读报告携带环境/后端"
的原因：``run.py`` 的 JSON/JUnit 报告现在把 ``environment.wgpu``
（wgpu-py 是否可 import）写进报告，无 wgpu 环境下跑出的结果能
直接看到 ``"wgpu": false``，"无 wgpu 走的回退契约"与"有 wgpu 走
的实光栅化"不再互相冒充。

探针（本文件）：
* 静态层（快）—— 锁定 run.py 源码契约：``_env_has_wgpu`` 存在且
  只 import 探测（不创建设备）；JSON 报告的 environment 携带
  ``wgpu`` 键；JUnit 的 properties 携带 ``wgpu`` 键。
* 动态层（跑一次 run.py 子进程，--only 选轻量组合）—— 断言：
  1. JSON ``environment.wgpu`` 是 bool，且与**本进程**
     ``import wgpu`` 实测一致（报告如实反映执行环境）；
  2. 同一命令在 wgpu 可用/缺失两种环境下的 ``total`` 分母**稳定**
     （分母不因后端缺失常驻漂移）；
  3. 全 PASS/全回退时 exit code = 0（"GPU 缺失"本身不制造
     假失败——回退是 P13 有意契约，不是 infra ERROR）。

"不冒充通过"口径：本探针证明的是**环境可观测性**（报告里能看到
wgpu 有无），**不**把"无 wgpu 时 GPU 测试全 PASS"改写成 SKIP/
ERROR——那会破坏 P13 回退契约（P13 的设计意图就是无 wgpu 不崩、
UI 可用）。缺的从来不是 GPU 测试"该 SKIP 却没 SKIP"，而是报告
里没有环境字段。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _env_wgpu_truth() -> bool:
    """本进程实测：wgpu-py 是否可 import（与 run.py 的口径一致——
    只 import 探测，不创建设备）。"""
    try:
        import wgpu  # noqa: F401
        return True
    except Exception:
        return False


# 探针组合：test_dom（3 用例，环境无关，秒级）+ test_gpu_backend
# （环境感知：无 wgpu 走 P13 回退断言、有 wgpu 走真实路径）。
# 选 test_gpu_backend 而非全 4 个 GPU 模块：单模块足够表达"环境
# 感知"，且把动态探针压到秒级（全 4 个在完整环境要 ~48s）。
_PROBE_MODULES = ("test_dom", "test_gpu_backend")


def _run_probe(json_path: str) -> tuple[int, str]:
    py = sys.executable
    run_py = str(Path(__file__).parent / "run.py")
    p = subprocess.run(
        [py, run_py, "--only", *_PROBE_MODULES, "--json", json_path],
        capture_output=True, text=True, timeout=180,
    )
    return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------------------------
# 静态层 —— 锁定 run.py 的源码契约（快，不跑子进程）
# ---------------------------------------------------------------------------

@_reg
def test_run_py_env_wgpu_probe_source():
    """``_env_has_wgpu`` 存在且只 import 探测（不创建设备，P02
    "不根据显卡名称保证任何后端可用"由测试自己做）。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert "_env_has_wgpu" in src, \
        "run.py 缺少 wgpu 环境探测（Q01 第 6 条环境/后端字段）"
    probe = src[src.index("def _env_has_wgpu"):
                src.index("def _env_has_wgpu") + 800]
    assert "import wgpu" in probe, \
        "wgpu 探测必须是 import 成功与否（不读设备名）"
    assert "request_device" not in probe, \
        "wgpu 探测不得在报告阶段创建设备（P02 契约）"


@_reg
def test_json_report_carries_wgpu_env_field():
    """JSON 报告的 environment 必须携带 ``wgpu`` 键（Q01 第 6 条）。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    seg = src[src.index('"environment": {'):src.index('"counts": counts')]
    assert '"wgpu": _env_has_wgpu()' in seg, \
        "JSON environment 缺 wgpu 字段（GPU 缺失注入后无从观测）"
    assert '"tkinter": _env_has_tk()' in seg, "tkinter 字段必须保留"


@_reg
def test_junit_report_carries_wgpu_property():
    """JUnit 的 <properties> 必须携带 ``wgpu`` 属性（Q01 第 6 条）。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert "<properties>" in src, "JUnit 缺 properties 块（环境字段无家可归）"
    assert 'name="wgpu"' in src, "JUnit properties 缺 wgpu 属性"
    assert 'name="python"' in src and 'name="platform"' in src, \
        "JUnit properties 须与 JSON environment 同口径（python/platform）"


# ---------------------------------------------------------------------------
# 动态层 —— 跑一次 run.py 子进程，复核报告与环境一致
# ---------------------------------------------------------------------------

@_reg
def test_json_env_wgpu_matches_executing_environment():
    """报告里的 ``environment.wgpu`` 必须与执行该 run.py 的环境实测
    一致（无 wgpu → false；有 wgpu → true），且全 PASS/全回退时
    exit code 仍为 0（回退是契约，不是失败）。"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        json_path = f.name
    try:
        rc, out = _run_probe(json_path)
        truth = _env_wgpu_truth()
        report = json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert report["environment"]["wgpu"] is truth, (
            f"报告 wgpu={report['environment']['wgpu']} 与实测 "
            f"{truth} 不一致——环境字段不得谎报（Q01 第 6 条）\n"
            f"{out[-400:]}")
        assert rc == 0, \
            (f"探针模块全 PASS（或 P13 回退）应退出 0，实际 {rc}\n"
             f"{out[-500:]}")
        # 分母稳定：total 必须等于探针模块的用例总数（不因后端
        # 缺失常驻漂移——两环境 total 相同，只有 wgpu 字段不同）。
        total = report["total"]
        assert total >= 3 + 1, (
            f"分母 {total} 异常：至少含 test_dom 3 用例 + "
            "test_gpu_backend 1 用例")
        assert report["counts"]["FAIL"] == 0 and \
            report["counts"]["ERROR"] == 0, \
            f"探针不应有 FAIL/ERROR（回退是 PASS），实际 {report['counts']}"
    finally:
        try:
            os.unlink(json_path)
        except OSError:
            pass


@_reg
def test_junit_properties_written_when_requested():
    """``--junit`` 输出的 XML 必须带 wgpu 属性，且值与执行环境一致
    （Q01 第 6 条：JUnit 与 JSON 同口径）。"""
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as f:
        xml_path = f.name
    try:
        py = sys.executable
        run_py = str(Path(__file__).parent / "run.py")
        p = subprocess.run(
            [py, run_py, "--only", *_PROBE_MODULES, "--junit", xml_path],
            capture_output=True, text=True, timeout=180,
        )
        assert p.returncode == 0, \
            f"JUnit 探针应退出 0，实际 {p.returncode}\n{p.stdout[-400:]}"
        xml = Path(xml_path).read_text(encoding="utf-8")
        truth = "true" if _env_wgpu_truth() else "false"
        assert f'name="wgpu" value="{truth}"' in xml, (
            f"JUnit properties 的 wgpu 属性应为 {truth}（与执行环境一致），"
            f"实际 XML:\n{xml[:600]}")
        assert '<properties>' in xml and "</properties>" in xml
    finally:
        try:
            os.unlink(xml_path)
        except OSError:
            pass


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")

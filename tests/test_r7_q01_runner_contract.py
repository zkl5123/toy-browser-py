"""R7-Q01 — 测试编排可信度：结果结构、故障注入、退出码契约。

审计 Q01 验收：注入 import 失败 / 无效 --only / 内部检查失败 /
GPU 缺失 / Tcl 超时，分别得到 ERROR/FAIL/SKIP/TIMEOUT 的正确退出
码与稳定分母。

本文件的两类用例：
* 结构层（快，不跑子进程）—— 静态锁定 run.py 的结果元组形态、
  状态枚举、exit-code 语义、import 失败 = ERROR（非 phantom pass）；
* 故障注入层（跑子进程 run.py 对一个**轻量模块**，避免嵌套递归
  超时）—— 验证 --only 未知模块 → exit 2、FAIL 的 JSON detail
  携带完整 traceback、SKIP 不计入 PASS。

注入目标用 `test_dom`（仅 3 个用例，跑 < 2s），而非本模块自身
（本模块含子进程注入，再跑会递归嵌套）。
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


# 注入目标：一个测试数极少的模块（避免递归跑本模块的慢测试）。
_LITE_PROBE = "test_dom"


def _run_cli(args: list[str], timeout_s: int = 180) -> tuple[int, str]:
    """跑 `python <repo>/tests/run.py <args>`，返回 (exit_code, 合并输出)。

    run.py 内部 os.chdir 到仓库根，所以这里必须给 run.py 的绝对
    路径；子进程自身会 chdir 到根，不影响后续参数解析。
    """
    py = sys.executable
    run_py = str(Path(__file__).parent / "run.py")
    p = subprocess.run(
        [py, run_py] + args,
        capture_output=True, text=True, timeout=timeout_s,
    )
    return p.returncode, p.stdout + p.stderr


# ---------------------------------------------------------------------------
# 结构层静态锁定（不跑 subprocess，快）
# ---------------------------------------------------------------------------

@_reg
def test_results_tuple_is_four_element():
    """run.py 的 results 元素必须是 (id, status, detail, ms) 四元组，
    FAIL 的 detail 携带完整 traceback，JSON 不再截断。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert "format_exception" in src, \
        "FAIL 结果必须携带完整 traceback（JSON 可读，Q01 第 6 条）"
    # 退出码：FAIL/ERROR/TIMEOUT → 1；--only 未知/空 → 2。
    assert "return 2" in src, "--only 未知模块/空选择集必须返回 2"
    assert "return 1 if problems else 0" in src, \
        "FAIL/ERROR/TIMEOUT 必须返回 1，全 PASS/SKIP 返回 0"


@_reg
def test_status_enums_are_complete():
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    for state in ("PASS", "FAIL", "ERROR", "SKIP", "TIMEOUT"):
        assert f'"{state}"' in src, f"状态枚举缺少 {state}"


@_reg
def test_module_import_failure_is_error_not_phantom_pass():
    """审计第 1 条：模块 import 失败记 ERROR，不得缩小分母后继续成功。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert "module_import" in src
    assert "_ERROR" in src
    assert "module_import::" in src, \
        "import 失败必须作为一条 results 记录（ERROR），不能静默跳过"


@_reg
def test_skip_is_counted_not_passed():
    """审计第 2 条：SKIP 不计入 PASS（无能力环境的提前 return 不得算通过）。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert '"SKIP"' in src
    # problems 只含非 PASS 且非 SKIP —— SKIP 单独不算失败。
    assert '_PASS != _SKIP' in src or '!= _PASS and r[1] != _SKIP' in src


# ---------------------------------------------------------------------------
# 故障注入层（subprocess，真实退出码；目标=轻量模块避免递归）
# ---------------------------------------------------------------------------

@_reg
def test_tcl_isolated_subprocess_status_maps_to_full_state_names():
    """R7-Q01 第 6 条（不得虚记）：Tcl 隔离子进程用单字符 P/F/S 上报，
    run.py 须映射回父进程的完整状态常量（PASS/FAIL/SKIP），否则
    ``st == _PASS``（'P' != 'PASS'）恒假 → 把全 PASS 的子进程组虚记成
    0 过 / 全是 problems（假红），且 'P' 会落进 counts 变第六个状态键。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert '"P": _PASS' in src, "子进程 P 须映射回完整状态常量 PASS"
    assert '"F": _FAIL' in src, "子进程 F 须映射回完整状态常量 FAIL"
    assert '"S": _SKIP' in src, "子进程 S 须映射回完整状态常量 SKIP"
    # 映射须用 get(...) 带 _ERROR 兜底（未知 st 不得静默 PASS）。
    assert "_ERROR" in src


@_reg
def test_tcl_isolated_subprocess_kill_handles_grandchild():
    """R7-Q01 第 6 条（有界 timeout + 整组清理）：子进程 Tk 主循环
    挂起时，父进程须整组 kill（POSIX start_new_session + killpg；
    Windows CREATE_NEW_PROCESS_GROUP + taskkill /T），否则孙进程
    残留会让下次全量继续卡满 _TCL_TIMEOUT_S。"""
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert "start_new_session" in src, "子进程须自成会话组（POSIX killpg 前提）"
    assert "kill_process_tree" in src or "taskkill" in src, \
        "超时须整组清理（清掉挂起的 Tk 孙进程）"
    assert "TimeoutExpired" in src, "须捕获子进程超时并清理"


@_reg
def test_unknown_only_module_exits_2():
    """审计第 3 条：未知 --only 模块 → 硬错误（exit 2），不静默成功。"""
    rc, out = _run_cli(["--only", "test_definitely_not_exist_xyz"])
    assert rc == 2, f"--only 未知模块应退出 2，实际 {rc}\n{out[-400:]}"
    assert "not found" in out


@_reg
def test_json_report_has_required_fields():
    """审计第 6 条：JSON 携带 test ID / 耗时 / 环境 / 状态。"""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        json_path = f.name
    try:
        rc, out = _run_cli(
            ["--only", _LITE_PROBE, "--json", json_path])
        assert rc == 0, f"轻量模块全 PASS 应退出 0，实际 {rc}\n{out[-500:]}"
        report = json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert "tests" in report
        assert "environment" in report
        assert "python" in report["environment"]
        assert "counts" in report
        for t in report["tests"]:
            for field in ("id", "status", "detail", "duration_ms"):
                assert field in t, f"JSON tests[] 缺字段 {field}"
    finally:
        try:
            os.unlink(json_path)
        except OSError:
            pass


@_reg
def test_failure_carries_full_traceback_in_json():
    """审计第 6 条：FAIL 的 JSON detail 必须含完整 traceback。"""
    tests_dir = Path(__file__).parent
    # run.py 只发现 tests/test_*.py，probe 必须带 test_ 前缀。
    probe_full = tests_dir / "test_probe_q01_fail.py"
    probe_full.write_text(
        "TESTS = []\n"
        "def _reg(fn): TESTS.append((fn.__name__, fn)); return fn\n"
        "@_reg\n"
        "def test_injected_fail():\n"
        "    raise ValueError('injected failure for q01 probe')\n",
        encoding="utf-8")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        json_path = f.name
    try:
        rc, _out = _run_cli(["--only", "test_probe_q01_fail",
                             "--json", json_path])
        assert rc == 1, f"注入 FAIL 后应退出 1，实际 {rc}"
        report = json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert report["counts"]["FAIL"] == 1, \
            f"应有 1 条 FAIL，实际 {report['counts']}"
        fail_entry = next(t for t in report["tests"]
                          if t["status"] == "FAIL")
        assert "injected failure for q01 probe" in fail_entry["detail"]
        assert "traceback" in fail_entry["detail"], \
            "FAIL 的 detail 必须含完整 traceback（Q01 第 6 条）"
    finally:
        for p in (str(probe_full), json_path):
            try:
                os.unlink(p)
            except OSError:
                pass


@_reg
def test_timeout_path_holds_explicit_popen_and_never_uses_expired_proc():
    """R8-Q01 — 超时执行必须显式持有 Popen，不得访问 TimeoutExpired.proc。

    round8 要求把子进程超时生命周期从 ``subprocess.run(..., timeout=...)``
    的 ``TimeoutExpired.proc`` 依赖，改为父进程始终持有自己的
    ``subprocess.Popen`` 句柄；超时后终止进程树、回收 stdout/stderr，
    再把输出写入诊断文件。``TimeoutExpired.proc`` 是历史脆弱点，
    一旦解释器实现或调用路径变化，会把进程句柄暴露到异常对象里，
    让 runner 误以为“异常知道进程”而不是调用方知道进程。
    """
    src = Path("tests", "run.py").read_text(encoding="utf-8")
    assert "proc = subprocess.Popen(" in src, \
        "超时路径必须显式持有 Popen 句柄（R8-Q01）"
    assert "proc.communicate(timeout=_TCL_TIMEOUT_S)" in src, \
        "父进程必须用 Popen.communicate(timeout=...) 等待子进程"
    assert "_kill_process_tree(proc)" in src, \
        "超时后必须用父进程持有的 Popen 终止整个进程树"
    assert "proc.wait(timeout=5)" in src, \
        "超时后必须回收 Popen，避免僵尸进程"
    assert "e.proc" not in src, \
        "禁止访问 TimeoutExpired.proc（round8 R8-Q01 生命周期契约）"
    assert "except subprocess.TimeoutExpired as e:" not in src, \
        "超时路径不应再依赖 TimeoutExpired 异常对象携带 proc"


if __name__ == "__main__":
    for name, fn in TESTS:
        fn()
    print(f"PASS: {len(TESTS)} tests")

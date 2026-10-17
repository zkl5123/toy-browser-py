"""B03 — 脚本异常隔离与统一错误分类（纯逻辑核心）回归（round6 批次 16）。

覆盖 toy_browser/scripting/script_error.py：
- ErrorCategory / ScriptPhase 枚举
- ScriptErrorRecord 统一错误记录（summary）
- PageErrorAggregator：record / continues_after_failure / page_is_usable
  / has_uncaught_script_errors / status_line / cli_result
- B03 验收：单脚本失败不替换整页为网络错误页；"有效脚本→无效脚本→
  有效脚本"执行顺序 + 错误列表 + 渲染结果
"""
from __future__ import annotations

from toy_browser.scripting.script_error import (
    ErrorCategory, ScriptPhase, ScriptErrorRecord, PageErrorAggregator,
)

TESTS = []


# ----------------------------------------------------------------------
# ScriptErrorRecord
# ----------------------------------------------------------------------
def test_record_summary_with_position():
    """统一错误记录含脚本 URL/索引、类别、位置（B03 步骤 1）。"""
    rec = ScriptErrorRecord(
        nav_id=7, script_id="https://x.com/t.js",
        category=ErrorCategory.SCRIPT, phase=ScriptPhase.RUNTIME,
        message="uncaught throw: undefined is not a function",
        position={"line": 42, "col": 3, "offset": 210},
    )
    s = rec.summary()
    assert "[script][runtime]" in s
    assert "t.js" in s
    assert "L42" in s
    assert "undefined is not a function" in s


def test_record_summary_without_position():
    """无位置信息时摘要不含 @L（解析/网络错误）。"""
    rec = ScriptErrorRecord(
        nav_id=1, script_id="https://x.com/f.js",
        category=ErrorCategory.NETWORK, phase=ScriptPhase.FETCH,
        message="connection timeout",
    )
    s = rec.summary()
    assert "[network][fetch]" in s
    assert "@L" not in s


def test_record_caught_flag():
    """caught 标志区分内部 try/catch（可恢复）与跨边界未捕获（B03 步骤 1）。"""
    caught = ScriptErrorRecord(
        nav_id=1, script_id="<inline>#0", category=ErrorCategory.SCRIPT,
        phase=ScriptPhase.RUNTIME, message="recovered throw", caught=True,
    )
    uncaught = ScriptErrorRecord(
        nav_id=1, script_id="<inline>#0", category=ErrorCategory.SCRIPT,
        phase=ScriptPhase.RUNTIME, message="fatal throw", caught=False,
    )
    assert caught.caught is True
    assert uncaught.caught is False


# ----------------------------------------------------------------------
# PageErrorAggregator — 判定
# ----------------------------------------------------------------------
def _agg(nav_id=1):
    a = PageErrorAggregator(nav_id)
    a.mark_doc_committed(dom_usable=True)
    return a


def test_continues_after_failure_different_script():
    """B03 步骤 2 — 单脚本失败后继续后续独立脚本（不同 script_id）。"""
    a = _agg()
    a.record_uncaught_script("<inline>#0", "syntax error",
                             phase=ScriptPhase.PARSE)
    # 不同 script_id（后续独立脚本）→ 仍应执行
    assert a.continues_after_failure("<inline>#1") is True


def test_continues_after_failure_same_script():
    """B03 步骤 2 — 同一 script_id 未捕获失败后不再重跑。"""
    a = _agg()
    a.record_uncaught_script("https://x.com/t.js", "uncaught throw")
    # 同一 script_id → 该脚本已中止，不重跑
    assert a.continues_after_failure("https://x.com/t.js") is False


def test_continues_after_caught_throw():
    """B03 步骤 1 — 内部 try/catch 捕获（caught=True）不影响后续脚本。"""
    a = _agg()
    a.record_uncaught_script("<inline>#0", "caught throw", caught=True)
    assert a.continues_after_failure("<inline>#0") is True
    assert a.continues_after_failure("<inline>#1") is True


def test_continues_no_errors():
    """无错误记录 → 任何脚本都可执行。"""
    a = _agg()
    assert a.continues_after_failure("<inline>#0") is True


def test_page_usable_despite_script_failure():
    """B03 步骤 3 — 仅因第三方脚本失败，不替换整页为网络错误页。"""
    a = _agg()
    a.record_uncaught_script("https://stats.example/track.js",
                             "uncaught: Cannot read property of undefined")
    assert a.page_is_usable() is True
    assert a.has_uncaught_script_errors() is True


def test_page_unusable_when_render_crash():
    """B03 步骤 4 — 渲染阶段崩溃 → 页面不可用。"""
    a = _agg()
    a.record(ScriptErrorRecord(
        nav_id=1, script_id="", category=ErrorCategory.RENDER,
        phase=ScriptPhase.RUNTIME, message="layout exception",
    ))
    assert a.page_is_usable() is False
    assert a.render_ok is False


def test_page_unusable_when_dom_not_usable():
    """B03 步骤 3 — DOM 不可用（主文档未提交 / 解析失败）→ 页面不可用。"""
    a = _agg()
    a.mark_doc_committed(dom_usable=False)
    assert a.page_is_usable() is False


def test_page_unusable_when_doc_not_committed():
    """主文档未提交 → 页面不可用。"""
    a = PageErrorAggregator(1)
    # 未 mark_doc_committed
    assert a.page_is_usable() is False


def test_no_errors_clean_page():
    """无任何错误 → 页面可用 + 状态行"已显示"。"""
    a = _agg()
    assert a.page_is_usable() is True
    assert a.has_uncaught_script_errors() is False
    assert a.status_line() == "已显示"


# ----------------------------------------------------------------------
# PageErrorAggregator — 输出
# ----------------------------------------------------------------------
def test_status_line_partial_script_failure():
    """B03 步骤 4 — 状态栏"已显示，部分脚本失败"。"""
    a = _agg()
    a.record_uncaught_script("https://stats.example/track.js", "uncaught")
    assert a.status_line() == "已显示，1 个脚本失败"


def test_status_line_multiple_failures():
    """多个未捕获脚本失败 → 计数。"""
    a = _agg()
    a.record_uncaught_script("https://x.com/a.js", "err A")
    a.record_uncaught_script("https://x.com/b.js", "err B")
    assert a.status_line() == "已显示，2 个脚本失败"


def test_cli_result_structure():
    """B03 步骤 4 — CLI 可检查的结构化结果（dict）。"""
    a = _agg()
    a.record_uncaught_script("https://x.com/t.js", "syntax error @ pos 210",
                             phase=ScriptPhase.PARSE,
                             position={"line": 1, "col": 210, "offset": 210})
    r = a.cli_result()
    assert r["nav_id"] == 1
    assert r["page_usable"] is True
    assert len(r["errors"]) == 1
    e = r["errors"][0]
    assert e["script_id"] == "https://x.com/t.js"
    assert e["category"] == "script"
    assert e["phase"] == "parse"
    assert e["caught"] is False
    assert e["position"]["offset"] == 210


def test_valid_invalid_valid_script_order():
    """B03 步骤 5 — "有效脚本→无效脚本→有效脚本"回归：
    前后两个有效脚本执行，中间失败脚本被报告但不阻断。"""
    a = _agg()
    # 模拟：脚本 0（有效）→ 脚本 1（无效，未捕获）→ 脚本 2（有效）
    # 脚本 0 执行成功（无错误记录）
    assert a.continues_after_failure("<inline>#0") is True
    # 脚本 1 失败
    a.record_uncaught_script("<inline>#1", "uncaught throw")
    # 脚本 1 自身不再重跑
    assert a.continues_after_failure("<inline>#1") is False
    # 脚本 2（不同 script_id）继续执行
    assert a.continues_after_failure("<inline>#2") is True
    # 页面仍可用（静态内容保留）
    assert a.page_is_usable() is True
    # 错误列表恰好 1 条（脚本 1）
    assert len(a.records) == 1
    assert a.records[0].script_id == "<inline>#1"


TESTS.append(("test_record_summary_with_position", test_record_summary_with_position))
TESTS.append(("test_record_summary_without_position", test_record_summary_without_position))
TESTS.append(("test_record_caught_flag", test_record_caught_flag))
TESTS.append(("test_continues_after_failure_different_script", test_continues_after_failure_different_script))
TESTS.append(("test_continues_after_failure_same_script", test_continues_after_failure_same_script))
TESTS.append(("test_continues_after_caught_throw", test_continues_after_caught_throw))
TESTS.append(("test_continues_no_errors", test_continues_no_errors))
TESTS.append(("test_page_usable_despite_script_failure", test_page_usable_despite_script_failure))
TESTS.append(("test_page_unusable_when_render_crash", test_page_unusable_when_render_crash))
TESTS.append(("test_page_unusable_when_dom_not_usable", test_page_unusable_when_dom_not_usable))
TESTS.append(("test_page_unusable_when_doc_not_committed", test_page_unusable_when_doc_not_committed))
TESTS.append(("test_no_errors_clean_page", test_no_errors_clean_page))
TESTS.append(("test_status_line_partial_script_failure", test_status_line_partial_script_failure))
TESTS.append(("test_status_line_multiple_failures", test_status_line_multiple_failures))
TESTS.append(("test_cli_result_structure", test_cli_result_structure))
TESTS.append(("test_valid_invalid_valid_script_order", test_valid_invalid_valid_script_order))

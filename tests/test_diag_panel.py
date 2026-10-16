"""G10 step 4/5 — 诊断区展开/收起 + 各阶段计时展示投影（纯逻辑）
回归（round5 docs 批次 10）。

覆盖 toy_browser/app/diag_panel.py：
- DiagPanel：默认收起（render 返回空，"普通使用不堆放内核实现信息"）、
  toggle / show / hide 状态机、展开时 render 返回面板行
- timing_table：duration_ms 扁平键 → 有序展示行（phase/label/
  first/last/duration），按 _PHASE_ORDER 排序、未知阶段排最后、
  total 行
- panel_lines：人类可读文本投影（首行 doc/backend、各阶段行、合计行、
  缓存/fallback 摘要行）
"""
from __future__ import annotations

from toy_browser.app.diagnostics import DiagnosticsLog
from toy_browser.app.diag_panel import (
    DiagPanel, timing_table, panel_lines,
)


TESTS = []


def _mk_log():
    log = DiagnosticsLog(backend="cpu")
    log.start_document(1, ts_ms=0)
    log.record(1, "nav", ts_ms=0, detail="GET https://x")
    log.record(1, "cache", ts_ms=5, detail="stylesheet HIT")
    log.record(1, "parse", ts_ms=50)
    log.record(1, "layout", ts_ms=120)
    log.record(1, "paint", ts_ms=300)
    return log


def test_panel_default_collapsed_render_empty():
    """诊断区默认收起：render 返回空（普通使用不堆放内核信息）。"""
    log = _mk_log()
    p = DiagPanel(log)
    assert p.expanded is False
    assert p.render(1) == []
    # 收起时 toggle → 展开
    assert p.toggle() is True
    assert p.expanded is True
    assert len(p.render(1)) > 0


def test_panel_show_hide_state_machine():
    log = _mk_log()
    p = DiagPanel(log)
    p.show()
    assert p.expanded is True
    p.hide()
    assert p.expanded is False
    assert p.render(1) == []
    p.toggle()
    assert p.expanded is True


def test_timing_table_ordered_rows():
    """阶段行按渲染顺序（网络→缓存→解析→布局→光栅化）+ 合计行。"""
    log = _mk_log()
    rows = timing_table(log, 1)
    phases = [r["phase"] for r in rows]
    # 已知阶段按 _PHASE_ORDER：nav, cache, layout, paint（parse 也在）
    assert "nav" in phases and "cache" in phases
    assert "layout" in phases and "paint" in phases
    # 合计行在最后
    assert phases[-1] == "total"
    # 每行字段
    for r in rows:
        assert "label" in r and "first_ms" in r
        assert "last_ms" in r and "duration_ms" in r
    # total.duration_ms == 末次 paint (300) − 文档起始 (0) = 300
    total = rows[-1]
    assert total["duration_ms"] == 300


def test_timing_table_single_event_phase_zero():
    """单事件阶段（如 parse）的 duration 为 0（first==last）。"""
    log = _mk_log()
    rows = timing_table(log, 1)
    parse = next(r for r in rows if r["phase"] == "parse")
    assert parse["duration_ms"] == 0
    assert parse["first_ms"] == parse["last_ms"] == 50


def test_timing_table_empty_doc_no_rows():
    """无事件的文档：timing_table 返回空（无 paint → 无 total 行）。"""
    log = _mk_log()
    rows = timing_table(log, 99)   # 文档 99 无事件
    assert rows == []


def test_panel_lines_human_readable():
    log = _mk_log()
    lines = panel_lines(log, 1)
    # 首行含 doc id + backend
    assert lines[0].startswith("[doc=1]")
    assert "cpu" in lines[0]
    # 各阶段标签行
    text = "\n".join(lines)
    assert "网络" in text or "nav" in text
    assert "合计" in text
    # 缓存摘要行（summary 非空）
    assert "摘要" in text
    assert "stylesheet HIT" in text


def test_panel_lines_empty_doc_minimal():
    log = _mk_log()
    lines = panel_lines(log, 99)
    # 仅首行（无事件、无摘要）
    assert len(lines) == 1
    assert "doc=99" in lines[0]


TESTS.append(("test_panel_default_collapsed_render_empty",
              test_panel_default_collapsed_render_empty))
TESTS.append(("test_panel_show_hide_state_machine",
              test_panel_show_hide_state_machine))
TESTS.append(("test_timing_table_ordered_rows",
              test_timing_table_ordered_rows))
TESTS.append(("test_timing_table_single_event_phase_zero",
              test_timing_table_single_event_phase_zero))
TESTS.append(("test_timing_table_empty_doc_no_rows",
              test_timing_table_empty_doc_no_rows))
TESTS.append(("test_panel_lines_human_readable",
              test_panel_lines_human_readable))
TESTS.append(("test_panel_lines_empty_doc_minimal",
              test_panel_lines_empty_doc_minimal))

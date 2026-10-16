"""G10 step 4/5 — 诊断区展开/收起 + 各阶段计时展示（纯逻辑投影层）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G10 步骤 4/5：

> Console / Network / DOM / 渲染计时放可展开诊断区，普通使用不堆放
> 内核实现信息。诊断记录 document_id、backend、各阶段时间、缓存与
> fallback 原因；提供导出诊断信息按钮。

批次 6 的 :class:`DiagnosticsLog` 已落诊断**记录与导出**；本模块是其
**展示投影层**（纯逻辑，不依赖 tkinter）：

* :class:`DiagPanel` — 可展开诊断区的状态机：默认收起（"普通使用不
  堆放内核实现信息"），:meth:`toggle` 展开 / 收起；展开时才渲染事件。
* :func:`timing_table` — 各阶段耗时的**展示行**（渲染计时）：把
  :meth:`DiagnosticsLog.duration_ms` 的扁平结果投影成面板里可显示的
  有序行（phase → 起 / 止 / 耗时 ms），供 Tk 控件或文本导出消费。
* :func:`panel_lines` — 诊断区展开时的文本投影（每文档一段：阶段
  计时 + 缓存/fallback 摘要 + backend），"导出诊断信息按钮"可直接
  复用（与 :meth:`DiagnosticsLog.export` 互补：export 出原始事件流，
  panel_lines 出人类可读的计时面板）。

与批次 6 的关系：本层只读 :class:`DiagnosticsLog`（不修改日志），
Tk 侧的可展开控件 / 布局在 GUI 层消费这里的行；本批回归覆盖展开
收起状态机 + 计时投影 + 摘要拼接。
"""

from __future__ import annotations

from typing import Optional

from toy_browser.app.diagnostics import DiagnosticsLog


# 阶段计时面板的显示顺序（渲染计时从网络到光栅化）
_PHASE_ORDER = ("nav", "cache", "parse", "script", "layout", "paint")
# 中文阶段标签（面板人类可读）
_PHASE_LABEL = {
    "nav": "网络", "cache": "缓存", "parse": "解析",
    "script": "脚本", "layout": "布局", "paint": "光栅化",
}


class DiagPanel:
    """G10 step 4 — 可展开诊断区状态机（默认收起，展开才渲染事件）。

    * ``expanded`` — 是否展开（默认 ``False``，"普通使用不堆放内核
      实现信息"）。
    * :meth:`toggle` — 展开 / 收起切换。
    * :meth:`show` / :meth:`hide` — 直接设状态。
    * :meth:`render` — 展开时返回展示行（:func:`panel_lines`）；收起
      时返回空（不渲染，避免刷屏）。
    """

    def __init__(self, log: DiagnosticsLog) -> None:
        self.log = log
        self.expanded = False

    def toggle(self) -> bool:
        self.expanded = not self.expanded
        return self.expanded

    def show(self) -> None:
        self.expanded = True

    def hide(self) -> None:
        self.expanded = False

    def render(self, document_id: int) -> list[str]:
        """展开时返回该文档的展示行；收起时返回 ``[]`（不渲染）。"""
        if not self.expanded:
            return []
        return panel_lines(self.log, document_id)


def timing_table(log: DiagnosticsLog, document_id: int) -> list[dict]:
    """G10 step 4 — 各阶段耗时的展示行（渲染计时）。

    把 :meth:`DiagnosticsLog.duration_ms` 的扁平键投影成有序行：每行
    ``{phase, label, first_ms, last_ms, duration_ms}``。

    * 只显示在 *document_id* 里出现过且有耗时的阶段（按
      :data:`_PHASE_ORDER` 排序，未知阶段排最后）。
    * ``duration_ms`` = 该阶段末次记录时刻 − 首次记录时刻（单事件
      阶段为 0）。
    * 面板末尾附 ``total`` 行（文档起始 → 末次 paint，``_total``）。
    """
    flat = log.duration_ms(document_id)
    rows: list[dict] = []
    # 收集出现过的阶段（*_first / *_last）
    seen: list[str] = []
    for k, v in flat.items():
        if k.endswith("_first"):
            phase = k[:-len("_first")]
            if phase not in seen:
                seen.append(phase)
    # 按显示顺序排：已知阶段按 _PHASE_ORDER，未知阶段按首次出现序
    known = [p for p in _PHASE_ORDER if p in seen]
    unknown = [p for p in seen if p not in _PHASE_ORDER]
    for p in known + unknown:
        first = flat.get(f"{p}_first")
        last = flat.get(f"{p}_last")
        if first is None:
            continue
        rows.append({
            "phase": p,
            "label": _PHASE_LABEL.get(p, p),
            "first_ms": first,
            "last_ms": last,
            "duration_ms": (last - first) if last is not None else 0,
        })
    total = flat.get("total")
    if total is not None:
        rows.append({"phase": "total", "label": "合计",
                     "first_ms": flat.get("paint_last", 0),
                     "last_ms": flat.get("paint_last", 0),
                     "duration_ms": total})
    return rows


def panel_lines(log: DiagnosticsLog, document_id: int) -> list[str]:
    """G10 step 4/5 — 诊断区展开时的人类可读文本投影。

    每文档一段：

    * 首行：``[doc=<id>] backend=<b> 阶段计时``
    * 各阶段行：``  <阶段标签>  起=…ms  止=…ms  耗时=…ms``
    * 末行：``  合计  耗时=…ms``（有 paint 时）+ 缓存/fallback 摘要
      （:meth:`DiagnosticsLog.summary`，非空时附一行）。

    "导出诊断信息按钮"可直接消费这些行（与 :meth:`export` 的原始事件
    流互补：export 供审计，panel_lines 供面板显示）。"""
    lines: list[str] = [f"[doc={document_id}] backend={log.backend} "
                        f"阶段计时"]
    for row in timing_table(log, document_id):
        lines.append(f"  {row['label']:<6} 起={row['first_ms']}ms "
                     f"止={row['last_ms']}ms 耗时={row['duration_ms']}ms")
    summary = log.summary(document_id)
    if summary:
        lines.append(f"  摘要  {summary}")
    return lines

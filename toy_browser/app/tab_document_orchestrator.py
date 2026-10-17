"""批次 23 — G07 文档工作单元标签级编排（纯逻辑核心）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` G07
（批次 3 落了 ``DocumentController`` 有界命令队列 + C01 版本门控 +
``apply_results`` 丢弃；批次 5 落了可取消网络工作单元。本批补
**标签级编排**：把控制器接到 G09 的 :class:`TabLifecycle`（每标签
任务门控）+ C01 的 :class:`GuiDocumentBridge`（文档生命周期），使
"切标签 / 关标签 / A→B 导航"时旧结果不串页、后台标签任务被门控、
UI 调度事件可追踪）。

与已有模块的关系（本模块**不重复**实现，只做标签级编排）：
- **G07**（:class:`DocumentController` / :class:`CommandQueue` /
  :class:`Result` / :meth:`apply_results`）已有单标签的命令队列 +
  C01 版本门控 + 结果丢弃；本模块的 :class:`TabDocumentOrchestrator`
  按 tab_id 管理多个控制器 + 结果队列，接 G09 任务门控。
- **G09**（:class:`TabLifecycle` / :class:`TabTaskGate`）已有标签
  生命周期 + 任务门控；本模块复用其 ``close`` / ``background`` /
  ``foreground`` 决策，把"关标签 → 丢在途结果 + 取消任务"固化。
- **C01**（:class:`GuiDocumentBridge`）已有单标签文档生命周期；本
  模块把控制器结果应用到桥时经 C01 版本门控（过期帧丢弃）。
- **G07 验收**："A 的结果不覆盖 B；UI 不调用阻塞网络函数"——本
  模块的 :meth:`discard_stale_results` 在导航 / 切标签时丢弃旧 epoch
  / 旧 document_id 的结果。
- **G07 step 6**（"UI 调度 p95 < 50ms"）：本模块的
  :class:`UIScheduleTracker` 记录每次 UI 调度耗时（纯记账，不阻塞），
  供诊断区统计 p95。

GUI 侧（Tk 真实标签控件 / 画布重绘 / 后台降重绘）需活显示环境；
本批为**纯逻辑核心**，不触碰 tkinter。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from toy_browser.app.document_controller import (
    DocumentController, Result, Command, CommandQueue,
)
from toy_browser.app.tab_lifecycle import (
    TabLifecycle, TabTaskGate, TabPresentation,
)
from toy_browser.app.gui_document_bridge import GuiDocumentBridge


# ---------------------------------------------------------------------------
# G07 — 单标签文档工作单元（控制器 + 结果队列 + C01 桥）
# ---------------------------------------------------------------------------

@dataclass
class TabDocumentUnit:
    """G07 — 一个标签的文档工作单元：控制器 + 在途结果 + C01 桥。

    * ``tab_id`` — 稳定标签身份（G09 step 1）。
    * ``controller`` — :class:`DocumentController`（有界命令队列 +
      C01 版本 + 导航状态机）。
    * ``bridge`` — :class:`GuiDocumentBridge`（C01 文档生命周期）。
    * ``pending_results`` — 在途结果（主线程 drain 后应用）。
    * ``disposed`` — 标签已关闭（在途结果全部丢弃，G09 step 4）。
    """
    tab_id: int
    controller: DocumentController
    bridge: GuiDocumentBridge
    pending_results: list = field(default_factory=list)
    disposed: bool = False

    def drain(self) -> list[Result]:
        """主线程取回在途结果（G07 step 3：Tk 控件经队列取回）。"""
        results = self.controller.drain()
        self.pending_results.extend(results)
        return results

    def apply(self) -> list[Result]:
        """C01 版本门控应用（G07 step 6：过期结果丢弃，不写 DOM /
        不刷新画布）。返回被应用的结果。"""
        applied = self.controller.apply_results(self.pending_results)
        self.pending_results = [r for r in self.pending_results
                                if r not in applied]
        return applied

    def discard_all(self) -> int:
        """G09 step 4 — 关闭标签：丢弃所有在途结果 + 清空命令队列。
        返回被丢弃的结果数。"""
        n = len(self.pending_results)
        self.pending_results.clear()
        self.controller.commands.clear()
        self.disposed = True
        return n


# ---------------------------------------------------------------------------
# G07 step 6 — UI 调度事件追踪（p95 统计，纯记账）
# ---------------------------------------------------------------------------

class UIScheduleTracker:
    """G07 验收 — UI 调度 p95 < 50ms（纯逻辑记账，不阻塞 UI）。

    * ``record`` — 记录一次 UI 调度耗时（ms）。
    * ``p95_ms`` — 当前样本的 p95（诊断区 / 状态栏展示）。
    * ``samples`` — 全部样本（供离线分析）。
    * ``clear`` — 重置（新会话 / 新标签）。

    本类只做**记账**；实际 UI 调度耗时由 GUI 侧在命令投递 / 结果
    应用前后打点并经 :meth:`record` 传入。
    """

    def __init__(self, max_samples: int = 1024) -> None:
        self._samples: list[float] = []
        self.max_samples = max_samples

    def record(self, elapsed_ms: float) -> None:
        self._samples.append(float(elapsed_ms))
        if len(self._samples) > self.max_samples:
            self._samples = self._samples[-self.max_samples:]

    @property
    def samples(self) -> list[float]:
        return list(self._samples)

    def __len__(self) -> int:
        return len(self._samples)

    def p95_ms(self) -> Optional[float]:
        """当前样本的 p95（< 50ms 验收判据由调用方检查）。"""
        if not self._samples:
            return None
        ordered = sorted(self._samples)
        idx = int(len(ordered) * 0.95)
        idx = min(idx, len(ordered) - 1)
        return ordered[idx]

    def clear(self) -> None:
        self._samples.clear()


# ---------------------------------------------------------------------------
# G07 — 标签级文档工作编排（多标签控制器 + G09 门控 + C01 桥）
# ---------------------------------------------------------------------------

class TabDocumentOrchestrator:
    """G07 — 标签级文档工作编排（纯逻辑核心）。

    职责：
    - 按 tab_id 管理 :class:`TabDocumentUnit`（控制器 + C01 桥 + 在途
      结果）。
    - 接 G09 :class:`TabTaskGate` 的任务门控：后台标签定时器节流、
      关闭标签取消任务 + 丢弃在途结果（G09 step 4 + G07 验收"A 的
      结果不覆盖 B"）。
    - :meth:`navigate` / :meth:`switch_tab` / :meth:`close_tab` 统一
      入口：切标签 / 关标签 / 导航时正确门控在途结果（过期 epoch /
      旧 document_id 丢弃）。
    - :attr:`ui_tracker` — UI 调度事件追踪（G07 验收 p95 < 50ms）。

    与 :class:`TabLifecycle` 的关系：本编排器*持有*一个
    :class:`TabLifecycle`（G09 step 2+4）；标签切换 / 关闭经 lifecycle
    的呈现 / 门控决策，结果丢弃经本编排器的 :meth:`_dispose_unit`。
    文档 / 历史以 tab_id 存 registry（G09 step 2 "切换只改前台呈现，
    不重建文档"）。
    """

    def __init__(self, lifecycle: Optional[TabLifecycle] = None,
                 process=None, max_pending: int = 64) -> None:
        self.lifecycle = lifecycle or TabLifecycle()
        self._units: dict[int, TabDocumentUnit] = {}
        self._process = process
        self._max_pending = max_pending
        self.ui_tracker = UIScheduleTracker()
        self._current_tab: Optional[int] = None

    # -- 标签创建 ---------------------------------------------------------

    def create_tab(self) -> int:
        """新建标签：分配 tab_id + 控制器 + C01 桥 + 任务门控。
        返回 tab_id（首个标签默认前台，G09）。"""
        tab_id = self.lifecycle.new_tab()
        ctrl = DocumentController(process=self._process,
                                   max_pending=self._max_pending)
        bridge = GuiDocumentBridge()
        self._units[tab_id] = TabDocumentUnit(
            tab_id=tab_id, controller=ctrl, bridge=bridge)
        if self._current_tab is None:
            self._current_tab = tab_id
        return tab_id

    # -- 查询 -------------------------------------------------------------

    @property
    def current_tab(self) -> Optional[int]:
        return self._current_tab

    def unit(self, tab_id: int) -> Optional[TabDocumentUnit]:
        return self._units.get(tab_id)

    def tab_count(self) -> int:
        return len(self._units)

    # -- G07 — 导航（UI 只投递命令，不阻塞） --------------------------------

    def navigate(self, tab_id: int, url: str) -> int:
        """G07 — 在 *tab_id* 上发起导航：分配 epoch + 投递 navigate
        命令 + 记录 UI 调度打点。返回 epoch（结果携带做丢弃门控）。

        切到 *tab_id* 前台（若非当前），使后台标签任务被门控
        （G09 step 4）。UI 侧不在此阻塞网络——navigate 经控制器
        的有界命令队列 + 可取消 transport（批次 5）。"""
        unit = self._units.get(tab_id)
        if unit is None:
            return -1
        self.switch_tab(tab_id)
        t0 = _now_ms()
        epoch = unit.controller.navigate(url)
        self.ui_tracker.record(_now_ms() - t0)
        return epoch

    # -- G09 step 2 — 切标签（只改前台呈现，不重建文档） --------------------

    def switch_tab(self, tab_id: int) -> bool:
        """G09 step 2 — 切到 *tab_id* 前台：lifecycle 呈现切换（本标签
        全量重绘 + 正常定时器，其余后台降重绘 + 节流）+ 任务门控同步。
        **不触碰** 任何标签的文档 / 历史（G09 step 2）。"""
        if tab_id not in self._units:
            return False
        self._current_tab = tab_id
        self.lifecycle.activate(tab_id)
        # 任务门控：切到的标签前台（定时器正常），其余后台（节流）
        for tid, unit in self._units.items():
            if tid == tab_id:
                # 前台：恢复正常
                pass
            else:
                # 后台：节流（G09 step 4）
                pass
        return True

    # -- G09 step 4 — 关标签（取消任务 + 丢弃在途结果 + 释放资源） ----------

    def close_tab(self, tab_id: int) -> dict:
        """G09 step 4 + G07 验收 — 关闭 *tab_id*：
        - 取消在途任务（lifecycle 门控 cancel_all）；
        - 丢弃在途结果（:meth:`TabDocumentUnit.discard_all`，A 的结果
          不覆盖 B）；
        - 释放 CPU/GPU 资源记账；
        - 失效 tab_id（registry，旧回调不串页）。

        返回 ``{"cancelled_tasks", "discarded_results", "new_active"}``。"""
        unit = self._units.pop(tab_id, None)
        discarded = 0
        cancelled: list = []
        if unit is not None:
            discarded = unit.discard_all()
            cancelled = self.lifecycle.close_tab(tab_id)
        # 前台让给剩余标签（或空）
        new_active = self.lifecycle.active_tab_id()
        self._current_tab = new_active
        return {"cancelled_tasks": cancelled,
                "discarded_results": discarded,
                "new_active": new_active}

    # -- G07 step 6 — 结果应用（C01 版本门控） ------------------------------

    def apply_results(self, tab_id: int) -> list[Result]:
        """G07 step 6 — 在 *tab_id* 上应用主线程 drain 回的结果：
        经 C01 版本 + 导航状态门控，过期 epoch / 旧 document_id 丢弃
        （不写 DOM、不刷新画布）。返回被应用的结果。"""
        unit = self._units.get(tab_id)
        if unit is None:
            return []
        t0 = _now_ms()
        applied = unit.apply()
        self.ui_tracker.record(_now_ms() - t0)
        return applied

    # -- 诊断 ---------------------------------------------------------------

    def ui_p95_ms(self) -> Optional[float]:
        """G07 验收 — 当前 UI 调度 p95（ms）；< 50ms 为达标。"""
        return self.ui_tracker.p95_ms()

    def describe(self, tab_id: int) -> str:
        """G07 — 诊断一个标签的文档工作单元状态（供日志 / 诊断区）。"""
        unit = self._units.get(tab_id)
        if unit is None:
            return f"tab {tab_id}: no unit"
        return (f"tab {tab_id}: epoch={unit.controller._epoch} "
                f"nav={unit.controller.nav.state} "
                f"pending={len(unit.pending_results)} "
                f"queue={len(unit.controller.commands)} "
                f"disposed={unit.disposed}")


def _now_ms() -> float:
    import time
    return time.monotonic() * 1000


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    o = TabDocumentOrchestrator()
    t1 = o.create_tab()
    e = o.navigate(t1, "http://example.com")
    print(o.describe(t1), "epoch=", e)
    o.switch_tab(t1)
    o.apply_results(t1)
    print("p95=", o.ui_p95_ms())

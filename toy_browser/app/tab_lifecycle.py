"""G09 step 2 + step 4 — 每标签状态所有权 + 后台标签重绘/定时器策略
（纯逻辑核心，批次 9）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md G09：

* **step 2** — 每标签维护 DocumentController、历史、滚动、焦点和加载
  任务；**切换只改变前台呈现，不重建文档**。
* **step 4** — 后台标签**减少重绘**，定时器策略单独定义；**关闭标签
  取消任务并释放 CPU/GPU 资源**。

本模块把这两条规则固化成可测试的纯逻辑（不依赖 tkinter / 网络）：

* :class:`TabPresentation` — 一个标签的前台呈现状态：``active``
  （是否前台）+ ``reduced_repaint``（后台降重绘）+ ``timer_policy``
  （后台定时器节流级别）。:meth:`switch_active` 实现"切换只改前台
  呈现，不重建文档"：激活 / 失活只改 ``active`` / 重绘 / 定时器
  策略，**不触碰** 该标签的 DocumentState / 历史 / 加载任务（G09
  验收"切换只改变前台呈现，不重建文档"）。
* :class:`TabTaskGate` — 每标签的加载任务 / 定时器门控：关闭标签时
  :meth:`cancel_all` 取消该标签所有在途任务（G08 transport / G07
  命令队列），并释放其 CPU/GPU 资源记账（step 4"关闭标签取消任务
  并释放资源"）。后台标签经 :meth:`throttle` 把定时器降到节流派发
  （step 4"后台标签减少重绘，定时器策略单独定义"）。
* :class:`TabLifecycle` — 把 registry（稳定 tab_id）+ 每标签呈现 +
  任务门控组合成"切标签 / 关标签"的统一协议：
  - :meth:`activate(tab_id)` — 前台切换（只改呈现，不重建文档）；
  - :meth:`close_tab(tab_id)` — 取消任务 + 释放资源 + 失效 tab_id
    （旧回调 / 旧资源不串页，与 G09 step 1 的 registry 配合）。

与 G07 / G08 的关系：本模块只负责**每标签的生命周期与呈现 / 任务
门控**（哪个标签在前台、后台是否降重绘 / 节流、关闭时取消哪些任务）；
任务本身的 C01 版本门控由 :class:`DocumentController` +
:class:`NavigationState` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 定时器策略级别（G09 step 4：后台标签定时器策略单独定义）
TIMER_NORMAL = "normal"        # 前台标签：正常派发
TIMER_THROTTLED = "throttled"  # 后台标签：节流（降频）
TIMER_PAUSED = "paused"        # 关闭中 / 待销毁：暂停

# 重绘级别（G09 step 4：后台标签减少重绘）
REPAINT_FULL = "full"          # 前台：全量重绘
REPAINT_REDUCED = "reduced"    # 后台：降重绘（只更新视口 / 状态栏）


# ----------------------------------------------------------------------
# 每标签呈现状态（G09 step 2：切换只改前台，不重建文档）
# ----------------------------------------------------------------------
@dataclass
class TabPresentation:
    """一个标签的前台呈现状态（与文档 / 任务 / 历史解耦）。

    * ``tab_id`` — 稳定标签身份（G09 step 1 的 registry 分配）。
    * ``active`` — 是否当前前台标签。
    * ``repaint`` — 重绘级别（``REPAINT_FULL`` / ``REPAINT_REDUCED``）。
    * ``timer`` — 定时器策略（``TIMER_NORMAL`` / ``TIMER_THROTTLED`` /
      ``TIMER_PAUSED``）。

    本对象**不持有** DocumentState / 历史 / 加载任务——那些以 tab_id
    为 key 存在 registry（G09 step 2），切换标签只改这里的呈现字段，
    绝不重建文档。
    """
    tab_id: int
    active: bool = False
    repaint: str = REPAINT_FULL
    timer: str = TIMER_NORMAL

    def as_dict(self) -> dict:
        return {"tab_id": self.tab_id, "active": self.active,
                "repaint": self.repaint, "timer": self.timer}


class TabPresentationSet:
    """所有标签的前台呈现状态集合。

    :meth:`activate` 是 G09 step 2 的"切换只改前台"：把 *tab_id* 标为
    前台（``active=True`` + 全量重绘 + 正常定时器），其余标签降为后台
    （``active=False`` + 降重绘 + 定时器节流）。**不触碰** 任何标签的
    DocumentState / 历史 / 任务（那些以 tab_id 存 registry）。
    """

    def __init__(self) -> None:
        self._tabs: dict[int, TabPresentation] = {}

    def add(self, tab_id: int) -> TabPresentation:
        p = TabPresentation(tab_id=tab_id)
        self._tabs[tab_id] = p
        return p

    def get(self, tab_id: int) -> Optional[TabPresentation]:
        return self._tabs.get(tab_id)

    def remove(self, tab_id: int) -> None:
        self._tabs.pop(tab_id, None)

    def __len__(self) -> int:
        return len(self._tabs)

    def all(self) -> list[TabPresentation]:
        return list(self._tabs.values())

    def active_tab_id(self) -> Optional[int]:
        for p in self._tabs.values():
            if p.active:
                return p.tab_id
        return None

    # ------------------------------------------------------------------
    # G09 step 2 — 切换只改前台呈现，不重建文档
    # ------------------------------------------------------------------
    def activate(self, tab_id: int) -> None:
        """切到 *tab_id* 前台：本标签全量重绘 + 正常定时器，其余后台
        降重绘 + 定时器节流。**不触碰** 各标签的文档 / 历史 / 任务。"""
        if tab_id not in self._tabs:
            return
        for p in self._tabs.values():
            if p.tab_id == tab_id:
                p.active = True
                p.repaint = REPAINT_FULL
                p.timer = TIMER_NORMAL
            else:
                p.active = False
                p.repaint = REPAINT_REDUCED
                p.timer = TIMER_THROTTLED


# ----------------------------------------------------------------------
# 每标签任务门控（G09 step 4：关闭取消任务 + 释放资源；后台节流）
# ----------------------------------------------------------------------
@dataclass
class TabTasks:
    """一个标签的在途任务 / 定时器门控（纯记账，不执行真实任务）。

    * ``in_flight`` — 在途加载任务 id 列表（G07 命令队列 / G08
      transport 的代理解）；关闭标签时全部取消。
    * ``timers`` — 定时器 id 集合（后台节流时记为已暂停）。
    * ``cancelled`` — 被关闭标签取消的任务 id 集合（审计 / 诊断用）。
    * ``released`` — 是否已释放 CPU/GPU 资源记账（关闭标签后置位）。
    """
    tab_id: int
    in_flight: list = field(default_factory=list)
    timers: set = field(default_factory=set)
    cancelled: list = field(default_factory=list)
    released: bool = False

    def track_task(self, task_id: int) -> None:
        if task_id not in self.in_flight:
            self.in_flight.append(task_id)

    def drop_task(self, task_id: int) -> None:
        """任务正常完成 / 失败后从在途集合移除（已取消的不重复记）。"""
        if task_id in self.in_flight:
            self.in_flight.remove(task_id)

    def cancel_all(self) -> list:
        """G09 step 4 — 关闭标签：取消所有在途任务 + 标记资源已释放。
        返回被取消的任务 id 列表（供审计 / 释放 CPU/GPU 资源记账）。"""
        out = list(self.in_flight)
        for tid in out:
            if tid not in self.cancelled:
                self.cancelled.append(tid)
        self.in_flight = []
        # 后台定时器全部暂停（关闭中）
        self.timers = set()
        self.released = True
        return out

    def throttle_timers(self, level: str) -> None:
        """G09 step 4 — 后台标签定时器策略：*level* 为
        ``TIMER_THROTTLED``（节流）/ ``TIMER_PAUSED``（关闭中）。"""
        self._timer_policy = level

    @property
    def timer_policy(self) -> str:
        return getattr(self, "_timer_policy", TIMER_NORMAL)


class TabTaskGate:
    """所有标签的任务 / 定时器门控集合。"""

    def __init__(self) -> None:
        self._tabs: dict[int, TabTasks] = {}

    def add(self, tab_id: int) -> TabTasks:
        t = TabTasks(tab_id=tab_id)
        self._tabs[tab_id] = t
        return t

    def get(self, tab_id: int) -> Optional[TabTasks]:
        return self._tabs.get(tab_id)

    def __len__(self) -> int:
        return len(self._tabs)

    # ------------------------------------------------------------------
    # G09 step 4
    # ------------------------------------------------------------------
    def background(self, tab_id: int) -> None:
        """切到后台：定时器节流（不取消任务——后台标签仍加载，只是
        减少重绘 / 派发频率）。"""
        t = self._tabs.get(tab_id)
        if t is not None:
            t.throttle_timers(TIMER_THROTTLED)

    def foreground(self, tab_id: int) -> None:
        """切回前台：定时器恢复正常派发。"""
        t = self._tabs.get(tab_id)
        if t is not None:
            t.throttle_timers(TIMER_NORMAL)

    def close(self, tab_id: int) -> list:
        """G09 step 4 — 关闭标签：取消在途任务 + 释放 CPU/GPU 资源
        记账 + 移除门控条目。返回被取消的任务 id 列表。"""
        t = self._tabs.pop(tab_id, None)
        if t is None:
            return []
        return t.cancel_all()


# ----------------------------------------------------------------------
# 统一生命周期协议（G09 step 1+2+4 组合）
# ----------------------------------------------------------------------
class TabLifecycle:
    """把稳定 tab_id 分配 + 每标签呈现 + 任务门控组合成"切 / 关标签"
    的统一入口（纯逻辑核心；Tk 侧的 widget 重绑 / 画布降重绘在 GUI
    层消费这里的呈现 / 任务决策）。

    与 :class:`TabIdentityRegistry`（G09 step 1 稳定身份）配合：

    * :meth:`new_tab` — 分配 tab_id + 呈现 + 任务门控（首个标签默认
      前台）。
    * :meth:`activate(tab_id)` — G09 step 2：切换只改前台呈现（激活
      标签全量重绘 + 正常定时器；其余后台降重绘 + 节流），**不重建
      文档**（文档 / 历史以 tab_id 存 registry 资源）。
    * :meth:`close_tab(tab_id)` — G09 step 4：取消该标签在途任务 +
      释放资源 + 经 registry 失效 tab_id（旧回调 / 旧资源不串页）。
    """

    def __init__(self, registry=None,
                 presentation: Optional[TabPresentationSet] = None,
                 gate: Optional[TabTaskGate] = None) -> None:
        self.registry = registry
        self.presentation = presentation or TabPresentationSet()
        self.gate = gate or TabTaskGate()
        # 审计：被关闭标签取消的任务（诊断区 / G09 step 4 用）
        self._cancelled: dict[int, list] = {}

    # ------------------------------------------------------------------
    # 标签创建
    # ------------------------------------------------------------------
    def new_tab(self) -> int:
        """分配稳定 tab_id，挂上呈现 + 任务门控；注册到 registry
        （若提供）。首个标签默认前台。"""
        if self.registry is not None:
            tab_id = self.registry.allocate()
        else:
            # 无 registry：用递增计数当稳定 id（纯逻辑路径）
            tab_id = len(self.presentation) + 1
        p = self.presentation.add(tab_id)
        self.gate.add(tab_id)
        # 首个标签默认前台
        if len(self.presentation) == 1:
            p.active = True
        return tab_id

    # ------------------------------------------------------------------
    # G09 step 2 — 前台切换（只改呈现，不重建文档）
    # ------------------------------------------------------------------
    def activate(self, tab_id: int) -> TabPresentation:
        """切到 *tab_id* 前台：呈现集激活（全量重绘 + 正常定时器），
        其余标签后台（降重绘 + 定时器节流）；任务门控同步前 / 后台。
        **不触碰** 任何标签的文档 / 历史 / 在途任务（G09 step 2）。"""
        self.presentation.activate(tab_id)
        for p in self.presentation.all():
            if p.tab_id == tab_id:
                self.gate.foreground(p.tab_id)
            else:
                self.gate.background(p.tab_id)
        return self.presentation.get(tab_id)

    # ------------------------------------------------------------------
    # G09 step 4 — 关闭标签（取消任务 + 释放资源 + 失效 tab_id）
    # ------------------------------------------------------------------
    def close_tab(self, tab_id: int) -> list:
        """关闭 *tab_id*：取消在途任务 + 释放 CPU/GPU 资源记账 +
        移除呈现 / 门控 + 经 registry 失效（旧回调 / 资源不串页）。
        返回被取消的任务 id 列表。"""
        cancelled = self.gate.close(tab_id)
        self._cancelled[tab_id] = cancelled
        self.presentation.remove(tab_id)
        if self.registry is not None:
            # 经 registry 失效 tab_id（旧 idx 回调 no-op）
            idx = self.registry.idx_of(tab_id)
            if idx is not None:
                self.registry.invalidate(idx)
        # 若关闭的是前台标签，把前台让给剩余标签（或空）
        active = self.presentation.active_tab_id()
        if active is None and len(self.presentation) > 0:
            first = self.presentation.all()[0].tab_id
            self.activation_activate(first)
        return cancelled

    def activation_activate(self, tab_id: int) -> None:
        self.activate(tab_id)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def active_tab_id(self) -> Optional[int]:
        return self.presentation.active_tab_id()

    def tab_count(self) -> int:
        return len(self.presentation)

    def cancelled_tasks(self, tab_id: int) -> list:
        """审计：某标签被关闭时取消的任务（诊断区用）。"""
        return list(self._cancelled.get(tab_id, []))

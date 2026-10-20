"""R7-S04 第 4 点 — 页面维度独立资源限额（纯逻辑层，不 import 运行时）。

审计 S04 第 4 点：页面 **DOM 数量/深度**、**源码字节**、**图像像素**、
**字体表/组件**、**任务队列**与 **IPC 队列**设**独立**限额（与本
模块所在批次已有的网络原始/解压字节 ``PageBudget`` 正交——那层只
限字节，这里限页面结构 / 计算资源维度）。

本模块只提供**决策**（该维度还剩多少、这一笔要不要拒绝），不做
I/O、不读时钟、不 import 运行时（subprocess / threading /
multiprocessing / socket）。真实"DOM 超限截断树""timer 超限拒入队"
的执行由各入口点（build.py / js.py / document_process.py /
layout.py / font.py）经 :meth:`PageResourceLimits.admit` 调用——
超限即拒（分配前防护，审计 S04 第 1 点"不能先读完/创建完再检查"）。

契约（测试锁定）：
  * 六维度各有**独立**上限，缺省值显式给定（避免"缺省即无限制"
    这种隐性缺省）；上限 <= 0 表示**该维度未启用**（放行）；
  * 逐维记账：``charge(dim, n)`` 累计已用；``would_exceed(dim, n)``
    纯判定"再加 n 是否超"；``remaining(dim)`` 剩余可容纳；
  * ``admit(dim, n)`` 分配前防护：超限额即返回 False（**不**记账、
    **不**放行）；调用方据此拒收该笔资源；
  * 全维度 :attr:`exhausted` 任一用满 → 页面级拒收（与 PageBudget
    的"用满即拒绝"同口径，但维度独立）；
  * 所有判定无副作用、无 I/O、无时钟。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PageDimension(str, Enum):
    """R7-S04 第 4 点 — 页面资源限额的六个独立维度。

    每个维度一个上限、一份已用计数，互不干扰（DOM 节点数再多，
    只要图像像素没超就不拒绝图像；与网络字节预算正交）。
    """
    DOM_NODES = "dom_nodes"            # 元素 + 文本节点总数
    DOM_DEPTH = "dom_depth"            # 嵌套深度（祖先链长度）
    SOURCE_BYTES = "source_bytes"      # 解析的 HTML/JS 源码字节
    IMAGE_PIXELS = "image_pixels"      # 解码后图像像素总数（w×h）
    FONT_COMPONENTS = "font_components"  # 字体表 / 组件（face/表数）
    TASK_QUEUE = "task_queue"          # timer + microtask 队长度
    IPC_QUEUE = "ipc_queue"            # 跨进程命令 / 帧在途长度


#: 默认上限（防"缺省即无限制"；均为保守可测值，调用方可按产品
#: 目标调大/调小）。None 表示"未配置 → 视为无限制"（显式缺省，
#: 与 PageBudget 的"必须显式传"不同：这里允许调用方不配某维）。
DEFAULT_LIMITS = {
    PageDimension.DOM_NODES: 100_000,
    PageDimension.DOM_DEPTH: 500,
    PageDimension.SOURCE_BYTES: 4 * 1024 * 1024,   # 4 MiB
    PageDimension.IMAGE_PIXELS: 4 * 1024 * 1024,    # 4 Mpx（如 2K×2K）
    PageDimension.FONT_COMPONENTS: 256,
    PageDimension.TASK_QUEUE: 10_000,
    PageDimension.IPC_QUEUE: 1024,
}


class PageLimitExceeded(Exception):
    """R7-S04 第 4 点 — 某维度限额已用满，剩余资源拒绝分配。

    与 :class:`PageBudget` 的"网络字节用满"同等待遇：调用方把它
    归入"该维度已封顶，展示错误页 / 截断 / 拒入队"的失败类。
    携带 :attr:`dimension` 便于诊断是哪一维超了。"""
    def __init__(self, dimension: PageDimension,
                 limit: int, used: int, requested: int) -> None:
        super().__init__(
            f"{dimension.value} limit {limit} reached "
            f"(used={used} requested={requested})")
        self.dimension = dimension
        self.limit = limit
        self.used = used
        self.requested = requested


@dataclass
class PageResourceLimits:
    """R7-S04 第 4 点 — 页面维度独立资源限额（纯逻辑，逐维记账）。

    * 构造时 ``limits`` 可给**部分**维度（缺省取 :data:`DEFAULT_LIMITS`）；
      显式传 ``0`` 或负数表示"该维度未启用"（放行，remaining=∞）。
    * 每维独立的已用计数（``_used``），互不串用。

    本对象无"全局用满"语义：每个维度独立封顶，某维用满只拒该维
    的剩余分配，不影响其它维度（与网络 ``PageBudget`` 的"两维
    字节合计"不同——这里维度间正交）。
    """
    limits: dict = field(default_factory=dict)
    #: 各维度已用计数（懒初始化：仅 charge/admit 过的维度入表，
    #: 避免 6 维全建 dict 的内存浪费）。
    _used: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for dim in PageDimension:
            # 显式配置 > 默认 > 未配置（None = 无限制）
            if dim in self.limits:
                limit = self.limits[dim]
            else:
                limit = DEFAULT_LIMITS.get(dim)
            self.limits[dim] = int(limit) if limit is not None else 0

    # -- 查询 ---------------------------------------------------------

    def limit(self, dim: PageDimension) -> int:
        """该维度的上限（0 = 未启用，恒放行）。"""
        return self.limits.get(dim, 0)

    def used(self, dim: PageDimension) -> int:
        """该维度已用计数（未记账 = 0）。"""
        return self._used.get(dim, 0)

    def remaining(self, dim: PageDimension) -> float:
        """该维度还能容纳多少（未启用 / 无限制 → ∞）。"""
        cap = self.limit(dim)
        if cap <= 0:
            return float("inf")
        return max(0, cap - self.used(dim))

    def exhausted(self, dim: PageDimension) -> bool:
        """该维度是否已用满（== 上限即算耗尽，审计 S04"用满即
        拒绝"同口径；未启用维度恒 False）。"""
        cap = self.limit(dim)
        if cap <= 0:
            return False
        return self.used(dim) >= cap

    # -- 记账 ---------------------------------------------------------

    def charge(self, dim: PageDimension, n: int = 1) -> None:
        """一次资源分配之后记账（先记账后继续；失败 / 拒绝的不计）。

        ``n`` 可为 0 / 正（负按 0 处理，防恶意负记账绕过）。"""
        n = max(0, int(n))
        if n == 0:
            return
        self._used[dim] = self.used(dim) + n

    def would_exceed(self, dim: PageDimension, n: int = 1) -> bool:
        """纯判定：再加 *n* 是否会超上限（不记账、无副作用）。

        未启用维度（cap<=0）恒 False（放行）。"""
        cap = self.limit(dim)
        if cap <= 0:
            return False
        return self.used(dim) + max(0, int(n)) > cap

    def admit(self, dim: PageDimension, n: int = 1) -> bool:
        """**分配前防护**（审计 S04 第 1 点"不能先创建完再检查"）。

        返回 True = 允许分配这一笔；False = 超限，调用方**拒收**
        该笔资源（不记账、不创建）。与 :meth:`would_exceed` 等价，
        语义上强调"先判后做"。"""
        return not self.would_exceed(dim, n)

    def admit_or_raise(self, dim: PageDimension, n: int = 1) -> None:
        """同 :meth:`admit`，但超限抛 :class:`PageLimitExceeded`
        （调用方要统一异常处理时用）。"""
        if not self.admit(dim, n):
            raise PageLimitExceeded(
                dim, self.limit(dim), self.used(dim), n)

    def reset(self, dim: Optional[PageDimension] = None) -> None:
        """重置计数（导航 / 关标签时调用；``dim=None`` 清全部）。"""
        if dim is None:
            self._used.clear()
        else:
            self._used.pop(dim, None)

    def describe(self) -> str:
        parts = []
        for dim in PageDimension:
            cap = self.limit(dim)
            used = self.used(dim)
            if cap <= 0:
                parts.append(f"{dim.value}=off")
            else:
                parts.append(f"{dim.value}={used}/{cap}")
        return "PageResourceLimits(" + ", ".join(parts) + ")"


__all__ = [
    "PageDimension", "PageResourceLimits", "PageLimitExceeded",
    "DEFAULT_LIMITS",
]

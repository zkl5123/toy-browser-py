"""C05 — 样式缓存键 + 失效范围 + 命中率统计（纯逻辑核心，批次 11）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C05：

> 证据与位置：``toy_browser/style/css.py`` 已有 rulesheet/选择器等
> 缓存（P34.1/P34.5），不能再叠一套无失效条件的全局缓存。
>
> 1. 统计解析、选择器匹配、级联与属性解析各阶段命中率，找出实际
>    重复工作。
> 2. 以 stylesheet_revision、DOM 结构/属性版本、继承输入、媒体条件、
>    交互状态构成明确缓存键。
> 3. 分离不可变计算样式与每节点可变交互状态，复用样式对象时避免
>    把一个节点的状态写到其他节点。
> 4. DOM class/id/style、祖先变化、媒体条件、字体资源与 hover/focus
>    分别生成适当失效范围。
> 5. 所有长期缓存有上限并按 Document 管理；节点释放后不依赖可复用
>    的 Python id 单独识别身份。

本模块是**纯逻辑核心**（不依赖 tkinter / 网络），把 css.py 里散点的
缓存键 / 失效条件显式化成可测试的数据结构，并补 C05 要求的
**命中率统计**（找出实际重复工作）：

* :class:`StyleCacheKey` — 明确缓存键：``stylesheet_revision`` +
  ``media_conditions``（媒体条件）+ ``interaction``（hover/focus 等
  交互状态）。C05 步骤 2 的五要素中，DOM 结构/属性版本与继承输入是
  节点级（per-node）而非样式表级，不进本键（避免把节点状态写进
  全局键——步骤 3 "避免把一个节点的状态写到其他节点"）。
* :class:`InvalidateScope` — 失效范围分类（C05 步骤 4）：哪个改动
  触发哪种失效（全量 / 仅节点 / 仅交互 / 仅媒体 / 字体资源）。
* :class:`HitRateStats` — 各阶段命中率统计（C05 步骤 1）：记录
  命中 / 未命中次数，算命中率，找出"实际重复工作"（命中率为 0 的
  阶段说明缓存白做）。

与 css.py 现有缓存的关系：本模块**不叠**新缓存，只是把现有
P34.5 candidate-rule cache 的键语义 / 失效范围 / 命中统计抽成可独立
测试的纯逻辑；css.py 仍持有实际缓存实例（按 Document 管理，C05
步骤 5），本模块提供"该用什么键、该失效多大范围、命中率多少"的
决策层。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# 失效范围分类（C05 步骤 4：不同改动生成适当失效范围）
# ----------------------------------------------------------------------
# 失效范围常量（按"影响面"从大到小）
INV_ALL = "all"                  # 全量：stylesheet 变了 / 字体资源变了
INV_NODE = "node"                # 仅节点：DOM class/id/style 变了
INV_INTERACTION = "interaction"  # 仅交互：hover/focus 变了
INV_MEDIA = "media"              # 仅媒体条件：resize / 媒体查询重估
INV_FONT = "font"                # 字体资源加载

# 失效范围大小序（all 最大；用于"取并集"时保留影响面最大的）
_INV_ORDER = {INV_FONT: 4, INV_MEDIA: 3, INV_INTERACTION: 2,
              INV_NODE: 1, INV_ALL: 5}


def invalidate_scope(change: str) -> str:
    """C05 步骤 4 — 一个"改动"对应的失效范围。

    * *change* — 改动类型（字符串标签）：
      - ``"stylesheet_revision"`` / ``"font_resource"`` → ``INV_ALL``
        （stylesheet 变了 / 字体资源变了，全量重解析 + 重匹配）。
      - ``"class"`` / ``"id"`` / ``"style"`` → ``INV_NODE``
        （DOM 属性变化，只失效相关节点，不重解析 stylesheet）。
      - ``"hover"`` / ``"focus"`` → ``INV_INTERACTION``
        （交互状态变化，只失效交互态样式，不重解析 / 不重匹配普通
        规则）。
      - ``"media"`` / ``"resize"`` → ``INV_MEDIA``
        （媒体条件重估，只失效媒体查询相关规则）。

    未知改动类型保守返回 ``INV_ALL``（正确性优先，宁多失效不多匹配）。
    """
    c = (change or "").lower()
    if c in ("stylesheet_revision", "font_resource"):
        return INV_ALL
    if c in ("class", "id", "style", "dom"):
        return INV_NODE
    if c in ("hover", "focus", "interaction"):
        return INV_INTERACTION
    if c in ("media", "resize", "media_query"):
        return INV_MEDIA
    return INV_ALL


def widen_scope(a: str, b: str) -> str:
    """两个失效范围取并集（保留影响面最大的那个）。

    当同一文档有多个改动同时发生，失效范围取并集（大者胜），
    避免小范围失效后大范围改动的结果被残留。"""
    return a if _INV_ORDER.get(a, 0) >= _INV_order_of(b) else b


def _INV_order_of(s: str) -> int:
    return _INV_ORDER.get(s, 0)


# ----------------------------------------------------------------------
# 明确缓存键（C05 步骤 2：stylesheet_revision + 媒体 + 交互）
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class StyleCacheKey:
    """C05 步骤 2 — 样式匹配缓存的明确键（hashable，可作 dict key）。

    * ``stylesheet_revision`` — 样式表版本号（stylesheet 文本变则 bump；
      字体资源变也 bump，见 :func:`invalidate_scope`）。
    * ``media`` — 当前生效的媒体条件快照（frozenset 或 tuple，如
      ``{"min-width: 800px", "light"}``；resize / 媒体查询重估时变）。
    * ``interaction`` — 交互状态快照（frozenset，如 ``{"hover:btn1",
      "focus:input2"}``；hover/focus 变化时变）。

    不含 DOM 结构 / 继承输入（节点级，不进全局键）；同一节点复用
    样式对象时，其交互态单独存（C05 步骤 3 "避免把一个节点的状态写到
    其他节点"）。"""
    stylesheet_revision: int
    media: frozenset = frozenset()
    interaction: frozenset = frozenset()

    def as_tuple(self) -> tuple:
        """供日志 / 诊断的扁平表示。"""
        return (self.stylesheet_revision, tuple(sorted(self.media)),
                tuple(sorted(self.interaction)))


# ----------------------------------------------------------------------
# 命中率统计（C05 步骤 1：各阶段命中率，找出实际重复工作）
# ----------------------------------------------------------------------
@dataclass
class HitRateStats:
    """C05 步骤 1 — 各阶段命中率统计。

    * ``phase_hits`` / ``phase_misses`` — 每阶段的命中 / 未命中次数
      （"parse" / "match" / "cascade" / "property" 等）。
    * :meth:`record` 记一次命中 / 未命中。
    * :meth:`rate` 算某阶段命中率（0.0–1.0；无样本返回 1.0——无重复
      工作即"不需要缓存"，不算白做）。
    * :meth:`summary` 人类可读摘要（诊断区 / 性能追踪消费，C05 交付
      "记录真实命中率和分段耗时"）。
    """
    phase_hits: dict = field(default_factory=dict)
    phase_misses: dict = field(default_factory=dict)

    def record(self, phase: str, hit: bool) -> None:
        if hit:
            self.phase_hits[phase] = self.phase_hits.get(phase, 0) + 1
        else:
            self.phase_misses[phase] = self.phase_misses.get(phase, 0) + 1

    def rate(self, phase: str) -> float:
        h = self.phase_hits.get(phase, 0)
        m = self.phase_misses.get(phase, 0)
        total = h + m
        if total == 0:
            return 1.0
        return h / total

    def all_phases(self) -> list[str]:
        """出现过的所有阶段（命中或未命中）。"""
        return sorted(set(list(self.phase_hits) + list(self.phase_misses)))

    def summary(self) -> str:
        lines = []
        for p in self.all_phases():
            lines.append(f"  {p:<12} hit={self.phase_hits.get(p, 0):>5} "
                         f"miss={self.phase_misses.get(p, 0):>5} "
                         f"rate={self.rate(p):.2%}")
        return "；".join(lines) if lines else "（无样本）"

    def reset(self) -> None:
        self.phase_hits.clear()
        self.phase_misses.clear()


# ----------------------------------------------------------------------
# 按 Document 管理的缓存上限（C05 步骤 5）
# ----------------------------------------------------------------------
def document_cache_budget(default_max: int = 4096) -> dict:
    """C05 步骤 5 — 返回"按 Document 管理"的缓存键预算（上限）。

    实际缓存实例仍由 css.py 持有（P34.5 candidate-rule cache 上限
    4096）；本函数只返回决策层的**预算字典**：

    * ``"max_entries"`` — 单文档缓存上限（默认 4096，与 css.py
      ``_CANDIDATE_RULES_CACHE_MAX`` 一致）。
    * 调用方在文档销毁（C01 ``new_context`` / 关标签）时按 document_id
      清掉该文档的缓存（节点释放后不依赖可复用的 Python id 识别身份，
      C05 步骤 5）。

    本层不创建缓存实例（避免叠无失效条件的全局缓存），只声明预算
    与"按 document_id 清"的约定。"""
    return {"max_entries": default_max, "clear_on": "document_destroy"}


# ----------------------------------------------------------------------
# 失效决策（C05 步骤 4 组合）
# ----------------------------------------------------------------------
def apply_invalidation(current_key: StyleCacheKey,
                       change: str) -> StyleCacheKey:
    """C05 步骤 4 — 一个改动发生时，缓存键如何更新。

    * ``stylesheet_revision`` / ``font_resource`` → bump
      ``stylesheet_revision``（全量失效）。
    * ``media`` / ``resize`` → 更新 ``media``（媒体条件失效）。
    * ``hover`` / ``focus`` → 更新 ``interaction``（交互失效）。
    * ``class`` / ``id`` / ``style`` → 键不变（节点级失效，靠节点自己的
      版本标记，不进全局键——C05 步骤 3）。

    返回新键（调用方据此决定命中还是重算）。"""
    scope = invalidate_scope(change)
    c = (change or "").lower()
    if scope == INV_ALL:
        # stylesheet 或字体变了：bump revision
        return StyleCacheKey(
            stylesheet_revision=current_key.stylesheet_revision + 1,
            media=current_key.media,
            interaction=current_key.interaction)
    # 其余 scope（node/interaction/media/font）不改键；调用方按 scope
    # 决定失效多大范围。键保持原样（节点级失效在节点维度处理）。
    return current_key

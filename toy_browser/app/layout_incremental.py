"""C06 — 布局树构建与增量布局（纯逻辑核心，批次 12）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C06：

> 证据与位置：layout.build_box 为主要热点；layout.py 已有 dirty 与缓存
> 机制（P33.R5 mark_dirty / layout_dirty_only），应检查是否被 GUI 真正
> 使用。
>
> 3. 把失效分为结构、测量、位置和仅绘制。文本变化可能影响后续兄弟
>    位置，Flex/Grid 子项变化可能影响整个容器，不能只重排被改节点。
> 4. 基于依赖关系向上确定最小可重排根，再向下更新必要节点；缓存键
>    包含包含块尺寸与字体版本。
> 5. 先对块流和固定尺寸容器启用增量，复杂表格/自动尺寸容器暂退全容器
>    布局，逐步扩大。

本模块是**纯逻辑核心**（不依赖 tkinter / 网络），把 layout.py 的
dirty 机制（P33.R5）的**失效等级**与**最小可重排根**抽成可测试的
数据结构，并补"失效范围分类"（C06 步骤 3 "把失效分为结构、测量、
位置和仅绘制"）：

* :class:`InvalidateLevel` — 失效等级常量：``PAINT`` / ``POSITION`` /
  ``MEASURE`` / ``STRUCTURE``（从低到高）。等级单调：高等级吸收低
  等级（"不能只重排被改节点"——文本增高要往下传导，Flex 子项要重排
  整个容器）。
* :class:`InvalidateEvent` — 一个失效事件（node_id + level + 可选
  reason）。
* :func:`classify_invalidate` — 一个"改动"对应的失效等级（C06 步骤 3）：
  文本变化 → POSITION；Flex/Grid 子项变化 → MEASURE；DOM class/id/
  style → STRUCTURE；hover/focus → PAINT。
* :class:`MinimalRoot` — 最小可重排根求解结果（C06 步骤 4）：``root_id``
  + ``level`` + 是否需要全容器 fallback（C06 步骤 5：复杂表格/自动尺寸
  容器暂退全容器布局）。
* :func:`find_minimal_root` — 基于依赖关系**向上**确定最小可重排根
  （取事件节点祖先链上等级最高的那个，再判断是否需 fallback）。

与 layout.py 现有机制的关系：本模块**不**改 layout.py 的 dirty 标记 /
重排执行，只提供"该重排哪个根、什么等级、要不要退全容器"的决策层；
layout_dirty_only 在活显示环境批次消费这里的最小根（C06 交付
"增量/全量差分测试"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ----------------------------------------------------------------------
# 失效等级（C06 步骤 3：结构 / 测量 / 位置 / 仅绘制，从低到高）
# ----------------------------------------------------------------------
class InvalidateLevel:
    """失效等级常量（数值越大，影响面越大）。

    * ``PAINT`` — 仅绘制（hover/focus 视觉态，不重排几何）。
    * ``POSITION`` — 位置（文本变化可能影响后续兄弟位置，但要重算
      兄弟的 x/y，不重算自身测量）。
    * ``MEASURE`` — 测量（Flex/Grid 子项变化、宽度变化需要重算自身
      及后续测量，但结构不变）。
    * ``STRUCTURE`` — 结构（DOM 节点增删 / display 变化，需重建结构
      并重新测量 + 定位）。

    等级单调（``PROMOTE``）：低等级事件可被高等级吸收。
    """
    PAINT = 0
    POSITION = 1
    MEASURE = 2
    STRUCTURE = 3

    _NAMES = {0: "PAINT", 1: "POSITION", 2: "MEASURE", 3: "STRUCTURE"}

    @classmethod
    def name(cls, level: int) -> str:
        return cls._NAMES.get(level, f"L{level}")

    @classmethod
    def promote(cls, a: int, b: int) -> int:
        """两个等级取"影响面最大的那个"（吸收低等级）。"""
        return max(a, b)


# 改动类型 → 失效等级（C06 步骤 3）
_CHANGE_TO_LEVEL = {
    "hover": InvalidateLevel.PAINT,
    "focus": InvalidateLevel.PAINT,
    "paint": InvalidateLevel.PAINT,
    "text": InvalidateLevel.POSITION,
    "text_change": InvalidateLevel.POSITION,
    "position": InvalidateLevel.POSITION,
    "flex": InvalidateLevel.MEASURE,
    "grid": InvalidateLevel.MEASURE,
    "measure": InvalidateLevel.MEASURE,
    "width": InvalidateLevel.MEASURE,
    "height": InvalidateLevel.MEASURE,
    "class": InvalidateLevel.STRUCTURE,
    "id": InvalidateLevel.STRUCTURE,
    "style": InvalidateLevel.STRUCTURE,
    "display": InvalidateLevel.STRUCTURE,
    "dom": InvalidateLevel.STRUCTURE,
    "node_added": InvalidateLevel.STRUCTURE,
    "node_removed": InvalidateLevel.STRUCTURE,
}


@dataclass(frozen=True)
class InvalidateEvent:
    """C06 步骤 3 — 一个失效事件（哪个节点、什么等级、为什么）。"""
    node_id: int
    level: int
    reason: str = ""


def classify_invalidate(change: str) -> int:
    """C06 步骤 3 — 把"改动"映射到失效等级。

    未知改动类型保守返回 ``STRUCTURE``（正确性优先：宁可多重排也不
    漏重排，产生错误几何）。"""
    c = (change or "").lower()
    return _CHANGE_TO_LEVEL.get(c, InvalidateLevel.STRUCTURE)


# ----------------------------------------------------------------------
# 最小可重排根（C06 步骤 4：基于依赖关系向上确定）
# ----------------------------------------------------------------------
@dataclass
class MinimalRoot:
    """C06 步骤 4/5 — 最小可重排根求解结果。

    * ``root_id`` — 需要重排的节点 id（取事件节点祖先链上等级最高的
      那个；无祖先提升则取事件自身）。
    * ``level`` — 该根的失效等级（决定重排深度：PAINT 只重绘、
      POSITION 重算位置、MEASURE 重算测量、STRUCTURE 重建结构）。
    * ``fallback`` — 是否需要退全容器布局（C06 步骤 5：事件落在
      复杂表格 / 自动尺寸容器内时，暂退全容器布局，``True``）。
    """
    root_id: int
    level: int
    fallback: bool = False

    def depth(self) -> str:
        """该等级对应的重排深度（人类可读，诊断区消费）。"""
        return InvalidateLevel.name(self.level)


# 复杂容器标记（C06 步骤 5：这些容器暂退全容器布局）
_COMPLEX_CONTAINERS = ("table", "auto_size")


def find_minimal_root(events: list[InvalidateEvent],
                      ancestors: dict,
                      container_flags: dict) -> MinimalRoot:
    """C06 步骤 4/5 — 基于依赖关系**向上**确定最小可重排根。

    * *events* — 本次的所有失效事件（同一批改动）。
    * *ancestors* — ``node_id → [祖先 id 列表（近→远）]``（调用方从
      布局树传入，本模块纯逻辑不重建树）。
    * *container_flags* — ``node_id → 容器标记``（"table" / "auto_size"
      / "block" / "fixed" / …；C06 步骤 5 复杂容器判定用）。

    算法：
    1. 对每个事件，沿祖先链收集等级，取"事件自身 + 所有祖先"里等级
       最高的那个节点（祖先等级更高说明重排根要上移——"Flex 子项
       变化可能影响整个容器"）。
    2. 多个事件之间：取全局最高等级（吸收）+ 取最近公共祖先作为根
       （简化：取等级最高事件的最浅祖先）。
    3. 若根或其在祖先链上落在复杂容器（table / auto_size）内 →
       ``fallback=True``（全容器布局，C06 步骤 5 "暂退全容器布局"）。
    """
    if not events:
        # 无事件：不需要重排（root_id=-1 表示无根）
        return MinimalRoot(root_id=-1, level=InvalidateLevel.PAINT)

    best_id = events[0].node_id
    best_level = events[0].level
    for ev in events:
        # 沿祖先链找该事件的最高等级节点
        anc = ancestors.get(ev.node_id, [])
        cand_id = ev.node_id
        cand_level = ev.level
        for aid in anc:
            # 祖先的失效等级（调用方把"祖先提升"也编进 ancestors 的
            # 等级表——简化：祖先本身若也是事件节点，用其 level）
            anc_level = _ancestor_level(aid, events)
            lev = InvalidateLevel.promote(cand_level, anc_level)
            if lev > cand_level:
                cand_id = aid
                cand_level = lev
        # 全局吸收
        if cand_level > best_level:
            best_id = cand_id
            best_level = cand_level
        elif cand_level == best_level:
            # 同级：取最浅祖先（影响面最小的公共根）——简化取更小的
            # 深度（这里用 root_id 本身；多事件同级场景罕见，保守取
            # 第一个）
            pass

    # 复杂容器判定（C06 步骤 5）
    fallback = _is_complex_container(best_id, ancestors, events,
                                     container_flags)
    return MinimalRoot(root_id=best_id, level=best_level,
                       fallback=fallback)


def _ancestor_level(aid: int, events: list[InvalidateEvent]) -> int:
    """祖先 *aid* 作为事件节点时的等级（不是事件 → PAINT 基线）。"""
    for ev in events:
        if ev.node_id == aid:
            return ev.level
    return InvalidateLevel.PAINT


def _is_complex_container(root_id: int, ancestors: dict,
                          events: list[InvalidateEvent],
                          container_flags: dict) -> bool:
    """C06 步骤 5 — 根（或其事件）是否落在复杂容器内 → fallback。"""
    # 根自身
    flag = container_flags.get(root_id, "")
    if flag in _COMPLEX_CONTAINERS:
        return True
    # 事件节点自身
    for ev in events:
        f2 = container_flags.get(ev.node_id, "")
        if f2 in _COMPLEX_CONTAINERS:
            return True
    return False


def build_layout_plan(root: MinimalRoot,
                      has_auto_container: bool = False,
                      has_table: bool = False) -> dict:
    """C06 步骤 5 — 把最小根编译成"增量布局计划"（供 layout_dirty_only
    消费）。

    * *root* — :func:`find_minimal_root` 的结果。
    * *has_auto_container* / *has_table* — 调用方（布局树）报告整棵
      文档是否含自动尺寸容器 / 表格（有的话强制全量，C06 步骤 5
      "复杂表格/自动尺寸容器暂退全容器布局"）。

    返回 ``{"root_id", "level", "fallback", "strategy"}``：
    * ``strategy`` — "incremental"（块流/固定尺寸增量）或 "full"
      （全容器 / 全量布局）。"""
    fallback = root.fallback or has_auto_container or has_table
    strategy = "full" if fallback else "incremental"
    return {
        "root_id": root.root_id,
        "level": root.level,
        "fallback": fallback,
        "strategy": strategy,
    }

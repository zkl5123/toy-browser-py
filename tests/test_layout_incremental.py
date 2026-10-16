"""C06 — 布局树构建与增量布局（纯逻辑核心）回归（round5 docs 批次 12）。

覆盖 toy_browser/app/layout_incremental.py：
- classify_invalidate：改动 → 失效等级（hover/focus → PAINT，text →
  POSITION，flex/grid/width/height → MEASURE，class/id/style/display/
  dom → STRUCTURE，未知保守 → STRUCTURE）
- InvalidateLevel：常量 + name + promote（取最高）
- find_minimal_root：无事件返回无根；单事件自身；祖先提升上移根；
  多事件全局吸收；复杂容器 fallback
- build_layout_plan：incremental vs full（has_auto_container/has_table
  强制全量）
"""
from __future__ import annotations

from toy_browser.app.layout_incremental import (
    InvalidateLevel, InvalidateEvent, MinimalRoot,
    classify_invalidate, find_minimal_root, build_layout_plan,
)


TESTS = []


# ----------------------------------------------------------------------
# classify_invalidate — 改动 → 失效等级（C06 步骤 3）
# ----------------------------------------------------------------------
def test_classify_paint_level():
    assert classify_invalidate("hover") == InvalidateLevel.PAINT
    assert classify_invalidate("focus") == InvalidateLevel.PAINT
    assert classify_invalidate("paint") == InvalidateLevel.PAINT


def test_classify_position_level():
    assert classify_invalidate("text") == InvalidateLevel.POSITION
    assert classify_invalidate("text_change") == InvalidateLevel.POSITION
    assert classify_invalidate("position") == InvalidateLevel.POSITION


def test_classify_measure_level():
    assert classify_invalidate("flex") == InvalidateLevel.MEASURE
    assert classify_invalidate("grid") == InvalidateLevel.MEASURE
    assert classify_invalidate("width") == InvalidateLevel.MEASURE
    assert classify_invalidate("height") == InvalidateLevel.MEASURE


def test_classify_structure_level():
    assert classify_invalidate("class") == InvalidateLevel.STRUCTURE
    assert classify_invalidate("id") == InvalidateLevel.STRUCTURE
    assert classify_invalidate("style") == InvalidateLevel.STRUCTURE
    assert classify_invalidate("display") == InvalidateLevel.STRUCTURE
    assert classify_invalidate("dom") == InvalidateLevel.STRUCTURE
    assert classify_invalidate("node_added") == InvalidateLevel.STRUCTURE
    assert classify_invalidate("node_removed") == InvalidateLevel.STRUCTURE


def test_classify_unknown_conservative_structure():
    """未知改动类型保守返回 STRUCTURE（正确性优先）。"""
    assert classify_invalidate("bogus") == InvalidateLevel.STRUCTURE
    assert classify_invalidate("") == InvalidateLevel.STRUCTURE
    assert classify_invalidate(None) == InvalidateLevel.STRUCTURE


def test_level_name_and_promote():
    assert InvalidateLevel.name(InvalidateLevel.PAINT) == "PAINT"
    assert InvalidateLevel.name(InvalidateLevel.STRUCTURE) == "STRUCTURE"
    # promote 取最高
    assert InvalidateLevel.promote(
        InvalidateLevel.PAINT, InvalidateLevel.MEASURE) == \
        InvalidateLevel.MEASURE
    assert InvalidateLevel.promote(
        InvalidateLevel.STRUCTURE, InvalidateLevel.POSITION) == \
        InvalidateLevel.STRUCTURE


# ----------------------------------------------------------------------
# find_minimal_root — 最小可重排根（C06 步骤 4）
# ----------------------------------------------------------------------
def test_root_no_events():
    r = find_minimal_root([], {}, {})
    assert r.root_id == -1
    assert r.level == InvalidateLevel.PAINT
    assert r.fallback is False


def test_root_single_event_no_ancestor():
    """单事件、无祖先提升：根 = 事件自身，等级 = 事件等级。"""
    ev = InvalidateEvent(node_id=5,
                         level=InvalidateLevel.POSITION,
                         reason="text")
    r = find_minimal_root([ev], {}, {})
    assert r.root_id == 5
    assert r.level == InvalidateLevel.POSITION
    assert r.fallback is False


def test_root_ancestor_promotes_up():
    """事件节点祖先链上有更高事件节点 → 根上移到该祖先（Flex 子项
    影响整个容器）。"""
    text_ev = InvalidateEvent(node_id=7,
                               level=InvalidateLevel.POSITION,
                               reason="text")
    # 节点 7 的祖先链 [3]，而 3 也是一个 MEASURE 事件节点
    flex_ev = InvalidateEvent(node_id=3,
                              level=InvalidateLevel.MEASURE,
                              reason="flex")
    ancestors = {7: [3]}
    r = find_minimal_root([text_ev, flex_ev], ancestors, {})
    # 全局最高等级是 MEASURE（节点 3）→ 根应是 3
    assert r.root_id == 3
    assert r.level == InvalidateLevel.MEASURE


def test_root_complex_container_fallback():
    """事件落在 table / auto_size 容器 → fallback=True（全容器布局）。"""
    ev = InvalidateEvent(node_id=9,
                         level=InvalidateLevel.POSITION,
                         reason="text")
    ancestors = {}
    container_flags = {9: "table"}
    r = find_minimal_root([ev], ancestors, container_flags)
    assert r.fallback is True
    r2 = find_minimal_root([ev], {}, {9: "auto_size"})
    assert r2.fallback is True


def test_root_block_no_fallback():
    """事件落在 block / fixed 容器 → 不 fallback（块流增量）。"""
    ev = InvalidateEvent(node_id=9,
                         level=InvalidateLevel.POSITION,
                         reason="text")
    r = find_minimal_root([ev], {}, {9: "block"})
    assert r.fallback is False
    r2 = find_minimal_root([ev], {}, {9: "fixed"})
    assert r2.fallback is False


# ----------------------------------------------------------------------
# build_layout_plan — 增量布局计划（C06 步骤 5）
# ----------------------------------------------------------------------
def test_plan_incremental_when_no_complex():
    """无复杂容器：块流 / 固定尺寸增量。"""
    root = MinimalRoot(root_id=1, level=InvalidateLevel.MEASURE)
    p = build_layout_plan(root)
    assert p["strategy"] == "incremental"
    assert p["fallback"] is False
    assert p["root_id"] == 1
    assert p["level"] == InvalidateLevel.MEASURE


def test_plan_full_when_auto_container():
    """整棵文档含自动尺寸容器 → 强制全量（C06 步骤 5）。"""
    root = MinimalRoot(root_id=1, level=InvalidateLevel.POSITION)
    p = build_layout_plan(root, has_auto_container=True)
    assert p["strategy"] == "full"
    assert p["fallback"] is True


def test_plan_full_when_table():
    """整棵文档含表格 → 强制全量。"""
    root = MinimalRoot(root_id=1, level=InvalidateLevel.PAINT)
    p = build_layout_plan(root, has_table=True)
    assert p["strategy"] == "full"
    assert p["fallback"] is True


def test_plan_full_when_root_fallback():
    """根自身标记 fallback（复杂容器）→ 全量。"""
    root = MinimalRoot(root_id=2, level=InvalidateLevel.MEASURE,
                       fallback=True)
    p = build_layout_plan(root)
    assert p["strategy"] == "full"


TESTS.append(("test_classify_paint_level", test_classify_paint_level))
TESTS.append(("test_classify_position_level",
              test_classify_position_level))
TESTS.append(("test_classify_measure_level",
              test_classify_measure_level))
TESTS.append(("test_classify_structure_level",
              test_classify_structure_level))
TESTS.append(("test_classify_unknown_conservative_structure",
              test_classify_unknown_conservative_structure))
TESTS.append(("test_level_name_and_promote",
              test_level_name_and_promote))
TESTS.append(("test_root_no_events", test_root_no_events))
TESTS.append(("test_root_single_event_no_ancestor",
              test_root_single_event_no_ancestor))
TESTS.append(("test_root_ancestor_promotes_up",
              test_root_ancestor_promotes_up))
TESTS.append(("test_root_complex_container_fallback",
              test_root_complex_container_fallback))
TESTS.append(("test_root_block_no_fallback",
              test_root_block_no_fallback))
TESTS.append(("test_plan_incremental_when_no_complex",
              test_plan_incremental_when_no_complex))
TESTS.append(("test_plan_full_when_auto_container",
              test_plan_full_when_auto_container))
TESTS.append(("test_plan_full_when_table", test_plan_full_when_table))
TESTS.append(("test_plan_full_when_root_fallback",
              test_plan_full_when_root_fallback))

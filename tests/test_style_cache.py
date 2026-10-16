"""C05 — 样式缓存键 + 失效范围 + 命中率统计（纯逻辑核心）回归
（round5 docs 批次 11）。

覆盖 toy_browser/app/style_cache.py：
- invalidate_scope：不同改动 → 适当失效范围（stylesheet/font → all，
  class/id/style → node，hover/focus → interaction，media/resize →
  media，未知保守 → all）
- widen_scope：两个失效范围取并集（大者胜）
- StyleCacheKey：明确缓存键（stylesheet_revision + media +
  interaction），hashable，as_tuple 扁平表示
- HitRateStats：各阶段命中率统计（命中/未命中次数、rate、summary、
  无样本返回 1.0）
- apply_invalidation：stylesheet/font bump revision；其余 scope 键不变
  （节点级失效在节点维度处理）
- document_cache_budget：按 Document 管理的缓存上限约定
"""
from __future__ import annotations

from toy_browser.app.style_cache import (
    StyleCacheKey, HitRateStats,
    invalidate_scope, widen_scope, apply_invalidation,
    document_cache_budget,
    INV_ALL, INV_NODE, INV_INTERACTION, INV_MEDIA, INV_FONT,
)


TESTS = []


# ----------------------------------------------------------------------
# invalidate_scope — 改动 → 失效范围（C05 步骤 4）
# ----------------------------------------------------------------------
def test_scope_stylesheet_and_font_all():
    """stylesheet / 字体资源变了 → 全量失效。"""
    assert invalidate_scope("stylesheet_revision") == INV_ALL
    assert invalidate_scope("font_resource") == INV_ALL


def test_scope_node_changes():
    """DOM class/id/style 变化 → 仅节点失效（不重解析 stylesheet）。"""
    assert invalidate_scope("class") == INV_NODE
    assert invalidate_scope("id") == INV_NODE
    assert invalidate_scope("style") == INV_NODE
    assert invalidate_scope("dom") == INV_NODE


def test_scope_interaction_changes():
    """hover / focus 变化 → 仅交互失效。"""
    assert invalidate_scope("hover") == INV_INTERACTION
    assert invalidate_scope("focus") == INV_INTERACTION
    assert invalidate_scope("interaction") == INV_INTERACTION


def test_scope_media_changes():
    """媒体条件 / resize → 仅媒体失效。"""
    assert invalidate_scope("media") == INV_MEDIA
    assert invalidate_scope("resize") == INV_MEDIA
    assert invalidate_scope("media_query") == INV_MEDIA


def test_scope_unknown_conservative_all():
    """未知改动类型保守返回 all（正确性优先）。"""
    assert invalidate_scope("bogus") == INV_ALL
    assert invalidate_scope("") == INV_ALL
    assert invalidate_scope(None) == INV_ALL


def test_widen_scope_picks_largest():
    """两个失效范围取并集（影响面最大的那个）。"""
    assert widen_scope(INV_ALL, INV_NODE) == INV_ALL
    assert widen_scope(INV_NODE, INV_INTERACTION) == INV_INTERACTION
    assert widen_scope(INV_FONT, INV_MEDIA) == INV_FONT
    assert widen_scope(INV_MEDIA, INV_NODE) == INV_MEDIA


# ----------------------------------------------------------------------
# StyleCacheKey — 明确缓存键（C05 步骤 2）
# ----------------------------------------------------------------------
def test_key_hashable_and_unique():
    k1 = StyleCacheKey(stylesheet_revision=1,
                       media=frozenset({"min-width: 800px"}),
                       interaction=frozenset({"hover:btn1"}))
    k2 = StyleCacheKey(stylesheet_revision=1,
                       media=frozenset({"min-width: 800px"}),
                       interaction=frozenset({"focus:in2"}))
    assert k1 != k2
    # 可作 dict key（hashable）
    d = {k1: "A", k2: "B"}
    assert d[k1] == "A" and d[k2] == "B"
    # 相同键命中
    k1b = StyleCacheKey(stylesheet_revision=1,
                        media=frozenset({"min-width: 800px"}),
                        interaction=frozenset({"hover:btn1"}))
    assert k1b == k1
    assert d[k1b] == "A"


def test_key_as_tuple_sorted():
    k = StyleCacheKey(stylesheet_revision=3,
                      media=frozenset({"b", "a"}),
                      interaction=frozenset({"z", "y"}))
    t = k.as_tuple()
    # media / interaction 排序
    assert t[0] == 3
    assert t[1] == ("a", "b")
    assert t[2] == ("y", "z")


def test_key_default_empty_frozensets():
    k = StyleCacheKey(stylesheet_revision=1)
    assert k.media == frozenset()
    assert k.interaction == frozenset()


# ----------------------------------------------------------------------
# HitRateStats — 命中率统计（C05 步骤 1）
# ----------------------------------------------------------------------
def test_stats_record_and_rate():
    s = HitRateStats()
    s.record("match", hit=True)
    s.record("match", hit=True)
    s.record("match", hit=False)
    s.record("parse", hit=False)
    s.record("parse", hit=False)
    assert s.rate("match") == 2 / 3
    assert s.rate("parse") == 0.0
    assert s.phase_hits["match"] == 2
    assert s.phase_misses["match"] == 1
    assert s.phase_misses["parse"] == 2


def test_stats_no_sample_returns_one():
    """无样本阶段命中率返回 1.0（无重复工作 = 不需要缓存，不算白做）。"""
    s = HitRateStats()
    assert s.rate("cascade") == 1.0
    assert s.rate("property") == 1.0


def test_stats_summary_lists_phases():
    s = HitRateStats()
    s.record("parse", hit=False)
    s.record("match", hit=True)
    s.record("match", hit=True)
    text = s.summary()
    assert "parse" in text and "match" in text
    assert "hit=" in text and "miss=" in text and "rate=" in text


def test_stats_all_phases_sorted():
    s = HitRateStats()
    s.record("zzz", hit=True)
    s.record("aaa", hit=False)
    s.record("mmm", hit=True)
    assert s.all_phases() == ["aaa", "mmm", "zzz"]


def test_stats_reset():
    s = HitRateStats()
    s.record("match", hit=True)
    s.reset()
    assert s.all_phases() == []
    assert s.rate("match") == 1.0


# ----------------------------------------------------------------------
# apply_invalidation — 改动时键如何更新（C05 步骤 4）
# ----------------------------------------------------------------------
def test_apply_stylesheet_bumps_revision():
    cur = StyleCacheKey(stylesheet_revision=1,
                       media=frozenset({"m"}),
                       interaction=frozenset({"h"}))
    new = apply_invalidation(cur, "stylesheet_revision")
    assert new.stylesheet_revision == 2
    assert new.media == cur.media
    assert new.interaction == cur.interaction
    new2 = apply_invalidation(cur, "font_resource")
    assert new2.stylesheet_revision == 2


def test_apply_node_change_keeps_key():
    """class/id/style 变化：键不变（节点级失效在节点维度处理）。"""
    cur = StyleCacheKey(stylesheet_revision=5)
    new = apply_invalidation(cur, "class")
    assert new == cur          # 键不变
    new2 = apply_invalidation(cur, "id")
    assert new2 == cur


def test_apply_media_change_keeps_key():
    cur = StyleCacheKey(stylesheet_revision=5)
    new = apply_invalidation(cur, "media")
    assert new == cur          # 键不变（媒体条件由调用方按 scope 处理）


# ----------------------------------------------------------------------
# document_cache_budget — 按 Document 管理（C05 步骤 5）
# ----------------------------------------------------------------------
def test_budget_default_and_custom():
    b = document_cache_budget()
    assert b["max_entries"] == 4096
    assert b["clear_on"] == "document_destroy"
    b2 = document_cache_budget(1024)
    assert b2["max_entries"] == 1024


TESTS.append(("test_scope_stylesheet_and_font_all",
              test_scope_stylesheet_and_font_all))
TESTS.append(("test_scope_node_changes", test_scope_node_changes))
TESTS.append(("test_scope_interaction_changes",
              test_scope_interaction_changes))
TESTS.append(("test_scope_media_changes", test_scope_media_changes))
TESTS.append(("test_scope_unknown_conservative_all",
              test_scope_unknown_conservative_all))
TESTS.append(("test_widen_scope_picks_largest",
              test_widen_scope_picks_largest))
TESTS.append(("test_key_hashable_and_unique",
              test_key_hashable_and_unique))
TESTS.append(("test_key_as_tuple_sorted", test_key_as_tuple_sorted))
TESTS.append(("test_key_default_empty_frozensets",
              test_key_default_empty_frozensets))
TESTS.append(("test_stats_record_and_rate", test_stats_record_and_rate))
TESTS.append(("test_stats_no_sample_returns_one",
              test_stats_no_sample_returns_one))
TESTS.append(("test_stats_summary_lists_phases",
              test_stats_summary_lists_phases))
TESTS.append(("test_stats_all_phases_sorted",
              test_stats_all_phases_sorted))
TESTS.append(("test_stats_reset", test_stats_reset))
TESTS.append(("test_apply_stylesheet_bumps_revision",
              test_apply_stylesheet_bumps_revision))
TESTS.append(("test_apply_node_change_keeps_key",
              test_apply_node_change_keeps_key))
TESTS.append(("test_apply_media_change_keeps_key",
              test_apply_media_change_keeps_key))
TESTS.append(("test_budget_default_and_custom",
              test_budget_default_and_custom))

"""C08 — 脏区域分块与交互重绘（纯逻辑核心）回归（round5 docs 批次 13）。

覆盖 toy_browser/app/dirty_region.py：
- merge_bounds / expand_to_effect：合并旧+新 bounds、向效果边界扩展
- DamageEvent.to_region / DamageSet.generate：从事件生成 damage
- DamageRegion.tiles / DamageSet.tiled_regions / covered_tiles：固定
  tile 切分
- DamageSet.should_fallback / fallback_viewport：damage 过多 / 复杂变换
  / 不确定依赖 → 回退全视口（C08 步骤 5）
- InteractionCommitter：hover/focus 只在状态变化时提交（C08 步骤 2，
  无变化帧不重新绘制）
- scroll_damage：滚动只更新视口与新暴露区域（C08 步骤 2，单纯滚动
  不触发 build_box）
- TileCache：按文档 / 布局 / 绘制版本失效（C08 步骤 3）
"""
from __future__ import annotations

from toy_browser.app.dirty_region import (
    merge_bounds, expand_to_effect, DamageEvent, DamageSet,
    DamageRegion, InteractionCommitter, scroll_damage, TileCache,
)


TESTS = []


# ----------------------------------------------------------------------
# merge_bounds / expand_to_effect（C08 步骤 1）
# ----------------------------------------------------------------------
def test_merge_bounds_union():
    m = merge_bounds((0, 0, 10, 10), (5, 5, 20, 20))
    assert m == (0, 0, 20, 20)


def test_expand_shadow():
    e = expand_to_effect((0, 0, 10, 10), shadow_radius=5)
    # 阴影 expand=10 → [-10,-10,20,20]
    assert e == (-10, -10, 20, 20)


def test_expand_outline_and_transparency():
    e = expand_to_effect((0, 0, 10, 10), outline_width=2,
                         transparency_margin=3)
    # 描边外扩 2 → [-2,-2,12,12]；透明再外扩 3 → [-5,-5,15,15]
    assert e == (-5, -5, 15, 15)


# ----------------------------------------------------------------------
# DamageEvent.to_region（C08 步骤 1：旧+新合并 + 效果边界扩展）
# ----------------------------------------------------------------------
def test_event_to_region_merges_old_new():
    ev = DamageEvent(old_bounds=(0, 0, 10, 10),
                     new_bounds=(20, 20, 30, 30),
                     source="layout",
                     version=(1, 2, 3))
    r = ev.to_region()
    assert r.bounds == (0, 0, 30, 30)
    assert r.source == "layout"
    assert r.version == (1, 2, 3)


def test_event_to_region_only_new():
    ev = DamageEvent(new_bounds=(5, 5, 15, 15))
    r = ev.to_region()
    assert r.bounds == (5, 5, 15, 15)


def test_event_to_region_no_bounds():
    r = DamageEvent().to_region()
    assert r.bounds == (0.0, 0.0, 0.0, 0.0)


def test_event_to_region_expands_shadow():
    ev = DamageEvent(new_bounds=(0, 0, 10, 10), shadow_radius=5)
    r = ev.to_region()
    assert r.bounds == (-10, -10, 20, 20)


# ----------------------------------------------------------------------
# DamageRegion.tiles（C08 步骤 3 固定 tile 切分）
# ----------------------------------------------------------------------
def test_region_tiles_single():
    r = DamageRegion(bounds=(0, 0, 64, 64), tile_size=64) \
        if False else DamageRegion(bounds=(0, 0, 64, 64))
    tiles = r.tiles(64)
    assert (0, 0) in tiles


def test_region_tiles_multi():
    r = DamageRegion(bounds=(0, 0, 100, 100))
    tiles = set(r.tiles(64))
    # 覆盖 tile (0,0),(1,0),(0,1),(1,1)（100 > 64 跨两 tile）
    assert (0, 0) in tiles and (1, 0) in tiles
    assert (0, 1) in tiles and (1, 1) in tiles


# ----------------------------------------------------------------------
# DamageSet.generate / covered_tiles / should_fallback（C08 步骤 1/3/5）
# ----------------------------------------------------------------------
def test_set_generate_and_tiles():
    ds = DamageSet(tile_size=64)
    ds.generate([DamageEvent(new_bounds=(0, 0, 50, 50)),
                 DamageEvent(new_bounds=(70, 70, 120, 120))])
    assert len(ds.regions) == 2
    tiles = ds.covered_tiles()
    assert (0, 0) in tiles
    assert (1, 1) in tiles
    assert ds.tile_count() == len(tiles)


def test_set_fallback_when_too_many():
    ds = DamageSet(tile_size=64, fallback_threshold=4)
    # 10 个分散事件 → tile 数远超 4
    events = [DamageEvent(new_bounds=(i * 80, i * 80, i * 80 + 10,
                                       i * 80 + 10)) for i in range(10)]
    ds.generate(events)
    assert ds.should_fallback() is True
    # 回退全视口（非整页）
    vp = (0, 0, 800, 600)
    assert ds.fallback_viewport(vp) == vp


def test_set_no_fallback_when_few():
    ds = DamageSet(tile_size=64, fallback_threshold=10)
    ds.generate([DamageEvent(new_bounds=(0, 0, 10, 10))])
    assert ds.should_fallback() is False


def test_set_fallback_on_complex_transform():
    ds = DamageSet()
    ds.generate([DamageEvent(new_bounds=(0, 0, 10, 10))])
    assert ds.should_fallback() is False
    ds.mark_complex_transform()
    assert ds.should_fallback() is True


def test_set_fallback_on_uncertain_dep():
    ds = DamageSet()
    ds.generate([DamageEvent(new_bounds=(0, 0, 10, 10))])
    ds.mark_uncertain_dependency()
    assert ds.should_fallback() is True


# ----------------------------------------------------------------------
# InteractionCommitter（C08 步骤 2：状态变化才提交）
# ----------------------------------------------------------------------
def test_committer_hover_only_on_change():
    c = InteractionCommitter()
    assert c.submit_hover("a") is True     # 新状态 → 提交
    assert c.submit_hover("a") is False     # 相同输入 → 不提交
    assert c.submit_hover("b") is True     # 变化 → 提交
    assert c.submit_hover(None) is True     # 离开 → 变化


def test_committer_focus_only_on_change():
    c = InteractionCommitter()
    assert c.submit_focus("x") is True
    assert c.submit_focus("x") is False
    assert c.submit_focus(None) is True


def test_committer_state_and_reset():
    c = InteractionCommitter()
    c.submit_hover("h")
    c.submit_focus("f")
    assert c.state() == ("h", "f")
    c.reset()
    assert c.state() == (None, None)


# ----------------------------------------------------------------------
# scroll_damage（C08 步骤 2：只更新视口与新暴露区域）
# ----------------------------------------------------------------------
def test_scroll_damage_no_scrollable():
    """无 scrollable_bounds：只更新视口本身。"""
    d = scroll_damage((0, 0, 100, 100), dx=10, dy=5)
    assert d == (0, 0, 100, 100)


def test_scroll_damage_with_exposed_region():
    """有 scrollable_bounds：视口 + 新暴露区域并集。"""
    # 内容总 bounds [0,0,500,500]；视口 [0,0,100,100]；滚动 dx=50
    # → 新暴露区域 [50, 0, 150, 100]；并集 [0,0,150,100]
    d = scroll_damage((0, 0, 100, 100), dx=50, dy=0,
                      scrollable_bounds=(0, 0, 500, 500))
    assert d[0] == 0 and d[2] == 150


def test_scroll_damage_no_exposed_clamps():
    """滚动到边界无新暴露：只更新视口。"""
    # 内容 [0,0,100,100]，视口 [0,0,100,100]，滚 dx=0 dy=0
    d = scroll_damage((0, 0, 100, 100), dx=0, dy=0,
                      scrollable_bounds=(0, 0, 100, 100))
    assert d == (0, 0, 100, 100)


# ----------------------------------------------------------------------
# TileCache（C08 步骤 3：按版本失效）
# ----------------------------------------------------------------------
def test_tile_cache_hit_and_miss():
    tc = TileCache()
    tc.store(0, 0, (1, 1, 1), "bitmap0")
    assert tc.get(0, 0, (1, 1, 1)) == "bitmap0"
    # 版本变了 → miss（需重画）
    assert tc.get(0, 0, (1, 1, 2)) is None


def test_tile_cache_invalidate_stale():
    tc = TileCache()
    tc.store(0, 0, (1, 1, 1), "a")
    tc.store(1, 0, (1, 1, 1), "b")
    tc.store(0, 1, (1, 1, 2), "c")
    # 当前版本 (1,1,2)：前两个 tile stale
    n = tc.invalidate((1, 1, 2))
    assert n == 2
    assert tc.get(0, 0, (1, 1, 2)) is None
    assert tc.get(0, 1, (1, 1, 2)) == "c"
    assert len(tc) == 1


TESTS.append(("test_merge_bounds_union", test_merge_bounds_union))
TESTS.append(("test_expand_shadow", test_expand_shadow))
TESTS.append(("test_expand_outline_and_transparency",
              test_expand_outline_and_transparency))
TESTS.append(("test_event_to_region_merges_old_new",
              test_event_to_region_merges_old_new))
TESTS.append(("test_event_to_region_only_new",
              test_event_to_region_only_new))
TESTS.append(("test_event_to_region_no_bounds",
              test_event_to_region_no_bounds))
TESTS.append(("test_event_to_region_expands_shadow",
              test_event_to_region_expands_shadow))
TESTS.append(("test_region_tiles_single", test_region_tiles_single))
TESTS.append(("test_region_tiles_multi", test_region_tiles_multi))
TESTS.append(("test_set_generate_and_tiles",
              test_set_generate_and_tiles))
TESTS.append(("test_set_fallback_when_too_many",
              test_set_fallback_when_too_many))
TESTS.append(("test_set_no_fallback_when_few",
              test_set_no_fallback_when_few))
TESTS.append(("test_set_fallback_on_complex_transform",
              test_set_fallback_on_complex_transform))
TESTS.append(("test_set_fallback_on_uncertain_dep",
              test_set_fallback_on_uncertain_dep))
TESTS.append(("test_committer_hover_only_on_change",
              test_committer_hover_only_on_change))
TESTS.append(("test_committer_focus_only_on_change",
              test_committer_focus_only_on_change))
TESTS.append(("test_committer_state_and_reset",
              test_committer_state_and_reset))
TESTS.append(("test_scroll_damage_no_scrollable",
              test_scroll_damage_no_scrollable))
TESTS.append(("test_scroll_damage_with_exposed_region",
              test_scroll_damage_with_exposed_region))
TESTS.append(("test_scroll_damage_no_exposed_clamps",
              test_scroll_damage_no_exposed_clamps))
TESTS.append(("test_tile_cache_hit_and_miss",
              test_tile_cache_hit_and_miss))
TESTS.append(("test_tile_cache_invalidate_stale",
              test_tile_cache_invalidate_stale))

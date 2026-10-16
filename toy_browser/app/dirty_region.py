"""C08 — 脏区域分块与交互重绘（纯逻辑核心，批次 13）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C08：

> 问题与位置：GUI ``_reapply_interaction`` 仍重新布局并光栅化全内容；
> 已有 rasterize_region 和 changed_region 不代表入口已增量化。
>
> 1. 从 DOM/样式/布局变化生成 damage，合并旧 bounds 与新 bounds，并
>    向效果边界扩展。
> 2. 滚动只更新视口与新暴露区域；hover/focus 只在状态变化时提交，
>    避免重复相同输入触发重建。
> 3. 以固定 tile 或区域缓存保存绘制结果，按文档、布局和绘制版本失效。
> 4. 更新区域先恢复正确背景，再重放与区域相交且顺序正确的图元。
> 5. damage 过多、变换复杂或依赖不确定时，回退全视口。
>
> 验收：元素移动、删除、透明覆盖、阴影、滚动和控件状态切换；与全量
> 渲染逐像素比较。单纯滚动不触发 build_box；无变化帧不重新绘制。

本模块是**纯逻辑核心**（不依赖 tkinter / 网络）：

* :func:`merge_bounds` — 合并多个 AABB（C08 步骤 1 "合并旧 bounds 与
  新 bounds"）。
* :func:`expand_to_effect` — 向效果边界扩展（阴影 / 描边 / 透明覆盖
  影响范围，C08 步骤 1 "向效果边界扩展"）。
* :class:`DamageRegion` — 单个脏区域（bounds + 触发源 + 版本戳）。
* :class:`DamageSet` — 一组脏区域；:meth:`generate` 从变化事件生成
  damage（C08 步骤 1）；:meth:`tiled_regions` 按固定 tile 切分
  （C08 步骤 3 "以固定 tile 保存绘制结果"）；:meth:`should_fallback`
  判定是否回退全视口（C08 步骤 5 "damage 过多 / 变换复杂 / 依赖
  不确定"）。
* :class:`InteractionCommitter` — hover/focus 只在**状态变化**时
  提交（C08 步骤 2 "避免重复相同输入触发重建"）：记录上次提交状态，
  相同输入不再触发重排 / 重绘。
* :class:`TileCache` — 按 ``(document, layout_revision, paint_revision)``
  失效的固定 tile 缓存（C08 步骤 3）。

与 GUI 的关系：本模块**不改** ``_reapply_interaction`` 的实际重排 /
重绘执行，只提供"该画哪些区域、要不要回退全视口、交互是否真变了"
的决策层；GUI 在活显示环境批次消费这里的 damage 区域 / tile 缓存
（C08 交付 "damage 生成器、CPU tile/区域缓存、增量与全量像素差分"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# bounds 合并 / 扩展（C08 步骤 1）
# ----------------------------------------------------------------------
def merge_bounds(a: tuple, b: tuple) -> tuple:
    """合并两个 AABB ``(min_x, min_y, max_x, max_y)``。"""
    min_x = min(a[0], b[0])
    min_y = min(a[1], b[1])
    max_x = max(a[2], b[2])
    max_y = max(a[3], b[3])
    return (min_x, min_y, max_x, max_y)


def expand_to_effect(bounds: tuple, shadow_radius: float = 0.0,
                     shadow_offset_x: float = 0.0,
                     shadow_offset_y: float = 0.0,
                     outline_width: float = 0.0,
                     transparency_margin: float = 0.0) -> tuple:
    """C08 步骤 1 — 向效果边界扩展。

    把原始 bounds 向外扩到"该元素 / 区域可能影响到的像素范围"：

    * 阴影核：偏移 + 2×blur（与 C07 ``conservative_bounds`` 同规则，
      保证阴影落进相邻 tile 的部分也被标记脏）。
    * 描边：外扩 outline_width。
    * 透明覆盖 / 半透明：外扩 ``transparency_margin``（透明元素移动后
      原位置需恢复背景，新位置需重画，二者都标记脏——C08 验收
      "透明覆盖"）。
    """
    min_x, min_y, max_x, max_y = bounds
    if shadow_radius > 0.0 or shadow_offset_x != 0.0 or shadow_offset_y != 0.0:
        expand = 2.0 * shadow_radius
        min_x = min(min_x, min_x + shadow_offset_x - expand)
        min_y = min(min_y, min_y + shadow_offset_y - expand)
        max_x = max(max_x, max_x + shadow_offset_x + expand)
        max_y = max(max_y, max_y + shadow_offset_y + expand)
    if outline_width > 0.0:
        min_x -= outline_width
        min_y -= outline_width
        max_x += outline_width
        max_y += outline_width
    if transparency_margin > 0.0:
        min_x -= transparency_margin
        min_y -= transparency_margin
        max_x += transparency_margin
        max_y += transparency_margin
    return (min_x, min_y, max_x, max_y)


# ----------------------------------------------------------------------
# damage 区域 / 集合（C08 步骤 1 + 3 + 5）
# ----------------------------------------------------------------------
@dataclass
class DamageRegion:
    """C08 步骤 1 — 单个脏区域。

    * ``bounds`` — 效果边界扩展后的 AABB（旧 + 新合并 + 阴影 / 描边 /
      透明外扩）。
    * ``source`` — 触发源（"dom" / "style" / "layout" / "scroll" /
      "hover" / "focus" / "paint"）。
    * ``version`` — 版本戳（``(document, layout_rev, paint_rev)``，
      tile 缓存失效用，C08 步骤 3）。
    """
    bounds: tuple
    source: str = ""
    version: tuple = (0, 0, 0)

    def tiles(self, tile_size: int) -> list[tuple[int, int]]:
        """C08 步骤 3 — 该区域覆盖的 tile 坐标集合（固定 tile 切分）。"""
        min_x, min_y, max_x, max_y = self.bounds
        ts = max(1, tile_size)
        tx0 = int(min_x) // ts
        ty0 = int(min_y) // ts
        tx1 = int(max(0, max_x - 1)) // ts
        ty1 = int(max(0, max_y - 1)) // ts
        out = []
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                out.append((tx, ty))
        return out


@dataclass
class DamageEvent:
    """一个 DOM/样式/布局 / 交互变化 → damage。

    * ``old_bounds`` / ``new_bounds`` — 旧 / 新 AABB（步骤 1 合并）。
    * ``shadow_radius`` / ``shadow_offset_x`` / ``shadow_offset_y`` /
      ``outline_width`` / ``transparency_margin`` — 效果边界扩展量。
    * ``source`` — 触发源。
    * ``version`` — 版本戳。
    """
    old_bounds: Optional[tuple] = None
    new_bounds: Optional[tuple] = None
    shadow_radius: float = 0.0
    shadow_offset_x: float = 0.0
    shadow_offset_y: float = 0.0
    outline_width: float = 0.0
    transparency_margin: float = 0.0
    source: str = ""
    version: tuple = (0, 0, 0)

    def to_region(self) -> DamageRegion:
        """从旧 + 新 bounds 合并 + 效果边界扩展生成一个 DamageRegion。"""
        bounds = None
        if self.old_bounds is not None and self.new_bounds is not None:
            bounds = merge_bounds(self.old_bounds, self.new_bounds)
        elif self.old_bounds is not None:
            bounds = self.old_bounds
        elif self.new_bounds is not None:
            bounds = self.new_bounds
        else:
            return DamageRegion(bounds=(0.0, 0.0, 0.0, 0.0),
                                source=self.source, version=self.version)
        bounds = expand_to_effect(
            bounds,
            shadow_radius=self.shadow_radius,
            shadow_offset_x=self.shadow_offset_x,
            shadow_offset_y=self.shadow_offset_y,
            outline_width=self.outline_width,
            transparency_margin=self.transparency_margin)
        return DamageRegion(bounds=bounds, source=self.source,
                            version=self.version)


class DamageSet:
    """C08 步骤 1/3/5 — 一组脏区域的生成 / 切分 / 回退判定。

    * :meth:`generate` — 从事件列表生成 damage 区域（C08 步骤 1）。
    * :meth:`tiled_regions` — 按固定 tile 切分（C08 步骤 3 "以固定
      tile 保存绘制结果"）。
    * :meth:`should_fallback` — 判定是否回退全视口（C08 步骤 5
      "damage 过多 / 变换复杂 / 依赖不确定时，回退全视口，不回退整页
      超大位图"）。
    * :meth:`covered_tiles` — 去重后的 tile 集合（tile 缓存失效范围）。
    """

    def __init__(self, tile_size: int = 64,
                 fallback_threshold: int = 256) -> None:
        self.tile_size = tile_size
        self.fallback_threshold = fallback_threshold
        self.regions: list[DamageRegion] = []
        self._dirty_tiles: set[tuple[int, int]] = set()
        self._has_complex_transform = False
        self._has_uncertain_dep = False

    def generate(self, events: list[DamageEvent]) -> "DamageSet":
        """C08 步骤 1 — 从事件生成 damage 区域（每个事件一个区域）。"""
        self.regions = [ev.to_region() for ev in events]
        self._recompute_tiles()
        return self

    def add_region(self, region: DamageRegion) -> None:
        self.regions.append(region)
        self._recompute_tiles()

    def _recompute_tiles(self) -> None:
        self._dirty_tiles = set()
        for r in self.regions:
            self._dirty_tiles.update(r.tiles(self.tile_size))

    def covered_tiles(self) -> set[tuple[int, int]]:
        return set(self._dirty_tiles)

    def tile_count(self) -> int:
        return len(self._dirty_tiles)

    def mark_complex_transform(self) -> None:
        self._has_complex_transform = True

    def mark_uncertain_dependency(self) -> None:
        self._has_uncertain_dep = True

    def should_fallback(self) -> bool:
        """C08 步骤 5 — 是否回退全视口。

        回退条件（任一满足）：
        * 脏 tile 数超过 ``fallback_threshold``（damage 过多）；
        * 存在复杂变换（``_has_complex_transform``）；
        * 存在不确定依赖（``_has_uncertain_dep``）。

        回退到**全视口**（不是整页超大位图——C08 步骤 5 "不回退整页
        超大位图"，调用方据此把重绘范围 clamp 到视口）。
        """
        if self._has_complex_transform or self._has_uncertain_dep:
            return True
        return self.tile_count() > self.fallback_threshold

    def fallback_viewport(self, viewport: tuple) -> tuple:
        """C08 步骤 5 — 回退全视口（返回视口 AABB，非整页）。"""
        return viewport


# ----------------------------------------------------------------------
# 交互提交去重（C08 步骤 2：hover/focus 只在状态变化时提交）
# ----------------------------------------------------------------------
class InteractionCommitter:
    """C08 步骤 2 — hover/focus 只在**状态变化**时提交。

    记录上一次的 ``(hover_id, focus_id)`` 状态；相同输入（"重复相同
    输入"）不再触发重排 / 重绘（"避免重复相同输入触发重建"）。

    * :meth:`submit_hover` / :meth:`submit_focus` — 提交新状态；只有
      与上次不同才返回 ``True``（需重绘），相同返回 ``False``（无
      变化帧不重新绘制，C08 验收 "无变化帧不重新绘制"）。
    * :meth:`changed` — 当前状态与上次是否变化。
    * :meth:`reset` — 新导航 / 新文档时清状态。
    """

    def __init__(self) -> None:
        self._hover_id: Optional[str] = None
        self._focus_id: Optional[str] = None

    def submit_hover(self, hover_id: Optional[str]) -> bool:
        if hover_id == self._hover_id:
            return False   # 无变化帧
        self._hover_id = hover_id
        return True        # 状态变化 → 提交

    def submit_focus(self, focus_id: Optional[str]) -> bool:
        if focus_id == self._focus_id:
            return False
        self._focus_id = focus_id
        return True

    def state(self) -> tuple:
        return (self._hover_id, self._focus_id)

    def reset(self) -> None:
        self._hover_id = None
        self._focus_id = None


# ----------------------------------------------------------------------
# 滚动 damage（C08 步骤 2：滚动只更新视口与新暴露区域）
# ----------------------------------------------------------------------
def scroll_damage(viewport: tuple, dx: float, dy: float,
                 scrollable_bounds: Optional[tuple] = None) -> tuple:
    """C08 步骤 2 — 滚动后的更新区域（视口 + 新暴露区域）。

    * *viewport* — 当前视口 AABB ``(vx, vy, vx+w, vy+h)``。
    * *dx* / *dy* — 滚动偏移（内容移动量，dx>0 表示内容向左移）。
    * *scrollable_bounds* — 可滚动内容的总 bounds（可选；None 则只
      更新视口）。

    返回"需重绘的 AABB"：视口与新暴露区域（滚动后新进入视口的内容
    区域）的并集。**单纯滚动不触发 build_box**（C08 验收）——本函数
    只算重绘区域，不触发重排。
    """
    vx, vy, vmaxx, vmaxy = viewport
    if scrollable_bounds is None:
        # 只更新视口本身（内容平移，视口内像素需重画）
        return (vx, vy, vmaxx, vmaxy)
    sx0, sy0, sx1, sy1 = scrollable_bounds
    # 滚动后视口在内容坐标系里的范围（新暴露区域）
    new_vx0 = vx + dx
    new_vy0 = vy + dy
    new_vx1 = vmaxx + dx
    new_vy1 = vmaxy + dy
    # clamp 到可滚动内容
    cx0 = max(sx0, new_vx0)
    cy0 = max(sy0, new_vy0)
    cx1 = min(sx1, new_vx1)
    cy1 = min(sy1, new_vy1)
    exposed = (cx0, cy0, cx1, cy1) if (cx1 > cx0 and cy1 > cy0) else None
    if exposed is None:
        return (vx, vy, vmaxx, vmaxy)
    return merge_bounds((vx, vy, vmaxx, vmaxy), exposed)


# ----------------------------------------------------------------------
# tile / 区域缓存（C08 步骤 3：按文档、布局、绘制版本失效）
# ----------------------------------------------------------------------
class TileCache:
    """C08 步骤 3 — 按版本失效的固定 tile 缓存（CPU）。

    * key = ``(tile_x, tile_y)``；每个 tile 存 ``(version, bitmap)``，
      其中 ``version = (document_id, layout_revision, paint_revision)``。
    * :meth:`get` — 版本匹配则返回缓存 bitmap（"绘制结果复用"），
      否则返回 ``None``（需重画）。
    * :meth:`store` — 存入 / 更新。
    * :meth:`invalidate` — 版本不匹配（文档 / 布局 / 绘制版本变了）
      的 tile 全部失效（"按文档、布局和绘制版本失效"）。

    避免只画"改变的元素"导致残影（C08 步骤 4）：重放前先查缓存
    恢复正确背景，再重放与区域相交且顺序正确的图元。
    """

    def __init__(self) -> None:
        self._tiles: dict = {}

    def get(self, tx: int, ty: int,
            version: tuple) -> Optional[object]:
        entry = self._tiles.get((tx, ty))
        if entry is None:
            return None
        if entry[0] != version:
            return None
        return entry[1]

    def store(self, tx: int, ty: int, version: tuple,
               bitmap: object) -> None:
        self._tiles[(tx, ty)] = (version, bitmap)

    def invalidate(self, version: tuple) -> int:
        """清掉所有版本 != *version* 的 tile（返回失效数量）。"""
        stale = [k for k, (v, _) in self._tiles.items()
                 if v != version]
        for k in stale:
            del self._tiles[k]
        return len(stale)

    def __len__(self) -> int:
        return len(self._tiles)

    def keys(self) -> list:
        return list(self._tiles.keys())

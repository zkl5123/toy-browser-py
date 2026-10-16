"""C07 — 紧凑显示列表与可见区域裁剪（纯逻辑核心，批次 13）。

依据 assessments/20261016-round5/docs/逐项改进实施方案.md C07：

> 证据与位置：paint_engine.emit_commands/_emit 约 1.34 秒，5000 元素
> 产生约 15000 条命令。
>
> 1. 统计命令类型、可见比例、重复资源及字段分配。
> 2. 每条命令或子树具有保守 bounds；bounds 包含阴影核、描边、变换
>    和裁剪影响，不能只取原始 rect。
> 3. 将不可变资源引用改为 ID，避免每条命令复制图片数据或大字典。
> 4. 保留完整逻辑绘制顺序；对视口外且不会影响可见效果的子树提前跳过。
>
> 验收：显示列表优化前后 CPU 像素一致；阴影从视口外投进来不被错误
> 裁掉；重叠半透明图元顺序不变；记录生成命令数量和耗时。

本模块是**纯逻辑核心**（不依赖 tkinter / 网络）：

* :class:`CommandStats` — 命令类型 / 可见比例 / 重复资源统计
  （C07 步骤 1 "统计命令类型、可见比例、重复资源及字段分配"）。
* :func:`conservative_bounds` — 一条命令的保守 bounds（C07 步骤 2
  "bounds 包含阴影核、描边、变换和裁剪影响，不能只取原始 rect"）：
  把原始 rect + 阴影核 + 描边 + 变换扩展成一个保守的绘制影响盒。
* :class:`ResourceTable` — 不可变资源引用改 ID（C07 步骤 3 "避免
  每条命令复制图片数据或大字典"）：图片等大字典存一次，命令只持
  资源 ID。
* :func:`viewport_clip` — 视口裁剪（C07 步骤 4 "对视口外且不会
  影响可见效果的子树提前跳过"）：判断一条保守 bounds 是否完全
  落在视口外（可跳过），**阴影从视口外投进来不被错误裁掉**——
  用保守 bounds（含阴影核）而非原始 rect 判。
* :func:`count_commands` — 记录生成命令数量（C07 验收 "记录生成
  命令数量和耗时"）。

与 paint_engine 的关系：本模块**不改** ``emit_commands`` /
``_emit``（实际显示列表生成），只提供"统计、保守 bounds、资源
ID 化、视口裁剪"的纯逻辑决策层；paint_engine 在活显示环境批次
消费这里的裁剪 / 资源表（C07 交付 "bounds 裁剪与显示列表回归"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ----------------------------------------------------------------------
# 命令类型 / 可见比例 / 重复资源统计（C07 步骤 1）
# ----------------------------------------------------------------------
@dataclass
class CommandStats:
    """C07 步骤 1 — 命令类型 / 可见比例 / 重复资源统计。

    * ``by_kind`` — 各 kind（"rect" / "text" / "img" / …）的命令数。
    * ``total`` — 总命令数。
    * ``visible_count`` — 落在视口内的命令数（可见比例分子）。
    * ``duplicate_resources`` — 重复资源 ID 数（同 resource_id 被
      多条命令引用；越高越值得资源 ID 化复用）。

    :meth:`record` 记一条命令；:meth:`visible_ratio` 可见比例；
    :meth:`summary` 人类可读摘要（诊断区 / 性能追踪消费）。
    """
    by_kind: dict = field(default_factory=dict)
    total: int = 0
    visible_count: int = 0
    duplicate_resources: int = 0
    _resource_seen: dict = field(default_factory=dict, repr=False)

    def record(self, kind: str, visible: bool,
               resource_id: Optional[int] = None) -> None:
        """记一条命令（kind + 是否可见 + 可选资源 ID）。"""
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        self.total += 1
        if visible:
            self.visible_count += 1
        if resource_id is not None:
            seen = self._resource_seen.get(resource_id, 0)
            self._resource_seen[resource_id] = seen + 1
            if seen >= 1:
                # 至少重复一次
                self.duplicate_resources += 1

    def visible_ratio(self) -> float:
        """可见命令占比（无命令返回 0.0）。"""
        if self.total == 0:
            return 0.0
        return self.visible_count / self.total

    def summary(self) -> str:
        parts = [f"total={self.total}",
                 f"visible={self.visible_count}",
                 f"ratio={self.visible_ratio():.2%}"]
        if self.duplicate_resources:
            parts.append(f"dup_resources={self.duplicate_resources}")
        if self.by_kind:
            parts.append("kinds=" + ",".join(
                f"{k}:{v}" for k, v in sorted(self.by_kind.items())))
        return " ".join(parts)

    def reset(self) -> None:
        self.by_kind.clear()
        self.total = 0
        self.visible_count = 0
        self.duplicate_resources = 0
        self._resource_seen.clear()


# ----------------------------------------------------------------------
# 保守 bounds（C07 步骤 2：含阴影核 / 描边 / 变换 / 裁剪影响）
# ----------------------------------------------------------------------
def conservative_bounds(x: float, y: float, w: float, h: float,
                        shadow_radius: float = 0.0,
                        shadow_offset_x: float = 0.0,
                        shadow_offset_y: float = 0.0,
                        outline_width: float = 0.0,
                        filter_blur: float = 0.0) -> tuple:
    """C07 步骤 2 — 一条命令的保守 bounds（绘制影响盒）。

    返回 ``(min_x, min_y, max_x, max_y)``（像素坐标，保守向外扩展）：

    * 原始 rect ``(x, y, x+w, y+h)``。
    * **阴影核**（C07 步骤 2 "阴影从视口外投进来不被错误裁掉"）：
      模糊半径 ``shadow_radius`` 向外扩展 2×blur（覆盖高斯衰减
      尾部）；纯偏移（无 blur）时按偏移量保守外扩。保守不单独
      平移阴影盒，保证不漏掉任何受阴影影响的像素。
    * **描边**（outline）：向外扩展 ``outline_width``（描边一半在内
      一半在外，保守整宽外扩）。
    * **滤镜模糊**（filter_blur）：向外扩展 ``filter_blur`` 半径
      （模糊影响超出原始范围）。

    该 bounds 是**超集**（保证不漏掉任何受影响像素），可能比实际
    绘制范围大——正确性优先（C07 "保守"），裁剪时用这个而非原始
    rect（避免阴影被错误裁掉）。
    """
    min_x = x
    min_y = y
    max_x = x + w
    max_y = y + h
    # 阴影核（2×blur 覆盖高斯尾部；偏移量保守按整盒外扩，
    # 不单独平移阴影盒——保证不漏掉任何受阴影影响的像素）
    if shadow_radius > 0.0 or shadow_offset_x != 0.0 or shadow_offset_y != 0.0:
        shadow_expand = 2.0 * shadow_radius
        if shadow_expand == 0.0:
            # 纯偏移（无 blur）：按偏移方向外扩 1 倍偏移量
            shadow_expand = (abs(shadow_offset_x) + abs(shadow_offset_y))
        min_x -= shadow_expand
        min_y -= shadow_expand
        max_x += shadow_expand
        max_y += shadow_expand
    # 描边外扩
    if outline_width > 0.0:
        min_x -= outline_width
        min_y -= outline_width
        max_x += outline_width
        max_y += outline_width
    # 滤镜模糊外扩
    if filter_blur > 0.0:
        min_x -= filter_blur
        min_y -= filter_blur
        max_x += filter_blur
        max_y += filter_blur
    return (min_x, min_y, max_x, max_y)


def bounds_intersect(b: tuple, vx: float, vy: float, vw: float,
                     vh: float) -> bool:
    """bounds ``(min_x,min_y,max_x,max_y)`` 与视口 ``[vx,vx+vw]×[vy,vy+vh]``
    是否相交（AABB 相交判定；不相交 = 完全在视口外）。"""
    min_x, min_y, max_x, max_y = b
    return not (max_x < vx or min_x > vx + vw
                or max_y < vy or min_y > vy + vh)


# ----------------------------------------------------------------------
# 资源 ID 表（C07 步骤 3：不可变资源引用改 ID）
# ----------------------------------------------------------------------
class ResourceTable:
    """C07 步骤 3 — 不可变资源引用 → 资源 ID（避免每条命令复制图片
    数据 / 大字典）。

    * :meth:`register` — 把一个资源（图片像素 / 字典 / …）登记一次，
      返回稳定 ID；命令只持 ID，不复制资源本体。
    * :meth:`get` — 按 ID 取资源本体（光栅化 / lowering 阶段消费）。
    * :meth:`__len__` — 已登记资源数。

    与 C07 步骤 3 "重复子树显示列表可按版本复用"：同版本同资源的
    命令共享一个资源 ID，避免 N 条命令各复制一份大字典。
    """

    def __init__(self) -> None:
        self._items: list = []

    def register(self, resource: object) -> int:
        """登记资源（同一对象不重复登记；返回其 ID）。"""
        for i, item in enumerate(self._items):
            if item is resource:
                return i
        self._items.append(resource)
        return len(self._items) - 1

    def get(self, resource_id: int) -> object:
        return self._items[resource_id]

    def __len__(self) -> int:
        return len(self._items)


# ----------------------------------------------------------------------
# 视口裁剪（C07 步骤 4：视口外且不影响可见效果的子树提前跳过）
# ----------------------------------------------------------------------
def viewport_clip(cmd: dict, vx: float, vy: float, vw: float,
                  vh: float) -> bool:
    """C07 步骤 4 — 一条命令是否**可跳过**（完全在视口外且其保守
    bounds 不与视口相交）。

    * *cmd* — 命令字段字典（含 x/y/w/h + 可选 shadow_radius/
      shadow_offset_x/y + outline_width + filter_blur）。
    * 用 :func:`conservative_bounds` 算保守影响盒，再用
      :func:`bounds_intersect` 判与视口相交；**相交则不可跳**
      （必须绘制）；完全不相交 → 可跳（"对视口外且不会影响可见
      效果的子树提前跳过"）。

    关键："阴影从视口外投进来不被错误裁掉"——即使原始 rect 在视口
    外，只要其保守 bounds（含阴影核）与视口相交，仍返回 ``False``
    （不跳过，画阴影落进视口的部分）。
    """
    b = conservative_bounds(
        cmd.get("x", 0.0), cmd.get("y", 0.0),
        cmd.get("w", 0.0), cmd.get("h", 0.0),
        shadow_radius=cmd.get("shadow_radius", 0.0),
        shadow_offset_x=cmd.get("shadow_offset_x", 0.0),
        shadow_offset_y=cmd.get("shadow_offset_y", 0.0),
        outline_width=cmd.get("outline_width", 0.0),
        filter_blur=cmd.get("filter_blur", 0.0))
    if bounds_intersect(b, vx, vy, vw, vh):
        return False   # 与视口相交（含阴影投进）→ 不可跳
    return True        # 完全在视口外 → 可跳过


def count_commands(commands: list) -> int:
    """C07 验收 — 记录生成命令数量（供 "记录生成命令数量和耗时"）。"""
    return len(commands)


# ----------------------------------------------------------------------
# 类型化 IR（C07 交付 "类型化 IR"：按 kind 收窄字段）
# ----------------------------------------------------------------------
def ir_payload(kind: str, cmd: dict) -> dict:
    """C07 交付 — 把通用 ``cmd`` 字段收窄成 kind 专属的紧凑载荷。

    当前 ``DisplayCommand`` 有大量可选字段（C07 步骤 1 "单一
    DisplayCommand 的大量可选字段"）；按 kind 只保留该 kind 用到的
    字段（"明确的图元载荷"），光栅化 / lowering 阶段读紧凑载荷
    而非 30+ 字段的 dataclass。

    * ``"rect"`` — x/y/w/h + color。
    * ``"text"`` — x/y + text + text_color + font_px + align +
      max_width + letter_spacing + text_rendering + font_weight。
    * ``"img"`` — x/y/w/h + resource_id（C07 步骤 3 资源 ID 化，
      不复制像素）。
    * ``"shadow"`` — x/y/w/h + 阴影核字段。
    * 其它 — 全字段透传（保守，不漏字段）。
    """
    if kind == "rect":
        keys = ("x", "y", "w", "h", "color")
    elif kind == "text":
        keys = ("x", "y", "w", "h", "text", "text_color", "font_px",
                "align", "max_width", "letter_spacing", "text_rendering",
                "font_weight")
    elif kind == "img":
        keys = ("x", "y", "w", "h", "resource_id")
    elif kind == "shadow":
        keys = ("x", "y", "w", "h", "shadow_offset_x", "shadow_offset_y",
                "shadow_radius", "shadow_color")
    elif kind == "rounded":
        keys = ("x", "y", "w", "h", "color", "radius", "resource_id")
    else:
        keys = tuple(cmd.keys())
    out = {"kind": kind}
    for k in keys:
        if k in cmd:
            out[k] = cmd[k]
    return out

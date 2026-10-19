"""R7-C02 §5 — stacking context 坐标统一 + 绘制顺序同源。

Round-7 实施方案 R7-C02 第 5 点：

    "stacking context/clip/transform/scroll 坐标统一，
     绘制顺序与 hit-test 同源。"

本模块是**纯逻辑决策层**（无 OS / wgpu / Tkinter，P13 不崩；
真实 transform 矩阵 / GPU clip / 多核 worker 留活 GPU 批次）。

核心契约（CSS 2.1 §9.6.1 + 现行标准 stacking context 规则）：

* **stacking context 创建条件**（以下任一成立即创建新 SC）：
  - 根元素
  - position ≠ static/relative + z-index ≠ auto（absolute/fixed + 数字 z）
  - position: sticky
  - opacity < 1
  - transform ≠ none（translate/scale/rotate 等）
  - filter ≠ none（blur/brightness/contrast）
  - isolation: isolate
  - mix-blend-mode ≠ normal（本可测子集暂不含）

* **绘制顺序**（每个 SC 内部按以下 7 层，层间不可交错）：
  1. 负 z-index 子 SC（从最负到最接近 0）
  2. 块级祖先背景/边框
  3. 负 z-index 定位后代（按树序）
  4. 非定位块级后代
  5. 浮动元素（从左到右）
  6. 行内元素
  7. 正 z-index 子 SC（从最接近 0 到最大）

* **坐标逆变换**（R7-U01 zoom/DPR 约定的延伸）：
  hit-test 须用同一 transform/scroll 栈把视口坐标逆转到每个
  SC 的局部坐标。本可测子集：仅 translate（transform_tx/ty）
  + scroll 偏移（scroll_left/top）+ 无 zoom（zoom 由
  ``ViewportState.logical_to_css`` 单独处理，本模块不重复）。
  真实 transform 矩阵（scale/rotate/skew）留活 GPU 批次。

设计边界（"不冒充通过"）：

* 不含真实 transform 矩阵（仅 translate 平移）。
* 不含 mix-blend-mode / backdrop-filter。
* 不含 opacity 子合成（本可测子集仅记录 opacity，不做透明
  合成像素运算）。
* 不含 GPU clip 区域（仅记录 clip 矩形，光栅化裁剪由
  既有 rasterize 后处理做）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ----------------------------------------------------------------------
# stacking context 创建判定
# ----------------------------------------------------------------------

@dataclass
class StackingContext:
    """一个 stacking context 节点（对应一个 LayoutBox）。

    字段与 CSS 属性一一对应：
    - box: 对应的 LayoutBox
    - parent: 父 SC（根 SC 的 parent 为 None）
    - z: 本 SC 在父 SC 内的 z-index 序（负/零/正）
    - is_root: 是否为根 SC
    - opacity: 本 SC 的 opacity（<1 时创建新 SC）
    - transform_tx/ty: transform:translate 偏移（≠0 时创建新 SC）
    - filter_enabled: filter:blur/brightness/contrast（≠none 时创建新 SC）
    - position: 定位方式（absolute/fixed/sticky + z≠auto 时创建新 SC）
    """
    box: object = None            # LayoutBox 引用
    parent: "StackingContext | None" = None
    z: int = 0                    # 本 SC 在父 SC 内的 z 序
    is_root: bool = False
    opacity: float = 1.0
    transform_tx: float = 0.0
    transform_ty: float = 0.0
    filter_enabled: bool = False
    position: str = "static"      # static/relative/absolute/fixed/sticky
    scroll_left: float = 0.0
    scroll_top: float = 0.0
    # 子 SC（已按 z 序排序，负 < 0 < 正，同 z 按 DOM 序）
    children: list = field(default_factory=list)
    # 非定位浮动元素标记（本可测子集仅记录，不做浮动布局）
    is_float: bool = False
    # DOM 序索引（稳定排序用）
    dom_index: int = 0

    @property
    def has_transform(self) -> bool:
        return self.transform_tx != 0.0 or self.transform_ty != 0.0

    @property
    def creates_stacking_context(self) -> bool:
        """CSS 2.1 §9.6.1 — 本 box 是否创建新 stacking context。

        创建条件（任一成立）：
        - 根元素
        - position ∈ {absolute, fixed} 且 z-index ≠ auto（数字）
        - position: sticky
        - opacity < 1
        - transform ≠ none（本可测子集：tx/ty ≠ 0）
        - filter ≠ none（本可测子集：filter_enabled）
        - isolation: isolate（本可测子集：未接 CSS parse，默认 False）
        """
        if self.is_root:
            return True
        if self.position in ("absolute", "fixed") and self.z != 0:
            # z=0 仅当 z-index: auto（默认）时成立；数字 0 视为 0
            # （auto 与 0 等价，但此处无法区分，保守：z!=0 才创建）。
            return True
        if self.position == "sticky":
            return True
        if self.opacity < 1.0:
            return True
        if self.has_transform:
            return True
        if self.filter_enabled:
            return True
        return False


# ----------------------------------------------------------------------
# 从 LayoutBox 构造 StackingContext 树
# ----------------------------------------------------------------------

def _sc_from_box(bx, parent: StackingContext | None, dom_index: int = 0) -> StackingContext:
    """从 LayoutBox 构造 StackingContext（本可测子集）。

    position 字段从 LayoutBox 推导：
    - is_absolute → "absolute"
    - is_relative → "relative"
    - is_sticky → "sticky"
    - 否则 → "static"
    （is_fixed 在本可测子集中未单独标记，归入 absolute。）
    """
    if getattr(bx, "is_absolute", False):
        pos = "absolute"
    elif getattr(bx, "is_sticky", False):
        pos = "sticky"
    elif getattr(bx, "is_relative", False):
        pos = "relative"
    else:
        pos = "static"

    sc = StackingContext(
        box=bx,
        parent=parent,
        z=int(getattr(bx, "z_index", 0) or 0),
        is_root=(parent is None),
        opacity=float(getattr(bx, "opacity", 1.0) or 1.0),
        transform_tx=float(getattr(bx, "transform_tx", 0.0) or 0.0),
        transform_ty=float(getattr(bx, "transform_ty", 0.0) or 0.0),
        filter_enabled=bool(getattr(bx, "filter_enabled", False)),
        position=pos,
        scroll_left=float(getattr(bx, "scroll_left", 0.0) or 0.0),
        scroll_top=float(getattr(bx, "scroll_top", 0.0) or 0.0),
        is_float=bool(getattr(bx, "is_float", False)),
        dom_index=dom_index,
    )
    return sc


def build_stacking_context_tree(root_box) -> StackingContext:
    """从根 LayoutBox 构建完整的 StackingContext 树。

    仅对"创建新 SC"的 box 建节点；不创建新 SC 的 box 不单独
    建节点（其绘制层归属父 SC 的 4/5/6 层）。子 SC 按 z 序
    （负 → 0 → 正，同 z 按 DOM 序）排序。
    """
    root_sc = _sc_from_box(root_box, parent=None, dom_index=0)
    _attach_children(root_box, root_sc, dom_index=0)
    return root_sc


def _attach_children(bx, sc: StackingContext, dom_index: int = 0) -> None:
    """递归把 bx 的子 box 挂到 sc 的子 SC 列表（仅创建新 SC 的）。"""
    children = getattr(bx, "children", None) or []
    child_index = 0
    for child in children:
        child_sc = _sc_from_box(child, sc, dom_index=dom_index + 1 + child_index)
        child_index += 1
        if child_sc.creates_stacking_context:
            sc.children.append(child_sc)
            # 递归挂孙 SC
            _attach_children(child, child_sc, dom_index=dom_index + 1 + child_index)
        else:
            # 不创建新 SC 的 box：其子若创建 SC，仍归父 SC
            _attach_children(child, sc, dom_index=dom_index + 1 + child_index)
    # 按 z 序稳定排序：负 → 0 → 正，同 z 按 dom_index
    sc.children.sort(key=lambda s: (s.z, s.dom_index))


# ----------------------------------------------------------------------
# 绘制顺序（7 层）
# ----------------------------------------------------------------------

def paint_order(root_sc: StackingContext) -> list[tuple[int, int, StackingContext]]:
    """返回绘制顺序列表（layer, dom_index, sc）。

    每个元素是 (层号, dom_index, sc)，层号：
    1 = 负 z 子 SC, 4 = 块级后代（本可测子集不区分），
    7 = 正 z 子 SC, 0 = 自身（根 SC 背景/边框）。
    中间层（3/5/6）本可测子集与层 4 合并（不做浮动/行内拆分）。
    """
    order: list[tuple[int, int, StackingContext]] = []
    # 层 1：负 z 子 SC（从最负到最接近 0）
    neg_children = [c for c in root_sc.children if c.z < 0]
    for sc in neg_children:
        order.append((1, sc.dom_index, sc))
        # 递归（负 z 子 SC 内部同样 7 层）
        _recurse_paint(sc, order)
    # 层 0：自身背景/边框
    order.append((0, root_sc.dom_index, root_sc))
    # 层 4：非定位块级后代（本可测子集：z==0 且 non-SC 的归层 4）
    zero_children = [c for c in root_sc.children if c.z == 0]
    for sc in zero_children:
        order.append((4, sc.dom_index, sc))
        _recurse_paint(sc, order)
    # 层 7：正 z 子 SC（从最接近 0 到最大）
    pos_children = [c for c in root_sc.children if c.z > 0]
    for sc in pos_children:
        order.append((7, sc.dom_index, sc))
        _recurse_paint(sc, order)
    return order


def _recurse_paint(sc: StackingContext, order: list) -> None:
    """递归一个非根 SC 的内部 7 层。"""
    neg = [c for c in sc.children if c.z < 0]
    zero = [c for c in sc.children if c.z == 0]
    pos = [c for c in sc.children if c.z > 0]
    for c in neg:
        order.append((1, c.dom_index, c))
        _recurse_paint(c, order)
    order.append((4, sc.dom_index, sc))
    for c in zero:
        order.append((4, c.dom_index, c))
        _recurse_paint(c, order)
    for c in pos:
        order.append((7, c.dom_index, c))
        _recurse_paint(c, order)


# ----------------------------------------------------------------------
# 坐标逆变换（hit-test 用）
# ----------------------------------------------------------------------

@dataclass
class InverseTransform:
    """视口坐标 → 局部坐标的逆转换栈。

    栈从根 SC 到目标 SC，每层记录：
    - tx/ty: transform:translate 偏移（正向：子相对父的平移）
    - sx/sy: scroll 偏移（正向：内容相对容器的滚动量）

    逆转换：从目标 SC 回到视口，每层做：
    - 先加回 scroll 偏移（内容被滚动了，视口坐标要补偿）
    - 再减去 transform 偏移（transform 把内容平移了，逆转换要平移回去）
    """
    tx: float = 0.0
    ty: float = 0.0
    sx: float = 0.0
    sy: float = 0.0

    @property
    def is_identity(self) -> bool:
        return self.tx == 0.0 and self.ty == 0.0 and self.sx == 0.0 and self.sy == 0.0

    def to_local(self, vx: float, vy: float) -> tuple[float, float]:
        """视口坐标 → 局部坐标。

        正向（父→子）：child_x = parent_x + tx + sx
                         child_y = parent_y + ty + sy
        逆向（子→父，即 hit-test 用）：parent_x = child_x - tx - sx
                                        parent_y = child_y - ty - sy
        """
        lx = vx - self.tx - self.sx
        ly = vy - self.ty - self.sy
        return (lx, ly)

    def describe(self) -> str:
        if self.is_identity:
            return "identity"
        return (f"tx={self.tx:g} ty={self.ty:g} "
                f"sx={self.sx:g} sy={self.sy:g}")


def coordinate_inverse(root_sc: StackingContext,
                       target_sc: StackingContext | None = None) -> InverseTransform:
    """从 root_sc 到 target_sc 的坐标逆变换（仅 translate + scroll）。

    若 target_sc 为 None，返回 identity（root 自身即视口坐标）。
    累加路径上每层 SC 的 transform_tx/ty + scroll_left/top。
    """
    if target_sc is None:
        return InverseTransform()

    # 找路径：target 到 root 的父链
    path: list[StackingContext] = []
    cur: StackingContext | None = target_sc
    while cur is not None and cur is not root_sc:
        path.append(cur)
        cur = cur.parent
    if cur is None:
        # target_sc 不在 root_sc 子树内（不可能从 root 到达）——
        # 返回 identity（退化兜底，不崩 P13）。
        return InverseTransform()
    # path 现在是 [target, ..., child_of_root]（逆序）
    path.reverse()  # → [child_of_root, ..., target]
    tx = ty = sx = sy = 0.0
    for sc in path:
        tx += sc.transform_tx
        ty += sc.transform_ty
        sx += sc.scroll_left
        sy += sc.scroll_top
    return InverseTransform(tx=tx, ty=ty, sx=sx, sy=sy)


def hit_test_inverse(root_sc: StackingContext,
                     sc: StackingContext,
                     vx: float, vy: float) -> tuple[float, float]:
    """hit-test 专用：视口 (vx,vy) → sc 局部坐标。

    经 coordinate_inverse 累加 root→sc 路径上的 translate + scroll，
    再逆转换。无 transform/scroll 时退化为 identity（向后兼容
    既有 hit_test_at 的 y+yoffset 语义）。
    """
    inv = coordinate_inverse(root_sc, sc)
    return inv.to_local(vx, vy)


# ----------------------------------------------------------------------
# 与既有 paint_engine 的契约对齐
# ----------------------------------------------------------------------

def z_sort_key(bx) -> int:
    """向后兼容：从 LayoutBox 读 z_index（与既有 emit_commands 的
    ``cmd._layer = z`` 排序契约一致）。

    本可测子集：z_index 默认 0（auto），仅定位元素可用。
    """
    return int(getattr(bx, "z_index", 0) or 0)

"""批次 30 — C06 验收"全量 vs 增量布局差分"纯逻辑核心。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` C06
验收：

> 全量布局与增量布局对相同修改产生相同边框盒；前一段文本增高后
> 后续元素正确下移；窗口改宽、字体加载和 Flex 重分配不残留旧尺寸。

批次 12（:mod:`toy_browser.app.layout_incremental`，C06 step 3–4）
已落地**失效等级 + 最小可重排根 + 全容器 fallback 决策**（
:class:`InvalidateLevel` / :func:`classify_invalidate` /
:class:`MinimalRoot` / :func:`find_minimal_root`）；本批补 C06 验收
的**差分核对层**——把"全量布局与增量布局对相同修改产生相同边框盒"
这件事固化成可测试的纯逻辑：

* :func:`box_fingerprint` — 一个 :class:`toy_browser.layout.layout.
  LayoutBox` 的**边框盒指纹**（rect + padding + border + margin 的
  确定性序列化 SHA-1）。"边框盒"= 内容盒 + 内边距 + 边框 + 外边距
  （CSS box model 的外层边界），C06 验收比较的就是这层几何。
* :func:`collect_box_fingerprints` — 遍历布局树，收集每个节点
  （dom id 为键）的边框盒指纹，返回 ``{dom_id: fp}``。
* :func:`diff_fingerprints` — 两个指纹表（全量 vs 增量）逐项比对：
  返回 :class:`LayoutDiffReport`（一致 / 有差异 + 差异明细
  :class:`BoxDiff`）。
* :class:`LayoutDiffReport` / :class:`BoxDiff` — 差异报告与单节点
  差异明细（哪个 dom_id、全量 fp / 增量 fp、是否几何不一致）。

设计要点（与批次 12 `layout_incremental` **兼容不重叠**）：
- 批次 12 管"该重排哪个根、什么等级、要不要退全容器"（决策层）；
  本模块管"重排后全量 vs 增量**结果是否一致**"（核对层 / 回归
  断言层）。活显示批次 `layout_dirty_only` 跑完增量布局后，用
  :func:`collect_box_fingerprints` + :func:`diff_fingerprints` 核对
  增量结果与全量基线是否逐节点边框盒一致（C06 验收"相同边框盒"）。
- 指纹只取**几何量**（rect / padding / border / margin），不取
  样式 / 文本内容 / 脚本 Host（与 G09 step 5"仅保存可恢复状态"
  同语义）——内容相同但几何不同的两树会被判为差异（正确性优先）。
- 纯逻辑核心：不触碰 tkinter / 网络；仅 stdlib + 批次 12 /
  layout.py 现有数据结构。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional

# LayoutBox 惰性导入（活显示批次 / 测试从 repo root import 正常；
# 直接 ``py -3 toy_browser/app/layout_diff.py`` 无 toy_browser 包路径
# 时只在 ``__main__`` 烟测里补 sys.path，模块本体不强制顶层 import）。
LayoutBox = None  # type: ignore


# ----------------------------------------------------------------------
# 边框盒几何（C06 验收"相同边框盒"）
# ----------------------------------------------------------------------

def _edge(e) -> tuple:
    """EdgeSizes → (top, right, bottom, left) 浮点元组。"""
    if e is None:
        return (0.0, 0.0, 0.0, 0.0)
    return (float(getattr(e, "top", 0.0)),
            float(getattr(e, "right", 0.0)),
            float(getattr(e, "bottom", 0.0)),
            float(getattr(e, "left", 0.0)))


def box_geometry(bx: LayoutBox) -> dict:
    """一个 :class:`LayoutBox` 的**边框盒几何**（rect + 四边 + 外边距）。

    C06 验收比较的"边框盒"= 内容盒（rect）+ padding + border +
    margin（CSS box model 外层边界）。只取几何量，不取样式 / 文本 /
    脚本 Host。"""
    r = getattr(bx, "rect", None)
    return {
        "rect": (float(r.x) if r else 0.0,
                 float(r.y) if r else 0.0,
                 float(r.w) if r else 0.0,
                 float(r.h) if r else 0.0),
        "padding": _edge(getattr(bx, "padding", None)),
        "border": _edge(getattr(bx, "border", None)),
        "margin": _edge(getattr(bx, "margin", None)),
    }


def box_fingerprint(bx: LayoutBox) -> str:
    """一个 :class:`LayoutBox` 的边框盒几何**确定性指纹**（SHA-1）。

    同几何同指纹、异几何异指纹（"是否边框盒一致"判据）。指纹只含
    几何量（rect / padding / border / margin），不含样式 / 文本 /
    脚本 Host。"""
    geo = box_geometry(bx)
    raw = json.dumps(geo, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _dom_id_of(bx: LayoutBox) -> Optional[int]:
    """取布局节点的 dom id（用作指纹表键）。

    优先 ``dom.id``（Node 的 dom id）；无 dom 时退回 ``id(bx)``
    （仅测试 / 无 dom 场景；活显示批次 LayoutBox 都挂 dom）。"""
    dom = getattr(bx, "dom", None)
    if dom is not None:
        nid = getattr(dom, "id", None)
        if nid is not None:
            return int(nid)
        # dom 无 id 属性（如某些 Node 形态）→ 退 id(dom)
        return id(dom)
    return id(bx)


def collect_box_fingerprints(root: LayoutBox) -> dict:
    """遍历布局树，收集每个节点的边框盒指纹（``{dom_id: fp}``）。

    深度优先（DFS 序）；跳过 ``is_display_none`` 节点（不参与布局，
    无边框盒——C06 step 5 "display:none 不重排"同语义）。返回的键
    是 dom id（活显示批次每个 LayoutBox 都挂 dom）；无 dom 时退
    ``id(bx)``（测试场景）。"""
    out: dict = {}
    stack = [root]
    while stack:
        bx = stack.pop()
        if getattr(bx, "is_display_none", False):
            continue
        out[_dom_id_of(bx)] = box_fingerprint(bx)
        for child in reversed(getattr(bx, "children", []) or []):
            stack.append(child)
    return out


# ----------------------------------------------------------------------
# 差异比对（C06 验收"全量 vs 增量产生相同边框盒"）
# ----------------------------------------------------------------------

@dataclass
class BoxDiff:
    """C06 验收 — 单节点边框盒差异明细。

    * ``dom_id`` — 节点 dom id。
    * ``kind`` — 差异类别：``"geometry"``（两节点都在但指纹不同）/
      ``"missing_in_incremental"``（全量有、增量无——增量漏重排）/
      ``"extra_in_incremental"``（增量有、全量无——增量多重排）。
    * ``full_fp`` / ``incremental_fp`` — 全量 / 增量的边框盒指纹
      （缺失侧为 None）。
    """
    dom_id: int
    kind: str
    full_fp: Optional[str] = None
    incremental_fp: Optional[str] = None

    def summary(self) -> str:
        return (f"[{self.dom_id}] {self.kind} "
                f"full={self.full_fp} incr={self.incremental_fp}")


@dataclass
class LayoutDiffReport:
    """C06 验收 — 全量 vs 增量布局差分结果。

    * ``matches`` — 边框盒一致的节点数。
    * ``diffs`` — 差异明细（空 = 全量与增量"产生相同边框盒"，
      C06 验收通过）。
    * ``tolerance`` — 本批指纹是**精确**几何（无容差）；保留字段
      供后续 C03 字体边缘容差接入（几何 diff 留批次 27
      `geometry_freeze` 的 tolerance 语义）。
    * :meth:`passed` — 是否无差异（C06 验收"相同边框盒"判定）。
    """
    matches: int
    diffs: list = field(default_factory=list)
    tolerance: float = 0.0

    def passed(self) -> bool:
        """C06 验收"全量 vs 增量产生相同边框盒"——无差异即通过。"""
        return not self.diffs

    def summary(self) -> str:
        return (f"matches={self.matches} diffs={len(self.diffs)} "
                f"passed={self.passed()}")


def diff_fingerprints(full: dict, incremental: dict) -> LayoutDiffReport:
    """C06 验收 — 全量 vs 增量布局边框盒指纹逐项比对。

    * *full* / *incremental* — :func:`collect_box_fingerprints` 的
      结果（``{dom_id: fp}``，全量 / 增量各跑一次）。
    * 返回 :class:`LayoutDiffReport`：一致节点数 + 差异明细
      （几何不一致 / 增量漏重排 / 增量多重排）。

    判定（C06 验收"全量布局与增量布局对相同修改产生相同边框盒"）：
    - 两表 dom_id 交集里指纹相同 → ``matches`` +1；
    - 交集里指纹不同 → ``BoxDiff(kind="geometry")``；
    - 全量有、增量无 → ``BoxDiff(kind="missing_in_incremental")``
      （增量漏重排了该节点）；
    - 增量有、全量无 → ``BoxDiff(kind="extra_in_incremental")``
      （增量多重排 / 多收集了节点）。

    纯逻辑（不触碰 tkinter / 网络）；活显示批次 `layout_dirty_only`
    跑完增量布局后调用本函数核对，差分通过 = C06 验收"相同边框盒"。
    """
    full = dict(full or {})
    incremental = dict(incremental or {})
    diffs: list[BoxDiff] = []
    matches = 0

    full_keys = set(full)
    incr_keys = set(incremental)
    common = full_keys & incr_keys
    only_full = full_keys - incr_keys
    only_incr = incr_keys - full_keys

    for k in common:
        if full[k] == incremental[k]:
            matches += 1
        else:
            diffs.append(BoxDiff(dom_id=k, kind="geometry",
                                 full_fp=full[k],
                                 incremental_fp=incremental[k]))
    for k in only_full:
        diffs.append(BoxDiff(dom_id=k, kind="missing_in_incremental",
                             full_fp=full[k], incremental_fp=None))
    for k in only_incr:
        diffs.append(BoxDiff(dom_id=k, kind="extra_in_incremental",
                             full_fp=None, incremental_fp=incremental[k]))

    return LayoutDiffReport(matches=matches, diffs=diffs)


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.layout.layout import LayoutBox, Rect
    from toy_browser.app.layout_diff import (
        collect_box_fingerprints, diff_fingerprints, box_fingerprint,
        box_geometry)

    b1 = LayoutBox(box_type="block")
    b1.rect = Rect(0, 0, 100, 50)
    b1.is_display_none = False
    b2 = LayoutBox(box_type="text")
    b2.rect = Rect(0, 50, 100, 20)
    b1.children = [b2]
    b1.dom = None
    b2.dom = None
    full = collect_box_fingerprints(b1)
    incr = collect_box_fingerprints(b1)  # 同树 → 应一致
    rep = diff_fingerprints(full, incr)
    print("matches:", rep.matches, "diffs:", len(rep.diffs),
          "passed:", rep.passed())
    print("fps:", full)

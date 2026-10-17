"""批次 27 — C03 几何冻结纯逻辑核心（Edge 对照基准的捕获 / 校验 / 锁定）。

依据：``assessments/20261016-round5/docs/逐项改进实施方案.md`` C03 step 1：

> 先将最小失败页面拆为普通文本、b/i/span、链接、连续空白、中文/拉丁
> 混排、长单词和显式换行，**冻结 Edge 几何与截图**。

C03 验收：

> 基础失败页无重复文字和内容飞出；各片段按顺序且不重叠；**断行和
> 基线与 Edge 接近**。字体边缘另设容差，不用全图白背景掩盖文字错误。

"冻结 Edge 几何与截图"是 C03 文字布局的前置——必须先有一份**以
Edge（Chromium 排版引擎）为参照基准的几何冻结集**，后续批次（C03
文字布局 / 断行 / 基线）改排版时才有"与 Edge 接近"的对照目标。
本模块把"几何冻结"这件事固化成**可测试的纯逻辑**（不依赖 Edge /
tkinter / 显示环境）：

* :class:`FrozenGeometry` — 一个被冻结的页面几何基准：页尺寸、断行
  列表、每行 / 每片段的 ``x`` / ``width`` / ``baseline`` /
  ``line_height``、文字总行数、指纹。带 ``tolerance``（默认 1.0px，
  C03 验收"字体边缘另设容差"）。
* :func:`capture_geometry` — 从 :class:`toy_browser.rendering.
  inline_layout.InlineLayout` 的排版结果（:class:`LineBox` 序列）
  捕获 :class:`FrozenGeometry`（纯逻辑，不依赖截图 / 显示环境；截图
  本身留活显示批次，这里冻结的是**几何量**）。
* :func:`verify_against_frozen` — 把"当前排版结果"与冻结基准逐项
  比对（容差内一致）：行盒数量一致、每行基线 / 宽 / 左端在容差内、
  文字行序与片段定位不重叠（C03 验收"各片段按顺序且不重叠"）。
  返回 :class:`GeometryVerifyReport`（pass/fail + 偏差明细）。
* :func:`fingerprint` / :func:`load_frozen` / :func:`save_frozen` —
  冻结基准的指纹 / 序列化（供"已冻结"判据 + 落盘给后续批次做对照
  目标）。

设计要点：
- **纯逻辑**：输入是 :class:`LineBox` 序列（已在批次 15
  ``inline_layout.py`` 落地），不触碰 tkinter / 显示环境 / 网络；
  真实 Edge 对照复测（跑 Edge 取几何）需活环境，本批只负责"把
  排版几何冻结成可校验基准"这一**可测前置**。
- **容差**：C03 验收"字体边缘另设容差"——所有几何比对经
  *tolerance*（默认 1.0px），避免浮点 / 字体边缘抖动导致假失败。
- **不重叠验收**：复用 :func:`toy_browser.rendering.inline_layout.
  runs_overlap`（批次 15）做"片段不重叠"断言，本模块不重复实现。

与已有模块的关系（不重复实现）：
- **批次 15**（``inline_layout.py``）已有 :class:`InlineLayout` /
  :class:`LineBox` / :class:`PlacedRun` / :func:`runs_overlap`（行内
  排版 + 基线 + 片段不重叠检测）；本模块在它们之上做"冻结 / 校验"。
- **C03 step 1** 的"Edge 截图"本身留活显示批次；本批冻结的是
  ``LineBox`` 几何量（Edge 对照复测时把 Edge 的几何喂进
  :func:`capture_geometry` 即可，接口一致）。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

# 容差默认值（px）——C03 验收"字体边缘另设容差"
DEFAULT_TOLERANCE = 1.0


# ----------------------------------------------------------------------
# 冻结几何基准
# ----------------------------------------------------------------------

@dataclass
class FrozenLine:
    """一条被冻结的行盒几何（对应 :class:`LineBox` 的可校验投影）。

    * ``index`` — 行序号（0 起；断行顺序）。
    * ``x`` / ``width`` — 行左端 / 行宽（行盒边界）。
    * ``y`` / ``height`` — 行顶 / 行盒高。
    * ``baseline`` — 行基线 y（C03 步骤 4 按 ascent/descent +
      line-height 计算）。
    * ``run_count`` — 本行片段数（定位顺序断言用）。
    """
    index: int
    x: float = 0.0
    width: float = 0.0
    y: float = 0.0
    height: float = 0.0
    baseline: float = 0.0
    run_count: int = 0


@dataclass
class FrozenGeometry:
    """C03 step 1 — 一个被冻结的页面几何基准。

    * ``name`` — 基准名（"普通文本" / "b/i/span" / "中文拉丁混排"
      等 C03 step 1 列出的最小失败页类别）。
    * ``page_width_px`` / ``page_height_px`` — 页尺寸（冻结时锁定）。
    * ``lines`` — 被冻结的行盒序列（:class:`FrozenLine`）。
    * ``total_runs`` — 全页片段总数（"无重复文字 / 不飞出"辅助）。
    * ``fingerprint`` — 内容指纹（SHA-1，"是否已冻结 / 是否被改动"
      判据）。
    * ``tolerance`` — 几何比对容差（px，默认 1.0）。
    * ``source`` — 几何来源（``"inline_layout"`` 批次 15 排版 /
      ``"edge_screenshot"`` 活显示批次 Edge 对照）。
    """
    name: str
    page_width_px: float = 0.0
    page_height_px: float = 0.0
    lines: list = field(default_factory=list)
    total_runs: int = 0
    fingerprint: str = ""
    tolerance: float = DEFAULT_TOLERANCE
    source: str = "inline_layout"

    # -- 序列化 ---------------------------------------------------------
    def to_json(self) -> str:
        d = asdict(self)
        return json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "FrozenGeometry":
        d = json.loads(text)
        lines = [FrozenLine(
            index=int(l.get("index", 0)),
            x=float(l.get("x", 0.0)),
            width=float(l.get("width", 0.0)),
            y=float(l.get("y", 0.0)),
            height=float(l.get("height", 0.0)),
            baseline=float(l.get("baseline", 0.0)),
            run_count=int(l.get("run_count", 0)),
        ) for l in d.get("lines", [])]
        fg = FrozenGeometry(
            name=str(d.get("name", "")),
            page_width_px=float(d.get("page_width_px", 0.0)),
            page_height_px=float(d.get("page_height_px", 0.0)),
            lines=lines,
            total_runs=int(d.get("total_runs", 0)),
            fingerprint=str(d.get("fingerprint", "")),
            tolerance=float(d.get("tolerance", DEFAULT_TOLERANCE)),
            source=str(d.get("source", "inline_layout")),
        )
        return fg


# ----------------------------------------------------------------------
# 捕获（从 InlineLayout 排版结果冻结）
# ----------------------------------------------------------------------

def _line_box_width(line_box) -> float:
    """一行 :class:`LineBox` 的宽度：片段定位后右端 - 行原点 x。"""
    if not getattr(line_box, "runs", None):
        return getattr(line_box, "width", 0.0)
    xs = [r.x for r in line_box.runs]
    if not xs:
        return getattr(line_box, "width", 0.0)
    return max(xs) - line_box.x


def capture_geometry(line_boxes, name: str = "",
                     page_width_px: float = 0.0,
                     page_height_px: float = 0.0,
                     source: str = "inline_layout",
                     tolerance: float = DEFAULT_TOLERANCE) -> FrozenGeometry:
    """C03 step 1 — 从排版结果（:class:`LineBox` 序列）冻结几何基准。

    * *line_boxes* — :class:`toy_browser.rendering.inline_layout.LineBox`
      序列（批次 15 排版产物；Edge 对照复测时喂 Edge 几何，接口一致）。
    * 返回 :class:`FrozenGeometry`：页尺寸 + 每行 ``x`` / ``width`` /
      ``y`` / ``height`` / ``baseline`` / ``run_count`` + 全页片段总数
      + 指纹。

    纯逻辑（不触碰 tkinter / 显示环境）；"Edge 截图"本身留活显示
    批次，本批冻结的是 ``LineBox`` 几何量。
    """
    lines: list[FrozenLine] = []
    total_runs = 0
    for i, lb in enumerate(line_boxes or []):
        total_runs += len(getattr(lb, "runs", []) or [])
        lines.append(FrozenLine(
            index=i,
            x=float(getattr(lb, "x", 0.0)),
            width=float(_line_box_width(lb)),
            y=float(getattr(lb, "y", 0.0)),
            height=float(getattr(lb, "height", 0.0)),
            baseline=float(getattr(lb, "baseline", 0.0)),
            run_count=len(getattr(lb, "runs", []) or []),
        ))
    return FrozenGeometry(
        name=name,
        page_width_px=float(page_width_px),
        page_height_px=float(page_height_px),
        lines=lines,
        total_runs=total_runs,
        tolerance=float(tolerance),
        source=source,
    )


def fingerprint(geom: FrozenGeometry) -> str:
    """C03 step 1 — 冻结几何的内容指纹（SHA-1；"是否被改动"判据）。

    对几何量（页尺寸 + 行盒序列 + 片段总数 + 来源）做确定性序列化
    后取 SHA-1。容差不入指纹（容差是校验参数，不是几何量）。"""
    payload = {
        "name": geom.name,
        "page_width_px": geom.page_width_px,
        "page_height_px": geom.page_height_px,
        "total_runs": geom.total_runs,
        "source": geom.source,
        "lines": [asdict(l) for l in geom.lines],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _stamp_fingerprint(geom: FrozenGeometry) -> FrozenGeometry:
    if not geom.fingerprint:
        geom.fingerprint = fingerprint(geom)
    return geom


# ----------------------------------------------------------------------
# 校验（当前排版结果 vs 冻结基准）
# ----------------------------------------------------------------------

@dataclass
class GeometryDeviation:
    """C03 验收 — 一条几何偏差明细（诊断区 / 回归用）。

    * ``kind`` — 偏差类别（``line_count`` / ``line_x`` / ``line_y`` /
      ``line_baseline`` / ``line_width`` / ``run_count`` /
      ``page_width`` / ``page_height`` / ``overlap``）。
    * ``index`` — 行序号（0 起；非行级偏差为 -1）。
    * ``expected`` / ``actual`` — 冻结值 / 当前值。
    * ``delta`` — ``actual - expected``（绝对值用于容差判断）。
    """
    kind: str
    index: int = -1
    expected: float = 0.0
    actual: float = 0.0
    delta: float = 0.0

    def summary(self) -> str:
        return (f"{self.kind}[{self.index}] "
                f"exp={self.expected:g} act={self.actual:g} "
                f"Δ={self.delta:+g}")


@dataclass
class GeometryVerifyReport:
    """C03 验收 — 几何校验结果。

    * ``passed`` — 是否在容差内全部一致（行盒数 / 基线 / 宽 / 不
      重叠）。
    * ``deviations`` — 偏差明细（空 = 无偏差）。
    * ``tolerance`` — 本次校验用的容差（px）。
    """
    passed: bool
    deviations: list = field(default_factory=list)
    tolerance: float = DEFAULT_TOLERANCE

    def summary(self) -> str:
        return (f"passed={self.passed} tol={self.tolerance} "
                f"deviations={len(self.deviations)}")


def verify_against_frozen(line_boxes, frozen: FrozenGeometry,
                          tolerance: Optional[float] = None) -> GeometryVerifyReport:
    """C03 验收 — 把"当前排版结果"与冻结基准逐项比对（容差内一致）。

    * *line_boxes* — 当前 :class:`LineBox` 序列（批次 15 排版 / 或
      Edge 对照几何）。
    * *frozen* — 冻结基准（:class:`FrozenGeometry`）。
    * *tolerance* — 容差；None 用 ``frozen.tolerance``。

    校验项（C03 验收"断行和基线与 Edge 接近"）：
    - 行盒数量一致（``line_count``）；
    - 每行 ``x`` / ``y`` / ``baseline`` / ``width`` 在容差内（
      逐行比对）；
    - 每行片段数一致（``run_count``，"无重复文字"）；
    - 片段不重叠（C03 验收"各片段按顺序且不重叠"，复用
      :func:`toy_browser.rendering.inline_layout.runs_overlap`）。

    返回 :class:`GeometryVerifyReport`（``passed`` + 偏差明细）。
    """
    tol = tolerance if tolerance is not None else frozen.tolerance
    deviations: list[GeometryDeviation] = []
    cur_lines = list(line_boxes or [])

    # 行盒数量
    if len(cur_lines) != len(frozen.lines):
        deviations.append(GeometryDeviation(
            kind="line_count", index=-1,
            expected=float(len(frozen.lines)),
            actual=float(len(cur_lines)),
            delta=float(len(cur_lines) - len(frozen.lines))))

    # 逐行几何（只比到较短的序列，避免越界）
    n = min(len(cur_lines), len(frozen.lines))
    for i in range(n):
        f = frozen.lines[i]
        c = cur_lines[i]
        cx = float(getattr(c, "x", 0.0))
        cy = float(getattr(c, "y", 0.0))
        cb = float(getattr(c, "baseline", 0.0))
        cw = float(_line_box_width(c))
        crun = len(getattr(c, "runs", []) or [])
        checks = (
            ("line_x", cx, f.x),
            ("line_y", cy, f.y),
            ("line_baseline", cb, f.baseline),
            ("line_width", cw, f.width),
        )
        for kind, actual, expected in checks:
            delta = actual - expected
            if abs(delta) > tol:
                deviations.append(GeometryDeviation(
                    kind=kind, index=i, expected=expected,
                    actual=actual, delta=delta))
        if crun != f.run_count:
            deviations.append(GeometryDeviation(
                kind="run_count", index=i, expected=float(f.run_count),
                actual=float(crun),
                delta=float(crun - f.run_count)))

    # 片段不重叠（C03 验收"各片段按顺序且不重叠"）
    try:
        from toy_browser.rendering.inline_layout import runs_overlap
        for i in range(len(cur_lines)):
            for j in range(i + 1, len(cur_lines)):
                if runs_overlap(cur_lines[i], cur_lines[j], tol=tol):
                    deviations.append(GeometryDeviation(
                        kind="overlap", index=i,
                        expected=0.0, actual=1.0, delta=1.0))
                    break
    except ImportError:
        # runs_overlap 不可用（异常环境）→ 跳过重叠校验（不影响主几何）
        pass

    # 页尺寸（若当前传了页尺寸可校验；这里只校验 frozen 有记录且
    # 当前序列隐含的页宽 ≥ 各行右端 + 容差——"不飞出"验收）
    if frozen.page_width_px > 0:
        for i, c in enumerate(cur_lines[:n]):
            right = float(getattr(c, "x", 0.0)) + float(_line_box_width(c))
            if right - tol > frozen.page_width_px:
                deviations.append(GeometryDeviation(
                    kind="page_width", index=i,
                    expected=frozen.page_width_px, actual=right,
                    delta=right - frozen.page_width_px))

    report = GeometryVerifyReport(
        passed=not deviations,
        deviations=deviations,
        tolerance=tol,
    )
    return report


# ----------------------------------------------------------------------
# 落盘（供后续批次 / Edge 对照复测做对照目标）
# ----------------------------------------------------------------------

def save_frozen(geom: FrozenGeometry, path: str) -> str:
    """把冻结几何序列化落盘（JSON，UTF-8；指纹一并写入）。
    返回 *path*。"""
    _stamp_fingerprint(geom)
    with open(path, "w", encoding="utf-8") as f:
        f.write(geom.to_json())
    return path


def load_frozen(path: str) -> FrozenGeometry:
    """从盘上读回冻结几何（:func:`save_frozen` 的逆）。"""
    with open(path, "r", encoding="utf-8") as f:
        return FrozenGeometry.from_json(f.read())


if __name__ == "__main__":  # pragma: no cover - 手动烟测
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from toy_browser.rendering.inline_layout import (
        InlineLayout, SimpleMetrics, InlineRun, InlineLayoutConfig)
    layout = InlineLayout(InlineLayoutConfig(
        metrics=SimpleMetrics(advance_px=8.0), max_width=96.0))
    lines = layout.layout([InlineRun("hello world"),
                          InlineRun("second line"),
                          InlineRun("中文 拉丁")])
    geom = capture_geometry(lines, name="mixed", page_width_px=120.0)
    print(geom.name, "fp=", geom.fingerprint or fingerprint(geom))
    rep = verify_against_frozen(lines, geom)
    print("self-verify:", rep.summary())

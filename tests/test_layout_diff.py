"""批次 30 — C06 验收"全量 vs 增量布局差分"纯逻辑核心 回归。

被测：``toy_browser/app/layout_diff.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.layout.layout import LayoutBox, Rect, EdgeSizes
from toy_browser.app.layout_diff import (
    box_geometry, box_fingerprint,
    collect_box_fingerprints, diff_fingerprints,
    BoxDiff, LayoutDiffReport,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _box(rect=(0, 0, 100, 50), padding=None, border=None, margin=None,
         dom_id=None, children=None, display_none=False):
    bx = LayoutBox(box_type="block")
    bx.rect = Rect(*rect)
    if padding is not None:
        bx.padding = EdgeSizes(*padding)
    if border is not None:
        bx.border = EdgeSizes(*border)
    if margin is not None:
        bx.margin = EdgeSizes(*margin)
    bx.is_display_none = display_none
    if dom_id is not None:
        # 伪造一个带 dom.id 的对象
        class _D:
            def __init__(self, i):
                self.id = i
        bx.dom = _D(dom_id)
    if children:
        bx.children = children
    return bx


# ---------------------------------------------------------------------------
# box_geometry / box_fingerprint
# ---------------------------------------------------------------------------

@_reg
def test_box_geometry_fields():
    """box_geometry：rect + padding + border + margin（几何量）。"""
    bx = _box(rect=(1, 2, 100, 50), padding=(4, 4, 4, 4),
              border=(2, 2, 2, 2), margin=(8, 8, 8, 8))
    geo = box_geometry(bx)
    assert geo["rect"] == (1.0, 2.0, 100.0, 50.0)
    assert geo["padding"] == (4.0, 4.0, 4.0, 4.0)
    assert geo["border"] == (2.0, 2.0, 2.0, 2.0)
    assert geo["margin"] == (8.0, 8.0, 8.0, 8.0)


@_reg
def test_box_fingerprint_deterministic():
    """box_fingerprint：同几何同指纹、异几何异指纹。"""
    b1 = _box(rect=(0, 0, 100, 50))
    b2 = _box(rect=(0, 0, 100, 50))
    b3 = _box(rect=(0, 0, 100, 60))  # 高度不同
    assert box_fingerprint(b1) == box_fingerprint(b2)
    assert box_fingerprint(b1) != box_fingerprint(b3)
    assert len(box_fingerprint(b1)) == 40


@_reg
def test_box_fingerprint_differs_by_edges():
    """指纹含四边（padding/border/margin）：仅 padding 变 → 指纹变。"""
    a = _box(rect=(0, 0, 100, 50), padding=(0, 0, 0, 0))
    b = _box(rect=(0, 0, 100, 50), padding=(4, 4, 4, 4))
    assert box_fingerprint(a) != box_fingerprint(b)


# ---------------------------------------------------------------------------
# collect_box_fingerprints（布局树遍历）
# ---------------------------------------------------------------------------

@_reg
def test_collect_fingerprints_tree():
    """collect：遍历树（含子节点），按 dom_id 收集指纹。"""
    child = _box(rect=(0, 50, 100, 20), dom_id=2)
    root = _box(rect=(0, 0, 100, 80), dom_id=1, children=[child])
    fps = collect_box_fingerprints(root)
    assert set(fps) == {1, 2}
    assert fps[1] == box_fingerprint(root)
    assert fps[2] == box_fingerprint(child)


@_reg
def test_collect_skips_display_none():
    """display_none 节点不参与布局（无边框盒）→ 跳过不收集。"""
    child = _box(rect=(0, 50, 100, 20), dom_id=2, display_none=True)
    root = _box(rect=(0, 0, 100, 80), dom_id=1, children=[child])
    fps = collect_box_fingerprints(root)
    assert set(fps) == {1}  # 只有 root，child(display_none) 跳过


@_reg
def test_collect_no_dom_fallback_to_id():
    """无 dom 的节点 → 退回 id(bx) 作键（测试场景）。"""
    b = _box(rect=(0, 0, 10, 10))  # dom=None
    fps = collect_box_fingerprints(b)
    assert len(fps) == 1
    key = next(iter(fps))
    assert key == id(b)


# ---------------------------------------------------------------------------
# diff_fingerprints（全量 vs 增量"相同边框盒"核对）
# ---------------------------------------------------------------------------

@_reg
def test_diff_identical_trees_pass():
    """全量 == 增量（同树）→ 0 差异、passed（C06 验收"相同边框盒"）。"""
    child = _box(rect=(0, 50, 100, 20), dom_id=2)
    root = _box(rect=(0, 0, 100, 80), dom_id=1, children=[child])
    full = collect_box_fingerprints(root)
    incr = collect_box_fingerprints(root)
    rep = diff_fingerprints(full, incr)
    assert rep.passed() is True
    assert rep.diffs == []
    assert rep.matches == 2


@_reg
def test_diff_geometry_mismatch():
    """增量某节点几何变了（高度 80→90）→ geometry 差异。"""
    full_root = _box(rect=(0, 0, 100, 80), dom_id=1)
    incr_root = _box(rect=(0, 0, 100, 90), dom_id=1)  # 增量重排后高 90
    full = collect_box_fingerprints(full_root)
    incr = collect_box_fingerprints(incr_root)
    rep = diff_fingerprints(full, incr)
    assert rep.passed() is False
    assert any(d.kind == "geometry" for d in rep.diffs)
    assert rep.matches == 0


@_reg
def test_diff_missing_in_incremental():
    """全量有、增量无（增量漏重排该节点）→ missing_in_incremental。"""
    full_root = _box(rect=(0, 0, 100, 80), dom_id=1)
    # 增量树只有 dom_id=1（漏了子节点 2）
    incr_root = _box(rect=(0, 0, 100, 80), dom_id=1)
    full = collect_box_fingerprints(
        _box(rect=(0, 0, 100, 80), dom_id=1,
             children=[_box(rect=(0, 50, 100, 20), dom_id=2)]))
    incr = collect_box_fingerprints(incr_root)
    rep = diff_fingerprints(full, incr)
    assert any(d.kind == "missing_in_incremental" for d in rep.diffs)
    assert rep.passed() is False


@_reg
def test_diff_extra_in_incremental():
    """增量有、全量无（增量多重排 / 多收集）→ extra_in_incremental。"""
    full = collect_box_fingerprints(
        _box(rect=(0, 0, 100, 80), dom_id=1))
    incr = collect_box_fingerprints(
        _box(rect=(0, 0, 100, 80), dom_id=1,
             children=[_box(rect=(0, 50, 100, 20), dom_id=2)]))
    rep = diff_fingerprints(full, incr)
    assert any(d.kind == "extra_in_incremental" for d in rep.diffs)
    assert rep.passed() is False


@_reg
def test_diff_report_summary_and_matches():
    """LayoutDiffReport：matches 计数 + summary 串 + passed。"""
    rep = diff_fingerprints({1: "a", 2: "b"}, {1: "a", 2: "b"})
    assert rep.matches == 2
    assert rep.passed() is True
    s = rep.summary()
    assert "matches=2" in s and "diffs=0" in s and "passed=True" in s


@_reg
def test_box_diff_summary():
    """BoxDiff.summary：差异明细串。"""
    d = BoxDiff(dom_id=5, kind="geometry",
                full_fp="aa", incremental_fp="bb")
    s = d.summary()
    assert "[5]" in s and "geometry" in s
    assert "aa" in s and "bb" in s


@_reg
def test_diff_empty_inputs():
    """空输入（无节点）→ 0 matches、0 diffs、passed（vacuously）。"""
    rep = diff_fingerprints({}, {})
    assert rep.matches == 0
    assert rep.diffs == []
    assert rep.passed() is True


@_reg
def test_diff_tolerance_field():
    """LayoutDiffReport.tolerance 默认 0（本批精确几何，无容差）。"""
    rep = diff_fingerprints({1: "a"}, {1: "a"})
    assert rep.tolerance == 0.0


if __name__ == "__main__":
    passed = 0
    failed = []
    for name, fn in TESTS:
        try:
            fn(); passed += 1; print(f"ok  {name}")
        except Exception as e:
            failed.append((name, str(e))); print(f"FAIL {name}: {e}")
    print(f"\n{passed}/{len(TESTS)} passed")
    if failed:
        print("FAILED:", failed); sys.exit(1)

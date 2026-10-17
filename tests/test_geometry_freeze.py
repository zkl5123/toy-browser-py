"""批次 27 — C03 几何冻结纯逻辑核心 回归。

被测：``toy_browser/app/geometry_freeze.py``。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.app.geometry_freeze import (
    FrozenGeometry, FrozenLine, GeometryVerifyReport,
    capture_geometry, verify_against_frozen,
    fingerprint, save_frozen, load_frozen,
    DEFAULT_TOLERANCE,
)
from toy_browser.rendering.inline_layout import (
    InlineLayout, SimpleMetrics, InlineRun, InlineLayoutConfig,
)

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _lines(width=96.0):
    layout = InlineLayout(InlineLayoutConfig(
        metrics=SimpleMetrics(advance_px=8.0), max_width=width))
    return layout.layout([InlineRun("hello world"),
                         InlineRun("second line"),
                         InlineRun("中文 拉丁")])


# ---------------------------------------------------------------------------
# 捕获
# ---------------------------------------------------------------------------

@_reg
def test_capture_geometry_lines():
    """capture_geometry：从 LineBox 序列冻结行盒几何（x/width/y/height/
    baseline/run_count + 全页片段总数）。"""
    lines = _lines()
    geom = capture_geometry(lines, name="mixed", page_width_px=120.0)
    assert geom.name == "mixed"
    assert geom.page_width_px == 120.0
    assert len(geom.lines) == len(lines)
    assert geom.total_runs == sum(len(l.runs) for l in lines)
    for f, c in zip(geom.lines, lines):
        assert f.index is not None
        assert abs(f.x - c.x) < 1e-9
        assert f.baseline == c.baseline


@_reg
def test_capture_empty():
    """capture_geometry 空序列 → 0 行、total_runs=0。"""
    geom = capture_geometry([], name="empty")
    assert geom.lines == []
    assert geom.total_runs == 0


@_reg
def test_capture_fingerprint_set():
    """捕获的几何指纹为空（capture 不自动打指纹；fingerprint() 打）。"""
    geom = capture_geometry(_lines(), name="m")
    assert geom.fingerprint == ""
    fp = fingerprint(geom)
    assert len(fp) == 40  # SHA-1 hex


# ---------------------------------------------------------------------------
# 校验（当前 vs 冻结）
# ---------------------------------------------------------------------------

@_reg
def test_verify_self_passes():
    """自校验（同一排版结果冻结再校验）→ passed、无偏差。"""
    lines = _lines()
    geom = capture_geometry(lines, name="self", page_width_px=200.0)
    rep = verify_against_frozen(lines, geom)
    assert rep.passed is True
    assert rep.deviations == []
    assert rep.tolerance == DEFAULT_TOLERANCE


@_reg
def test_verify_line_count_deviation():
    """行盒数量不一致 → line_count 偏差、passed=False。"""
    lines = _lines()
    geom = capture_geometry(lines, name="n")
    rep = verify_against_frozen([], geom)  # 当前 0 行 vs 冻结 N 行
    assert rep.passed is False
    assert any(d.kind == "line_count" for d in rep.deviations)


@_reg
def test_verify_baseline_deviation():
    """基线偏移超容差 → line_baseline 偏差。"""
    lines = _lines()
    geom = capture_geometry(lines, name="b")
    # 构造"当前"行盒：复制 lines 但把 baseline 抬 5px（超 1.0 容差）
    class _FakeLB:
        def __init__(self, lb):
            self.x = lb.x; self.y = lb.y
            self.height = lb.height
            self.baseline = lb.baseline + 5.0
            self.runs = lb.runs
    fakes = [_FakeLB(l) for l in lines]
    rep = verify_against_frozen(fakes, geom)
    assert rep.passed is False
    assert any(d.kind == "line_baseline" for d in rep.deviations)


@_reg
def test_verify_within_tolerance_passes():
    """基线偏移在容差内（≤1px）→ 不记偏差、passed=True。"""
    lines = _lines()
    geom = capture_geometry(lines, name="t")
    class _FakeLB:
        def __init__(self, lb):
            self.x = lb.x + 0.5; self.y = lb.y
            self.height = lb.height
            self.baseline = lb.baseline + 0.5
            self.runs = lb.runs
    fakes = [_FakeLB(l) for l in lines]
    rep = verify_against_frozen(fakes, geom)
    assert rep.passed is True
    assert rep.deviations == []


@_reg
def test_verify_run_count_deviation():
    """片段数不一致 → run_count 偏差。"""
    lines = _lines()
    geom = capture_geometry(lines, name="rc")
    class _FakeLB:
        def __init__(self, lb):
            self.x = lb.x; self.y = lb.y; self.height = lb.height
            self.baseline = lb.baseline
            self.runs = lb.runs[:1]  # 砍掉多余片段
    fakes = [_FakeLB(l) for l in lines]
    rep = verify_against_frozen(fakes, geom)
    assert any(d.kind == "run_count" for d in rep.deviations)


@_reg
def test_verify_page_width_no_flyout():
    """片段飞出页宽（右端 > page_width + tol）→ page_width 偏差。"""
    lines = _lines()
    geom = capture_geometry(lines, name="w", page_width_px=20.0)
    # 当前行盒右端远超 20px（96px 宽排版）→ 记 page_width 偏差
    rep = verify_against_frozen(lines, geom)
    assert rep.passed is False
    assert any(d.kind == "page_width" for d in rep.deviations)


@_reg
def test_verify_tolerance_override():
    """verify 可覆盖容差（更大的 tol 让更多偏差通过）。"""
    lines = _lines()
    geom = capture_geometry(lines, name="to")
    class _FakeLB:
        def __init__(self, lb):
            self.x = lb.x + 3.0; self.y = lb.y
            self.height = lb.height
            self.baseline = lb.baseline
            self.runs = lb.runs
    fakes = [_FakeLB(l) for l in lines]
    # 默认 1.0 容差 → x 偏差 3px 记失败
    r1 = verify_against_frozen(fakes, geom)
    assert r1.passed is False
    # 覆盖容差 5.0 → x 偏差 3px 通过
    r2 = verify_against_frozen(fakes, geom, tolerance=5.0)
    assert r2.passed is True
    assert r2.tolerance == 5.0


# ---------------------------------------------------------------------------
# 指纹 / 序列化
# ---------------------------------------------------------------------------

@_reg
def test_fingerprint_stable():
    """fingerprint：确定性（同几何同指纹、异几何异指纹）。"""
    g1 = capture_geometry(_lines(), name="a")
    g2 = capture_geometry(_lines(), name="a")
    assert fingerprint(g1) == fingerprint(g2)
    g3 = capture_geometry(_lines(), name="b")
    assert fingerprint(g1) != fingerprint(g3)


@_reg
def test_to_from_json_roundtrip():
    """to_json / from_json 往返：字段保持（含行盒序列）。"""
    geom = capture_geometry(_lines(), name="rt", page_width_px=120.0)
    _stamp = save_frozen.__module__  # ensure module loaded
    text = geom.to_json()
    back = FrozenGeometry.from_json(text)
    assert back.name == "rt"
    assert back.page_width_px == 120.0
    assert len(back.lines) == len(geom.lines)
    assert back.lines[0].x == geom.lines[0].x
    assert back.lines[0].baseline == geom.lines[0].baseline
    assert back.tolerance == geom.tolerance
    assert back.total_runs == geom.total_runs


@_reg
def test_save_load_frozen(tmp_path=None):
    """save_frozen / load_frozen 往返 + 指纹写入。"""
    import tempfile
    geom = capture_geometry(_lines(), name="disk", page_width_px=100.0)
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        save_frozen(geom, path)
        back = load_frozen(path)
        assert back.name == "disk"
        assert back.fingerprint != ""
        assert back.fingerprint == fingerprint(geom)
        assert len(back.lines) == len(geom.lines)
    finally:
        os.remove(path)


@_reg
def test_frozen_line_dataclass():
    """FrozenLine / FrozenGeometry / GeometryVerifyReport 字段。"""
    fl = FrozenLine(index=0, x=1.0, width=2.0, y=3.0,
                    height=4.0, baseline=5.0, run_count=6)
    assert fl.index == 0 and fl.run_count == 6
    fg = FrozenGeometry(name="x", lines=[fl], total_runs=6)
    assert fg.lines[0].x == 1.0
    rep = GeometryVerifyReport(passed=True, tolerance=2.0)
    assert rep.passed and rep.tolerance == 2.0


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

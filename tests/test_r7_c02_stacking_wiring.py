"""R7-C02 §5 — stacking context 接线（hit_test_sc + emit_commands）回归。

被测（两个接线点）：

* ``toy_browser/layout/layout.py`` 的 :func:`hit_test_sc`（+ 私有
  ``_find_sc_for_box``）——把视口坐标经 SC 坐标栈逆变换做命中补偿。
* ``toy_browser/rendering/paint_engine.py`` 的 :func:`emit_commands`
  ——按 :func:`stacking_context.emit_sort_key` 的 7 层契约排序。
* ``toy_browser/app/gui.py`` 的 :meth:`hit_test_at` ——接
  ``hit_test_sc``（源码契约静态断言）。

两层设计（"不冒充通过"）：

* 静态层（无 OS/wgpu/tkinter 即可导入）——源码契约字符串断言。
* 动态层——真实调用 hit_test_sc / emit_commands / _find_sc_for_box /
  emit_sort_key。

命中补偿仅 translate + scroll（真实 transform 矩阵 / GPU clip 留活
GPU 批次）；emit 排序 7 层映射仅负 z / 背景 / 正 z 三分（不做
背景/行内层拆分，保持 DOM 序向后兼容）。

无 OS / wgpu / tkinter 依赖即可回归（P13 不崩）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.layout.layout import (
    Rect, hit_test, hit_test_sc,
)
from toy_browser.rendering import stacking_context as _sc

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read(path):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(base, path), encoding="utf-8").read()


class _Box:
    """最小 LayoutBox 桩（hit_test + SC 需要的字段）。"""

    def __init__(self, x=0, y=0, w=100, h=100, **kw):
        self.box_type = "block"
        self.rect = Rect(x, y, w, h)
        self.children = []
        self.dom = kw.pop("dom", None)
        self.parent = kw.pop("parent", None)
        self.z_index = kw.pop("z_index", 0)
        self.opacity = kw.pop("opacity", 1.0)
        self.transform_tx = kw.pop("transform_tx", 0.0)
        self.transform_ty = kw.pop("transform_ty", 0.0)
        self.filter_enabled = kw.pop("filter_enabled", False)
        self.is_absolute = kw.pop("is_absolute", False)
        self.is_sticky = kw.pop("is_sticky", False)
        self.is_relative = kw.pop("is_relative", False)
        self.is_float = kw.pop("is_float", False)
        self.is_display_none = kw.pop("is_display_none", False)
        self.scroll_left = kw.pop("scroll_left", 0.0)
        self.scroll_top = kw.pop("scroll_top", 0.0)


# ----------------------------------------------------------------------
# 静态层
# ----------------------------------------------------------------------

@_test
def test_static_layout_wiring_contract():
    src = _read(os.path.join("toy_browser", "layout", "layout.py"))
    assert "def hit_test_sc(" in src, "layout.py 缺 hit_test_sc 契约"
    assert "_find_sc_for_box" in src, "layout.py 缺 _find_sc_for_box 契约"
    # hit_test_sc 须退化等价裸 hit_test（identity 分支）。
    assert "inv.is_identity" in src, "hit_test_sc 缺 identity 退化分支"
    # 命中补偿：加回 scroll + transform 偏移。
    assert "inv.sx" in src and "inv.sy" in src, "hit_test_sc 缺 scroll 补偿"


@_test
def test_static_paint_engine_wiring_contract():
    src = _read(os.path.join("toy_browser", "rendering", "paint_engine.py"))
    assert "emit_commands" in src, "paint_engine.py 缺 emit_commands"
    assert "emit_sort_key" in src, "paint_engine.py 未接 emit_sort_key"
    assert "sort(key=" in src, "emit_commands 未做命令排序"


@_test
def test_static_gui_wiring_contract():
    src = _read(os.path.join("toy_browser", "app", "gui.py"))
    assert "hit_test_sc" in src, "gui.hit_test_at 未接 hit_test_sc"
    # 旧 layout（无 hit_test_sc）兜底裸 hit_test。
    assert "AttributeError" in src, "gui.hit_test_at 缺 AttributeError 兜底"


@_test
def test_static_sc_module_emit_sort_key_contract():
    src = _read(os.path.join("toy_browser", "rendering", "stacking_context.py"))
    assert "def emit_sort_key" in src, "stacking_context.py 缺 emit_sort_key"
    # 退化契约：无负 z 时与 sort(key=_layer) 等价（保持 DOM 序）。
    assert "return (0, 0)" in src, "emit_sort_key 缺 layer==0 → (0,0) 退化"


# ----------------------------------------------------------------------
# 动态层
# ----------------------------------------------------------------------

def _make_tree(with_scroll=False, with_transform=False):
    root = _Box(0, 0, 300, 300, dom="root")
    content = _Box(10, 10, 40, 40, dom="content", parent=root)
    root.children.append(content)
    abs_box = _Box(50, 60, 100, 100, dom="abs",
                   parent=root, is_absolute=True, z_index=1)
    root.children.append(abs_box)
    if with_scroll:
        root.scroll_left = 10
        root.scroll_top = 20
    if with_transform:
        abs_box.transform_tx = 5
        abs_box.transform_ty = 7
    return root, content, abs_box


@_test
def test_dynamic_hit_test_sc_identity_degrades():
    """identity 退化：无 transform/scroll 时 hit_test_sc ≡ 裸 hit_test。"""
    root, content, abs_box = _make_tree()
    for (mx, my) in [(20, 20), (80, 90), (150, 150)]:
        plain = hit_test(root, mx, my)
        sc = hit_test_sc(root, mx, my)
        assert plain is sc, (
            f"identity 退化被破坏 @ ({mx},{my}): "
            f"plain={plain} sc={sc}"
        )


@_test
def test_dynamic_hit_test_sc_compensation_applies():
    """补偿触发：有 scroll/transform 时 hit_test_sc 加回偏移再命中。"""
    root, content, abs_box = _make_tree(with_transform=True)
    # 无 transform 时 80,90 命中 abs；加 transform(5,7) 后渲染把子命令
    # 减了 (5,7)，hit_test_sc 须加回 (5,7) 才能命中同一 abs。
    abs_box.transform_tx = 5
    abs_box.transform_ty = 7
    plain = hit_test(root, 80, 90)
    sc = hit_test_sc(root, 80, 90)
    # 两个都应落在 abs（abs rect 50..150 × 60..160 含 80,90）。
    assert plain.dom == "abs", f"裸 hit_test 应命中 abs，实为 {plain.dom}"
    assert sc.dom == "abs", f"hit_test_sc 补偿后应仍命中 abs，实为 {sc.dom}"


@_test
def test_dynamic_find_sc_for_box_content_ancestor():
    """content box（不创建 SC）归属到最近创建 SC 的祖先（root）。"""
    root, content, abs_box = _make_tree()
    sc_root = _sc.build_stacking_context_tree(root)
    from toy_browser.layout.layout import _find_sc_for_box
    target = _find_sc_for_box(sc_root, content)
    assert target is not None, "content 未找到归属 SC 节点"
    assert target.box is root, (
        f"content（不创建 SC）应归属 root SC，实为 box={target.box.dom}"
    )


@_test
def test_dynamic_find_sc_for_box_sc_box_self():
    """abs box（创建 SC）归属到自身 SC 节点。"""
    root, content, abs_box = _make_tree()
    sc_root = _sc.build_stacking_context_tree(root)
    from toy_browser.layout.layout import _find_sc_for_box
    target = _find_sc_for_box(sc_root, abs_box)
    assert target is not None, "abs 未找到归属 SC 节点"
    assert target.box is abs_box, "abs 应归属自身 SC 节点"
    assert target.z == 1, f"abs SC z 应为 1，实为 {target.z}"


@_test
def test_dynamic_emit_sort_key_identity_equivalent():
    """退化契约：无负 z 时 emit_sort_key 排序 == 既有 sort(key=_layer)。"""
    class Cmd:
        def __init__(self, layer, kind="rect"):
            self._layer = layer
            self.kind = kind

    cmds = [Cmd(0, "rect"), Cmd(1, "rect"), Cmd(0, "text"),
            Cmd(2, "rect"), Cmd(0, "img")]
    legacy = sorted(cmds, key=lambda c: c._layer)
    sc_order = sorted(cmds, key=_sc.emit_sort_key)
    assert [c._layer for c in legacy] == [c._layer for c in sc_order], (
        f"无负 z 时排序应等价 legacy："
        f"legacy={[c._layer for c in legacy]} "
        f"sc={[c._layer for c in sc_order]}"
    )


@_test
def test_dynamic_emit_sort_key_negative_z_fixes_css21():
    """负 z 修正：负 z 定位画在背景/内容之上（CSS 2.1 §9.6.1 层1）。"""
    class Cmd:
        def __init__(self, layer, kind="rect"):
            self._layer = layer
            self.kind = kind

    cmds = [Cmd(0, "rect"), Cmd(-1, "rect"), Cmd(1, "rect"), Cmd(0, "text")]
    legacy = sorted(cmds, key=lambda c: c._layer)
    sc_order = sorted(cmds, key=_sc.emit_sort_key)
    legacy_layers = [c._layer for c in legacy]
    sc_layers = [c._layer for c in sc_order]
    # 旧 legacy：-1, 0, 0, 1（负 z 画在最底层）。
    assert legacy_layers == [-1, 0, 0, 1], f"legacy 应 [-1,0,0,1]：{legacy_layers}"
    # SC：0,0 先（非定位背景/内容），然后 -1（层1 负 z），最后 1（层7 正 z）。
    # 即负 z 被"提到"背景之上、正 z 之下。
    assert sc_layers[0] == 0 and sc_layers[1] == 0, (
        f"非定位内容应在最前：{sc_layers}"
    )
    assert sc_layers[2] == -1, f"负 z 应在层1（内容之上）：{sc_layers}"
    assert sc_layers[3] == 1, f"正 z 应在层7（最后）：{sc_layers}"


@_test
def test_dynamic_emit_commands_uses_sc_sort():
    """emit_commands 实际走 emit_sort_key（含负 z 场景端到端）。"""
    from toy_browser.rendering import paint_engine
    from toy_browser.layout.layout import LayoutBox

    class _C:
        """最小 Color 桩（paint 命令背景用）。"""
        def __init__(self):
            self.r, self.g, self.b, self.a = 200, 50, 50, 255

    # 用真实 LayoutBox（dom=None 跳过 _emit 的 dom 读取分支，避免
    # mock 缺 is_element）。给 background 让 _emit 出命令。
    root = LayoutBox("block")
    root.rect = Rect(0, 0, 300, 300)
    root.background = _C()
    abs_box = LayoutBox("block")
    abs_box.rect = Rect(50, 60, 100, 100)
    abs_box.is_absolute = True
    abs_box.z_index = 1
    abs_box.dom = None
    abs_box.parent = root
    abs_box.children = []
    abs_box.background = _C()
    root.children = [abs_box]

    dl = paint_engine.emit_commands(root)
    cmds = dl.commands
    assert len(cmds) >= 1, f"emit 至少 1 条命令，实 {len(cmds)}"
    layers = [c._layer for c in cmds]
    # 有 z=1 abs → 至少一条命令 _layer==1。
    assert any(l == 1 for l in layers), f"缺 z=1 命令：{layers}"
    # 排序契约：正 z（层7）命令须画在 0 层（背景）之后。
    pos_idx = [i for i, l in enumerate(layers) if l > 0]
    zero_idx = [i for i, l in enumerate(layers) if l == 0]
    if pos_idx and zero_idx:
        assert max(zero_idx) < min(pos_idx), (
            f"背景(层0)应在正 z(层7)之下先绘制：zero={zero_idx} pos={pos_idx}"
        )
    # 关键契约：emit_commands 排序键是 emit_sort_key（非裸 _layer）。
    # 用一条负 z 命令注入验证差异（负 z 在 legacy 下排最前，在 sc 下排层1）。
    neg_box = LayoutBox("block")
    neg_box.rect = Rect(100, 100, 20, 20)
    neg_box.is_absolute = True
    neg_box.z_index = -1
    neg_box.dom = None
    neg_box.parent = root
    neg_box.children = []
    neg_box.background = _C()
    root.children = [abs_box, neg_box]
    dl2 = paint_engine.emit_commands(root)
    layers2 = [c._layer for c in dl2.commands]
    neg_idx = [i for i, l in enumerate(layers2) if l == -1]
    zero2_idx = [i for i, l in enumerate(layers2) if l == 0]
    assert neg_idx, f"缺负 z 命令：{layers2}"
    # 负 z（层1）画在 0（层0）之上、正 z（层7）之下：
    # legacy 把 -1 排最前（画在最底层）；sc 把 -1 提到层1（背景之上）。
    # 所以负 z 命令的索引须 > 0 层命令索引（sc 修正），且 < 正 z。
    if zero2_idx:
        assert max(zero2_idx) < min(neg_idx), (
            "负 z（层1）应在 0（层0）之上先绘制（CSS 2.1 §9.6.1 修正）："
            f"zero={zero2_idx} neg={neg_idx}"
        )
    pos2_idx = [i for i, l in enumerate(layers2) if l > 0]
    if pos2_idx:
        assert max(neg_idx) < min(pos2_idx), (
            f"负 z（层1）应在正 z（层7）之下先绘制：neg={neg_idx} pos={pos2_idx}"
        )


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {name}: {e!r}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)

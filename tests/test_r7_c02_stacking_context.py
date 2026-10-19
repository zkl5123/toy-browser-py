"""R7-C02 §5 — stacking context 坐标统一 + 绘制顺序同源回归。

被测：``toy_browser/rendering/stacking_context.py``

两层设计（"不冒充通过"——区分静态契约与动态真实调用）：

* 静态层（无 OS/wgpu 可导入即全过）——源码契约字符串断言。
* 动态层——真实调用 build_stacking_context_tree / paint_order /
  coordinate_inverse / hit_test_inverse。

无 OS / wgpu 依赖即可回归（P13 不崩）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering import stacking_context as _sc

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read_module(name):
    import importlib
    mod = importlib.import_module(name)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(
        os.path.join(base, "toy_browser", "rendering",
                     os.path.basename(mod.__file__)),
        encoding="utf-8").read()


class _FB:
    """最小 LayoutBox 桩（仅 stacking context 需要的字段）。"""

    def __init__(self, **kw):
        self.children = []
        for k, v in kw.items():
            setattr(self, k, v)
        for d in ("is_float", "filter_enabled", "transform_tx",
                  "transform_ty", "scroll_left", "scroll_top", "z_index"):
            if d not in kw:
                setattr(self, d, 0)
        for d in ("is_absolute", "is_relative", "is_sticky"):
            if d not in kw:
                setattr(self, d, False)
        if "opacity" not in kw:
            self.opacity = 1.0


# ----------------------------------------------------------------------
# 静态层
# ----------------------------------------------------------------------

@_test
def test_static_module_contract():
    src = _read_module("toy_browser.rendering.stacking_context")
    for sym in ("StackingContext", "build_stacking_context_tree",
                "paint_order", "coordinate_inverse", "hit_test_inverse",
                "InverseTransform", "creates_stacking_context"):
        assert sym in src, f"stacking_context.py 缺 {sym} 契约"


# ----------------------------------------------------------------------
# 动态层
# ----------------------------------------------------------------------

@_test
def test_dynamic_abs_z_creates_sc():
    """absolute + z!=0 创建新 SC（CSS 2.1 §9.6.1）；
    absolute + z=0（auto）非根不创建。"""
    parent = _sc._sc_from_box(_FB(), None, 0)  # 根 parent
    sc_ = _sc._sc_from_box(_FB(is_absolute=True, z_index=2), parent, 1)
    assert sc_.creates_stacking_context is True
    sc0 = _sc._sc_from_box(_FB(is_absolute=True, z_index=0), parent, 2)
    assert sc0.creates_stacking_context is False


@_test
def test_dynamic_sticky_creates_sc():
    """sticky 总是创建新 SC（非根）。"""
    parent = _sc._sc_from_box(_FB(), None, 0)
    sc_ = _sc._sc_from_box(_FB(is_sticky=True, z_index=0), parent, 1)
    assert sc_.creates_stacking_context is True


@_test
def test_dynamic_opacity_creates_sc():
    """opacity<1 创建新 SC；opacity==1 不创建（非根）。"""
    parent = _sc._sc_from_box(_FB(), None, 0)
    sc_lt = _sc._sc_from_box(_FB(opacity=0.5), parent, 1)
    sc_eq = _sc._sc_from_box(_FB(opacity=1.0), parent, 2)
    assert sc_lt.creates_stacking_context is True
    assert sc_eq.creates_stacking_context is False


@_test
def test_dynamic_transform_creates_sc():
    """transform:translate 非零创建新 SC（非根）。"""
    parent = _sc._sc_from_box(_FB(), None, 0)
    sc_tf = _sc._sc_from_box(_FB(transform_tx=5, transform_ty=0), parent, 1)
    sc_none = _sc._sc_from_box(_FB(transform_tx=0, transform_ty=0), parent, 2)
    assert sc_tf.creates_stacking_context is True
    assert sc_none.creates_stacking_context is False


@_test
def test_dynamic_filter_creates_sc():
    """filter 非 none 创建新 SC（非根）。"""
    parent = _sc._sc_from_box(_FB(), None, 0)
    sc_f = _sc._sc_from_box(_FB(filter_enabled=True), parent, 1)
    sc_nf = _sc._sc_from_box(_FB(filter_enabled=False), parent, 2)
    assert sc_f.creates_stacking_context is True
    assert sc_nf.creates_stacking_context is False


@_test
def test_dynamic_root_always_creates():
    """根 SC 恒创建。"""
    sc_r = _sc._sc_from_box(_FB(), None, 0)
    assert sc_r.is_root is True
    assert sc_r.creates_stacking_context is True


@_test
def test_dynamic_build_tree_neg_zero_pos_order():
    """build_stacking_context_tree：负 z 子在前，正 z 子在后。

    注意：z=0（auto）的 absolute 不创建新 SC（本可测子集约定），
    故只有 z≠0 的定位子才出现在 SC 树中。"""
    root = _FB()
    b_neg = _FB(is_absolute=True, z_index=-2)
    b_pos = _FB(is_absolute=True, z_index=1)
    root.children = [b_pos, b_neg]  # DOM 序：正 z 在前，负 z 在后
    tree = _sc.build_stacking_context_tree(root)
    zs = [c.z for c in tree.children]
    # 排序契约：负 z 在前（更负先），正 z 在后（更大后）
    assert zs == [-2, 1], f"子 SC 须按 z 序（负→正）, got {zs}"


@_test
def test_dynamic_paint_order_layers():
    """paint_order 7 层：负 z → 自身(层0) → z=0 → 正 z。"""
    root = _FB()
    b_neg = _FB(is_absolute=True, z_index=-1)
    b_pos = _FB(is_absolute=True, z_index=1)
    root.children = [b_neg, b_pos]
    tree = _sc.build_stacking_context_tree(root)
    order = _sc.paint_order(tree)
    layers = [l for l, _, _ in order]
    # 负 z 子先（层1），自身（层0），正 z 子后（层7）
    # 层序必须单调不减（按层号去重看序列）
    assert 1 in layers and 0 in layers and 7 in layers, f"层号缺失 {layers}"
    # 负 z 子在自身之前，正 z 子在自身之后
    idx_neg = layers.index(1)
    idx_self = layers.index(0)
    idx_pos = layers.index(7)
    assert idx_neg < idx_self < idx_pos, f"层序违反, got {layers}"


@_test
def test_dynamic_coordinate_inverse_nested():
    """嵌套 scroll + transform：逆转换累加路径偏移。"""
    root = _FB()
    scroll = _FB(is_absolute=True, scroll_left=10, scroll_top=20, z_index=1)
    tf = _FB(is_absolute=True, transform_tx=5, transform_ty=7, z_index=1)
    scroll.children = [tf]
    root.children = [scroll]
    tree = _sc.build_stacking_context_tree(root)
    scroll_sc = next(c for c in tree.children if c.box is scroll)
    tf_sc = next(c for c in scroll_sc.children if c.box is tf)
    inv = _sc.coordinate_inverse(tree, tf_sc)
    assert inv.tx == 5 and inv.ty == 7, f"transform 累加错 {inv}"
    assert inv.sx == 10 and inv.sy == 20, f"scroll 累加错 {inv}"
    lx, ly = _sc.hit_test_inverse(tree, tf_sc, 100, 200)
    # local = viewport - (tx+sx, ty+sy) = (100-15, 200-27)
    assert (lx, ly) == (85.0, 173.0), f"逆转换错 ({lx},{ly})"


@_test
def test_dynamic_identity_inverse():
    """root 自身 / None / 无偏移 SC 的逆转换退化为 identity。"""
    root = _FB()
    inv_none = _sc.coordinate_inverse(root, None)
    assert inv_none.is_identity is True
    inv_self = _sc.coordinate_inverse(root, root)
    assert inv_self.is_identity is True
    static_child = _FB()
    root.children = [static_child]
    tree = _sc.build_stacking_context_tree(root)
    # 无 transform/scroll 的 SC 路径偏移恒 0
    inv_st = _sc.coordinate_inverse(tree, tree)
    assert inv_st.is_identity is True


@_test
def test_dynamic_z_sort_key_compat():
    """z_sort_key 与既有 paint_engine 的 cmd._layer 排序契约一致。"""
    assert _sc.z_sort_key(_FB(z_index=3)) == 3
    assert _sc.z_sort_key(_FB(z_index=0)) == 0
    # 无 z_index 属性的对象退化 0（向后兼容）
    class _NoZ:
        pass
    assert _sc.z_sort_key(_NoZ()) == 0


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")

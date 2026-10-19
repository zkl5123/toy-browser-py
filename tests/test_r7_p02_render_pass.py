"""R7-P02 第 4 点 — 真实 render pass（rect/image/glyph 图元）+
CPU 独立 oracle 对照。

Round-7 评估（``渲染与标准差距审计.md`` §5 / ``逐项改进实施方案.md``
R7-P02 第 4、5 点）核心验收：

* 实现矩形 / 图像 / 字形的**真实 render pass**——逐图元命令光栅化
  进独立帧缓冲，**不允许 GPU render 先把整帧在 CPU 画完**（§253
  "CPU 是独立 oracle，而不是 GPU 主链的整帧前处理器"）；
* CPU 作为**独立 oracle**：对照基准帧经权威
  ``paint_engine.rasterize_to_canvas`` 独立重算，**不经** pass；
* 逐像素对照 0 mismatch 即 pass 图元通路正确（P02 验收"GPU 实际
  绘制单独通路像素对照通过"的 CPU 侧等价形态）；
* frame trace 证明常规帧不经整帧 CPU raster（``frame_pass`` 标记）。

本模块两层设计（"不冒充通过"——区分静态契约与动态真实调用）：

* **静态层**（无 wgpu / 无字体可导入即全过）——源码契约字符串
  断言：:class:`SoftwareRenderPass` 确实按 rect / img / text 三类
  图元命令光栅化、帧底白（与 oracle 一致）、oracle 走
  ``rasterize_to_canvas`` 独立通路、``render_with_pass`` 不经
  整帧前处理、``verify_frame_vs_oracle`` 拒绝非 pass 帧。
* **动态层**（真实调用）——手工构造含 rect / img / text 的
  :class:`DisplayList`，跑 :meth:`SoftwareRenderPass.render` 与
  :func:`cpu_oracle_frame` 逐像素对照（0 mismatch），并在
  :class:`GPUBackend` 上验证 :meth:`render_with_pass` 的
  ``frame_pass`` 标记 / 图元计数 / oracle 对照 / 非 pass 帧拒绝。

  动态层在无字体环境下 text 图元字形 mask 缺失 → pass 与 oracle
  对该字形皆退化为不画（对照仍一致）；rect / img 图元恒可对照。
  真实字体 / 真实 wgpu shader 的"实际 GPU 光栅化 + 上传字节 + 帧
  时间"留待活 GPU 批次（P13 不崩契约）。

无 OS / wgpu 依赖即可回归（text 字形在受管无字体环境自动降级）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering import paint_engine as _pe
from toy_browser.rendering.paint_engine import DisplayCommand
from toy_browser.rendering import gpu as _gpu
from toy_browser.rendering import render_pass as _rp

TESTS = []


def _test(f):
    TESTS.append((f.__name__, f))
    return f


def _read_module(name):
    import importlib
    mod = importlib.import_module(name)
    return open(
        os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "toy_browser", "rendering",
            os.path.basename(mod.__file__)),
        encoding="utf-8").read()


# ----------------------------------------------------------------------
# 静态层——源码契约断言（不实际执行渲染）
# ----------------------------------------------------------------------

@_test
def test_static_pass_consumes_three_primitive_kinds():
    """pass 源码消费 rect / img / text 三类图元命令。"""
    src = _read_module("toy_browser.rendering.render_pass")
    for kind in ("rect", "img", "text"):
        assert f'kind == "{kind}"' in src, (
            f"render_pass must consume the '{kind}' primitive, "
            f"missing dispatch branch")
    # 三类图元分别委托已验收的图元原语（不自行整帧光栅化）。
    assert "fill_rect_rgb8" in src, "rect 应经 fill_rect_rgb8 图元原语"
    assert "paint_image_nearest" in src, "img 应经 paint_image_nearest"
    assert "draw_text" in src, "text 应经权威 draw_text 图元原语"


@_test
def test_static_pass_not_whole_frame_preprocessor():
    """pass 不调用整帧 rasterize_to_canvas（§253 核心不变量）。"""
    src = _read_module("toy_browser.rendering.render_pass")
    # SoftwareRenderPass.render 主体不得出现整帧光栅化入口。
    assert "rasterize_to_canvas" not in src.split(
        "class SoftwareRenderPass")[1].split(
        "def cpu_oracle_frame")[0], (
        "SoftwareRenderPass 自身不得调用 rasterize_to_canvas 整帧光栅化")
    # 整帧光栅化只允许出现在 oracle（独立通路）。
    oracle_part = src[src.index("def cpu_oracle_frame"):]
    assert "rasterize_to_canvas" in oracle_part, (
        "独立 oracle 必须经权威 rasterize_to_canvas 重算基准帧")


@_test
def test_static_oracle_is_independent_of_pass():
    """oracle 通路独立：不 import / 不经 SoftwareRenderPass。"""
    src = _read_module("toy_browser.rendering.render_pass")
    oracle_part = src[src.index("def cpu_oracle_frame"):]
    oracle_part = oracle_part.split("def compare_frames")[0]
    assert "SoftwareRenderPass" not in oracle_part, (
        "oracle 不得依赖 pass（必须独立通路）")
    assert "paint_engine" in oracle_part or "_pe" in oracle_part, (
        "oracle 必须经 paint_engine 权威光栅化器")


@_test
def test_static_pass_white_base_matches_oracle():
    """pass 帧底白 (255,255,255)，与 oracle 权威画布底色一致。"""
    src = _read_module("toy_browser.rendering.render_pass")
    assert "[255] * n" in src or "bytearray([255]" in src, (
        "pass 帧缓冲必须默认白底（与 rasterize_to_canvas 画布一致）")


@_test
def test_static_verify_rejects_non_pass_frame():
    """verify_frame_vs_oracle 拒绝非 pass 帧（不冒充通过）。"""
    src = _read_module("toy_browser.rendering.gpu")
    assert "not a pass frame" in src, (
        "verify_frame_vs_oracle 必须拒绝非 render_with_pass 产出的帧")
    assert "frame_pass" in src, "帧须带 frame_pass 标记"


# ----------------------------------------------------------------------
# 动态层——真实调用（rect / img 恒可对照；text 依字体环境降级）
# ----------------------------------------------------------------------

def _build_primitive_dl():
    """手工构造含 rect + img + text 的 DisplayList（不经 parse，
    聚焦三类图元，保证 img 源像素可控）。"""
    dl = _pe.DisplayList()
    # rect：红块 (10,10)-(60,50)。
    dl.commands.append(DisplayCommand(
        "rect", x=10, y=10, w=50, h=40,
        color=_pe.Color(200, 30, 30, 255)))
    # img：4×4 源蓝像素，最近邻放大到 (80,80,20,20)。
    # 注意 0xRRGGBB：0x0000FF = 蓝（避免 RRGGBBAA 误读）。
    blue = [0x0000FF] * 16
    dl.commands.append(DisplayCommand(
        "img", x=80, y=80, w=20, h=20,
        img_pixels=blue, img_src_w=4, img_src_h=4))
    # text：黑色 "Hi"。
    dl.commands.append(DisplayCommand(
        "text", x=10, y=100, text="Hi", font_px=16,
        color=_pe.Color(0, 0, 0, 255)))
    return dl


@_test
def test_dynamic_rect_pass_pixel_matches_oracle():
    """rect 图元：pass 与 oracle 逐像素一致（红块内部）。"""
    dl = _build_primitive_dl()
    frame = _rp.SoftwareRenderPass(200, 200).render(dl)
    oracle = _rp.cpu_oracle_frame(dl, 200, 200)
    # 红块内部 (15,15) 与边缘 (55,45)。
    for (x, y) in ((15, 15), (55, 45), (10, 10)):
        assert frame.get_pixel(x, y) == oracle.get_pixel(x, y), (
            f"rect pixel ({x},{y}) pass={frame.get_pixel(x, y)} "
            f"oracle={oracle.get_pixel(x, y)} 不一致")
    # 整帧对照 0 mismatch。
    cmp = _rp.compare_frames(frame, oracle, tolerance=0)
    assert cmp["ok"], f"rect 整帧对照失败: {cmp.get('count')} mismatch"


@_test
def test_dynamic_img_pass_pixel_matches_oracle():
    """img 图元：最近邻放大后 pass 与 oracle 逐像素一致（蓝块内部）。"""
    dl = _build_primitive_dl()
    frame = _rp.SoftwareRenderPass(200, 200).render(dl)
    oracle = _rp.cpu_oracle_frame(dl, 200, 200)
    for (x, y) in ((85, 85), (95, 95), (99, 99)):
        assert frame.get_pixel(x, y) == oracle.get_pixel(x, y), (
            f"img pixel ({x},{y}) pass={frame.get_pixel(x, y)} "
            f"oracle={oracle.get_pixel(x, y)} 不一致")
    # 蓝块应为 (0,0,255)。
    assert frame.get_pixel(85, 85) == (0, 0, 255), (
        f"img 蓝块内部应为 (0,0,255), got {frame.get_pixel(85, 85)}")


@_test
def test_dynamic_text_pass_consistent_with_oracle():
    """text 图元：pass 与 oracle 一致（有字体时黑字形；无字体时
    两侧皆退化不画 → 仍一致；不冒充字形已真实渲染）。"""
    dl = _build_primitive_dl()
    frame = _rp.SoftwareRenderPass(200, 200).render(dl)
    oracle = _rp.cpu_oracle_frame(dl, 200, 200)
    cmp = _rp.compare_frames(frame, oracle, tolerance=0)
    assert cmp["ok"], (
        f"text 图元 pass vs oracle 不一致: "
        f"{len(cmp['mismatches'])} mismatch, sample={cmp['sample'][:3]}")


@_test
def test_dynamic_pass_kind_counts_trace():
    """frame trace——pass 记录逐图元命令计数（rect/img/text 各 1），
    证明经图元通路而非整帧前处理。"""
    dl = _build_primitive_dl()
    p = _rp.SoftwareRenderPass(200, 200)
    p.render(dl)
    assert p.kind_counts.get("rect") == 1, f"rect 计数: {p.kind_counts}"
    assert p.kind_counts.get("img") == 1, f"img 计数: {p.kind_counts}"
    assert p.kind_counts.get("text") == 1, f"text 计数: {p.kind_counts}"
    assert p.commands_rendered == 3, (
        f"应渲染 3 类图元, got {p.commands_rendered}")


@_test
def test_dynamic_compare_size_mismatch_rejected():
    """对照负例——尺寸不一致直接 ok=False（不冒充通过）。"""
    a = _rp.SoftwareRenderPass(10, 10).render(_pe.DisplayList())
    b = _rp.cpu_oracle_frame(_pe.DisplayList(), 12, 10)
    cmp = _rp.compare_frames(a, b)
    assert not cmp["ok"] and "size mismatch" in cmp.get("reason", ""), (
        f"尺寸不一致必须判负: {cmp}")


@_test
def test_dynamic_gpubackend_render_with_pass_and_oracle():
    """GPUBackend 接入——render_with_pass 产 frame_pass 帧 +
    verify_frame_vs_oracle 独立 oracle 对照通过。"""
    dl = _build_primitive_dl()
    be = _gpu.GPUBackend(device=None)  # CPU 回退形态（无 wgpu）
    frame = be.render_with_pass(dl, {"width": 200, "height": 200})
    assert frame.frame_pass is True, "帧必须带 frame_pass 标记"
    assert frame.pass_kind_counts.get("rect") == 1
    assert frame.pass_kind_counts.get("img") == 1
    assert frame.pass_kind_counts.get("text") == 1
    # 独立 oracle 对照 0 mismatch。
    cmp = be.verify_frame_vs_oracle(
        frame, dl, {"width": 200, "height": 200}, tolerance=0)
    assert cmp["ok"], f"render_with_pass vs oracle 对照失败: " \
                      f"{cmp.get('count')} mismatch"
    assert cmp["oracle_mode"] == "rasterize_to_canvas", (
        "oracle 必须标注独立通路 rasterize_to_canvas")


@_test
def test_dynamic_gpubackend_verify_rejects_non_pass_frame():
    """负例——CPU 后端的常规 render 帧（无 frame_pass）被 oracle
    对照拒绝，不冒充"经 pass 通路"。"""
    dl = _build_primitive_dl()
    be = _gpu.GPUBackend(device=None)
    bad = _gpu.CPURenderBackend().render(dl, {"width": 200, "height": 200})
    cmp = be.verify_frame_vs_oracle(
        bad, dl, {"width": 200, "height": 200})
    assert not cmp["ok"] and "not a pass frame" in cmp.get("reason", ""), (
        f"非 pass 帧必须被拒绝: {cmp}")


def run():
    for name, f in TESTS:
        print(f"  {name}")
        f()


if __name__ == "__main__":
    run()
    print(f"OK ({len(TESTS)} tests)")

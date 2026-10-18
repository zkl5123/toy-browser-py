"""P06 — 字形图集（GlyphAtlas）回归（批次 50）。

依据 GPU 光栅化迁移方案 P06"字形 atlas"：按字形 key（码点 +
family + weight + font_px）去重，打包进 atlas 纹理 + UV 查询 +
refcount 生命周期 + 容量 LRU 淘汰 + prepare 预解析，接在 P05
图片纹理缓存之上（GPU 侧逐阶段迁移）；无 wgpu 设备时退化为
**逻辑**atlas 形态（不上传，仅槽位 / UV 记录，P13 不崩）。

覆盖：
- get_or_rasterize 同 key 命中去重（uploads 仍 1、uv/slot 不变）
- 不同 key 各自打包（pack_slots 增长、uv 逐槽位推进）
- refcount 生命周期（ref>=2 的条目不被容量 LRU 驱逐、可常驻超
  容量；unref 回 1 后可随容量淘汰）
- release 显式释放（refcount 清零 + 立即移除，二次 release False）
- prepare 预解析（文档加载期，重复 prepare 同 key 命中不重复
  光栅化）
- capacity=0 不限驻留（LRU 不驱逐）
- uv 查询（归一化 UV 子矩形，未驻留返回 None）
- 无 wgpu 设备时逻辑 atlas（has_atlas=False，不崩，P13）
- describe 诊断串（cap/res/pack/hit/miss/ev/rate/atlas）
- GPUBackend.glyph_atlas 属性（每后端一个，无 GPU 时仍可访问）
- GPUBackend.render 自动对 text 命令经 atlas 预解析（resident 增
  长 + glyph_atlas_stats 挂 FrameHandle）
- 同资源多帧复用字形（uploads 不变、命中计数）
- create_backend(glyph_atlas_capacity=N) 容量透传（GPU 后端透传，
  CPU 后端不持字形图集）
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from toy_browser.rendering.gpu import (
    GlyphAtlas,
    GPUBackend,
    create_backend,
    _glyph_key,
)
from toy_browser.rendering.paint_engine import DisplayList, DisplayCommand

TESTS: list = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _text_dl(chars: str = "Hello", font_px: float = 16.0) -> DisplayList:
    """构造一个 text 命令 DisplayList（P06 预解析验证用）。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(kind="text", text=chars,
                                      font_px=font_px))
    return dl


@_reg
def test_glyph_atlas_hit_dedupe_by_key():
    """P06 — 同 key 命中去重（uploads 仍 1、uv/slot 不变）。"""
    a = GlyphAtlas(capacity=8)
    uv1, slot1 = a.get_or_rasterize("A")
    uv2, slot2 = a.get_or_rasterize("A")
    assert uv1 == uv2 and slot1 == slot2, "同 key uv/slot 不变"
    s = a.stats()
    assert s["hits"] == 1 and s["misses"] == 1, f"命中 1 / 未命中 1: {s}"
    assert s["uploads"] == 1, f"uploads 仍 1: {s}"
    assert s["resident"] == 1, f"驻留 1: {s}"


@_reg
def test_glyph_atlas_different_keys_distinct():
    """P06 — 不同 key 各自打包（pack_slots 增长、uv 逐槽位推进）。"""
    a = GlyphAtlas(capacity=16)
    uv_a, slot_a = a.get_or_rasterize("A")
    uv_b, slot_b = a.get_or_rasterize("B")
    assert slot_a != slot_b, "不同 key 不同槽位"
    s = a.stats()
    assert s["pack_slots"] == 2 and s["uploads"] == 2, f"2 个槽位: {s}"
    # UV 逐槽位推进（atlas_width=16 → u 步长 1/16）
    assert abs(uv_b[0] - uv_a[2]) < 1e-6, "B 的 u0 = A 的 u1（逐列）"


@_reg
def test_glyph_atlas_refcount_prevents_lru_eviction():
    """P06 — refcount 生命周期：被外部引用（ref>=2）的字形不被
    容量 LRU 驱逐（可常驻超容量）；unref 回 1 后可随容量淘汰。"""
    a = GlyphAtlas(capacity=2)
    key_hot = _glyph_key("H", "monospace", 400, 16.0)
    a.get_or_rasterize(key_hot, family="monospace", weight=400, font_px=16.0)
    assert a.ref(key_hot) == 2, "ref() 提升到 2"
    # 灌入超容量（驱逐最久未用且 ref<=1 的条目；H 受保护不驱逐）
    for i in range(5):
        a.get_or_rasterize(f"C{i}", family="monospace", weight=400,
                           font_px=16.0)
    s = a.stats()
    assert s["resident"] == 3, f"refcount 保护的 H 应常驻: {s}"
    assert a.uv(key_hot) is not None, "H 仍驻留（refcount 保护）"
    # unref 回 1 → 不再受保护，再灌入应使 H 被 LRU 淘汰
    assert a.unref(key_hot) == 1
    for i in range(6, 9):
        a.get_or_rasterize(f"C{i}", family="monospace", weight=400,
                           font_px=16.0)
    assert a.uv(key_hot) is None, "H 已被 LRU 驱逐（ref=1 可淘汰）"


@_reg
def test_glyph_atlas_release_removes_entry():
    """P06 — release 显式释放（refcount 清零 + 立即移除，二次
    release 返回 False）。"""
    a = GlyphAtlas(capacity=8)
    key = _glyph_key("R", "monospace", 400, 16.0)
    a.get_or_rasterize(key, family="monospace", weight=400, font_px=16.0)
    assert a.release(key) is True, "首次 release 成功"
    assert a.uv(key) is None, "release 后不驻留"
    assert a.release(key) is False, "二次 release 返回 False"
    s = a.stats()
    assert s["evicted"] == 1, f"release 计入驱逐: {s}"


@_reg
def test_glyph_atlas_prepare_preresolves():
    """P06 — prepare 预解析（文档加载期，重复 prepare 同 key 命中
    不重复光栅化）。"""
    a = GlyphAtlas(capacity=32)
    glyphs = [("A", "monospace", 400, 16.0),
              ("B", "monospace", 400, 16.0),
              ("A", "monospace", 400, 16.0)]  # 重复 A
    n = a.prepare(glyphs)
    assert n == 3, "处理 3 个（含命中）"
    s = a.stats()
    assert s["hits"] == 1 and s["misses"] == 2, f"重复 A 命中: {s}"
    assert s["uploads"] == 2, f"uploads 仅 2: {s}"
    assert s["resident"] == 2, f"驻留 2 个不同字形: {s}"


@_reg
def test_glyph_atlas_capacity_zero_unlimited():
    """P06 — capacity=0 不限驻留（LRU 不驱逐）。"""
    a = GlyphAtlas(capacity=0)
    for i in range(20):
        a.get_or_rasterize(chr(ord("A") + i % 26),
                           family="monospace", weight=400, font_px=16.0)
    s = a.stats()
    assert s["evicted"] == 0, f"capacity=0 不驱逐: {s}"
    assert s["resident"] > 0, f"不限驻留: {s}"


@_reg
def test_glyph_atlas_no_gpu_logical_form():
    """P06 — 无 wgpu 设备时逻辑 atlas（has_atlas=False，不崩，
    P13 回退契约）。"""
    a = GlyphAtlas(rasterizer=None, capacity=8)
    uv, slot = a.get_or_rasterize("Z")
    assert uv is not None and slot is not None, "逻辑 atlas 仍记录 uv/slot"
    s = a.stats()
    assert s["has_atlas"] is False, f"无 GPU 时 has_atlas=False: {s}"


@_reg
def test_glyph_atlas_uv_query():
    """P06 — uv 查询（归一化 UV 子矩形，未驻留返回 None）。"""
    a = GlyphAtlas(capacity=8)
    key = _glyph_key("K", "monospace", 400, 16.0)
    a.get_or_rasterize(key, family="monospace", weight=400, font_px=16.0)
    uvr = a.uv(key)
    assert uvr is not None, "驻留字形 uv 非 None"
    (u0, v0), (u1, v1) = uvr
    assert 0.0 <= u0 < u1 <= 1.0 and 0.0 <= v0 < v1 <= 1.0, \
        f"UV 归一化: {uvr}"
    # 未驻留字形返回 None
    assert a.uv("Q") is None, "未驻留返回 None"


@_reg
def test_glyph_atlas_describe_diagnostic():
    """P06 — describe 诊断串（cap/res/pack/hit/miss/ev/rate/atlas，
    供 G10 诊断区）。"""
    a = GlyphAtlas(capacity=4)
    a.get_or_rasterize("A")
    d = a.describe()
    for token in ("cap=", "res=", "pack=", "hit=", "miss=", "ev=",
                  "rate=", "atlas="):
        assert token in d, f"describe 缺 {token!r}: {d}"


@_reg
def test_gpu_backend_glyph_atlas_property():
    """P06 — GPUBackend.glyph_atlas 属性（每后端一个，无 GPU 时
    仍可访问，逻辑 atlas 形态不崩，P13）。"""
    b = GPUBackend(device=None, glyph_atlas_capacity=8)
    assert isinstance(b.glyph_atlas, GlyphAtlas), "glyph_atlas 属性"
    assert b.glyph_atlas.capacity == 8, "容量透传"


@_reg
def test_gpu_backend_render_prepares_glyph_atlas():
    """P06 — GPUBackend.render 自动对 text 命令经 atlas 预解析
    （resident 增长 + glyph_atlas_stats 挂 FrameHandle）。"""
    b = GPUBackend(device=None, glyph_atlas_capacity=32)
    dl = _text_dl("Hi")
    h = b.render(dl, None)
    s = getattr(h, "glyph_atlas_stats", None)
    assert s is not None, "FrameHandle 挂 glyph_atlas_stats"
    assert s["resident"] >= 2, f"text 'Hi' 预解析 2 字形: {s}"
    assert s["uploads"] >= 2, f"uploads >= 2: {s}"
    assert b.glyph_atlas.resident() >= 2, "atlas 驻留增长"


@_reg
def test_gpu_backend_render_reuses_glyph_across_frames():
    """P06 — 同字形多帧复用 atlas 槽位（uploads 不变、命中计数）。"""
    b = GPUBackend(device=None, glyph_atlas_capacity=32)
    dl = _text_dl("Hi")
    b.render(dl, None)
    s1 = dict(b.glyph_atlas.stats())
    b.render(dl, None)  # 同字形第二帧
    s2 = dict(b.glyph_atlas.stats())
    assert s2["uploads"] == s1["uploads"], f"多帧不重复光栅化: {s1}→{s2}"
    assert s2["hits"] > s1["hits"], f"第二帧命中增长: {s1}→{s2}"
    assert s2["resident"] == s1["resident"], "驻留不变"


@_reg
def test_create_backend_glyph_atlas_capacity_passthrough():
    """P06 — create_backend(glyph_atlas_capacity=N) 容量透传（GPU
    后端透传；CPU 后端不持字形图集）。"""
    from toy_browser.rendering.renderer.contracts import BackendMode
    from toy_browser.rendering.renderer.cpu_backend import CPURenderBackend
    cpu = create_backend(BackendMode.CPU, glyph_atlas_capacity=8)
    assert isinstance(cpu, CPURenderBackend), "cpu 不持字形图集"
    assert not hasattr(cpu, "glyph_atlas"), "CPU 后端无 glyph_atlas"
    gpu = create_backend(BackendMode.GPU, device=None,
                         glyph_atlas_capacity=16)
    if isinstance(gpu, GPUBackend):
        assert gpu.glyph_atlas.capacity == 16, "GPU 后端容量透传"
    else:
        # 无 wgpu 设备 → CPU 回退（P13 契约，不崩）
        assert isinstance(gpu, CPURenderBackend)


TESTS.sort(key=lambda t: t[0])

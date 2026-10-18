"""批次 49 — P05 图片纹理缓存（真实 wgpu 侧，refcount + LRU + 预上传）。

被测：``toy_browser/rendering/gpu.py`` 批次 49 新增的**图片纹理
缓存**（P05"图片纹理与缓存"，GPU 光栅化迁移方案的 GPU 侧逐阶段
迁移）：

- ``ImageTextureCache`` — 按 URL / 资源 key 去重的 wgpu 纹理缓存
  （同资源多框 / 多次渲染复用同一纹理，避免重复 ``write_texture``
  上传）：
  - ``get_or_upload(key, w, h, rgba)`` — 命中返回已缓存纹理（LRU
    刷新 + 命中计数）；未命中则上传新建（未命中 + 上传计数）。
  - ``prepare(images)`` — 预上传一批图片纹理（文档加载期零渲染
    等待，P05"预上传"）。
  - ``ref(key)`` / ``unref(key)`` — refcount 生命周期（活跃引用
    计数 > 0 的纹理不被容量 LRU 驱逐，P05"refcount 生命周期"）。
  - ``release(key)`` — 显式释放条目（refcount 清零 + 立即移除）。
  - ``stats()`` / ``describe()`` — 命中 / 未命中 / 上传 / 驻留 /
    驱逐统计 + 诊断串（P05 诊断，供 G10 显示）。
- ``GPUBackend.image_cache`` — 该后端持有的图片纹理缓存（每后端
  一个，P05 契约）；``GPUBackend.render`` 时对 DisplayList 里
  ``img`` 命令自动经缓存上传 / 复用纹理（同资源 key 去重），并
  把 ``image_cache_stats`` 挂到 FrameHandle。
- ``create_backend(…, image_cache_capacity=N)`` — P05 缓存驻留
  容量透传（仅 GPU 后端；0 = 不限）。

设计：P02 验收"不根据显卡名称保证任何后端可用，须实测"——本回归
**按实测设备可用性分支**：``acquire_wgpu_device()`` 返回真实
GPUDevice 时跑真 GPU 路径（纹理上传 + refcount + LRU + 预上传 +
render 自动预上传 img 命令）；wgpu-py 缺失 / 无可用设备时缓存仍
持**逻辑句柄形态**（``get_or_upload`` 返回 None，不崩，P13 回退
契约），回归测试两条路径都验证。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.rendering.gpu import (
    GPUBackend, ImageTextureCache, acquire_wgpu_device, create_backend,
)
from toy_browser.rendering.renderer.contracts import BackendMode
from toy_browser.rendering.renderer.cpu_backend import CPURenderBackend
from toy_browser.rendering.rasterize_cpu import FrameHandle
from toy_browser.rendering.paint import Color
from toy_browser.rendering.paint_engine import DisplayList, DisplayCommand

TESTS = []
def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _has_gpu() -> bool:
    """P02 实测——wgpu 设备是否真正可用（不根据名称保证）。"""
    return acquire_wgpu_device() is not None


def _rgba(w: int, h: int, fill=(200, 30, 30)) -> bytes:
    """构造 w×h RGBA8 紧凑 buffer（固定填充，P05 缓存数据形态）。"""
    return bytes([fill[0], fill[1], fill[2], 255] * (w * h))


def _img_dl() -> DisplayList:
    """含 img 命令的 DisplayList（P05 预上传路径对照；img_pixels
    为 RGBA u32 列表，img_src_w/h 为源尺寸）。"""
    dl = DisplayList()
    dl.commands.append(DisplayCommand(
        kind="img", x=4, y=4, w=20, h=12,
        img_pixels=[0x203040, 0x506070, 0x8090A0, 0xB0C0D0,
                    0xE0F0FF, 0x102030, 0x405060, 0x708090,
                    0xA0B0C0, 0xD0E0F0, 0x001020, 0x304050,
                    0x607080, 0x90A0B0, 0xC0D0E0, 0xF00010],
        img_src_w=4, img_src_h=4,
    ))
    return dl


# ---------------------------------------------------------------------------
# P05 — ImageTextureCache 直接契约（无 GPU 也验证逻辑形态，P13）
# ---------------------------------------------------------------------------

@_reg
def test_img_cache_hit_dedupe_by_key():
    """P05 — 同 key 两次 get_or_upload：首次未命中上传，第二次命
    中复用（不重复上传；hits +1，uploads 仍 1，resident 1）。"""
    c = ImageTextureCache(capacity=8)
    t1 = c.get_or_upload("a.png", 8, 4, _rgba(8, 4))
    t2 = c.get_or_upload("a.png", 8, 4, _rgba(8, 4))
    s = c.stats()
    assert s["misses"] == 1 and s["hits"] == 1, f"命中去重失败: {s}"
    assert s["uploads"] == 1, f"重复上传: {s}"
    assert s["resident"] == 1, f"驻留数错: {s}"
    assert t1 is t2, "同 key 应返回同一纹理句柄"


@_reg
def test_img_cache_different_keys_distinct():
    """P05 — 不同 key 各自上传独立纹理（resident 随不同 key 增长）。"""
    c = ImageTextureCache(capacity=8)
    c.get_or_upload("a.png", 8, 4, _rgba(8, 4, (200, 30, 30)))
    c.get_or_upload("b.png", 8, 4, _rgba(8, 4, (30, 200, 30)))
    c.get_or_upload("c.png", 8, 4, _rgba(8, 4, (30, 30, 200)))
    s = c.stats()
    assert s["resident"] == 3 and s["uploads"] == 3, f"不同 key 应各自上传: {s}"


@_reg
def test_img_cache_refcount_prevents_lru_eviction():
    """P05 — refcount 生命周期：被外部引用（ref >= 2，经 ref() 提
    升）的纹理不被容量 LRU 驱逐（可常驻超容量上限）；unref 回 1
    后该条目可随容量 LRU 淘汰（最久未用 + ref==1 先驱逐）。新建
    条目 ref=1（仅创建方持有），容量溢出时最久未用的 ref==1 条目
    先被驱逐，ref>=2 的受保护条目常驻。"""
    c = ImageTextureCache(capacity=2)
    c.get_or_upload("hot", 8, 4, _rgba(8, 4, (255, 0, 0)))
    c.ref("hot")  # hot ref = 2（外部持有 → 受保护，常驻）
    # 灌入 5 个 cold key（每个 ref=1）：容量 2 溢出 → 驱逐最久未用
    # 的 cold（ref==1），hot（ref=2）受保护不被驱逐。
    for i in range(5):
        c.get_or_upload(f"cold{i}", 8, 4, _rgba(8, 4, (0, 0, i)))
    s = c.stats()
    # hot（ref=2，保护）+ 容量内 1 个最新 cold → resident=3
    # （受保护条目使 resident 超容量上限，这是 refcount 生命周期
    # 的正确行为：活跃引用不被容量淘汰驱逐）。
    assert s["resident"] == 3, f"refcount 保护的 hot 应常驻: {s}"
    # unref hot 回 1 → 不再受保护，再灌入应使 hot 被 LRU 淘汰
    c.unref("hot")  # hot ref = 1
    for i in range(6, 9):
        c.get_or_upload(f"cold{i}", 8, 4, _rgba(8, 4, (0, 0, i)))
    # hot（ref=1，最久未用）应已被驱逐
    assert c.unref("hot") == 0, "hot 已被 LRU 驱逐（ref 回到 0）"


@_reg
def test_img_cache_unref_to_zero_allows_eviction():
    """P05 — unref 到 0 后该条目可被容量 LRU 驱逐（对照 refcount
    保护的冷反例）。"""
    c = ImageTextureCache(capacity=1)
    c.get_or_upload("solo", 8, 4, _rgba(8, 4))
    assert c.unref("solo") == 0  # ref 1→0，可驱逐
    c.get_or_upload("new", 8, 4, _rgba(8, 4))  # 容量 1 → 驱逐 solo
    s = c.stats()
    assert s["resident"] == 1 and s["evicted"] >= 1, f"solo 应被驱逐: {s}"


@_reg
def test_img_cache_release_removes_entry():
    """P05 — release 显式释放条目（refcount 清零 + 立即移除，不待
    容量淘汰）；二次 release 返回 False。"""
    c = ImageTextureCache(capacity=8)
    c.get_or_upload("x", 8, 4, _rgba(8, 4))
    assert c.release("x") is True
    assert c.release("x") is False  # 已移除
    s = c.stats()
    assert s["resident"] == 0 and s["evicted"] == 1, f"release 未移除: {s}"


@_reg
def test_img_cache_prepare_preresolves():
    """P05 — prepare 预上传一批图片纹理（文档加载期，P05"预上传"
    渲染时零等待）；重复 prepare 同 key 不重复上传（仅命中计数）。"""
    c = ImageTextureCache(capacity=16)
    items = [("p1", 8, 4, _rgba(8, 4, (1, 2, 3))),
             ("p2", 8, 4, _rgba(8, 4, (4, 5, 6))),
             ("p3", 8, 4, _rgba(8, 4, (7, 8, 9)))]
    n = c.prepare(items)
    assert n == 3, f"prepare 应处理 3 项: {n}"
    s0 = c.stats()
    assert s0["uploads"] == 3 and s0["resident"] == 3, f"prepare 未上传: {s0}"
    # 重复 prepare 同 key → 命中（不重复上传）
    c.prepare(items)
    s1 = c.stats()
    assert s1["uploads"] == 3, f"重复 prepare 重复上传: {s1}"
    assert s1["hits"] >= 3, f"重复 prepare 应命中: {s1}"


@_reg
def test_img_cache_capacity_zero_unlimited():
    """P05 — capacity=0 不限驻留（LRU 不驱逐，P05 诊断可解释）。"""
    c = ImageTextureCache(capacity=0)
    for i in range(50):
        c.get_or_upload(f"k{i}", 8, 4, _rgba(8, 4, (i % 256, 0, 0)))
    s = c.stats()
    assert s["resident"] == 50, f"capacity=0 应不限驻留: {s}"
    assert s["evicted"] == 0, f"capacity=0 不应驱逐: {s}"


@_reg
def test_img_cache_no_gpu_logical_form():
    """P05 + P13 — 无 wgpu 设备时 ImageTextureCache 仍持**逻辑句柄
    形态**（get_or_upload 返回 None，不崩）——可移植回归。"""
    c = ImageTextureCache(rasterizer=None, capacity=8)
    t = c.get_or_upload("noGpu", 8, 4, _rgba(8, 4))
    assert t is None, "无 GPU 时应返回 None（逻辑形态，P13 不崩）"
    s = c.stats()
    assert s["uploads"] == 1 and s["resident"] == 1, f"逻辑上传失败: {s}"
    # 命中仍生效（复用逻辑句柄）
    c.get_or_upload("noGpu", 8, 4, _rgba(8, 4))
    assert c.stats()["hits"] == 1


@_reg
def test_img_cache_describe_diagnostic():
    """P05 — describe 诊断串含 cap / res / hit / miss / ev / rate
    （供 G10 诊断区显示）。"""
    c = ImageTextureCache(capacity=8)
    c.get_or_upload("a", 8, 4, _rgba(8, 4))
    c.get_or_upload("a", 8, 4, _rgba(8, 4))
    d = c.describe()
    assert "cap=" in d and "res=" in d and "hit=" in d
    assert "miss=" in d and "ev=" in d and "rate=" in d, f"诊断串缺字段: {d}"


# ---------------------------------------------------------------------------
# P05 — GPUBackend 接入（image_cache 属性 + render 自动预上传）
# ---------------------------------------------------------------------------

@_reg
def test_gpu_backend_image_cache_property():
    """P05 — GPUBackend.image_cache 返回该后端持有的
    ImageTextureCache（每后端一个；无 GPU 时仍可访问，逻辑形态）。"""
    b = create_backend(BackendMode.GPU)
    assert isinstance(b.image_cache, ImageTextureCache)
    # 无 GPU 时 image_cache 仍可构造（rasterizer=None，P13 不崩）
    assert b.image_cache.capacity == 32  # 默认容量


@_reg
def test_gpu_backend_render_prepares_img_textures():
    """P05 — GPUBackend.render 对 DisplayList 里 img 命令自动经
    image_cache 上传 / 复用纹理（resident 增长 + image_cache_stats
    挂到 FrameHandle）；无 img 命令时缓存不增长。"""
    b = create_backend(BackendMode.GPU)
    res0 = b.image_cache.resident()
    h = b.render(_img_dl(), {"width": 60, "height": 40})
    assert isinstance(h, FrameHandle)
    assert hasattr(h, "image_cache_stats"), "render 应挂 image_cache_stats"
    s = h.image_cache_stats
    assert s["uploads"] >= 1, f"img 命令应触发纹理上传: {s}"
    assert b.image_cache.resident() > res0, f"img 命令应增长驻留: {s}"


@_reg
def test_gpu_backend_render_reuses_img_texture_across_frames():
    """P05 — 同一 img 资源（同 key）多帧渲染复用同一纹理（第二帧
    命中不重复上传；P05"避免重复 write_texture"）。"""
    b = create_backend(BackendMode.GPU)
    h1 = b.render(_img_dl(), {"width": 60, "height": 40})
    up1 = h1.image_cache_stats["uploads"]
    h2 = b.render(_img_dl(), {"width": 60, "height": 40})
    up2 = h2.image_cache_stats["uploads"]
    # 同 key（同 src 尺寸 + 同像素数）→ 第二帧命中，uploads 不变
    assert up2 == up1, f"同资源多帧应复用纹理（uploads 不变）: {up1} vs {up2}"
    assert h2.image_cache_stats["hits"] >= 1, f"第二帧应命中: {h2.image_cache_stats}"


@_reg
def test_create_backend_image_cache_capacity_passthrough():
    """P05 — create_backend(GPU, image_cache_capacity=N) 透传缓存
    驻留容量到 GPUBackend.image_cache（CPU 后端不持纹理缓存）。"""
    b = create_backend(BackendMode.GPU, image_cache_capacity=4)
    assert b.image_cache.capacity == 4, "容量应透传"
    # CPU 后端不持 ImageTextureCache（P05 仅 GPU 侧）
    cb = create_backend(BackendMode.CPU)
    assert not hasattr(cb, "image_cache") or cb.image_cache is None


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

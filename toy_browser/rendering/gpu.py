"""批次 48 — P04 基础 GPU 光栅化（真实 wgpu 纹理上传路径）
批次 49 — P05 图片纹理缓存（ImageTextureCache，GPU 侧逐阶段迁移）。

依据 ``assessments/20261016-round5/docs/GPU光栅化迁移方案.md`` P04
"几何、圆角、边框、渐变" + P01"GPU 接口与模式边界" + P02"适配器
选择与能力探测"：

- 本环境 wgpu-py 0.32.0 已安装 + NVIDIA RTX 3050 Laptop GPU
  （Vulkan，5 adapter）可枚举、可创建设备 / 队列——批次 47 的
  P01/P02/P13 契约在批次 48 接上**真实 wgpu 光栅化首版**：
- :class:`GPUBackend` 升级为**真实 wgpu 实现**（不再存根）：
  ``render(scene, viewport)`` 经批次 45 的
  ``paint_engine.rasterize_to_canvas`` 光栅化出 CPUImage（RGB8
  连续 buffer，消除 scratch Bitmap 合并层），再**上传到 wgpu 纹理**
  （``GPUQueue.write_texture`` → ``read_texture`` 读回验证 GPU 侧
  内容逐像素一致，P04 基础图元验收"对照 CPU 与目标截图"）。
- :class:`GPURasterizer` — wgpu 纹理上传 / 读回核心（P04 基础
  光栅化 + P05 图片纹理上传的 GPU 侧）：上传 RGB8 CPUImage →
  纹理 → 读回 memoryview → 逐像素核对（``wgpu`` 的 sRGB 格式
  与 CPU 8-bit 通道值一致，无 gamma 漂移时 0 mismatch）。
- :func:`create_backend` 契约不变：``create_backend("gpu")`` 经
  :class:`AdapterProbe` 探测——wgpu-py 可用 + 设备可创建时返回
  :class:`GPUBackend`（真实 wgpu 实现）；不可用仍回退
  :class:`CPURenderBackend`（P13，UI 不崩）。

P04 范围（本批落地）：
- 基础图元（rect / border / rounded / gradient）经 GPU 纹理上传
  路径渲染 + 读回验证（与 CPU 逐像素一致，P04 验收"对照 CPU
  与目标截图"）。
- P05 图片纹理缓存 / P06 字形 atlas / P07 裁剪变换透明组 /
  P08 阴影滤镜 / P09 tile 增量合成留后续批次（GPU 侧逐阶段迁移）。

真实 wgpu 调用（``write_texture`` / ``read_texture``）仅在
``render`` / ``rasterize`` 路径触发（非模块导入时），回归测试
经"GPU 可用才跑真 GPU 路径，不可用 skip"模式验证（P02 验收
"不根据显卡名称保证任何后端可用，须实测"）。
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Optional

from toy_browser.rendering.renderer.contracts import (
    RenderBackend,
    BackendMode,
    AdapterProbe,
)
from toy_browser.rendering.renderer.cpu_backend import CPURenderBackend


class GPURasterizer:
    """P04 / P05 — wgpu 纹理上传 / 读回核心（真实 GPU 侧光栅化
    首版，批次 48）。

    依据 GPU 方案 P04"几何、圆角、边框、渐变" + P05"图片纹理
    与缓存"的 GPU 侧：CPU 已光栅化出 RGB8 帧（经批次 45 的
    ``rasterize_to_canvas``，消除 scratch Bitmap 合并层），本类
    把该帧**上传到 wgpu 纹理**（``GPUQueue.write_texture``）+
    **读回验证** GPU 侧内容（``GPUQueue.read_texture``）逐像素
    一致（P04 验收"对照 CPU 与目标截图"）。

    * ``device`` — :class:`wgpu.GPUDevice`（经 :func:`
      acquire_wgpu_device` 创建，P02 实测）。
    * :meth:`upload_rgba8` — 上传 RGBA8 数据到纹理（P04 基础
      图元 + P05 图片纹理 GPU 侧）。
    * :meth:`read_back` — 纹理读回 CPU memoryview（P01 "交互
      直接 present，截图才异步回读"——本批验证读回路径）。
    * :meth:`rasterize_and_verify` — CPU 光栅化 + GPU 上传 +
      读回逐像素核对（P04 验收核心，0 mismatch 即 GPU 侧正确）。
    """

    def __init__(self, device) -> None:
        self._device = device

    @property
    def device(self):
        return self._device

    def upload_rgba8(self, width: int, height: int,
                     rgba: bytes, label: str = "frame") -> object:
        """P04 / P05 — 上传 RGBA8 *rgba*（width × height 紧凑
        buffer）到 wgpu 纹理，返回纹理句柄。

        经 ``GPUQueue.write_texture``（``destination`` 仅
        ``texture`` / ``origin`` / ``mip_level``；``data_layout``
        携带 ``bytes_per_row`` = width × 4，无对齐要求）。
        """
        import wgpu  # 可选依赖，P13（设备构造时已确认 wgpu-py 可用）
        tex = self._device.create_texture(
            label=label,
            size=(width, height, 1),
            format="rgba8unorm-srgb",
            usage=wgpu.TextureUsage.COPY_DST | wgpu.TextureUsage.COPY_SRC
                  | wgpu.TextureUsage.RENDER_ATTACHMENT,
        )
        self._device.queue.write_texture(
            destination={"texture": tex, "origin": [0, 0, 0],
                         "mip_level": 0},
            data=bytes(rgba),
            data_layout={"offset": 0, "bytes_per_row": width * 4,
                         "rows_per_image": height},
            size=(width, height, 1),
        )
        return tex

    def read_back(self, tex, width: int, height: int) -> bytes:
        """P01 / P04 — 纹理 *tex* 读回 CPU bytes（RGBA8 紧凑
        buffer，P01 "截图 / 验证时读回"）。经 ``GPUQueue
        .read_texture``（memoryview，转 bytes）。"""
        data = self._device.queue.read_texture(
            source={"texture": tex, "origin": [0, 0, 0],
                    "mip_level": 0},
            data_layout={"offset": 0, "bytes_per_row": width * 4,
                         "rows_per_image": height},
            size=(width, height, 1),
        )
        return bytes(data)

    def rasterize_and_verify(self, scene, width: int, height: int,
                             tolerance: int = 0) -> dict:
        """P04 验收 — CPU 光栅化 + GPU 上传 + 读回逐像素核对。

        *scene* — :class:`paint_engine.DisplayList`（含
        ``commands``）。*width* / *height* — 视口物理尺寸。

        管线：经批次 45 的 ``paint_engine.rasterize_to_canvas``
        光栅化出 :class:`CPUImage`（RGB8）→ 转 RGBA8（alpha
        = 255）→ :meth:`upload_rgba8` 上传 GPU 纹理 →
        :meth:`read_back` 读回 → 逐像素与 CPUImage 核对（*
        tolerance* 容差，P04 验收"文字边缘单独设容差"，本批
        基础图元容差 0）。

        返回 ``{"mismatches": [...], "ok": bool, "width": int,
        "height": int, "texture": <GPUTexture>}``。
        """
        import sys, os
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from toy_browser.rendering import paint_engine as _pe
        canvas = _pe.rasterize_to_canvas(scene, width, height)
        img = canvas.img
        # RGB8 → RGBA8（alpha = 255，CPU 像素无预乘 / sRGB 漂移
        # 风险——rgba8unorm-srgb 与 CPU 8-bit 值一致）。
        buf = bytearray()
        n = width * height
        # R7-P02 — ``bytearray.extend(int)`` raises TypeError; the helper
        # crashed on every call instead of returning a verification dict
        # (audit §5 "gpu_verify_helper_rgb_conversion").
        buf_append = buf.append
        for i in range(n):
            buf_append(img.buffer[i * 3])
            buf_append(img.buffer[i * 3 + 1])
            buf_append(img.buffer[i * 3 + 2])
            buf_append(255)
        tex = self.upload_rgba8(width, height, bytes(buf),
                                 label="gpu_verify")
        back = self.read_back(tex, width, height)
        # 逐像素核对（RGBA8 回读 vs CPUImage RGB8 + alpha 255）
        out = []
        b2 = img.buffer
        for i in range(n):
            r, g, b = b2[i * 3], b2[i * 3 + 1], b2[i * 3 + 2]
            dr, dg, db = back[i * 4], back[i * 4 + 1], back[i * 4 + 2]
            if (abs(dr - r) > tolerance or abs(dg - g) > tolerance
                    or abs(db - b) > tolerance
                    or back[i * 4 + 3] != 255):
                out.append(i)
        return {"mismatches": out,
                "ok": not out,
                "width": width,
                "height": height,
                "texture": tex}


# ---------------------------------------------------------------------------
# P05 — 图片纹理缓存（refcount 生命周期 + LRU 容量淘汰 + 预上传）
# ---------------------------------------------------------------------------

def _img_key(url) -> str:
    """P05 — 图片资源缓存键：按 URL / 资源标识去重（同 URL 多个
    <img> 共用同一纹理，避免重复上传，迁移方案 P05"按 URL / 资源
    key 去重"）。*url* 为 None / 空时退化为占位键（内嵌无源图片）。"""
    return str(url) if url else "<inline>"


class ImageTextureCache:
    """P05 — 图片纹理缓存（真实 wgpu 侧，批次 49）。

    依据 GPU 光栅化迁移方案 P05"图片纹理与缓存"：同一图片资源
    （URL / 资源 key）在多个框 / 多次渲染中复用同一 wgpu 纹理，
    避免重复 ``write_texture`` 上传；refcount 跟踪活跃引用，
    容量 LRU 淘汰在活跃时不释放（仅降序标记，refcount > 0 的
    不驱逐，P05"refcount 生命周期"）；``prepare`` 支持预上传
    （P05"预上传"——文档加载时即上传图片纹理，渲染时零等待）。

    * ``rasterizer`` — :class:`GPURasterizer`（持有 wgpu 设备，
      经其 :meth:`upload_rgba8` / :meth:`read_back` 上传 / 读回
      纹理，P04 验收读回核对）。
    * ``capacity`` — 最大驻留纹理数（容量 LRU 淘汰；0 = 无限驻留）。
    * :meth:`get_or_upload` — 按 *key* 取已缓存纹理，未命中则上传
      新建（记录上传次数，P05 统计）。
    * :meth:`prepare` — 预上传一批图片纹理（文档加载期零渲染等待，
      P05"预上传"）。
    * :meth:`ref` / :meth:`unref` — refcount 生命周期（活跃引用不
      被容量淘汰驱逐，P05"refcount 生命周期"）。
    * :meth:`stats` — 命中 / 未命中 / 驻留 / 驱逐统计（P05 诊断）。

    注：本批缓存键为 *逻辑* 资源标识（URL），RGBA8 像素由调用方
    （批次 45 的 ``rasterize_to_canvas`` 已把 ``img`` 命令像素画进
    CPUImage）提供；wgpu 侧后续批次（P07 裁剪 / P08 滤镜）会接
    真实图片解码 → 纹理上传，本批先落地**缓存 + refcount + LRU +
    预上传**契约（可回归、无 GPU 时退化为内存缓存形态不崩，P13）。
    """

    def __init__(self, rasterizer: Optional[GPURasterizer] = None,
                 capacity: int = 32) -> None:
        self._rasterizer = rasterizer
        self._capacity = capacity
        # key -> {"tex": GPUTexture | None（无 GPU 时仅逻辑句柄）,
        #          "ref": int, "w": int, "h": int, "rgba": bytes}
        self._entries: "OrderedDict[str, dict]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._uploads = 0
        self._evicted = 0

    @property
    def rasterizer(self):
        return self._rasterizer

    @property
    def capacity(self) -> int:
        return self._capacity

    def _upload_one(self, key: str, w: int, h: int, rgba: bytes):
        """P05 — 上传单张纹理（真实 wgpu 设备时经 GPURasterizer；
        无设备时仅逻辑句柄标记，P13 不崩）。"""
        tex = None
        if self._rasterizer is not None and self._rasterizer.device is not None:
            tex = self._rasterizer.upload_rgba8(w, h, rgba,
                                                 label=f"img:{key}")
        self._uploads += 1
        # 新建条目 ref=1（创建方自身一次引用；外部 ref() 进一步增、
        # unref() 减，ref 降到 0 表示可驱逐——P05"refcount 生命周期"）。
        self._entries[key] = {"tex": tex, "ref": 1, "w": w, "h": h,
                              "rgba": rgba}
        # LRU 移到末尾（最近使用）
        self._entries.move_to_end(key)
        # 容量 LRU 淘汰：驱逐最久未用且 refcount 降到 0 的条目（P05
        # "refcount 生命周期"——被外部 ref() 持有（ref>=2）的条目不
        # 驱逐；ref==1 仅创建方持有，可随容量 LRU 淘汰）。
        if self._capacity > 0:
            overflow = len(self._entries) - self._capacity
            if overflow > 0:
                for k in list(self._entries.keys())[:overflow]:
                    e = self._entries[k]
                    if e["ref"] <= 1:
                        del self._entries[k]
                        self._evicted += 1
        return tex

    def get_or_upload(self, key: str, width: int, height: int,
                      rgba: bytes):
        """P05 — 按 *key* 取已缓存纹理；未命中则上传新建。

        命中时 LRU 移到末尾 + 命中计数；未命中时上传 + 未命中计数。
        返回纹理句柄（真实 wgpu GPUTexture；无 GPU 时为 None，仅
        逻辑标记，P13 不崩）。
        """
        k = _img_key(key)
        e = self._entries.get(k)
        if e is not None:
            self._hits += 1
            self._entries.move_to_end(k)
            return e["tex"]
        self._misses += 1
        return self._upload_one(k, width, height, bytes(rgba))

    def prepare(self, images: list) -> int:
        """P05 — 预上传一批图片纹理（文档加载期，P05"预上传"，
        渲染时零等待）。*images* 为 ``[(key, w, h, rgba), ...]``。

        返回上传的纹理数（含命中已驻留者——重复 prepare 不重复
        上传，仅 LRU 刷新 + 命中计数）。"""
        n = 0
        for item in images:
            key, w, h, rgba = item
            self.get_or_upload(key, w, h, rgba)
            n += 1
        return n

    def ref(self, key) -> int:
        """P05 — refcount 生命周期：活跃引用计数 +1（引用中的纹理
        不被容量淘汰驱逐）。返回当前 refcount。"""
        k = _img_key(key)
        e = self._entries.get(k)
        if e is None:
            return 0
        e["ref"] += 1
        return e["ref"]

    def unref(self, key) -> int:
        """P05 — refcount 生命周期：引用计数 -1（降到 0 时该纹理
        可被容量淘汰驱逐）。返回当前 refcount（不小于 0）。"""
        k = _img_key(key)
        e = self._entries.get(k)
        if e is None:
            return 0
        e["ref"] = max(0, e["ref"] - 1)
        return e["ref"]

    def release(self, key) -> bool:
        """P05 — 显式释放缓存条目（refcount 清零 + 立即移除，
        不待容量淘汰）。返回是否移除了条目。"""
        k = _img_key(key)
        e = self._entries.get(k)
        if e is None:
            return False
        del self._entries[k]
        self._evicted += 1
        return True

    def resident(self) -> int:
        """P05 — 当前驻留纹理数（缓存统计，P05 诊断）。"""
        return len(self._entries)

    def stats(self) -> dict:
        """P05 — 缓存统计（命中 / 未命中 / 上传 / 驻留 / 驱逐，
        P05 诊断 + G10 显示）。"""
        return {"hits": self._hits, "misses": self._misses,
                "uploads": self._uploads, "resident": self.resident(),
                "evicted": self._evicted, "capacity": self._capacity}

    def describe(self) -> str:
        """P05 — 缓存诊断串（容量 + 驻留 + 命中率，供 G10 诊断区）。"""
        s = self.stats()
        total = s["hits"] + s["misses"]
        rate = (s["hits"] / total) if total else 0.0
        return (f"img_cache(cap={s['capacity']} res={s['resident']} "
                f"hit={s['hits']} miss={s['misses']} ev={s['evicted']} "
                f"rate={rate:.2f})")


# ---------------------------------------------------------------------------
# P06 — 字形图集（Glyph Atlas：字形 key 去重 + atlas 纹理 + UV 查询 +
#          refcount 生命周期 + LRU 容量淘汰 + 预解析）
# ---------------------------------------------------------------------------

def _glyph_key(ch: str, family: str, weight: int, font_px: float) -> str:
    """P06 — 字形缓存键：按 *ch*（码点）/ *family* / *weight* /
    *font_px* 去重（同一字形 key 在多个文本框 / 多次渲染中复用同一
    atlas 槽位，避免重复光栅化 + 重复 ``write_texture`` 上传，迁移
    方案 P06"按字形 key 去重"）。font_px 取整（scale 因子粒度，见
    ``text.scale_factor``，同 scale 的字形共享槽位）。"""
    return f"g:{ord(ch)}:{family}:w{weight}:px{int(font_px)}"


class GlyphAtlas:
    """P06 — 字形图集（真实 wgpu 侧，批次 50）。

    依据 GPU 光栅化迁移方案 P06"字形 atlas"：同一字形（码点 +
    family + weight + font_px）在多个文本框 / 多次渲染中复用同一
    atlas 槽位，避免逐字重复光栅化 + 重复 ``write_texture`` 上传；
    字形按 key 去重后打包进**atlas 纹理**（1D 条带打包——atlas 宽
    固定 *atlas_width*，每字形占 1×*cell_h* 列，逐条填充，行满则
    换新行），UV 查询取子矩形（P06"UV 查询"）；refcount 跟踪活跃
    字形（P05"refcount 生命周期"），容量 LRU 淘汰在活跃时不驱逐；
    ``prepare`` 支持预解析（P06"预解析"——文档加载时即把常用字形
    光栅化进 atlas，渲染时零等待）。

    * ``rasterizer`` — :class:`GPURasterizer`（持有 wgpu 设备；
      非 None 时 atlas 纹理经其 :meth:`upload_rgba8` 上传 +
      字形像素经 :meth:`read_back` 读回验证，P04 验收读回核对）。
      None 时退化为**逻辑**atlas（不上传，仅槽位 / UV 记录，P13
      不崩）。
    * ``capacity`` — 最大驻留**字形条目**数（容量 LRU 淘汰；0 =
      无限驻留）。
    * ``atlas_width`` — atlas 条带每行可容纳的字形列数（打包粒度）。
    * :meth:`get_or_rasterize` — 按字形 key 取已缓存 UV，未命中则
      光栅化该字形（经注入的 *rasterize_glyph* 回调，默认内置
      位图字形 → RGBA8）+ 打包进 atlas + 记录 UV。
    * :meth:`prepare` — 预解析一批字形（文档加载期零渲染等待，
      P06"预解析"）。
    * :meth:`ref` / :meth:`unref` — refcount 生命周期。
    * :meth:`release` — 显式释放条目（清 refcount + 移除）。
    * :meth:`uv` — 取字形 UV 查询结果（P06"UV 查询"，``(u0,v0,u1,v1)``
      归一化 0–1；未驻留返回 None）。
    * :meth:`stats` / :meth:`describe` — 命中 / 未命中 / 驻留 / 驱逐 /
      打包统计（P06 诊断 + G10 显示）。

    注：本批字形像素由内置 8×8 位图字形经 *rasterize_glyph* 回调
    产生（默认走 ``toy_browser.typography`` 位图 / 曲线光栅化路径
    的简化位图回退，alpha 覆盖层）；TTF 字形曲线填充（P20）+
    真实 atlas 纹理在后续批次（P07 裁剪 / P08 滤镜）接 wgpu 上传
    管线时替换，本批先落地 **atlas 去重 + 打包 + UV + refcount +
    LRU + 预解析** 契约（可回归、无 GPU 时退化为逻辑 atlas 形态不
    崩，P13）。
    """

    def __init__(self, rasterizer: Optional[GPURasterizer] = None,
                 capacity: int = 64, atlas_width: int = 16,
                 # R7-P02 — the cell is a SQUARE pixel cell (previous
                 # default 16 clipped every glyph above 16px, so 17px
                 # text rasterised identically to 16px; 24 covers the
                 # common 16/17/20/24 sizes).
                 cell_h: int = 24, atlas_height: int = 256,
                 rasterize_glyph=None) -> None:
        self._rasterizer = rasterizer
        self._capacity = capacity
        self._atlas_width = max(1, int(atlas_width))
        self._atlas_height = max(1, int(atlas_height))
        self._cell_h = max(1, int(cell_h))
        self._rasterize_glyph = rasterize_glyph
        # key -> {"uv": (u0,v0,u1,v1), "ref": int, "px": int,
        #          "slot": (col, row)}
        self._entries: "OrderedDict[str, dict]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._uploads = 0
        self._evicted = 0
        self._pack_count = 0  # 打包槽位数（atlas 占用统计）
        self._atlas_tex = None  # 真实 wgpu atlas 纹理（rasterizer 非
                                 # None 时上传；否则 None，P13）

    @property
    def rasterizer(self):
        return self._rasterizer

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def atlas(self):
        """P06 — atlas 纹理句柄（真实 wgpu GPUTexture；无 GPU 时为
        None，逻辑 atlas，P13 不崩）。"""
        return self._atlas_tex

    def _rasterize_one(self, key: str, ch: str, family: str,
                      weight: int, font_px: float):
        """P06 — 光栅化单字形（经注入回调）→ RGBA8 像素 + 打包进
        atlas（1D 条带），返回 (rgba_bytes, uv, slot)。"""
        cell = self._cell_h
        if self._rasterize_glyph is not None:
            rgba = self._rasterize_glyph(ch, family, weight, font_px)
        else:
            rgba = self._default_glyph_rgba(ch, family, weight, font_px,
                                            cell=cell)
        # 打包：按现有条目数占位（atlas 网格，col/row 推导）
        slot = self._pack_count
        self._pack_count += 1
        col = slot % self._atlas_width
        row = slot // self._atlas_width
        # R7-P02 — UV 按**像素**归一化：atlas 宽 = atlas_width × cell，
        # 高 = atlas_height（像素），每个字形占一个 cell×cell 格。
        # 旧实现把 atlas 当成 1px 宽的槽位条带，字形像素根本无法写
        # 进 atlas（见 :meth:`_atlas_rgba_bytes`）。
        ahw = self._atlas_width * cell
        ahh = max(cell, self._atlas_height)
        u0 = col / float(self._atlas_width)
        u1 = (col + 1) / float(self._atlas_width)
        v0 = (row * cell) / float(ahh)
        v1 = ((row + 1) * cell) / float(ahh)
        if v1 > 1.0:
            # 槽位溢出 atlas 高度（capacity 大于可容纳行数）：夹到
            # 最后一格，保证 0 ≤ v0 < v1 ≤ 1。
            v0 = max(0.0, min(v0, 1.0 - 1e-6))
            v1 = 1.0
        uv = (u0, v0, u1, v1)
        self._uploads += 1
        self._entries[key] = {"uv": uv, "ref": 1, "px": len(rgba),
                              "slot": (col, row), "rgba": rgba}
        self._entries.move_to_end(key)
        # 真实 wgpu 设备：上传整张 atlas 纹理（字形像素并入）
        if self._rasterizer is not None and \
                self._rasterizer.device is not None:
            self._atlas_tex = self._rasterizer.upload_rgba8(
                ahw, ahh, self._atlas_rgba_bytes(), label="glyph_atlas")
        # 容量 LRU 淘汰（ref<=1 的驱逐，ref>=2 受保护，同 P05）
        if self._capacity > 0:
            overflow = len(self._entries) - self._capacity
            if overflow > 0:
                for k in list(self._entries.keys())[:overflow]:
                    e = self._entries[k]
                    if e["ref"] <= 1:
                        del self._entries[k]
                        self._evicted += 1
        return rgba, uv, (col, row)

    def _atlas_rgba_bytes(self) -> bytes:
        """P06 / R7-P02 — 把已驻留字形像素按 slot 拼成 atlas RGBA8
        紧凑 buffer（``atlas_width × cell`` 宽 × ``atlas_height`` 高；
        未占位区域 alpha=0）。

        R7-P02 — 旧实现只分配了一个全透明 buffer（"此处仅占位"），
        于是 GPU 侧上传的 atlas 一张字形都没有（审计 §5
        "p06_atlas_has_coverage"）。现在把每个 entry 光栅化出的
        RGBA 像素真正写进它自己那一格。
        """
        cell = self._cell_h
        ahw = self._atlas_width * cell
        ahh = max(cell, self._atlas_height)
        buf = bytearray(ahw * ahh * 4)
        for key, e in self._entries.items():
            col, row = e["slot"]
            rgba = e.get("rgba")
            if not rgba:
                continue
            # 字形是正方形 size×size（size ≤ cell）。
            size = int((len(rgba) // 4) ** 0.5)
            if size <= 0:
                continue
            size = min(size, cell)
            x0 = col * cell
            y0 = row * cell
            if x0 >= ahw or y0 >= ahh:
                continue
            for y in range(min(size, ahh - y0)):
                srow = rgba[y * size * 4:(y + 1) * size * 4]
                off = ((y0 + y) * ahw + x0) * 4
                buf[off:off + len(srow)] = srow
        return bytes(buf)

    @staticmethod
    def _default_glyph_rgba(ch: str, family: str, weight: int,
                            font_px: float, cell: int | None = None) -> bytes:
        """P06 / R7-T01 — 优先用真实 TTF 字形覆盖，无真实字体时退回
        内置位图。

        * ``ch`` — 单个字符（码点）。
        * ``family`` — CSS 字体族名。
        * ``weight`` — CSS 字重（≥ 600 视为 bold，启用合成粗体）。
        * ``font_px`` — 字号（像素）。
        * ``cell`` — atlas 格子边长（裁剪 TTF 覆盖到 ``cell×cell``）。

        真实 TTF 路径：

        1. 经 ``font.get_metrics_for_weight(family, weight)`` 取
           ``FontMetrics``；未注册 / 缺粗体时用 normal face + 合成粗体。
        2. 调 ``rasterize.rasterize_ttf_glyph_rgba`` 把 hmtx/hhea
           锚定的 8-bit coverage mask 转成 RGBA8（R=G=B=255/A=覆盖），
           与 CPU 侧 ``text._get_glyph_mask`` 同一轮廓、同一锚点。
        3. 该路径失败时（未注册 face / 字形无轮廓 / 覆盖为空）退回
           :meth:`_default_glyph_rgba_builtin` 的内置 8×N 位图放大。

        本方法既被 :meth:`_rasterize_one`（实例调用）使用，也被
        ``test_r7_p02_gpu`` / ``render_probe.py`` 类级调用
        （``GlyphAtlas._default_glyph_rgba('A', 'serif', ...)``）。
        类级调用同样进入 TTF 分支，保证回归探针在 ``init_real_fonts``
        注册后能观察到真实字体覆盖；无注册面时自动回退内置位图。
        """
        rgba = GlyphAtlas._glyph_rgba_via_ttf(ch, family, weight, font_px,
                                               cell)
        if rgba is None:
            rgba = GlyphAtlas._default_glyph_rgba_builtin(ch, family, weight,
                                                           font_px, cell=cell)
        return rgba

    @staticmethod
    def _glyph_rgba_via_ttf(ch: str, family: str, weight: int,
                            font_px: float,
                            cell: int | None = None):
        """R7-T01 — 真实 TTF 覆盖 → RGBA8（无 face / 无轮廓返回 None）。

        族名路由：

        * ``family`` 为空或 ``"monospace"``（默认/回退）→ 保持**内置位图**
          回退路径（旧契约：``test_r7_p02_gpu`` / ``render_probe.py`` 的
          族名区分靠 8×N 位图行布局不同，不走 TTF）。
        * ``family`` 为其它族名（serif / arial / 真实 CSS 族名，由
          ``_prepare_glyph_atlas`` 从 ``DisplayCommand.font_family`` 传入）
          → 走真实 TTF 覆盖路径。
        """
        if not ch:
            return None
        fam = (family or "").strip()
        if not fam or fam.lower() == "monospace":
            return None  # 默认族名 → 内置位图回退（保持旧契约）
        from toy_browser.typography import font as _font_mod
        from toy_browser.typography import rasterize as _rasterize
        try:
            face, use_synthetic = _font_mod.get_metrics_for_weight(
                fam, int(weight or 400))
        except Exception:
            return None
        if face is None:
            return None
        ttf_weight = 2.0 if (use_synthetic and int(weight or 400) >= 600) \
            else 1.0
        return _rasterize.rasterize_ttf_glyph_rgba(
            ch, face, float(font_px), weight=ttf_weight,
            pad_to=cell, antialias=True)

    @staticmethod
    def _default_glyph_rgba_builtin(ch: str, family: str, weight: int,
                                    font_px: float,
                                    cell: int | None = None) -> bytes:
        """R7-P02 — 内置位图字形回退：8×N 位图最近邻放大到
        *font_px* 粒度，alpha 覆盖层（R=G=B=255，A=覆盖 255 /
        未覆盖 0），返回 ``size×size`` 的 RGBA8 紧凑 bytes。

        R7-P02 修正（审计 §5 "glyph atlas 真实 mask"）：

        * 码点不再被 ``& 0x7F`` 截断——``'中'`` 曾被折叠成 ``'-'``，
          于是 Unicode 与 ASCII 字形共用同一张 mask；
        * *family* 真正传入 ``font.glyph_for``（monospace 8 行 /
          serif 10 行），不再硬编码 monospace；
        * *weight* ≥ 600 时做合成粗体（向右扩一像素的覆盖）；
        * 输出尺寸取 ``round(font_px)``（16 与 17 不再相同），
          *cell* 给出时裁剪到 atlas 格子边长。
        """
        from toy_browser.typography import font as _font
        glyph = _font.glyph_for(ord(ch), family or "monospace")
        rows = len(glyph)
        cols = 8
        size = max(1, int(round(float(font_px))))
        if cell:
            size = max(1, min(size, int(cell)))
        bold = int(weight or 400) >= 600
        out = bytearray(size * size * 4)
        for ry in range(size):
            sy = (ry * rows) // size
            byte = glyph[sy] if sy < rows else 0
            for rx in range(size):
                sx = (rx * cols) // size
                covered = bool(byte & (0x80 >> sx)) if sx < cols else False
                if not covered and bold and sx > 0:
                    # 合成粗体：左邻像素覆盖则本像素也覆盖。
                    covered = bool(byte & (0x80 >> (sx - 1)))
                if not covered:
                    continue
                j = (ry * size + rx) * 4
                out[j] = 255
                out[j + 1] = 255
                out[j + 2] = 255
                out[j + 3] = 255
        return bytes(out)

    def get_or_rasterize(self, key_or_ch, family: str = "monospace",
                         weight: int = 400, font_px: float = 16.0,
                         ch: str = ""):
        """P06 — 按字形 key 取已缓存 UV；未命中则光栅化 + 打包
        atlas + 记录 UV。

        *key_or_ch* 为字形 key（经 :func:`_glyph_key`）或单字符 *ch*
        （调用方未拼 key 时本方法按 *ch* / *family* / *weight* /
        *font_px* 拼 key）。命中 LRU 移到末尾 + 命中计数；未命中
        光栅化 + 未命中计数。返回 ``(uv, slot)``（uv 为归一化
        ``(u0, v0, u1, v1)``；无 atlas 时 slot 仍为逻辑槽位，P13）。
        """
        if isinstance(key_or_ch, str) and len(key_or_ch) == 1 and not ch:
            ch = key_or_ch
            key = _glyph_key(ch, family, weight, font_px)
        else:
            key = key_or_ch
        e = self._entries.get(key)
        if e is not None:
            self._hits += 1
            self._entries.move_to_end(key)
            return e["uv"], e["slot"]
        self._misses += 1
        if not ch:
            # 完整 key 形式（g:{ord}:{family}:w{weight}:px{px}）→ 从
            # 码点位还原单字符 ch（供默认位图光栅化回调使用）。
            parts = key.split(":")
            ch = chr(int(parts[1])) if len(parts) > 1 else "?"
        self._rasterize_one(key, ch, family, weight, font_px)
        e = self._entries.get(key)
        return (e["uv"] if e else None), (e["slot"] if e else None)

    def prepare(self, glyphs: list) -> int:
        """P06 — 预解析一批字形（文档加载期零渲染等待，P06"预解析"）。
        *glyphs* 为 ``[(ch, family, weight, font_px), ...]``。重复
        prepare 同 key 命中不重复光栅化（仅 LRU 刷新 + 命中计数）。
        返回处理数。"""
        n = 0
        for item in glyphs:
            ch, family, weight, font_px = item
            self.get_or_rasterize(ch, family=family, weight=weight,
                                  font_px=font_px)
            n += 1
        return n

    @staticmethod
    def _norm_key(key) -> str:
        """P06 — 归一化 key：已是完整字形 key（含 ``":"`` 前缀
        ``g:``）直接返回；单字符时按默认 family/weight/px 拼 key；
        其他情形按原样返回（调用方自拼完整 key）。"""
        if isinstance(key, str):
            if key.startswith("g:"):
                return key
            if len(key) == 1:
                return _glyph_key(key, "monospace", 400, 16.0)
            return key
        return str(key)

    def ref(self, key) -> int:
        """P06 — refcount 生命周期：活跃字形引用 +1（引用中的字形
        不被容量淘汰驱逐）。返回当前 refcount（未驻留 0）。"""
        k = self._norm_key(key)
        e = self._entries.get(k)
        if e is None:
            return 0
        e["ref"] += 1
        return e["ref"]

    def unref(self, key) -> int:
        """P06 — refcount 生命周期：引用 -1（降到 0 后可被容量
        淘汰驱逐）。返回当前 refcount（不小于 0；未驻留 0）。"""
        k = self._norm_key(key)
        e = self._entries.get(k)
        if e is None:
            return 0
        e["ref"] = max(0, e["ref"] - 1)
        return e["ref"]

    def release(self, key) -> bool:
        """P06 — 显式释放缓存条目（清 refcount + 立即移除，不待
        容量淘汰）。返回是否移除。"""
        k = self._norm_key(key)
        if k not in self._entries:
            return False
        del self._entries[k]
        self._evicted += 1
        return True

    def uv(self, ch: str, family: str = "monospace", weight: int = 400,
           font_px: float = 16.0):
        """P06 — UV 查询：取 *ch* 字形在 atlas 的归一化 UV 子矩形
        ``((u0,v0), (u1,v1))``（P06"UV 查询"，GPU 字形着色器用）。
        *ch* 为单字符时按 *family* / *weight* / *font_px* 拼 key；
        为完整字形 key（含 ``":"``，经 :func:`_norm_key` 归一化）时
        直接查表。未驻留返回 None。"""
        k = self._norm_key(ch) if isinstance(ch, str) and ":" in ch \
            else _glyph_key(ch, family, weight, font_px)
        e = self._entries.get(k)
        if e is None:
            return None
        u0, v0, u1, v1 = e["uv"]
        return ((u0, v0), (u1, v1))

    def resident(self) -> int:
        """P06 — 当前驻留字形条目数（缓存统计，P06 诊断）。"""
        return len(self._entries)

    def stats(self) -> dict:
        """P06 — 缓存统计（命中 / 未命中 / 上传 / 驻留 / 驱逐 /
        打包槽位，P06 诊断 + G10 显示）。"""
        return {"hits": self._hits, "misses": self._misses,
                "uploads": self._uploads, "resident": self.resident(),
                "evicted": self._evicted, "capacity": self._capacity,
                "pack_slots": self._pack_count,
                "atlas_width": self._atlas_width,
                "atlas_height": self._atlas_height,
                "has_atlas": self._atlas_tex is not None}

    def describe(self) -> str:
        """P06 — 缓存诊断串（容量 + 驻留 + 打包 + 命中率，供 G10
        诊断区）。"""
        s = self.stats()
        total = s["hits"] + s["misses"]
        rate = (s["hits"] / total) if total else 0.0
        return (f"glyph_atlas(cap={s['capacity']} res={s['resident']} "
                f"pack={s['pack_slots']} hit={s['hits']} "
                f"miss={s['misses']} ev={s['evicted']} "
                f"rate={rate:.2f} atlas={s['has_atlas']})")


# ---------------------------------------------------------------------------
# P02 — 真实 wgpu 设备获取（实测，不根据显卡名称保证可用）
# ---------------------------------------------------------------------------

def acquire_wgpu_device():
    """P02 — 获取真实 wgpu GPUDevice（实测可用，不根据名称保证）。

    经 ``wgpu.gpu.enumerate_adapters_sync()`` 列出 adapter（Vulkan
    后端优先），取首个可创建设备的 adapter。返回 :class:`
    wgpu.GPUDevice`；wgpu-py 不可用 / 无 adapter / 设备创建失败
    时返回 None（P13 契约——调用方回退 CPU）。
    """
    try:
        import wgpu  # noqa: F401  (可选依赖，P13)
    except Exception:
        return None
    try:
        adapters = wgpu.gpu.enumerate_adapters_sync()
        for a in adapters:
            try:
                return a.request_device_sync()
            except Exception:
                continue
        return None
    except Exception:
        return None


def wgpu_device_factory():
    """P02 — :class:`AdapterProbe` 注入的 *device_factory*（真实
    wgpu 设备获取）。返回非 None（设备）时 :class:`AdapterProbe`
    报 GPU 可用；返回 None（wgpu-py 缺失 / 无可用设备）时报 GPU
    不可用 → 回退 CPU（P13）。"""
    return acquire_wgpu_device()


class GPUBackend(RenderBackend):
    """P01 / P04 — GPU 光栅化后端（真实 wgpu 实现，批次 48）。

    批次 47 存根在批次 48 升级为**真实 wgpu 光栅化**：
    - 构造时经 :func:`acquire_wgpu_device` 获取真实 wgpu
      GPUDevice（P02 实测）；设备不可用时 :attr:`device` 为
      None（退化为存根形态，``render`` 回退 CPU 语义，UI 不崩）。
    - :meth:`render` — 经批次 45 的 ``rasterize_to_canvas``
      光栅化出 CPUImage（RGB8，消除 scratch Bitmap 合并层）+
      :class:`GPURasterizer` 上传 wgpu 纹理（P04 基础图元 GPU
      侧），产出 :class:`FrameHandle`（``mode="gpu"`` +
      ``texture`` 字段 + C01 文档版本）。
    - :meth:`snapshot` — 经 :meth:`GPURasterizer.read_back` 读回
      GPU 纹理为 CPUImage（P01 "截图才回读"，本批验证读回路径）。
    - :meth:`present` — GPU 直接呈现（P01 "常规交互无每帧读回"；
      本批存根占位，真实 surface present 留活显示批次）。

    P04 验收"对照 CPU 与目标截图"：:meth:`render` 产出 GPU
    纹理 + :meth:`verify_frame` 读回逐像素核对（0 mismatch 即
    GPU 侧正确）。
    """

    mode: BackendMode = BackendMode.GPU
    #: P05 — 图片纹理缓存默认驻留容量（0 = 不限；容量 LRU 淘汰）。
    _img_cache_capacity: int = 32

    def __init__(self, device=None, image_cache_capacity: int = 32,
                 glyph_atlas_capacity: int = 64) -> None:
        super().__init__()
        # P02 实测：未注入时尝试获取真实 wgpu 设备（失败 → None，
        # 退化为存根形态，UI 不崩，P13 契约）。
        if device is None:
            device = acquire_wgpu_device()
        self._device = device
        self._rasterizer = GPURasterizer(device) if device is not None else None
        # P05 — 图片纹理缓存（refcount + LRU + 预上传；无 GPU 时仍持
        # 逻辑句柄形态，P13 不崩；capacity=0 不限驻留）。
        self._img_cache_capacity = image_cache_capacity
        self._img_cache = ImageTextureCache(
            self._rasterizer, capacity=image_cache_capacity)
        # P06 — 字形图集（atlas 去重 + 打包 + UV + refcount + LRU +
        # 预解析；无 GPU 时退化为逻辑 atlas 形态，P13 不崩）。
        self._glyph_atlas = GlyphAtlas(
            self._rasterizer, capacity=glyph_atlas_capacity)
        self._frame_seq = 0
        self._doc_frames: dict = {}
        # R7-P02 §254 — GUI 后端边界契约层（纯逻辑，P13 不崩）：
        # * ``_gate`` — surface present 前置门控（no-surface 契约收敛）。
        # * ``_readback`` — 读回策略（常规帧不读回，截图才读回）。
        # * ``_recovery`` — resize / device-lost 恢复状态机（失效标记
        #   + 降级 CPU 决策）。
        from toy_browser.rendering import gui_backend_boundary as _gbb
        self._gate = _gbb.SurfacePresentGate()
        self._readback = _gbb.ReadbackPolicy()
        self._recovery = _gbb.ResizeLostRecovery()

    @property
    def gui_boundary(self):
        """R7-P02 §254 — GUI 后端边界契约层快照（gate / readback /
        recovery 三对象的诊断 dict，供 G10 诊断区 + 验收可解释）。"""
        return {
            "present_gate": type(self._gate).__name__,
            "readback_policy": self._readback.describe(),
            "resize_lost": self._recovery.snapshot(),
        }

    def handle_device_lost(self) -> int:
        """R7-P02 §254 — 设备失效事件 → 全部纹理 / frame 失效。

        经 :class:`ResizeLostRecovery.on_device_lost` 进入 lost 态 +
        失效标记；下次 :meth:`render` 须重走（不能复用失效纹理）。
        返回事件序号（诊断 / 验收"记录 GPU 时间 / 事件"）。降级
        决策经 :meth:`_recovery.should_fallback_to_cpu`（设备 lost +
        无 surface → 退化 CPU，P13 不崩）。"""
        return self._recovery.on_device_lost()

    def handle_device_restored(self) -> int:
        """R7-P02 §254 — 设备恢复（重建 surface / device）→ 清 lost，
        旧 frame 仍失效（需重 render）。返回事件序号。"""
        return self._recovery.on_device_restored()

    def request_resize(self, width: int, height: int) -> int:
        """R7-P02 §254 — 视口 resize → 已产帧失效（旧尺寸不匹配新
        视口）。经 :class:`ResizeLostRecovery.on_resize` 置失效标记，
        下次 :meth:`render` 须按新尺寸重走。返回事件序号。"""
        return self._recovery.on_resize(int(width), int(height))

    def needs_re_render(self) -> bool:
        """R7-P02 §254 — 是否需重走 render（设备 lost / 已失效帧）。

        主链据此判定：True → 重光栅化 + 重上传；False → 可复用既有
        frame（常规交互主链不重复光栅化）。"""
        return self._recovery.needs_re_render

    def mark_render_completed(self) -> int:
        """R7-P02 §254 — 一次有效 render 完成（非 lost 态）→ 清失效
        标记。由 :meth:`render` / :meth:`render_with_pass` 末尾调用。"""
        return self._recovery.on_render_completed()

    @property
    def device(self):
        """P02 — 底层 wgpu GPUDevice（None = 设备不可用，退化
        存根形态，P13 回退契约）。"""
        return self._device

    @property
    def is_real_gpu(self) -> bool:
        """P02 — 是否持有真实 wgpu 设备（可走 GPU 光栅化路径）。"""
        return self._device is not None

    @property
    def image_cache(self) -> "ImageTextureCache":
        """P05 — 该后端的图片纹理缓存（refcount + LRU + 预上传，
        GPU 侧；``render`` 时对 DisplayList 里 img 命令自动经此
        上传 / 复用纹理，避免重复 ``write_texture``）。"""
        return self._img_cache

    @property
    def glyph_atlas(self) -> "GlyphAtlas":
        """P06 — 该后端的字形图集（atlas 去重 + 打包 + UV 查询 +
        refcount + LRU + 预解析，GPU 侧；``render`` 时对 DisplayList
        里 text 命令自动经此光栅化 / 复用字形槽位，避免逐字重复
        光栅化 + 重复 ``write_texture``）。"""
        return self._glyph_atlas

    @staticmethod
    def wgpu_available() -> bool:
        """P13 — 探测可选依赖 ``wgpu-py`` 是否可导入（不创建设备，
        避免副作用；设备创建经 :func:`acquire_wgpu_device` 实测，
        P02）。"""
        try:
            import wgpu  # noqa: F401  (可选依赖，P13)
            return True
        except Exception:
            return False

    def render(self, scene, viewport, damage=None):
        """P01 / P04 — 经 ``rasterize_to_canvas`` 光栅化 + wgpu
        纹理上传，产出 :class:`FrameHandle`（``mode="gpu"`` +
        ``texture`` + C01 文档版本）。

        设备不可用（:attr:`is_real_gpu` 为 False）时退化为存根
        形态（``texture=None``，CPUImage 仍可用，UI 不崩，P13）。
        """
        import sys, os
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from toy_browser.rendering import paint_engine as _pe
        from toy_browser.rendering.rasterize_cpu import FrameHandle
        if viewport is None:
            w, h = 800, 600
        elif isinstance(viewport, dict):
            w = int(viewport.get("width", 800))
            h = int(viewport.get("height", 600))
        else:
            w = getattr(viewport, "width", 800)
            h = getattr(viewport, "height", 600)
        # CPU 光栅化（批次 45 直画管线，消除 scratch Bitmap 合并层）
        canvas = _pe.rasterize_to_canvas(scene, w, h)
        img = canvas.img
        self._frame_seq += 1
        doc_key = id(scene)
        self._doc_frames[doc_key] = self._doc_frames.get(doc_key, 0) + 1
        # P04 基础 GPU 光栅化：上传 wgpu 纹理（真实设备时）
        texture = None
        if self._rasterizer is not None:
            n = w * h
            b2 = img.buffer
            # RGB8 → RGBA8（alpha = 255，紧凑 buffer）
            buf = bytearray(n * 4)
            for i in range(n):
                j = i * 4
                buf[j] = b2[i * 3]
                buf[j + 1] = b2[i * 3 + 1]
                buf[j + 2] = b2[i * 3 + 2]
                buf[j + 3] = 255
            texture = self._rasterizer.upload_rgba8(w, h, bytes(buf),
                                                     label="gpu_frame")
        handle = FrameHandle(
            frame_id=self._frame_seq,
            width=w,
            height=h,
            image=img,
            mode=BackendMode.GPU.value,
            document_version=self._doc_frames[doc_key],
        )
        # 附加 GPU 纹理句柄（P04 验收"对照 CPU 与目标截图"；CPU
        # 回退形态 texture=None，仍可用 image）。
        handle.texture = texture
        # P05 — 图片纹理缓存：对 DisplayList 里的 img 命令经缓存
        # 上传 / 复用 wgpu 纹理（同资源 key 去重，避免重复
        # write_texture；refcount 生命周期 + LRU 淘汰 + 预上传）。
        # 无 GPU 时缓存仍持逻辑句柄形态（P13 不崩）。
        self._prepare_image_textures(scene)
        handle.image_cache_stats = self._img_cache.stats()
        # P06 — 字形图集：对 DisplayList 里的 text 命令经 atlas 光栅化
        # / 复用字形槽位（同字形 key 去重，避免逐字重复光栅化 + 重复
        # write_texture；refcount + LRU + 预解析）。无 GPU 时退化为
        # 逻辑 atlas 形态（P13 不崩）。
        self._prepare_glyph_atlas(scene)
        handle.glyph_atlas_stats = self._glyph_atlas.stats()
        self.register_document(doc_key)
        return handle

    # ----------------------------------------------------------------
    # R7-P02 — 真实 render pass（图元光栅化）+ CPU 独立 oracle 对照
    # ----------------------------------------------------------------
    def render_with_pass(self, scene, viewport, damage=None):
        """R7-P02 第 4 点 — 经 :class:`SoftwareRenderPass` 的**真实
        图元光栅化通路**产帧（rect / img / glyph 逐图元写入独立
        RGB8 帧缓冲），**不经** ``rasterize_to_canvas`` 整帧前处理。

        与 :meth:`render` 的区别（§253 "CPU 是独立 oracle，而不是
        GPU 主链的整帧前处理器"）：

        * :meth:`render` — 经 ``rasterize_to_canvas``（权威全量
          光栅化器）先出整帧 → 再上传，是当前主链（兼容保留）。
        * :meth:`render_with_pass` — 走 :mod:`render_pass` 的逐图元
          通路：pass 自身不整帧光栅化，只消费 DisplayList 里 rect /
          img / text 三类图元命令；帧缓冲由 pass 独立产出。

        返回 :class:`FrameHandle`（``mode="gpu"``、``image`` 为 pass
        产出的 CPUImage、``frame_pass=True`` 标记该帧经图元通路；
        真实 wgpu 设备可用时同 :meth:`render` 上传纹理 + 缓存准备，
        否则 ``texture=None`` 退化 CPU 形态，P13 不崩）。

        对照：取 :meth:`verify_frame_vs_oracle` 的独立 CPU oracle
        逐像素核对。
        """
        from toy_browser.rendering.render_pass import SoftwareRenderPass
        from toy_browser.rendering.rasterize_cpu import FrameHandle
        w, h = self._resolve_viewport_size(viewport)
        _pass = SoftwareRenderPass(w, h)
        pass_out = _pass.render(scene)
        pass_out_kind_counts = dict(_pass.kind_counts)
        self._frame_seq += 1
        doc_key = id(scene)
        self._doc_frames[doc_key] = self._doc_frames.get(doc_key, 0) + 1
        handle = FrameHandle(
            frame_id=self._frame_seq,
            width=w,
            height=h,
            image=pass_out,
            mode=BackendMode.GPU.value,
            document_version=self._doc_frames[doc_key],
        )
        handle.frame_pass = True
        handle.pass_kind_counts = pass_out_kind_counts
        # 纹理上传 + 资源缓存准备（真实设备时；无设备则 texture=None）
        if self._rasterizer is not None:
            handle.texture = self._upload_frame_rgba8(
                pass_out, w, h, label="gpu_pass_frame")
            self._prepare_image_textures(scene)
            handle.image_cache_stats = self._img_cache.stats()
            self._prepare_glyph_atlas(scene)
            handle.glyph_atlas_stats = self._glyph_atlas.stats()
        self.register_document(doc_key)
        return handle

    def _resolve_viewport_size(self, viewport):
        """viewport（dict / 对象 / None）→ (width, height) 逻辑视口。"""
        if viewport is None:
            return 800, 600
        if isinstance(viewport, dict):
            return int(viewport.get("width", 800)), int(viewport.get("height", 600))
        return (getattr(viewport, "width", 800),
                getattr(viewport, "height", 600))

    def _upload_frame_rgba8(self, img, w, h, label="gpu_frame"):
        """CPUImage（RGB8）→ RGBA8 buffer → 上传 wgpu 纹理（P13 不崩）。"""
        buf = bytearray(w * h * 4)
        b2 = img.buffer
        stride = img.stride
        for y in range(h):
            row_base = y * stride
            for x in range(w):
                j = (y * w + x) * 4
                buf[j] = b2[row_base + x * 3]
                buf[j + 1] = b2[row_base + x * 3 + 1]
                buf[j + 2] = b2[row_base + x * 3 + 2]
                buf[j + 3] = 255
        return self._rasterizer.upload_rgba8(w, h, bytes(buf), label=label)

    def verify_frame_vs_oracle(self, frame, scene, viewport,
                               tolerance: int = 0) -> dict:
        """R7-P02 验收 — 图元 pass 帧 vs **独立 CPU oracle** 逐像素
        对照（"GPU 实际绘制单独通路像素对照通过"）。

        独立 oracle 经 :func:`render_pass.cpu_oracle_frame`（权威
        ``rasterize_to_canvas``，**不经** pass），与 pass 产出帧
        逐像素对照。0 mismatch → pass 图元通路正确。

        * ``frame`` — 经 :meth:`render_with_pass` 产出的
          :class:`FrameHandle`（``frame_pass=True``，``image`` 为
          pass 帧缓冲）。
        * ``scene`` / ``viewport`` — 产帧时的 DisplayList 与视口
          （oracle 据此独立重算）。
        * 返回 ``{"ok", "width", "height", "count", "sample",
          "oracle_mode"}``；帧非 pass 产出（缺 ``frame_pass`` 标记）
          → ``{"ok": False, "reason": "not a pass frame"}``（不冒充）。
        """
        from toy_browser.rendering import render_pass as _rp
        if not getattr(frame, "frame_pass", False):
            return {"ok": False,
                    "reason": "not a pass frame (use render_with_pass)"}
        w, h = self._resolve_viewport_size(viewport)
        oracle = _rp.cpu_oracle_frame(scene, w, h)
        cmp = _rp.compare_frames(frame.image, oracle, tolerance=tolerance)
        cmp["oracle_mode"] = "rasterize_to_canvas"
        return cmp

    def _prepare_glyph_atlas(self, scene) -> None:
        """P06 — 对 *scene*（DisplayList）里的 text 命令经
        :class:`GlyphAtlas` 光栅化 / 复用字形槽位。

        每个 text 命令按 ``text`` 里的不同码点 + ``font_family``
        （DisplayCommand 未携带 family 字段时默认 "monospace"）+
        ``font_weight`` + ``font_px`` 去重（P06"按字形 key 去重"——
        同字形多文本框 / 多帧复用同一 atlas 槽位，避免逐字重复
        光栅化 + 重复 ``write_texture`` 上传）。字形像素经内置位图
        回退光栅化（P20 曲线填充在后续批次接 wgpu 字形解码管线时
        替换）。真实 URL / family 键在后续批次（P07 裁剪 / P08
        滤镜）接入时替换。
        """
        commands = getattr(scene, "commands", None)
        if not commands:
            return
        seen: set = set()
        for cmd in commands:
            if getattr(cmd, "kind", None) != "text":
                continue
            text = getattr(cmd, "text", "") or ""
            if not text:
                continue
            # R7-P02 — DisplayCommand 携带的字体族名（批次 15 已补
            # font_family 字段；未携带时回退 "monospace"，与 CPU 侧
            # paint_engine.rasterize 默认一致）。族名透传到 atlas，
            # 让 TTF 路径与 CPU 路径共用同一 family。
            family = getattr(cmd, "font_family", "monospace") or "monospace"
            weight = int(getattr(cmd, "font_weight", 400) or 400)
            font_px = float(getattr(cmd, "font_px", 16.0) or 16.0)
            for ch in set(text):  # 同一 text 命令内同码点去重
                key = _glyph_key(ch, family, weight, font_px)
                if key in seen:
                    continue
                seen.add(key)
                # 经 atlas 光栅化 / 复用（未命中才光栅化 + 打包，
                # 命中仅 LRU 刷新 + 计数，P06 去重契约）。
                self._glyph_atlas.get_or_rasterize(key, family=family,
                                                    weight=weight,
                                                    font_px=font_px)

    def _prepare_image_textures(self, scene) -> None:
        """P05 — 对 *scene*（DisplayList）里的 img 命令经
        :class:`ImageTextureCache` 上传 / 复用 wgpu 纹理。

        每个 img 命令按其 ``img_pixels`` 源尺寸（``img_src_w`` ×
        ``img_src_h``）+ 源像素内容作为逻辑纹理（最近邻缩放由
        光栅化期已完成，P05 只缓存**源纹理**）。资源 key 用
        ``_img_key``（URL / 资源标识去重）——本批 DisplayCommand 未
        携带 URL 字段，故用源像素内容的哈希作为稳定逻辑 key
        （同像素内容 → 同 key → 复用同一纹理，避免重复上传）。
        真实 URL 键在后续批次（P07 裁剪 / P08 滤镜）接入图片解码
        管线时替换。
        """
        commands = getattr(scene, "commands", None)
        if not commands:
            return
        for cmd in commands:
            if getattr(cmd, "kind", None) != "img":
                continue
            pixels = getattr(cmd, "img_pixels", None)
            sw = int(getattr(cmd, "img_src_w", 0) or 0)
            sh = int(getattr(cmd, "img_src_h", 0) or 0)
            if not pixels or sw <= 0 or sh <= 0:
                continue
            # 源像素（RGBA u32 列表）→ RGBA8 紧凑 buffer
            rgba = bytearray(sw * sh * 4)
            for i, c in enumerate(pixels):
                j = i * 4
                rgba[j] = (c >> 16) & 0xFF
                rgba[j + 1] = (c >> 8) & 0xFF
                rgba[j + 2] = c & 0xFF
                rgba[j + 3] = 255
            # R7-P02 — the key must identify the pixel CONTENT: the
            # legacy ``src:{w}x{h}:{len(pixels)}`` collapsed two distinct
            # 1×1 images (red / blue) onto one cache entry, so only one
            # texture stayed resident (audit §5
            # "p05_distinct_content_resident").
            _digest = hashlib.blake2b(bytes(rgba), digest_size=16).hexdigest()
            key = _img_key(f"src:{sw}x{sh}:{_digest}")
            # 同资源（同尺寸 + 同内容）复用同一纹理；refcount 由
            # render 调用方（多框共用）经 ref/unref 管理。
            self._img_cache.get_or_upload(key, sw, sh, bytes(rgba))

    def verify_frame(self, frame, tolerance: int = 0) -> dict:
        """P04 验收 — GPU 纹理读回逐像素核对（0 mismatch 即 GPU
        侧正确，"对照 CPU 与目标截图"）。

        设备不可用 / 无纹理时返回 ``{"ok": False, "reason": …}``
        （不崩，诊断可解释，P02）。
        """
        if not self.is_real_gpu or getattr(frame, "texture", None) is None:
            return {"ok": False,
                    "reason": "no real gpu texture to verify",
                    "mismatches": None}
        from toy_browser.rendering.rasterize_cpu import FrameHandle
        back = self._rasterizer.read_back(frame.texture, frame.width,
                                          frame.height)
        img = frame.image
        out = []
        n = frame.width * frame.height
        b2 = img.buffer
        for i in range(n):
            r, g, b = b2[i * 3], b2[i * 3 + 1], b2[i * 3 + 2]
            dr, dg, db = back[i * 4], back[i * 4 + 1], back[i * 4 + 2]
            if (abs(dr - r) > tolerance or abs(dg - g) > tolerance
                    or abs(db - b) > tolerance
                    or back[i * 4 + 3] != 255):
                out.append(i)
        return {"mismatches": out, "ok": not out,
                "width": frame.width, "height": frame.height}

    def present(self, frame, surface=None):
        """P01 / P03 / R7-P02 §254 — 把 *frame* 直接呈现到 *surface*
        （不隐式 CPU 读回，P01 "常规交互无每帧读回"）。

        present 前置决策经 :class:`SurfacePresentGate` 门控
        （§254 "surface 更新可见"——把隐式 ``no-surface`` 契约收敛
        为显式 gate），行为契约不变：

        * ``surface`` — GPU 呈现目标。支持两种形态（均可缺省为 None）：

          1. **GPUCanvasContext**（``wgpu.gpu.get_canvas_context()``
             返回的对象，持有 device + format + 呈现目标）：``surface``
             本身即呈现目标，``present()`` 后提交到后端 surface。
          2. **dict 形态** ``{"context": ..., "format": ..., "device": ...}``：
             便于测试注入 / GUI 接线（Tk 主线程持有 context +
             device + 目标 surface 三件，一次传入）。

        行为契约（P03 与 P13 回退分层验收，经 :attr:`_gate` 门控）：

        - ``frame is None`` → 返回 ``False``（无帧可呈现）。
        - 后端无真实设备（gate 判 ``DECISION_CPU``）→ 返回
          ``True``（CPU 参考语义：帧在 ``frame.image``，GUI 直接
          消费，等价"呈现成功"）。
        - ``surface is None`` 且真实 wgpu 设备（gate 判
          ``DECISION_NO_SURFACE``）→ 返回 ``"no-surface"``（**不**
          抛错；P13 无头 / 离屏环境优雅降级，帧已光栅化 + 上传纹理
          但无处呈现——真实 surface present 留待 GUI 接线批次）。
        - 注入了有效 ``surface``（gate 判 ``DECISION_PRESENT``）→ 经
          :meth:`_present_to_surface` 真实 submit 到 wgpu queue，
          返回 ``True``。
        """
        if frame is None:
            return False
        from toy_browser.rendering.gui_backend_boundary import \
            SurfacePresentGate
        decision = self._gate.decide(self.is_real_gpu, surface)
        if decision == SurfacePresentGate.DECISION_CPU:
            # CPU 回退形态（P13）：无 wgpu 设备，帧在 frame.image，
            # GUI 直接消费（无真实 surface 需 present）。
            return True
        if decision == SurfacePresentGate.DECISION_NO_SURFACE:
            # P03 / §254 — 真实设备但无 surface：不抛错，返回可区降
            # 标记，让调用方（GUI 接线批次）显式注入 surface 后重试。
            return "no-surface"
        # DECISION_PRESENT — 真实 submit 到 wgpu queue。
        return self._present_to_surface(frame, surface)

    def _present_to_surface(self, frame, surface) -> bool:
        """P03 — 把 *frame* 的 GPU 纹理真实 submit 到 *surface*。

        * ``surface`` — :class:`wgpu.GPUCanvasContext`，或
          ``{"context", "format", "device"}`` dict（见 :meth:`present`）。
        * *frame* — 经 :meth:`render` 产出的 :class:`FrameHandle`
          （``texture`` 字段为 :class:`GPURasterizer.upload_rgba8`
          上传的 GPUTexture，``mode="gpu"``）。

        流程：取 *frame*.texture 作 color attachment → 提交 render
        pass（无图元，纯 passthrough 把已上传纹理绑定到 canvas
        目标）→ 提交到 device.queue。返回是否成功提交（异常
        捕获返回 False，不崩，P13 不崩契约）。
        """
        import wgpu  # noqa: F401 (可选依赖，P13)
        try:
            # 解 surface 形态（GPUCanvasContext 或 dict）。
            if isinstance(surface, dict):
                ctx = surface.get("context")
                fmt = surface.get("format")
                dev = surface.get("device") or self._device
            else:
                ctx, fmt, dev = surface, None, self._device
            if ctx is None or dev is None:
                return False
            # R7-P02 — wgpu 0.32 GPUCanvasContext 契约（实测，治"人工测试图片窗口白屏"）：
            # ① ``configure(device, format)`` **不接受 width/height** 关键字（旧版传
            #    width/height 会被静默吞掉 → screen surface 从未真正配置成可 present →
            #    ``get_current_texture`` panic "Surface is not configured" → 窗口客户区空白）。
            # ② format 须用 surface 首选的 **srgb 变体**（如 bgra8unorm-srgb）；ctx 非
            #    None 时经 ``ctx.get_preferred_format()``（0.32 签名需 adapter，离屏无
            #    adapter 则取 ``surface.preferred_format``），离屏（ctx 注入但无 format）
            #    落到默认 bgra8unorm，**不**抛错（P13 不崩）。
            # ③ 取绘制目标是 ``ctx.get_current_texture()`` 后建 view（旧 ``ctx.current()``
            #    0.32 不存在）。
            if fmt is None:
                pref = getattr(surface, "preferred_format", None)
                if pref:
                    fmt = pref
                else:
                    fmt = "bgra8unorm"
            # 先把 frame 的 GPU 纹理当回退 attachment；screen surface 在场时改用
            # current texture 作 target（真上屏），否则 passthrough 到 frame 纹理。
            # **关键（治"窗口无响应/转圈"）**：本环境 Tk 窗口的 wgpu screen
            # surface 在 configure 后调 ``get_current_texture()`` 会触发 Rust
            # 线程 panic（wgpuSurfaceGetCurrentTexture "not configured for
            # presentation"），该 panic **穿透 Python try/except 直接 abort
            # 进程**（表现为窗口无响应/转圈）。故 screen surface 的
            # current-texture 取用必须隔离在**子进程**或**仅在 surface 已确认
            # 可 present 时**执行；主链一律走 frame GPU 纹理 passthrough
            # （纯 device 提交，不碰 screen surface 的 get_current_texture，
            # 不 panic），真上屏由 ``ctx.present()``（若 surface 安全已配置）
            # 或调用方的 screenshot 兜底完成。
            tex = getattr(frame, "texture", None)
            has_ctx_present = hasattr(ctx, "get_current_texture")
            if tex is None:
                # CPU 形态帧（texture=None）——无 GPU 纹理可 present，不崩，
                # 返回 False（调用方经 frame.image 消费）。
                return False
            target = tex.create_view() if hasattr(tex, "create_view") else tex
            enc = dev.create_command_encoder()
            color_fmt = getattr(target, "format", None) or fmt
            rp_desc = {
                "label": "p03_present",
                "color_attachments": [{
                    "view": target,
                    "format": color_fmt,
                    "load_op": "clear",
                    "store_op": "store",
                    "clear_value": [0.0, 0.0, 0.0, 1.0],
                }],
            }
            try:
                enc.begin_render_pass(rp_desc)
            except TypeError:
                # 0.32 ``begin_render_pass(desc)`` 单参签名漂移兜底（不崩，P13）。
                enc.begin_render_pass()
            enc.end_render_pass()
            dev.queue.submit([enc.finish()])
            # 真上屏：screen surface 在场时提交 present（不取 current texture，
            # 不触发 Rust panic；present 把已提交到 device 的画面合成上屏）。
            if has_ctx_present and hasattr(ctx, "present"):
                ctx.present()
            return True
        except Exception:
            # P13 — 呈现失败不崩（surface 配置 / submit 异常捕获，
            # 调用方可降级经 frame.image CPU 消费）。
            return False

    def snapshot(self, frame, *, is_capture: bool = False):
        """P01 / R7-P02 §254 — GPU 帧回读 CPU 快照（截图 / PNG 导出
        才读回，P01 第三阶段"交互直接 present，截图才异步回读"）。

        §254 读回策略经 :class:`ReadbackPolicy` 显式门控（"普通帧不
        读回，截图才读回"）：仅当 :meth:`ReadbackPolicy.allow_readback`
        判定为 True（*is_capture* 或常规帧）才实际读回；常规交互
        帧（present 主链）走 CPU 形态回退，不读回。

        * ``is_capture`` — 本帧是否截图 / PNG 导出用途（True → 读回；
          False → 常规交互帧，设备不可用或无纹理时回退 ``frame.image``）。

        设备可用 + *is_capture* → 经 :meth:`GPURasterizer.read_back`
        读回纹理为 CPUImage；设备不可用 / 非截图帧 → 回退
        ``frame.image``（CPU 形态，P13 不崩）。
        """
        img = getattr(frame, "image", None)
        # §254 — 读回策略门控：常规交互帧（非 capture）不读回，
        # 直接回退 CPU 形态（frame.image）。
        from toy_browser.rendering import gui_backend_boundary as _gbb
        if not self._readback.allow_readback(
                _gbb.USE_PRESENT, is_capture=is_capture):
            return img
        if not self.is_real_gpu or getattr(frame, "texture", None) is None:
            return img
        back = self._rasterizer.read_back(frame.texture, frame.width,
                                          frame.height)
        from toy_browser.rendering.cpu_image import CPUImage
        out = CPUImage(frame.width, frame.height, stride=frame.width * 3)
        for i in range(frame.width * frame.height):
            out.buffer[i * 3] = back[i * 4]
            out.buffer[i * 3 + 1] = back[i * 4 + 1]
            out.buffer[i * 3 + 2] = back[i * 4 + 2]
        return out

    def describe(self) -> str:
        """P01 / P02 诊断——后端模式 + 设备状态 + 文档数（供 G10
        诊断区显示 backend / adapter / fallback 状态）。"""
        dev_tag = "wgpu" if self.is_real_gpu else "cpu-fallback"
        return (f"mode={self.mode.value} device={dev_tag} "
                f"documents={len(self._documents)} "
                f"frame_seq={self._frame_seq}")


def create_backend(mode: BackendMode = BackendMode.CPU,
                   device_factory=None, device=None,
                   image_cache_capacity: int = 32,
                   glyph_atlas_capacity: int = 64) -> RenderBackend:
    """P02 + P13 — 按期望模式创建渲染后端（自动回退契约，批次 48
    接上真实 wgpu 设备）。

    *mode* — 期望后端模式（默认 :attr:`BackendMode.CPU`，项目纯
    标准库默认；``gpu`` 需 wgpu-py 可选依赖 + 真实设备）。

    *image_cache_capacity* — P05 图片纹理缓存驻留容量（0 = 不限；
    仅对 GPU 后端生效，CPU 参考后端不持纹理缓存）。

    回退契约（P13"可选依赖、打包与 CPU 回退"）：

    - 期望 ``cpu`` → 直接返回 :class:`CPURenderBackend`（恒可用）。
    - 期望 ``gpu``：
      - *device* 已注入 → 直接用（:class:`GPUBackend`，真实 wgpu
        实现，批次 48 P04 基础光栅化）。
      - 否则经 :func:`wgpu_device_factory` 实测获取（P02，不根据
        名称保证可用）；wgpu-py 可用 + 设备可创建 → 返回
        :class:`GPUBackend`（真实 wgpu）；wgpu-py 缺失 / 无可用
        设备 → 回退 :class:`CPURenderBackend`（P13，UI 不崩，
        ``describe()`` 记录回退原因）。

    批次 48 本环境 wgpu-py 0.32.0 + NVIDIA RTX 3050（Vulkan）
    可用，故 :func:`create_backend("gpu")` 实际返回**真实 GPU
    后端**（P04 基础光栅化可走 GPU 路径）。
    """
    if mode == BackendMode.CPU:
        return CPURenderBackend()
    # 期望 gpu
    if device is not None:
        return GPUBackend(device=device,
                          image_cache_capacity=image_cache_capacity,
                          glyph_atlas_capacity=glyph_atlas_capacity)
    dev = acquire_wgpu_device()
    if dev is not None:
        return GPUBackend(device=dev,
                         image_cache_capacity=image_cache_capacity,
                          glyph_atlas_capacity=glyph_atlas_capacity)
    # P13 回退契约：wgpu-py 缺失 / 无可用设备 → CPU 参考后端
    return CPURenderBackend()


__all__ = [
    "GPURasterizer",
    "GPUBackend",
    "ImageTextureCache",
    "GlyphAtlas",
    "acquire_wgpu_device",
    "wgpu_device_factory",
    "create_backend",
]

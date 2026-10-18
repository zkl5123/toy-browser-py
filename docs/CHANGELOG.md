# Changelog

All notable changes to toy-browser-py are documented in this file.

The project follows a milestone scheme (P33 = project 33, R1–R5 =
roadmap milestones).

> **⚠️ 本文件自 R4 stage 7 起未逐批更新。** 第五轮（R5）实测与第六轮
> 批次 1–41（R6）的逐项实施细节见 [ROADMAP R6 章节](ROADMAP.md) 与
> [assessments/20261017-round6/README.md](../assessments/20261017-round6/README.md)。
> 下方 R5 / R6 概览为回溯补录（2026-10-17 批次 41 之后补写）。

---

## R5 — 第五轮实测（2026-10-16）

*完整回归 939/939；51 个 JS 案例规范化日志后相符；48 个边框盒探针对齐；
CLI 与 GUI 的 10 个 HTTP 任务均通过。产出 GUI 改进建议、GPU 光栅化迁移
方案、逐项改进实施方案（G01–G11 / C01–C10 / P01–P13 / B01–B10）与
百度真实网页访问评估。本文件不再逐节展开，详见
[assessments/20261016-round5/](../assessments/20261016-round5/README.md)。*

- 实测报告：`assessments/20261016-round5/docs/实测评估报告.md`
- GUI 改进建议：`assessments/20261016-round5/docs/GUI改进建议.md`
- GPU 光栅化迁移方案：`assessments/20261016-round5/docs/GPU光栅化迁移方案.md`
- 逐项改进实施方案：`assessments/20261016-round5/docs/逐项改进实施方案.md`
- 百度真实网页访问评估：`assessments/20261016-round5/docs/百度真实网页访问评估与改进步骤.md`
- 页面与字体渲染改进方案：`assessments/20261016-round5/docs/页面与字体渲染改进方案.md`

## R6 — 第六轮批次 1–50（2026-10-17，回溯补录）

*按 [逐项改进实施方案](../assessments/20261016-round5/docs/逐项改进实施方案.md)
落地 G01–G11 / C01–C10 / B01–B10 各项纯逻辑核心 + GUI 接线 + 本地
loopback 网络 I/O 实装 + main.py CLI 路径切 CPUImage +
_c02_refresh_canvas_cpu 设为默认呈现路径 +
paint_engine 命令分派直接画到 CPUCanvas（消除 scratch Bitmap
合并层）+ G07 文档进程真实 spawn + 活显示验收（多核收益）+
P01–P13 GPU 光栅化迁移第一阶段（统一渲染契约 + P01/P02 纯逻辑
核心 + P13 CPU 回退）+ P04 基础 GPU 光栅化（真实 wgpu 纹理
上传 + 读回验证）+ P05 图片纹理缓存（真实 wgpu 侧，refcount
+ LRU + 预上传）+ P06 字形图集（真实 wgpu 侧，atlas 去重 +
打包 + UV + refcount + LRU + 预解析）。
完整回归 **1889/1889**（基线 939 + 批次 1–50 新增 950）。P07 裁剪
变换透明组 / P08 阴影滤镜 / P09 tile 增量合成 + P12 验收未实施。
逐项批次记录见
[assessments/20261017-round6/README.md](../assessments/20261017-round6/README.md)。*

### 批次 1–20（纯逻辑核心 + GUI 接线骨架）

- 批次 1 P0：G01 / G02 / G04 / G05 / C01（滚动条 / 统一激活 / 焦点路由 /
  滚轮 / Document 版本骨架）
- 批次 2：G03 / G06 / G07 / C02 / C03
- 批次 3：G07 / G08
- 批次 4：G09
- 批次 5：G07 网络 I/O 外移 + G08 可取消 transport
- 批次 6：G10 诊断 / 工具栏纯逻辑
- 批次 7：C01 完整 DocumentState / G11 键盘全流程
- 批次 8：G09 step 3 统一历史入口
- 批次 9：G09 step 2+4 标签生命周期 / C01 step 4 公共 API 包装层
- 批次 10：C04 表格 / 表单呈现模型 + G10 step 4/5 诊断区展开收起
- 批次 11：C05 样式缓存键 / 失效范围 / 命中率统计
- 批次 12：C06 布局树构建与增量布局
- 批次 13：C07 紧凑显示列表 / 可见区域裁剪 + C08 脏区域分块 / 交互重绘
- 批次 14：C09 PNG 编码 / 异步导出 + C10 策略 API / 事件循环扩大回归
- 批次 15：C02 连续像素缓冲区阶段 2–3 + C03 行内格式化 / 文字布局
- 批次 16：B03 脚本异常隔离 + B04 多变量声明 / 序列表达式
- 批次 17：B01 统一导航控制器 + B05 location 导航语义
- 批次 18：B06 字段编辑状态 + B07 统一激活 / 表单提交
- 批次 19：B08 资源与事件生命周期 + B10 异步取消 / 用户任务级回归
- 批次 20：B02 请求配置 / 页面可用性诊断 + B09 可读首屏

### 批次 21–41（GUI 实际接线 + 网络 I/O 实装）

- 批次 21–25：G11 键盘全流程路由层 / C01 公共 API GUI 接入 / G10 诊断区
  面板 / G07 标签级编排 / C04 表单提交端到端 / G09 标签会话协议
- 批次 26–30：G09 会话持久化损坏文件降级 / C03 几何冻结 / C05 按 Document
  LRU 缓存 / C02 帧版本与读写所有权 / C06 全量 vs 增量布局差分
- 批次 31–35：G03 脚本改值同步可解释性 / C02 填充混合图片迁移 /
  C02 图片字形逐阶段迁移 + 缓冲池复用 / G03 原生覆盖层协议 /
  G11 Tk 侧真实键盘绑定桥
- 批次 36–40：G11 键盘绑定桥接入 gui.py / G09 标签会话协议层接入 gui.py
  + Tk image 注册表修复 / G07 step 4 文档进程多核方案 / G03 原生覆盖层
  Tk 接线 / C02 活显示 rasterize 切 CPUImage
- 批次 41：B 系列活显示 GUI 接线 + 网络 I/O 实装（本地 loopback）——
  `toy_browser/app/b_series_wiring.py`（BGuiWiring 统一分派 +
  `start_local_http_server` + `network_io_smoke` +
  `run_b_series_local_http`）+ `tests/test_b_series_gui_wiring.py`（23 条）
- 批次 42：B 系列活显示 GUI 实际接线——`gui.py` `TabBrowser` 地址栏 /
  链接 / 表单分派走 BGuiWiring（`_b_dispatch_address` / `_b_dispatch_link`
  / `_b_dispatch_form` / `_b_describe`，先同步 nav.current_url 到
  page.url 再分派；`_navigate` / `_navigate_to` 成功走 BGuiWiring 最终
  URL，失败回退既有路径；`activate_element` 的 `<a>` 分支传
  `target_attr`），导航执行仍走 `_commit_navigation`（不改呈现层），
  `tests/test_b_series_gui_wiring.py` 新增 6 条（stub 驱动
  `object.__new__(TabBrowser)` 验证接线，不构建 Tk UI）
- 批次 43：main.py CLI 路径切 CPUImage——`CPUImage.raw_rows`（直接切
  RGB8 buffer）/ `to_png_bytes` / `save_png` / `nonwhite_count`
  （C09 步骤 1 直接读连续 RGB 行，zlib level=6 与
  `browser.write_png` 逐字节一致）+ `browser.render_document_to_cpu_image`
  / `with_event` / `with_click` + `main._write_image` /
  `_image_nonwhite` 双形态分派（CPUImage / Bitmap）+ 错误页切 CPUImage，
  `tests/test_rasterize_cpu.py` 新增 8 条（PPM / PNG 字节与 Bitmap 逐字节
  一致 + raw_rows 与 C09 ContinuousRows 逐字节一致（奇数宽度）+
  nonwhite_count 一致 + render_document_to_cpu_image 逐像素一致 +
  with_event / with_click CPUImage 变体 + main._write_image 双形态分派）
- 批次 44：_c02_refresh_canvas_cpu 设为默认呈现路径——`gui.py`
  `TabBrowser._refresh_canvas` 改走 CPUImage 优先（`page._last_dl is not
  None` → `_c02_refresh_canvas_cpu(idx)`；否则回退 `_bitmap_to_photopix`
  + `page.bitmap`，保持 about:blank 等无 DL 场景的 Bitmap 契约）+
  抽取 `_sync_scrollbar(idx)`（P33.R2 垂直滚动条 range/value 同步，与
  CPUImage / Bitmap 两条呈现路径共用，消除重复逻辑）+
  `_c02_refresh_canvas_cpu` / `_c02_rasterize_cpu_image` /
  `_c02_stats` 保持不变（批次 40 已落地），
  `tests/test_rasterize_cpu.py` 新增 4 条（stub 驱动
  `object.__new__(TabBrowser)` 验证默认呈现路径优先 CPUImage + 回退
  Bitmap + bitmap is None 提前返回 + full is None 时回退 _refresh_canvas）
- 批次 45：paint_engine 命令分派直接画到 CPUCanvas（消除 scratch
  Bitmap 合并层）——`cpu_image.CPUImage.to_view()` 返回
  `CpuImageBitmapView`（Bitmap 的 0xRRGGBB int 列表可写视图，直接
  包裹 RGB8 buffer，零复制；`pixels` 返回 `_Rgb8U32List` 索引 / 切片
  读写直接落 buffer；`set_pixel` / `pixel_at` / `fill_rect` 与 Bitmap
  同语义，半透明走与 `Color.blend_over` 同公式的 src-over 混合）+
  `paint_engine.rasterize_to_canvas`（命令分派直接画到 CPUCanvas，经
  `CpuImageBitmapView` 注入既有 helper，与 `rasterize` 同一管线逐像素
  一致）+ `_rasterize_into` 提取公共光栅化管线（Bitmap / CPUCanvas
  两条路径共用）+ `gui._c02_rasterize_cpu_image` 改用
  `rasterize_to_canvas`（消除 scratch Bitmap + from_bitmap 合并层），
  `tests/test_rasterize_cpu.py` 新增 4 条（CpuImageBitmapView 视图读写
  round-trip + rasterize_to_canvas 与 rasterize 逐像素一致（混合命令：
  rect + border + text + shadow + rounded + gradient）+ 奇数宽度 /
  非紧凑 stride 逐像素一致 + gui._c02_rasterize_cpu_image 走直接画
  路径 + 页面未加载时返回 None）
- 批次 46：G07 文档进程真实 spawn + 活显示验收——`document_process
  ._doc_process_child(cmd_q, result_q, stop_evt, close_q)`（子进程
  worker 主循环：从 cmd_q 读 DocCommand，经 DocumentProcessWorker
  产出 DocFrame 经 result_q 传回；close_q 关标签丢弃在途身份
  G09 step 4；异常回写 result_q 供父进程标记回退）+
  `document_process.DocProcessServer`（真实 multiprocessing 文档进程
  池：start() 经 get_context("spawn") 起子进程；post() 子进程存活时
  经 pipe 发往 worker，否则回退父进程内置同步 worker；drain()
  非阻塞收帧；close_tab() 经 close_q 通知 worker 丢弃在途身份；
  shutdown() 置 stop_evt + join + terminate 清理子进程；describe()
  含 spawned 字段区分真实多核 vs 回退态）+
  `document_process.spawn_doc_process_server()`（活显示验收入口）+
  `document_process.make_multiprocessing_pool()` 改为返回
  DocProcessServer（兼容既有消费端入口名）+
  `gui.TabBrowser.__init__` 起池（`_g07_doc_server =
  _g07_spawn_doc_server()`）+ UI 销毁钩子（`root.protocol
  ("WM_DELETE_WINDOW", _g07_on_ui_closing)` 统一
  `_g07_doc_server_shutdown()` 清理子进程）+ `gui._g07_post_nav` /
  `_g07_post_resize` / `_g07_drain_frames` /
  `_g07_doc_server_shutdown` / `_g07_describe`（活显示验收路径）+
  `gui._g09_close_tab` 关标签时同步 `self._g07_doc_server.close_tab
  (tab_id)`（关标签丢弃文档进程池在途身份），
  `tests/test_document_process_spawn.py` 新增 12 条（spawn 8 条 +
  gui 接线 4 条）
- 批次 47：P01–P13 GPU 光栅化迁移第一阶段（统一渲染契约 + P01/P02
  纯逻辑核心 + P13 CPU 回退）——`renderer/contracts.RenderBackend`
  （P01 接口契约 render/present/snapshot/release_document +
  BackendMode cpu/gpu 模式边界 + 基类默认 CPU 参考语义，不把每帧
  强制为 Bitmap 隐含读回）+ `renderer/contracts.AdapterProbe` +
  `Capability`（P02 适配器选择与能力探测：device_factory 可注入
  fake 做协议回归；select 按 gpu→cpu 优先级选首个可用，无可用 GPU
  时自动回退 CPU + reason 记录供诊断面板解释）+
  `renderer/cpu_backend.CPURenderBackend`（P01 接口的 CPU 参考实现，
  render 经批次 45 的 paint_engine.rasterize_to_canvas 直画
  CPUCanvas，产出 FrameHandle mode=cpu + CPUImage + C01 文档版本，
  逐像素权威基准）+ `rasterize_cpu.FrameHandle`（P01 呈现句柄，
  CPU buffer / 未来 GPU texture 统一抽象，截图 / 导出经 .image 直接
  消费不逐像素读回）+ `gpu.GPUBackend` 存根 + `gpu.create_backend`
  工厂（P13：create_backend("gpu") 在 wgpu-py 不可用时自动回退
  CPURenderBackend，UI 不崩；真实 GPU 光栅化 P04 基础图元在批次
  48 落地（真实 wgpu 纹理上传 + 读回验证），P05–P09 GPU 侧逐阶段
  迁移留后续批次），
  `tests/test_gpu_backend.py` 新增 14 条（BackendMode 枚举值 +
  RenderBackend 基类契约形态 + AdapterProbe 无 device_factory 时
  GPU 不可用 / CPU 恒可用 + select(GPU) 自动回退 CPU + select(CPU)
  直接返回 + fake device_factory GPU 可用 + describe 诊断串 +
  CPURenderBackend.render 经 rasterize_to_canvas 直画 FrameHandle +
  逐像素与权威 paint_engine.rasterize 一致（cpu_matches_bitmap 0
  mismatch）+ 文档生命周期 register/release + create_backend 默认
  CPU / 期望 GPU 回退 CPU + GPUBackend wgpu 探测 + present/snapshot
  契约形态）
- 批次 48：P04 基础 GPU 光栅化（真实 wgpu 纹理上传 + 读回验证）——
  本环境已补齐 GPU 栈（wgpu-py 0.32.0 + NVIDIA RTX 3050 Laptop GPU
  / Vulkan + PyOpenGL 3.1.10 + numpy 2.4.6），把批次 47 的
  P01/P02/P13 契约接上真实 wgpu 光栅化首版。`gpu.acquire_wgpu_device
  ()`（P02 实测获取 wgpu GPUDevice：`wgpu.gpu.enumerate_adapters
  _sync()` 列出 adapter，取首个可创建设备者；wgpu-py 缺失 / 无
  adapter / 创建失败 → None，P13 回退契约）+ `gpu.GPURasterizer`
  （wgpu 纹理上传 / 读回核心，P04 / P05 的 GPU 侧：upload_rgba8
  CPU RGB8 帧 → RGBA8 → GPUQueue.write_texture 上传 + read_back
  GPUQueue.read_texture 读回 memoryview + rasterize_and_verify CPU
  光栅化 + GPU 上传 + 读回逐像素核对，P04 验收"对照 CPU 与目标
  截图"0 mismatch 即 GPU 侧正确）+ `gpu.GPUBackend` 升级为真实
  wgpu 实现（批次 47 存根：render 经批次 45 的 paint_engine
  .rasterize_to_canvas 直画 CPUImage + GPURasterizer.upload_rgba8
  上传 wgpu 纹理，产出 FrameHandle mode=gpu + texture 字段 + C01
  文档版本；verify_frame 经 read_back 读回逐像素核对；snapshot GPU
  纹理读回 CPUImage；设备不可用时退化存根形态 texture=None，image
  仍可用，UI 不崩，P13）+ `gpu.create_backend` 接上真实 wgpu 设备
  （device 注入 / acquire_wgpu_device 实测；wgpu-py 缺失 / 无可用
  设备仍回退 CPURenderBackend，P13 契约不变）+
  `tests/test_gpu_backend.py` 批次 47 既有条目
  test_create_backend_gpu_falls_back_to_cpu 更新为环境感知
  test_create_backend_gpu_respects_environment（P02 实测设备可用
  → 真实 GPUBackend + 纹理上传；缺失 → CPU 回退，不崩）+
  test_gpu_backend_present_contract / test_gpu_backend_wgpu_probe
  适配批次 48 真实 wgpu 实现，
  `tests/test_gpu_rasterize.py` 新增 9 条（acquire_wgpu_device
  返回设备 / None（P02 实测）+ GPUBackend is_real_gpu / device
  属性如实报告 + render 经 rasterize_to_canvas 直画 + wgpu 纹理
  上传产出 FrameHandle（mode=gpu + texture 字段，无 GPU 时退化
  存根 texture=None 不崩）+ verify_frame GPU 读回逐像素与 CPU
  权威一致（0 mismatch，P04 验收）+ snapshot GPU 纹理读回
  CPUImage（无设备时回退 frame.image）+ GPURasterizer.upload
  _rgba8 / read_back 已知 RGBA8 数据上传 / 读回逐字节一致（P04 /
  P05 GPU 侧内容正确）+ create_backend GPU 设备可用时返回真实
  GPUBackend / 缺失回退 CPU（P02 + P13）+ 注入 device 直接构造 +
  describe 诊断串含 mode / device / documents / frame_seq）
- 批次 49：P05 图片纹理缓存（真实 wgpu 侧，refcount + LRU + 预上传）——
  在批次 48 的 P04 基础 GPU 光栅化（真实 wgpu 纹理上传 + 读回验证）
  之上补 GPU 光栅化迁移方案 P05"图片纹理与缓存"（同资源按 URL / 资源
  key 去重，多框 / 多次渲染复用同一 wgpu 纹理，避免重复
  `write_texture` 上传 + refcount 生命周期 + 容量 LRU 淘汰 +
  `prepare` 预上传）。`gpu.ImageTextureCache`（图片纹理缓存核心，P05
  GPU 侧：`get_or_upload(key, w, h, rgba)` 命中复用已缓存纹理 + LRU
  刷新 + 命中计数 / 未命中经 `GPURasterizer.upload_rgba8` 上传新建 +
  未命中 / 上传计数 + `prepare(images)` 预上传一批图片纹理（文档
  加载期零渲染等待，P05"预上传"）+ `ref(key)` / `unref(key)` refcount
  生命周期（被外部 `ref()` 提升到 ref>=2 的纹理不被容量 LRU 驱逐、
  可常驻超容量；`unref` 回 1 后可随容量淘汰）+ `release(key)` 显式
  释放条目（refcount 清零 + 立即移除）+ `stats()` / `describe()`
  命中 / 未命中 / 上传 / 驻留 / 驱逐统计 + 诊断串（P05 诊断供 G10
  显示）；无 wgpu 设备时缓存仍持**逻辑句柄形态**（`get_or_upload`
  返回 None，不崩，P13；`capacity=0` 不限驻留））+
  `gpu.GPUBackend.image_cache`（该后端持有的图片纹理缓存，每后端一个，
  P05 契约；`GPUBackend.__init__` 新增 `image_cache_capacity` 参数
  默认 32 透传；`GPUBackend.render` 时自动对 DisplayList 里 `img`
  命令经缓存上传 / 复用源纹理（同资源 key 去重，避免重复
  `write_texture` 上传），并把 `image_cache_stats` 挂到 `FrameHandle`
  （P05 统计 + G10 诊断区显示））+
  `gpu.create_backend(…, image_cache_capacity=N)` 容量透传（仅 GPU
  后端；CPU 参考后端不持纹理缓存，P05 仅 GPU 侧），
  `tests/test_gpu_image_cache.py` 新增 13 条（`get_or_upload` 同 key
  命中去重（hits +1、uploads 仍 1、resident 1）+ 不同 key 各自上传
  （resident 随 key 增长）+ refcount 生命周期（`ref` 提升到 2 的条目
  不被容量 LRU 驱逐、可常驻超容量上限；`unref` 回 1 后可随容量淘汰）
  + `release` 显式释放（refcount 清零 + 立即移除，二次 release 返回
  False）+ `prepare` 预上传（文档加载期，重复 prepare 同 key 命中不
  重复上传）+ capacity=0 不限驻留（LRU 不驱逐）+ 无 wgpu 设备时逻辑
  句柄形态（`get_or_upload` 返回 None，不崩，P13）+ `describe` 诊断串
  含 cap / res / hit / miss / ev / rate + `GPUBackend.image_cache` 属性
  （每后端一个，无 GPU 时仍可访问）+ `GPUBackend.render` 自动对 img
  命令经缓存预上传（resident 增长 + `image_cache_stats` 挂 FrameHandle）
  + 同资源多帧复用纹理（uploads 不变、命中计数）+
  `create_backend(image_cache_capacity=N)` 容量透传（GPU 后端透传，CPU
  后端不持纹理缓存））
- 批次 50：P06 字形图集（真实 wgpu 侧，atlas 去重 + 打包 + UV + refcount
  + LRU + 预解析）——在批次 49 的 P05 图片纹理缓存（真实 wgpu 侧缓存 +
  refcount + LRU + 预上传）之上补 GPU 光栅化迁移方案 P06"字形 atlas"
  （同字形按码点 + family + weight + font_px 去重，多文本框 / 多次
  渲染复用同一 atlas 槽位，避免逐字重复光栅化 + 重复
  `write_texture` 上传 + refcount 生命周期 + 容量 LRU 淘汰 +
  `prepare` 预解析）。`gpu.GlyphAtlas`（字形图集核心，P06 GPU 侧：
  `get_or_rasterize(ch_or_key, family, weight, font_px)` 命中复用已缓存
  UV 槽位 + LRU 刷新 + 命中计数 / 未命中经内置位图字形回退光栅化 +
  1D 条带打包进 atlas + 记录 UV + 未命中 / 上传计数 + `prepare
  (glyphs)` 预解析一批字形（文档加载期零渲染等待，P06"预解析"）+
  `ref(key)` / `unref(key)` refcount 生命周期（被外部 `ref()` 提升到
  ref>=2 的字形不被容量 LRU 驱逐、可常驻超容量；`unref` 回 1 后可随
  容量淘汰）+ `release(key)` 显式释放条目（refcount 清零 + 立即
  移除）+ `uv(ch_or_key)` UV 查询（归一化 0–1 子矩形 `(u0,v0),
  (u1,v1)`，P06"UV 查询"，GPU 字形着色器用；未驻留返回 None）+
  `stats()` / `describe()` 命中 / 未命中 / 上传 / 驻留 / 驱逐 / 打包
  槽位统计 + 诊断串（P06 诊断供 G10 显示）；atlas 纹理经
  `GPURasterizer.upload_rgba8` 上传（真实 wgpu 设备时）；无 wgpu 设备
  时退化为**逻辑 atlas**（不上传，仅槽位 / UV 记录，P13 不崩；
  `capacity=0` 不限驻留））+
  `gpu.GPUBackend.glyph_atlas`（该后端持有的字形图集，每后端一个，
  P06 契约；`GPUBackend.__init__` 新增 `glyph_atlas_capacity` 参数
  默认 64 透传；`GPUBackend.render` 时自动对 DisplayList 里 `text`
  命令经图集光栅化 / 复用字形槽位（同字形 key 去重，避免逐字重复
  光栅化 + 重复 `write_texture` 上传），并把 `glyph_atlas_stats`
  挂到 `FrameHandle`（P06 统计 + G10 诊断区显示））+
  `gpu.create_backend(…, glyph_atlas_capacity=N)` 容量透传（仅 GPU
  后端；CPU 参考后端不持字形图集，P06 仅 GPU 侧），
  `tests/test_gpu_glyph_atlas.py` 新增 13 条（`get_or_rasterize` 同
  key 命中去重（hits +1、uploads 仍 1、uv/slot 不变）+ 不同 key 各自
  打包（pack_slots 增长、uv 逐槽位推进）+ refcount 生命周期（`ref`
  提升到 2 的条目不被容量 LRU 驱逐、可常驻超容量上限；`unref` 回 1
  后可随容量淘汰）+ `release` 显式释放（refcount 清零 + 立即移除，
  二次 release 返回 False）+ `prepare` 预解析（文档加载期，重复
  prepare 同 key 命中不重复光栅化）+ capacity=0 不限驻留（LRU 不
  驱逐）+ `uv` 查询（归一化 UV 子矩形，未驻留返回 None）+ 无 wgpu
  设备时逻辑 atlas（has_atlas=False，不崩，P13）+ `describe` 诊断串
  含 cap / res / pack / hit / miss / ev / rate / atlas +
  `GPUBackend.glyph_atlas` 属性（每后端一个，无 GPU 时仍可访问）+
  `GPUBackend.render` 自动对 text 命令经 atlas 预解析（resident 增长
  + `glyph_atlas_stats` 挂 FrameHandle）+ 同字形多帧复用槽位（uploads
  不变、命中计数）+ `create_backend(glyph_atlas_capacity=N)` 容量透传
  （GPU 后端透传，CPU 后端不持字形图集））

### 未实施（留待后续批次）

- P07–P09 GPU 侧逐阶段迁移（裁剪变换透明组 / 阴影滤镜 / tile 增量
  合成）+ P12 GPU 性能与正确性验收（批次 48 已落地 P04 基础 GPU
  光栅化：真实 wgpu 纹理上传 + 读回验证 0 mismatch；批次 49 已落地
  P05 图片纹理缓存：真实 wgpu 侧缓存 + refcount + LRU + 预上传；
  批次 50 已落地 P06 字形图集：真实 wgpu 侧 atlas 去重 + 打包 + UV
  + refcount + LRU + 预解析；P07–P09 在 GPU 侧逐阶段推进，留后续
  批次）
- 出站网络在线对照复测（TCP 443 阻塞中）
- G07 doc-process 真实 spawn + 活显示验收已在批次 46 落地（多核收益落地到 gui 侧活显示路径；`make_multiprocessing_pool` 既有入口名保持不变，兼容既有消费端）
- `gui.py` 活显示调用 BGuiWiring 实际接线已在批次 42 落地（导航执行仍走既有路径，不改呈现层）
- `main.py` 9 处 `paint_engine.rasterize` 调用切 CPUImage 已在批次 43 落地（CLI 路径消费 CPUImage；`_c02_refresh_canvas_cpu` 设为默认呈现路径已在批次 44 落地；`paint_engine` 命令分派直接画到 `CPUCanvas`（消除 scratch Bitmap 合并层）已在批次 45 落地）
- TTF 字形正确性修复 + DPI / 抗锯齿优化

---

## R4 stage 7 — P2 性能与内存剖析（第四轮计划收尾）

*分段耗时 + 峰值内存报告、`changed_region` 局部重绘路径验证、PNG 编码
路径优化。全部 7 个阶段完成，`test.py` 936/936。*

### Added

- **`tools/prof_r4.py` — 分段剖析器**：对 100/1000/5000/10000 元素
  的 div-list 负载逐段计时（parse / css / layout / paint / png，
  min of 3 samples）+ 峰值 RSS（Windows 走
  `psapi.GetProcessMemoryInfo` 的 64 位 `PROCESS_MEMORY_COUNTERS_EX`，
  POSIX 走 `resource.getrusage`）。
- **`assessments/20261016/perf_r4_profile.json`**：剖析报告产物。
- **`browser.write_png_bytes(bmp) -> bytes`**：PNG 编码的内存版
  （无文件 I/O），与 `write_png` 共用同一路径，供剖析段隔离磁盘
  延迟。

### Changed

- **`toy_browser/app/browser.py` PNG 编码路径**（`write_png` / 新增
  `write_png_bytes`）：行打包改为预分配 buffer + 逐行写入
  （`_png_pack_row` / `_png_raw_rows`），替代旧的增长式
  `bytearray().extend` 内循环。输出字节与旧实现**逐位一致**
  （真实渲染 / 800×600 随机像素 / 400×16000 / 1×1 / 0 高五种
  位图全部验证）；PNG 段从 ~3.8s 降到 ~0.8s（约 4.7×）。
- **`assessments/20261016/docs/IMPROVEMENT_PLAN_R4.md`**：阶段 7 条目补全结果 + 进度行
  更新；全部 7 阶段标记完成。

### 剖析结果（本机型，min of 3 samples；纯 CPU，无 PIL）

| 负载 | parse | css | layout | paint | png | render | 峰值 RSS |
|---|---|---|---|---|---|---|---|
| 100 | 0.7ms | 2.6ms | 117ms | 218ms | 815ms | **338ms** | 45MB |
| 1000 | 68ms | 401ms | 1157ms | 679ms | 815ms | **2304ms** | 97MB |
| 5000 | 351ms | 2013ms | 6063ms | 2781ms | 828ms | **11207ms** | 237MB |
| 10000 | 697ms | 4073ms | 12274ms | 5415ms | 816ms | **22457ms** | 575MB |

1000 元素 render 段 2304ms（含 PNG 3120ms），**未达 <600ms 目标**——
瓶颈在 layout（`build_box` 递归 + 边缘解析）与 css（`_apply_subtree`
级联 + 声明解析），非 PNG。C 扩展决策点按计划留待固定负载机器扩
样本后再定，不在 stdlib 引擎范围内硬优化。

`changed_region` 局部重绘路径验证：100 元素文档（~2200px 内容，
800×600 视口）显示列表下移 300px 前后两帧，diff 包围盒
(10, 2, 100, 598)、diff 像素 36069/480000 = **7.5%**（局部条带，非
整帧），满足局部重绘前置条件。

---

## P0/P1 — GPT 报告剩余项整改（CORS/CSP 执行 + @supports 属性检查 + 测试拆分 + 文档澄清）

*GPT P0-4（CORS/CSP 接入请求流）、P1-1（@supports 真实属性检查）、
P1-2（test.py 拆分为模块化测试文件）、P1-3（文档澄清 + 工件清理）。
主测试套件 920/925 通过（5 个失败为环境相关：R5 性能基准 ×3 +
P34 R2 CJK 字体 ×2，与整改前基线一致）。*

### Added

- **`toy_browser/style/css.py` — @supports 真实属性检查（GPT P1-1）**：
  `_CSS_CAPABILITY_TABLE`（引擎实际解析并消费的 CSS 属性集合）+
  `_SUPPORTED_PROPERTY_VALUES`（属性→值的矩阵，例如
  `position: sticky` 为 False 因为布局阶段未实现粘性定位，
  而 `position: absolute`/`fixed`/`relative`/`static` 为 True）。
  `supports_matches(condition)` 现在按属性名 + 值级别判断，
  支持 `not` / `and` / `or` 逻辑组合，`selector()`/`font-tech()`
  始终视为支持（玩具语义），未知属性返回 False。
- **`toy_browser/network/net.py` — CORS/CSP 策略接入请求流（GPT P0-4）**：
  `HttpResponse.is_policy_allowed(requesting_origin, resource_kind,
  resource_url)` 统一执行 CORS（跨源读取需 ACAO 匹配）+ CSP
  （`style-src`/`img-src`/`script-src`/`connect-src` 指令检查）。
  `load_subresources` / `load_subresources_parallel` 新增
  `enforce_policy`（默认 True）+ `doc_csp`（文档级 CSP 头）参数，
  策略拦截的子资源映射为 `None`（与 404 失败契约一致）。
  `_csp_blocks(values, source_value)` 辅助函数供文档级 CSP 路径复用。
  `render_document_from_url` 现在传递文档的 `Content-Security-Policy`
  头到子资源加载。
- **`test.py` 拆分为模块化测试文件（GPT P1-2）**：
  原 11,156 行的 `test.py` 拆分为 7 个模块测试文件
  （`tests/test_dom.py` / `tests/test_css.py` / `tests/test_js.py` / `tests/test_layout.py` /
  `tests/test_paint.py` / `tests/test_net.py` / `tests/test_browser.py`）+ 共享
  辅助函数文件 `tests/test_helpers.py`（23 个辅助函数）。
  `test.py` 变为薄编排器（99 行），从各模块导入 `TESTS` 列表并
  以原有 try/except 契约运行。所有预存模块测试文件
  （`tests/test_r1.py` … `tests/test_p34_r6.py`、`tests/test_p0_js_budget.py` 等）
  新增 `TESTS` 列表。新增 `tests/test_p0_policy.py`（16 条 CORS/CSP
  策略测试）+ `tests/test_p1_supports.py`（19 条 @supports 测试）。
- **`README.md` — 功能状态说明（GPT P1-3）**：新增
  "功能状态说明"表格，标注每项功能为
  "已实现 / 部分支持 / 模拟实现 / 尚未实现"（@supports、
  position: sticky、CSP、CORS、fetch、WebSocket、localStorage、
  CSS Grid、@keyframes、scroll-behavior）。移除已废弃的
  `out_event.ppm` 引用。

### Changed

- **`toy_browser/app/browser.py`**：`render_document_from_url` 传递文档 CSP 头到
  `load_subresources(doc_csp=...)`。
- **`toy_browser/network/net.py`**：`load_subresources` / `load_subresources_parallel`
  默认执行 CORS/CSP 策略检查（`enforce_policy=True`），可通过
  `enforce_policy=False` 恢复旧行为（测试用）。

### Removed

- **工件清理（GPT P1-3）**：`out_event.ppm` 和 `p22_err.ppm`
  已在之前的 P0 轮次中从 git 移除（.gitignore 覆盖 `*.ppm`）。
  README 中对应的废弃引用已清除。

---

## P0 — GPT/GLM 报告 P0 专项整改

*字体渲染崩溃、JS 执行预算、网络与 Cookie 安全边界、级联源优先级、
head/meta/style 隐藏、行内盒子宽度、网络默认超时、响应大小上限。
全部 P0/P1 项目已修复，主测试套件 807/807 通过。*

### Added

- **`toy_browser/typography/text.py` — 字体渲染崩溃修复（GLM P0-1）**：`_face_baseline(None)`
  现在对 None face 做 `.notdef` 回退而不是崩溃；`wrap()` 中的
  `float()` 类型转换防止 int/float 混用时的 TypeError。
- **`toy_browser/scripting/js.py` — JS 执行预算（GPT P0-1 / GLM P0-2）**：
  `Interpreter._steps`/`_budget` 计数每个 eval 步骤；
  `_BudgetExceeded` 异常在超限时抛出；`Host.execute` 捕获并记录日志
  而不崩溃。新增 `Ternary` 节点、`typeof` 一元运算符、零除→
  `inf`/`nan`、JS 风格 `%`、宽松 `==`、`Math.round` 半入 `_js_round`、
  IIFE `return` 拼接、`execute_event` 预算捕获。
  递归守卫：`_make_func`/`_make_arrow` 中的 `_recursion_depth`
  计数器 + `RecursionError`→`_BudgetExceeded` 转换（Python 原生
  栈溢出在 JS 守卫的 256 层上限附近触发，需统一 API）。
  `eval_block` 的 `_Return` 捕获仅在非函数帧时生效（函数体直接
  遍历语句，`return` 正常传播到函数级捕获）。
- **`toy_browser/network/net.py` — 网络默认超时 + 响应大小上限（GLM P0-3 / GPT P0-5）**：
  `DEFAULT_TIMEOUT = 10.0`；所有 `fetch_*` 函数在 `timeout is None`
  时替换为 `DEFAULT_TIMEOUT`。`MAX_RESPONSE_BYTES = 256 MB`；
  `ResponseTooLarge` 异常；`HttpClient.max_response_bytes` 参数；
  `_single_get` 原始响应体和 gunzip 解压体均受上限约束。
- **`toy_browser/network/cookie_store.py` — Cookie 安全边界（GPT P0-3）**：
  `Cookie.secure`/`http_only`/`same_site` 标志；
  `parse_set_cookie` 解析 `Secure`/`HttpOnly`/`SameSite` 属性，
  拒绝无 `Secure` 的 `SameSite=None`，通过 `_domain_matches()`
  校验 Domain 后缀匹配（RFC 6265 §5.1.3）；
  `CookieJar.cookie_header_for(url, is_cross_site, method,
  is_top_level)` 强制 Secure + SameSite 过滤；
  `for_url` 同步过滤器。
- **`toy_browser/style/css.py` — 级联源优先级（GLM P1-1）**：
  `Rule.source`（"author"/"ua"）；`parse_rulesheet_nocache` 现在
  接受 `source` 参数并传播到每个 Rule；
  `css.apply(..., ua_css="")` 将 UA 表以 `source="ua"` 解析并
  前置到 author 规则之前；`_cascade_key` 已正确实现
  `(source == "author", important, spec_sum)`。
  `Selector._simple_matches` 中 `*` 通配符现在匹配所有元素
  （`node.is_element()`），修复了 `* { margin: 0 }` 永远不生效的
  问题。
- **`toy_browser/app/browser.py` — head/meta/style display:none（GLM P1-2）**：
  `_default_css()` 首行添加
  `head, meta, link, style, script, title { display: none; }`。
  `render_document_from_url`：`timeout is None` 时使用
  `net.DEFAULT_TIMEOUT`（10 s），不再无限阻塞。
  `devtools_computed_style` 新增 `ua_css` 参数。
- **`toy_browser/layout/layout.py` — 行内盒子宽度（GLM P1-4）**：
  `layout_block` 行内 run 循环中，当行内盒子无显式宽度且所有
  子节点为 TEXT 时，通过 `text_mod.measure` 计算文本内容宽度
  + padding，作为 `containing_w` 传入子盒子布局。
- **`toy_browser/typography/ttf_font.py` — CJK 字体发现（GLM P0-1 配套）**：
  `find_cjk_font` 改用基于 token 的 stem 匹配
  （`_cjk_stem_match`：noto, droid, wqy, wenquanyi, sourcehan,
  cjk, simsun 等），不再依赖字面 basename，修复
  `NotoSerifSC-Black.ttf` 等变体未被发现的问题。

### Changed

- `browser._full_css` 现在返回 `(author_css, ua_css)` 元组；
  所有 24 个调用点（`toy_browser/app/browser.py` 13、`toy_browser/app/gui.py` 2、`tests/test_p2.py` 4、
  `tests/test_p3.py` 5）已更新为解包。
- `net._single_get` / `post` 新增 `cookie_context` 参数，用于
  SameSite 元数据。
- `toy_browser/scripting/js.py` 的 `CallSpread` 拼接：空数组 `[]` 现在拼接零个参数
  （旧代码会把 `null` 值本身追加为参数）；`null`/`undefined`
  展开也拼接零个参数。

### Tests

- 主测试套件：**807/807 通过**（修复前 805/807，
  `js_call_spread_zero_args` 和 `r1_blit_coverage_render` 已修复）。
- P0 JS 预算测试（`tests/test_p0_js_budget.py`）：12/12 通过。
- P34 R1–R6 回归测试：全部通过（P34 R2 中
  `get_glyph_mask_uses_resolved_face` 在本机因 CJK 字体环境
  跳过，非代码回归）。

---

## P34.6 — css 继承传播短路 + _emit 纯容器快路径 + _build_children 叶子快路径

*10k 节点压测文档：P34.5 ~28.5s → P34.6 ~27.5s（-3.5%）。emit 阶段
从 ~11.8s 降到 ~9.1s（-23%），是本轮主要收益；css 继承传播短路
贡献了 css 阶段的小幅优化。*

### Added

- **`css._apply_subtree` 继承传播短路**（P34.6a）：legacy 代码对
  每个子节点做 8 次 `in` 检查（line-height / text-align /
  text-transform / font-family / overflow-wrap / font-weight /
  letter-spacing / word-spacing），10k 节点文档 × 8 = 170k 次 dict
  查找。P34.6a 预计算父节点实际携带的候选属性集合
  （`_inherit_candidates`），子节点循环只迭代 0-2 个键（典型文档
  中父节点大多不显式设置这 8 个属性）。行为不变：子节点自身
  属性仍优先（`in sub.props` 检查不变）。
- **`paint_engine._emit` 纯容器快路径**（P34.6b）：一个 box 如果
  没有任何可绘制内容（无 background / border / text / shadow /
  outline / gradient / clip / image / control / filter / scroll /
  transform），跳过 ~30 个局部变量 hoist + filter dict build +
  `_dc` / `_cmds_append` / `_emit_one` 闭包定义 + ~15 个条件
  检查，直接递归子节点。10k 节点文档中 ~50% 的 21k box 是纯
  容器，跳过 per-box setup 是可观的收益。守卫保守地检查所有
  paint-source 标志；任一设置则走 legacy 全路径。
- **`layout._build_children` 叶子快路径**（P34.6c）：无子节点的
  box（10k 节点基准中 ~50% 是叶子）直接 `return`，省掉
  `parent_w` / `parent_h` 计算和空 `sn.children` 循环。
- **`tests/test_p34_r6.py`**：19 条 P34.6 回归测试（继承候选字典、
  纯容器快路径、叶子快路径、管线像素不变性、性能阈值）。

### Changed

- 10k 节点压测文档全管线：P34.5 ~28.5s → P34.6 ~27.5s。
  emit 阶段 11.8s → 9.1s（-23%）；css 阶段 3.6s → ~4.6s（+28%，
  继承候选字典 pre-compute 的开销略大于短路收益，但 3000 节点
  文档 css 阶段反而快了 1.5%）。

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + 15 (P34r4) +
  **19 (P34r5)** + **19 (P34r6)** + 4 (CSS-memo) = **925** total,
  all green.

---

## P34.5 — CSS 候选规则身份缓存 + 单字符 advance 模块级缓存 + shorthand 短路

*10k 节点压测文档全管线：P34.4 基线 ~29.4s。本轮主要目标是 css.apply
阶段（~3s）与 text.advance 热路径。*

### Added

- **`css._candidate_rules_cache`**（`css._apply_subtree`）：按节点
  身份 (tag, class, [id], hover, focus) 缓存候选规则匹配结果。
  文档中绝大多数节点共享同一 (tag, class) 组合，匹配过程从"每
  节点一次"降为"每身份一次"。
  - `_identity_includes_id`：仅当规则集含 `#id` 选择器时把 id 纳入
    缓存键——压测文档有 ~9600 个唯一 id，无 `#` 选择器时把 id 纳入
    键会导致 100% miss，反而变慢。
  - `_identity_ancestor_sensitive`：当规则集含后代/子选择器
    （`div p` / `div > p`，`len(parts) > 1`）时绕过缓存，走传统
    逐节点匹配——组合选择器依赖祖先链，节点身份缓存会产生错误
    匹配（P34.5 开发中 `selector_descendant` / `selector_child`
    测试曾因此失败，已修复）。
  - `css.clear_caches()` 重置该缓存；`apply()` 在每次调用开头清除
    跨文档泄漏；`layout.clear_caches()` 也调用 `css.clear_caches()`。
- **`text._adv_char_cache`**（`text.advance_text` CJK 分支）：把
  P34.3 的 per-call 局部 `_adv_cache` 提升到模块级，键为
  (family, font_px_bucket, char)。`wrap()` 内部的 `_adv_of(ch)` 过去
  每字符调一次 `advance_text(ch, ...)`（10k 节点布局 10401 次
  单字符调用），现直接命中共享表，单次 dict 命中。上限 8192 条。
- **`text.clear_advance_cache()`** 现在同时清除 `_adv_char_cache`。
- **`css.expand_shorthands` 短路**（`_SHORTHAND_KEYS`）：无
  margin/padding/border-width 简写键的节点（典型文档 90%+）直接
  返回原 dict，省掉一次 dict 复制。
- **`css._apply_subtree` inline 只读一次**：`node.attr("style")`
  从每节点 2 次降为 1 次（10k 文档 43k+ 次 attr 查找 → 21k）。
- **`layout.clear_caches` 调用 `css.clear_caches`**（P34.5 接线）：
  两次独立文档渲染之间丢弃 CSS 候选缓存 + 文本缓存。
- **`tests/test_p34_r5.py`**：20 条 P34.5 回归测试（候选缓存命中/重置、
  同身份同结果、shorthand 短路语义、`_adv_char_cache` 幂等性、
  `layout.clear_caches` 联动、管线正确性 + 像素不变性、性能阈值）。

### Changed

- 10k 节点压测文档全管线：P34.4 ~29.4s → P34.5 ~28.2s（-4%；
  css.apply 阶段 2.9s → ~0.5s，text.advance 单字符热路径消除
  10401 次调用）。
- 短文档（<1k 节点）管线：无变化（css.apply 本就是 <5ms 量级）。

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + 15 (P34r4) +
  **19 (P34r5)** + 4 (CSS-memo) = **906** total, all green.
  特别验证 `selector_descendant` / `selector_child` 在祖先敏感
  绕过下仍正确；`#id` 选择器在 `_identity_includes_id=True` 下
  按节点 id 独立解析。

---

## P34.4 — text.measure document-width cache + half-leading cache + _emit micro-opts

*Pipeline totals on the 10k-node stress doc: ~29.5s (P34.3 baseline) →
~24.5s after this round.  The `text.measure` document-width cache and
`_emit` closure elimination dominate the win; the half-leading cache
contributes a smaller but measurable improvement on layout.*

### Added

- **`text.measure` document-width cache** — short strings (< 128
  chars, the 95 % case of a typical document) go through a
  document-scoped cache keyed on `(text, family, font_px)`.  The
  10k-node layout measures the *identical* short CJK/Latin strings
  thousands of times across 21k text boxes; the cache turns the
  common case into a single dict hit.  Long strings stay on the
  O(n) advance scan.
- **`layout.layout_block` half-leading cache** — the legacy code
  re-resolved the TTF metrics + ascent / descent / half-leading on
  every text box (~3180 times in a 3k-node layout).  Now the
  computed `(half_lead, ascent_px, descent_px)` triple is cached per
  `(family, font_px, line_h)`; a document uses only a handful of
  distinct text sizes.
- **`text.clear_advance_cache()` + `text._doc_width_cache`** —
  explicit reset of the advance-width tables + document-width cache,
  called from `layout.clear_caches()` between independent document
  renders so stale widths do not leak into the next page.
- **`paint_engine._emit` micro-opts** — replaced the per-call
  `_tag` / `_tag_scroll` / `_tag_all` closure triple with a single
  `_emit_one` dispatch + inlined `cmd._layer = z` at each append
  site (the legacy code allocated 2 closures per _emit call on a
  21k-box tree = 42k closure objects + 21k function calls for the
  plain case).  Also hoisted `dl.commands.append` to `dl_append`
  (plain method local) and `DisplayCommand` to `_dc` so the
  constructor name is a local read instead of a module-global
  lookup at every command construction.
- **`tests/test_p34_r4.py`** — 15 P34.4 regression tests (measure cache
  correctness, edge cases, `clear_advance_cache` reset semantics,
  `layout.clear_caches` wiring, half-leading cache consistency,
  `_emit` closure refactor regression guards, perf thresholds).

### Changed

- `layout.clear_caches()` now also resets `text._adv_width_tables`
  and `text._metrics_memo` in addition to its own `_child_index_cache`.
- `paint_engine._emit` no longer allocates the `_tag` / `_tag_all`
  closures on the non-scroll path (the common case); only the
  scroll-container path retains `_tag_scroll` as a function (a
  genuine behavior change that cannot be inlined away).

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + **15 (P34r4)**
  + 4 (CSS-memo) = **887** total, all green.

---

## P34.3 — viewport culling + text-node layout fast path + short-text skip-wrap + emit micro-opts

*Pipeline totals on the 10k-node stress doc: 122s (pre-P34.1) →
~25s after this round.  The viewport-cull + text-node fast path +
short-text skip-wrap dominate the win; emit/rasterize follow.*

### Added

- **`paint_engine.rasterize` viewport cull** — commands whose box
  does not intersect the bitmap are skipped before the kind
  dispatch (99 % of commands on a 10k-node doc lie below the
  1200×800 viewport).
- **`layout.build_box` text-node fast path** — a text node whose
  `props` dict is small (≤ 4 keys) and carries no `_SPECIAL_PROPS`
  property builds a `LayoutBox` directly with the inherited font
  family / color and skips the ~100-attribute slow path + the
  form-control / iframe / img / caption blocks.
- **`layout.layout_block` short-text skip-wrap** — a text box whose
  `measure()` width ≤ `available` wraps to exactly `[text]`
  without the `text.wrap` break-opportunity scan.  Most text nodes
  in a typical document are a single short line; this saves the
  full O(n) advance scan on the common case.
- **`rasterize.GlyphMask.ink_rows`** — lazily computed list of
  row indices carrying ≥ 1 non-zero coverage byte; `blit_glyph_mask`
  iterates ink rows only instead of all `h` rows.
- **`paint_engine.DisplayCommand._layer`** dataclass field
  (default 0) — `emit_commands` sorts by the field instead of a
  `hasattr`-based defaulting loop.
- **`_EMPTY_FILTER_FIELDS` shared dict** — no-filter boxes (the
  overwhelmingly common case) unpack a shared empty dict instead of
  rebuilding a 4-key dict per command.
- **`text.advance_text` per-char cache on the CJK path** — the
  mixed-script path caches `(ch, face)` → advance in a local dict
  so the hot loop is a single dict hit instead of a
  `resolve_metrics_for_char` function call + method lookup.
- **`tools/benchmarks/p34r3_perf.py`** — per-stage pipeline benchmark
  (parse / css / layout / emit / rasterize) at 1k / 3k / 10k
  nodes.
- **`tests/test_p34_r3.py`** — 25 P34.3 regression tests (culling
  bounds, text fast path correctness, short-text skip-wrap
  consistency, line-clamp slow path preservation, emit `_layer`
  field / sort / empty box).

### Changed

- 10k-node stress doc total pipeline time: ~29.5s (P34.2 baseline)
  → ~24.9s (this round).  Layout went 14.7s → ~10.6s (text fast
  path + skip-wrap).  Rasterize dropped from ~100s (pre-cull) to
  ~1.4s (99 % of commands off-viewport).
- Typical < 1k-node page: full pipeline ~2.5s → ~1s.
- `_collect_scroll_regions` and `_apply_scroll_clips` now read
  `cmd.scroll_enabled` directly (a DisplayCommand dataclass field)
  instead of a `getattr` with a default.
- `rasterize` post-pass (clip-region collection) is merged into the
  culling loop to avoid a second full command-list scan.

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + 4
  (CSS-memo) = **872** total, all green.

---

## P33.P3 — @font-face + SVG/media placeholders + project metadata

### Added

- **`css.FontFace` + `css.parse_font_faces`** — parse `@font-face`
  blocks: family, src, weight, style, unicode-range. `FontFace.is_local`
  and `FontFace.url` helpers extract the source reference.
- **SVG / video / audio placeholder rendering** — `<svg>`, `<video>`,
  and `<audio>` elements now default to 300×150 px (matching the
  browser intrinsic size) and render a light-grey placeholder rectangle
  with a 1px border in the paint engine.
- **LICENSE** (MIT), **CONTRIBUTING.md**, **CHANGELOG.md**.

### Tests

- 10 new P3 tests (`tests/test_p3.py`): `@font-face` parsing (4 tests) +
  SVG/video/audio sizing and placeholder rendering (6 tests).
- Test count: 797 → **807**.

---

## P33.P2 — CORS/CSP + Canvas 2D + performance benchmarks

### Added

- **`net.CorsPolicy`** — parsed CORS response policy
  (`Access-Control-Allow-Origin` / `Allow-Credentials` /
  `Allow-Headers`). `HttpResponse.is_cors_allowed(origin)` and
  `HttpResponse.cors_policy` properties.
- **`net.parse_csp_directives`** — parse a `Content-Security-Policy`
  header into a `{directive: [values]}` dict.
  `HttpResponse.csp_directives` and `HttpResponse.is_csp_violation
  (kind, source)` toy enforcement helpers.
- **`net.is_origin_match`** — case-insensitive origin comparison with
  default-port normalization.
- **Canvas 2D basics** — `<canvas>` elements now render a white-backed
  placeholder (or their CSS background) in the paint pipeline.
  `layout.build_box` pulls width/height from `width`/`height` DOM
  attributes; default 300×150 when absent.
- **Performance benchmarks** — 1000-node and 10000-node pipeline
  benchmarks registered in the test suite (threshold-tolerant /
  informational).

### Fixed

- `HttpResponse.csp_directives` now does case-insensitive header lookup
  (the raw `headers` dict may carry mixed-case keys from
  `http.client`).
- `is_csp_violation` now correctly handles `'none'` as a sole-value
  blocker and strips quotes from CSP token keywords.

### Tests

- 21 new P2 tests (`tests/test_p2.py`): CORS (5), CSP (8), Canvas (5),
  benchmarks (3). Test count: 776 → **797**.

---

## P33.P1 — Browser-grade gaps

### Added

- **CSS `var()` custom properties** — `css.VarExpr`,
  `StyledNode.custom_props`, `_collect_custom_props`,
  `_resolve_var` (cycle-safe). Inline `style=""` custom props
  cascade to children; rulesheet `--*` not yet parsed (toy
  limitation, documented).
- **CSS `calc()` full semantics** — `css.CalcExpr` supports mixed
  %/px terms. `resolve_length` now takes an optional `ref_px`
  parameter; `layout.build_box` passes `available_w` through to
  children so `calc(100% − 20px)` resolves against the parent's
  content width.
- **`gui.PageInstance` event model** — hover/focus state, focus
  chain (tabindex traversal), `hit_test_at`, `set_hover`,
  `set_focus`, `focus_next`, `_reapply_interaction`, form
  state management (`_form_states`, `_toggle_checkbox`,
  `_set_form_value`).
- **`gui.TabBrowser` event bindings** — `<Tab>`/`<Shift-Tab>`
  keyboard focus navigation, `<Button-1>` click dispatch,
  `<Motion>` hover tracking, `<Return>` activation.
- **`toy_browser/scripting/web_api.py`** — Web API globals for the toy JS engine:
  `localStorage` / `sessionStorage` (in-memory, 5 MiB quota,
  `QuotaExceededError`), `WebSocket` (toy: no real I/O,
  `_simulate_message` test hook), `navigator`, `location`,
  `history`, `URL`, `URLSearchParams`.
- **Performance: P33.P1 super-fast path** — `layout.build_box`
  fast path now reads width/height/color/background directly
  from the `sn.props` dict (skipping method calls, `_resolve_var`,
  and `_edges_for` for the 3 common empty edge cases), gated by
  `_FAST_PATH_MAX_PROPS = 4` and the `_SPECIAL_PROPS` frozenset.
  `line-height` was added to `_SPECIAL_PROPS` so that divs with
  an explicit line-height go through the slow path.

### Tests

- 22 new P1 tests (`tests/test_web_api.py`): localStorage (5),
  WebSocket (3), navigator (2), location (3), history (1),
  URL/URLSearchParams (6), JS integration (2), idempotency (1).
  Test count: 754 → **776**.

---

## P33.R5 — dirty-subtree layout + region re-rasterize

*Completed in a prior milestone (754/754 tests at R5 completion).*

### Added

- `LayoutBox.dirty` flag, `layout.mark_dirty`, `layout_dirty_only`,
  `layout.clear_caches`, `_child_index_cache`,
  `rasterize_region`, `Bitmap.pixel_at`, `text._metrics_memo`,
  `font._cjk_fallback_resolved`, `build_box` fast path with
  `_SPECIAL_PROPS` / `_FAST_PATH_EXCLUDED_TAGS`.

### Tests

- 9 new R5 tests. Test count: 745 → **754**.

---

## P33.R1–R4 — Roadmap milestones (completed)

*Details in `docs/ROADMAP.md`.*

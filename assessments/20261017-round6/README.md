# 20261017 — 第六轮：批次 1 P0 实施记录

> 日期：2026-10-17
> 基线：第五轮提交 `956220e` 的包结构（`toy_browser/`），远程最新 `5cfd0a9`。
> 依据：`assessments/20261016-round5/docs/` 的 [逐项改进实施方案](../20261016-round5/docs/逐项改进实施方案.md) 批次 1（基本操作：G01 / G02 / G04 / G05 / C01）。

## 本次完成的工作项

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G01 滚动条协议与范围 | ✅ | `toy_browser/app/gui.py` `handle_scroll_command` + 拇指公式 | `tests/test_gui_scroll.py`（14 条） |
| G02 鼠标与键盘统一激活 | ✅ | `activate_element` / `PageInstance.activate_focused_keyboard` | `tests/test_gui_activation.py`（4 条） |
| G04 焦点路由 | ✅ | `toy_browser/app/foci.py` `FocusManager` + tabindex 两级排序 | `tests/test_focus_routing.py`（10 条） |
| G05 滚轮统一输入 | ✅ | `handle_wheel_event`（粗/精 delta 分流） | 随 G01 测试 |
| C01 Document 版本骨架 | ✅ | `toy_browser/app/document_state.py` `DocumentVersion` | 版本单调性已验证 |

## 批次 2 完成的工作项（用户指定：G03 / G06 / G07 / C02 / C03）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G03 编辑、选区与输入法 | ✅（纯逻辑核心） | `toy_browser/app/editor.py` `EditorState` | `tests/test_editor.py`（16 条） |
| G06 动态视口、DPI 与缩放 | ✅（坐标契约 + 合并器） | `toy_browser/app/viewport.py` `ViewportState` / `ResizeCoalescer` | `tests/test_viewport.py`（10 条） |
| G07 UI 与文档工作分离 | ✅（有界命令队列 + 任务版本丢弃骨架） | `toy_browser/app/document_controller.py` | `tests/test_document_controller.py`（10 条） |
| C02 连续像素缓冲区及呈现 | ✅（数据契约 + PPM 批量打包 + 缓冲池） | `toy_browser/rendering/cpu_image.py` | `tests/test_cpu_image.py`（6 条） |
| C03 行内格式化与文字布局 | ⏳ 未实施（需先冻结 Edge 几何对照，见下） | — | — |

## 批次 3 完成的工作项（G08 / G07 补全）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G08 导航状态、停止与错误 | ✅ | `toy_browser/app/navigation_state.py` `NavigationState` | `tests/test_navigation_state.py`（10 条） |
| G07 命令队列 + 任务丢弃 | ✅（批次 2 已落骨架，批次 3 补 drain / apply 门控） | `toy_browser/app/document_controller.py` | 随 G07 测试 |

## 批次 4 完成的工作项（G09 标签页与历史会话）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G09 step 1 稳定标签身份 | ✅ | `toy_browser/app/tab_identity.py` `TabIdentityRegistry` | `tests/test_tab_identity.py`（5 条） |
| G09 step 5 会话持久化 | ✅ | `toy_browser/app/session_store.py` `SessionStore` | `tests/test_tab_identity.py`（9 条） |
| G09 step 3 统一历史提交入口 | ⏳ 未实施（见下） | — | — |

## 批次 2 关键实现说明

### G03 编辑状态机（纯逻辑核心）
- `EditorState`：node 标识 + value + selection_start/end + caret +
  composition_range（IME 组合区）+ undo/redo + dirty + password 显示
  分离。`apply_input(InputEvent)` 按 beforeinput（可取消）→ value
  更新 → input 顺序派发；`commit_change` 在失焦 / 提交时触发一次
  change（不为每键重复）。
- IME 三阶段：`ime_composition_start` / `ime_composition_update`（组合
  中间态不派发 input）/ `ime_composition_commit`（最终汉字一次性写入
  并派发 input），取消走 `deleteByComposition`。
- 文本编辑命令：左右 / Home / End、Backspace / Delete / 选区删除、
  粘贴替换选区、undo/redo；脚本改值按编辑 / 非编辑两态同步
  （`sync_from_dom`）。
- **未实施**：Tk 原生 Entry/Text 覆盖层（G03 step 2，需活显示环境）；
  剪贴板 / IME 人工验收（需真实键盘 + 系统 IME）。

### G06 视口 / DPI / 缩放
- `ViewportState` 统一 css/ui 像素、device_scale、page_zoom、scroll 的
  换算：`css_to_ui` / `ui_to_css` / `logical_to_css`（G06 step 3 的
  坐标公式），`set_size` / `set_zoom`（围绕锚点）/ `clamp_scroll`。
- `ResizeCoalescer`：连续 resize 合并，合并窗口（首个 offer 起算）
  ≥ 16ms 才提交最新尺寸（G06 step 1）。
- **未实施**：Tk `Configure` 绑定到重排、命中树 / 编辑覆盖层 /
  scrollbar 的随动（需活显示环境）。

### C02 连续像素缓冲区
- `CPUImage`（RGB8 连续 `bytearray` 缓冲区 + stride）；
  `from_bitmap` 用 `struct.pack` 逐行批量打包（0xRRGGBB → RGB PPM
  字节序，与 legacy `Bitmap.save_ppm` 逐字节一致）；
  `to_ppm_bytes` 供 Tk `PhotoImage` 直接消费，不再逐像素创建临时
  bytes（第五轮实测 800×600 转换链 ~1209ms 的根因路径）。
- `ImagePool`：按尺寸复用 + 清零 + 有界（帧所有权由 C01 版本保证）。
- 已接入 `TabBrowser._bitmap_to_photopix`（Tk 呈现走 CPUImage 路径）。
- **未实施**：填充 / 混合 / 图片 / 字形逐阶段迁移到连续缓冲区
  （批次 3）；独立进程 + 共享内存槽（可选 GPU 方案）。

## 批次 3 关键实现说明

### G08 导航状态机（`navigation_state.py`）
- `NavigationState` 六态：`idle / loading / interactive / complete /
  failed / cancelled`，记录 current/final URL、错误分类、进度、
  epoch。完成 / 失败 / 取消**分别**更新（不统一写 "Loaded"）；
  `ui_label()` 按态映射状态栏文本，`stop_button_visible` 驱动刷新
  按钮变停止。
- `cancel_request()` 只在 busy（loading/interactive）时生效：先逻辑
  取消（UI 立即反馈 + 置 `cancel_requested`），transport 在下一可
  取消点收尾（G08 step 3，不强杀线程）。
- `accept_result(epoch, ok)` 门控异步结果：旧 epoch / 已取消的结果
  直接丢弃（A 文档的结果不写入 B 文档，与 G07 配合）；成功 →
  COMPLETE，失败 → FAILED + 错误分类（与 `net.categorize_fetch_error`
  词表对齐，`describe_error` 出中文摘要）。

### G07 有界命令队列与任务丢弃（`document_controller.py`）
- `CommandQueue`：navigate / input / stop 按序保留（`max_pending`
  有界，超限拒绝）；resize / scroll **同类**合并只留最新（旧 resize /
  scroll 被顶掉，G07 step 5）。
- `DocumentController.navigate()` 分配 epoch + bump C01
  `navigation_epoch`/`document_id`；`stop()` 先翻 CANCELLED 再投
  stop 命令；`drain()` 取出全部在途命令产出 `Result`（取消时丢弃）。
- `Result` 携带 C01 `document_id`/`frame_id`；`apply_results()` 用
  `Result.is_stale` + `NavigationState.accept_result` 双门控——过期
  帧 / 旧 epoch / 已取消的结果不写 DOM、不刷画布（G07 step 6）。
- **本批为骨架**：内置同步工作单元（about:/本地成功 stub，http(s)
  占位不实际拉取）。真实的网络 I/O 移出主线程 / 文档进程、可取消
  transport（G08 step 3 的 socket 收尾）是批次 4 的后续实施——本批
  先把命令契约、有界队列与版本丢弃逻辑落好并测试。

## 批次 4 关键实现说明

### G09 step 1 稳定标签身份（`tab_identity.py`）
- `TabIdentityRegistry` 把 **稳定 tab_id** 与**可变 idx** 解耦：
  `allocate()` 分配递增 tab_id（关闭后不复用，避免旧资源 / 回调串页）；
  `invalidate(idx)` 关闭时标记 tab_id 失效并重建槽位；`compact()` 返回
  "关闭前旧 idx → 关闭后新 idx" 映射供 widget / 闭包重绑，旧回调经
  `valid_idx` 检查后 no-op（G09 step 1：不操作别的标签）。
- 每标签资源（DocumentController / 历史 / 滚动 / 焦点 / 任务）以
  tab_id 为 key（G09 step 2）：切换标签只改前台呈现，不重建文档；
  `release_tab` 在关闭时释放资源（G09 step 4）。

### G09 step 5 会话持久化（`session_store.py`）
- `SessionStore.save` 仅保存**可恢复状态**（url / title / back / fwd /
  scroll / zoom / active，不含 DOM / 位图 / 脚本 Host），经
  `<path>.tmp` + `os.replace` 原子写（复用 session.py 同款原子写语义，
  崩溃中途不写坏文件）。
- `SessionStore.load` 损坏可降级：文件缺失 / 非对象 / 未知版本 /
  `tabs` 非 list → 空会话；单 tab 字段缺失 / 类型错 → 逐字段默认值
  （不整份丢弃）；无 url 的 tab 跳过；活动标签指向不存在 tab_id →
  回退首个标签。
- `TabIdentityRegistry.snapshot` / `restore` 负责可恢复状态 ↔ 磁盘
  快照的序列化往返。

## 本批未完成 / 阻塞项

- **C03 行内格式化与文字布局**：需先将最小失败页面拆为普通文本 /
  b/i/span / 链接 / 连续空白 / 中文拉丁混排 / 长单词，并冻结 Edge
  几何与截图（C03 step 1），本批未执行该冻结步骤，故未实施
  InlineRun/LineBox。
- **G07 网络 I/O 外移 / 文档进程**：本批落命令队列与版本丢弃骨架；
  把网络 fetch 真正移出 UI 线程、实现可取消 transport 是后续批次。
- **G09 step 3 统一历史提交入口**：本批落稳定身份（step 1）与会话
  持久化（step 5）；把地址栏 / 链接 / 书签 / 文件打开的导航历史提交
  收敛到单一入口并明确重定向只形成一个历史项，属 Tk 侧路由改造，
  需活显示环境验收，留作后续批次。

## 剩余限制（下一批次）

- G10 工具栏 / 状态栏 / 诊断区、G11 可访问性、G09 step 3 统一历史
  提交入口。
- G07 线程 / 进程分离（网络 I/O 外移 + 可取消 transport）、G08
  transport 收尾（socket 关闭/超时不强杀线程）。
- C01 完整 `DocumentState` 抽取（DOM 树 + Host + 策略 + 子资源注册表
  合一）、C03 文字布局（需先冻结 Edge 几何对照）、C04 表格表单、
  C05 样式缓存、C02 填充 / 混合 / 图片 / 字形逐阶段迁移到连续
  缓冲区。
- 滚轮 / 滚动条 / G03 编辑覆盖层 / G06 resize 的 Tk 实测需有显示
  环境执行；本批回归覆盖纯逻辑与协议公式。
- 百度等真实网站可用性仍受 B01–B10 阻断（JS 多变量声明、location
  语义、错误分类），不在本批范围。

## 回归数字

| 验证项 | 结果 |
|---|---|
| `py -3 test.py` | **1033/1033 通过**（基线 939 + 批次 1 新增 28 + 批次 2 新增 32 + 批次 3 新增 20 + 批次 4 G09 新增 14） |

## 关键实现说明

### G01 滚动条协议
- Tk scrollbar 的 `command` 回调协议字符串是 `"moveto"` / `"scroll"` / `"set"` / `"none"`；legacy 用 `"movements"` / `"units"` 匹配，拖动永远不命中。已改为 `handle_scroll_command(tab_id, operation, *args)`，moveto 按 `fraction × content_height` 计算（不是 `× scroll_max`），scroll 区分 units（40px/步）与 pages（0.9 × 视口高）。
- 拇指 `first = offset / content_height`，`last = (offset + viewport_h) / content_height`，内容不溢出时 `(0, 1)`。

### G02 统一激活
- `TabBrowser.activate_element(page, node, idx, source)`：先对共享 Host 执行 `execute_event_bubbled(el, "click")`（脚本 onclick 先于默认动作），再按控件类型走默认动作表（a → 导航；checkbox/radio → toggle；text/number/submit/button → 聚焦；select → 记录）。鼠标（`_click_dispatch`）与键盘（`_activate_focused`）共用同一入口，消除 legacy "Enter 不执行 onclick" 的 SAFE/CLICKED 失败。
- 无 Tk 路径：`PageInstance.activate_focused_keyboard()` 供评估探针与单元测试驱动。

### G04 焦点路由
- `foci.FocusManager` 把键盘事件按当前焦点归属（chrome / document / editor / dialog）分流：地址栏 Return 只导航、不误激活页面按钮；Tab / Shift+Tab 仅在 document 归属时进页面 focus_chain。
- `_build_focus_chain` 改为两级排序：显式正 tabindex 数值优先（平局按文档顺序），隐式可聚焦（无 tabindex 的表单控件 / a[href]）按文档顺序跟随其后；tabindex="-1" 排除出 Tab 链（G03 预留脚本聚焦入口）。

### G05 滚轮
- `handle_wheel_event(tab_id, delta)`：`|delta| ≥ 120` 粗轮（40px/档），`0 < |delta| < 120` 高精度触控板（10px/单位）；绑定到 label 与 viewport 双层，鼠标在页面文字 / 空白 / 控件上均可滚动。

### C01 版本骨架
- `DocumentVersion` 四个单调计数器：`document_id`（新文档加载）、`navigation_epoch`（导航请求，含错误页）、`layout_revision`（restate / scroll / resize）、`frame_id`（光栅化帧）。`is_stale_frame` / `is_stale_layout` 供 G07 / G08 的任务队列丢弃过期帧；`reset_version_sequences()` 测试钩子。

## 剩余限制（下一批次）

- G08 导航状态 / 停止 / 错误、G09 标签页与历史会话、G10 工具栏 /
  状态栏 / 诊断区、G11 可访问性。
- G07 UI 与文档工作分离（有界命令队列 + 线程分离）依赖 G08 与
  C01 完整 `DocumentState` 抽取。
- C01 完整 `DocumentState` 抽取（DOM 树 + Host + 策略 + 子资源注册表
  合一）、C03 行内格式化与文字布局（需先冻结 Edge 几何对照）、
  C04 表格表单、C05 样式缓存、C02 填充 / 混合 / 图片 / 字形逐阶段
  迁移到连续缓冲区。
- 滚轮 / 滚动条 / G03 编辑覆盖层 / G06 resize 的 Tk 实测需有显示
  环境执行；本批回归覆盖纯逻辑与协议公式。
- 百度等真实网站可用性仍受 B01–B10 阻断（JS 多变量声明、location
  语义、错误分类），不在本批范围。

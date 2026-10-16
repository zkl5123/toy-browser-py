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

## 批次 5 完成的工作项（G07 网络 I/O 外移 + G08 可取消 transport）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G07 step 3 网络 I/O 移出 UI 调用点 | ✅ | `toy_browser/app/transport.py` `CancellableTransport` + `DocumentController.enable_cancelable_network` | `tests/test_transport.py`（9 条） |
| G08 step 3 可取消 transport（不强杀线程） | ✅ | `transport.py` `cancel()` + `_cooperative_sleep` 可取消点 | 同上 |
| G07 可取消网络工作单元接入 | ✅ | `document_controller.py` `navigate` 走工作线程 + `collect_network_results` | `tests/test_document_controller.py`（+3 条） |

## 批次 6 完成的工作项（G10 工具栏 / 状态栏 / 诊断区，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G10 step 4/5 诊断区（记录 + 导出） | ✅ | `toy_browser/app/diagnostics.py` `DiagnosticsLog` / `DiagEvent` | `tests/test_diagnostics.py`（19 条） |
| G10 step 1/2/3 工具栏 / 状态栏 / 标题映射 | ✅ | `diagnostics.py` `toolbar_state` / `status_text` / `tab_title` / `security_note` | 同上 |

## 批次 7 完成的工作项（C01 完整 DocumentState + G11 键盘全流程，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C01 完整 DocumentState 抽取 | ✅ | `toy_browser/app/document_state_full.py` `DocumentState`（DOM + Host + 策略 + 资源 + 稳定 node_id + 生命周期入口） | `tests/test_document_state_full.py`（8 条） |
| G11 键盘全流程纯逻辑 | ✅ | `toy_browser/app/accessibility.py` `FocusViewport` / `mouseless_tasks` / `focus_action` / `KeyboardSession` | `tests/test_accessibility.py`（24 条） |

## 批次 8 完成的工作项（G09 step 3 统一历史提交入口，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G09 step 3 统一历史提交入口 | ✅ | `toy_browser/app/history_entry.py` `HistoryCommitter` / `HistoryEntry` | `tests/test_history_entry.py`（12 条） |

### G09 step 3 统一历史提交入口（`history_entry.py`）
- `HistoryCommitter.commit(url, source, final_url, dedup)` 是**唯一**的
  历史提交入口（地址栏 Go / 链接 / 书签 / 文件打开都接同一入口，
  来源用 `SRC_ADDRESS`/`SRC_LINK`/`SRC_BOOKMARK`/`SRC_FILE` 标签区分）。
- **一次导航 = 恰好一个历史项**：携带 *最终* URL（重定向链走完落到的
  地址）+ `start_url`（发起 URL，诊断用），中间 3xx 跳转**不**建独立
  历史项（G09 step 3 "明确重定向只形成哪一个历史项"）。
- **前进/后退是游标移动**（`back`/`forward` 不新增项）；新导航清空
  cursor 之后的前进栈（标准浏览器语义，无历史分叉）。
- 同址去重：cursor 指向项 URL == 最终 URL 时不新增，原地更新 source。
- `can_back`/`can_forward`/`current` 供工具栏按钮启停 + 状态栏（G10
  的 `toolbar_state` 消费这里）。
- **纯逻辑核心**：GUI/CLI/测试都接同一入口；Tk 侧把
  `PageInstance.back_stack/fwd_stack` 迁移到 `HistoryCommitter`（保留
  旧调用兼容）属批次 8 的 Tk 路由改造，留后续批次（需活显示验收）。

## 批次 9 完成的工作项（G09 step 2+4 标签生命周期 / C01 step 4 公共 API 包装层，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G09 step 2 每标签状态所有权 | ✅ | `toy_browser/app/tab_lifecycle.py` `TabPresentation` / `TabPresentationSet`（切换只改前台呈现，不重建文档） | `tests/test_tab_lifecycle.py`（14 条） |
| G09 step 4 后台降重绘 + 定时器策略 / 关闭取消任务 | ✅ | `tab_lifecycle.py` `TabTasks` / `TabTaskGate` / `TabLifecycle`（关闭标签取消在途任务 + 释放资源记账 + 失效 tab_id） | 同上 |
| C01 step 4 公共 API 包装层 | ✅ | `toy_browser/app/document_renderer.py` `render_document_state` / `render_document_state_restate`（渲染管线接同一 `DocumentState` 生命周期入口，旧 `browser.render_document` 保持兼容） | `tests/test_document_renderer.py`（6 条） |

### G09 step 2+4 标签生命周期（`tab_lifecycle.py`）
- `TabPresentation`（每标签前台呈现）：`active` / `repaint`（全量/降重绘）
  / `timer`（正常/节流/暂停）。**不持有** DocumentState / 历史 / 任务
  ——那些以 tab_id 存 registry（G09 step 2）。`TabPresentationSet.activate`
  实现"切换只改前台呈现，不重建文档"：激活标签全量重绘 + 正常定时器，
  其余后台降重绘 + 定时器节流。
- `TabTasks` / `TabTaskGate`（G09 step 4 任务门控）：`close(tab_id)` 取消
  该标签所有在途任务 + 标记 CPU/GPU 资源已释放；后台标签 `background`
  把定时器降到节流派发（不取消任务——后台仍加载，只是减少重绘 / 派发
  频率）；`cancelled` 记录供诊断区审计。
- `TabLifecycle`（step 1+2+4 组合协议）：`new_tab` 分配稳定 tab_id + 挂
  呈现 + 任务门控（首个默认前台）；`activate` 前台切换（不触碰各标签的
  文档/历史/在途任务）；`close_tab` 取消任务 + 释放资源 + 经
  `TabIdentityRegistry.invalidate` 失效 tab_id（旧回调 / 旧资源不串页），
  关闭前台标签后把前台让给剩余标签。

### C01 step 4 公共 API 包装层（`document_renderer.py`）
- `render_document_state(doc, html, ...)` 在 `DocumentState` 上跑
  "解析 → 脚本初始化 → 样式 → 布局 → 光栅化"，每阶段按 C01 规则 bump
  正确版本：解析+脚本初始化经 `new_document`（计数 +1，仅一次）；
  样式+布局经 `mark_restate`（只 bump layout_revision，不重建 Host /
  不重跑脚本）；光栅化经 `mark_render`（只 bump frame_id）。
- `render_document_state_restate(doc, ...)` 复用已有 doc 的 DOM/Host
  重排 + 渲染（hover / resize 场景），`script_init_count` 保持不变。
- 旧公共 API（`browser.render_document` 等）保持签名 / 行为不变
  （兼容旧调用）；包装层是 CLI / 测试接"同一流程"的新入口，对相同
  输入与旧 API 得到同一 DOM 指纹 + 同一位图（C01 验收
  `assert_same_dom` 已验证）。GUI 侧把 `PageInstance` 也接到这里属
  活显示环境批次。

### C01 完整 DocumentState（`document_state_full.py`）
- `DocumentState` 持有 C01 骨架的 `DocumentVersion`（四计数器唯一持有者）
  + 文档策略 `policy` + 源 `base_url` + DOM/Host + 子资源注册表 +
  稳定 node_id。生命周期入口固化阶段失效规则：
  - `new_document` — 解析 + 脚本初始化（`script_init_count` +1）；
  - `mark_restate` — hover/focus/scroll/resize 只 bump `layout_revision`，
    **不重建 Host、不重跑脚本**（验收"脚本初始化只增加一次、重排后
    点击监听仍在"：node_id 跨重排不变 + Host 对象不变）；
  - `mark_render` — 光栅化只 bump `frame_id`；
  - `new_context` — 新导航创建**新**上下文，旧对象 `disposed=True`，
    策略 / JS 全局 / 资源**不跨源复用**（A 的帧对 B 全 stale）。
- `assert_same_dom` — CLI / GUI 相同输入同一 DOM 验收：比对两棵树的
  标签 / 属性 / 文本指纹（不含 node_id 实现细节）。

### G11 键盘全流程（`accessibility.py`）
- `FocusViewport.scroll_for_focus` — 焦点滚入可见区域纯公式（已可见
  不跳、顶部越上对齐顶部、底部越下对齐底部、clamp 到 scroll_max、
  留 8px padding）。
- `mouseless_tasks(a11y_tree, tab_order)` — 复用现有
  `browser.collect_accessibility_tree` + `browser.tab_order`，生成
  "无鼠标任务"清单（每个 focusable 元素 `{id, role, name, action}`，
  供键盘全流程回归 / 无障碍审计）。
- `focus_action(role, key)` — 焦点元素上 Enter/Space/Escape 默认动作
  （link/button → activate，textbox/combobox → commit，escape →
  dismiss）：键盘可独立完成与鼠标点击等价的默认动作。
- `KeyboardSession` — chrome / document / editor / dialog 四焦点域
  键盘状态机（tab / shift_tab / enter / escape / 域切换），
  Tab 从 chrome 进 document 首位，Shift+Tab 首位回 chrome，
  循环移动不越界。

## 本批未完成 / 阻塞项

- **G11 Tk 绑定**：本批落焦点滚动公式 + 无鼠标任务清单 + 键盘状态机
  的纯逻辑核心；真实 :class:`tk.Text`/Entry 覆盖层的键盘绑定、焦点
  可见性的画布滚动需活显示环境验收，留作后续批次。
- **C01 公共 API 包装层 GUI 接入**：本批（批次 9）落地渲染管线包装层
  `document_renderer.py`（CLI/测试接同一 `DocumentState` 生命周期入口，
  旧 `browser.render_document` 保持兼容）；把 GUI 的 `PageInstance`
  也接到同一入口属活显示环境批次，留后续。
- **G09 step 2+4 Tk 侧路由**：本批（批次 9）落地标签生命周期纯逻辑
  核心（`tab_lifecycle.py`：切换只改前台呈现不重建文档、关闭取消任务
  + 释放资源 + 失效 tab_id、后台降重绘 + 定时器节流）；Tk widget 重绑
  / 画布降重绘 / 定时器实测需活显示环境验收，留后续。
- **C03 行内格式化与文字布局**：需先冻结 Edge 几何与截图
  （C03 step 1），本批未执行该冻结步骤。
- **G07 文档进程方案**：可取消网络工作单元 + 线程初版；把 CPU
  工作（DOM / 布局）移入文档进程是后续（纯 Python 线程无多核收益）。

## 剩余限制（下一批次）

- G10 Tk 诊断区布局 / 控件绑定、G11 Tk 键盘绑定 / 焦点画布滚动、
  G09 step 2+4 Tk 侧路由、C01 公共 API 包装层 GUI 接入、
  G07 文档进程（CPU 工作移出 UI 线程的多核方案）。
- C03 文字布局（需先冻结 Edge 几何对照）、C04 表格表单、C05 样式
  缓存、C02 填充 / 混合 / 图片 / 字形逐阶段迁移到连续缓冲区。
- 滚轮 / 滚动条 / G03 编辑覆盖层 / G06 resize / G10 诊断区 / G11
  键盘 / G09 标签切换的 Tk 实测需有显示环境执行；本批回归覆盖纯逻辑
  与协议公式。
- 百度等真实网站可用性仍受 B01–B10 阻断（JS 多变量声明、location
  语义、错误分类），不在本批范围。

## 回归数字

| 验证项 | 结果 |
|---|---|
| `py -3 test.py` | **1128/1128 通过**（基线 939 + 批次 1 新增 28 + 批次 2 新增 32 + 批次 3 新增 20 + 批次 4 G09 新增 14 + 批次 5 G07/G08 可取消网络新增 12 + 批次 6 G10 诊断/工具栏纯逻辑新增 19 + 批次 7 C01 完整 DocumentState + G11 键盘全流程新增 32 + 批次 8 G09 step 3 统一历史入口新增 12 + 批次 9 G09 step 2+4 标签生命周期 / C01 step 4 公共 API 包装层新增 20） |

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

### G07 网络 I/O 外移 + G08 可取消 transport（`transport.py` + 控制器接线）
- `CancellableTransport`：一次 fetch 任务。`run(fetch, url)` 同步或
  `async_=True` 投工作线程；`cancel()` 立即置取消标志（UI 侧"立即
  反馈"），工作线程在**下一个可取消点**（`_cooperative_sleep` 的
  分片检查点 / 回调内检查点）收尾——**不强杀 Python 线程**
  （G08 step 3）。
- `TransportResult` 携带 C01 `document_id` / `frame_id` + 导航
  `nav_epoch`；`cancel` 后 `cancelled=True`，主线程取回后由
  `NavigationState.accept_result` / `DocumentVersion.is_stale_frame`
  门控丢弃（过期完成事件无效）。
- `DocumentController.enable_cancelable_network(fetch_fn)` 打开可取消
  网络工作单元：http(s) 导航经 transport 在工作线程里执行（G07
  step 3 初版：网络 I/O 移出 UI 调用点），主线程经
  `collect_network_results` + `apply_results` 取回；`about:` / inline /
  本地文件仍走内置同步 stub。取回时重新盖戳当前 C01 帧版本（worker
  线程 bump frame 后旧帧过期，避免误判 stale）。
- **受控慢服务器可测**：注入分片 sleep 的 fake fetch 回调（不依赖
  真实网络 / 活显示）——Stop 可立即反馈、取消后在途结果被丢弃、
  快速 A→B 导航的旧结果因 C01 帧更新被丢弃。
- **本批为线程初版**：纯 Python 线程无多核收益（G07 step 4 明确），
  文档进程方案（DOM 长期留在文档进程、只传紧凑命令与帧）留作后续。

## 批次 6 关键实现说明

### G10 工具栏 / 状态栏 / 诊断区（纯逻辑核心，`diagnostics.py`）
- `DiagnosticsLog`（G10 step 4/5）：诊断事件流。每条 `DiagEvent` 记录
  `document_id`（C01 文档身份）/ `phase`（nav/parse/layout/paint/
  script/subresource/cache/fallback…）/ `backend`（默认 cpu，文档明确
  GPU 为可选第三方）/ `ts_ms`（渲染计时）/ `detail`（缓存命中与
  fallback 原因）。`for_document` 按文档过滤（诊断区展开时才渲染，
  普通使用不堆放内核信息，step 4）；`duration_ms` 出各 phase 的
  首/末时刻 + 总耗时（渲染计时）；`export` 导出文本 / JSON（"导出
  诊断信息"按钮的数据源，step 5）；`summary` 出缓存 / fallback 摘要。
- 工具栏 / 状态栏 / 标题映射（G10 step 1–3 纯函数，Tk 控件共用）：
  - `toolbar_state(nav, can_back, can_forward)`：后退/前进按历史栈
    启停；刷新/停止随 G08 状态切换（busy → "停止"/stop，否则
    "刷新"/reload），**不同加载状态按钮一致**。
  - `status_text(nav, hover_url)`：悬停链接目标 > 真实加载阶段 >
    失败摘要（区分完成/失败/取消，不统一写 "Loaded"）；失败用
    `describe_error` 的中文摘要（DNS/超时/HTTP/…）。
  - `tab_title(nav, doc_title, loading)`：标题用文档 title，加载前
    临时显示 URL（step 2）。
  - `security_note(url, https_verified)`：不以 HTTPS 图标代替页面
    整体安全结论——http 明示"未加密"，https 未完全校验明示"证书未
    完全校验"（step 3）。
- **本批为纯逻辑核心**：Tk 侧（工具栏 / 状态栏 / 可展开诊断区的
  布局、控件绑定与"导出诊断信息"按钮）在 GUI 层消费这些纯函数，
  需活显示环境验收，留作后续批次。

## 本批未完成 / 阻塞项

- **C03 行内格式化与文字布局**：需先将最小失败页面拆为普通文本 /
  b/i/span / 链接 / 连续空白 / 中文拉丁混排 / 长单词，并冻结 Edge
  几何与截图（C03 step 1），本批未执行该冻结步骤，故未实施
  InlineRun/LineBox。
- **G07 文档进程方案**：可取消网络工作单元 + 线程初版（网络
  I/O 移出 UI 调用点 + 取消语义）；把 CPU 工作（DOM / 布局）移入
  文档进程是后续（纯 Python 线程无多核收益）。
- **G09 step 3 Tk 侧路由改造**：本批（批次 8）落统一历史提交入口的
  纯逻辑核心（`history_entry.py` `HistoryCommitter`，唯一入口 +
  重定向只形成一个历史项 + 游标移动不清前进栈）；把 GUI/CLI/测试
  接到同一入口、迁移 `PageInstance.back_stack/fwd_stack` 到
  `HistoryCommitter`（保留旧调用兼容）属 Tk 侧路由改造，需活显示
  环境验收，留作后续批次。
- **G10 Tk 布局 / 控件绑定**：本批落诊断记录 + 工具栏 / 状态栏 /
  标题 / 安全摘要的纯函数映射；Tk 可展开诊断区布局与"导出诊断
  信息"按钮的 GUI 绑定需活显示环境，留作后续批次。

## 剩余限制（下一批次）

- G09 step 3 Tk 侧路由改造（历史提交入口接入 GUI/CLI/测试）、
  G10 Tk 诊断区布局 / 控件绑定、G11 Tk 键盘绑定 / 焦点画布滚动、
  G07 文档进程（CPU 工作移出 UI 线程的多核方案）。
- C01 公共 API 包装层接入（GUI/CLI/测试接同一入口，保留旧调用兼容）、
  C03 文字布局（需先冻结 Edge 几何对照）、C04 表格表单、C05 样式
  缓存、C02 填充 / 混合 / 图片 / 字形逐阶段迁移到连续缓冲区。
- 滚轮 / 滚动条 / G03 编辑覆盖层 / G06 resize / G10 诊断区 / G11
  键盘的 Tk 实测需有显示环境执行；本批回归覆盖纯逻辑与协议公式。
- 百度等真实网站可用性仍受 B01–B10 阻断（JS 多变量声明、location
  语义、错误分类），不在本批范围。

## 回归数字

| 验证项 | 结果 |
|---|---|
| `py -3 test.py` | **1108/1108 通过**（基线 939 + 批次 1 新增 28 + 批次 2 新增 32 + 批次 3 新增 20 + 批次 4 G09 新增 14 + 批次 5 G07/G08 可取消网络新增 12 + 批次 6 G10 诊断/工具栏纯逻辑新增 19 + 批次 7 C01 完整 DocumentState + G11 键盘全流程新增 32 + 批次 8 G09 step 3 统一历史入口新增 12） |

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

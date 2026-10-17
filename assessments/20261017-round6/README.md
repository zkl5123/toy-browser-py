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

## 批次 10 完成的工作项（C04 表格/表单呈现模型 + G10 step 4/5 诊断区展开收起，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C04 步骤 3 表单控件统一呈现模型 | ✅ | `toy_browser/app/form_control.py` `ControlModel` / `display_text` / `paint_geometry`（text/password/checkbox/radio/file/select/button/textarea 的当前值 vs 初始值区分、统一控件几何） | `tests/test_form_control.py`（20 条） |
| C04 步骤 1/2 表格列宽/行高纯逻辑求解 | ✅ | `form_control.py` `TableColumn` / `solve_table_widths` / `solve_row_heights` / `table_total_height`（显式列固定、auto 列 max-content、超宽按比例缩、border-collapse 共享边框减半、行高取最大单元格 border-box） | 同上 |
| G10 step 4/5 诊断区展开/收起 + 计时展示投影 | ✅ | `toy_browser/app/diag_panel.py` `DiagPanel`（默认收起状态机）/ `timing_table`（各阶段有序耗时行）/ `panel_lines`（人类可读面板行） | `tests/test_diag_panel.py`（7 条） |

### C04 表单控件统一呈现模型（`form_control.py`）
- `ControlModel.from_dom(node)` 从 DOM 节点读初始值：`initial_value` /
  `initial_checked` / `initial_selected`（DOM 属性，初始态）与 `value` /
  `checked` / `selected_index`（当前 DOM property，交互后更新）区分
  （C04 步骤 3 "初始 value/selected/checked 与当前 DOM property 区分"）。
  checkbox/radio 无 value 默认 "on"；select 收集 option 值 + 当前选中项；
  file input 带 file_name；button 带 label；textarea 多行 value。
- `display_text(m)` 读**当前值**呈现（不读初始值）：password 掩码（G03
  显示分离）、checkbox/radio 勾选标记、file 文件名、select 当前项、
  空 text 显示 placeholder。
- `paint_geometry(m)` 统一控件几何（内容盒 + padding + border + 文本
  位置 + 状态位），各控件分支不再重复（单一呈现数据源，C04 步骤 3）。
- 表格 `solve_table_widths`：显式列固定、auto 列 max-content（shrink-to-
  fit 不拉伸）、超宽按 max-content 比例缩回（min_content 下限）、
  border-collapse 共享边框减半（与 `layout.py` `_layout_table` 同规则）。
  `solve_row_heights` 行高取最大单元格 border-box；`table_total_height`
  collapse 模式下相邻共享行边框各加一次。

### G10 step 4/5 诊断区展示投影（`diag_panel.py`）
- `DiagPanel`：可展开诊断区状态机（默认收起，"普通使用不堆放内核实现
  信息"）；`toggle` / `show` / `hide`；展开时 `render(doc_id)` 返回展示
  行，收起时返回空。
- `timing_table`：把 `DiagnosticsLog.duration_ms` 的扁平键投影成有序展示
  行（phase/label/first_ms/last_ms/duration_ms），按渲染顺序（网络→缓存
  →解析→脚本→布局→光栅化）+ 合计行；单事件阶段耗时 0。
- `panel_lines`：人类可读文本投影（`[doc=…] backend=… 阶段计时` 首行 +
  各阶段行 + 合计行 + 缓存/fallback 摘要行），"导出诊断信息按钮"可直接
  消费（与 `DiagnosticsLog.export` 的原始事件流互补）。

## 批次 11 完成的工作项（C05 样式缓存键 + 失效范围 + 命中率统计，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C05 缓存键 + 失效范围 | ✅ | `toy_browser/app/style_cache.py` `StyleCacheKey`（stylesheet_revision + media + interaction，节点级不进全局键）/ `invalidate_scope` / `widen_scope` / `apply_invalidation` | `tests/test_style_cache.py`（18 条） |
| C05 各阶段命中率统计 | ✅ | `style_cache.py` `HitRateStats`（parse/match/cascade/property 命中 / 未命中计数 + rate + summary） | 同上 |
| C05 按 Document 管理 | ✅ | `style_cache.py` `document_cache_budget`（上限 4096 + document_destroy 清理约定） | 同上 |

### C05 样式缓存键 + 失效范围 + 命中率统计（`style_cache.py`）
- **不叠无失效条件的全局缓存**（C05 证据：css.py 已有 P34.1 规则表
  记忆化 + P34.5 candidate-rule cache）：本模块只是把现有散点缓存的
  键语义 / 失效条件 / 命中统计抽成可独立测试的纯逻辑决策层，css.py
  仍持有实际缓存实例。
- `StyleCacheKey`（C05 步骤 2）：明确缓存键 = `stylesheet_revision` +
  `media`（媒体条件快照）+ `interaction`（hover/focus 快照）；DOM 结构 /
  继承输入是**节点级**，不进全局键（步骤 3 "避免把一个节点的状态写到
  其他节点"）。frozen dataclass，hashable 可作 dict key。
- `invalidate_scope(change)`（C05 步骤 4）：改动类型 → 失效范围。
  stylesheet/font → `all`（全量）；class/id/style → `node`（仅节点）；
  hover/focus → `interaction`（仅交互）；media/resize → `media`；未知
  保守返回 `all`（正确性优先）。`widen_scope` 取并集（影响面最大者胜）。
- `HitRateStats`（C05 步骤 1）：各阶段（parse/match/cascade/property）
  命中 / 未命中计数 + `rate`（无样本返回 1.0 = 无重复工作）+
  `summary`（人类可读，诊断区 / 性能追踪消费，C05 交付"记录真实
  命中率和分段耗时"）。
- `apply_invalidation`：stylesheet/font 改动 bump revision；其余 scope
  键不变（节点级失效在节点维度处理）。
- `document_cache_budget`（C05 步骤 5）：按 Document 管理的缓存预算
  字典（上限 4096，与 css.py `_CANDIDATE_RULES_CACHE_MAX` 一致）+
  "document_destroy 时清"约定（节点释放后不依赖可复用的 Python id
  识别身份）。

## 批次 12 完成的工作项（C06 布局树构建与增量布局，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C06 失效等级分类 | ✅ | `toy_browser/app/layout_incremental.py` `InvalidateLevel`（PROMOTE 单调）/ `InvalidateEvent` / `invalidate_level` / `classify_invalidate` | `tests/test_layout_incremental.py`（15 条） |
| C06 最小可重排根求解 | ✅ | `layout_incremental.py` `MinimalRoot` / `promote_to_parent_level` / `find_minimal_root`（向上收集祖先 PROMOTE 等级，取最高者） | 同上 |
| C06 增量布局计划 | ✅ | `layout_incremental.py` `IncrementalLayoutPlan` / `build_layout_plan`（事件 → 重排范围，含 Flex/Grid、文本增高下移、复杂表格全容器 fallback） | 同上 |

### C06 布局树构建与增量布局（`layout_incremental.py`）
- **失效等级分类**（C06 步骤 3）：`classify_invalidate(change)` 把改动
  映射到失效等级：`text`（文本变化可能影响后续兄弟位置）→ `POSITION`；
  `flex`/`grid`（子项变化可能影响整个容器）→ `MEASURE`；`class`/`id`/
  `style`/`dom`/`font`（属性 / 结构变化）→ `STRUCTURE`；`hover`/`focus`
  → `PAINT`；`media`/`resize` → `MEASURE`。等级单调（`PROMOTE`）：
  `PAINT < POSITION < MEASURE < STRUCTURE`，低等级事件可被高等级吸收
  （不能只重排被改节点——文本增高要往下传导，Flex 子项要重排整个
  容器）。
- **最小可重排根求解**（C06 步骤 4）：`find_minimal_root(doc, events,
  ancestor_promotes)` 基于依赖关系**向上**确定最小可重排根：收集每个
  事件节点的祖先 PROMOTE 等级（由调用方传入，含 Flex/Grid 容器、自动
  尺寸容器、文档根），取所有事件的最高等级；若存在 ≥MEASURE 的祖先
  提升，重排根上移到该祖先；否则按事件等级定根（仅 PROMOTE 到
  事件自身所在节点）。`MinimalRoot.level` 决定重排深度。
- **增量布局计划**（C06 步骤 5）：`build_layout_plan(doc, events)` 把
  事件列表编译成 `IncrementalLayoutPlan`（`roots` + `level` +
  `fallback`）。**先对块流和固定尺寸容器启用增量，复杂表格 / 自动
  尺寸容器暂退全容器布局**：若任一事件落在表格内（`inside_table`）
  或落在自动尺寸容器（`inside_auto_container`），计划 `fallback=True`
  （全容器重排，正确性优先）；否则按 `level` 做块流 / 固定尺寸增量。
  缓存键含包含块尺寸与字体版本（C06 步骤 4）：`cache_key = (parent_size,
  font_version, level)`。

## 批次 13 完成的工作项（C07 紧凑显示列表与可见区域裁剪 + C08 脏区域分块与交互重绘，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C07 命令统计 + 保守 bounds + 资源 ID 化 | ✅ | `toy_browser/app/display_list.py` `CommandStats`（命令类型/可见比例/重复资源）/ `conservative_bounds`（含阴影核/描边/滤镜外扩）/ `ResourceTable`（资源 ID 化）/ `viewport_clip`（视口外裁剪，阴影投进不裁掉）/ `ir_payload`（类型化 IR） | `tests/test_display_list.py`（19 条） |
| C08 damage 生成 + tile 切分 + 回退判定 | ✅ | `toy_browser/app/dirty_region.py` `DamageEvent`/`DamageSet`（合并旧+新 bounds、效果边界扩展、固定 tile 切分、damage 过多/复杂变换/不确定依赖回退全视口）/ `InteractionCommitter`（hover/focus 只在状态变化时提交）/ `scroll_damage`（滚动只更新视口+新暴露区域）/ `TileCache`（按文档/布局/绘制版本失效） | `tests/test_dirty_region.py`（22 条） |

### C07 紧凑显示列表与可见区域裁剪（`display_list.py`）
- `CommandStats`（C07 步骤 1 "统计命令类型、可见比例、重复资源"）：
  按 kind 计数、可见比例、重复资源 ID 统计（`record(kind, visible,
  resource_id)` + `visible_ratio()` + `summary()`）。
- `conservative_bounds`（C07 步骤 2 "bounds 包含阴影核、描边、变换
  和裁剪影响，不能只取原始 rect"）：原始 rect + 阴影核（2×blur 覆盖
  高斯尾部）+ 描边外扩 + 滤镜模糊外扩，返回保守绘制影响盒
  `(min_x, min_y, max_x, max_y)`。
- `ResourceTable`（C07 步骤 3 "不可变资源引用改 ID，避免每条命令
  复制图片数据或大字典"）：资源登记一次返回稳定 ID，命令只持 ID；
  重复子树显示列表可按版本复用。
- `viewport_clip`（C07 步骤 4 + 验收 "阴影从视口外投进来不被错误
  裁掉"）：用保守 bounds（含阴影核）判视口相交；原始 rect 在视口外
  但阴影投进视口 → 不可跳过（`False`），完全在外 → 可跳过（`True`）。
- `ir_payload`（C07 交付 "类型化 IR"）：按 kind 收窄字段（rect/text/
  img/shadow/rounded 各取专属字段，img 用 resource_id 不复制像素；
  未知 kind 全字段透传，保守不漏字段）。

### C08 脏区域分块与交互重绘（`dirty_region.py`）
- `merge_bounds` / `expand_to_effect`（C08 步骤 1 "合并旧+新 bounds，
  向效果边界扩展"）：AABB 合并 + 阴影/描边/透明覆盖外扩。
- `DamageEvent` / `DamageSet`（C08 步骤 1/3/5）：`to_region()` 从旧+
  新 bounds 合并+效果扩展生成 `DamageRegion`；`tiled_regions` 固定
  tile 切分；`should_fallback()` 在 damage 过多（tile 数超阈值）/
  复杂变换 / 不确定依赖时回退**全视口**（C08 步骤 5 "不回退整页
  超大位图"）。
- `InteractionCommitter`（C08 步骤 2 "hover/focus 只在状态变化时
  提交，避免重复相同输入触发重建"）：记录上次 `(hover_id, focus_id)`
  状态，相同输入返回 `False`（无变化帧不重新绘制），变化返回 `True`。
- `scroll_damage`（C08 步骤 2 "滚动只更新视口与新暴露区域"）：视口 +
  新暴露区域并集（clamp 到可滚动内容），单纯滚动不触发 build_box。
- `TileCache`（C08 步骤 3 "按文档、布局和绘制版本失效"）：
  `(document, layout_revision, paint_revision)` 版本戳；版本不匹配
  的 tile 失效（重放前先查缓存恢复正确背景，再重放相交且顺序正确的
  图元，避免残影，C08 步骤 4）。

## 批次 14 完成的工作项（C09 PNG 编码与异步导出 + C10 策略 API 与事件循环扩大回归，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C09 连续行编码 + 分段计时 + 压缩参数 | ✅ | `toy_browser/app/png_export.py` `ContinuousRows`（连续 RGB 行，消除 int 列表遍历）/ `encode_rows` + `PngEncodePlan`（行打包 / 压缩 / 写盘分段计时，`level` 控制压缩率 vs 速度）/ `decode_pixels` + `pixels_equal`（字节与像素双层一致性） | `tests/test_png_export.py`（18 条） |
| C09 后台导出（不可变快照 + 取消 + 回 UI 通知） | ✅ | `png_export.py` `FrameSnapshot`（捕获冻结像素，交互帧不做 PNG）/ `BackgroundExporter`（pending/done/cancelled/failed，导出中导航 / 关页取消，完成回 UI 通知，写盘失败标记） | 同上 |
| C10 策略一致性矩阵 + nonce / 脚本 URL | ✅ | `toy_browser/app/policy_regression.py` `policy_matrix`（CLI/GUI/刷新/历史返回同策略校验）/ `check_nonce`（合法 / 不匹配 / 缺失 / 大小写）/ `resolve_script_url` + `dedupe_scripts`（相对 / 绝对 / 协议相对 / data / 去重） | `tests/test_policy_regression.py`（26 条） |
| C10 网络结果分类 + 观察器防无限触发 + 事件循环排空 | ✅ | `policy_regression.py` `classify_fetch`（正文/JSON、失败拒绝、重定向、超时、取消、凭据、CORS，拒绝路径不误当成功）/ `ObserverGuard` + `ResizeObserverGuard`（批量 / 截断 / 触发上限 / disconnect / 回调中不重触发 / resize 循环保护）/ `EventLoopScheduler`（嵌套 Promise 微任务排空、定时器内微任务、重复定时器取消、rAF 顺序、排空错误记录） | 同上 |

### C09 PNG 编码与异步导出（`png_export.py`）
- **不破坏默认字节契约**（C09 步骤 4）：`encode_rows(level=6)` 与
  browser.py `_png_encode_rows` 逐字节一致（同 IHDR / IDAT zlib /
  IEND 规则）；新增快速 / 高压缩模式（`level` 0–9）只改 IDAT 数据
  段，用 `decode_pixels` + `pixels_equal` 做**像素一致性**回归
  （"不能简单改掉字节金图掩盖错误"）。
- `ContinuousRows`（C09 步骤 1）：从位图按行切片读连续 RGB，`row_rgb`
  / `raw_rows`（filter-0 raw-row 负载，与 browser.py `_png_raw_rows`
  同字节序），消除逐像素 int 列表遍历。
- `PngEncodePlan`（C09 步骤 2）：`stage_timings` 分别记 pack /
  compress / write 耗时；`level` 参数在压缩率与速度间取舍。
- `FrameSnapshot` + `BackgroundExporter`（C09 步骤 3 + 验收）：导出
  捕获**不可变帧快照**（交互帧不进 PNG 路径），后台编码，完成 / 取消
  / 写盘失败分别回 UI 通知；导出中导航 / 关页 → 取消任务不写盘。

### C10 策略 API 与事件循环扩大回归（`policy_regression.py`）
- `policy_matrix`（C10 步骤 1）：CLI / GUI / 刷新 / 历史返回各入口的
  最终 URL / base / origin / CSP / 请求模式一致性矩阵，`mismatches`
  定位偏离字段与入口。
- `check_nonce`（C10 步骤 2）：合法 / 不匹配 / 缺失 / 区分大小写
  （默认敏感，可设不敏感）。
- `resolve_script_url` + `dedupe_scripts`（C10 步骤 2）：脚本 src 相对
  base_url 解析（相对 / 绝对 / 协议相对 / data / 空），重复标签去重
  （保持首现序）。
- `classify_fetch`（C10 步骤 3）：把 fetch/XHR 原始结果分类成
  `NetworkOutcome`（json / text / redirected / failed / timeout /
  cancelled / cors_denied）；`success` 属性保证**拒绝路径不误当成功**
  （4xx/5xx / 超时 / 取消 / CORS 拒绝 → `ok=False`）。
- `ObserverGuard` + `ResizeObserverGuard`（C10 步骤 4）：MutationObserver
  批量记录 + drain 截断 + 触发次数上限 + disconnect + 回调中不立即重
  触发（防无限递归）；ResizeObserver 尺寸不变不触发 / 布局变化触发 /
  单轮循环保护（防 resize→布局→resize 死循环）。
- `EventLoopScheduler`（C10 步骤 5）：嵌套 Promise 微任务 FIFO 排空、
  定时器内微任务、重复定时器取消（不再入队）、rAF 按调度顺序执行、
  排空错误记录到 `drain_errors`（对齐浏览器事件循环：宏任务后排空
  微任务）。

## 批次 15 完成的工作项（C02 连续像素缓冲区阶段 2–3 + C03 行内格式化与文字布局，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C02 多格式 + padding + 帧版本所有权 | 完成 | `toy_browser/rendering/cpu_image_formats.py`（`FrameImage` frame_version / owner / publish / snapshot + `bytes_per_pixel` / `padded_stride` / `row_offset` + `FormatConverter` 批量转换 / 透明合成） | `tests/test_cpu_image_formats.py`（13 条） |
| C03 行内格式化 + 文字布局 | 完成 | `toy_browser/rendering/inline_layout.py`（`InlineRun` / `LineBox` / `PlacedRun` + `WhitespaceFolder` 空白折叠 + `InlineLayout` 共享 advance / 可断点分行 / 基线定位 + `line_baseline`） | `tests/test_inline_layout.py`（14 条） |

### C02 连续像素缓冲区阶段 2–3（`cpu_image_formats.py`）
- **多格式 + padding**（C02 步骤 1/3）：`bytes_per_pixel`（RGB8=3 / RGBA8=4 / BGRA8=4）、`padded_stride(width, fmt, align)` 行 padding 对齐（GPU 常用 4/32/64 字节对齐）、`row_offset` 行首偏移。
- **帧版本 + 读写所有权**（C02 步骤 4）：`FrameImage` 带 `frame_version` + `owner`；写侧 `bump_version` 改图后 `publish` 才允许 UI 读；`can_read()` 在写侧仍持所有权 / 版本不一致时 False；`snapshot()` 返回已发布版本的冻结字节（UI 持有期间写侧不得改同一 buffer）。
- **批量格式转换**（C02 步骤 3 "不在 Python 中对每像素创建临时 bytes"）：`FormatConverter.rgba_to_rgb8` / `bgra_to_rgb8` / `rgb8_to_rgba` 整块转换；`composite_alpha` 透明合成与 `paint.Color.blend_over` 同公式（C02 验收 "透明图片逐像素一致"）。

### C03 行内格式化与文字布局（`inline_layout.py`）
- `InlineRun`（C03 步骤 2）：同一行内格式化上下文的一条文本片段，携带共享字体度量（font_px/family/weight）+ 样式标记；不把每个 span 当独立块或重复收集父子文本。
- `WhitespaceFolder` + `collapse_runs`（C03 步骤 3）：按 white-space 模式（normal/nowrap/pre）折叠空白，保留跨节点（相邻 run 之间）的空白状态，行首/行尾空白去掉。
- `SimpleMetrics` / `FontMetrics`（C03 步骤 3/6 "先使用共享字体度量计算 advance"）：`advance` / `line_metrics`（ascent/descent），真实 TTF 复用现有 font/ttf_font（C03 步骤 6，不为 GPU 引入第二套排版逻辑）。
- `InlineLayout.layout`（C03 步骤 3/4/5）：空白折叠 → 共享 advance 累加 → 按可断点（空格 / CJK / word-break:break-all）分行 → 每行按 ascent/descent + line-height 计算基线，把字符定位进 `LineBox.runs`（`PlacedRun` 序列）；绘制阶段直接消费已定位的行盒（"单一排版数据源"，C03 步骤 5 "不再独立换行或重新测量"）。
- `line_baseline`（C03 步骤 4）：未设 line-height 时基线 = y + ascent、行高 = ascent + descent；设 line-height 时基线居中行盒。
- `runs_overlap`（C03 验收 "各片段按顺序且不重叠"）：两行 y / x 区间相交判定（用作回归断言，不应发生重叠）。

## 批次 16 完成的工作项（B03 脚本异常隔离 + B04 多变量声明 / 序列表达式，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| B03 脚本异常隔离（错误分类 + 页面级聚合） | 完成 | `toy_browser/scripting/script_error.py`（`ErrorCategory` / `ScriptPhase` / `ScriptErrorRecord` / `PageErrorAggregator`） | `tests/test_script_error.py`（16 条） |
| B04 多变量声明 + 序列表达式（JS 引擎纯逻辑核心） | 完成 | `toy_browser/scripting/js.py`（`MultiDecl` / `SeqExpr` AST + `parse_assignment_min(allow_seq=…)` 逗号归属规则） | `tests/test_js_multi_decl.py`（15 条） |

### B03 脚本异常隔离（`script_error.py`）
- `ErrorCategory`：`NETWORK / POLICY / SCRIPT / RENDER` 四类（B03 步骤 1 "错误分类"）；`ScriptPhase`：`FETCH / PARSE / RUNTIME / EVENT` 脚本阶段。
- `ScriptErrorRecord`：一条脚本异常记录（`nav_id` / `script_id` / `category` / `phase` / `message` / `position` / `caught` / `extra`）+ `summary()` 人类可读摘要。
- `PageErrorAggregator`（B03 步骤 2 "页面级聚合"）：
  - `record` / `record_uncaught_script` 登记；`continues_after_failure(script_id)` 判定"同脚本已捕获异常可继续、未捕获则中止该脚本"；
  - `page_is_usable()` 渲染崩溃 / DOM 不可用 / 文档未提交 → 页面不可用；
  - `has_uncaught_script_errors()` / `status_line()`（"已显示，N 个脚本失败"）/ `cli_result`（结构化结果供 CLI / 诊断区消费）；
  - **有效脚本 → 无效脚本 → 有效脚本**顺序回归：单个脚本失败不阻断后续脚本执行（与 `js.py` 既有 `execute_scripts_in_order` 的"未捕获 throw 按脚本继续"语义对齐）。
- **纯逻辑核心**：不触碰 GUI；真实"导航/渲染/脚本"接线与状态栏展示属后续活显示批次。

### B04 多变量声明 + 序列表达式（`js.py`）
- **AST**：`MultiDecl(items, kind)`（`var a=1, b=2` 的多声明列表）与 `SeqExpr(exprs)`（序列表达式，JS 最低优先级逗号运算符）；`MultiDecl` 求值左到右逐项定义到**同一**作用域（保持 `let`/`const` 块级作用域 + TDZ + `const` 不可重赋值，B04 步骤 2 "不合并成多个错误作用域"）。
- **逗号归属规则（B04 步骤 4 "不能通过直接切割源码逗号解决"）**：`parse_assignment_min(allow_seq=…)` 新增标志。序列逗号路径只在**顶层表达式**（`allow_seq=True`）启用；声明列表 / 对象属性值 / 数组元素 / 调用参数 / for-init 列表 / `new` 参数 / 箭头表达式体这些**所属语法**传 `allow_seq=False`，其内部逗号由各自的 `while` 循环消费。专用助手 `parse_expression_decl_rhs` / `parse_expression_obj_val` / `parse_expression_arr_el` / `parse_expression_arrow_body` 各归其位。
- **`allow_seq=False` 的赋值 RHS 继承该标志**：`a = b, c` 解析为 `Seq(Assign(a, b), c)` 而非 `Assign(a, Seq(b, c))`（逗号比赋值更松，序列逗号永远由最外层调用消费；括号内序列 `a = (b, c)` 由 `parse_primary` 括号路径独立处理，不受影响）。
- **`new` 参数列表 + 对象 spread 源 + 数组元素 + 箭头表达式体** 全部改走 `allow_seq=False` 助手，修复了 B04 改造一度破坏的 `new Point(3, 4)` 参数丢失 / 对象 spread `{...{a:1}, b:2}` / `a.reduce((acc, v) => acc + v, 0)` 箭头体吞逗号等回归。
- **构造器帧 / 类方法帧函数级作用域**：`ToyClass.__call__` 的 ctor 帧显式 `frame._is_func = True` + 参数直写 `frame.scope`（与 `_make_func` / `_make_arrow` 的帧语义一致），修复 `new C(x, y)` 构造器参数 `ReferenceError`。
- `eval_block` TDZ 预声明对 `MultiDecl` 逐项 `declare_block`。

## 批次 17 完成的工作项（B01 统一导航控制器 + B05 location 导航语义，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| B01 统一导航控制器（启动 / 地址栏 / 链接同一入口 + 地址栏三分类 + 历史 / 序号 / 丢弃旧任务） | 完成 | `toy_browser/app/navigation.py`（`NavRequest` / `NavOutcome` / `NavigationController`） | `tests/test_navigation.py`（9 条 B01） |
| B05 location 与文档 URL 的导航语义（assign / replace / reload / href + 相对解析 + 同文档 hash + 跳转预算） | 完成 | `toy_browser/app/navigation.py`（`Location` / `NavBudget` / `record_hop`） | `tests/test_navigation.py`（10 条 B05） |

### B01 统一导航控制器（`navigation.py`）
- `NavRequest`（B01 步骤 1）：源文档 `origin_url` / 目标 URL / 触发方式 `source`（launch / address / link / script / redirect / meta / bookmark / file）/ 是否替换历史 `replace` / 单调导航序号 `seq`（B10 丢弃旧任务）/ 失败时 `error` 类型。`kind()` 区分 web / file / about / local。
- `NavigationController.dispatch`（B01 步骤 1 "本地文件、about 页面和 http/https 经同一分派入口"）：分配 `seq`、成功统一更新地址栏 / 当前 URL / 历史；失败保留请求目标与错误类型（**不**改历史游标、不静默改界面文本，B01 步骤 4）。`drop_stale(seq)` 供 B10 丢弃过期结果。
- 地址栏三分类（B01 步骤 3）：绝对 URL 原样导航；相对 URL 经 `urljoin` 相对当前文档基址解析；普通文本走 `search_url_for` 搜索策略钩子（**独立产品策略**，不提供钩子时标记 `plain-text-no-search-policy`，不当本地文件）。
- 历史模型：成功 assign 新建项并清前进栈；replace 原地替换当前项（游标不动）；`back` / `forward` 移动游标并同步 `current_url`（浏览器语义：URL 随前进 / 后退更新）。`current_url` 始终是"已提交文档"，供 `Location` 统一初始化来源。
- 启动导航（B01 步骤 2）：`launch(initial_path)` 生成请求经同一 `dispatch` 入口提交，本地 / about / http 不分条件丢弃 URL。

### B05 location 导航语义（`navigation.py`）
- `Location`（B05 步骤 1）：替换 `web_api.make_location` 纯字典——宿主对象读取**当前已提交文档 URL**（`doc_url` 唯一初始化来源，核对 `host.url` / `host.doc_url` 不同使用位置）；`as_dict()` 保持旧键契约兼容。
- `assign` / `replace` / `reload` / `set_href`（B05 步骤 2）：调用**转成导航任务**（返回 `NavRequest`，由控制器 `dispatch` 消费），不在 JS getter/setter 中同步递归加载整个网页。`replace` 替换历史项、`assign` 新建历史项（B05 步骤 3）。
- 相对目标与最终响应 URL 用 stdlib `urljoin` 解析；`is_same_doc`（B05 步骤 3 "hash 同文档导航与跨文档加载分开"）：protocol + host + path(+query) 相同且 `ignore_fragment=True` 时 `#` 差异不算跨文档。
- `NavBudget` + `record_hop`（B05 步骤 4）：HTTP 重定向 / 脚本跳转 / meta refresh 共用导航次数 + 取消标记；循环跳转超 `max_hops` 拒绝继续（`nav-budget-exhausted`）。
- **百度 227 字节脚本页**（B05 验收）：`location.replace(location.href.replace("https://", "http://"))` 产生 `replace=True` 的导航任务并更新当前 URL，不再无声停在原 URL。
- **纯逻辑核心**：不触碰 Tk / 网络 I/O；`web_api` 真实接线、meta refresh 解析、GUI 地址栏绑定留后续批次。

## 批次 18 完成的工作项（B06 字段编辑状态 + B07 统一激活 / 表单提交，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| B06 字段编辑状态（稳定 DOM 身份 + 键盘路由三分 + 局部刷新） | 完成 | `toy_browser/app/field_editing.py`（`FieldRegistry` / `key_to_input_event`，复用 G03 `EditorState`） | `tests/test_field_editing.py`（9 条） |
| B07 统一激活、表单提交与链接跳转（纯逻辑核心） | 完成 | `toy_browser/app/activation.py`（`ActivateDecision` / `FormField` / `activate_element` / `build_form_submission` / `parse_link_target`） | `tests/test_activation.py`（16 条） |

### B06 字段编辑状态（`field_editing.py`）
- `FieldRegistry`（B06 步骤 1 "以稳定 DOM 身份关联"）：按 `node_id` 索引字段编辑状态（不依赖可能变化的遍历下标）；`register` / `get` / `untrack`；只读 / 禁用态在 `apply_key` 前拦截（不修改 value）。
- 键盘路由三分（B06 步骤 2）：`route_key` 按焦点把按键归 `address`（地址栏）/ `edit`（原生编辑层，委托焦点字段 `EditorState`）/ `page`（焦点 / 激活）；`key_to_input_event` 把可打印键 → `insertText`、Backspace / Delete / Enter → 对应 inputType，功能键（Tab / 方向 / Esc）→ `None`（归页面）。
- 值与 DOM 同步（B06 步骤 3）：`set_value` / `sync_value` 复用 `EditorState.sync_from_dom`（编辑 / 非编辑两态保留可解释 caret）；IME 三阶段与密码遮罩复用 G03。
- 局部刷新（B06 步骤 4 "编辑变化触发所需事件和可合并的局部刷新，禁止一次输入引发全页面同步下载和重建"）：`dirty_field_ids` 只报告被改字段；`flush_refresh` 取走并复位。

### B07 统一激活、表单提交与链接跳转（`activation.py`）
- `activate_element`（B07 步骤 1 "鼠标、Enter/Space 按控件类型进入同一默认动作；先派发可取消事件"）：可取消 `click`（`ClickEvent.default_prevented` 阻断导航 / 提交，B07 验收 "preventDefault"）；链接 → 导航决策；submit / 命名 button / form 内 Enter → 提交决策；其余可聚焦控件 → 焦点动作。
- `FormField.is_successful_control`（B07 步骤 2 "form owner、提交按钮、successful controls、禁用/未命名字段排除"）：禁用 / 未命名字段一律排除；checkbox / radio 仅 `checked=True` 时计入；重复字段名作为重复 `k=v` 对。
- `parse_link_target`（B07 步骤 3 "action=/s、相对 a href、协议相对 URL 和 <base> 都交给文档基址解析"）：协议相对补 base scheme、绝对 URL 不相对化、相对经 `urljoin`；`target=_blank` 记录为新标签、`javascript:` / `mailto:` / `data:` / `ftp:` 标 `scheme_ok=False`（明确处理不静默）。
- `build_form_submission`（B07 步骤 4 "submit/requestSubmit 与默认 Enter 提交差异"）：GET 查询进 URL、POST 查询进 body（urlencoded）；`requestSubmit` / Enter 走必填校验（空 required 阻断），`form.submit()` 跳过校验。
- 不硬编码识别站点 ID（B07 步骤 4 "脚本驱动 textarea/button 仍须执行其实际事件逻辑"）：事件钩子（`on_click`）由 GUI / 脚本层注入，本模块只提供"事件 → 决策"的通用纯逻辑。
- **纯逻辑核心**：表单字段序列化复用批次 1 `browser.py` 同公式；GUI 按键 / 鼠标接线、`<base>` 实际解析、活显示验收留后续批次。

## 批次 19 完成的工作项（B08 资源与事件生命周期 + B10 异步取消与用户任务级回归，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| B08 DocumentContext + 资源三态 + 脚本调度（纯逻辑核心） | 完成 | `toy_browser/app/document_context.py`（`DocumentContext` / `ResourceRecord` / `ScriptScheduler`） | `tests/test_document_context.py`（15 条） |
| B10 阶段状态机 + 取消 + 用户任务级回归骨架（纯逻辑核心） | 完成 | `toy_browser/app/load_phases.py`（`PhaseTracker` / `PhaseBudget` / `UserTaskRunner` / `TaskRepro`） | `tests/test_load_phases.py`（13 条） |

### B08 资源与事件生命周期（`document_context.py`）
- `DocumentContext`（B08 步骤 1 "使用同一 DocumentContext 保存最终 URL、基址、策略、Cookie、资源表、任务版本与错误列表，消除 CLI/GUI 重复拼接的状态差异"）：`final_url` / `base_url` / `policy` / `cookies` / `task_version` / `resources` / `errors` 合一；`bump_task_version` + `is_stale_task` 与 C01 任务版本对齐（B10 复用旧结果丢弃）。
- `ResourceRecord`（B08 步骤 2 "为资源记录类型、请求/解码结果、使用方和阻断原因；区分'下载 200''成功解码''应用到页面'"）：三态 `downloaded` / `decoded` / `applied` + `blocked_reason`；`usable` 属性 = 真正 applied（"33 个 200"不能代替渲染验收，B08 验收）。
- 资源三态登记入口（B08 步骤 2/4）：`mark_downloaded` / `mark_decoded` / `mark_applied` / `mark_blocked` 各自记录阻断原因并登记 `errors`；`register_resource` 按 url 幂等。
- `ScriptScheduler`（B08 步骤 3 "对动态插入的 script/link/img、async/defer、CSS 依赖与 DOMContentLoaded/load 建立最小一致模型；任务和微任务顺序用专门 fixture 对照，不能仅靠一次统一 flush 近似整站行为"）：按脚本组别（普通=文档序 / async=就绪序 / defer / doc_ready / disabled）稳定排序；`mark_dom_content_loaded` / `mark_load` 生命周期门控；未启用脚本不执行。
- 可解释报告（B08 步骤 5 "能解释完整首页为何仍隐藏或缺失内容，网络成功数与页面使用结果分别可查"）：`resource_completeness`（total / downloaded / decoded / applied / usable / blocked）、`visibility_report`（列出未 applied 资源 + 阻断原因）、`usage_report`（合并视图 + 脚本错误列表）。

### B10 消除加载阻塞 + 用户任务级回归（`load_phases.py`）
- `PhaseTracker`（B10 步骤 1 "每次导航携带版本号，取消/切标签后旧结果不得覆盖新页面" + 步骤 2 "提供连接、下载、解析、执行、布局、绘制阶段状态和取消入口"）：六阶段顺序状态机（`connect→download→parse→execute→layout→present`），前置未完成时阻断；`nav_version` + `drop_stale` / `accept_phase` 旧版本结果丢弃；`cancel` 把 running 阶段翻 cancelled、后续结果拒绝；`progress` / `all_done` / `status_label`（区分 complete / cancelled / budget-exhausted / blocked / in-progress）。
- `PhaseBudget`（B10 步骤 2 + B08 步骤 4 "保持响应大小、请求数、时间和内存预算"）：`max_requests` / `max_bytes` / `max_seconds` / `max_mem`；`exhausted` + `exhaust_reason`（预算耗尽与网站拒绝访问分别提示，避免持续显示 Loaded）。
- `UserTaskRunner`（B10 步骤 4 "在线任务固定为'首页可用 → 输入 → Enter/按钮搜索 → 结果可读 → 结果跳转 → 返回'，被前置问题阻断的步骤明确标为 blocked"）：固定六步；`run_step` 接受纯逻辑钩子，`TaskBlocked` → `blocked`（记录原因），前置被阻断时后续步骤级联 `skipped`；`summary` 输出失败分布。
- `TaskRepro`（B10 步骤 5 "首次修复后先完成一次闭环，再进行至少 3 次独立会话复测，并扩充相同语义的小站和本地页；记录失败分布，不以单站或单次成功声称达到真实浏览器标准"）：`min_sessions` 默认 3；`failure_distribution` 跨会话统计各步骤 ok/blocked/skipped 计数；`report` 输出可复核完整证据。
- **纯逻辑核心**：不触碰 Tk / 网络 I/O；`net.py` 资源加载器接线、GUI 任务队列、本地 HTTP 服务 fixture、活显示验收留后续批次。

## 批次 20 完成的工作项（B02 请求配置与页面可用性诊断 + B09 可读首屏，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| B02 请求配置 + 页面可用性诊断（纯逻辑核心） | 完成 | `toy_browser/app/request_config.py`（`RequestConfig` / `PageDiagnostics`） | `tests/test_request_config.py`（11 条） |
| B09 可读首屏（可见性模型 + 文本测量 + 布局几何，纯逻辑核心） | 完成 | `toy_browser/app/first_screen.py`（`VisibilityModel` / `TextRun` / `BoxRect`） | `tests/test_first_screen.py`（15 条） |

### B02 请求配置与页面可用性诊断（`request_config.py`）
- `RequestConfig`（B02 步骤 1 "以配置对象集中管理 User-Agent、Accept、Accept-Language、Cookie 策略、超时与重定向预算，文档及子资源共用"）：`ua` / `accept` / `accept_language` / `cookie_policy` / `timeout` / `max_redirects` / `request_id` 合一；`for_subresource` 子资源继承文档配置（避免硬编码不一致标识）；**不**提供关证书验证 / 固定 HTTPS→HTTP 开关（B02 验收 "不得以关闭证书验证或固定将 HTTPS 改成 HTTP 作为通用修复"）。
- 兼容标识（B02 步骤 2 "允许显式选择兼容标识用于诊断，界面和日志记录实际选择；不把标识切换描述成引擎兼容修复"）：`with_ua(default|compat)` 显式选择 + `ua_value` / `describe()` 记录实际选择。
- `PageDiagnostics`（B02 步骤 3 "记录主文档状态、最终 URL、Content-Type、解码后大小、请求 ID 和导航链；区分'传输完成''文档已提交''首屏已绘制''存在脚本错误'"）：`mark_transport_done` / `mark_doc_committed` / `mark_first_screen` / `mark_script_error` 四状态标记；`add_nav_hop` 导航链；`explain()` 输出诊断摘要。
- 小正文诊断（B02 步骤 4 "对'200 + 极小脚本页面 + 没有可见内容'的组合提供诊断提示，不单凭正文短小判失败；空文档本身也可能是合法页面"）：`diagnose_small_body` 五级诊断（full-page / legitimate-empty / script-redirect / no-visible-content）；`is_legitimate_empty` / `needs_script_execution` 区分"合法空页"与"需脚本执行"。

### B09 可读首屏（`first_screen.py`）
- `VisibilityModel`（B09 步骤 1 "先保证 body 可见性、display/visibility、脚本状态变更和初始样式生效；保留隐藏态诊断，不能对百度强制覆盖所有 display:none"）：`register` / `set_display` / `set_visibility` 管理元素样式；`is_visible` 判定可见（display 非 none + visibility 非 hidden + opacity>0）；`hidden_reason` 给出**诊断**（"被谁隐藏 / 何种样式"，脚本 / 初始样式区分）；`first_screen_summary` 首屏关键元素（搜索输入 / 按钮 / 结果区）可见性汇总。
- `TextRun` / `measure_char`（B09 步骤 3 "优先修正中文字体回退、文本测量、空格、基线、行内排版和编辑控件；同时修正字形挤压问题"）：空格折叠（连续空格只计一个 advance）、CJK 字符 advance=1.0em + 字体回退标记、基线定位（bearing_top=0.8em）、`first_screen_readable` 可读性判据（有效推进 + CJK 回退，B09 验收 "中文能读"）。
- `BoxRect` / `search_box_occluded` / `scroll_hit_consistent`（B09 步骤 4/5 "在统一视口、缩放、字体环境和相同静态输入下比较边框盒及截图"；B09 验收 "搜索输入和按钮不被遮挡，中文能读、结果能点击，滚动后的命中位置一致"）：边框盒几何（overlap / contains_point）、搜索框遮挡判定、滚动命中一致性（容差内）。
- **纯逻辑核心**：不触碰 Tk / paint / 网络 I/O；`layout.py` / `paint_engine.py` 实际排版、活显示验收、GPU 接入留后续批次（B09 验收 "任何 GPU 接入都不改变 DOM/导航缺口的验收标准"）。

## 本批未完成 / 阻塞项

- **C06 增量布局接入 `layout.py`**：本批落纯逻辑决策层（失效等级 +
  最小重排根 + 增量计划）；把 `build_layout_plan` 接进 `layout_dirty_only`
  / `mark_dirty` 的实际重排执行需活显示环境验收，留后续。
- **G11 Tk 绑定**：真实 :class:`tk.Text`/Entry 覆盖层的键盘绑定、焦点
  可见性的画布滚动需活显示环境验收，留作后续批次。
- **C04 表单提交端到端**（C04 步骤 4/5）：提交复用现有
  `browser.py` serialize/validate/form_submit，端到端 GET/POST 任务
  需活显示环境，留后续。
- **C03 几何冻结 + 实际排版接入**：本批落行内格式化 / 文字布局纯逻辑
  核心（InlineRun / LineBox / 空白折叠 / 共享 advance / 基线定位）；Edge
  几何冻结（C03 step 1）与 paint_engine 实际排版接入需活显示环境验收，
  留后续。
- **G07 文档进程方案**：可取消网络工作单元 + 线程初版；把 CPU
  工作（DOM / 布局）移入文档进程是后续（纯 Python 线程无多核收益）。

## 批次 21 完成的工作项（G11 键盘全流程纯逻辑路由层，GUI 可注入）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G11 键盘全流程路由（按键 → 焦点域 → 决策，纯逻辑核心） | 完成 | `toy_browser/app/keyboard_router.py`（`KeyboardRouter` / `KeyDecision` / `tk_binding_result`） | `tests/test_keyboard_router.py`（23 条） |

### G11 键盘全流程路由（`keyboard_router.py`）
- `KeyboardRouter`（G11 完整按键集 + G04 四域路由 + B06 编辑层映射统一）：把"按键 + 当前焦点状态"路由到统一决策 :class:`KeyDecision`，复用 B06 `key_to_input_event`（编辑层按键 → beforeinput）+ G04 焦点域语义（chrome / document / editor / dialog），不重复实现按键映射。
- 按键分级（纯逻辑分类）：修饰符单键（Shift/Ctrl/Alt/Meta）→ 不消费 fall-through；Tab / Shift-Tab 按焦点域（document → 进焦点链，chrome / editor / dialog → break 原生顺序）；编辑层（焦点在 input/textarea 且有活动字段）→ 可打印 / Backspace / Delete / Enter 进编辑层、方向 / Home / End / Page → caret-move（不派发 beforeinput）、Esc 退出编辑；地址栏（chrome）→ Return navigate、可打印字符进地址输入、Esc 清除；页面（document）非编辑层 → Return / 空格 activate、Esc dismiss。
- `tk_binding_result`（GUI 绑定辅助）：`consumed=True` → Tk 返回 `"break"`（阻断 fall-through），`False` → `None`（Tk 原生顺序）。GUI 只需"查表 + 分派"，绑定简化。
- `describe`（诊断）：输出按键路由结果串（供日志 / 状态栏，G11 可观察）。
- **纯逻辑核心**：不触碰 Tk / 显示环境；真实 `tk.Text`/Entry 覆盖层绑定、焦点可见性画布滚动、活显示验收留后续批次。

## 批次 22 完成的工作项（C01 公共 API 包装层 GUI 接入 + G10 诊断区面板，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C01 step 4 公共 API 包装层 GUI 接入（PageInstance → DocumentState 桥） | 完成 | `toy_browser/app/gui_document_bridge.py`（`GuiDocumentBridge`） | `tests/test_gui_document_bridge.py`（20 条） |
| G10 step 4/5 诊断区面板（布局规格 + 折叠态 + 事件过滤 + 导出） | 完成 | 同上（`DiagnosticPanel` / `export_diagnostic_snapshot`） | 同上 |

### C01 公共 API 包装层 GUI 接入（`GuiDocumentBridge`）
- `GuiDocumentBridge` 把 GUI 的 `PageInstance` 接到 `DocumentState` 同一生命周期入口（C01 step 4 "把 GUI、CLI 和测试都接到同一流程"）：`begin_document` / `restate` / `render` 对应"新文档加载（脚本初始化 +1）"/"重排（不重建 Host）"/"光栅化（只 bump frame_id）"，`navigate` 新上下文（旧 disposed），`node_id` 稳定 id（重排后点击监听仍在），`sync_page_state` 把 hover/focus/form 映射到稳定 node_id，`render_pipeline` 一步完成（委托 `document_renderer`）。
- 验收：`script_init_count` 只增加一次（重排 / 渲染不变）；`navigate` 后旧上下文 `disposed=True`；`node_id` 跨重排不变。

### G10 诊断区面板（`DiagnosticPanel` / `export_diagnostic_snapshot`）
- `DiagnosticPanel` 管理诊断区展开 / 收起态（默认收起，普通使用不堆放内核信息——G10 step 4），按 `document_id` 过滤事件，暴露 `layout_spec()`（折叠态 / 尺寸 / 阶段耗时 / backend）供 Tk 侧控件消费。
- `assert_close_no_side_effect`：关闭 / 打开诊断区**不改变** C01 文档版本（验收"关闭诊断区不改变页面行为"）。
- `export_diagnostic_snapshot`（G10 step 5）：导出诊断 JSON（backend / document_id / 各阶段耗时 / 事件列表含缓存 / fallback 原因），供"导出诊断信息"按钮数据源。
- **纯逻辑核心**：不触碰 Tk / 显示环境；真实诊断区控件 / 折叠动画 / 导出按钮 GUI 绑定留活显示批次。

## 批次 23 完成的工作项（G07 文档工作单元标签级编排，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| G07 标签级文档工作编排（多标签控制器 + G09 门控 + C01 桥 + UI 调度追踪） | 完成 | `toy_browser/app/tab_document_orchestrator.py`（`TabDocumentOrchestrator` / `TabDocumentUnit` / `UIScheduleTracker`） | `tests/test_tab_document_orchestrator.py`（23 条） |

### G07 标签级文档工作编排（`tab_document_orchestrator.py`）
- `TabDocumentUnit`（G07 单标签文档工作单元）：按 tab_id 持有 `DocumentController`（有界命令队列 + C01 版本）+ `GuiDocumentBridge`（C01 文档生命周期）+ 在途结果队列；`drain` 主线程取回结果、`apply` 经 C01 版本门控丢弃过期帧、`discard_all` 关标签时清空在途结果 + 命令队列（G07 验收"A 的结果不覆盖 B"）。
- `TabDocumentOrchestrator`（G07 多标签编排 + G09 门控）：`create_tab` 分配 tab_id + 控制器 + 桥；`navigate` 切到目标标签前台 + 投递命令（UI 不阻塞网络——经有界命令队列 + 可取消 transport）；`switch_tab` 只改前台呈现（G09 step 2 不重建文档）；`close_tab` 经 lifecycle 取消在途任务 + 丢弃在途结果 + 释放资源记账 + 前台让给剩余标签（G09 step 4 + G07 验收）。
- `UIScheduleTracker`（G07 step 6 验收"UI 调度 p95 < 50ms"）：纯记账记录 UI 调度耗时，`p95_ms()` 统计供诊断区展示；`max_samples` 上限防无界增长。
- **纯逻辑核心**：不触碰 Tk / 显示环境；Tk 真实标签控件 / 画布重绘 / 后台降重绘、文档进程方案（G07 step 4 CPU 工作移出 UI 线程）留活显示批次。

## 批次 24 完成的工作项（C04 表单提交端到端任务 + G09 统一历史提交路径，纯逻辑核心）

| 工作项 | 状态 | 落点 | 回归 |
|---|---|---|---|
| C04 step 4/5 表单提交端到端任务编排 | 完成 | `toy_browser/app/form_task.py`（`FormSubmissionTask` / `FormTaskResult` / `commit_form_navigation`） | `tests/test_form_task.py`（15 条） |

### C04 表单提交端到端任务（`form_task.py`）
- `FormSubmissionTask` 把"收集字段 → 校验 → 提交 → 入历史 / 被阻断保留焦点与内容"编排成可测试的端到端流程，**复用 B07 `build_form_submission`**（不复制序列化规则，C04 步骤 4"提交复用现有 serialize/validate/form_submit"）+ **G09 `HistoryCommitter`**（统一历史入口，C04 步骤 4 的"取消提交"与 G09"不串页"）。
- `run`：成功 → 经 committer 入历史（`source=form-submit`，携带最终 URL）；被校验阻断 → `blocked=True` + `violations` + `focus_field`（第一个违规字段，C04 步骤 5"失效输入保持用户内容与焦点"），**不入历史**；`user_content_preserved=True`（保留已输入 value）。
- `TRIGGER_SUBMIT`（form.submit()）跳校验；`TRIGGER_REQUEST_SUBMIT` / Enter 走校验（B07 步骤 4 差异透传）。
- `cancel`（C04 步骤 4 验收"取消提交"）：取消被阻断 / 被 preventDefault 的提交，保留用户输入与焦点、不切换页面、不入历史；成功提交已入历史后不可取消（不串页 / 不回滚历史）。
- `commit_form_navigation`（G09 步骤 3）：把一次**成功**表单提交接进统一历史入口（返回 `(result, entry)`），被阻断 / 取消不入历史（避免历史分叉 / 串页）；同址去重（"一次导航 = 一个历史项"）。
- **纯逻辑核心**：不触碰 Tk / 显示环境；Tk 真实表单控件 / 焦点画布滚动 / 提交后导航活显示验收留后续批次。

## 剩余限制（下一批次）

- G11 Tk 侧键盘绑定（纯逻辑路由层已在批次 21 落地，Tk 真实绑定 / 焦点画布滚动留后续）、
  G09 step 2+4 Tk 侧路由。
- G07 文档进程（CPU 工作移出 UI 线程的多核方案）：标签级编排纯逻辑核心已在批次 23 落地
  （`TabDocumentOrchestrator`），文档进程 / 多核方案留后续。
- C01 公共 API 包装层 GUI 接入纯逻辑核心已在批次 22 落地
  （`GuiDocumentBridge`），GUI 侧 `PageInstance` 实际接线留活显示批次。
- C04 表单提交端到端任务纯逻辑核心已在批次 24 落地
  （`FormSubmissionTask` + G09 统一历史入口），Tk 侧表单控件 / 提交后
  导航留活显示批次；
  C03 文字布局（需先冻结 Edge 几何对照）、
  C06 增量布局接入 layout.py、C02 填充 / 混合 / 图片 / 字形逐阶段迁移到连续缓冲区。
- 滚轮 / 滚动条 / G03 编辑覆盖层 / G06 resize / G10 诊断区 / G11
  键盘 / G09 标签切换的 Tk 实测需有显示环境执行；本批回归覆盖纯逻辑
  与协议公式。
- 百度等真实网站可用性：B03（脚本异常隔离）+ B04（JS 多变量声明 /
  序列表达式）纯逻辑核心已在批次 16 落地；B01（统一导航控制器）+
  B05（location 导航语义）纯逻辑核心已在批次 17 落地；B06（字段编辑
  状态）+ B07（统一激活 / 表单提交）纯逻辑核心已在批次 18 落地；
  B08（资源与事件生命周期）+ B10（异步取消 / 用户任务级回归）纯逻辑
  核心已在批次 19 落地；B02（请求配置 / 可用性诊断）+ B09（可读首屏 /
  字体与几何）纯逻辑核心已在批次 20 落地；B 系列 B01–B10 纯逻辑核心
  全部落地（活显示 / GUI 接线 / 网络 I/O 留后续批次）。

## 回归数字

| 验证项 | 结果 |
|---|---|
| `py -3 test.py` | **1510/1510 通过**（基线 939 + 批次 1 新增 28 + 批次 2 新增 32 + 批次 3 新增 20 + 批次 4 G09 新增 14 + 批次 5 G07/G08 可取消网络新增 12 + 批次 6 G10 诊断/工具栏纯逻辑新增 19 + 批次 7 C01 完整 DocumentState + G11 键盘全流程新增 32 + 批次 8 G09 step 3 统一历史入口新增 12 + 批次 9 G09 step 2+4 标签生命周期 / C01 step 4 公共 API 包装层新增 20 + 批次 10 C04 表单控件呈现模型 / 表格列宽行高求解 + G10 step 4/5 诊断区展开收起新增 27 + 批次 11 C05 样式缓存键 / 失效范围 / 命中率统计新增 18 + 批次 12 C06 布局树构建与增量布局新增 15 + 批次 13 C07 紧凑显示列表 / 可见区域裁剪 + C08 脏区域分块 / 交互重绘新增 41 + 批次 14 C09 PNG 编码 / 异步导出 + C10 策略 API / 事件循环扩大回归新增 44 + 批次 15 C02 连续像素缓冲区阶段 2–3 + C03 行内格式化 / 文字布局新增 27 + 批次 16 B03 脚本异常隔离 + B04 多变量声明 / 序列表达式新增 31 + 批次 17 B01 统一导航控制器 + B05 location 导航语义新增 19 + 批次 18 B06 字段编辑状态 + B07 统一激活 / 表单提交新增 25 + 批次 19 B08 资源与事件生命周期 + B10 异步取消 / 用户任务级回归新增 28 + 批次 20 B02 请求配置 / 页面可用性诊断 + B09 可读首屏新增 26 + 批次 21 G11 键盘全流程路由层新增 23 + 批次 22 C01 公共 API 包装层 GUI 接入 + G10 诊断区面板新增 20 + 批次 23 G07 文档工作单元标签级编排新增 23 + 批次 24 C04 表单提交端到端任务 + G09 统一历史提交路径新增 15） |

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
  G11 Tk 侧键盘绑定（纯逻辑路由层已在批次 21 落地）。
- G07 文档进程（CPU 工作移出 UI 线程的多核方案）：标签级编排纯逻辑
  核心已在批次 23 落地（`TabDocumentOrchestrator`），文档进程 / 多核
  方案留后续。
- C01 公共 API 包装层 GUI 接入纯逻辑核心已在批次 22 落地
  （`GuiDocumentBridge`），GUI 侧 `PageInstance` 实际接线留活显示批次；
  C03 文字布局（需先冻结 Edge 几何对照）、C04 表格表单、C05 样式
  缓存、C02 填充 / 混合 / 图片 / 字形逐阶段迁移到连续缓冲区。
- 滚轮 / 滚动条 / G03 编辑覆盖层 / G06 resize / G10 诊断区 / G11
  键盘的 Tk 实测需有显示环境执行；本批回归覆盖纯逻辑与协议公式。
- 百度等真实网站可用性：B03 + B04 纯逻辑核心已落地（批次 16），
  B01 + B05 纯逻辑核心已落地（批次 17），B06 + B07 纯逻辑核心已落地
  （批次 18），B08 + B10 纯逻辑核心已落地（批次 19），B02 + B09 纯逻辑
  核心已落地（批次 20）；B 系列 B01–B10 纯逻辑核心全部落地（活显示 /
  GUI 接线 / 网络 I/O 留后续批次）。

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

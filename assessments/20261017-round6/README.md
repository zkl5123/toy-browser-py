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
| G07 UI 与文档工作分离 | ⏳ 未实施（依赖网络线程，见下） | — | — |
| C02 连续像素缓冲区及呈现 | ✅（数据契约 + PPM 批量打包 + 缓冲池） | `toy_browser/rendering/cpu_image.py` | `tests/test_cpu_image.py`（6 条） |
| C03 行内格式化与文字布局 | ⏳ 未实施（需先冻结 Edge 几何对照，见下） | — | — |

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

## 本批未完成 / 阻塞项

- **G07 UI 与文档工作分离**：有界命令队列 + 网络 I/O 移出主线程 +
  文档进程方案。依赖 G08 导航状态机与 C01 完整 `DocumentState`
  抽取（批次 3）；本批先落 C01 版本骨架（批次 1）为 G07 任务丢弃
  提供基础，但未实施线程分离本身（纯 Python 线程无多核收益，文档
  建议评估文档进程方案）。
- **C03 行内格式化与文字布局**：需先将最小失败页面拆为普通文本 /
  b/i/span / 链接 / 连续空白 / 中文拉丁混排 / 长单词，并冻结 Edge
  几何与截图（C03 step 1），本批未执行该冻结步骤，故未实施
  InlineRun/LineBox。

## 剩余限制（下一批次）

- G08 导航状态 / 停止 / 错误、G09 标签页与历史会话、G10 工具栏 /
  状态栏 / 诊断区、G11 可访问性。
- C01 完整 `DocumentState` 抽取（DOM 树 + Host + 策略 + 子资源注册表
  合一）、C03 文字布局、C04 表格表单、C05 样式缓存。
- 滚轮 / 滚动条 / G03 编辑覆盖层 / G06 resize 的 Tk 实测需有显示
  环境执行；本批回归覆盖纯逻辑与协议公式。
- 百度等真实网站可用性仍受 B01–B10 阻断（JS 多变量声明、location
  语义、错误分类），不在本批范围。

## 回归数字

| 验证项 | 结果 |
|---|---|
| `py -3 test.py` | **999/999 通过**（基线 939 + 批次 1 新增 28 + 批次 2 新增 32） |

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

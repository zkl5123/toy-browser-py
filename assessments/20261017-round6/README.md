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

## 回归数字

| 验证项 | 结果 |
|---|---|
| `py -3 test.py` | **967/967 通过**（基线 939 + 新增 28） |

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

- G03（编辑、选区与输入法）与 G06（动态视口 / DPI / 缩放）未实施；G03 依赖 G02 + G04（已就绪）。
- C01 完整 `DocumentState` 抽取（DOM 树 + Host + 策略 + 子资源注册表合一）为批次 2 重构；本批只落了版本计数器骨架。
- 滚轮 / 滚动条的 Tcl 实测（真实 Tk command）需在有显示环境执行；本批回归覆盖 PageInstance 协议逻辑与纯解析公式。
- 百度等真实网站可用性仍受 B01–B10 阻断（JS 多变量声明、location 语义、错误分类），不在本批范围。

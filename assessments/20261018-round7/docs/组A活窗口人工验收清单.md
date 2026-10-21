# 组 A 活窗口人工验收清单（swapchain 上屏 / 真实 IME / 真实读屏器）

> **性质**：这三项属 OS 侧真实外设行为，无法沙箱自动测通。脚手架
> `toy_browser/app/live_acceptance.py` 把"驱动到最大程度 + 证据可采集"
> 做实；**每一项的最终判定必须经人工对照证据后签字**。签字前一律视为
> "未验证"（"不冒充通过"原则，round7 起贯彻）。
>
> **使用方法**：
> 1. 在完整环境（3.11.9，RTX 3050 + Tk + rendercanvas）运行脚手架：
>    ```bash
>    python -m toy_browser.app.live_acceptance --ime ni,hao --a11y-input <a11y.json>
>    ```
>    （不加 `--headless` 让 swapchain 窗口可见；`--out 报告.md` 落可签字报告）
> 2. 对照下表的**人工判定项**，逐项确认现场证据；
> 3. 在每条"签字"位填写判定人 + 日期；
> 4. 有 FAIL 或证据缺失的项，**不得签字**，回到脚手架 / 代码修。

---

## 1. swapchain 真上屏可见（R7-P02 §254 "surface 更新可见"）

脚手架：`probe_swapchain_visible()` → 真开活窗口 → 拿真实 wgpu screen
surface → `GPUBackend.present` 通路 + `SurfacePresentGate` 可观测差异。

| 项 | 可判定标准（对照证据） | 证据字段 | 判定人签字 |
|----|----------------------|---------|-----------|
| 1.1 活窗口真实 HWND | 窗口拿到的 `hwnd` > 0，且 `window_geometry` 非空 | `hwnd`, `window_geometry` |  〇 ____ @ ____ |
| 1.2 真实 screen surface | `surface_format` 为真实 srgb 表面格式（含 `srgb` / `bgra8unorm`），非硬编码 | `surface_format` |  〇 ____ @ ____ |
| 1.3 gate 可观测差异 | `gate_decision == present`（活窗口 surface 在场）且 `gate_no_surface == no-surface`（headless 对照），两态可观测不同 | `gate_decision`, `gate_no_surface` |  〇 ____ @ ____ |
| 1.4 **肉眼确认上屏**（人工边界） | 活窗口在屏幕上真实显示对应画面，且随内容更新可见 | 人眼 |  〇 ____ @ ____ |

> **说明**：1.1–1.3 脚手架可自动驱动出证据；1.4 是脚手架**无法自动
> 判定**的 OS 行为（屏幕可见性），必须由人在窗口可见时确认。缺
> tkinter / wgpu / rendercanvas 任一 → 探针报 `UNSUPPORTED`，本行
> 整项不得签字。

---

## 2. 真实 IME 选词（R7-U02 可自动化到最大程度）

脚手架：`probe_ime_composition()` → 活窗口真实 Tk Entry + G03 IME 组合
三阶段（`start` / `update` / `commit`）+ 一段取消。

| 项 | 可判定标准（对照证据） | 证据字段 | 判定人签字 |
|----|----------------------|---------|-----------|
| 2.1 拼音中间态 | 组合 `update` 阶段 `composing=True`，组合区 value 随拼音段累加 | `composition[*]` |  〇 ____ @ ____ |
| 2.2 汉字提交 | 末段 `commit` 后 `committed=True`、`end_composing=False`，`final_value` 含最终汉字 | `committed`, `end_composing`, `final_value` |  〇 ____ @ ____ |
| 2.3 真实控件同步 | `widget_value`（真实 Tk Entry 读回）与 `final_value` 一致 | `widget_value` |  〇 ____ @ ____ |
| 2.4 组合取消复位 | 取消路径 `cancel_composing=False`（is_composing 复位，不串进值） | `cancel_composing` |  〇 ____ @ ____ |
| 2.5 **真人 IME 选词**（人工边界） | 在真实系统输入法（中/日/韩）下逐词选字，组合区下划线 / 候选 / 最终汉字与本项目组合状态机一致 | 真人键盘 |  〇 ____ @ ____ |

> **说明**：2.1–2.4 是"可自动化到最大程度"的部分（脚手架驱动到真实
> 控件）；2.5 是**真实系统输入法选词**，脚手架无法自动判定，须人工。

---

## 3. 真实读屏器（R7-U03 UIA 契约 + 事件流暴露）

脚手架：`probe_screen_reader()` → 内部 a11y 树经 `to_uia_node` 映射为
UIA 控件契约 + `UiaFocusTracker` 事件流（`focus-moved` /
`property-changed` / `structure-change`）+ 对话框焦点归还。

| 项 | 可判定标准（对照证据） | 证据字段 | 判定人签字 |
|----|----------------------|---------|-----------|
| 3.1 UIA 控件契约 | `uia_contract` 含 `ControlType`/`Name`/`Value`/`IsEnabled`/`IsKeyboardFocusable`/`HasKeyboardFocus`，子节点递归 | `uia_contract` |  〇 ____ @ ____ |
| 3.2 焦点事件流 | 事件流含 `focus-moved`（Tab 移动焦点） | `event_stream[*].type` |  〇 ____ @ ____ |
| 3.3 值/状态事件流 | 事件流含 `property-changed`（value 变 / IsEnabled 变） | `event_stream[*]` |  〇 ____ @ ____ |
| 3.4 结构 + 对话框归还 | 事件流含 `structure-change`（插/删子）+ 对话框 `focus-moved`（`restored_focus` 归还） | `event_stream[*]`, `focused_final` |  〇 ____ @ ____ |
| 3.5 **读屏器真实读到**（人工边界） | 用真实读屏器（NVDA / JAWS / Windows 讲述人）+ UIA 检查器，确认这份契约 + 事件流被读屏器真实播报（焦点移动 / 值变 / 对话框焦点归还） | 真人读屏器 |  〇 ____ @ ____ |

> **说明**：3.1–3.4 脚手架把契约 + 事件流**暴露**出来（读屏器可消费
> 的最小证据）；3.5 是"真实读屏器读到并播报"，脚手架**不广播 OS
> 事件、不宣称读屏器已读到**，须人工。

---

## 判定规则（不冒充通过）

- 每项签字位必须填"判定人 + 日期"才生效；**未签字 = 未验证**。
- 探针 `UNSUPPORTED`（缺 OS 能力）或 `FAIL`（驱动失败）时，对应行
  **不得签字**；先修脚手架 / 环境，再重跑。
- 人工边界行（1.4 / 2.5 / 3.5）必须**真人**完成才能签字，脚手架的
  `PASS` 只证明"驱动成功 + 证据齐全"，不等于"人工已验收"。
- 完整环境（3.11.9）跑通三项 + 人工签字后，才可在 round7 评估把组 A
  对应开放项从"未验证"推进为"已人工验收"。

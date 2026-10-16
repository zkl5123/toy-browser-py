# toy-browser-py 改进计划（基于第四轮实测评估报告 2026-10-16）

> **依据报告**：`assessments/20261016/docs/实测评估报告.md`（第四轮，提交 `305163d`，对照 Edge 155.0.4283.45）
> **执行方式**：**一轮一个阶段**（7 个阶段，外加第 0 轮统一策略上下文）。每轮完成并验收后，再开下一轮。
> **基线（计划制定时已核实）**：`test.py` 925/925 全绿；性能哨兵 3000 节点 7.09s（阈值 12s）、10k 节点 22.0s（阈值 ~29.4s）**均通过**——第二轮报告所记"哨兵持续 FAIL"已在 P34.x 轮次消除，**不在本计划范围**。
> **验收总原则**：按真实浏览器语义实现（用户决策）；真实网络走注入式 transport（用户决策）；第四轮探针固化为离线回归（用户决策）。每轮验收 = ① `test.py` 全绿（新增探针后 >925）② 受影响场景的 `assessments/20261016/functional_summary` 判定翻转为目标值 ③ 不破坏已对齐项（48 几何探针 / 47 原始 JS 例 / 网络 8/8 / GUI 点击与导航）。

---

## 现状核实（计划依据）

报告与代码逐行核对后，遗留缺陷清单（均经代码定位确认）：

| 编号 | 缺陷 | 代码定位 | 报告证据 |
|---|---|---|---|
| C1 | GUI 文档 CSP 未生效：GUI 取 `getattr(resp, "content_security_policy", "")` 恒空串；`HttpResponse` 无该属性（但已有 `csp_directives` property） | `toy_browser/app/gui.py:601`、`toy_browser/network/net.py:276` | `csp_self`/`csp_inline` GUI 误放行 |
| C2 | 脚本顺序错：外部脚本被延后到内联之后 | `toy_browser/scripting/js.py:6208–6232`（"未声明 src 兜底补充执行"段） | `script_order` 期望 `SAFE\|EXTERNAL\|INLINE`，实际 `SAFE\|INLINE\|EXTERNAL` |
| C3 | 重复 src 只执行一次 | `toy_browser/scripting/js.py:6218`（`_declared` 去重） | `repeat_external` 期望 `EXTERNAL\|EXTERNAL`，实际 `EXTERNAL` |
| C4 | `type=application/json` 数据块被当 JS 执行 | `toy_browser/scripting/js.py:6178` 循环不查 `type` | `data_script` 期望 `SAFE`，实际 `EXECUTED` |
| C5 | CSP nonce 匹配未通（CLI 合法 nonce 被挡） | `toy_browser/scripting/js.py._csp_inline_allowed` | `csp_nonce` CLI 判 `SAFE`（Edge `EXECUTED`） |
| C6 | fetch 为"已兑现假 Response"（status=0, text 字符串） | `toy_browser/scripting/js.py:5035` `_fetch` | `fetch_real` 期望 `200:function` |
| C7 | XHR 纯占位，不触发 onload | `toy_browser/scripting/js.py:4380` `_xhr_ctor` | `xhr_real` 期望 `200:NETWORK_OK` |
| C8 | Mutation/ResizeObserver 仅记录不回调 | `toy_browser/scripting/js.py:4400/4417` | `mutation` 期望 `[mutated,checkpoint]`、`resize` 期望 `[resized]` |
| C9 | 行内排版/绘制盒模型：flex 含 padding/border 色块边界偏移（Edge `63,6,237,36` vs 项目 `67,10,229,28`）；基础页文字重叠、粗体 span 横飞、表格拆词 | `toy_browser/layout/layout.py` flex cross 轴 + `toy_browser/rendering/paint_engine.py` 背景/边框填充 | 报告"几何已对齐但像素仍有错误"表 |
| C10 | 功能行为未进持续回归（`typeof` 通过 ≠ 功能可用） | `test.py` 缺探针 | 报告 P1 末两条 |

> C2/C3 根因同为 `execute_scripts_in_order` 尾段（6208–6232），按一个修复单元处理。

---

## 阶段划分（一轮一阶段）

### 第 0 轮 · P0 — 统一文档策略上下文（C1 根因 + 地基）

**目标**：GUI/CLI/刷新/历史返回/子资源加载共用同一份"文档策略上下文"，杜绝"CLI 测过策略、GUI 没接"的分叉。按真实浏览器语义：文档的 `Content-Security-Policy` 响应头由文档来源提供，子资源加载也用它门控。

**改动**：
1. 抽 `DocumentPolicy` 数据类（`toy_browser/network/net.py`）：`doc_origin` + `csp_directives`（已解析 dict）+ CORS 上下文，供 CLI/GUI/历史返回构造并传递。
2. `toy_browser/app/gui.py:601` 由 `getattr(resp, "content_security_policy", "")` 改为走 `DocumentPolicy`（内部用 `resp.csp_directives` property，`toy_browser/network/net.py:331` 已实现响应头解析）；`toy_browser/app/gui.py:622` 的 `net.parse_csp_directives(_doc_csp)` 同步改为读该上下文。
3. CLI（`toy_browser/app/browser.py` / `main.py` 的脚本执行入口）改为构造同一 `DocumentPolicy` 传入 `execute_scripts_in_order` 与 `load_subresources`。
4. 历史返回（`go_back`/`go_forward`）与刷新路径复用同一上下文，不重新取空串。

**验收**：`csp_self`、`csp_inline`、`cross_css`、`csp_nonce` 四场景 **CLI 与 GUI 同判**（Edge 阻止的二者都阻止，Edge 放行的都放行）。`test.py` 全绿。

---

### 第 1 轮 · P0 — 脚本执行模型（C2 + C3 + C4）

**目标**：相对/绝对 src 按文档顺序执行；相同 src 两次执行两次；JSON 数据块不执行。按 HTML spec 的"collect scripts → run each in document order"。

**改动（`toy_browser/scripting/js.py:execute_scripts_in_order`）**：
1. **删除 6208–6232 "未声明 src 兜底补充执行"段**——它是顺序错乱（C2）与去重（C3）的共同根因。
2. 文档序遍历每个 `<script>` 元素：外部元素 `code = cache[resolve(src, base)]`，`code` 为 None（拉取失败/被策略挡）则跳过不阻塞后续。
3. **src 用文档最终 URL + base 解析**：`net.parse_url(src, base=eff_base)` 归一绝对 URL 再查 cache（cache key 同步改绝对 URL），消除"相对 src 查不到"。
4. **类型过滤**：读 `el.attr("type")`，非 `text/javascript`/`application/javascript`/`text/ecmascript`/`module`（空或 JS 类型）的（如 `application/json`）视为数据块**不执行**；`module`/`nomodule` 按规范记录为"module 暂不执行"，但不得把 JSON 块当 JS 跑。

**验收**：`script_order` → `SAFE|EXTERNAL|INLINE`；`repeat_external` → `SAFE|EXTERNAL|EXTERNAL`；`data_script` → `SAFE`。三者 CLI/GUI 一致。`test.py` 全绿。

---

### 第 2 轮 · P0/P1 — CSP 统一执行 + nonce（C5）

**目标**：`'self'` 正确匹配文档 origin；nonce 带引号/大小写敏感正确匹配；GUI/CLI 一致。

**改动**：
1. 核实并修 `toy_browser/scripting/js.py._csp_inline_allowed` 的 nonce 比较：`el.attr("nonce")` 与 directive 中 `nonce-…` token **精确相等**（CSP3 区分大小写，token 可带引号需剥引号）。
2. `toy_browser/network/net.py` 的 `'self'` 展开：`doc_origin` 非空时 `'self'` → 文档 origin，匹配同源资源；directive 收集 `'self'`/`nonce-…`/带引号 token 时统一处理。
3. 配合第 0 轮 `DocumentPolicy`，把 nonce 上下文带进 CLI/GUI 两入口。

**验收**：`csp_nonce` 合法 nonce → `EXECUTED`（CLI 与 GUI 同判）；不匹配 nonce → 阻止；`'self'` 禁止的跨源样式 CLI/GUI 都白底。

---

### 第 3 轮 · P1 — 真实 fetch / XHR / Response（C6 + C7）

**目标**：本机服务可读到状态与正文；网络失败走 reject；`text()/json()` 是可调用方法；XHR 触发 onload。按真实浏览器语义，真实网络通过**注入式 transport** 实现（离线默认安全）。

**改动**：
1. `js.Host` 增加可选 `fetch_transport`（默认 `None` 保持离线安全；CLI/GUI 网络页注入真实 `net.HttpClient`，与 `load_subresources` 共用同一 keep-alive client，连接池复用）。
2. `_fetch(url)`（`toy_browser/scripting/js.py:5035`）：transport 为 None 时维持现状（已兑现 ok=False）；transport 可用时真发请求，**失败走 reject**（不再 resolve 假对象），成功 resolve 到带 `status`/`ok`/`text()`/`json()` **方法**的 Response 对象（报告明确 `text` 应为 function）。
3. `XMLHttpRequest`（`toy_browser/scripting/js.py:4380`）：注入 transport 后 `open/send` 真发，响应到达时回调 `onload`；无 transport 维持占位（教学模拟标注"未完成"）。

**验收**：`fetch_real` → `200:function`；`xhr_real` → `200:NETWORK_OK`；断网/404 → 不进成功 then。离线默认路径（transport=None）不回归 925。

---

### 第 4 轮 · P1 — Observer 实际回调（C8）

**目标**：DOM 变更生成 MutationRecord 并回调；ResizeObserver 首次观察回调。

**改动**：
1. `MutationObserver`（`toy_browser/scripting/js.py:4400`）：Host 在 DOM 变更入口（`append_child`/`remove_child`/`insert_before`/`replace_child`/`set_attribute`，`toy_browser/scripting/js.py:5108+`）打补丁，变更入队后在微任务队列（与 `queueMicrotask` 同队列）里派发 MutationRecord 给已 observe 的节点。`functional_mutation_observer` 期望先 `mutated` 后 `checkpoint` → 回调须先于 promise 队列。
2. `ResizeObserver`（`toy_browser/scripting/js.py:4417`）：观察元素时立即（首次观察）输出 `resized`；后续布局尺寸变化再触发。

**验收**：两功能案例日志与 Edge 一致（`[mutated,checkpoint]`、`[resized]`）。

---

### 第 5 轮 · P1 — 行内排版与绘制盒模型（C9）

**目标**：基础页不再重叠/粗体 span 横飞/表格异常拆词；flex 含 padding/border 色块边界 1px 内。把"布局矩形、背景、边框、文字、命中测试"统一在**同一套几何契约**上，不能只调对外返回的矩形。

**改动**：
1. `toy_browser/layout/layout.py` flex 子项：cross 轴 margin/padding/border 的盒坐标与 `border_rect`/paint 一致（报告 `67,10,229,28` vs Edge `63,6,237,36` 说明绘制阶段再次扣了 padding/border）。
2. `toy_browser/rendering/paint_engine.py`：背景/边框填充用 border_box 坐标，不再二次减 padding。
3. 行内排版（`toy_browser/typography/text.py` 断行 + `toy_browser/layout/layout.py` inline 段拼接）：消除重叠、粗体 span 横飞、表格拆词。

**验收**：4 色块 PNG 联合边界 1px 内；`render_basic` 前景并集差异显著下降（当前 81%）。

---

### 第 6 轮 · P1 — 功能行为纳入持续回归（C10）

**目标**：把第四轮探针固化为离线回归，"不以 typeof 通过代替功能完成"。

**改动**：
- 将 `assessments/20261016/` 的 `layout_cases.json` / `js_cases.json` / `render_cases.json` 抽成 `tests/test_functional_*.py`（离线版：网络 API 用第 3 轮注入的假 transport，几何/JS 用本地夹具）；`test.py` 新增 TESTS 组接入。
- 48 几何探针保留并扩展；HTTP 策略、真实 API、交互任务（点击/导航/滚动）各加可离线复现的哨兵。

**验收**：`test.py` 全绿且包含上述功能探针；新缺陷可被回归哨兵捕获。

---

### 第 7 轮 · P2 — 性能与内存剖析（报告 P2，非阻断）

**目标**：产出分段耗时 + 峰值内存报告，验证滚动局部重绘（`changed_region`）；固定负载机器扩样本后再决定是否上 C 扩展光栅化。**剖析而非硬优化**。

**改动**：
- 对 style/layout/paint/PNG 编码分段计时；短页面交互位图生成目标 <500ms（1000 元素当前 1318ms，作下轮优化目标）。
- 验证 `changed_region` 驱动的局部重绘路径。

**验收**：产出分段耗时 + 峰值内存报告；1000 元素位图生成较当前下降（目标 <600ms，激进 <500ms）。

---

## 执行顺序与门禁

> **进度（2026-10-16 起）**：阶段 0/1/2 已提交（commit 305163d 之后）；
> 阶段 3（commit d971bb5）、阶段 4（commit d8320e0）、阶段 5（commit 69ec574）
> 已完成并提交；阶段 6（离线回归夹具）已完成并提交（commit d00c0cf）；
> 阶段 7（P2 性能剖析 + PNG 编码优化 + changed_region 局部重绘验证）已完成并提交。

1. 第 0 → 1 → 2 轮（P0：统一策略上下文 + 脚本模型 + CSP/nonce），每轮后跑 `test.py` + 对应 functional_summary 场景回归。 **[已完成]**
2. 第 3 轮（P1 真实 fetch/XHR，注入 transport 模式，离线可测）。**[已完成]**
   - 实现：`net.FetchTransport`（包装共享 keep-alive HttpClient，相对 URL 经文档 base 解析，网络失败→None/HTTP 错误→ok=False 响应）；
     `js._fetch` 真实请求 + `Response{text()/json()/arrayBuffer()}` 方法、失败 reject `TypeError`；
     `_xhr_ctor` 真实 open/send（onreadystatechange/onload/onerror + status/responseText/readyState）；
     `eval_assign` 对普通 Python 对象实例用 `setattr` 落地属性赋值（修复 `xhr.onload=fn` 被 `else: pass` 静默丢弃）；
     `NewExpr` 内置类构造器正确实例化；toy_browser/app/browser.py/gui.py 注入 transport；
     GUI 补 `eff_client`（load_url 未传 client 时自建，避免 `FetchTransport(None)` 抛 AttributeError 被静默吞掉）；
     `execute_scripts_in_order` 尾部补 `flush_microtasks()`（V8 序：同步→计时器→微任务）使 fetch().then 落地后再渲染。
   - 结果：e2e `fetch_real`→`200:function`、`xhr_real`→`200:NETWORK_OK`，**CLI 与 GUI 均与 Edge 一致**；
     `compare_js` 49/51（`functional_response_methods` 翻转匹配，离线 fetch 改 reject 以镜像真实网络失败）；
     `test.py` 925/925；geometry 15/17（2 项既有浮点差，非回归）；net 全 ok。
3. 第 4 轮（P1 MutationObserver/ResizeObserver 真实回调 + 离线夹具）。**[已完成]**
   - 实现：`js._mutation_observer_ctor` 改为真实 `observe`/`unobserve`/`disconnect`，
     注册到 `Host._mutation_observers`（按目标节点键控）；
     `js._resize_observer_ctor` 改为 `observe` 时向微任务队列投递初始回调
     （V8 ResizeObserver 异步首次回调契约）；
     新增 `Host._queue_mutation` 分发器：在 `set_element_prop`（textContent/innerHTML/
     属性写）与 `DomElement.append_child`/`insert_before`/`remove_child`/`remove`/
     `replace_child`/`set_attribute` 每个变更入口调用，将回调入队为微任务
     （与 Promise `.then` 同队列，保证 `mutated` 先于 `checkpoint` 落地）；
     去重哨兵（`_mo_pending`）防止同一 flush 批次内多次入队。
   - 结果：`compare_js` **51/51** 全匹配（`functional_mutation_observer` 翻转
     `['mutated','checkpoint']`、`functional_resize_observer` 翻转 `['resized']`）；
     `test.py` 925/925；network_e2e 10/10 仍匹配 Edge；net 8/8；geometry 15/17
     （2 项既有浮点差，非回归）。
4. 第 5 轮（P1 绘制盒模型 + 内联排版统一 C9）。**[已完成]**
    - 实现（`toy_browser/rendering/paint_engine.py`）：`_emit` 的背景填充/渐变/阴影/边框环/
      svg-video 占位/控件/焦点环/outline 全部改到 **border-box** 坐标
      （origin = 内容盒原点对 `border_rect()` 契约减去 padding，size = 内容+padding+border），
      并做整数对齐（floor origin + ceil 尺寸）使 1px 栅格边框环落在像素边界；
      文字仍画在内容盒原点（`r.x`/`r.y` + transform 偏移，不再二次加 padding），
      滚动裁剪后处理仍锚定内容 rect + padding insets。
    - 实现（`toy_browser/layout/layout.py` 内联行）：C9 行内排版修复——裸 TEXT 片段按**实测文本宽度**
      （而非容器全宽 `max(run_w, avail)`）排版，inline 元素不再额外叠加
      `char_advance` 整格（消除粗体 span 横飞 / 文字重叠）；
      `w = cb.rect.w + padding` 统一为内容宽 + padding 的几何契约。
    - 结果：`test.py` **931/931**（925 基线 + 6 个 C9 回归探针）；
      4 色块绘制像素边界匹配 Edge（flex_padding_border [62,5,238,37] vs Edge
      [63,6,237,36]，1px 容差内；其余 3 项精确）；
      17 项几何探针 **45/48**（3 项既有浮点差：table collapse / grid 1fr2fr，
      非回归）；render_* 前景占比贴近 Edge（render_basic 几何对齐后无重叠/横飞）；
      compare_js 51/51；network_e2e 10/10 CLI+GUI 匹配 Edge；GUI 交互/滚动/历史全绿。
5. 第 6 轮（回归固化），把探针写进 `tests/`。 **[已完成]**
    - 实现：新增 `tests/fixtures/r4_regression.json`（离线夹具，固化
      20261016 评估的 Edge 参考值）+ `tests/test_r4_regression.py`
      （5 个回归门，注册进 `test.py`）：
      * **JS 引擎**：评估的 51 个 JS 用例经 toy `js.Host` 离线回放，
        45 个与 Edge 参考完全匹配的用例冻结为本门（`js_object_methods`
        的 JSON 报错措辞、`js_optional_nullish` 的行尾空格为 toy 与 V8
        的已知差异，由评估 `compare_js` 门跟踪，不入此门）；
        `while(true)` 步进预算哨兵保留（性能门不破坏）。
      * **布局几何**：17 个文档的探针元素 border-box（
        getBoundingClientRect 契约）对照 Edge 参考，严格相等；
        3 项既有浮点差（table collapse 高 / grid 1fr2fr 分割）
        以显式 known-diff 集跟踪，任何 *新* 差异即失败（45/48 契约）。
      * **绘制红像素**：4 个色块文档的可见纯红边界在 1px 内匹配
        Edge GPU 截图参考（1px 栅格环 vs CSS 2px border 的既定容差）。
      * **网络/CSP e2e**：10 个 e2e 页面离线回放——文档 + CSP 指令来自
        夹具，子资源/外链脚本/fetch/XHR 响应来自内存夹具（无 socket、
        无线程、无真实服务器），#probe 文本匹配 Edge 参考。
    - 结果：`test.py` **936/936**（931 基线 + 5 个离线回归门）；
      4 色块红边界 / 17 几何探针 / JS / 网络 e2e 全部固化，
      评估探针固化为离线回归（第四轮计划"把探针写进 tests/"落地）。
6. 第 7 轮（P2 剖析）收尾。 **[已完成]**
    - 实现：新增 `tools/prof_r4.py`（分段剖析器）+ `assessments/20261016/perf_r4_profile.json`
      （报告产物）；`toy_browser/app/browser.py` 的 PNG 编码路径（`write_png` / 新增 `write_png_bytes`）
      重构为预分配 buffer + 逐行写（`_png_pack_row` / `_png_raw_rows`），输出字节与
      旧实现**逐位一致**（真实渲染 / 800×600 随机像素 / 400×16000 / 1×1 / 0 高五种
      位图全部验证），PNG 段从 ~3.8s 降到 ~0.8s（约 4.7×）。
    - 结果（本机型，min of 3 samples；纯 CPU，无 PIL）：
      | 负载 | parse | css | layout | paint | png | render（parse+css+layout+paint） | 峰值 RSS |
      |---|---|---|---|---|---|---|---|
      | 100 | 0.7ms | 2.6ms | 117ms | 218ms | 815ms | **338ms** | 45MB |
      | 1000 | 68ms | 401ms | 1157ms | 679ms | 815ms | **2304ms** | 97MB |
      | 5000 | 351ms | 2013ms | 6063ms | 2781ms | 828ms | **11207ms** | 237MB |
      | 10000 | 697ms | 4073ms | 12274ms | 5415ms | 816ms | **22457ms** | 575MB |
      1000 元素 render 段 2304ms（含 PNG 3120ms），未达 <600ms 目标——瓶颈在
      layout（1157ms，`build_box` 递归 + 边缘解析）与 css（401ms，`_apply_subtree`
      级联 + 声明解析），非 PNG。C 扩展决策点按计划留待固定负载机器扩样本后再定，
      不在 stdlib 引擎范围内硬优化。
    - `changed_region` 局部重绘路径验证：100 元素文档（~2200px 内容，800×600 视口）
      显示列表下移 300px 前后两帧，diff 包围盒 (10, 2, 100, 598)、diff 像素 36069 /
      480000 = **7.5%**（局部条带，非整帧），满足"局部重绘前置条件"。
    - 结果：`test.py` **936/936**；PNG 路径改动为字节等价重构，所有 P21/P22
      网络渲染、几何、JS 回归门全绿。

**每轮门禁**：
- `python test.py` 全绿（新增探针后 >925）；
- 受影响场景判定翻转为目标值：`csp_self`/`csp_inline`/`script_order`/`repeat_external`/`data_script`/`csp_nonce`/`fetch_real`/`xhr_real` 的 CLI 与 GUI 同时匹配 Edge；
- 不破坏已对齐项（48 几何探针、47 原始 JS 例、网络 8/8、GUI 点击与导航历史、17000px 滚动）。

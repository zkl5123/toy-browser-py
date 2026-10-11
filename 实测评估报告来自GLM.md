# toy-browser-py 真实浏览器标准实测评估报告

> 评估日期：2026-10-11 ｜ 评估环境：Linux x86_64, Python 3.12.14, 纯净系统（含 Noto Serif SC 等 CJK 字体）
> 对照基准：headless Chromium（agent-browser 0.38.1）
> 评估方法：双端对照实测——同一组页面同时在真实浏览器与 toy-browser 中加载，逐项比对几何数据、JS 求值结果、控制台输出与渲染截图；另做网络、性能、健壮性、GUI 专项。

---

## 一、执行摘要

| 维度 | 结论 |
|---|---|
| 架构与工程质量 | ★★★★☆ 纯标准库、模块解耦、文档详尽、自带 807+ 测试 |
| 网络栈 | ★★★★☆ TLS/gzip/重定向/子资源并发真实可用，能渲染真实互联网页面 |
| 布局引擎 | ★★☆☆☆ 基础块布局与文本几何正确，但级联、边距塌陷、行内混排、Grid 存在系统性错误 |
| JS 引擎 | ★★☆☆☆ 语义一致率 51%，一处语法不识别即整脚本静默失效 |
| 渲染保真 | ★★☆☆☆ 综合真实风格页面与 Chromium 像素差异 66% |
| 健壮性 | ★☆☆☆☆ 一个 em-dash 崩溃整个渲染管线；网络默认无超时可永久挂起 |
| 性能 | ★☆☆☆☆ 5000 元素页面比 Chromium 慢约 95 倍 |

**一句话总评**：这是一个架构设计出色、教学价值很高的"渲染管线 + 网络栈"演示项目；但按"真实可用浏览器"标准衡量，当前在任何真实网页上都会出现：JS 大概率整体失效、布局系统性偏移、遇到非常用字符即崩溃。距离能正常打开一个真实网页还差若干关键修复。

---

## 二、基线状况（自带测试套件）

- `test.py` 主套件：**798 / 807 通过**，9 条失败
- `test_r1~r5 / test_mem / test_web_api / test_p2 / test_p3`：全部通过（exit 0）
- 失败项集中在：**本机无 CJK 字体时的 8 条**（`'NoneType' object has no attribute 'ascent'`，涉及 `gui_page_load_file` / `gui_back_forward` / `p32_main_devtools_flag` 等——即 **demo.html 本身在本机无法渲染**）+ 1 条 `text_measure_consistency`（measure 44.49 vs TTF 总计 33.80）
- 性能基准 `p2_bench_1000_nodes_under_100ms`：实测 1596ms，超阈值 15 倍，但因"软阈值"仍标记通过——**测试存在掩盖问题的设计**

> 注：失败原因并非环境偶然——Linux 上几乎任何未预装 simhei/simsun 的机器（服务器、CI、容器）都会复现。

---

## 三、主要发现（按严重度分级）

### P0 —— 崩溃 / 页面级失效（真实浏览器绝不如此）

**P0-1 缺字形 → 整条渲染管线崩溃**
- 复现：`python3 main.py demo.html out.ppm` 在含 `—`（em-dash）的页面直接 AttributeError 崩溃、退出码 1、无输出。
- 根因链：`ttf_font.find_cjk_font()` 按**文件名字面**匹配（`noto.ttf`/`cjk.ttf`/`wqy.ttf` 等）→ 本机实际存在的 `NotoSerifSC-Black.ttf` 匹配不上 → `font._cjk_fallback = None` → `text.py:857 _face_baseline(None)` 崩溃。
- 真实浏览器行为：缺字形渲染 tofu（□）或逐级字体回退，永不崩溃。
- 连带：项目自带 demo.html 都无法在纯净 Linux 上渲染（README 第一条示例命令失败）。

**P0-2 JS「全有或全无」：一处语法不识别，整个 `<script>` 静默消失**
- 实测 `t6_js.html` 24 条 console.log 输出为 **0 条**。
- 根因：`parse_program` 一次性解析整段脚本，任何一处不支持即抛 SyntaxError；`Host.execute` 捕获后写入 `self.log`——而 `_run_scripts` 传入的 log sink 是 `lambda _s: None`，**错误被彻底丢弃**。
- 实测会触发整脚本失败的真实常见写法：
  - `"x:" + typeof y`（typeof 出现在二元表达式，`SyntaxError expected ')'`）
  - 三元 `cond ? a : b`（`expected ')', got '?'`）
  - `function A(){};` 函数声明后的孤立分号（`unexpected token ';'`）
  - 双引号字符串内 `\"` 转义
- 真实浏览器行为：即使有语法错误也向 console 报错，其余脚本块照常执行。
- 后果：真实网页几乎必含上述写法之一 → **JS 功能整体失效概率接近 100%**。

**P0-3 网络层默认无超时，不可达主机 = 渲染永久挂起**
- `render_document_from_url` 的 `timeout: float | None = None` 默认值会**覆盖** `HttpClient` 自身的 10s 默认 → 挂起实测 55 秒+ 无任何反馈、无法取消。
- 真实浏览器行为：连接超时约 20~60s 后显示错误页，UI 保持响应。
- 另：`timeout=3` 时实际 6.0s 才报错（超时预算疑似在连接/读取两阶段各计一次）。

### P1 —— 系统性兼容错误（影响几乎所有真实页面）

**P1-1 级联缺少「起源优先级」：作者 `*{margin:0}` 输给 UA `body{margin:8px}`**
- 实证：`*{margin:0;padding:0}` 页面中 `div#t.x = 8.0`（期望 0）。
- CSS 规范：作者样式（任意选择器、任意 specificity）恒胜 UA 样式（除 `!important`）。当前实现只按 specificity 比较 → **所有使用通配符 reset 的真实网页整体偏移 8px**。

**P1-2 `<head>/<meta>/<style>` 未设 `display:none`，占据布局空间**
- 盒树转储：`head y=0 h=19.2`，body 被推到 y=27.2 —— 所有页面系统性下移约 19~27px。

**P1-3 相邻兄弟外边距塌陷语义错误：塌成 0 而非 max()**
- 实证：`#top`（margin-bottom:30px）+ `#bot`（margin-top:10px）间距应为 30px（取 max），实测 0px。
- 父子塌陷行为碰巧近似正确。

**P1-4 行内元素各占容器整宽，行内混排视觉散架**
- 盒树转储：`before <b>bold</b> middle` 中 `<b>` 宽 400px（=容器宽），后续文本 x 坐标被推到 821 / 1221px。
- 像素实证：t5 行内流容器（400px）右侧溢出文字像素采样 140 个。
- 真实浏览器：行内盒子按文字实际测量宽度排列。

**P1-5 JS 语义一致率 60/118（51%），核心缺陷清单**

| 类别 | 缺陷 | 实测（真实 → 玩具） |
|---|---|---|
| 运算符 | `typeof` 基本不工作 | `"s" → s`、`undefined → None`、`function(){} → SyntaxError` |
| 运算符 | `%` 采用 Python 规则 | `-7%3：-1 → 2` |
| 运算符 | `==` 无强制转换 | `1=="1"：true → false` |
| 运算符 | 优先级错误 | `x%2===0` 被解析为 `x%(2===0)` → ZeroDivisionError |
| 数值 | `Math.round` 银行家舍入 | `2.5 → 3` 变 `2.5 → 2` |
| 数值 | `1/0` 抛 ZeroDivisionError | 应为 `Infinity` |
| 语义 | `this` 绑定失效 | `obj.get()` → `None`（应 42） |
| 变异 | `sort/splice` 不原地生效 | 语句形式 `arr.sort(fn)` 后数组未变（表达式形式返回新数组恰好正确） |
| 语义 | `undefined+1`/`null+1`/`NaN+1`/`"5"-3` 全部抛 TypeError | 应为 NaN/1/NaN/2 |
| 字符串 | 布尔字符串化 | `false → "False"` |
| 字符串 | `replace` 替换全部而非首个 | `"aaa".replace("a","b")：baa → bbb` |
| 字符串 | 字符串索引 `"abc"[1]` 不支持 | `→ None` |
| DOM | `document.title` 默认值 | `"" → None` |
| 语法 | `?:` 三元、`in`、`instanceof`、ES6+（class/arrow/template/解构/spread/async）不支持 | 全部 SyntaxError |

（t6 中 24 项语义测试实际全部因 P0-2 而未执行；上表为逐项直连 Interpreter 的结果。）

**P1-6 CSS Grid 三连缺陷**
- `grid-template-columns: 100px 1fr 2fr`：`fr` 比例被当等分（三条 track 均为 200px，期望 100/166.7/333.3）
- `repeat(3, 50px)` 完全不识别 → 退化为纵向块堆叠、子项宽 180px（期望 50px）
- 容器显式 height:90px 被 30px 行高覆盖

**P1-7 `transform` 不进布局**：`translate(15px,25px)` 仅在绘制阶段生效，几何查询/命中测试类语义缺失（视觉近似正确，但 `getBoundingClientRect` 等价语义缺失）。

### P2 —— 中等/局部问题

- **P2-1** 文本宽度度量不一致（`text_measure_consistency`：measure 44.49 vs TTF hmtx 合计 33.80）——letter-spacing 重复计入或 advance 口径不一。
- **P2-2** 表格宽度：`<table>` 被拉到 800px 全宽（真实按内容收缩 ~29px），td 同理；`<input>` 默认宽 160 vs 真实 185。
- **P2-3** 视觉保真：真实风格综合页（header/nav/flex 卡片/表格/表单/footer）与 Chromium 截图**采样像素差异 66%**：header 仅 33px 高（真实 ~54px+）、footer 不在首屏出现、内容整体压缩。
- **P2-4** `render_document_from_url(url, css_text, width, height)` 第 2 个位置参数是 `css_text`——与直觉（width）相反，实测即被坑；两个渲染入口签名易误用。
- **P2-5** cookie 会话保持非默认：裸 `HttpClient()` 的 Set-Cookie 需显式挂 cookie jar（`cookies=`），`render_document_from_url` 未接 session 层 → 登录态跨页流程默认失效。
- **P2-6** GUI 可在 Xvfb 下正常启动运行（无崩溃），但同一 CJK 崩溃问题在 GUI 路径同样存在。

---

## 四、性能实测（对照 Chromium）

**渲染管线分阶段计时（800×600，flex 行布局）**

| 元素数 | parse | style | layout | paint_emit | rasterize | 总计 |
|---:|---:|---:|---:|---:|---:|---:|
| 100 | 2.9ms | 3.8ms | 441.7ms | 74.2ms | 1302.2ms | **1.8s** |
| 500 | 93.8ms | 253.6ms | 1524.8ms | 382.9ms | 1194.8ms | **3.5s** |
| 1000 | 272.9ms | 512.8ms | 3061.4ms | 849.4ms | 1193.3ms | **5.9s** |
| 5000 | 1129.8ms | 2663.5ms | 15688.1ms | 4150.7ms | 1286.0ms | **24.9s** |

- 同一 5000 元素页面 Chromium DOMContentLoaded：**263ms（含网络）** → 慢约 **95 倍**
- 热点：layout（占 60%+）与 rasterize（全画布逐像素 CPU 绘制，恒定 ~1.2s）
- JS 引擎：200,000 次累加循环 2957ms ≈ **68k ops/s**（V8 约 10⁸~10⁹ ops/s，差 3~4 个数量级）
- 真实互联网页面（httpbin.org/html，含 TLS+gzip）完整渲染 8.7s

---

## 五、亮点（值得保持的设计）

1. **架构**：解析/样式/布局/绘制严格解耦、渲染核心零 I/O、会话层正交可插拔——符合真实浏览器分层。
2. **网络栈完成度最高**：TLS 证书验证（真实站点握手成功）、gzip 透明解码、多跳重定向（实测 2 跳 OK）、子资源并发抓取、缓存、进度回调、错误分类。**能真正渲染互联网页面**。
3. **文本排版几何正确**：换行、line-height、text-align 的容器几何与 Chromium 完全一致（仅整体偏移）。
4. **DOM 基础 API 可用**：getElementById / textContent 读写 / createElement / appendChild / children.length / 事件绑定全链路工作。
5. **测试与文档文化**：每阶段 ~20 测试 + CHANGELOG/ROADMAP/ARCHITECTURE/CONTRIBUTING 齐全，P 编号体系贯穿源码。
6. **内存治理**：资源上限、字形缓存、mem_guard（test_mem 全绿）。

---

## 六、改进建议（按优先级路线图）

### 🔴 第一优先：让它「不崩溃、不静默」（1~2 天工作量，收益最大）

1. **字体回退兜底**：`_resolve_face` 返回 None 时回退到内置 8×8 位图字体并按半宽渲染 CJK（画 tofu 也行），绝不向上抛 None。`find_cjk_font` 增加内容级探测（打开 TTF 查 cmap 是否覆盖 0x4E00-0x9FFF，而非只看文件名）——本机 NotoSerifSC 即可被命中。
2. **JS 错误可见化**：`_run_scripts` 把 log sink 改为收集到 `host.logs`（哪怕前缀 `[error]`）；`Host.execute` 的 SyntaxError 至少 console.error 一条。这一步立刻让"脚本为何没跑"可诊断。
3. **JS 解析容错降级**：`parse_program` 失败时按语句切分（`;`/换行/块边界）逐条解析，跳过失败语句执行其余部分——真实页面的脚本可用率会从 ~0% 立刻显著提升。
4. **网络超时兜底**：`render_document_from_url` 默认 `timeout=10`（None 仅作为显式 opt-out）；单次 fetch 的超时预算改为一次性总预算。

### 🟠 第二优先：布局正确性（1~2 周）

5. **级联引入三层排序**：`(origin, specificity, order)`——UA < 作者 < 作者!important；这一条修复后所有 reset 型页面立即归位。
6. **UA 样式表补**：`head,meta,style,script,title,link { display: none }`。
7. **外边距塌陷**：兄弟间取 `max`（当前实现疑似直接清零）；父子塌陷已近似正确，加负 margin 取 min-max 规则即可。
8. **行内排版改造**：行内元素宽度按文字测量宽度（`text.advance_text`）而非容器宽；行盒内 x 前进量 = 前一个行内盒的实际宽度。这是 t5 视觉散架与 realistic.html 差异的主因之一。
9. **Grid**：`fr` 按比例分配剩余空间；支持 `repeat(N, w)`（解析期展开即可）；track 尺寸参与容器高度计算。

### 🟡 第三优先：JS 语义补齐（2~4 周，按使用频率排序）

10. 高频缺件：`?:` 三元、`typeof` 全类型、`in`/`instanceof`、`%` 改截断除法（`math.fmod`）、`Math.round` 半值远离零、`1/0=Infinity`、`==` 强转表（ECMA 7.2.14 的 14 行规则表足够）、`this` 方法调用绑定、`sort/splice` 原地变异、`undefined/null` 算术 → NaN。
11. 变异方法回归测试：每个 `Array.prototype` 方法断言"调用后原数组状态"而非仅返回值。
12. 中期：`class` 语法糖（可 desugar 到现有 prototype 机制）、模板字符串、解构、`fetch` Promise 链真实化。

### 🟢 第四优先：性能（结构性，1~2 月）

13. layout 60% 热点：先加 profile 确认（嫌疑：每节点全量属性拷贝的 dataclass + O(n²) 的兄弟遍历）；P33.R5 的增量布局已开好头，把 `build_box` 从全量重算改为 dirty 子树重算。
14. rasterize 恒定 1.2s：改为 dirty-region 光栅化（`changed_region` API 已存在，接线即可）+ 跳过纯白区域。
15. JS 提速：ARCHITECTURE 已规划字节码 + 栈 VM；短期先做 AST 节点 memo 与运算符特化。

### 🔵 工程实践建议

16. **测试断言升级**：把"软阈值"改为分级报告（pass/warn/fail 分开统计）；为 8 项 P0/P1 各加一条"真实写法"回归用例（如 `"x"+typeof y` 必须解析成功）。
17. **引入双端对照测试**：本次的 probe 方法（同页同断言，Chromium 产出期望 JSON）可以固化为 `test_conformance.py`，用少量页面持续守护布局/JS 一致率。
18. **API 防呆**：`render_document_from_url` 把 `css_text` 改为 keyword-only（`*` 分隔符），杜绝位置参数误用。
19. **CI 矩阵**：在无 CJK 字体的 Linux 容器跑全量测试（当前 9 条失败即此类环境暴露）。

---

## 七、附：对照实验原始数据摘要

| 页面 | 比对维度 | 结果 |
|---|---|---|
| t1_block | 几何 | x 全对；y 系统偏移（head 占位 + 边距塌陷错误） |
| t2_flex | 几何 | flex-grow 分配正确；全局 x+8（级联起源 bug）、y+27 偏移 |
| t3_grid | 几何 | fr 等分化、repeat 不识别、高度覆盖 |
| t4_position | 几何 | 定位/相对偏移正确；transform 不进布局 |
| t5_text | 几何 | **w/h 全部匹配**（核心文本排版正确）；行内混排溢出容器 |
| t6_js | console | 24/24 未输出（parse 全灭） |
| t7_dom | console | getElementById/textContent/createElement/appendChild/children 全对；title=None 错误 |
| t8_htmlerr | 几何 | 隐式闭合/实体/table 结构基本正确；宽度语义差异（table 全宽化） |
| JS 语义专项 | 118 项 | 60/118 一致（51%） |
| realistic.html | 截图像素 | 66% 采样差异 |
| mc_test | 边距塌陷 | 兄弟塌陷=0（应为 max） |

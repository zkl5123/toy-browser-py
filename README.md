# toy-browser-py — Python 版浏览器内核（纯标准库）

`toy-browser`（Rust 版）的 Python 重实现。零外部依赖，仅使用 Python 3.11
标准库。实现完整的"HTML 到像素"渲染管线 + 玩具 JS 引擎。

## 架构

```
toy-browser-py/
├── main.py            CLI 入口（--a11y / --links / --png / --animate / P23 --insecure / --cache / --progress）
├── dom.py             P24 兼容 shim（转发 html_pkg；import dom / from dom import Node 不变）
├── html_pkg/          P24 HTML 解析器子模块（node / entities / tokenize / build，字节流输入）
├── css.py             CSS 解析 + 级联 + 选择器（后代/子/多类）+ @keyframes
├── layout.py          盒模型布局（块/内联/文本/占位/flex/grid + 命中测试）
├── paint.py           位图 + 颜色混合
├── paint_engine.py    显示列表 + CPU 光栅化 + 图片像素渲染
├── text.py            8×8 位图字体 + 文本测量/换行/绘制（P17 TTF 度量）
├── font.py            内建 ASCII 字体表 + P17 真实 TTF 度量注册
├── ttf_font.py        P17 TTF/OTF 解析器（head/cmap/hmtx/glyf/loca/OS/2）
├── rasterize.py       P20 二次贝塞尔曲线光栅化（扫描线 + 抗锯齿 + 合成粗体）
├── net.py             P21/P22/P23 URL + HTTP/1.1 客户端 + 重定向/gzip/chunked + 缓存 + TLS + 进度
├── js.py              玩具 JS 引擎（词法 + Pratt 解析 + Math/JSON/Date/RegExp）
├── browser.py         公开 API + 失效区域 + a11y + links + PNG + transition + animate
├── transition.py      P3.4 CSS 过渡引擎（属性插值 + 帧推进 + 统一 lerp）
├── gui.py             P31 tkinter GUI 外壳（零依赖，多标签页；P21 http 加载）
├── test.py            514 条测试（python test.py）
├── fonts/            P17 TTF 测试夹具（arial.ttf + arialbd.ttf）
├── ROADMAP.md         改进路线图（P0–P24 + P31 全部完成）
└── demo.html          示例文档
```

## 管线

```
HTML → dom.parse → css.apply → layout.layout → paint_engine.emit_commands → rasterize → PPM
```

P21 — 地址栏输入 URL（http/https）时：`net.parse_url` →
`HttpClient.get` → `fetch_html`（Content-Type 分派）→ 渲染管线；
网络失败 → 统一错误页。

P22 — 3xx 重定向链（≤5 层 + 循环检测）；gzip 透明解压；chunked 解析
钩子；`<link rel=stylesheet>` / `<img>` 子资源按文档 base（或
`<base href>`）加载，失败不阻塞渲染；`render_document_from_url`
一站式入口。

P23 — HTTPS（ssl 包裹，`--insecure` 关校验）；启发式磁盘缓存
（`Cache-Control` + 10% 启发式 TTL）；`ETag`/`If-None-Match` 304 协商
（`CacheStore`）；加载进度事件（`LoadEventSink` → M3.2 GUI 进度条）；
`HttpClient.post`（表单 urlencoded）。CLI 新旗标：`--insecure` /
`--cache DIR` / `--progress`。

P24 — HTML5 容错解析（M2 收官）：Parser 独立成 `html_pkg/` 子模块
（node / entities / tokenize / build），输入统一为字节流（UTF-8 /
UTF-16 BOM 嗅探 + Latin-1 回退）；原始文本模式（`<script>` /
`<style>` / `<textarea>` 内容不按 HTML 解析）；void elements
（`<br>` / `<img>` / `<input>` 不压栈）+ 自动闭合纠错（`<p>` 遇块级
/ `<li>` 遇 `<li>`）；格式化元素重构（`<b><i>x</b></i>` adoption
agency 简化版）；完整 entities 表（命名子集 + 十进制 / 十六进制数字
引用，码点 0 / 代理区 → U+FFFD）；注释 / DOCTYPE 丢弃、大小写不敏感
标签名、表格 foster parenting 玩具。`dom.py` 瘦身为兼容 shim，下游
零改动。

JS 引擎在样式/布局/绘制之前运行内联 `<script>`，并支持：

### DOM 绑定
- `document.getElementById` / `document.querySelectorAll`
- `element.textContent` / `element.style.color = …` / `element.src`
- `console.log`（自动转 JS 字符串格式：`true`/`false`/`null`/数字）

### 表达式与运算符
- `??` nullish coalescing
- `+=` / `-=` 复合赋值
- `===` / `!==` / `==` / `!=` / `<` / `>` / `<=` / `>=`
- `&&` / `||`
- 字符串拼接（`+`）
- **数组字面量** `[1, 2, 3]`
- **对象字面量** `{a: 1, b: "two"}`
- **数组方法**：`.push()`、`.length`、`.slice(start, end)`、`.indexOf(v)`、`.includes(v)`
- **字符串方法**：`.length`、`.includes(sub)`、`.indexOf(sub)`、`.split(sep)`、
  `.toLowerCase()`、`.toUpperCase()`、`.trim()`、`.replace(old, new)`
### 控制流
- `if` / `else` / `while` / `for`
- `let` / `const` / `var`（同义）
- `function` 声明与表达式
- `break` / `continue` / `return`

### 事件循环与安全
- `setTimeout`（同步 FIFO 定时器队列，`flush_timers` 在脚本结束后清空）
- **`requestAnimationFrame`**（入队回调，`flush_raf()` 在脚本结束后 FIFO 执行）
- **`Promise`**（玩具版：`.then` / `.catch` 链，同步 resolve/reject；`Promise(executor)` 函数形式）
- **`Host.logs`**（收集所有 `console.log` 输出，与 log 回调镜像）
- **坐标命中测试**：`layout.hit_test` + `browser.render_document_with_click` + CLI `--click x,y`
- **事件循环**：`render_document_with_event`（模拟点击 → 执行 `onclick` → 重渲染）
- **安全沙箱**：
  - `fetch()` 只允许 `http://` / `https://`，`file://` 与裸路径被拒绝
  - 跨源 `iframe.src` 赋值被静默丢弃，只接受同源
  - 远程 iframe 的子 DOM 排除在 JS 元素表之外

### 布局特性

| 特性 | 说明 |
|---|---|
| `display: none` | layout 与 paint 双侧跳过，兄弟元素正常排列 |
| `line-height` | px / em / 数字，影响文本盒高度 |
| 显式 `width` / `height` | 覆盖内容推导尺寸（CSS 或 DOM 属性） |
| `text-align` | left / center / right |
| 内联元素 | span / em / a / code / b / i / u / mark 与文本共享同一行 |
| **`<img>` 占位** | 灰色 32×32 占位框 + 1px 边框 + `img: <src>` 角标文字；支持 `width` / `height` 属性 |
| **`<input>` 占位** | 160×20 占位框 + 1px 边框 |
| **`<button>` 边框** | 默认 UA 样式表：1px 边框 + 4px 内边距 |
| **CSS 选择器扩展** | 后代（`a b`）、直接子（`a > b`）、多类（`.a.b`） |
| **`display: flex`** | 子元素水平/垂直排列；`justify-content: start \| center \| end \| space-between` |
| **`flex-grow`（P3.2）** | 剩余空间按权重比例分配给 flex-grow 子项 |
| **`align-items`（P3.2）** | 交叉轴对齐：stretch / start / center / end |
| **`opacity`** | 0–1 透明度，背景/边框/文字 alpha 混合 |
| **`overflow: hidden`** | 解析并存储到布局盒，paint 层裁剪语义 |
| **完整 UA 样式表** | h1–h6、ul/ol/li、table/tr/td/th、hr、pre、blockquote、textarea/select、a 链接色 |
| **内嵌图片解码（P3.1）** | `data:` URI base64 解码（原始 RGB + 最小 PNG 解码器）；无显式尺寸时用固有像素尺寸 |

### 内嵌图片（P3.1）

- `<img src="data:image/octet-stream;base64,…">` — 原始 RGB 字节（w×h×3）直接映射。
- `<img src="data:image/png;base64,…">` — 最小 PNG 解码器（IHDR + zlib IDAT + 5 种行滤波器，8-bit RGB）。
- 无 `width` / `height` 属性时，使用像素缓冲的固有尺寸。
- `paint_engine` 发射 `DisplayCommand("img", …)`，光栅化阶段用最近邻缩放到盒尺寸。

### JS 内置对象（P3.3 / P4.2）

- **`Math`**：`abs`、`floor`、`ceil`、`round`、`sqrt`、`min`、`max`、`pow`、`PI`、`E`。
- **`JSON`**：`stringify`（dict/list/str/num/bool/None → JSON 字符串）、`parse`（JSON → Python 对象）。
- **`Date`**（P4.2）：`new Date()` 构造函数 + `getTime` / `getFullYear` / `getMonth` / `getDate` /
  `getHours` / `getMinutes` / `getSeconds` / `toString` / `toISOString`。
- **`RegExp`**（P4.2）：`new RegExp(pattern, flags)` + `.test` / `.exec` / `.replace` / `.split`
  （基于 Python `re`，支持 `i` / `m` / `s` 标志）。
- **`String`**（P4.2）：`String.fromCharCode(...codes)` + 字符串方法 `charCodeAt` / `charAt` / `substring`。
- **通用对象分派**（P4.2）：非标准 Python 对象的方法调用走 `getattr` 回退，属性访问同理。

### CSS Grid（P4.3）

- `display: grid` + `grid-template-columns: 1fr 1fr …` → 等宽列轨道。
- 子元素按列优先顺序填入网格，每个单元格宽度 = 容器宽度 / 列数。
- `grid-template-rows` 解析并存储。
- CLI：无需额外标志，`--animate` 可渲染网格动画。

### 多值简写（P4.3）

- `margin` / `padding` / `border-width` 的 1 / 2 / 3 / 4 值简写解析（`parse_shorthand`）。
  - 1 值 → 四边相同
  - 2 值 → 上下 / 左右
  - 3 值 → 上 / 水平 / 下
  - 4 值 → 上 / 右 / 下 / 左

### CSS 过渡（P3.4）

- `transition.py`：`TransitionSpec`（属性名 + 起/止值 + 帧数）+ `TransitionEngine`（帧推进 + 线性插值）。
- `browser.run_transition(html, css, w, h, element_id, start, end, frames)` → 返回 N+1 帧 `Bitmap`。
- 支持颜色（RGB 各通道插值）与标量（width/opacity 等）属性。
- 中间帧可逐帧导出为 PPM / PNG 序列。

### @keyframes 动画（P4.1）

- `css.py`：`@keyframes` 解析器（`from` / `to` / `NN%` 关键帧选择器，`Keyframe` / `Animation` 数据类）。
- `css.apply_animation_frame(styled, element_id, anim, t)` — 相邻关键帧之间做属性插值。
- `browser.animate_keyframes(html, css, w, h, element_id, anim_name, frames)` → 返回 N+1 帧 `Bitmap`。
- CLI：`--animate NAME --event ID --anim-frames N --anim-out DIR [--png]` 逐帧导出动画序列。
- 示例：
  ```css
  @keyframes fade {
      from { background: #ff0000; }
      to   { background: #0000ff; }
  }
  ```
  ```bash
  python main.py demo.html out.ppm --event box --animate fade --anim-frames 10 --anim-out frames/
  ```

### CSS 视觉特效深化（P5.1）

- **`border-radius`**：圆角矩形渲染（逐像素圆形测试），`paint_engine` 新增 `"rounded"` 命令。
- **`box-shadow`**：`box-shadow: Xpx Ypx Bpx color` → 模糊扩展 + 35% alpha 混合，`"shadow"` 命令。
- **`transform: translate(x, y)`**：平移整个盒子的渲染位置（不改变布局坐标）。

### CSS float（P5.2）

- `float: left` / `float: right`：浮动元素固定在容器指定侧，后续内联内容环绕。
- 简化版：浮动元素宽度占用容器一侧，后续块级/内联内容从对侧开始排布。

### JS 数组高阶方法 + console.assert（P5.3）

- **`Array`**（P5.3）：`map`、`filter`、`forEach`、`join`、`reverse`、`concat`。
- **`console.assert(cond, msg)`**（P5.3）：条件为假时记录 `ASSERTION FAILED: msg`。

### position: absolute（P6.1）

- `layout.py`：`position: absolute` + `top` / `left` → `LayoutBox.is_absolute` / `abs_top` / `abs_left`。
- 绝对定位子元素跳过文档流，相对父内容区按偏移放置。

### text-overflow / line-clamp（P6.2）

- `layout.py`：`text-overflow: ellipsis` + `line-clamp: N` 解析，向 `TEXT` 子盒传播。
- `paint_engine.py`：文本发射时按 `max_width` 截断并追加 `…`；`line-clamp` 限制行数。

### JS 内置对象深化（P6.3）

- **`Array.reduce(fn, init?)`**：累加器折叠，支持初始值或首元素两种模式。
- **`Object.keys(obj)` / `Object.values(obj)`**：跳过 `__` 前缀内部键。
- **`String.repeat(n)` / `padStart(len, fill)` / `padEnd(len, fill)`**。

### CSS z-index（P7.1）

- `layout.py`：`z-index` 解析 → `LayoutBox.z_index`。
- `paint_engine.py`：显示命令按 `z_index` 层稳定排序（高层后绘制，同层保持 DOM 序）。

### display: table（P7.2）

- `layout.py`：`display: table` + `tr`/`td`/`th` 识别 → `is_table` / `is_table_row` / `is_table_cell`。
- `_layout_table`：等宽列轨道，行高取该行最高单元格；`border-collapse` 存储。

### JS 数组高阶方法补全（P7.3）

- **`Array.find(pred)`**：返回首个匹配元素。
- **`Array.findIndex(pred)`**：返回首个匹配索引，无匹配返回 -1。
- **`Array.some(pred)`**：任一匹配返回 true。
- **`Array.every(pred)`**：全部匹配返回 true。

### position: sticky（P7.4）

- `layout.py`：`position: sticky` + `top` / `right` / `bottom` / `left` 全向偏移解析。
- 静态布局下 sticky 盒保持文档流位置，偏移量记录供未来滚动感知渲染器使用。

### CSS :hover / :focus 伪类（P8.1）

- `css.py`：`Selector.pseudo` 字段 + `matches(node, interaction_state)` 交互态匹配。
- `css.apply(dom_root, css_text, interaction_state)` 透传 hover/focus 状态；
  `:hover` / `:focus` 规则仅在状态匹配时级联。

### position: relative（P8.2）

- `layout.py`：`position: relative` + `top` / `left` 偏移解析 → `LayoutBox.is_relative` /
  `rel_top` / `rel_left`。
- 相对定位盒保留文档流占位，自身按偏移量平移（不影响兄弟流位置）。

### JS 方法补全（P8.3）

- **`Array.sort()`**：数值排序（无比较器）。
- **`Array.includes(v)`**：线性查找。
- **`Object.entries(o)`**：返回 `[[key, value], …]`。
- **`String.startsWith(prefix)` / `endsWith(suffix)`**。

### CSS 文本属性（P8.4）

- `layout.py`：`letter-spacing` / `word-spacing`（px）+ `text-transform`
  （`uppercase` / `lowercase` / `capitalize`）解析。
- `paint_engine.py`：文本发射前应用 `text-transform` 大小写变换。

### CSS @media queries（P9.1）

- `css.py`：`MediaQuery` dataclass + `parse_media_queries(css_text)` 提取 `@media` 块。
- `css.py`：`media_matches(condition, w, h)` 评估 `min-width` / `max-width` /
  `min-height` / `max-height`（支持 `and` 组合）。
- `css.apply` 新增 `media_rules` / `viewport_w` / `viewport_h` 参数；
  匹配的 media 规则并入级联。
- `browser.render_document` 全链路接入：解析 → 评估 → 级联。

### position:absolute / relative 全向偏移（P9.2）

- `layout.py`：`LayoutBox` 新增 `abs_right` / `abs_bottom` / `rel_right` / `rel_bottom`。
- `build_box`：`position: absolute` / `relative` 解析 `right` / `bottom` 偏移。
- `layout_block`：absolute 盒按 `right` / `bottom` 从父容器边缘反向偏移；
  relative 盒按 `right` / `bottom` 向反方向平移。

### JS 方法深化（P9.3）

- **`Array.flat(depth)`**：深度展平（默认 1 层）。
- **`Array.flatMap(fn)`**：map + 一层展平。
- **`Array.map` / `filter` / `forEach`**：回调增加第二个参数 `index`。
- **`Object.assign(target, …sources)`**：浅合并。
- **`String.trimStart()` / `trimEnd()`**。
- **`Number` 内置对象**：`isInteger` / `isNaN` / `parseInt` / `parseFloat`。
- **`Host.eval(code)`**：表达式求值 API。

### CSS 视觉效果深化（P9.4）

- `layout.py`：`clip-path: inset(T R B L)` 解析 → `LayoutBox.clip_*` 字段。
- `layout.py`：`background: linear-gradient(angle, c1, c2)` 解析 →
  `LayoutBox.gradient_*` 字段。
- `layout.py`：`text-shadow: X Y B color` 解析 → `LayoutBox.text_shadow_*` 字段。
- `paint_engine.py`：`rasterize` 新增 `"gradient"` / `"text_shadow"` 命令类型；
  `_draw_gradient` 逐像素双线性插值；`_apply_clip_path` 后置裁剪。
- `browser.py`：`render_document_with_hover(html, css, w, h, hover_id)` API。

### CSS :first-child / :last-child / :nth-child(n)（P10.1）

- `css.py`：`Selector` 支持 `:first-child` / `:last-child` / `:nth-child(N)` /
  `:nth-child(even)` / `:nth-child(odd)` / `:nth-child(An+B)`。
- `Selector._child_index(node)` / `_is_first_child` / `_is_last_child` /
  `_nth_matches` 辅助方法直接基于 DOM 结构匹配。
- `layout.py`：`LayoutBox.child_index` 字段记录 1-based 元素兄弟索引。

### JS Map / Set / Array.from / String.replaceAll（P10.2）

- `js.py`：`ToyMap` 类 — `set` / `get` / `has` / `delete` / `size` /
  `keys` / `values` / `entries` / `forEach`。
- `js.py`：`ToySet` 类 — `add` / `has` / `delete` / `size` / `values` /
  `forEach`（自动去重）。
- `js.py`：`Array.from(iterable, mapFn?)` / `Array.of(...items)`。
- `js.py`：`String.replaceAll(search, replace)`。

### CSS outline + background-image toy（P10.3）

- `layout.py`：`outline` / `outline-width` / `outline-color` 解析 →
  `LayoutBox.outline_width` / `outline_color`（缺省色 black）。
- `paint_engine.py`：`outline` 以 4 条 `rect` 命令在 border 外侧绘制空心环。
- `layout._raw_str` 修复：`Length` 对象正确返回 `"4px"` 而非 `"4.0"`。

### JS Error / 嵌套 JSON / Array.toString（P10.4）

- `js.py`：`ToyError` 类 — `message` / `name` 属性；`ToyTypeError` 子类。
- `js.py`：`JSON.parse` 改用 stdlib `json.loads` — 支持嵌套对象/数组。
- `js.py`：`Array.toString()` 逗号连接 + 嵌套数组扁平化。

### CSS transition 多属性 + transform（P11.1）

- `transition.py`：`TransitionSpec.value_at` 支持 `("translate", x, y)` 元组插值。
- `transition.py`：`TransitionEngine.add_multi` 批量添加；
  `parse_transition_css_multi` 解析逗号分隔多属性
  （`background 1s, opacity 2s, transform 1s`）。
- `lerp_value` 支持 transform 元组。

### JS try / catch / finally + throw（P11.2）

- `js.py`：AST `Try` / `Throw` 节点 + `parse_statement` 识别 `try` / `throw`。
- `js.py`：`_Throw` 异常携带值；`Interpreter.this_value` 绑定 `this` 关键字；
  `Host.execute_event` 将 `this` 绑定到目标元素（onclick 中
  `this.style.display = 'none'` 可用）。
- 未处理 throw 记 log 不崩溃。

### CSS @supports + display 切换（P11.3）

- `css.py`：`SupportsRule` dataclass + `parse_supports_blocks` 提取
  `@supports` 块；`supports_matches` 玩具永远为 True；
  `css.apply` 新增 `supports_rules` 参数并入级联。
- `layout.py`：display:none 盒保留在布局树中（`is_display_none` 标志 +
  零尺寸 rect），flex / grid / table 布局跳过隐藏子项。

### 命中测试 + 事件坐标 API（P11.4）

- `browser.py`：`element_at(html, css, w, h, x, y)` →
  `{"id", "tag", "rect"}` 或 `None`。
- `browser.py`：`event_target(html, css, w, h, x, y)` 命中并执行 onclick，
  返回元素 id。
- `browser.py`：`click_and_render(html, css, w, h, x, y)` → `(id, Bitmap)`。
- 全部渲染 API 接入 `@supports` 级联。

### 多属性 @keyframes + Web Animations 迷你 API（P12.1）

- `css.py`：`KeyframeTrack` dataclass（property + samples + duration）+
  `AnimationHandle`（tracks、`frame_overrides(t)`、`update(t)`、
  `playState` / `currentTime` / `iterations` 字段）。
- `css.build_keyframe_handle(anim)` — 将 `@keyframes` 声明按属性分组为
  独立 track；`parse_keyframes` 提取 `duration` / `iteration-count`。
- `transition.lerp_value` 新增 Keyword↔scalar 数值处理。

### JS class / extends / constructor / super（P12.2）

- `js.py`：AST `ClassDecl` / `NewExpr`；`parse_class` 解析
  `class X extends Y { constructor(…) { super(…) } method() { super.m() } }`。
- `ToyClass` — dict 实例；构造函数设置 `this.x` 字段；`super()` 通过
  延迟 `_run_parent` 执行父类 ctor 一次；`super.method()` 解析父类方法。
- `Interpreter.this_value` + `this` / `super` 标识符解析；
  `Host._class_registry` 存储 ToyClass；`Host._interp()` 工厂方法。
- `new X()` 先查类注册表再回退到内置构造函数
  （`new Date()` / `new RegExp()` 不受影响）。
- Pratt 解析器修复：`*` / `/` / `%` 左结合，`+` / `-` 右侧吸收高优先级链。

### CSS calc() 玩具 + media 复合条件（P12.3）

- `css.py`：`parse_calc(expr)` — 两个操作数之间支持二元 `+` / `-`，
  可选单位；`em` → `px`（1em = 16px）；不支持的单位组合回退为
  `Keyword(f"calc({expr})")`；裸数字 → `Length` 或 `Keyword`。
- `css.parse_value` 接入 `calc()` 分支；
  `LayoutBox` 宽度/高度经 `calc()` 求值后生效。
- `css.media_matches` 扩展：剥离外层括号、按 `"and"` 拆分、
  支持 `min|width/height` 及精确 `width|height: Npx` 形式。

### mousemove 状态机 + 预渲染回调（P12.4）

- `browser.py`：`render_document_with_hover_at(html, css, w, h, x, y)`
  — 通过 `element_at` 命中测试将 `:hover` 应用到目标元素。
- `browser.py`：`render_document_with_mouse(html, css, w, h, events,
  on_before_render=None, log_sink=None)` — 驱动 mousemove / mouseleave /
  click 事件序列，返回每事件一帧的 `list[Bitmap]`；状态 =
  `{"hover": id, "mouse": (x, y)}`；`on_before_render(state, i)` 回调
  在每帧渲染前触发。

### JS 解构赋值 + 对象展开运算符（P13.1）

- `js.py`：`DestructuringDecl` AST + `parse_destructuring` —
  `let {a, b} = obj`（支持 `key: alias` 重命名）与
  `let [x, y] = arr`（按位置绑定）；缺失键 / 越界 → `None`。
- `ObjectSpread` AST：`{...obj}` 展开语法；`parse_object_lit` 识别
  连续三个 `.`；解释器合并展开对象的键（跳过 `__` 内部键），
  显式键优先于展开键。

### JS 字符串模板字面量（P13.2）

- `js.py` 词法器：反引号发射 `"tpl"` 文本块 + `"open_tpl"` /
  `"close_tpl"` 插值标记；内部表达式重新词法化。
- `TemplateLit` AST：`chunks` 交替存放文本与插值表达式；
  解释器按 JS 字符串拼接语义格式化（整数浮点渲染为整数，
  None → "null"）。

### CSS filter 效果（P13.3）

- `layout.py`：`LayoutBox` 新增 `filter_blur` / `filter_brightness` /
  `filter_contrast` / `filter_enabled`；`build_box` 解析
  `filter: blur(Npx) / brightness(N%) / contrast(N%)`。
- `paint_engine.py`：`DisplayCommand` 新增 filter 字段；`rasterize`
  末尾调用 `_apply_filters` 后处理 — brightness / contrast 直接变换
  区域像素（contrast 以 128 为枢轴），blur 用 `(2r+1)²` 盒式平均近似。

### 事件委托 + 输入事件模拟（P13.4）

- `js.py`：`execute_event(element_id, event_name="onclick")` 扩展为
  通用事件名；委托回退 — 若目标没有该 handler，沿 DOM 父链向上查找
  最近祖先 handler（`this` 仍绑定到目标元素）。
- `js.py`：`DomElement` 属性读写新增 `value`。
- `browser.py`：`render_document_with_mouse` 新增事件类型
  `{"type": "input", "id": …, "value": …}`（设置 input value + 触发
  oninput）与 `{"type": "focus", "id": …}`（触发 onfocus）；
  新增 `_find_element_by_id` DOM 遍历辅助。

### CSS 滚动容器（P14.1）

- `layout.py`：`LayoutBox` 新增 `overflow_x` / `overflow_y` /
  `scroll_left` / `scroll_top`；`build_box` 解析 `overflow:auto` /
  `overflow:scroll`（x/y 分轴或单一 `overflow`）及内联
  `scroll-top: Npx` / `scroll-left: Npx`。
- `paint_engine.py`：scroll 容器子命令偏移 + `_apply_scroll_clips`
  后处理（超出滚动视口的像素擦白）。
- `js.py`：`DomElement` 新增 `scrollTop` / `scrollLeft` / `offsetWidth`
  / `offsetHeight` / `overflow` 属性（惰性 layout cache）。

### JS 箭头函数（P14.2）

- `js.py`：`=>` 词法 token + `ArrowFunc` AST（`params` / `body` /
  `is_block`）；单标识符 `v => …`、多参数 `(a, b) => …`、零参数
  `() => …`；表达式体隐式返回，块体显式 `return`。
- 参数列表解析完全投机（预读 `id ( , id )* ) =>` 模式，不匹配时
  零消费），括号表达式 `(m >= 0) && (m <= 11)` 不受影响。
- 既有高阶数组方法（`reduce` / `some` / `every` / `find` /
  `findIndex` / `filter` / `map`）直接接受箭头回调。

### CSS text-indent（P14.3）

- `layout.py`：`LayoutBox.text_indent` 字段；`build_box` 解析
  `text-indent: Npx`（支持负值，首行左移）。
- `paint_engine.py`：文本命令 x 偏移应用 `text_indent`。

### CSS @font-face 玩具（P14.4）

- `css.py`：`@font-face` 块解析安全（toy 语义：存储 font-family，
  不做真实字体加载）；多个 `@font-face` + `font-family` 引用级联
  不崩溃。

### JS Infinity / NaN + Array.at（P15.1）

- `js.py`：`Infinity` / `NaN` 作为 JS 字面量（`Num(float("inf"))` /
  `Num(float("nan"))`），`console.log` / 字符串拼接 / 模板字面量统一
  渲染为 `"Infinity"` / `"NaN"`。
- `js.py`：`Number.isNaN` / `Number.isInteger` 支持 Infinity / NaN
  浮点值。
- `js.py`：`Array.at(index)` 正/负下标；`Array.flat(Infinity)` 全深度
  展平。

### CSS 滚动 JS API（P15.2）

- `js.py`：`DomElement.scrollIntoView()` / `scrollBy(dx, dy)` 方法
  （toy 语义：记录滚动事件到日志）。
- `js.py`：`DomElement.overflow` 属性读取内联样式。

### CSS box-decoration-break / -webkit-appearance（P15.3）

- `layout.py`：`LayoutBox.box_decoration_break` / `webkit_appearance`
  字段；`build_box` 解析内联样式。
- `js.py`：`DomElement.boxDecorationBreak` / `appearance` 属性读取。

### 字体切换渲染（P15.4）

- `font.py`：`serif` 字体变体（10×8 宽字形）+ `_FONT_FAMILIES`
  注册表 + `cell_width` / `glyph_for` / `family_from_css` 辅助。
- `text.py`：`char_advance` / `measure` / `wrap` / `draw_text` 新增
  `font_family` 参数；`_blit` 支持 8/10 字节宽字形。
- `layout.py`：`LayoutBox.font_family` 字段 + 继承链；`css.py`
  将 `font-family` 加入 `INHERITED_PROPS`。
- `paint_engine.py`：文本命令传 `font_family` 给 `draw_text`。

### JS 数组/调用展开 + ===/!==（P16.1）

- `js.py`：`THREE_CHAR_OPS = ["..."]` 词法器；`ArraySpread` /
  `CallSpread` AST 节点；`parse_array_lit` 支持 `[...xs, 3]`，
  `parse_arg_list` 支持 `f(...xs)`；解释器 splice 展开元素与参数。
- `js.py`：`===` / `!==` 严格相等操作符（3-char token，优先于
  `==` / `!=` 匹配）。
- `js.py`：嵌套对象展开 `{z: 3, ...a, y: 99}` 回归测试。

### JS 可选链 + 空值合并（P16.2）

- `js.py`：`?.` 加入 `TWO_CHAR_OPS`；`OptionalChain` AST 节点
  （`base` / `prop` / `call` / `call_args`）；`parse_postfix` 识别
  `a?.b` / `a?.b()` / `a?.[b]` / `f?.(args)` 四种形式。
- 解释器 `OptionalChain`：DomElement / dict / list（`.length`）/
  str（`.length`）/ 任意对象属性读取 + 可选调用。
- `js.py`：修复 `eval_block` / `eval_while` / `eval_for` / `eval_try`
  吞掉 `_Return` 的 bug，使 `return` 在 if/else / 循环体内正确传播
  到函数级 catch。
- `??` 空值合并回归测试（0 / '' 不触发回退）。

### CSS 文本增强（P16.3）

- `layout.py`：`LayoutBox.text_justify` / `word_break` / `hyphens`
  字段；`build_box` 解析内联样式（toy 语义：存储，不影响渲染）。

### CSS scroll-snap 玩具（P16.4）

- `layout.py`：`LayoutBox.scroll_snap_type` 字段；`build_box` 解析
  `scroll-snap-type: x mandatory` / `y proximity` / `x both` / `none`
  （toy 语义：存储，不影响静态布局）。

### TTF 字体文件解析（P17）

- `ttf_font.py`（新模块）：纯标准库 `struct` 解析 TTF/OTF 二进制文件。
  - **P17.1 头部表**：`head`（unitsPerEm）、`hhea`（ascent/descent/lineGap）、
    `hmtx`（advance + LSB per glyph）、`maxp`（numGlyphs）、`OS/2`
    （weightClass）。损坏文件抛 `TTFError`。
  - **P17.2 glyf/loca 轮廓**：`_parse_simple_glyph`（endPtsOfContours +
    flags 重复编码 + 累计 delta 坐标）、`_parse_composite_glyph`
    （递归组件解析 + 仿射变换）、`GlyphOutline` dataclass。
  - **P17.3 cmap + advance width**：`_parse_cmap`（format 4 + 12，
    platform 3/1 优先）；`advance_width(ch, font_px)` 用真实 hmtx
    值替换固定 8px 估算；缺字形 fallback 到宽格。
  - **P17.4 @font-face 升级**：`load_font_for_family(family, bold,
    search_dirs)` 按注册表定位 TTF 文件；`font.init_real_fonts()`
    启动时加载 serif/monospace 真实字体；双字重 `arial.ttf`(400) +
    `arialbd.ttf`(700)。
- `text.py`：`advance_text` / `measure` / `line_height_px` / `draw_text`
  使用 TTF 逐字符 advance + 真实行高（比例字距）。
- `font.py`：`init_real_fonts(search_dirs)` + `get_real_metrics(family)`
  注册真实 TTF 度量。
- `fonts/arial.ttf` + `fonts/arialbd.ttf` 测试夹具。

### 文本度量与行盒模型（P18）

- **P18.1 半领导者模型**：`layout.py` 文本盒记录 `font_ascent_px` /
  `font_descent_px` / `baseline_y`；`line_height_px > em_box` 时
  `half_lead = (line_h - em_box) / 2` 偏移文本盒 y，文字垂直居中。
  `css.py` `line_height_px()` 优先 TTF 自然行高。
- **P18.2 内联跨行拆分**：内联 run 循环中 `<a>` / `<span>` 盒子携带
  自身样式，跨行时 `text_color` / `background` 不丢；run 高度取
  `max(child_h, line_height_px)`。
- **P18.3 vertical-align 玩具版**：`LayoutBox.vertical_align`
  字段（baseline / top / middle）；行内 `<img>` 抬高行高。
- **P18.4 text-align 真实行盒**：`text-align` / `text-transform`
  加入 `INHERITED_PROPS`，从父元素继承至文本子节点；`text-transform`
  应用于测量前。
- **继承扩展**：`css.py` `INHERITED_PROPS` 新增 `line-height` /
  `text-align` / `text-transform` / `font-family` / `overflow-wrap`。

### 断行与 Unicode 处理（P19）
- **P19.1 UAX#14 简化断行**：`text.py` `_break_class(ch)` 将字符分为
  SP/NS/BA/HY/OP/CL/CM/CJK/AL 九类；`wrap()` 重写为逐字符扫描，
  空格后断行、CJK 逐字断行、`word-break:break-all` 每字符断点。
- **P19.2 CJK 逐字断行 + 禁则**：`_CJK_NO_LINE_START`（，。！？）
  等）+ `_CJK_NO_LINE_END`（（〈《【 等）禁则字符集合；断行时
  禁则字不出现在行首（拉回当前行，允许轻微溢出）。
- **P19.3 &shy; 软连字符**：`dom.py` `_decode_entities()` 将 `&shy;`
  解码为 U+00AD；`wrap()` 在软连字符处断行时行末显示 '-'。
- **P19.4 长单词溢出**：单词本身超过容器宽度时逐字符折断
  （不丢失字符）；`overflow-wrap:break-word` 触发同样的强制断行。
- `layout.py`：`LayoutBox.overflow_wrap` 新字段；`wrap()` 接收
  `word_break` + `overflow_wrap` 参数。

### GUI 外壳（P31）

- **tkinter 零依赖**：`gui.py` `TabBrowser` — 多标签页（ttk.Notebook）、
  地址栏、前进/后退/刷新、状态栏 + 进度条。纯标准库，无外部依赖。
- **多标签页 + 页面实例隔离**：每标签独立的 `PageInstance`（URL、
  标题、Bitmap、前进/后退栈）+ 独立 notebook 子页 + 独立 PhotoImage。
  关闭最后一个标签自动退出。
- **键盘快捷键**：Ctrl+L 聚焦地址栏、Ctrl+T 新标签、Ctrl+W 关闭、
  Ctrl+R 刷新、Alt+←/→ 后退/前进。
- **Bitmap → P6 PPM**：`_bitmap_to_photopix` 将 paint.Bitmap 转为
  P6 二进制 PPM 字节，喂给 `tk.PhotoImage(data=...)`。
- **本地文件 / about: / 内联 HTML**：`load_file` 本地渲染、
  `about:blank`/`about:version`/`about:history`、内联 HTML
  （以 `<` 开头）直接渲染；P21 后 http/https 走 `net.fetch_html`
  真实加载，网络失败显示统一错误页（替换原「M2 前暂不支持」占位页）。
- **运行**：`python gui.py demo.html`（或 `python gui.py` 打开空白页）。

### 渲染质量与字重体系（P20）

- **P20.1 二次贝塞尔曲线填充**：`rasterize.py` —
  `_flatten_quadratic`（de Casteljau 细分，直线退化为端点、弯曲曲线
  自适应细分）、`_contour_to_points`（TrueType off-curve 中点规则 +
  y 轴翻转）、`_fill_polygon`（偶数-奇数扫描线 + 2×2 超采样覆盖率
  → 灰度抗锯齿）。曲线渲染经 `ttf_font.outline_valid` bbox 校验门控：
  顶点落在字形 bbox 内才走曲线路径，否则回退 P15.4 的 8×8 位图字形
  （保证零回归）。
- **P20.2 合成粗体（dilation）**：`_dilate_poly` 把像素空间多边形顶点
  沿局部质心方向外扩（外扩半径随字号缩放）；`draw_glyph_curves`
  `weight>1.0` 时触发。
- **P20.3 完整字重阶梯**：`font.get_metrics_for_weight(family, weight)`
  — weight≥600 优先 bold TTF（"bd" 文件），无则回退 normal + 置位
  合成粗体标志，由曲线路径执行 dilation。
- **P20.4 字体回退链**：`ttf_font.find_font_for_family`（指定族 →
  已注册回退族 → 搜索目录任意 TTF → 兜底）、`ttf_font.match_weight`
  （weight_class 最近匹配）。CSS `font-weight` 解析入
  `LayoutBox.font_weight`。
- **P20.5 letter-spacing / text-rendering**：CSS `text-rendering`
  （auto/optimizeSpeed/optimizeQuality）解析入 `LayoutBox.text_rendering`；
  optimizeSpeed 关闭曲线路径的 2×2 抗锯齿。`letter_spacing`（P8.4）
  接线到 `text.draw_text` / `rasterize.draw_text_curves` 逐字 advance。
- **P20.6 曲线路径接入渲染管线**：`text.draw_text` 每字符优先
  `glyph_outline` + bbox 校验走曲线填充，校验失败或无 TTF 则回退
  位图。`paint_engine` 经 `DisplayCommand` 携带 letter_spacing /
  text_rendering / font_weight 三字段传入渲染。
- `layout.py`：`LayoutBox.font_weight` + `LayoutBox.text_rendering` 新字段。
- `paint_engine.py`：`DisplayCommand` 三新字段。
- `font.py`：`get_metrics_for_weight(family, weight)`。
- 测试：22 条新 P20 测试（总数 415 → 437）。

### URL 与 HTTP/1.1 客户端（P21）

- **P21.1 URL 解析**：`net.py` — `URL` frozen dataclass
  （scheme/host/port/path/query/fragment/user/password）+
  `parse_url(url, base)` 相对解析（WHATWG 规则：`?q`/`#f` 替换 base 的
  query/fragment、`/abs` 同 host 新 path、`//host` scheme-relative、
  相对 path 按 base 目录 + `..` 规范化）；`effective_port`（默认 80/443
  推断）+ `authority`（默认端口省略）+ `to_string()` 往返 + `origin`；
  `normalize_url`（小写化 + 空 path → `/`）。
- **P21.2 HTTP/1.1 最小客户端**：`HttpClient.get(url)` —
  `http.client`（标准库，接口可替换）GET + `Host` 头；状态码分派
  200/301/404/500（3xx 原样返回，P22 跟进重定向链）；一次瞬时网络
  错误重试；死端口 → `ConnectionError`。`HttpResponse` 携带
  status/headers/body + `content_type` / `is_html` / `ok`。
- **P21.3 Content-Type 分派 + 统一错误页**：`fetch_html(url)` →
  `(text, resp)`：text/html UTF-8 解码；非 HTML Content-Type 或
  4xx/5xx 抛 `FetchError`。`error_page_html` / `render_error_page`
  超时 / 连接 / 404 / 500 统一错误页（经完整管线出 Bitmap）。
  `gui.py` http/https 输入走 `net.fetch_html`，`FetchError` → 统一错误页
  （替换 P31 的「M2 前暂不支持」占位页）；`main.py` URL 输入走 net 栈，
  网络失败退出码 3 + 仍写错误页 PPM。
- **测试**：19 条新 P21 测试（总数 437 → 456），全部用本地
  `http.server` 夹具（离线可测、CI 稳定）。

### HTTP 深化与子资源加载（P22）

- **P22.1 重定向链**：`HttpClient.get(url, follow_redirects=True,
  max_redirects=5)` — 301/302/303/307/308 自动跟随；上限 5 层 + 已见
  URL 集合循环检测，超限/循环抛 `net.RedirectLoop`（`FetchError` 子类，
  携带 `chain`）。`follow_redirects=False` 保留 P21 行为（3xx 原样返回，
  `redirected=True`）。相对 `Location` 按重定向源 URL 解析（WHATWG）。
- **P22.2 gzip**：`maybe_gunzip(resp)` — `Content-Encoding: gzip` 时透明
  解压；无该头或解压失败则原样返回（不崩页面）。请求携带
  `Accept-Encoding: gzip`。
- **P22.3 chunked**：`net.decode_chunked(raw)` 纯函数解析原始 chunked
  流（hex size `[;ext]` 帧 + 终止 `0` 块；畸形 size 行安全停止）。
  线上由 `http.client` 透明处理，此钩子供 M5 手写 socket 客户端复用。
- **P22.4 子资源 + P22.5 `<base>`**：`collect_subresources(dom)`
  文档序收集 `<link rel=stylesheet>` + `<img src>`；`base_url_from_dom`
  读 `<base href>`（无则 None，回退文档 URL）。`load_subresources(dom,
  base, client)` 按 base 解析相对 src、`data:` 跳过网络、顺序 GET、
  **失败不阻塞**（404/超时 → None，不中断、不崩页面），返回
  `{kind: {abs_url: HttpResponse|None}}`。`fetch_resource(url)` 子资源
  语义（成功返 resp，失败返 None）。`browser.render_document_from_url`
  文档走重定向链 + gzip，成功 CSS 子资源并入样式表后跑完整管线出
  Bitmap。`main.py` URL 输入改走此 API（网络失败仍退出码 3 + 错误页）。
- **测试**：20 条新 P22 测试（总数 456 → 476），全部用本地
  `http.server` 夹具（离线可测、CI 稳定）。

### HTTPS 与磁盘缓存（P23）

- **P23.1 HTTPS（TLS 包裹 + 校验开关）**：`HttpClient` 经 `ssl` 标准库
  包裹（暂不手写 socket）；证书校验默认开启，`verify_tls=False`
  （CLI `--insecure`）关闭 hostname + 证书校验，用于本地自签名 /
  调试。端到端用 `ssl.SSLContext` 自签名证书夹具验证握手。
- **P23.2 启发式磁盘缓存**：`net.CacheStore(root_dir)` — 响应存盘
  （SHA-256(URL) 为 key：`.json` 元数据 + `.bin` body）；`_parse_cache_control`
  解析 `Cache-Control: max-age` / `no-cache` / `no-store`（no-store 永不
  写盘）；无显式指示时启发式 TTL = `10% × (Date − Last-Modified)`
  （RFC 7234）。`HttpClient.get` 先查 `get_fresh`（命中 → `from_cache=True`
  不触网），成功后回写；`CacheStore.clear()` 清空。
- **P23.3 ETag 协商缓存**：过期但带 ETag 的条目经 `get_stale` 返回 →
  首跳请求携带 `If-None-Match`；304 → 复用旧 body（表面化 `from_cache=True`
  的 200）+ `revalidate_304` 刷新新鲜度（省流量）；无 ETag 直接重取。
  `HttpResponse.etag` 属性。
- **P23.4 加载进度事件**：`LoadProgress` / `LoadEventSink` — 每次 fetch
  回调 `start` / `document` / `subresource` / `done`（fraction 0–1），
  可 `on_event` 订阅（M3.2 GUI 进度条消费者）；`load_subresources` 逐
  子资源发 `subresource` 事件；CLI `--progress` 打印到 stderr。
- **P23 顺带**：`HttpClient.post`（表单 `x-www-form-urlencoded` / 逐字
  body；POST 不缓存），CLI `--insecure` / `--cache DIR` / `--progress`
  三新旗标；`browser.render_document_from_url` 新增 `use_cache` /
  `progress` 参数。
- **测试**：16 条新 P23 测试（总数 476 → 492，超出规划 485 目标），
  全部用本地 `http.server` 夹具 + `ssl` 自签名证书（离线可测、CI 稳定）。

### HTML5 容错解析（P24）

- **P24.1 原始文本模式 + entities 完整表**：`html_pkg/entities.py` —
  `decode_entities(text)` 单遍解码命名实体（WHATWG 策展子集，常用
  字符 / 希腊 / 数学 / 上下标 / 引号）+ 数字引用（`&#65;` 十进制、
  `&#x1F600;` / `&#X42;` 十六进制）；码点 0 与代理区 → U+FFFD（规范
  §tokenization）；未知名实体保留原文。`tokenize.py` 的
  `RAWTEXT_TAGS`（`script` / `style` / `textarea` / `title` / `xmp`）
  内容原样取到匹配的关闭标签为止，不做 HTML 解析 / 实体解码。
- **P24.2 void elements + 自动闭合纠错**：`VOID_TAGS`
  （`br` / `img` / `input` / `hr` 等 14 个）永不压栈（`<br/>` 自
  闭合语法合法）；隐式兄弟闭合（第二个 `<p>` / `<li>` / `<dt>` /
  `<dd>` 关闭前一个）；块级开始标签（`div` / `h1`–`h6` / `ul` 等）
  关闭 open 的 `<p>`；`<` 后首字符非字母（`a < b`、`<3`）整段作文本。
- **P24.3 注释 / DOCTYPE / 大小写不敏感 + foster parenting**：
  `<!DOCTYPE …>` / `<!-- … -->`（含 `<?…?>` 伪注释）词法产出
  `doctype` / `comment` 令牌，树构建直接丢弃；标签名 / 属性名全部
  小写化（`<DIV>` == `<div>`）；`<tr>` / `<td>` / `<th>` /
  `<colgroup>` 出现在表格上下文之外时挂到最近的非表格祖先（玩具
  foster parenting）。
- **P24.4 格式化元素重构（adoption agency 简化版）**：
  `<b><i>x</b></i>` — 迟到的 `</b>` 到达而栈顶是 `i` 时，把中间
  元素重挂到匹配的 `b` 下并关闭；`b` 保持 open 由其关闭标签弹出。
  格式化标签族 `b i u s em strong small mark sub sup span`。
- **P24.5 Parser 独立成 html_pkg/ + 字节流输入**：新建 `html_pkg/`
  包（`node.py` / `entities.py` / `tokenize.py` / `build.py` /
  `__init__.py`）；输入统一为字节流（UTF-8 / UTF-16 BOM 嗅探 +
  Latin-1 回退，`decode_bytes`）；`dom.py` 瘦身为兼容 shim
  （`import dom` / `from dom import Node` 不变，Node 与
  `html_pkg.node.Node` 同一类对象）；下游 `css` / `layout` /
  `paint` / `js` / `browser` / `main` / `gui` 零改动。
- **测试**：22 条新 P24 测试（总数 492 → 514，超出规划 512 目标），
  离线纯字符串 / 字节输入（CI 稳定）；既有 492 条测试全绿无回归。

### 失效（P0.5）

- `browser.changed_region(bmp_a, bmp_b)` 返回像素差异的包围盒 `(x, y, w, h)`，
  相同则返回 `None`。用于未来增量渲染：只重光栅化变化区域。

### 可访问性（P2.1）

- `browser.collect_accessibility_tree(dom_root)` → 扁平 a11y 树 `[{role, name, children}]`
- 语义化 tag→role 映射：`a`→link、`h1`–`h6`→heading、`button`→button、
  `ul/ol`→list、`li`→listitem、`table`→table、`img`→image、`iframe`→iframe、
  `textarea`→textbox、`select`→combobox、`div`→section、`p`→paragraph
- `script`/`style`/`link`/`meta` 排除在 a11y 树外
- `img` `alt` 属性回退为可访问名称；`aria-label` 优先于 textContent
- CLI：`--a11y file.json` 输出 JSON

### 链接收集（P2.2）

- `browser.collect_links(dom_root)` → `list[str]`（去重、文档序）
- `browser.write_links(links, path)` → 纯文本文件
- CLI：`--links file.txt` 输出链接列表

### PNG 输出（P2.3）

- `browser.write_png(bmp, path)` — 纯标准库 PNG 编码器（`zlib` + `struct` + CRC32）
- 8-bit RGB 无 alpha；IHDR/IDAT/IEND 分块 + zlib 压缩
- CLI：`--png` 标志输出 PNG 而非 PPM

## 使用

```bash
# 基本渲染
python main.py demo.html out.ppm

# 指定尺寸
python main.py demo.html out.ppm --width 800 --height 600

# 模拟点击按钮（按 id）
python main.py demo.html out.ppm --event btn

# 模拟坐标点击
python main.py demo.html out.ppm --click 100,50

# 附加用户 CSS
python main.py demo.html out.ppm --css extra.css

# 输出 PNG（纯标准库编码器）
python main.py demo.html out.png --png

# 收集可访问性树（JSON）
python main.py demo.html out.ppm --a11y a11y.json

# 收集所有 <a href> 链接
python main.py demo.html out.ppm --links links.txt

# 组合使用
python main.py demo.html out.png --png --a11y a11y.json --links links.txt

# 启动 tkinter GUI（零依赖，多标签页）
python gui.py demo.html        # 打开指定 HTML
python gui.py                  # 打开 about:blank
```

PNG 输出也可用 `ffmpeg` / `ImageMagick` 生成（与 `--png` 标志等价）：

```bash
ffmpeg -i out.ppm out.png
# 或
convert out.ppm out.png
```

## 测试

```bash
python test.py
```

**415 条测试**，覆盖：

| 类别 | 测试 |
|---|---|
| DOM | `dom_parse`、`dom_implicit_close` |
| CSS | `css_cascade`、`css_inline_wins`、`display_none`、`css_height_and_line_height`、`text_align_stored`、`line_height_affects_text_height`、`inline_elements_horizontal` |
| 布局 | `iframe_placeholder`、`hit_test`、`text_measure_consistency`、`render_display_none`、`img_placeholder`、`img_explicit_size`、`img_paints`、`button_border`、`input_placeholder` |
| 选择器 | `selector_descendant`、`selector_child`、`selector_multi_class` |
| JS | `js_toggle_display_none`、`js_event_loop`、`js_nullish`、`js_set_timeout_fifo`、`js_array_literal`、`js_string_methods`、`js_object_literal` |
| 沙箱 | `iframe_src_sandbox`、`fetch_sandbox` |
| 失效 | `changed_region` |
| Flexbox | `flex_row`、`flex_column`、`flex_justify_center` |
| 视觉特效 | `opacity_render`、`opacity_stored_on_box`、`overflow_hidden_flag` |
| 事件循环 | `request_animation_frame`、`promise_resolve_then`、`promise_catch`、`host_logs` |
| UA 样式表 | `ua_h3_default`、`ua_table_border` |
| 可访问性 | `a11y_tree_roles`、`a11y_img_alt_fallback`、`a11y_skip_script_style` |
| 链接收集 | `collect_links`、`collect_links_remote`、`write_links_and_a11y` |
| PNG 输出 | `png_encode`、`png_roundtrip_dimensions`、`ppm_png_equivalence` |
| 内嵌图片（P3.1） | `embedded_img_base64`、`embedded_img_intrinsic_size`、`embedded_img_png_data_uri` |
| Flexbox 深化（P3.2） | `flex_grow_distribution`、`flex_align_items_center`、`flex_grow_column` |
| JS 内置对象（P3.3） | `js_math_sqrt`、`js_math_max_abs`、`js_math_constants`、`js_json_stringify`、`js_json_parse` |
| CSS 过渡（P3.4） | `transition_color_interpolation`、`transition_render_frames`、`transition_lerp_color` |
| @keyframes 动画（P4.1） | `parse_keyframes`、`keyframes_animation_render`、`apply_animation_frame_interpolation` |
| JS 对象扩展（P4.2） | `js_date_now`、`js_date_getmonth`、`js_regexp_test`、`js_regexp_replace`、`js_string_charcode` |
| CSS Grid + 简写（P4.3） | `grid_columns`、`grid_render_cells`、`margin_shorthand_4val`、`padding_shorthand_2val`、`margin_shorthand_3val` |
| CSS 视觉特效（P5.1） | `border_radius_layout`、`border_radius_render_clips_corner`、`box_shadow_render`、`transform_translate_render` |
| CSS float（P5.2） | `float_left_layout`、`float_right_layout` |
| JS 数组高阶 + assert（P5.3） | `js_array_map_join`、`js_array_filter`、`js_array_reverse_concat`、`js_array_foreach`、`console_assert_false`、`console_assert_true_silent` |
| position: absolute（P6.1） | `abs_position_layout`、`abs_position_render`、`abs_position_top_left_offset` |
| text-overflow / line-clamp（P6.2） | `text_overflow_ellipsis_stored`、`line_clamp_layout` |
| JS 内置对象深化（P6.3） | `js_array_reduce`、`js_array_reduce_init`、`js_object_keys`、`js_object_values`、`js_string_repeat`、`js_string_pad_start`、`js_string_pad_end` |
| CSS z-index（P7.1） | `z_index_layout`、`z_index_paint_sort` |
| display: table（P7.2） | `table_layout`、`table_render_cells`、`table_cell_flags` |
| JS 数组高阶补全（P7.3） | `js_array_find`、`js_array_find_index`、`js_array_some`、`js_array_every` |
| position: sticky（P7.4） | `sticky_layout`、`sticky_full_direction` |
| CSS :hover / :focus（P8.1） | `hover_pseudo_layout`、`focus_pseudo_layout`、`pseudo_class_specificity`、`pseudo_class_parse_focus` |
| position: relative（P8.2） | `relative_position_layout`、`relative_position_render`、`relative_without_offsets` |
| JS 方法补全（P8.3） | `js_array_sort`、`js_array_includes`、`js_object_entries`、`js_string_starts_with`、`js_string_ends_with` |
| CSS 文本属性（P8.4） | `text_transform_layout`、`text_transform_render_uppercase`、`letter_spacing_layout`、`word_spacing_layout` |
| CSS @media queries（P9.1） | `media_min_width_match`、`media_min_width_no_match`、`media_max_width_match`、`media_parse` |
| position: abs/rel 全向（P9.2） | `abs_position_right_bottom`、`relative_right_bottom`、`abs_position_right_only`、`relative_full_direction` |
| JS 方法深化（P9.3） | `js_array_flat`、`js_array_flat_depth`、`js_array_flat_map`、`js_object_assign`、`js_object_assign_override`、`js_number_is_integer`、`js_number_is_nan`、`js_number_parse_int`、`js_string_trim_start`、`js_string_trim_end`、`js_map_with_index`、`js_filter_with_index` |
| CSS 视觉深化（P9.4） | `clip_inset_layout`、`clip_inset_4val_layout`、`linear_gradient_layout`、`linear_gradient_render`、`text_shadow_layout`、`text_shadow_render`、`hover_render_api` |
| CSS :first-child / :last-child / :nth-child（P10.1） | `first_child_layout`、`last_child_layout`、`nth_child_odd`、`nth_child_an_plus_b`、`nth_child_specificity` |
| JS Map / Set / Array.from / replaceAll（P10.2） | `js_map_basic`、`js_map_delete`、`js_set_basic`、`js_array_from`、`js_array_from_mapfn`、`js_array_of`、`js_string_replace_all`、`js_string_replace_all_regex` |
| CSS outline + background-image（P10.3） | `outline_layout`、`outline_render`、`outline_width_only` |
| JS Error / 嵌套 JSON / Array.toString（P10.4） | `js_error_object`、`js_error_name`、`js_json_nested_parse`、`js_json_stringify_nested`、`js_array_to_string`、`js_array_to_string_nested` |
| CSS transition 多属性（P11.1） | `transition_multi_property`、`transition_transform_lerp`、`transition_parse_multi`、`transition_color_lerp`、`transition_scalar_lerp`、`transition_advance_stops` |
| JS try/catch/throw（P11.2） | `js_throw_catch`、`js_throw_number`、`js_try_finally`、`js_try_catch_finally`、`js_uncaught_throw`、`js_throw_object` |
| CSS @supports + display（P11.3） | `supports_parse`、`supports_matches_always_true`、`supports_cascade_applies`、`display_toggle_none`、`display_toggle_block` |
| 命中测试 / 事件 API（P11.4） | `element_at_basic`、`element_at_miss`、`event_target_runs_handler`、`click_and_render`、`element_at_uses_supports` |
| 多属性 @keyframes（P12.1） | `keyframe_multi_property_tracks`、`animation_handle_frame_overrides`、`keyframes_three_stop`、`animation_handle_update`、`animate_keyframes_multi_render`、`keyframes_iteration_param`、`keyframes_multi_property_2` |
| JS class（P12.2） | `js_class_basic`、`js_class_inheritance`、`js_class_parent_method_fallback`、`js_class_method_no_return`、`js_new_builtin_still_works`、`js_class_method_on_dom`、`js_class_super_call`、`js_class_inheritance_depth` |
| CSS calc()（P12.3） | `calc_parse_value`、`calc_plus`、`calc_em_to_px`、`calc_unsupported_fallback`、`calc_in_layout_width`、`calc_minus`、`calc_bare_number`、`calc_em_mixed`、`calc_in_layout_height` |
| media 复合条件（P12.3） | `media_exact_width`、`media_composite_and` |
| mousemove 状态机（P12.4） | `hover_at_coordinate`、`mouse_events_sequence`、`pre_render_callback`、`mouse_events_runs_onclick`、`mouse_mouseleave_event` |
| JS 解构 + 对象展开（P13.1） | `js_object_destructuring`、`js_array_destructuring`、`js_object_spread`、`js_object_spread_override`、`js_destructuring_default_value` |
| JS 模板字面量（P13.2） | `js_template_literal_plain`、`js_template_literal_interpolation`、`js_template_literal_expression`、`js_template_literal_multiple`、`js_template_literal_number_formatting` |
| CSS filter（P13.3） | `css_filter_brightness_layout`、`css_filter_contrast_layout`、`css_filter_blur_layout`、`css_filter_brightness_render`、`css_filter_no_filter_identity` |
| 事件委托 + 输入事件（P13.4） | `js_event_input_direct`、`js_event_input_delegated`、`js_event_focus`、`js_element_value_prop`、`js_element_set_value` |
| CSS 滚动容器（P14.1） | `css_scroll_container_overflow_auto`、`css_scroll_container_scroll_top`、`css_scroll_container_render`、`js_element_scroll_top_prop`、`css_scroll_ymax_clamped` |
| JS 箭头函数（P14.2） | `js_arrow_expr_body`、`js_arrow_block_body`、`js_arrow_multi_param`、`js_arrow_reduce`、`js_arrow_filter`、`js_arrow_map`、`js_arrow_find`、`js_arrow_find_index` |
| CSS text-indent（P14.3） | `css_text_indent_layout`、`css_text_indent_render`、`css_text_indent_negative`、`css_text_indent_render_text_present` |
| CSS @font-face（P14.4） | `css_font_face_parse`、`css_font_face_no_crash` |
| JS Infinity / NaN + Array.at（P15.1） | `js_infinity_literal`、`js_nan_literal`、`js_flat_infinity`、`js_array_at_positive`、`js_number_isnan`、`js_number_is_integer` |
| CSS 滚动 JS API（P15.2） | `js_scroll_into_view`、`js_scroll_by`、`js_element_overflow_prop` |
| CSS box-decoration-break（P15.3） | `css_box_decoration_break_layout`、`css_webkit_appearance_layout`、`js_element_box_decoration_break_prop`、`js_element_appearance_prop` |
| 字体切换渲染（P15.4） | `font_cell_widths`、`font_measure_family`、`css_font_family_layout`、`css_font_family_inheritance`、`css_font_family_render` |
| JS 数组/调用展开 + ===/!==（P16.1） | `js_array_spread`、`js_call_spread`、`js_nested_object_spread`、`js_call_spread_zero_args` |
| JS 可选链 + 空值合并（P16.2） | `js_optional_chain_null`、`js_optional_chain_obj`、`js_optional_call_null`、`js_nullish_cohesion_zero`、`js_optional_chain_dom_element` |
| CSS 文本增强（P16.3） | `css_text_justify_layout`、`css_word_break_layout`、`css_hyphens_layout`、`css_text_justify_none` |
| CSS scroll-snap 玩具（P16.4） | `css_scroll_snap_type_layout`、`css_scroll_snap_type_y_proximity`、`css_scroll_snap_type_default_empty`、`css_scroll_snap_render` |
| TTF 字体解析（P17） | `ttf_load_header`、`ttf_cmap_lookup`、`ttf_advance_width`、`ttf_glyph_outline_a`、`ttf_composite_glyph`、`ttf_dual_weight`、`text_ttf_advance`、`render_with_ttf_metrics` |
| 文本度量与行盒（P18） | `p18_half_leading_baseline`、`p18_font_ascent_descent_fields`、`p18_inline_link_cross_line`、`p18_vertical_align_top`、`p18_text_align_center_line_box`、`p18_render_half_leading`、`p18_mixed_inline_block` |
| 断行与 Unicode（P19） | `p19_english_word_wrap`、`p19_cjk_char_by_char`、`p19_kinsoku_no_line_start`、`p19_soft_hyphen`、`p19_shy_html_entity`、`p19_word_break_all`、`p19_overflow_wrap_break_word`、`p19_long_word_overflow` |
| GUI 外壳（P31） | `gui_page_load_file`、`gui_about_blank`、`gui_http_stub`、`gui_inline_html`、`gui_back_forward`、`gui_bitmap_ppm`、`gui_tkinter_available` |
| 渲染质量与字重（P20） | `p20_quadratic_flatten`、`p20_offcurve_midpoint_rule`、`p20_fill_polygon_aa`、`p20_fill_polygon_speed`、`p20_synthetic_bold_increases_ink`、`p20_dilate_poly`、`p20_outline_bbox_validation`、`p20_letter_spacing_widens`、`p20_text_rendering_stored`、`p20_font_weight_stored`、`p20_metrics_for_weight`、`p20_font_fallback_chain`、`p20_match_weight`、`p20_draw_text_curves_letter_spacing`、`p20_glyf_instruction_count_layout`、`p20_point_flags_bits`、`p20_curve_glyph_fallback`、`p20_paint_command_fields`、`p20_layout_box_fields`、`p20_synthetic_bold_flag_roundtrip`、`p20_render_full_pipeline_weight`、`p20_glyf_offcurve_yreversed` |
| URL 与 HTTP/1.1（P21） | `p21_parse_url_absolute`、`p21_parse_url_https_default_port`、`p21_relative_url_resolution`、`p21_url_to_string_roundtrip`、`p21_normalize_url`、`p21_origin_property`、`p21_url_is_network_flag`、`p21_http_get_200`、`p21_http_get_404`、`p21_http_redirect_surfaced`、`p21_content_type_dispatch`、`p21_fetch_error_on_4xx`、`p21_unsupported_scheme`、`p21_error_page_html`、`p21_error_page_renders`、`p21_connection_error_retries`、`p21_http_query_in_path`、`p21_fetch_html_accepts_str_url`、`p21_cli_network_error_exit_code` |
| HTTP 深化与子资源（P22） | `p22_redirect_followed`、`p22_redirect_loop_detected`、`p22_redirect_exceeds_cap`、`p22_redirect_within_cap_succeeds`、`p22_redirect_no_follow_returns_302`、`p22_gzip_decoded`、`p22_gzip_no_header_passthrough`、`p22_chunked_decode_roundtrip`、`p22_chunked_with_extensions`、`p22_chunked_max_size_cap`、`p22_subresource_collection`、`p22_base_tag_overrides_doc_base`、`p22_base_tag_absent`、`p22_subresource_failure_does_not_block`、`p22_render_document_from_url`、`p22_render_from_url_gzip_doc`、`p22_render_from_url_network_error`、`p22_subresource_data_uri_skipped`、`p22_fetch_resource_returns_none_on_404`、`p22_redirect_relative_location` |
| HTTPS 与磁盘缓存（P23） | `p23_cache_hit_skips_wire`、`p23_cache_no_store_not_cached`、`p23_cache_max_age_expiry`、`p23_cache_etag_304_reuse`、`p23_cache_heuristic_ttl`、`p23_cache_store_clear`、`p23_cache_parse_directives`、`p23_progress_events_emitted`、`p23_progress_subresource_phase`、`p23_progress_on_event_subscription`、`p23_post_form_encoded`、`p23_post_verbatim_str`、`p23_insecure_flag_accepted`、`p23_https_tls_handshake`、`p23_cache_etag_absent_refetches`、`p23_cache_disabled_store` |
| HTML5 容错解析（P24） | `p24_entities_named`、`p24_entities_numeric_dec`、`p24_entities_numeric_hex`、`p24_entities_zero_and_surrogate`、`p24_entities_unknown_name_literal`、`p24_rawtext_script_literal`、`p24_rawtext_style_literal`、`p24_void_input_no_stack`、`p24_void_self_closing_br`、`p24_implicit_p_sibling`、`p24_block_closes_p`、`p24_adoption_b_i`、`p24_stray_close_ignored`、`p24_comment_doctype_dropped`、`p24_case_insensitive_tag`、`p24_foster_tr_in_div`、`p24_unclosed_lt_text`、`p24_utf16_bom`、`p24_utf8_bom`、`p24_dom_shim_reexport`、`p24_dom_parse_bytes`、`p24_full_pipeline_render` |

## 改进路线图

见 [ROADMAP.md](ROADMAP.md) — 按优先级（P0/P1/P2）列出改进项，含验收标准与预计工作量。

已完成：

- [x] P0.1 图片占位框
- [x] P0.2 表单控件渲染
- [x] P0.3 CSS 选择器扩展（后代/子/多类）
- [x] P0.4 JS 引擎补全（数组/对象字面量、字符串方法）
- [x] P0.5 失效区域计算
- [x] P1.1 视觉特效（opacity / overflow:hidden）
- [x] P1.2 事件循环完整化（requestAnimationFrame / Promise / Host.logs）
- [x] P1.3 CSS 默认样式表扩展（h1–h6、table、pre、blockquote…）
- [x] P1.4 Flexbox 最小实现（row / column / justify-content）
- [x] P2.1 可访问性树（tag→role 映射 + alt 回退 + aria-label + JSON 输出）
- [x] P2.2 链接收集（collect_links + write_links + CLI --links）
- [x] P2.3 PNG 输出（纯标准库 zlib+CRC32 编码器 + CLI --png）
- [x] P2.4 测试扩充至 52 条
- [x] P3.1 内嵌图片解码（data: URI base64 + 最小 PNG 解码器）
- [x] P3.2 Flexbox 深化（flex-grow 权重分配 + align-items 交叉轴对齐）
- [x] P3.3 JS 内置对象（Math / JSON.stringify / JSON.parse）
- [x] P3.4 CSS 过渡引擎（属性插值 + 帧推进 + N+1 帧渲染）
- [x] P3.5 测试扩充至 66 条
- [x] P4.1 @keyframes 动画（CSS 解析 + 关键帧插值 + browser.animate_keyframes + CLI --animate）
- [x] P4.2 JS 对象扩展（Date / RegExp / String.fromCharCode + 通用对象方法分派）
- [x] P4.3 CSS Grid 最小实现 + 多值简写测试
- [x] P4.4 测试扩充至 79 条
- [x] P5.1 CSS 视觉特效深化（border-radius / box-shadow / transform:translate）
- [x] P5.2 CSS float（left / right 浮动定位）
- [x] P5.3 JS 数组高阶方法 + console.assert
- [x] P5.4 测试扩充至 91 条
- [x] P6.1 position: absolute（top / left 偏移定位）
- [x] P6.2 text-overflow: ellipsis + line-clamp
- [x] P6.3 JS 内置对象深化（Array.reduce / Object.keys / String.repeat / padStart / padEnd）
- [x] P6.4 测试扩充至 103 条
- [x] P7.1 CSS z-index（层叠排序渲染）
- [x] P7.2 display: table（行列布局 + border-collapse）
- [x] P7.3 JS 数组高阶方法补全（find / findIndex / some / every）
- [x] P7.4 position: sticky（全向偏移）
- [x] P7.5 测试扩充至 114 条
- [x] P8.1 CSS :hover / :focus 伪类选择器（交互态匹配）
- [x] P8.2 position: relative（top / left 偏移定位）
- [x] P8.3 JS 方法补全（Array.sort / includes / Object.entries / String.startsWith / endsWith）
- [x] P8.4 CSS 文本属性（letter-spacing / word-spacing / text-transform）
- [x] P8.5 测试扩充至 130 条
- [x] P9.1 CSS @media queries（min-width / max-width / min-height / max-height）
- [x] P9.2 position:absolute / relative 全向偏移（right / bottom 支持）
- [x] P9.3 JS 方法深化（Array.flat / flatMap / Object.assign / Number / String.trimStart / trimEnd）
- [x] P9.4 CSS 视觉深化（clip-path inset / linear-gradient / text-shadow）+ hover 渲染 API
- [x] P9.5 测试扩充至 157 条
- [x] P10.1 CSS :first-child / :last-child / :nth-child(n) 伪类
- [x] P10.2 JS Map / Set / Array.from / String.replaceAll
- [x] P10.3 CSS outline + background-image toy
- [x] P10.4 JS Error / 嵌套 JSON / Array.toString
- [x] P10.5 测试扩充至 179 条
- [x] P11.1 CSS transition 多属性 + transform
- [x] P11.2 JS try / catch / finally + throw
- [x] P11.3 CSS @supports 玩具 + display 切换
- [x] P11.4 命中测试 + 事件坐标 API
- [x] P11.5 测试扩充至 201 条
- [x] P12.1 多属性 @keyframes + Web Animations 迷你 API（KeyframeTrack + AnimationHandle + build_keyframe_handle）
- [x] P12.2 JS class / extends / constructor / super（ToyClass + Pratt 修复 + 字符串拼接整数格式化）
- [x] P12.3 CSS calc() 玩具 + media 复合条件（parse_calc + media_matches 精确匹配）
- [x] P12.4 mousemove 状态机 + 预渲染回调（render_document_with_hover_at / render_document_with_mouse）
- [x] P13.1 JS 解构赋值 + 对象展开运算符（DestructuringDecl + ObjectSpread）
- [x] P13.2 JS 字符串模板字面量（反引号 + ${...} 插值）
- [x] P13.3 CSS filter 效果（blur / brightness / contrast 后处理）
- [x] P13.4 事件委托 + 输入事件模拟（oninput / onfocus + 委托回退 + this.value）
- [x] P13.5 测试扩充至 252 条
- [x] P14.1 CSS 滚动容器（overflow:auto/scroll + scroll-top/scroll-left + JS scrollTop/scrollLeft）
- [x] P14.2 JS 箭头函数（v => … / (a, b) => … / () => …，表达式体隐式返回）
- [x] P14.3 CSS text-indent（LayoutBox.text_indent + 负值支持 + 渲染偏移）
- [x] P14.4 CSS @font-face 玩具（多块解析 + font-family 引用级联安全）
- [x] P14.5 测试扩充至 280 条
- [x] P15.1 JS Infinity / NaN 字面量 + Array.at + Number.isNaN/isInteger
- [x] P15.2 CSS 滚动 JS API（scrollIntoView / scrollBy / overflow 属性）
- [x] P15.3 CSS box-decoration-break / -webkit-appearance 玩具
- [x] P15.4 字体切换渲染（serif 10×8 变体 + font-family 继承 + 渲染）
- [x] P15.5 测试扩充至 308 条
- [x] P16.1 JS 数组/调用展开 + ===/!== 严格相等
- [x] P16.2 JS 可选链 + 空值合并（?. / ??）
- [x] P16.3 CSS 文本增强（text-justify / word-break / hyphens）
- [x] P16.4 CSS scroll-snap 玩具
- [x] P16.5 测试扩充至 345 条
- [x] P17.1 TTF/OTF 头部表解析（head/hhea/hmtx/maxp/OS/2）
- [x] P17.2 glyf/loca 轮廓提取（二次贝塞尔 + 复合字形）
- [x] P17.3 cmap 逆向查询 + advance width 替换估算
- [x] P17.4 @font-face 升级：加载真实字体文件 + 双字重切换
- [x] P17.5 测试扩充至 370 条
- [x] P18.1 半领导者模型 + baseline 对齐
- [x] P18.2 内联元素跨行拆分 + 样式保留
- [x] P18.3 vertical-align 玩具版 + 行内图片抬高行高
- [x] P18.4 text-align 作用于真实行盒
- [x] P18.5 测试扩充至 389 条
- [x] P19.1 UAX#14 简化断行算法（break class 分级）
- [x] P19.2 CJK 逐字断行 + 禁则处理（行首/行尾禁则字）
- [x] P19.3 &shy; 软连字符 + word-break/overflow-wrap 补全
- [x] P19.4 长单词溢出的最小处理
- [x] P19.5 测试扩充至 408 条
- [x] P31.1 tkinter 零依赖外壳
- [x] P31.2 多标签页 + 页面实例隔离
- [x] P31.3 键盘快捷键（Ctrl+L/T/W/R、Alt+←/→）
- [x] P31.4 本地文件 / about: / 内联 HTML
- [x] P31.5 测试扩充至 415 条
- [x] P20.1 二次贝塞尔曲线填充 + 覆盖率抗锯齿
- [x] P20.2 合成粗体（stroke offset / dilation）
- [x] P20.3 完整字重阶梯 + 合成粗体兜底
- [x] P20.4 字体回退链 + weight_class 匹配
- [x] P20.5 letter-spacing / text-rendering 玩具
- [x] P20.6 曲线路径接入文字渲染管线
- [x] P20.7 测试扩充至 437 条（M1 收官）
- [x] P21.1 URL 解析（scheme/host/port/path/query/fragment + 相对 + 规范化）
- [x] P21.2 HTTP/1.1 最小客户端（GET + Host + 状态码分派）
- [x] P21.3 Content-Type 分派 + 统一错误页
- [x] P21.4 测试扩充至 456 条（本地 http.server 夹具，离线可测）
- [x] P22.1 重定向链（≤5 层 + 循环检测）
- [x] P22.2 Content-Encoding: gzip 解压
- [x] P22.3 Transfer-Encoding: chunked 解析钩子
- [x] P22.4 子资源并发加载 + 失败不阻塞
- [x] P22.5 `<base>` 标签玩具（测试总数 456 → 476）
- [x] P23.1 HTTPS（ssl 包裹 + 校验开关，--insecure）
- [x] P23.2 启发式磁盘缓存（Cache-Control + 10% 启发式）
- [x] P23.3 ETag / If-None-Match 304 协商缓存
- [x] P23.4 加载进度事件（接现有事件循环；--progress）
- [x] P23.5 测试扩充至 492 条 + POST 表单（测试总数 476 → 492）
- [x] P24.1 原始文本模式 + entities 完整表（script/style/textarea 原文 + 命名/数字引用）
- [x] P24.2 void elements + 自动闭合纠错（br/img 不压栈、<p> 隐式兄弟、块级闭合 <p>、裸 < 容错）
- [x] P24.3 注释 / DOCTYPE / 大小写不敏感 + 表格 foster parenting
- [x] P24.4 格式化元素重构（<b><i></b></i> adoption agency 简化版）
- [x] P24.5 html_pkg/ 子模块 + 字节流输入（dom.py 兼容 shim，下游零改动）
- [x] P24.6 测试扩充至 514 条（测试总数 492 → 514）

## 与 Rust 版的对应关系

| Python 模块 | Rust 模块 | 说明 |
|---|---|---|
| `dom.py` | `dom.rs` | P24 兼容 shim（转发 html_pkg；Node 与 html_pkg.node.Node 同一类对象） |
| `html_pkg/` | （新增） | P24 HTML 解析器子模块：node（Node + VOID_TAGS）/ entities（P24.1 完整表 + 数字引用）/ tokenize（P24.2/5 词法 + BOM 嗅探 + 原始文本模式）/ build（P24.2/3/4 容错树构建：void / 隐式闭合 / adoption agency / foster parenting / 大小写不敏感） |
| `css.py` | `css.rs` | CSS 解析 + 级联 + 选择器 |
| `layout.py` | `layout.rs` | 盒模型 + 命中测试 |
| `paint.py` | `paint.rs` | 位图 + 颜色 |
| `paint_engine.py` | `paint.rs`（显示列表部分） | 显示列表 + 光栅化 |
| `text.py` + `font.py` | `text.rs` + `font.rs` | 8×8 位图字体 |
| `rasterize.py` | （新增） | P20 二次贝塞尔曲线光栅化（扫描线 + 抗锯齿 + 合成粗体） |
| `ttf_font.py` | （新增） | P17 TTF/OTF 解析器 + P20 字重/回退链 |
| `js.py` | `js.rs` | 玩具 JS 引擎（Math/JSON/Date/RegExp + 通用对象分派） |
| `browser.py` | `lib.rs` / `browser.rs` | 公开 API + 失效 + a11y + links + PNG + transition + animate |
| `transition.py` | （新增） | CSS 过渡引擎（属性插值 + 帧推进 + 统一 lerp） |
| `gui.py` | （新增） | P31 tkinter 零依赖多标签 GUI 外壳（P21 http 加载） |
| `net.py` | （新增） | P21/P22/P23 URL 解析 + HTTP/1.1 客户端 + 重定向/gzip/chunked + 缓存 + TLS + 进度 |
| `main.py` | `main.rs` | CLI（--a11y / --links / --png / --animate / P23 --insecure / --cache / --progress） |

## 环境

- Python 3.11.9（`C:\Users\zklzk\AppData\Local\Python\pythoncore-3.11-64\python.exe`）
- 零外部依赖，纯标准库

## 参考

- [Web Browser Engineering](http://browser.engineering/) — Pavel Panchekha & Chris Harrelson, OUP 2024
- [Chrome RenderingNG Architecture](https://developer.chrome.com/docs/chromium/renderingng-architecture)
- [Servo Layout System](https://servo.org/files/2025-09-13-a-dive-into-the-servo-layout-system.pdf)

---

## 仓库说明


- **托管**：Gitee 私有仓库 <https://gitee.com/zklzkl11111/toy-browser-py>，默认分支 `main`。
- **本仓库只含源码与文档**（约 0.6MB）：14 个 `.py` 模块、`README.md`、`ROADMAP.md`、
  `demo.html`，以及 `a11y.json` / `links.json` / `links.txt` 等小体积测试数据。
  P17 起仓库额外携带 `fonts/`（arial.ttf + arialbd.ttf 测试夹具，~2MB）——
  纯标准库项目无安装步骤，测试夹具随仓库分发使 `python test.py` 开箱即跑。
- **已排除的生成物**（可由 CLI 重新生成，见 [.gitignore](.gitignore)）：

  | 已排除 | 体积 | 恢复方式 |
  |---|---|---|
  | `*.ppm`（`out.ppm` / `out3.ppm` / `out_click.ppm` / `out_event.ppm`） | ~4.5MB | `python main.py demo.html out.ppm` |
  | `out*.png` / `_smoke.png` | ~0.01MB | `python main.py demo.html out.png --png` |
  | `__pycache__/` | ~0.6MB | 运行时自动生成 |
  | `anim_test/` | — | `python main.py demo.html --animate` 的帧输出目录 |

- **运行环境**：Python 3.11.9，零外部依赖（纯标准库）。
- **自测**：`python test.py` → **415/415 通过**。
- **姊妹项目**：[toy-browser](https://gitee.com/zklzkl11111/toy-browser)（Rust 版，同一管线的原生实现）。

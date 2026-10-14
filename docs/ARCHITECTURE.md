# ARCHITECTURE — toy-browser-py

> **M4 里程碑文档（P32 重构窗口产物）+ P33.R1 CJK 渲染层 + 内存治理
> + P33.R2 网络并发 / GUI 滚动 + P33.R3 JS 块作用域 / TDZ
> + P33.R4 布局验证（position / z-index / transform / overflow / grid）
> + P33.R5 增量布局 / 区域光栅化 / 布局热点优化**。
> 描述 `toy-browser-py` 的模块边界、数据流与扩展点。读者假设：已了解 HTML/CSS/
> 浏览器管线，熟悉 Python 3.11。全文与源码注释中的 `P##` 编号对应。

---

## 1. 一图总览

```
                    ┌──────────────────────────────────────────────┐
                    │            入口（用户可见层）                  │
                    │   main.py   gui.py(Tk)   (headless: test.py)  │
                    └──────────────┬───────────────────────────────┘
                                   │
   ┌──────────────┐     ┌─────────▼──────────────────────────────────┐
   │ 会话层 P32    │     │           渲染管线（核心）                  │
   │ session.py   │     │  HTML ─▶ DOM ─▶ CSS ─▶ Layout ─▶ Paint ─▶ │
   │ cookie_store │     │   dom    css    layout   paint_engine     │
   │ (jar)        │     │        ─▶ js(脚本)   ─▶ Bitmap(像素)       │
   └──────────────┘     └─────────┬──────────────────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────────────┐
                    │          网络层 P21–P23                       │
                    │  net.py: URL 解析 / HTTP(TLS) / 缓存 / 进度    │
                    │  error_page_html + categorize_fetch_error     │
                    └──────────────────────────────────────────────┘
```

数据流单向：`HTML → DOM → styled → box tree → display list → pixels`。
脚本（`js.py`）在 **DOM 构建后、样式计算前** 运行，可变更 DOM，随后整条
管线重算。会话层（`session.py` / `cookie_store.py`）是**正交**的：不进入
渲染，只记录/回放外部状态。

---

## 2. 模块清单与职责

### 2.1 渲染核心（零 I/O，纯计算）

| 模块 | 职责 | 关键类型 / 函数 |
|---|---|---|
| `paint.py` | 像素位图与颜色模型；PPM/PNG 编码（纯 stdlib `struct`+`zlib`） | `Bitmap`、`Color`、`to_ppm()`、`write_png()` |
| `layout.py` (P33.R4/R5, P33.P1/P2/P3) | box 模型、flex、**position/absolute/relative/sticky + z-index + transform + overflow**、CSS Grid 行累积、**dirty 子树增量布局**、命中测试、**P33.P1 super-fast path（`_FAST_PATH_MAX_PROPS` + `_SPECIAL_PROPS` dict 直读）**、**P33.P2/P3 canvas/svg/video/audio 占位尺寸** | `build_box(styled)`、`_layout_grid`、`LayoutBox`（含 `dirty` 标志）、`hit_test`、`mark_dirty`、`layout_dirty_only`、`clear_caches` |
| `css.py` (P33.P1, P33.P3) | CSS 值/解析/选择器/级联（P25 引入 specificity + `!important`）、**P33.P1 `CalcExpr`/`VarExpr` + `--*` 自定义属性 + `calc()` 混合 %/px + `resolve_length(ref_px)`**、**P33.P3 `FontFace` + `parse_font_faces`** | `Value`、`Selector`、`CalcExpr`、`VarExpr`、`FontFace`、`parse_rulesheet`、`parse_font_faces`、`apply()`、`StyledNode` |
| `html_pkg/` (P24) | 容错 HTML5 解析器（替代早期 `dom.py` 手写版） | `parse(html) → Node` |
| `dom.py` | **兼容垫片**：`from html_pkg import Node` 等 re-export，保证旧 `dom.parse` 调用不变 | |
| `text.py`/`font.py`/`freetype.py`/`ttf_font.py` (P17–P20, P33.R1/R5) | 真实 TTF/TTC 解析、CJK 逐字回退面、8-bit 字形 mask 缓存、行盒/断行/光栅化（缺省 bitmap 字体）；**P33.R5 `_metrics_memo` 缓存 + `init_real_fonts` 快速路径** | |
| `net.py` (P21–P23, P33.R2, P33.P2) | URL 解析 / HTTP(TLS) / 缓存 / 进度 / 错误分类 / **keep-alive 连接池 + 子资源并发拉取** / **P33.P2 `CorsPolicy` + `parse_csp_directives` + `HttpResponse.is_cors_allowed` / `is_csp_violation`** | `HttpClient`、`CorsPolicy`、`parse_csp_directives`、`is_origin_match`、`load_subresources_parallel` |
| `js.py` (P33.R3, P33.P1) | 玩具 JS 引擎：lexer + Pratt 解析 + 树遍历解释器 + **块作用域 / TDZ / `++`/`--`** + **P33.P1 Web API 全局（`localStorage`/`WebSocket`/`navigator`/`location`/`history`/`URL`/`URLSearchParams`，由 `web_api.register_web_api` 注入）** | `Host(root, log)`、`execute_all()` |
| `web_api.py` (P33.P1) | **Web API toy 全局**：`Storage`（内存 + 5 MiB 配额 + `QuotaExceededError`）、`ToyWebSocket`（`readyState`/`send`/`close`/`onmessage`）、`make_navigator`/`make_location`/`make_history`、`ToyURL`/`ToyURLSearchParams`（包装 `urllib.parse`）、`register_web_api(host)` | `Storage`、`ToyWebSocket`、`ToyURL`、`ToyURLSearchParams`、`register_web_api` |
| `browser.py` | **公开管线 API**：`render_document(html, css, w, h)` 及 P2.1 a11y / P2.2 链接 / P2.3 PNG / P4.1 keyframes / P11.4 hit-test / **P32.4 DevTools** | `render_document`、`devtools_report` |

> **边界原则**：渲染核心不 import `net`、不写文件、不碰 tkinter。
> 所有 I/O 由 `main.py` / `gui.py` 注入。

### 2.2 网络层（P21–P23）

`net.py` 单一模块，自底向上：

1. **URL**：`parse_url(url, base)` → `URL` 数据类（scheme/host/port/path）。
2. **HTTP**：`HttpClient` 包装 `http.client`（`http.client.HTTPSConnection`
   做 TLS）。`get()` 内置：重定向（`_follow_redirects`，5 hop 上限 +
   环检测 → `RedirectLoop`）、**缓存**（`CacheStore`：`ETag`/`Last-Modified`
   + 304 复用 stale body）、**gzip** 透明解码（`maybe_gunzip`）、chunked
   （`decode_chunked`）、**P33.R2 keep-alive 连接池**
   （`_conn_pool` 按 `(scheme, host, port, verify_tls)` 键控，
   上限 8 条；`close_pool()` 关闭全部；`Connection: close` 响应
   不归还；死连接自动重试一次）。
3. **事件**：`LoadEventSink` + `LoadProgress`（P23.4 进度）。
4. **资源**：`collect_subresources`（P22.4/5：子资源 + `<base>`）；
   **P33.R2 `load_subresources_parallel`**：>2 条网络子资源走
   `ThreadPoolExecutor(max_workers=4)`；≤2 条顺序快速路径；
   `data:` URI 直接记录不走网络；进度事件随 future 完成发出。
5. **会话**：`cookie_store.CookieJar`（P32.3，见 §3.1）。
6. **错误**：`FetchError`（带 `.status`/`.url`）、`RedirectLoop`、
   `UnsupportedScheme`；**`categorize_fetch_error()`**（P32.5）把异常映射到
   `dns`/`connect`/`timeout`/`bad_url`/`redirect`/`http` 类别。

`fetch_html(url, client, ...)` 是高层便利入口：返回 `(html, HttpResponse)`，
失败抛 `FetchError`。

### 2.3 GUI 层（P31，`gui.py`）

零依赖（仅 `tkinter`，标准库）。两个类：

- **`PageInstance`**：单个标签页的渲染实例。`load_url()` 分发
  `about:` / 内联 `<...` / 本地文件 / `http(s)`；网络失败走
  `net.error_page_html`（P32.5 按类别出文案）。`back_stack`/`fwd_stack`
  是**页内**导航栈。`_about_page()` 渲染 `about:blank`/`about:history`/
  `about:bookmarks`/`view-source:`（P32）。
- **`TabBrowser`**：`tk.Notebook` 多标签 + 地址栏 + 工具条 + 状态栏 +
  快捷键（`Ctrl+L/T/W/R`、`Alt+←/→`）。持有 `history`/`bookmarks`
  两个全局 store（P32，见 §3.2），并给每个 `PageInstance` 设
  `_browser` 反向引用以便写全局历史。

**边界**：`gui.py` 只调用 `browser` / `net` / `session` 的公开 API，
不直接碰 DOM/CSS/layout 内部。headless 测试（`test.py`）构造
`PageInstance` 而不启动 `mainloop`。

### 2.4 CLI（`main.py`）

`argparse` 驱动的 headless 渲染器：读本地 HTML 或 URL，写 PPM/PNG。
旗标与阶段映射：

| 旗标 | 阶段 |
|---|---|
| `--a11y` / `--links` / `--png` | P2 |
| `--animate` / `--anim-frames` / `--anim-out` | P4.1 |
| `--event` / `--click` | P11.4 |
| `--insecure` / `--cache` / `--progress` | P23 |
| `--devtools` / `--devtools-el` / `--devtools-sel` | **P32.4** |

网络失败**仍出图**（错误页 PPM/PNG，exit 3）。

---

## 3. P32 新增模块

### 3.5 P33.R1 — CJK 渲染层（`ttf_font.py` + `text.py` + `font.py`）

- **`ttf_font.py`**：真实 TTF/TTC 解析（cmap / glyf / hmtx / loca / OS/2 /
  head / name）。`_font_candidates` 返回有序候选路径列表；
  `load_font_for_family` 逐个尝试直到解析成功（不可解析的系统字体
  不再阻塞回退）。`find_cjk_font` 按 `_CJK_FONT_FILES` 排序找 CJK 面
  （`simhei` 优先，~7 ms）。资源治理：`_MAX_FONT_BYTES = 12 MiB`、
  前进宽度数组上限 4096、`num_components > 128` 拒绝、
  `_outline_cache` 上限 4096。
- **`font.py`**：`init_real_fonts` 幂等（registry 被清空后自动 re-init，
  不触发全系统 CJK 扫描）；`get_cjk_fallback()` / `resolve_metrics_for_char`
  做逐字 CJK 回退。
- **`text.py`**：`_get_glyph_mask` 8-bit 覆盖 mask 缓存（上限 4096）；
  `_blit_coverage` 传行顶 `y`，按字符解析面（CJK 走回退面的 ascent），
  内部算 `baseline_y = y + ascent_px`；`advance_text` CJK 字用回退面的 advance。
- **`mem_guard.py`**（P33.R1 新增）：进程级堆内存看门狗。
  `MEMORY_CAP_MB = 6000`（6 GB 硬上限，留 2 GB 给 OS/Tk）；
  `MAX_FACE_MB = 12`（单面文件上限）；`can_load_face(bytes)` 按
  文件大小 + 当前堆预测拒绝超大面；`ttf_font._load_font_path` 接入
  该门控。纯 stdlib（tracemalloc），零外部依赖。

### 2.6 P33.R2 — GUI 全内容滚动（`gui.py`）

`PageInstance` 在 `load_html` 里先做一次 10 000 000 px 视口高度的
探测布局量出完整文档高度（上限 16 000 px），光栅化全图存
`_full_bitmap`；`_clip_bitmap(yoffset)` 裁剪到视口窗口。
`scroll_to / scroll_by / scroll_max / scroll_ratio` 平移裁剪。
`TabBrowser._ensure_page_frame` 给每个 tab 挂 `ttk.Scrollbar`
（垂直）+ `<MouseWheel>` 绑定（±40 px/格）；`_refresh_canvas`
同步 scrollbar thumb。

### 2.7 P33.R3 — JS 块作用域 + TDZ（`js.py`）

`Env.scope` 映射 `name → _Binding(value, kind, initialized)`；
`let`/`const` 创建块级记录（进块时 `declare_block`，执行时
`define` 填值）；`var` 保持函数级提升。读 `let`/`const`
在初始化之前 → `TDZError`。`const` 重赋值 → `RuntimeError`。
`for (let i = …)` 循环计数器在循环结束后不可见。
`++`/`--` 后缀/前缀自增（`IncDec` 节点 + `eval_incdec`）。
生成器体用独立 `Interpreter` 实例驱动，不污染外层 `env`。

### 2.8 P33.R4 — 布局验证（`layout.py` + `paint_engine.py`）

`position:absolute/relative/sticky` 的 `top/left/right/bottom`
偏移在 `build_box` 中解析、在 `layout_block` 中应用
（absolute 从父内容边缘偏移；relative 保持在文档流中平移；
sticky 静态布局下记录偏移）。`z-index` 正/负值均存储到
`LayoutBox.z_index`；`paint_engine.emit_commands` 按层稳定排序
（高 z 后绘制）。`transform:translate(x,y)` 解析到
`transform_tx/ty`，paint 阶段 `eff_x = rect.x + tx_off`。
`overflow:hidden/scroll/auto` 双轴存储（`overflow_x/y`）+
`scroll-top/left` 行内偏移；scroll 容器自身 paint 命令通过
`_tag_scroll` 标记 `scroll_enabled=True` + 裁剪区域。
`_layout_grid` 修复：按行累积高度，子项在正确的行 y 偏移。

### 2.9 P33.R5 — 增量布局 + 区域光栅化 + 布局热点优化

**dirty 子树增量布局**（`layout.py`）：
`LayoutBox.dirty: bool` 标志 + `mark_dirty(bx)` 迭代 DFS 标记
子树 + `layout_dirty_only(root)` 仅对 dirty 盒子重跑
`layout_block`（近似增量，局部样式变更正确；插入/移除兄弟
须重跑完整 `layout()`）。每次 `layout()` 完整路径末尾调用
`_mark_all_clean(root)` 重置全部标志。

**区域级重光栅化**（`paint_engine.py` + `paint.py`）：
`rasterize_region(bmp, dl, rx, ry, rw, rh)` 仅绘制 bbox 与
区域相交的显示命令，重叠命令裁剪到区域内，区域外像素不动；
`_fill_rounded_clipped` / `_draw_shadow_clipped` /
`_draw_border_clipped` 辅助裁剪绘制；`Bitmap.pixel_at(x, y)`
单像素读取（clamp 越界 → RGB 元组）。

**布局热点优化**：
- `build_box` 快速路径：普通 block 元素跳过 ~50 次属性读取，
  `_SPECIAL_PROPS` frozenset 集合交集守卫 +
  `_FAST_PATH_EXCLUDED_TAGS` 标签排除。
- `_child_index_cache`：`id(parent) → {id(child): idx}` 模块级
  缓存，O(n²) 兄弟扫描 → O(n) 一次构建 + O(1) 后续查找；
  `clear_caches()` 在每次 `layout()` 前清除。
- `_edges_for` 模块级导入 `EdgeList` / `resolve_length`。
- `css.StyledNode.line_height_px` 每节点 `_fm_cache` TTF 度量缓存。
- `font.init_real_fonts` 快速路径 + `_cjk_fallback_resolved` 守卫。
- `text._metrics_memo` + `clear_metrics_memo()` API；
  `advance_text` 拉丁 O(1) 路径（无 CJK 回退面时
  `len × single_advance`）。

### 2.10 P33.P1 — 浏览器级缺口补全（CSS 语义 + 事件模型 + Web API）

**CSS `var()` / `calc()` 完整语义**（`css.py` + `layout.py`）：
`CalcExpr(terms, unit_hint)` 支持混合 %/px 项；`VarExpr(name,
fallback)` 解析 `var(--name, fallback)`；`StyledNode.custom_props`
收集内联 `style=""` 中的 `--*` 声明并向下继承；
`_resolve_var` 带循环检测（frozenset）；`resolve_length(value,
parent_font_px, ref_px=None)` 新增 `ref_px` 参数供 % 项参照；
`layout.build_box` / `_build_children` 传递 `available_w` +
`sn.parent_width_px`。super-fast path：`_FAST_PATH_MAX_PROPS = 4` +
`_SPECIAL_PROPS`（含 `line-height`），`sn.props` dict 直读
width/height/color/background。

**GUI 事件模型 + 焦点**（`gui.py`）：
`PageInstance.hover_id` / `focus_id` / `focus_chain` /
`active_form_field` / `_form_states`；`_build_focus_chain`（tabindex
+ 表单控件遍历）；`hit_test_at(x, y)`（`layout.hit_test` + DOM
索引映射 + 滚动偏移）；`set_hover` / `set_focus` / `focus_next`；
`_reapply_interaction`（重跑 `css.apply(interaction_state)` +
layout + rasterize + clip）；`TabBrowser` 绑定：`<Tab>`/
`<Shift-Tab>` 焦点导航、`<Button-1>` 点击分发（命中 + 表单
toggle + 链接导航）、`<Motion>` 悬停跟踪、`<Return>` 激活。

**Web API 全局**（`web_api.py` + `js.py`）：
`Storage`（内存 + 5 MiB 配额 + `QuotaExceededError`）；
`ToyWebSocket`（`readyState`/`send`/`close`/`onmessage`，
`_simulate_message` 测试钩子）；`make_navigator`/`make_location`/
`make_history`；`ToyURL`/`ToyURLSearchParams`（包装
`urllib.parse`）；`js.Host.__init__` 自动注入
`web_api.register_web_api`（懒加载，失败静默降级）。

### 2.11 P33.P2 — CORS/CSP + Canvas 2D 基础

**网络栈 CORS/CSP**（`net.py`）：
`CorsPolicy` dataclass（解析 `Access-Control-Allow-*` 头）；
`HttpResponse.cors_policy` / `is_cors_allowed(origin)`；
`parse_csp_directives(header)` → `{directive: [values]}`；
`HttpResponse.csp_directives` / `is_csp_violation(kind, source)`
toy 执行（`'none'` 单独出现时阻塞一切，`'self'` 放行，
`default-src` 兜底）；`is_origin_match(a, b)` 大小写不敏感 +
默认端口归一化。

**Canvas 元素**（`layout.py` + `paint_engine.py`）：
`build_box` 读 `<canvas>` `width`/`height` DOM 属性，缺省
300×150；CSS 宽度覆盖属性值；`paint_engine` 无 CSS 背景时
渲染白色占位矩形，有 CSS 背景时渲染该颜色。

### 2.12 P33.P3 — @font-face + SVG/媒体占位

**@font-face 解析**（`css.py`）：
`FontFace` dataclass（`family`/`src`/`weight`/`style`/
`unicode_range`；`is_local` 属性；`url` 属性）；
`parse_font_faces(css_text)` 扫描 `@font-face` 块，解析声明
为 `FontFace` 对象列表。

**SVG / 媒体占位**（`layout.py` + `paint_engine.py`）：
`<svg>`/`<video>`/`<audio>` 读 `width`/`height` DOM 属性，
缺省 300×150；`paint_engine` 无 CSS 背景时渲染浅灰占位矩形
（#e6e6e6）+ 1px 边框（#a0a0a0）；`_FAST_PATH_EXCLUDED_TAGS`
加入这三个标签。

**项目元数据**：
`LICENSE`（MIT）、`CONTRIBUTING.md`、`CHANGELOG.md`。

---

## 4. 扩展点（M5 候选，见规划 §5）

### 3.1 `cookie_store.py` — Cookie 玩具

```
CookieJar ──ingest_response(url, headers)──▶ parse_set_cookie
            ──cookie_header_for(url)────────▶ "name=val; name2=val2"
```

- `parse_set_cookie(header, base_url)` 解析 `name=value` + 属性
  `Domain`/`Path`/`Max-Age`/`Expires`。`Max-Age<=0` → 立即过期。
  非法（无 `=` 或空名）返回 `None`（静默丢弃，符合玩具约定）。
- `CookieJar` 按 `(name, domain, path)` upsert；`cookie_header_for`
  做 host 后缀匹配（`.example.com` 适用子域）+ path 前缀匹配。
- **无安全边界**（计划明示）：不校验 `HttpOnly`/`Secure`/`SameSite`，
  不加密、不上锁。仅用于教学回放。
- **接线**：`net.HttpClient(cookies=jar)` 在 `_single_get` 请求时附
  `Cookie` 头、收到响应时 `ingest_response`。`fetch_html` 不改签名。

### 3.2 `session.py` — 历史 + 书签（JSON 持久化）

- **`HistoryStore`**：全局访问日志（`{url,title,ts}` 列表，上限
  `max_entries`，默认 500）。`add()` 折叠**相邻**重复 URL。
  `about:` 前缀不记录。`_atomic_write_json`（写 `.tmp` 再 `os.replace`）
  保证中途崩溃不损坏；`_load_json_list` 损坏时降级为空（不抛）。
  `in_memory_only=True` 用于测试，不落盘。
- **`BookmarkStore`**：`{url,title}` 列表，URL 唯一（重复 add 更新
  标题）。`add/remove/has/urls/all`。
- **`bookmarks_page_html(store)`**：渲染 `about:bookmarks` 页（含
  空态提示）；`_esc()` 做最小 HTML 转义（防注入）。

### 3.3 DevTools（`browser.py`）

- `devtools_dom_tree(root, max_depth, max_nodes)`：缩进树，
  `tag#id.class [text]`。
- `devtools_computed_style(root, css, el_id, w, h)`：跑 `css.apply`
  （含 UA + 内联 + author + `@media`/`@supports`），返回
  `{prop:"value"}` + `__meta__`；元素缺失返回 `__notfound__`。
- `devtools_selector_hits(css, root, sel, w, h)`：哪些节点命中
  *查询*，以及命中它们的 *作者规则*（用 `_devtools_effective_rules`，
  与渲染器同一套 at-rule 感知规则集）。
- `devtools_report(html, css, w, h, element_id, selector)`：聚合
  上述三者为一份文本报告，供 `--devtools` 直接打印。

### 3.4 统一错误页（`net.py`）

`error_page_html(title, detail, category)` 按 `category` 出独立文案
（`dns`/`connect`/`timeout`/`bad_url`/`redirect`/`http`/空=透传）。
`categorize_fetch_error(exc)` 依据异常类型 + 消息启发式归类。
`gui.py` 与 `main.py` 都在失败分支调用它。

---

## 4. 扩展点（M5 候选，见规划 §5）

- **性能（A）**：`layout.build_box` 全量重算 → 失效树化；
  `browser.changed_region` 已给出差分区域 API。
- **手写网络（B）**：`net.HttpClient._single_get` 当前基于
  `http.client`；`decode_chunked`/`maybe_gunzip` 是为裸 socket 复用
  而保留的 hook。
- **JS 提速（C）**：`js.py` 树遍历 → 字节码 + 栈 VM。
- **安全（D）**：同源策略 + CSP 头 + JS 沙箱边界（当前 `js.Host`
  无安全隔离）。
- **CSS Grid（E）**：`layout.py` 现仅 block/inline/flex；Grid 是
  P17 候选的"深做版"。

---

## 5. 模块边界不变式（M4 验收基线）

1. 渲染核心（§2.1）保持零 I/O；新增 I/O 一律注入。
2. `net.py` 不自建 TLS/缓存/cookie 之外的会话状态；
   cookie 只通过 `HttpClient(cookies=)` 挂接。
3. 会话层（`session`/`cookie_store`）可整体删除而不影响渲染：
   它们是**正交可插拔**的。
4. 每个 P 阶段 ~20 测试；`test.py` 尾部 `main()` 注册所有
   `("name", test_fn)`，全绿方为里程碑验收。

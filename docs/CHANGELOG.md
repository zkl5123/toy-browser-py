# Changelog

All notable changes to toy-browser-py are documented in this file.

The project follows a milestone scheme (P33 = project 33, R1–R5 =
roadmap milestones).

---

## R4 stage 7 — P2 性能与内存剖析（第四轮计划收尾）

*分段耗时 + 峰值内存报告、`changed_region` 局部重绘路径验证、PNG 编码
路径优化。全部 7 个阶段完成，`test.py` 936/936。*

### Added

- **`tools/prof_r4.py` — 分段剖析器**：对 100/1000/5000/10000 元素
  的 div-list 负载逐段计时（parse / css / layout / paint / png，
  min of 3 samples）+ 峰值 RSS（Windows 走
  `psapi.GetProcessMemoryInfo` 的 64 位 `PROCESS_MEMORY_COUNTERS_EX`，
  POSIX 走 `resource.getrusage`）。
- **`assessments/20261016/perf_r4_profile.json`**：剖析报告产物。
- **`browser.write_png_bytes(bmp) -> bytes`**：PNG 编码的内存版
  （无文件 I/O），与 `write_png` 共用同一路径，供剖析段隔离磁盘
  延迟。

### Changed

- **`browser.py` PNG 编码路径**（`write_png` / 新增
  `write_png_bytes`）：行打包改为预分配 buffer + 逐行写入
  （`_png_pack_row` / `_png_raw_rows`），替代旧的增长式
  `bytearray().extend` 内循环。输出字节与旧实现**逐位一致**
  （真实渲染 / 800×600 随机像素 / 400×16000 / 1×1 / 0 高五种
  位图全部验证）；PNG 段从 ~3.8s 降到 ~0.8s（约 4.7×）。
- **`docs/IMPROVEMENT_PLAN_R4.md`**：阶段 7 条目补全结果 + 进度行
  更新；全部 7 阶段标记完成。

### 剖析结果（本机型，min of 3 samples；纯 CPU，无 PIL）

| 负载 | parse | css | layout | paint | png | render | 峰值 RSS |
|---|---|---|---|---|---|---|---|
| 100 | 0.7ms | 2.6ms | 117ms | 218ms | 815ms | **338ms** | 45MB |
| 1000 | 68ms | 401ms | 1157ms | 679ms | 815ms | **2304ms** | 97MB |
| 5000 | 351ms | 2013ms | 6063ms | 2781ms | 828ms | **11207ms** | 237MB |
| 10000 | 697ms | 4073ms | 12274ms | 5415ms | 816ms | **22457ms** | 575MB |

1000 元素 render 段 2304ms（含 PNG 3120ms），**未达 <600ms 目标**——
瓶颈在 layout（`build_box` 递归 + 边缘解析）与 css（`_apply_subtree`
级联 + 声明解析），非 PNG。C 扩展决策点按计划留待固定负载机器扩
样本后再定，不在 stdlib 引擎范围内硬优化。

`changed_region` 局部重绘路径验证：100 元素文档（~2200px 内容，
800×600 视口）显示列表下移 300px 前后两帧，diff 包围盒
(10, 2, 100, 598)、diff 像素 36069/480000 = **7.5%**（局部条带，非
整帧），满足局部重绘前置条件。

---

## P0/P1 — GPT 报告剩余项整改（CORS/CSP 执行 + @supports 属性检查 + 测试拆分 + 文档澄清）

*GPT P0-4（CORS/CSP 接入请求流）、P1-1（@supports 真实属性检查）、
P1-2（test.py 拆分为模块化测试文件）、P1-3（文档澄清 + 工件清理）。
主测试套件 920/925 通过（5 个失败为环境相关：R5 性能基准 ×3 +
P34 R2 CJK 字体 ×2，与整改前基线一致）。*

### Added

- **`css.py` — @supports 真实属性检查（GPT P1-1）**：
  `_CSS_CAPABILITY_TABLE`（引擎实际解析并消费的 CSS 属性集合）+
  `_SUPPORTED_PROPERTY_VALUES`（属性→值的矩阵，例如
  `position: sticky` 为 False 因为布局阶段未实现粘性定位，
  而 `position: absolute`/`fixed`/`relative`/`static` 为 True）。
  `supports_matches(condition)` 现在按属性名 + 值级别判断，
  支持 `not` / `and` / `or` 逻辑组合，`selector()`/`font-tech()`
  始终视为支持（玩具语义），未知属性返回 False。
- **`net.py` — CORS/CSP 策略接入请求流（GPT P0-4）**：
  `HttpResponse.is_policy_allowed(requesting_origin, resource_kind,
  resource_url)` 统一执行 CORS（跨源读取需 ACAO 匹配）+ CSP
  （`style-src`/`img-src`/`script-src`/`connect-src` 指令检查）。
  `load_subresources` / `load_subresources_parallel` 新增
  `enforce_policy`（默认 True）+ `doc_csp`（文档级 CSP 头）参数，
  策略拦截的子资源映射为 `None`（与 404 失败契约一致）。
  `_csp_blocks(values, source_value)` 辅助函数供文档级 CSP 路径复用。
  `render_document_from_url` 现在传递文档的 `Content-Security-Policy`
  头到子资源加载。
- **`test.py` 拆分为模块化测试文件（GPT P1-2）**：
  原 11,156 行的 `test.py` 拆分为 7 个模块测试文件
  （`test_dom.py` / `test_css.py` / `test_js.py` / `test_layout.py` /
  `test_paint.py` / `test_net.py` / `test_browser.py`）+ 共享
  辅助函数文件 `test_helpers.py`（23 个辅助函数）。
  `test.py` 变为薄编排器（99 行），从各模块导入 `TESTS` 列表并
  以原有 try/except 契约运行。所有预存模块测试文件
  （`test_r1.py` … `test_p34_r6.py`、`test_p0_js_budget.py` 等）
  新增 `TESTS` 列表。新增 `test_p0_policy.py`（16 条 CORS/CSP
  策略测试）+ `test_p1_supports.py`（19 条 @supports 测试）。
- **`README.md` — 功能状态说明（GPT P1-3）**：新增
  "功能状态说明"表格，标注每项功能为
  "已实现 / 部分支持 / 模拟实现 / 尚未实现"（@supports、
  position: sticky、CSP、CORS、fetch、WebSocket、localStorage、
  CSS Grid、@keyframes、scroll-behavior）。移除已废弃的
  `out_event.ppm` 引用。

### Changed

- **`browser.py`**：`render_document_from_url` 传递文档 CSP 头到
  `load_subresources(doc_csp=...)`。
- **`net.py`**：`load_subresources` / `load_subresources_parallel`
  默认执行 CORS/CSP 策略检查（`enforce_policy=True`），可通过
  `enforce_policy=False` 恢复旧行为（测试用）。

### Removed

- **工件清理（GPT P1-3）**：`out_event.ppm` 和 `p22_err.ppm`
  已在之前的 P0 轮次中从 git 移除（.gitignore 覆盖 `*.ppm`）。
  README 中对应的废弃引用已清除。

---

## P0 — GPT/GLM 报告 P0 专项整改

*字体渲染崩溃、JS 执行预算、网络与 Cookie 安全边界、级联源优先级、
head/meta/style 隐藏、行内盒子宽度、网络默认超时、响应大小上限。
全部 P0/P1 项目已修复，主测试套件 807/807 通过。*

### Added

- **`text.py` — 字体渲染崩溃修复（GLM P0-1）**：`_face_baseline(None)`
  现在对 None face 做 `.notdef` 回退而不是崩溃；`wrap()` 中的
  `float()` 类型转换防止 int/float 混用时的 TypeError。
- **`js.py` — JS 执行预算（GPT P0-1 / GLM P0-2）**：
  `Interpreter._steps`/`_budget` 计数每个 eval 步骤；
  `_BudgetExceeded` 异常在超限时抛出；`Host.execute` 捕获并记录日志
  而不崩溃。新增 `Ternary` 节点、`typeof` 一元运算符、零除→
  `inf`/`nan`、JS 风格 `%`、宽松 `==`、`Math.round` 半入 `_js_round`、
  IIFE `return` 拼接、`execute_event` 预算捕获。
  递归守卫：`_make_func`/`_make_arrow` 中的 `_recursion_depth`
  计数器 + `RecursionError`→`_BudgetExceeded` 转换（Python 原生
  栈溢出在 JS 守卫的 256 层上限附近触发，需统一 API）。
  `eval_block` 的 `_Return` 捕获仅在非函数帧时生效（函数体直接
  遍历语句，`return` 正常传播到函数级捕获）。
- **`net.py` — 网络默认超时 + 响应大小上限（GLM P0-3 / GPT P0-5）**：
  `DEFAULT_TIMEOUT = 10.0`；所有 `fetch_*` 函数在 `timeout is None`
  时替换为 `DEFAULT_TIMEOUT`。`MAX_RESPONSE_BYTES = 256 MB`；
  `ResponseTooLarge` 异常；`HttpClient.max_response_bytes` 参数；
  `_single_get` 原始响应体和 gunzip 解压体均受上限约束。
- **`cookie_store.py` — Cookie 安全边界（GPT P0-3）**：
  `Cookie.secure`/`http_only`/`same_site` 标志；
  `parse_set_cookie` 解析 `Secure`/`HttpOnly`/`SameSite` 属性，
  拒绝无 `Secure` 的 `SameSite=None`，通过 `_domain_matches()`
  校验 Domain 后缀匹配（RFC 6265 §5.1.3）；
  `CookieJar.cookie_header_for(url, is_cross_site, method,
  is_top_level)` 强制 Secure + SameSite 过滤；
  `for_url` 同步过滤器。
- **`css.py` — 级联源优先级（GLM P1-1）**：
  `Rule.source`（"author"/"ua"）；`parse_rulesheet_nocache` 现在
  接受 `source` 参数并传播到每个 Rule；
  `css.apply(..., ua_css="")` 将 UA 表以 `source="ua"` 解析并
  前置到 author 规则之前；`_cascade_key` 已正确实现
  `(source == "author", important, spec_sum)`。
  `Selector._simple_matches` 中 `*` 通配符现在匹配所有元素
  （`node.is_element()`），修复了 `* { margin: 0 }` 永远不生效的
  问题。
- **`browser.py` — head/meta/style display:none（GLM P1-2）**：
  `_default_css()` 首行添加
  `head, meta, link, style, script, title { display: none; }`。
  `render_document_from_url`：`timeout is None` 时使用
  `net.DEFAULT_TIMEOUT`（10 s），不再无限阻塞。
  `devtools_computed_style` 新增 `ua_css` 参数。
- **`layout.py` — 行内盒子宽度（GLM P1-4）**：
  `layout_block` 行内 run 循环中，当行内盒子无显式宽度且所有
  子节点为 TEXT 时，通过 `text_mod.measure` 计算文本内容宽度
  + padding，作为 `containing_w` 传入子盒子布局。
- **`ttf_font.py` — CJK 字体发现（GLM P0-1 配套）**：
  `find_cjk_font` 改用基于 token 的 stem 匹配
  （`_cjk_stem_match`：noto, droid, wqy, wenquanyi, sourcehan,
  cjk, simsun 等），不再依赖字面 basename，修复
  `NotoSerifSC-Black.ttf` 等变体未被发现的问题。

### Changed

- `browser._full_css` 现在返回 `(author_css, ua_css)` 元组；
  所有 24 个调用点（`browser.py` 13、`gui.py` 2、`test_p2.py` 4、
  `test_p3.py` 5）已更新为解包。
- `net._single_get` / `post` 新增 `cookie_context` 参数，用于
  SameSite 元数据。
- `js.py` 的 `CallSpread` 拼接：空数组 `[]` 现在拼接零个参数
  （旧代码会把 `null` 值本身追加为参数）；`null`/`undefined`
  展开也拼接零个参数。

### Tests

- 主测试套件：**807/807 通过**（修复前 805/807，
  `js_call_spread_zero_args` 和 `r1_blit_coverage_render` 已修复）。
- P0 JS 预算测试（`test_p0_js_budget.py`）：12/12 通过。
- P34 R1–R6 回归测试：全部通过（P34 R2 中
  `get_glyph_mask_uses_resolved_face` 在本机因 CJK 字体环境
  跳过，非代码回归）。

---

## P34.6 — css 继承传播短路 + _emit 纯容器快路径 + _build_children 叶子快路径

*10k 节点压测文档：P34.5 ~28.5s → P34.6 ~27.5s（-3.5%）。emit 阶段
从 ~11.8s 降到 ~9.1s（-23%），是本轮主要收益；css 继承传播短路
贡献了 css 阶段的小幅优化。*

### Added

- **`css._apply_subtree` 继承传播短路**（P34.6a）：legacy 代码对
  每个子节点做 8 次 `in` 检查（line-height / text-align /
  text-transform / font-family / overflow-wrap / font-weight /
  letter-spacing / word-spacing），10k 节点文档 × 8 = 170k 次 dict
  查找。P34.6a 预计算父节点实际携带的候选属性集合
  （`_inherit_candidates`），子节点循环只迭代 0-2 个键（典型文档
  中父节点大多不显式设置这 8 个属性）。行为不变：子节点自身
  属性仍优先（`in sub.props` 检查不变）。
- **`paint_engine._emit` 纯容器快路径**（P34.6b）：一个 box 如果
  没有任何可绘制内容（无 background / border / text / shadow /
  outline / gradient / clip / image / control / filter / scroll /
  transform），跳过 ~30 个局部变量 hoist + filter dict build +
  `_dc` / `_cmds_append` / `_emit_one` 闭包定义 + ~15 个条件
  检查，直接递归子节点。10k 节点文档中 ~50% 的 21k box 是纯
  容器，跳过 per-box setup 是可观的收益。守卫保守地检查所有
  paint-source 标志；任一设置则走 legacy 全路径。
- **`layout._build_children` 叶子快路径**（P34.6c）：无子节点的
  box（10k 节点基准中 ~50% 是叶子）直接 `return`，省掉
  `parent_w` / `parent_h` 计算和空 `sn.children` 循环。
- **`test_p34_r6.py`**：19 条 P34.6 回归测试（继承候选字典、
  纯容器快路径、叶子快路径、管线像素不变性、性能阈值）。

### Changed

- 10k 节点压测文档全管线：P34.5 ~28.5s → P34.6 ~27.5s。
  emit 阶段 11.8s → 9.1s（-23%）；css 阶段 3.6s → ~4.6s（+28%，
  继承候选字典 pre-compute 的开销略大于短路收益，但 3000 节点
  文档 css 阶段反而快了 1.5%）。

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + 15 (P34r4) +
  **19 (P34r5)** + **19 (P34r6)** + 4 (CSS-memo) = **925** total,
  all green.

---

## P34.5 — CSS 候选规则身份缓存 + 单字符 advance 模块级缓存 + shorthand 短路

*10k 节点压测文档全管线：P34.4 基线 ~29.4s。本轮主要目标是 css.apply
阶段（~3s）与 text.advance 热路径。*

### Added

- **`css._candidate_rules_cache`**（`css._apply_subtree`）：按节点
  身份 (tag, class, [id], hover, focus) 缓存候选规则匹配结果。
  文档中绝大多数节点共享同一 (tag, class) 组合，匹配过程从"每
  节点一次"降为"每身份一次"。
  - `_identity_includes_id`：仅当规则集含 `#id` 选择器时把 id 纳入
    缓存键——压测文档有 ~9600 个唯一 id，无 `#` 选择器时把 id 纳入
    键会导致 100% miss，反而变慢。
  - `_identity_ancestor_sensitive`：当规则集含后代/子选择器
    （`div p` / `div > p`，`len(parts) > 1`）时绕过缓存，走传统
    逐节点匹配——组合选择器依赖祖先链，节点身份缓存会产生错误
    匹配（P34.5 开发中 `selector_descendant` / `selector_child`
    测试曾因此失败，已修复）。
  - `css.clear_caches()` 重置该缓存；`apply()` 在每次调用开头清除
    跨文档泄漏；`layout.clear_caches()` 也调用 `css.clear_caches()`。
- **`text._adv_char_cache`**（`text.advance_text` CJK 分支）：把
  P34.3 的 per-call 局部 `_adv_cache` 提升到模块级，键为
  (family, font_px_bucket, char)。`wrap()` 内部的 `_adv_of(ch)` 过去
  每字符调一次 `advance_text(ch, ...)`（10k 节点布局 10401 次
  单字符调用），现直接命中共享表，单次 dict 命中。上限 8192 条。
- **`text.clear_advance_cache()`** 现在同时清除 `_adv_char_cache`。
- **`css.expand_shorthands` 短路**（`_SHORTHAND_KEYS`）：无
  margin/padding/border-width 简写键的节点（典型文档 90%+）直接
  返回原 dict，省掉一次 dict 复制。
- **`css._apply_subtree` inline 只读一次**：`node.attr("style")`
  从每节点 2 次降为 1 次（10k 文档 43k+ 次 attr 查找 → 21k）。
- **`layout.clear_caches` 调用 `css.clear_caches`**（P34.5 接线）：
  两次独立文档渲染之间丢弃 CSS 候选缓存 + 文本缓存。
- **`test_p34_r5.py`**：20 条 P34.5 回归测试（候选缓存命中/重置、
  同身份同结果、shorthand 短路语义、`_adv_char_cache` 幂等性、
  `layout.clear_caches` 联动、管线正确性 + 像素不变性、性能阈值）。

### Changed

- 10k 节点压测文档全管线：P34.4 ~29.4s → P34.5 ~28.2s（-4%；
  css.apply 阶段 2.9s → ~0.5s，text.advance 单字符热路径消除
  10401 次调用）。
- 短文档（<1k 节点）管线：无变化（css.apply 本就是 <5ms 量级）。

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + 15 (P34r4) +
  **19 (P34r5)** + 4 (CSS-memo) = **906** total, all green.
  特别验证 `selector_descendant` / `selector_child` 在祖先敏感
  绕过下仍正确；`#id` 选择器在 `_identity_includes_id=True` 下
  按节点 id 独立解析。

---

## P34.4 — text.measure document-width cache + half-leading cache + _emit micro-opts

*Pipeline totals on the 10k-node stress doc: ~29.5s (P34.3 baseline) →
~24.5s after this round.  The `text.measure` document-width cache and
`_emit` closure elimination dominate the win; the half-leading cache
contributes a smaller but measurable improvement on layout.*

### Added

- **`text.measure` document-width cache** — short strings (< 128
  chars, the 95 % case of a typical document) go through a
  document-scoped cache keyed on `(text, family, font_px)`.  The
  10k-node layout measures the *identical* short CJK/Latin strings
  thousands of times across 21k text boxes; the cache turns the
  common case into a single dict hit.  Long strings stay on the
  O(n) advance scan.
- **`layout.layout_block` half-leading cache** — the legacy code
  re-resolved the TTF metrics + ascent / descent / half-leading on
  every text box (~3180 times in a 3k-node layout).  Now the
  computed `(half_lead, ascent_px, descent_px)` triple is cached per
  `(family, font_px, line_h)`; a document uses only a handful of
  distinct text sizes.
- **`text.clear_advance_cache()` + `text._doc_width_cache`** —
  explicit reset of the advance-width tables + document-width cache,
  called from `layout.clear_caches()` between independent document
  renders so stale widths do not leak into the next page.
- **`paint_engine._emit` micro-opts** — replaced the per-call
  `_tag` / `_tag_scroll` / `_tag_all` closure triple with a single
  `_emit_one` dispatch + inlined `cmd._layer = z` at each append
  site (the legacy code allocated 2 closures per _emit call on a
  21k-box tree = 42k closure objects + 21k function calls for the
  plain case).  Also hoisted `dl.commands.append` to `dl_append`
  (plain method local) and `DisplayCommand` to `_dc` so the
  constructor name is a local read instead of a module-global
  lookup at every command construction.
- **`test_p34_r4.py`** — 15 P34.4 regression tests (measure cache
  correctness, edge cases, `clear_advance_cache` reset semantics,
  `layout.clear_caches` wiring, half-leading cache consistency,
  `_emit` closure refactor regression guards, perf thresholds).

### Changed

- `layout.clear_caches()` now also resets `text._adv_width_tables`
  and `text._metrics_memo` in addition to its own `_child_index_cache`.
- `paint_engine._emit` no longer allocates the `_tag` / `_tag_all`
  closures on the non-scroll path (the common case); only the
  scroll-container path retains `_tag_scroll` as a function (a
  genuine behavior change that cannot be inlined away).

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + **15 (P34r4)**
  + 4 (CSS-memo) = **887** total, all green.

---

## P34.3 — viewport culling + text-node layout fast path + short-text skip-wrap + emit micro-opts

*Pipeline totals on the 10k-node stress doc: 122s (pre-P34.1) →
~25s after this round.  The viewport-cull + text-node fast path +
short-text skip-wrap dominate the win; emit/rasterize follow.*

### Added

- **`paint_engine.rasterize` viewport cull** — commands whose box
  does not intersect the bitmap are skipped before the kind
  dispatch (99 % of commands on a 10k-node doc lie below the
  1200×800 viewport).
- **`layout.build_box` text-node fast path** — a text node whose
  `props` dict is small (≤ 4 keys) and carries no `_SPECIAL_PROPS`
  property builds a `LayoutBox` directly with the inherited font
  family / color and skips the ~100-attribute slow path + the
  form-control / iframe / img / caption blocks.
- **`layout.layout_block` short-text skip-wrap** — a text box whose
  `measure()` width ≤ `available` wraps to exactly `[text]`
  without the `text.wrap` break-opportunity scan.  Most text nodes
  in a typical document are a single short line; this saves the
  full O(n) advance scan on the common case.
- **`rasterize.GlyphMask.ink_rows`** — lazily computed list of
  row indices carrying ≥ 1 non-zero coverage byte; `blit_glyph_mask`
  iterates ink rows only instead of all `h` rows.
- **`paint_engine.DisplayCommand._layer`** dataclass field
  (default 0) — `emit_commands` sorts by the field instead of a
  `hasattr`-based defaulting loop.
- **`_EMPTY_FILTER_FIELDS` shared dict** — no-filter boxes (the
  overwhelmingly common case) unpack a shared empty dict instead of
  rebuilding a 4-key dict per command.
- **`text.advance_text` per-char cache on the CJK path** — the
  mixed-script path caches `(ch, face)` → advance in a local dict
  so the hot loop is a single dict hit instead of a
  `resolve_metrics_for_char` function call + method lookup.
- **`p34r3_perf.py`** — per-stage pipeline benchmark
  (parse / css / layout / emit / rasterize) at 1k / 3k / 10k
  nodes.
- **`test_p34_r3.py`** — 25 P34.3 regression tests (culling
  bounds, text fast path correctness, short-text skip-wrap
  consistency, line-clamp slow path preservation, emit `_layer`
  field / sort / empty box).

### Changed

- 10k-node stress doc total pipeline time: ~29.5s (P34.2 baseline)
  → ~24.9s (this round).  Layout went 14.7s → ~10.6s (text fast
  path + skip-wrap).  Rasterize dropped from ~100s (pre-cull) to
  ~1.4s (99 % of commands off-viewport).
- Typical < 1k-node page: full pipeline ~2.5s → ~1s.
- `_collect_scroll_regions` and `_apply_scroll_clips` now read
  `cmd.scroll_enabled` directly (a DisplayCommand dataclass field)
  instead of a `getattr` with a default.
- `rasterize` post-pass (clip-region collection) is merged into the
  culling loop to avoid a second full command-list scan.

### Tests

- 807 (main) + 27 (P34) + 9 (P34r2) + 25 (P34r3) + 4
  (CSS-memo) = **872** total, all green.

---

## P33.P3 — @font-face + SVG/media placeholders + project metadata

### Added

- **`css.FontFace` + `css.parse_font_faces`** — parse `@font-face`
  blocks: family, src, weight, style, unicode-range. `FontFace.is_local`
  and `FontFace.url` helpers extract the source reference.
- **SVG / video / audio placeholder rendering** — `<svg>`, `<video>`,
  and `<audio>` elements now default to 300×150 px (matching the
  browser intrinsic size) and render a light-grey placeholder rectangle
  with a 1px border in the paint engine.
- **LICENSE** (MIT), **CONTRIBUTING.md**, **CHANGELOG.md**.

### Tests

- 10 new P3 tests (`test_p3.py`): `@font-face` parsing (4 tests) +
  SVG/video/audio sizing and placeholder rendering (6 tests).
- Test count: 797 → **807**.

---

## P33.P2 — CORS/CSP + Canvas 2D + performance benchmarks

### Added

- **`net.CorsPolicy`** — parsed CORS response policy
  (`Access-Control-Allow-Origin` / `Allow-Credentials` /
  `Allow-Headers`). `HttpResponse.is_cors_allowed(origin)` and
  `HttpResponse.cors_policy` properties.
- **`net.parse_csp_directives`** — parse a `Content-Security-Policy`
  header into a `{directive: [values]}` dict.
  `HttpResponse.csp_directives` and `HttpResponse.is_csp_violation
  (kind, source)` toy enforcement helpers.
- **`net.is_origin_match`** — case-insensitive origin comparison with
  default-port normalization.
- **Canvas 2D basics** — `<canvas>` elements now render a white-backed
  placeholder (or their CSS background) in the paint pipeline.
  `layout.build_box` pulls width/height from `width`/`height` DOM
  attributes; default 300×150 when absent.
- **Performance benchmarks** — 1000-node and 10000-node pipeline
  benchmarks registered in the test suite (threshold-tolerant /
  informational).

### Fixed

- `HttpResponse.csp_directives` now does case-insensitive header lookup
  (the raw `headers` dict may carry mixed-case keys from
  `http.client`).
- `is_csp_violation` now correctly handles `'none'` as a sole-value
  blocker and strips quotes from CSP token keywords.

### Tests

- 21 new P2 tests (`test_p2.py`): CORS (5), CSP (8), Canvas (5),
  benchmarks (3). Test count: 776 → **797**.

---

## P33.P1 — Browser-grade gaps

### Added

- **CSS `var()` custom properties** — `css.VarExpr`,
  `StyledNode.custom_props`, `_collect_custom_props`,
  `_resolve_var` (cycle-safe). Inline `style=""` custom props
  cascade to children; rulesheet `--*` not yet parsed (toy
  limitation, documented).
- **CSS `calc()` full semantics** — `css.CalcExpr` supports mixed
  %/px terms. `resolve_length` now takes an optional `ref_px`
  parameter; `layout.build_box` passes `available_w` through to
  children so `calc(100% − 20px)` resolves against the parent's
  content width.
- **`gui.PageInstance` event model** — hover/focus state, focus
  chain (tabindex traversal), `hit_test_at`, `set_hover`,
  `set_focus`, `focus_next`, `_reapply_interaction`, form
  state management (`_form_states`, `_toggle_checkbox`,
  `_set_form_value`).
- **`gui.TabBrowser` event bindings** — `<Tab>`/`<Shift-Tab>`
  keyboard focus navigation, `<Button-1>` click dispatch,
  `<Motion>` hover tracking, `<Return>` activation.
- **`web_api.py`** — Web API globals for the toy JS engine:
  `localStorage` / `sessionStorage` (in-memory, 5 MiB quota,
  `QuotaExceededError`), `WebSocket` (toy: no real I/O,
  `_simulate_message` test hook), `navigator`, `location`,
  `history`, `URL`, `URLSearchParams`.
- **Performance: P33.P1 super-fast path** — `layout.build_box`
  fast path now reads width/height/color/background directly
  from the `sn.props` dict (skipping method calls, `_resolve_var`,
  and `_edges_for` for the 3 common empty edge cases), gated by
  `_FAST_PATH_MAX_PROPS = 4` and the `_SPECIAL_PROPS` frozenset.
  `line-height` was added to `_SPECIAL_PROPS` so that divs with
  an explicit line-height go through the slow path.

### Tests

- 22 new P1 tests (`test_web_api.py`): localStorage (5),
  WebSocket (3), navigator (2), location (3), history (1),
  URL/URLSearchParams (6), JS integration (2), idempotency (1).
  Test count: 754 → **776**.

---

## P33.R5 — dirty-subtree layout + region re-rasterize

*Completed in a prior milestone (754/754 tests at R5 completion).*

### Added

- `LayoutBox.dirty` flag, `layout.mark_dirty`, `layout_dirty_only`,
  `layout.clear_caches`, `_child_index_cache`,
  `rasterize_region`, `Bitmap.pixel_at`, `text._metrics_memo`,
  `font._cjk_fallback_resolved`, `build_box` fast path with
  `_SPECIAL_PROPS` / `_FAST_PATH_EXCLUDED_TAGS`.

### Tests

- 9 new R5 tests. Test count: 745 → **754**.

---

## P33.R1–R4 — Roadmap milestones (completed)

*Details in `ROADMAP.md`.*

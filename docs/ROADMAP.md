# toy-browser-py — 改进路线图

基于 [Web Browser Engineering](http://browser.engineering/) 的 16 阶段教学模型，
对照当前实现差距，列出可落地的改进项。每项标注优先级与验收标准。

---

## 已完成（807/807 测试通过）

- [x] 阶段 1 — HTML 解析（`toy_browser/document/dom.py`）
- [x] 阶段 2 — CSS 样式 + 级联 + 内联（`toy_browser/style/css.py`）
- [x] 阶段 3 — 盒模型布局（`toy_browser/layout/layout.py`）
- [x] 阶段 4 — 显示列表 + CPU 光栅化（`toy_browser/rendering/paint_engine.py`）
- [x] 阶段 5 — 玩具 JS 引擎 + DOM 绑定 + `setTimeout` FIFO（`toy_browser/scripting/js.py`）
- [x] 阶段 6（部分）— `urllib.request` 拉取 URL；`fetch` 沙箱只允许 http(s)
- [x] 阶段 7（部分）— iframe 占位框、跨源 src 白名单、子 DOM 隔离
- [x] 文本 8×8 位图字体 + 换行/对齐（`toy_browser/typography/text.py` + `toy_browser/typography/font.py`）
- [x] 坐标命中测试 + 事件循环（`layout.hit_test` + `browser.render_document_with_click`）

---

## P0 — 全部已完成 ✅

### 0.1 图片 `<img>` 支持（阶段 15 Embedded Content）✅

- [x] `toy_browser/layout/layout.py`：`img` box 读 `width`/`height` DOM 属性，缺省 32×32。
- [x] `toy_browser/rendering/paint_engine.py`：灰色填充 + 1px 边框 + `img: <src>` 角标文字。
- [x] 测试：`test_img_placeholder`、`test_img_explicit_size`、`test_img_paints`。

### 0.2 表单控件渲染（阶段 7 Forms）✅

- [x] `browser._default_css()`：`button` 1px 边框 + 4px 内边距；`input` 1px 边框。
- [x] `toy_browser/layout/layout.py`：`input[type=text]` 固定 160×20 占位框。
- [x] 测试：`test_button_border`、`test_input_placeholder`。

### 0.3 CSS 选择器扩展（阶段 2 深化）✅

- [x] `css.Selector`：支持**后代组合**（`a b`）、**直接子**（`a > b`）、**多 class**（`.a.b`）。
- [x] 子选择器：沿**直接祖先链**无跳走，不匹配嵌套层级。
- [x] 测试：`test_selector_descendant`、`test_selector_child`、`test_selector_multi_class`。

### 0.4 JS 引擎补全（阶段 5 深化）✅

- [x] AST 新增 `ArrayLit` / `ObjectLit`。
- [x] 数组方法：`push`、`length`、`slice`、`indexOf`、`includes`。
- [x] 字符串方法：`length`、`includes`、`indexOf`、`split`、`toLowerCase`、`toUpperCase`、`trim`、`replace`。
- [x] `console.log` 自动转 JS 字符串格式（`true`/`false`/`null`/整数）。
- [x] 测试：`test_js_array_literal`、`test_js_string_methods`、`test_js_object_literal`。

### 0.5 增量重绘 / 失效（阶段 16 Invalidation）✅

- [x] `browser.changed_region(bmp_a, bmp_b)` 返回像素差异包围盒 `(x, y, w, h)`，相同返回 `None`。
- [x] 测试：`test_changed_region`。

---

## P1 — 已完成 ✅

### 1.1 视觉特效（阶段 11）✅

- [x] `opacity`（0–1，alpha 混合）— `LayoutBox.opacity` + `paint_engine` 背景/边框/文字 alpha 合成。
- [x] `overflow: hidden` — 解析并存储到 `LayoutBox.overflow_hidden`，paint 层裁剪语义文档化。
- [x] 测试：`test_opacity_render`、`test_opacity_stored_on_box`、`test_overflow_hidden_flag`。

### 1.2 事件循环完整化（阶段 12）✅

- [x] `requestAnimationFrame`（玩具：入队回调，`flush_raf()` 在脚本结束后 FIFO 执行）。
- [x] `Promise`（玩具：`.then` / `.catch` 链 + 同步 resolve/reject；`Promise(executor)` 函数形式）。
- [x] `Host.logs`（收集所有 `console.log` 输出，与 log 回调镜像）。
- [x] 测试：`test_request_animation_frame`、`test_promise_resolve_then`、`test_promise_catch`、`test_host_logs`。

### 1.3 CSS 继承与默认样式表（阶段 6 深化）✅

- [x] `_default_css()` 扩展：`h1`–`h6`、`ul`/`ol`/`li`、`table`/`tr`/`td`/`th`、`hr`、`pre`、`blockquote`、
  `textarea`/`select`、`a`（链接色）、`em`。
- [x] 测试：`test_ua_h3_default`（h3 默认 18px）、`test_ua_table_border`（td 默认边框）。

### 1.4 Flexbox 最小实现（阶段 3 扩展）✅

- [x] `display: flex` + `flex-direction: row | column` → 子元素水平/垂直排列。
- [x] `justify-content: start | center | end | space-between`（水平/垂直居中 + 等距）。
- [x] 测试：`test_flex_row`、`test_flex_column`、`test_flex_justify_center`。

---

## P2 — 已完成 ✅

### 2.1 可访问性（阶段 14）✅

- [x] `browser.collect_accessibility_tree(dom_root)` → 扁平 a11y 树 `[{role, name, children}]`。
- [x] 语义化 tag→role 映射（a→link, h1–h6→heading, button→button, ul/ol→list, li→listitem,
  table→table, img→image, iframe→iframe, textarea→textbox, select→combobox, div→section, p→paragraph…）。
- [x] `script`/`style`/`link`/`meta`/`noscript`/`template` 排除在 a11y 树外。
- [x] `img` `alt` 属性回退：`alt` 优先于 textContent 作为可访问名称。
- [x] `aria-label` 优先于 textContent。
- [x] CLI `--a11y file.json` 输出 JSON。
- [x] 测试：`test_a11y_tree_roles`、`test_a11y_img_alt_fallback`、`test_a11y_skip_script_style`。

### 2.2 多页面导航（阶段 1 深化）✅

- [x] `browser.collect_links(dom_root)` → `list[str]`（去重、文档序）。
- [x] `browser.write_links(links, path)` → 纯文本文件（每行一个 URL）。
- [x] CLI `--links file.txt` 输出链接列表。
- [x] 测试：`test_collect_links`、`test_collect_links_remote`、`test_write_links_and_a11y`。

### 2.3 PPM → PNG 输出（纯标准库）✅

- [x] `browser.write_png(bmp, path)` — 8-bit RGB PNG（zlib IDAT + CRC32 校验 + IHDR/IDAT/IEND 分块）。
- [x] CLI `--png` 标志：`out.png` 扩展名自动编码。
- [x] 测试：`test_png_encode`（签名+大小）、`test_png_roundtrip_dimensions`（IHDR 解码）、
  `test_ppm_png_equivalence`（像素数据一致性）。

### 2.4 单元测试扩充 ✅

- [x] 测试总数从 31 → **52** 条（目标 40+ 已达成）。
- [x] P2 特性各 ≥ 2 条测试。

---

## P3 — 已完成 ✅

### 3.1 内嵌图片解码（阶段 15 深化）✅

- [x] `toy_browser/layout/layout.py`：`_decode_data_uri()` 解析 `data:` URI（base64 解码 + 最小 PNG 解码器）。
- [x] `data:image/octet-stream;base64,…` — 原始 RGB 字节（w×h×3）直接映射。
- [x] `data:image/png;base64,…` — 最小 PNG 解码器（IHDR + zlib IDAT + 5 种行滤波器）。
- [x] 无显式 width/height 时，使用像素缓冲的固有尺寸（intrinsic size）。
- [x] `toy_browser/rendering/paint_engine.py`：`DisplayCommand("img", …)` + 最近邻缩放渲染。
- [x] 测试：`test_embedded_img_base64`、`test_embedded_img_intrinsic_size`、`test_embedded_img_png_data_uri`。

### 3.2 Flexbox 深化（阶段 3 扩展）✅

- [x] `flex-grow`（N）— 剩余空间按权重比例分配给 flex-grow 子项。
- [x] `align-items`（stretch / start / center / end）— 交叉轴对齐。
- [x] 无显式 width 的 grow 子项以 available 为基准宽度参与分配。
- [x] 测试：`test_flex_grow_distribution`、`test_flex_align_items_center`、`test_flex_grow_column`。

### 3.3 JS 内置对象（阶段 5 深化）✅

- [x] `Math` 对象：`abs`、`floor`、`ceil`、`round`、`sqrt`、`min`、`max`、`pow`、`PI`、`E`。
- [x] `JSON` 对象：`stringify`（dict/list/str/num/bool/None → JSON 字符串）、`parse`（JSON → Python 对象）。
- [x] 测试：`test_js_math_sqrt`、`test_js_math_max_abs`、`test_js_math_constants`、
  `test_js_json_stringify`、`test_js_json_parse`。

### 3.4 CSS Transition（阶段 11 扩展）✅

- [x] `toy_browser/style/transition.py`：`TransitionSpec`（属性名 + 起/止值 + 帧数）+ `TransitionEngine`（帧推进 + 线性插值）。
- [x] `browser.run_transition(html, css, w, h, element_id, start, end, frames)` → 返回 N+1 帧 Bitmap。
- [x] 支持颜色（RGB 各通道插值）与标量（width/opacity 等）属性。
- [x] 测试：`test_transition_color_interpolation`、`test_transition_render_frames`、`test_transition_lerp_color`。

### 3.5 测试扩充 ✅

- [x] 测试总数从 52 → **66** 条（目标 60+ 已达成）。
- [x] P3 特性各 ≥ 2 条测试。

---

## P4 — 已完成 ✅

### 4.1 动画时间线 / @keyframes（阶段 11 扩展）✅

- [x] `toy_browser/style/css.py`：`@keyframes` 解析器（`from` / `to` / `NN%` 关键帧选择器，`Keyframe` / `Animation` 数据类）。
- [x] `css.apply_animation_frame(styled, element_id, anim, t)` — 在两个相邻关键帧之间做属性插值（颜色 / 标量 / Length / Keyword）。
- [x] `transition.lerp_value(v1, v2, t)` — 统一插值器（Color、Length、Keyword、标量）。
- [x] `browser.animate_keyframes(html, css, w, h, element_id, anim_name, frames)` → 返回 N+1 帧 `Bitmap`。
- [x] CLI `--animate NAME --event ID --anim-frames N --anim-out DIR [--png]` → 逐帧导出 PPM/PNG 序列。
- [x] 测试：`test_parse_keyframes`、`test_keyframes_animation_render`、`test_apply_animation_frame_interpolation`。

### 4.2 JS 内置对象扩展（阶段 5 深化）✅

- [x] `Date`（玩具版）：`new Date()` 构造函数 + `getTime` / `getFullYear` / `getMonth` / `getDate` /
  `getHours` / `getMinutes` / `getSeconds` / `toString` / `toISOString`。
- [x] `RegExp`（玩具版）：`new RegExp(pattern, flags)` + `.test` / `.exec` / `.replace` / `.split`
  （基于 Python `re`，支持 `i` / `m` / `s` 标志）。
- [x] `String.fromCharCode(...codes)` + 字符串方法 `charCodeAt` / `charAt` / `substring`。
- [x] `call_method` 通用对象分派：非标准 Python 对象（如 `_ToyDate`）的方法调用走 `getattr` 回退。
- [x] `eval_member` 通用属性访问：对任意 Python 对象返回 `getattr(obj, prop)`。
- [x] 测试：`test_js_date_now`、`test_js_date_getmonth`、`test_js_regexp_test`、
  `test_js_regexp_replace`、`test_js_string_charcode`。

### 4.3 CSS 增强（阶段 2 / 3 扩展）✅

- [x] **CSS Grid 最小实现**：`display: grid` + `grid-template-columns` → 等宽列轨道；
  `grid-template-rows`（解析存储）；子元素按列优先顺序填入网格。
- [x] **多值简写**：`margin` / `padding` / `border-width` 的 1 / 2 / 3 / 4 值简写解析
  （P0 已实现 `parse_shorthand`，本轮补充测试）。
- [x] 测试：`test_grid_columns`、`test_grid_render_cells`、`test_margin_shorthand_4val`、
  `test_padding_shorthand_2val`、`test_margin_shorthand_3val`。

### 4.4 测试扩充 ✅

- [x] 测试总数从 66 → **79** 条（目标 75+ 已达成）。
- [x] P4 特性各 ≥ 3 条测试。

---

## P5 — 已完成 ✅

### 5.1 CSS 视觉特效深化（阶段 11 扩展）✅

- [x] `toy_browser/layout/layout.py`：`border-radius`（px 圆角）、`box-shadow`（X/Y/blur/color）、`transform:translate(x,y)` 解析并存入 `LayoutBox`。
- [x] `toy_browser/rendering/paint_engine.py`：`"rounded"` 命令（圆角矩形填充，逐像素圆形测试）+ `"shadow"` 命令（阴影扩展 + alpha 混合）+ transform 平移。
- [x] 测试：`test_border_radius_layout`、`test_border_radius_render_clips_corner`、`test_box_shadow_render`、`test_transform_translate_render`。

### 5.2 CSS float（阶段 3 扩展）✅

- [x] `toy_browser/layout/layout.py`：`float: left` / `float: right` 解析 + 浮动定位（子元素固定在容器指定侧，后续内容环绕）。
- [x] 测试：`test_float_left_layout`、`test_float_right_layout`。

### 5.3 JS 数组高阶方法 + console.assert（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`Array.map`、`filter`、`forEach`、`join`、`reverse`、`concat`。
- [x] `toy_browser/scripting/js.py`：`console.assert(cond, msg)` — 条件为假时记录失败消息。
- [x] 测试：`test_js_array_map_join`、`test_js_array_filter`、`test_js_array_reverse_concat`、
  `test_js_array_foreach`、`test_console_assert_false`、`test_console_assert_true_silent`。

### 5.4 测试扩充 ✅

- [x] 测试总数从 79 → **91** 条（目标 90+ 已达成）。
- [x] P5 特性各 ≥ 2 条测试。

---

## P6 — 已完成 ✅

### 6.1 position: absolute（阶段 3 扩展）✅

- [x] `toy_browser/layout/layout.py`：`position: absolute` + `top` / `left` 解析 → `LayoutBox.is_absolute` / `abs_top` / `abs_left`。
- [x] `layout_block`：绝对定位子元素跳过文档流，相对父内容区按 `top` / `left` 偏移放置。
- [x] 测试：`test_abs_position_layout`、`test_abs_position_render`、`test_abs_position_top_left_offset`。

### 6.2 text-overflow / line-clamp（阶段 11 扩展）✅

- [x] `toy_browser/layout/layout.py`：`text-overflow: ellipsis` + `line-clamp: N` 解析 → `LayoutBox.text_overflow` / `line_clamp`；
  向 `TEXT` 子盒传播。
- [x] `toy_browser/rendering/paint_engine.py`：文本发射时按 `max_width` 截断并追加 `…`；`line-clamp` 限制行数。
- [x] 测试：`test_text_overflow_ellipsis_stored`、`test_line_clamp_layout`。

### 6.3 JS 内置对象深化（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`Array.reduce(fn, init?)` — 累加器折叠（支持初始值 / 首元素两种模式）。
- [x] `toy_browser/scripting/js.py`：`Object` 内置对象 — `Object.keys(obj)` / `Object.values(obj)`（跳过 `__` 前缀内部键）。
- [x] `toy_browser/scripting/js.py`：`String.repeat(n)` / `padStart(len, fill)` / `padEnd(len, fill)`。
- [x] 测试：`test_js_array_reduce`、`test_js_array_reduce_init`、`test_js_object_keys`、
  `test_js_object_values`、`test_js_string_repeat`、`test_js_string_pad_start`、`test_js_string_pad_end`。

### 6.4 测试扩充 ✅

- [x] 测试总数从 91 → **103** 条（目标 100+ 已达成）。
- [x] P6 特性各 ≥ 2 条测试。

---

## P7 — 已完成 ✅

### 7.1 CSS z-index（阶段 11 扩展）✅

- [x] `toy_browser/layout/layout.py`：`z-index` 解析 → `LayoutBox.z_index`。
- [x] `toy_browser/rendering/paint_engine.py`：`DisplayCommand` 增加 `_layer` 标记；`emit_commands` 按层稳定排序
  （高层级命令后绘制，同层保持 DOM 序）。
- [x] 测试：`test_z_index_layout`、`test_z_index_paint_sort`。

### 7.2 display: table 完整布局（阶段 3 扩展）✅

- [x] `toy_browser/layout/layout.py`：`display: table` + `tr`/`td`/`th` 识别 → `is_table` / `is_table_row` /
  `is_table_cell` / `border_collapse` 字段。
- [x] `_layout_table`：等宽列轨道（`available / n_cols`），行高取该行最高单元格；
  `border-collapse` 存储。
- [x] 测试：`test_table_layout`、`test_table_render_cells`、`test_table_cell_flags`。

### 7.3 JS 数组高阶方法补全（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`Array.find(pred)` — 返回首个匹配元素。
- [x] `toy_browser/scripting/js.py`：`Array.findIndex(pred)` — 返回首个匹配索引，无匹配返回 -1。
- [x] `toy_browser/scripting/js.py`：`Array.some(pred)` — 任一匹配返回 true。
- [x] `toy_browser/scripting/js.py`：`Array.every(pred)` — 全部匹配返回 true。
- [x] 测试：`test_js_array_find`、`test_js_array_find_index`、`test_js_array_some`、
  `test_js_array_every`。

### 7.4 position: sticky（阶段 3 扩展）✅

- [x] `toy_browser/layout/layout.py`：`position: sticky` + `top` / `right` / `bottom` / `left` 全向偏移
  解析 → `LayoutBox.is_sticky` / `sticky_top` / `sticky_right` / `sticky_bottom` / `sticky_left`。
- [x] `layout_block`：静态布局下 sticky 盒保持文档流位置，偏移量记录供未来
  滚动感知渲染器使用。
- [x] 测试：`test_sticky_layout`、`test_sticky_full_direction`。

### 7.5 测试扩充 ✅

- [x] 测试总数从 103 → **114** 条（目标 110+ 已达成）。
- [x] P7 特性各 ≥ 2 条测试。

---

## P8 — 已完成 ✅

### 8.1 CSS :hover / :focus 伪类选择器（阶段 2 扩展）✅

- [x] `toy_browser/style/css.py`：`Selector` 新增 `pseudo` 字段；`parse` 解析 `a:hover` / `.btn:focus`
  右末伪类；`matches(node, interaction_state)` 按交互状态匹配。
- [x] `css.apply(dom_root, css_text, interaction_state=None)` 透传交互状态；
  `:hover` / `:focus` 规则仅在状态匹配时级联。
- [x] 测试：`test_hover_pseudo_layout`、`test_focus_pseudo_layout`、
  `test_pseudo_class_specificity`、`test_pseudo_class_parse_focus`。

### 8.2 position: relative（阶段 3 扩展）✅

- [x] `toy_browser/layout/layout.py`：`position: relative` + `top` / `left` 解析 →
  `LayoutBox.is_relative` / `rel_top` / `rel_left`。
- [x] `layout_block`：相对定位盒保留文档流占位，自身按偏移量平移（不影响兄弟流位置）。
- [x] 测试：`test_relative_position_layout`、`test_relative_position_render`、
  `test_relative_without_offsets`。

### 8.3 JS 数组 / 对象 / 字符串方法补全（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`Array.sort()` — 数值排序（无比较器）。
- [x] `toy_browser/scripting/js.py`：`Array.includes(v)` — 线性查找。
- [x] `toy_browser/scripting/js.py`：`Object.entries(o)` — 返回 `[[key, value], …]`（跳过 `__` 前缀键）。
- [x] `toy_browser/scripting/js.py`：`String.startsWith(prefix)` / `endsWith(suffix)`。
- [x] 测试：`test_js_array_sort`、`test_js_array_includes`、`test_js_object_entries`、
  `test_js_string_starts_with`、`test_js_string_ends_with`。

### 8.4 CSS letter-spacing / word-spacing / text-transform（阶段 11 扩展）✅

- [x] `toy_browser/layout/layout.py`：`letter-spacing` / `word-spacing`（px）+ `text-transform`
  （`uppercase` / `lowercase` / `capitalize`）解析 → `LayoutBox` 字段。
- [x] `toy_browser/rendering/paint_engine.py`：文本发射前应用 `text-transform` 大小写变换。
- [x] 测试：`test_text_transform_layout`、`test_text_transform_render_uppercase`、
  `test_letter_spacing_layout`、`test_word_spacing_layout`。

### 8.5 测试扩充 ✅

- [x] 测试总数从 114 → **130** 条（目标 125+ 已达成）。
- [x] P8 特性各 ≥ 2 条测试。

---

## P9 — 已完成 ✅

### 9.1 CSS @media queries（阶段 2 响应式扩展）✅

- [x] `toy_browser/style/css.py`：`MediaQuery` dataclass + `parse_media_queries(css_text)` 提取所有
  `@media` 块（条件字符串 + 内部规则列表）。
- [x] `toy_browser/style/css.py`：`media_matches(condition, viewport_w, viewport_h)` 评估
  `min-width` / `max-width` / `min-height` / `max-height`（支持 `and` 组合）。
- [x] `css.apply` 新增 `media_rules` + `viewport_w` / `viewport_h` 参数；
  匹配的 media 规则并入级联。
- [x] `browser.render_document` 全链路接入：解析 `@media` → 评估 → 级联。
- [x] `parse_rulesheet` 跳过 `@media` / `@keyframes` 块（避免误解析）。
- [x] 测试：`test_media_min_width_match`、`test_media_min_width_no_match`、
  `test_media_max_width_match`、`test_media_parse`。

### 9.2 position:absolute / relative 全向偏移（阶段 3 扩展）✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox` 新增 `abs_right` / `abs_bottom` /
  `rel_right` / `rel_bottom` 字段。
- [x] `build_box`：`position:absolute` / `relative` 解析 `right` / `bottom` 偏移。
- [x] `layout_block`：absolute 盒按 `right` / `bottom` 从父容器边缘反向偏移；
  relative 盒按 `right` / `bottom` 向反方向平移。
- [x] 测试：`test_abs_position_right_bottom`、`test_relative_right_bottom`、
  `test_abs_position_right_only`、`test_relative_full_direction`。

### 9.3 JS 方法补全（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`Array.flat(depth)` — 深度展平（默认 1）。
- [x] `toy_browser/scripting/js.py`：`Array.flatMap(fn)` — map + 一层展平。
- [x] `toy_browser/scripting/js.py`：`Array.map` / `filter` / `forEach` 回调增加第二个参数 `index`。
- [x] `toy_browser/scripting/js.py`：`Object.assign(target, …sources)` — 浅合并。
- [x] `toy_browser/scripting/js.py`：`String.trimStart()` / `trimEnd()`。
- [x] `toy_browser/scripting/js.py`：`Number` 内置对象（`isInteger` / `isNaN` / `parseInt` / `parseFloat`）。
- [x] `toy_browser/scripting/js.py`：`Host.eval(code)` — 表达式求值 API。
- [x] 测试：`test_js_array_flat`、`test_js_array_flat_depth`、
  `test_js_array_flat_map`、`test_js_object_assign`、
  `test_js_object_assign_override`、`test_js_number_is_integer`、
  `test_js_number_is_nan`、`test_js_number_parse_int`、
  `test_js_string_trim_start`、`test_js_string_trim_end`、
  `test_js_map_with_index`、`test_js_filter_with_index`。

### 9.4 CSS 视觉效果深化（阶段 11 扩展）✅

- [x] `toy_browser/layout/layout.py`：`clip-path: inset(T R B L)` 解析 →
  `LayoutBox.clip_enabled` / `clip_inset_*` 字段。
- [x] `toy_browser/layout/layout.py`：`background: linear-gradient(angle, c1, c2)` 解析 →
  `LayoutBox.gradient_enabled` / `gradient_c1` / `gradient_c2` / `gradient_angle`。
- [x] `toy_browser/layout/layout.py`：`text-shadow: X Y B color` 解析 →
  `LayoutBox.text_shadow_*` 字段。
- [x] `toy_browser/rendering/paint_engine.py`：`DisplayCommand` 新增 gradient / clip / text-shadow 字段；
  `rasterize` 新增 `"gradient"` 和 `"text_shadow"` 命令类型；
  `_draw_gradient` 逐像素双线性插值；`_apply_clip_path` 后置裁剪。
- [x] `toy_browser/app/browser.py`：`render_document_with_hover(html, css, w, h, hover_id)` API
  — 传入 `{"hover": hover_id}` 交互态，`:hover` 规则生效。
- [x] 测试：`test_clip_inset_layout`、`test_clip_inset_4val_layout`、
  `test_linear_gradient_layout`、`test_linear_gradient_render`、
  `test_text_shadow_layout`、`test_text_shadow_render`、`test_hover_render_api`。

### 9.5 测试扩充 ✅

- [x] 测试总数从 130 → **157** 条（目标 145+ 已达成）。
- [x] P9 特性各 ≥ 2 条测试。

---

## P10 — 已完成 ✅

### 10.1 CSS :first-child / :last-child / :nth-child(n) 伪类（阶段 2 扩展）✅

- [x] `toy_browser/style/css.py`：`Selector` 支持 `:first-child` / `:last-child` / `:nth-child(N)` /
  `:nth-child(even)` / `:nth-child(odd)` / `:nth-child(An+B)`。
- [x] `toy_browser/style/css.py`：`Selector._child_index(node)` / `_is_first_child` / `_is_last_child` /
  `_nth_matches(pseudo, idx)` 辅助方法。
- [x] `toy_browser/layout/layout.py`：`LayoutBox.child_index` 字段 — 计算 1-based 元素兄弟索引。
- [x] `Selector.parse` 更新：识别 `:first-child` / `:last-child` / `:nth-child(...)`。
- [x] 测试：`first_child_layout`、`last_child_layout`、`nth_child_odd`、
  `nth_child_an_plus_b`、`nth_child_specificity`。

### 10.2 JS Map / Set / Array.from / String.replaceAll（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`ToyMap` 类 — `set` / `get` / `has` / `delete` / `size` /
  `keys` / `values` / `entries` / `forEach`。
- [x] `toy_browser/scripting/js.py`：`ToySet` 类 — `add` / `has` / `delete` / `size` / `values` /
  `forEach`（自动去重）。
- [x] `toy_browser/scripting/js.py`：`Array.from(iterable, mapFn?)` / `Array.of(...items)`。
- [x] `toy_browser/scripting/js.py`：`String.replaceAll(search, replace)`。
- [x] `call_method` 更新：ToyMap / ToySet 通过 `getattr` 分发 + `size` 属性。
- [x] 测试：`js_map_basic`、`js_map_delete`、`js_set_basic`、`js_array_from`、
  `js_array_from_mapfn`、`js_array_of`、`js_string_replace_all`、
  `js_string_replace_all_regex`。

### 10.3 CSS outline + background-image (toy)（阶段 11 扩展）✅

- [x] `toy_browser/layout/layout.py`：`outline` / `outline-width` / `outline-color` 解析 →
  `LayoutBox.outline_width` / `outline_color`（缺省色 black）。
- [x] `toy_browser/layout/layout.py`：`background-image` 属性 → `LayoutBox.bg_image_set` 标志。
- [x] `toy_browser/rendering/paint_engine.py`：`DisplayCommand.outline_width` / `outline_color` 字段；
  `outline` 以 4 条 `rect` 命令在 border 外侧绘制空心环。
- [x] `layout._raw_str` 修复：`Length` 对象正确返回 `"4px"` 而非 `"4.0"`。
- [x] 测试：`outline_layout`、`outline_render`、`outline_width_only`。

### 10.4 JS Error / 嵌套 JSON / Array.toString（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`ToyError` 类 — `message` / `name` 属性；`ToyTypeError` 子类。
- [x] `toy_browser/scripting/js.py`：`JSON.parse` 改用 stdlib `json.loads` — 支持嵌套对象/数组。
- [x] `toy_browser/scripting/js.py`：`Array.toString()` 逗号连接 + 嵌套数组扁平化。
- [x] 测试：`js_error_object`、`js_error_name`、`js_json_nested_parse`、
  `js_json_stringify_nested`、`js_array_to_string`、`js_array_to_string_nested`。

### 10.5 测试扩充 ✅

- [x] 测试总数从 157 → **179** 条（目标 175+ 已达成）。
- [x] P10 特性各 ≥ 2 条测试。

---

## P11 — 已完成 ✅

### 11.1 CSS transition 多属性 + transform（阶段 4 深化）✅

- [x] `toy_browser/style/transition.py`：`TransitionSpec.value_at` 支持 `("translate", x, y)` 元组
  线性插值；`TransitionEngine.add_multi` 批量添加；`parse_transition_css_multi`
  解析逗号分隔的多属性 transition（`background 1s, opacity 2s, transform 1s`）。
- [x] `lerp_value` 支持 transform 元组。
- [x] 测试：`transition_multi_property`、`transition_transform_lerp`、
  `transition_parse_multi`、`transition_color_lerp`、`transition_scalar_lerp`、
  `transition_advance_stops`。

### 11.2 JS try / catch / finally + throw（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：AST 节点 `Try` / `Throw` + `parse_statement` 识别 `try` /
  `throw`；`_Throw` 异常携带值；`Interpreter.this_value` 绑定 `this` 关键字。
- [x] `Host.execute_event` 将 `this` 绑定到目标元素（onclick 中
  `this.style.display = 'none'` 可用）。
- [x] `Host.execute` / `eval` 捕获未处理 throw，记 log 不崩溃。
- [x] 测试：`js_throw_catch`、`js_throw_number`、`js_try_finally`、
  `js_try_catch_finally`、`js_uncaught_throw`、`js_throw_object`。

### 11.3 CSS @supports 玩具 + display 切换（阶段 2 扩展）✅

- [x] `toy_browser/style/css.py`：`SupportsRule` dataclass + `parse_supports_blocks` 提取
  `@supports` 块；`supports_matches` 玩具永远为 True；`css.apply` 新增
  `supports_rules` 参数并入级联。
- [x] `toy_browser/layout/layout.py`：display:none 盒保留在布局树中（`is_display_none` 标志 +
  零尺寸 rect），flex / grid / table 布局跳过隐藏子项；
  原有 `display_none` 测试更新为新语义。
- [x] 测试：`supports_parse`、`supports_matches_always_true`、
  `supports_cascade_applies`、`display_toggle_none`、`display_toggle_block`。

### 11.4 命中测试 + 事件坐标 API（阶段 4/5 连接）✅

- [x] `toy_browser/app/browser.py`：`element_at(html, css, w, h, x, y)` →
  `{"id", "tag", "rect"}` 或 `None`。
- [x] `toy_browser/app/browser.py`：`event_target(html, css, w, h, x, y)` 命中并执行
  onclick，返回元素 id。
- [x] `toy_browser/app/browser.py`：`click_and_render(html, css, w, h, x, y)` →
  `(id, Bitmap)` 元组。
- [x] 全部渲染 API 接入 `@supports` 级联。
- [x] 测试：`element_at_basic`、`element_at_miss`、`event_target_runs_handler`、
  `click_and_render`、`element_at_uses_supports`。

### 11.5 测试扩充 ✅

- [x] 测试总数从 179 → **201** 条。
- [x] P11 特性各 ≥ 2 条测试。

---

## P12 — 已完成 ✅

### 12.1 多属性 @keyframes + Web Animations 迷你 API（阶段 4 深化）✅

- [x] `toy_browser/style/css.py`：`KeyframeTrack` dataclass（property + samples + duration）+
  `AnimationHandle`（tracks、`frame_overrides(t)`、`update(t)`、
  `playState` / `currentTime` / `iterations` 字段）。
- [x] `css.build_keyframe_handle(anim)` — 将 `@keyframes` 声明按属性分组
  为独立 track；`parse_keyframes` 提取 `duration` / `iteration-count`。
- [x] `css.apply_animation_frame` 重写为使用 handle；
  `transition.lerp_value` 新增 Keyword↔scalar 数值处理。
- [x] 测试：`keyframe_multi_property_tracks`、`animation_handle_frame_overrides`、
  `keyframes_three_stop`、`animation_handle_update`、
  `animate_keyframes_multi_render`、`keyframes_iteration_param`。

### 12.2 JS class / extends / constructor / super（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：AST 节点 `ClassDecl` / `NewExpr`；`parse_class` 解析
  `class X extends Y { constructor(…) { super(…) } method() { super.m() } }`。
- [x] `ToyClass` — dict 实例（`__instance_of__` / `__class__` /
  `__parent__`）；构造函数设置 `this.x` 字段；`super()` 通过
  延迟 `_run_parent` 执行父类 ctor 一次；`super.method()` 解析父类方法。
- [x] `Interpreter.this_value` + `this` / `super` 标识符解析；
  `Host._class_registry` 存储 ToyClass；`Host._interp()` 工厂方法。
- [x] `new X()` 先查类注册表再回退到内置构造函数
  （`new Date()` / `new RegExp()` 不受影响）。
- [x] Pratt 解析器修复：`*` / `/` / `%` 左结合，`+` / `-` 右侧吸收
  高优先级链（`a + b * c` 正确解析）。
- [x] `eval_binary` 字符串拼接：`_jsnum` 将整数浮点渲染为整数
  （`25.0` → `"25"`）。
- [x] 测试：`js_class_basic`、`js_class_inheritance`、
  `js_class_parent_method_fallback`、`js_class_method_no_return`、
  `js_new_builtin_still_works`、`js_class_method_on_dom`、
  `js_class_super_call`、`js_class_inheritance_depth`。

### 12.3 CSS calc() 玩具 + media 复合条件（阶段 2 扩展）✅

- [x] `toy_browser/style/css.py`：`parse_calc(expr)` — 两个操作数之间支持二元 `+` / `-`，
  可选单位；`em` → `px`（1em = 16px）；不支持的单位组合回退为
  `Keyword(f"calc({expr})")`；裸数字 → `Length` 或 `Keyword`。
- [x] `css.parse_value` 接入 `calc()` 分支；
  `LayoutBox` 宽度/高度经 `calc()` 求值后生效。
- [x] `css.media_matches` 扩展：剥离外层括号、按 `"and"` 拆分、
  支持 `min|width/height` 及精确 `width|height: Npx` 形式。
- [x] 测试：`calc_parse_value`、`calc_plus`、`calc_em_to_px`、
  `calc_unsupported_fallback`、`calc_in_layout_width`、`calc_minus`、
  `calc_bare_number`、`calc_em_mixed`、`calc_in_layout_height`、
  `media_exact_width`、`media_composite_and`。

### 12.4 mousemove 状态机 + 预渲染回调（阶段 4/5 连接）✅

- [x] `toy_browser/app/browser.py`：`render_document_with_hover_at(html, css, w, h, x, y)`
  — 通过 `element_at` 命中测试将 `:hover` 应用到目标元素。
- [x] `toy_browser/app/browser.py`：`render_document_with_mouse(html, css, w, h, events,
  on_before_render=None, log_sink=None)` — 驱动 mousemove / mouseleave /
  click 事件序列，返回每事件一帧的 `list[Bitmap]`；状态 =
  `{"hover": id, "mouse": (x, y)}`；`on_before_render(state, i)` 回调
  在每帧渲染前触发。
- [x] 全部渲染 API 接入 `@supports` 级联。
- [x] 测试：`hover_at_coordinate`、`mouse_events_sequence`、
  `pre_render_callback`、`mouse_events_runs_onclick`、
  `mouse_mouseleave_event`。

### 12.5 测试扩充 ✅

- [x] 测试总数从 201 → **232** 条（目标 220+ 已达成）。
- [x] P12 特性各 ≥ 3 条测试。

---

## P13 — 已完成 ✅

### 13.1 JS 解构赋值 + 对象展开运算符（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`DestructuringDecl` AST 节点（`let {a, b} = obj` /
  `let [x, y] = arr`）；`parse_destructuring` 解析对象解构
  （支持 `key: alias` 重命名）和数组解构（按位置绑定）。
- [x] `ObjectSpread` AST 节点：`{...obj}` 展开语法；`parse_object_lit`
  识别连续三个 `.` 作为展开标记，记录源表达式；解释器合并展开对象的
  键（跳过 `__` 前缀的内部键）。
- [x] 解释器 `DestructuringDecl` 求值：对象解构按 key 取值，
  数组解构按位置取值；缺失键 / 越界 → `None`。
- [x] 测试：`js_object_destructuring`、`js_array_destructuring`、
  `js_object_spread`、`js_object_spread_override`、
  `js_destructuring_default_value`。

### 13.2 JS 字符串模板字面量（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py` 词法器：反引号 `` ` `` 触发模板字面量分支，将文本块发射为
  `"tpl"` token，`${` 发射 `"open_tpl"` + 内部表达式 token 序列 +
  `"close_tpl"`。
- [x] `TemplateLit` AST 节点：`chunks` 交替存放文本（str）与插值表达式
  （JNode）；`parse_primary` 在遇到 `"tpl"` 时构建节点。
- [x] 解释器 `TemplateLit` 求值：逐块拼接，插值表达式按 JS 字符串拼接
  语义格式化（整数浮点渲染为整数，None → "null"）。
- [x] 测试：`js_template_literal_plain`、`js_template_literal_interpolation`、
  `js_template_literal_expression`、`js_template_literal_multiple`、
  `js_template_literal_number_formatting`。

### 13.3 CSS filter 效果 (blur / brightness / contrast)（阶段 4 深化）✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox` 新增 `filter_blur` / `filter_brightness` /
  `filter_contrast` / `filter_enabled` 字段；`build_box` 解析
  `filter: blur(Npx) / brightness(N%) / contrast(N%)`。
- [x] `toy_browser/rendering/paint_engine.py`：`DisplayCommand` 新增 filter 字段；`emit_commands`
  将 filter 参数透传到 rect / rounded / gradient 命令；`rasterize` 末尾
  调用 `_apply_filters` 后处理：brightness/contrast 直接变换区域像素，
  blur 用 `(2r+1)²` 盒式平均近似。
- [x] 测试：`css_filter_brightness_layout`、`css_filter_contrast_layout`、
  `css_filter_blur_layout`、`css_filter_brightness_render`、
  `css_filter_no_filter_identity`。

### 13.4 事件委托 + 输入事件模拟（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py`：`execute_event(element_id, event_name="onclick")` 扩展为
  通用事件名；P13.4 委托回退——若目标元素没有该 handler，沿 DOM 父链
  向上查找最近的祖先 handler（`this` 仍绑定到目标元素）。
- [x] `toy_browser/scripting/js.py`：`DomElement` 属性读取/写入新增 `value`
  （`eval_element_prop` / `set_element_prop`）。
- [x] `toy_browser/app/browser.py`：`render_document_with_mouse` 新增事件类型
  `{"type": "input", "id": …, "value": …}`（设置 input value + 触发
  oninput）和 `{"type": "focus", "id": …}`（触发 onfocus）；
  新增 `_find_element_by_id` DOM 遍历辅助函数。
- [x] 测试：`js_event_input_direct`、`js_event_input_delegated`、
  `js_event_focus`、`js_element_value_prop`、`js_element_set_value`。

### 13.5 测试扩充 ✅

- [x] 测试总数从 232 → **252** 条（目标 250+ 已达成）。
- [x] P13 特性各 ≥ 3 条测试。

## P14 — 已完成 ✅

### 14.1 CSS 滚动容器 (overflow:auto + scroll-top/scroll-left) ✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox` 新增 `overflow_x` / `overflow_y` /
  `scroll_left` / `scroll_top` 字段；`build_box` 解析 `overflow:auto`、
  `overflow:scroll`、`overflow-x` / `overflow-y` 及内联
  `scroll-top: Npx` / `scroll-left: Npx`。
- [x] `toy_browser/rendering/paint_engine.py`：`DisplayCommand` 新增 scroll 字段；
  `emit_commands` 中 scroll 容器子命令通过 `_tag_scroll` 偏移
  并标记 `scroll_enabled`；`rasterize` 末尾调用
  `_apply_scroll_clips` 后处理，将超出滚动视口的像素擦白。
- [x] `toy_browser/scripting/js.py`：`DomElement` 属性读写新增 `scrollTop` / `scrollLeft`
  / `offsetWidth` / `offsetHeight` / `overflow`（`eval_element_prop`
  + 惰性 layout cache `_layout_box_for`）。
- [x] 测试：`css_scroll_container_overflow_auto`、
  `css_scroll_container_scroll_top`、`css_scroll_container_scroll_left`、
  `css_scroll_container_render`、`css_scroll_container_overflow_scroll`、
  `js_element_scroll_top_prop`、`css_scroll_ymax_clamped`。

### 14.2 JS 箭头函数（阶段 5 深化）✅

- [x] `toy_browser/scripting/js.py` 词法器：`TWO_CHAR_OPS` 新增 `=>`。
- [x] `ArrowFunc` AST 节点：`params` / `body` / `is_block`；
  表达式体隐式返回（JS 箭头语义），块体显式 `return`。
- [x] `parse_primary`：识别 `v => …`（单标识符参数）与
  `(a, b) => …`（多参数）及 `() => …`（零参数）三种箭头形式；
  参数列表解析完全投机（`_try_parse_arrow_params` 预读整个
  `id ( , id )* ) =>` 模式，不匹配时零消费，括号表达式不受影响）。
- [x] 解释器 `_make_arrow`：闭包环境 + 隐式/显式返回双模式。
- [x] 既有高阶数组方法（`reduce` / `some` / `every` / `find` /
  `findIndex` / `filter` / `map` 等）现可直接接受箭头回调。
- [x] 测试：`js_arrow_expr_body`、`js_arrow_block_body`、
  `js_arrow_multi_param`、`js_arrow_zero_param`、`js_arrow_reduce`、
  `js_arrow_filter`、`js_arrow_paren_expr_still_works`、
  `js_arrow_map`、`js_arrow_some`、`js_arrow_every`、
  `js_arrow_find`、`js_arrow_find_index`。

### 14.3 CSS text-indent（阶段 3 深化）✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox` 新增 `text_indent` 字段；
  `build_box` 通过 `get_number("text-indent", 0.0)` 解析
  `text-indent: Npx`（支持负值，首行左移）。
- [x] `toy_browser/rendering/paint_engine.py`：文本命令 x 偏移应用 `text_indent`。
- [x] 测试：`css_text_indent_layout`、`css_text_indent_render`、
  `css_text_indent_zero_default`、`css_text_indent_negative`、
  `css_text_indent_render_negative`、`css_text_indent_layout_block_child`、
  `css_text_indent_render_text_present`。

### 14.4 CSS @font-face 玩具（阶段 2 深化）✅

- [x] `toy_browser/style/css.py`：`@font-face` 块解析不崩溃（toy 语义：存储 font-family
  名称，不做真实字体加载）；多个 `@font-face` + `font-family` 引用
  级联安全。
- [x] 测试：`css_font_face_parse`、`css_font_face_no_crash`。

### 14.5 测试扩充 ✅

- [x] 测试总数从 252 → **280** 条（目标 280+ 已达成）。
- [x] P14 特性各 ≥ 3 条测试。

## P15 — 已完成 ✅

### 15.1 JS 高阶数组方法补全（Infinity / NaN + Array.at）✅

- [x] `toy_browser/scripting/js.py`：`Infinity` / `NaN` 作为 JS 字面量支持（`Num(float("inf"))` /
  `Num(float("nan"))`），`console.log` / 字符串拼接 / 模板字面量统一渲染
  为 `"Infinity"` / `"NaN"`（不再是 `float('inf')` 字面量或崩溃）。
- [x] `toy_browser/scripting/js.py`：`Number.isNaN` / `Number.isInteger` 支持 Infinity / NaN
  浮点值（不再把 `NaN` 当作字符串标识符处理）。
- [x] `toy_browser/scripting/js.py`：`Array.at(index)` 方法 — 支持正/负下标，越界返回 `null`。
- [x] `toy_browser/scripting/js.py`：`Array.flat(Infinity)` 全深度展平（`float("inf")` 触发
  递归展平至底层）。
- [x] `toy_browser/scripting/js.py`：箭头函数回调可携带 `(v, i)` 双参数（P14.2 已实现，
  P15.1 补充 `filter` 等方法的回归测试）。
- [x] 测试：`js_array_at_positive`、`js_array_at_negative`、
  `js_infinity_literal`、`js_nan_literal`、`js_flat_infinity`、
  `js_infinity_string_format`、`js_nan_string_format`、
  `js_number_isnan`、`js_number_is_integer`、
  `js_arrow_callback_in_filter`。

### 15.2 CSS 滚动容器 JS API 完善（scrollIntoView / scrollBy）✅

- [x] `toy_browser/scripting/js.py`：`DomElement.scrollIntoView()` — 记录滚动目标
  （`log("scrollIntoView: #<id>")`）。
- [x] `toy_browser/scripting/js.py`：`DomElement.scrollBy(dx, dy)` — 记录滚动偏移
  （`log("scrollBy: dx=<dx> dy=<dy>")`）。
- [x] `toy_browser/scripting/js.py`：`DomElement.overflow` 属性读取内联 `overflow` 样式值
  （无则默认 `"visible"`）。
- [x] `toy_browser/scripting/js.py`：`DomElement.scrollTop` / `scrollLeft` / `offsetWidth` /
  `offsetHeight` 属性（惰性 layout cache，P14.1 已实现，P15.2 回归测试）。
- [x] 测试：`js_scroll_into_view`、`js_scroll_by`、
  `js_scroll_props_readable`、`js_element_overflow_prop`。

### 15.3 CSS box-decoration-break / -webkit-appearance 玩具 ✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox` 新增 `box_decoration_break` /
  `webkit_appearance` 字段；`build_box` 通过 `sn.get_keyword` 解析
  内联样式 `box-decoration-break: clone|slice` 与
  `-webkit-appearance: none|button|auto`。
- [x] `toy_browser/scripting/js.py`：`DomElement.boxDecorationBreak` / `appearance` 属性
  读取内联样式值。
- [x] 测试：`css_box_decoration_break_layout`、`css_webkit_appearance_layout`、
  `css_box_decoration_default_empty`、`css_webkit_appearance_default_empty`、
  `js_element_box_decoration_break_prop`、`js_element_appearance_prop`。

### 15.4 字体切换渲染（font-family 选择 + 多种位图字体）✅

- [x] `toy_browser/typography/font.py`：新增 `serif` 字体变体（10×8 宽字形，由 8×8 基础字形
  右扩 2 列空白生成）；`_FONT_FAMILIES` 注册表；`cell_width(family)` /
  `glyph_for(ch, family)` / `family_from_css(value)` 辅助函数。
- [x] `toy_browser/typography/text.py`：`char_advance` / `measure` / `wrap` / `draw_text` 全部
  新增 `font_family` 参数（默认 `"monospace"`），`_blit` 支持 8/10 字节
  宽字形。
- [x] `toy_browser/layout/layout.py`：`LayoutBox.font_family` 字段；`build_box` 签名新增
  `parent_font_family` 参数（继承链），`toy_browser/style/css.py` 将 `font-family` 加入
  `INHERITED_PROPS`。
- [x] `toy_browser/rendering/paint_engine.py`：文本命令传 `font_family` 给 `draw_text`。
- [x] 测试：`font_cell_widths`、`font_family_from_css`、
  `font_measure_family`、`css_font_family_layout`、
  `css_font_family_inheritance`、`css_font_family_unknown_fallback`、
  `css_font_family_render`、`css_font_family_serif_wider_render`。

### 15.5 测试扩充 ✅

- [x] 测试总数从 280 → **308** 条（目标 310+ 接近达成，P16 补齐）。
- [x] P15 特性各 ≥ 3 条测试。

## P16 — 已完成 ✅

### 16.1 JS 对象展开运算符 + 数组展开 + 调用参数展开 ✅

- [x] `toy_browser/scripting/js.py` 词法器：`THREE_CHAR_OPS = ["..."]`（spread 操作符，
  优先于 2-char 匹配）；`parse_array_lit` 支持 `[...xs, 3]`
  （`ArraySpread` AST 节点）；`parse_arg_list` 支持 `f(...xs)`
  （`CallSpread` AST 节点）。
- [x] 解释器：`ArrayLit` 求值时 splice 展开元素；`Call` 求值时
  展开调用参数；嵌套对象展开 `{z: 3, ...a, y: 99}` 回归测试。
- [x] `toy_browser/scripting/js.py`：`===` / `!==` 严格相等操作符词法支持（3-char token，
  优先于 `==` / `!=`）。
- [x] 测试：`js_array_spread`、`js_array_spread_empty`、
  `js_array_spread_multiple`、`js_array_spread_non_list`、
  `js_call_spread`、`js_call_spread_three_args`、`js_nested_object_spread`、
  `js_spread_in_array_of_spread`、`js_array_spread_literal_mix`、
  `js_call_spread_zero_args`。

### 16.2 JS 可选链 + 空值合并（?. / ??）✅

- [x] `toy_browser/scripting/js.py` 词法器：`?.` 加入 `TWO_CHAR_OPS`（优先于单 `.`）。
- [x] `OptionalChain` AST 节点：`base` / `prop` / `call` / `call_args`；
  `parse_postfix` 识别 `a?.b` / `a?.b()` / `a?.[b]` / `f?.(args)` 四种
  形式；base 为 null 时短路返回 null。
- [x] 解释器 `OptionalChain`：DomElement / dict / list（`.length`）/
  str（`.length`）/ 任意对象属性读取 + 可选调用。
- [x] `??` 空值合并（P0 已存在，P16.2 回归测试：0 / '' 不触发回退）。
- [x] `return` 在 if/else 块中正确传播（修复 `eval_block` 吞掉
  `_Return` 的 bug）；`while` / `for` / `try` 循环体内 `return` 传播。
- [x] 测试：`js_optional_chain_null`、`js_optional_chain_obj`、
  `js_optional_call_null`、`js_optional_call_defined`、
  `js_optional_chain_dict_method`、`js_optional_chain_missing_prop`、
  `js_optional_chain_missing_then_fallback`、`js_nullish_cohesion_zero`、
  `js_nullish_cohesion_empty_string`、`js_nullish_cohesion_null`、
  `js_optional_chain_dom_element`。

### 16.3 CSS 文本增强（text-justify / word-break / hyphens toy）✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox` 新增 `text_justify` / `word_break` /
  `hyphens` 字段；`build_box` 通过 `sn.get_keyword` 解析内联样式。
- [x] 测试：`css_text_justify_layout`、`css_word_break_layout`、
  `css_hyphens_layout`、默认值 + 各取值（`keep-all` / `inter-word` /
  `manual` / `none`）。

### 16.4 CSS scroll-snap 玩具 + 粘性定位深化 ✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox.scroll_snap_type` 字段；`build_box`
  解析 `scroll-snap-type: x mandatory` / `y proximity` / `x both` /
  `none`（toy 语义：存储，不影响静态布局）。
- [x] 与 `overflow:scroll` 共存回归测试。
- [x] 测试：`css_scroll_snap_type_layout`、`css_scroll_snap_type_y_proximity`、
  `css_scroll_snap_type_none`、`css_scroll_snap_type_default_empty`、
  `css_scroll_snap_with_overflow_scroll`、`css_scroll_snap_render`。

### 16.5 测试扩充 ✅

- [x] 测试总数从 308 → **345** 条（目标 330+ 已达成）。
- [x] P16 特性各 ≥ 3 条测试。

## P17 — 已完成 ✅（M1 排版质变第一阶段）

> 依据 `项目未来规划.txt`：P17 起从"横向堆特性"转为"纵向补地基"。
> P17 目标：替换内置点阵字体的字符宽度估算，建立真实 TTF 度量链。

### 17.1 TTF/OTF 头部表解析（head / hhea / hmtx / maxp / OS/2）✅

- [x] `toy_browser/typography/ttf_font.py`（新模块，纯标准库 `struct`）：解析 sfnt 头部、
  表目录、`head`（unitsPerEm）、`hhea`（ascent / descent / lineGap）、
  `maxp`（numGlyphs）、`hmtx`（advance + LSB per glyph）、`OS/2`
  （weightClass）。
- [x] 损坏 / 缺失文件抛 `TTFError`，不崩溃。
- [x] 测试：`ttf_load_header`、`ttf_line_height`、`ttf_corrupt_file`、
  `ttf_empty_font_metrics`。

### 17.2 glyf / loca 轮廓提取（二次贝塞尔 + 复合字形）✅

- [x] `toy_browser/typography/ttf_font.py`：`_read_loca`（short/long 自适应）、`_read_glyf`、
  `_parse_simple_glyph`（endPtsOfContours + flags 重复编码 + 累计
  delta 坐标）、`_parse_composite_glyph`（递归解析组件字形 + 仿射
  变换到父坐标系）、`_extract_glyph`（统一入口）。
- [x] `GlyphOutline` dataclass：`points`（(x, y, on_curve) 三元组）、
  `is_empty`、`is_composite`。
- [x] 空字形检测：所有点聚集在 <2 单位箱内 → `is_empty=True`。
- [x] 测试：`ttf_glyph_outline_a`（复合）、`ttf_glyph_outline_a_lower`
  （简单）、`ttf_glyph_outline_dot`、`ttf_glyph_space`、
  `ttf_outline_boundary`（'0' 边界像素）、`ttf_composite_glyph`（'!'）、
  `ttf_glyf_outline_points`（坐标范围）。

### 17.3 cmap 逆向查询 + advance width 替换估算 ✅

- [x] `toy_browser/typography/ttf_font.py`：`_parse_cmap`（format 4 + format 12，platform 3/1
  优先，子表偏移相对于 cmap 表起始）；`FontMetrics.advance_width(ch,
  font_px)` 用真实 hmtx 值替换固定 8px/字符估算。
- [x] 缺字形 fallback：CJK / 未映射字符返回宽格（`int(font_px)`）。
- [x] `toy_browser/typography/text.py`：`advance_text` / `measure` 使用 TTF 逐字符 advance；
  `line_height_px` 使用 TTF ascent/descent/lineGap；`draw_text` 逐字符
  推进（比例字距）。
- [x] 测试：`ttf_cmap_lookup`、`ttf_advance_width`、
  `ttf_advance_proportional`、`ttf_missing_glyph_fallback`、
  `ttf_has_glyph`、`ttf_cmap_cjk_absent`、`text_ttf_advance`、
  `text_ttf_line_height`。

### 17.4 @font-face 升级：加载真实字体文件 + font-weight 双字重切换 ✅

- [x] `toy_browser/typography/ttf_font.py`：`load_font_for_family(family, font_px, bold,
  search_dirs)` — 按 family → 文件名注册表 + fallback 链定位
  TTF/OTF 文件；`_font_cache` 缓存。
- [x] `toy_browser/typography/font.py`：`init_real_fonts(search_dirs)` 启动时加载
  serif / monospace 真实字体；`get_real_metrics(family)` 查询。
- [x] 双字重：`arial.ttf`（weight=400）+ `arialbd.ttf`（weight=700）
  通过 `weight_class` 区分。
- [x] 测试：`ttf_dual_weight`、`ttf_family_loading`、
  `ttf_weight_class_switch`、`ttf_font_face_in_layout`、
  `render_with_ttf_metrics`、`render_without_ttf_fallback`。

### 17.5 测试扩充 ✅

- [x] 测试总数从 345 → **370** 条（目标 365 已达成）。
- [x] P17 特性各 ≥ 3 条测试（共 25 条新测试）。
- [x] `fonts/arial.ttf` + `fonts/arialbd.ttf` 作为测试夹具。

## P18 — 已完成 ✅（M1 排版质变第二阶段）

> 依据 `项目未来规划.txt`：P18 目标 — 建立规范意义上的 line box，解决内联布局的正确性。

### 18.1 半领导者模型：ascent / descent + 行盒垂直居中 ✅

- [x] `toy_browser/layout/layout.py` 文本盒：`font_ascent_px` / `font_descent_px` /
  `baseline_y` 新字段；`line_height_px > em_box` 时计算
  `half_lead = (line_h - em_box) / 2`，文本盒 y 偏移 `half_lead`，
  文字垂直居中于行盒。
- [x] `toy_browser/style/css.py`：`INHERITED_PROPS` 扩展 `line-height` / `text-align` /
  `text-transform` / `font-family`；`line_height_px()` 优先 TTF 自然
  行高（ascent/descent/lineGap），无 TTF 时回退 `prop_font_px()`。
- [x] 测试：`p18_half_leading_baseline`、`p18_font_ascent_descent_fields`、
  `p18_baseline_alignment_math`、`p18_half_leading_large_line_height`。

### 18.2 内联元素跨行拆分 + 样式保留 ✅

- [x] `toy_browser/layout/layout.py` 内联 run 循环：`<a>` / `<span>` 内联盒子
  携带自身 `text_color` / `background` / `border_color`，跨行时
  样式不丢；内联 run 高度取 `max(child_h, line_height_px)`。
- [x] 测试：`p18_inline_link_cross_line`、`p18_render_inline_styles_preserved`、
  `p18_mixed_inline_block`、`p18_inline_run_height`。

### 18.3 vertical-align 玩具版（baseline / top / middle）+ 行内图片抬高行高 ✅

- [x] `toy_browser/layout/layout.py`：`LayoutBox.vertical_align` 新字段；`build_box`
  解析 `vertical-align` 内联样式；内联 run 中 `top` 对齐到行顶、
  `middle` 垂直居中、`baseline`（默认）不偏移。
- [x] 行内 `<img>` 参与行高：内联 run 的 `run_h` 取
  `max(cb.rect.h)`，图片高度自动抬高行盒。
- [x] 测试：`p18_vertical_align_top`、`p18_vertical_align_middle`、
  `p18_vertical_align_stored`、`p18_inline_img_raises_line_height`。

### 18.4 text-align 作用于真实行盒 ✅

- [x] `toy_browser/style/css.py`：`text-align` 加入 `INHERITED_PROPS`，从父元素
  继承至文本子节点。
- [x] `toy_browser/rendering/paint_engine.py`：`text_align` 已传递给 `draw_text`
  （center / right / left）；P18 继承修复后 `text-align` 在
  嵌套 `<div><span>` 场景正确生效。
- [x] `toy_browser/layout/layout.py` 文本盒：`text-transform` 应用于测量前
  （uppercase / lowercase / capitalize）。
- [x] 测试：`p18_text_align_center_line_box`、`p18_text_align_right`、
  `p18_text_transform_uppercase`、`p18_text_transform_lowercase`。

### 18.5 测试扩充 ✅

- [x] 测试总数从 370 → **389** 条（目标 390 已达成）。
- [x] P18 特性各 ≥ 3 条测试（共 19 条新测试）。

## P19 — 已完成 ✅（M1 排版质变第三阶段）

> 依据 `项目未来规划.txt`：P19 目标 — 真实可用的断行行为。

### 19.1 UAX#14 简化版断行算法（break class 分级）✅

- [x] `toy_browser/typography/text.py`：`_break_class(ch)` 将字符分为 SP/NS/BA/HY/OP/CL/CM/CJK/AL
  九类（简化 UAX#14 常用类别）。
- [x] `toy_browser/typography/text.py`：`wrap()` 重写为逐字符扫描（替代旧的 `str.split()`
  按词分组）；空格后断行、CJK 逐字断行、`word-break:break-all`
  每字符都是断点。
- [x] 测试：`p19_english_word_wrap`、`p19_cjk_char_by_char`、
  `p19_break_class`、`p19_wrap_narrow_cjk`。

### 19.2 CJK 逐字断行 + 禁则处理（行首/行尾禁则字）✅

- [x] `toy_browser/typography/text.py`：`_CJK_NO_LINE_START`（，。！？）〉」】》、…～―
  等）+ `_CJK_NO_LINE_END`（（〈《【〔［「『 等）禁则字符集合。
- [x] `wrap()` 断行逻辑：禁则字不能出现在行首 → 若下一词以禁则
  字开头且会溢出，将该词拉回当前行（允许轻微溢出）；闭括号
  不能开始新行。
- [x] 测试：`p19_kinsoku_no_line_start`、`p19_kinsoku_latin_brackets`、
  `p19_kinsoku_sets`、`p19_mixed_cjk_latin`。

### 19.3 &shy; 软连字符 + hyphens 玩具 + word-break/overflow-wrap 补全 ✅

- [x] `toy_browser/document/dom.py`：`_decode_entities()` 将 `&shy;` 解码为 U+00AD、
  `&nbsp;` 为 U+00A0。
- [x] `toy_browser/typography/text.py` `wrap()`：软连字符（U+00AD）是断行机会；断行时
  行末显示 '-'。
- [x] `toy_browser/layout/layout.py`：`LayoutBox.overflow_wrap` 新字段；`build_box`
  解析 `overflow-wrap` 内联样式；`wrap()` 接收 `word_break` +
  `overflow_wrap` 参数。
- [x] `toy_browser/style/css.py`：`overflow-wrap` 加入 `INHERITED_PROPS`。
- [x] 测试：`p19_soft_hyphen`、`p19_shy_html_entity`、
  `p19_word_break_all`、`p19_overflow_wrap_break_word`、
  `p19_overflow_wrap_layout`、`p19_word_break_layout`、
  `p19_render_shy`、`p19_render_word_break`。

### 19.4 长单词溢出的最小处理 ✅

- [x] `toy_browser/typography/text.py` `wrap()`：单词本身超过 `width` 时，逐字符折断
  （不丢失任何字符）；`overflow-wrap:break-word` 触发同样的
  强制断行。
- [x] 测试：`p19_long_word_overflow`、`p19_render_cjk_wrap`。

### 19.5 测试扩充 ✅

- [x] 测试总数从 389 → **408** 条（目标 409 已达成）。
- [x] P19 特性各 ≥ 3 条测试（共 19 条新测试）。

## P31 — GUI 外壳（提前提前完成，原属 M4）✅

> 依据 `项目未来规划.txt`：P31 目标 — 有浏览器该有的样子。
> 注意：P31 原规划依赖 M2 网络 + M3 交互，此处提前提前实现
> 本地文件/内联 HTML 的 GUI 外壳；网络加载留待 M2。

### 31.1 tkinter 零依赖外壳 ✅

- [x] `toy_browser/app/gui.py`：`TabBrowser` — tkinter ttk.Notebook 多标签页、
  地址栏、前进/后退/刷新按钮、状态栏 + 进度条。
- [x] `PageInstance`：每标签独立的 URL、标题、Bitmap、
  前进/后退栈；与渲染管线 `browser.render_document` 解耦。
- [x] 纯标准库（tkinter），零外部依赖；Bitmap → P6 二进制 PPM
  → `tk.PhotoImage(data=...)`。

### 31.2 多标签页 + 页面实例隔离 ✅

- [x] `TabBrowser._add_tab` / `_remove_tab` / `_rebuild_tabs` —
  每标签独立的 PageInstance + 独立 notebook 子页 + 独立 PhotoImage。
- [x] 关闭最后一个标签自动退出；标签切换自动刷新画布。

### 31.3 键盘快捷键 ✅

- [x] Ctrl+L 聚焦地址栏、Ctrl+T 新标签、Ctrl+W 关闭标签、
  Ctrl+R 刷新、Alt+←/→ 后退/前进。

### 31.4 本地文件 / about: / 内联 HTML ✅

- [x] `load_file`：本地 HTML 文件渲染；`about:blank` /
  `about:version` / `about:history` 内置页；内联 HTML
  （以 `<` 开头）直接渲染；http/https 显示「M2 前暂不支持」占位页。

### 31.5 测试扩充 ✅

- [x] 测试总数从 408 → **415** 条（7 条新 GUI headless 测试）。
- [x] GUI 逻辑以 headless 可测部分为主（PageInstance + PPM 转换
  + tkinter 可用性），无需 mainloop。

## P20 — 渲染质量与字重体系（M1 收官）✅

> 依据 `项目未来规划.txt` §P20：二次贝塞尔曲线填充（替换 P17
> 近似渲染）、覆盖率灰度抗锯齿、合成粗体（无字重文件时的兜底）、
> 完整字重阶梯、字体回退链、letter-spacing / text-rendering 玩具。
>
> 说明：本阶段构建了完整的曲线光栅化机制（`toy_browser/typography/rasterize.py`）+ 字重
> 体系 + 回退链 + CSS 属性接线。TTF 轮廓坐标解析器（`_parse_simple_glyph`）
> 对部分复杂字形仍有已知的坐标读取缺陷，因此曲线渲染路径经
> bbox 校验（`ttf_font.outline_valid`）门控：轮廓点数落在字形 bbox 内
> 才走曲线填充，否则回退到 P15.4 的 8×8 位图字形（保证任何情况下
> 文字都能渲染，零回归）。P20 的曲线机制、抗锯齿、合成粗体、
> 字重匹配、字体回退链均有独立测试覆盖。

### 20.1 二次贝塞尔曲线填充 + 覆盖率抗锯齿 ✅

- [x] `toy_browser/typography/rasterize.py`：`_flatten_quadratic`（de Casteljau 细分，
  直线退化为端点、弯曲曲线自适应细分）、`_contour_to_points`
  （TrueType off-curve 中点规则：连续 off-curve 点之间插入其中点
  作为隐式 on-curve 顶点 + y 轴翻转）。
- [x] `_fill_polygon`：偶数-奇数扫描线填充 + 2×2 超采样覆盖率
  （0..4 → 灰度 alpha），`antialias=True` 出灰度边缘，
  `antialias=False` 走硬边缘单采样（optimizeSpeed）。
- [x] bbox 校验门控：`GlyphOutline.bbox` + `ttf_font.outline_valid`
  —— 解析出的顶点必须落在字形 bbox 内才走曲线路径，否则回退位图。
- [x] 测试：`p20_quadratic_flatten`、`p20_offcurve_midpoint_rule`、
  `p20_fill_polygon_aa`、`p20_fill_polygon_speed`、
  `p20_outline_bbox_validation`、`p20_point_flags_bits`、
  `p20_glyf_offcurve_yreversed`、`p20_glyf_instruction_count_layout`。

### 20.2 合成粗体（stroke offset / dilation）✅

- [x] `rasterize._dilate_poly`：把像素空间多边形顶点沿局部质心方向
  外扩（玩具近似；真实合成粗体用 offset curve / Minkowski 和）。
  外扩半径随字号缩放，保证小/大字下粗体一致。
- [x] `draw_glyph_curves(weight=2.0)` 触发外扩 → 墨水像素 >
  正常字重。`_dilate_poly` radius<=0 时原样返回。
- [x] 测试：`p20_synthetic_bold_increases_ink`、`p20_dilate_poly`。

### 20.3 完整字重阶梯 + 合成粗体兜底 ✅

- [x] `font.get_metrics_for_weight(family, weight)`：weight≥600 时
  优先找 bold TTF（`_WEIGHT_SUFFIX` → "bd" 文件）；找不到则返回
  normal 字重并置位「需合成粗体」标志，由曲线路径执行 dilation。
- [x] `text.draw_text(font_weight=...)`：≥600 且无 bold 文件 →
  weight=2.0 走合成粗体。
- [x] 测试：`p20_metrics_for_weight`、`p20_synthetic_bold_flag_roundtrip`。

### 20.4 字体回退链 + weight_class 匹配 ✅

- [x] `ttf_font.find_font_for_family`：指定 family → 已注册回退族
  （`_FAMILY_FALLBACKS`，如 monospace→cour→arial）→ 搜索目录里
  按族名匹配的任意 TTF → 兜底取任意可用 TTF。
- [x] `ttf_font.match_weight(available, requested)`：挑选与请求
  字重最接近的可用 weight_class（平局取更重）。
- [x] `LayoutBox.font_weight`：CSS `font-weight`（数字 100–900 或
  normal/bold）解析入布局盒，经 paint command 传入渲染。
- [x] 测试：`p20_font_fallback_chain`、`p20_match_weight`、
  `p20_font_weight_stored`。

### 20.5 letter-spacing / text-rendering 玩具 ✅

- [x] `LayoutBox.text_rendering`：CSS `text-rendering`
  （auto/optimizeSpeed/optimizeQuality）解析入布局盒；
  optimizeSpeed 关闭曲线路径的 2×2 抗锯齿。
- [x] `letter_spacing`（P8.4 已有）接线到 `text.draw_text` /
  `rasterize.draw_text_curves` 的逐字 advance。
- [x] `DisplayCommand` 携带 letter_spacing / text_rendering /
  font_weight 三个新字段，`rasterize` 按命令传参。
- [x] 测试：`p20_text_rendering_stored`、`p20_letter_spacing_widens`、
  `p20_draw_text_curves_letter_spacing`、`p20_paint_command_fields`、
  `p20_layout_box_fields`、`p20_render_full_pipeline_weight`。

### 20.6 曲线路径接入文字渲染管线 ✅

- [x] `text.draw_text`：每个字符优先 `glyph_outline` + bbox 校验
  走曲线填充（`rasterize.draw_glyph_curves`），校验失败或无 TTF
  则回退 P15.4 的 8×8 位图 blit（保证渲染不回归）。
- [x] `paint_engine` 把 letter_spacing / text_rendering / font_weight
  经 DisplayCommand 传入 `text.draw_text`。
- [x] 测试：`p20_curve_glyph_fallback`（全部轮廓失效时仍出墨水）。

### 20.7 测试扩充 ✅

- [x] 测试总数从 415 → **437** 条（22 条新 P20 测试，超出
  规划 425 目标）。
- [x] 端到端 CLI `python main.py demo.html out.ppm` 通过。

## P21 — URL 与 HTTP/1.1 客户端（M2 网络栈第一步）✅

> 依据 `项目未来规划.txt` §P21：URL 解析（scheme/host/port/path/query/
> fragment、相对 URL 按 urljoin 规范、规范化）、HTTP/1.1 最小客户端
> （GET + Host + 状态码分派 200/301/404/500）、Content-Type 分派
> （text/html → 渲染管线；其他 → 下载/忽略）、请求超时 / 连接错误 →
> 统一错误页渲染。测试全部使用本地 `http.server` 夹具（离线可测、
> CI 稳定）。接口设计成可替换（`http.client` 起步，为 M5 方向 B
> 手写 socket 留口子）。

### 21.1 URL 解析 ✅

- [x] `toy_browser/network/net.py`：`URL` frozen dataclass（scheme/host/port/path/query/
  fragment/user/password）+ `parse_url(url, base=None)` 相对解析
  （WHATWG 规则：`?q`/`#f` 替换 base 的 query/fragment；`/abs` 同 host
  新 path；`//host` scheme-relative；相对 path 按 base 目录 + `..`
  规范化）。
- [x] `effective_port`（默认端口 80/443 推断）+ `authority`
  （默认端口省略）+ `to_string()` 规范化往返 + `origin` 属性。
- [x] `normalize_url`（scheme/host 小写化、空 path → `/`）、
  `is_network`/`is_http`/`is_https` 标志。
- [x] 测试：`p21_parse_url_absolute`、`p21_parse_url_https_default_port`、
  `p21_relative_url_resolution`、`p21_url_to_string_roundtrip`、
  `p21_normalize_url`、`p21_origin_property`、`p21_url_is_network_flag`。

### 21.2 HTTP/1.1 最小客户端 ✅

- [x] `HttpClient.get(url)`：`http.client`（标准库）GET + `Host` 头 +
  `User-Agent: toy-browser-py/1.0 (P21)`；3xx 原样返回（P22 跟进
  重定向链，P21 不自动跟随）；一次瞬时网络错误重试。
- [x] `HttpResponse`：status / status_text / headers / body /
  final_url / `content_type` / `is_html` / `ok`（2xx 才算成功）。
- [x] 死端口 / 连接失败 → `ConnectionError`（调用方渲染统一错误页）。
- [x] 测试：`p21_http_get_200`、`p21_http_get_404`、
  `p21_http_redirect_surfaced`、`p21_http_query_in_path`、
  `p21_connection_error_retries`（全部用本地 `http.server` 夹具，离线）。

### 21.3 Content-Type 分派 + 统一错误页 ✅

- [x] `fetch_html(url, client)` → `(text, HttpResponse)`：`text/html`
  （或空 / text/plain）UTF-8 解码；非 HTML Content-Type 抛
  `FetchError`（下载/忽略路径）；4xx/5xx 抛 `FetchError` 携带 status。
- [x] `error_page_html(title, detail)` + `render_error_page(...)`
  （经完整管线出 Bitmap）：超时 / 连接 / 404 / 500 统一错误页。
- [x] `toy_browser/app/gui.py`：http/https 输入走 `net.fetch_html`，`FetchError` →
  统一错误页（替换 P31 的「M2 前暂不支持」占位页）。
- [x] `main.py`：URL 输入走 `net` 栈；网络失败退出码 3 + 仍写
  错误页 PPM（不崩）。
- [x] 测试：`p21_content_type_dispatch`、`p21_fetch_error_on_4xx`、
  `p21_unsupported_scheme`、`p21_error_page_html`、
  `p21_error_page_renders`、`p21_fetch_html_accepts_str_url`、
  `p21_cli_network_error_exit_code`。

### 21.4 测试扩充 ✅

- [x] 测试总数从 437 → **456** 条（19 条新 P21 测试，超出规划
  445 目标）。
- [x] 网络层全部用本地 `http.server` 夹具，离线可测、CI 稳定。
- [x] 端到端 CLI `python main.py demo.html out.ppm` 通过；
  `python main.py http://127.0.0.1:1/x.html` → 退出码 3 + 错误页。

## P22 — HTTP 深化与子资源加载（M2 网络栈第二步）✅

> 依据 `项目未来规划.txt` §P22：重定向链（≤5 层，循环检测）、
> `Content-Encoding: gzip` 解压、`Transfer-Encoding: chunked` 解析、
> `<link rel=stylesheet>` / `<img src>` 子资源并发 + 接入现有事件
> 循环、`<base>` 标签玩具、子资源失败不阻塞渲染。测试全部使用本地
> `http.server` 夹具（离线可测、CI 稳定）。

### 22.1 重定向链 ✅

- [x] `HttpClient.get(url, follow_redirects=True, max_redirects=5)` —
  301/302/303/307/308 自动跟随；`_follow_redirects` 上限 5 层 +
  已见 URL 集合循环检测，超限/循环抛 `RedirectLoop`（FetchError 子类，
  携带 `chain`）。`follow_redirects=False` 保留 P21 行为（3xx 原样返回，
  `redirected=True`）。相对 `Location` 按重定向源 URL 解析（WHATWG）。
- [x] `fetch_html(..., follow_redirects=True)` 文档请求走重定向链。

### 22.2 gzip ✅

- [x] `maybe_gunzip(resp)`：`Content-Encoding: gzip` 时透明解压；无
  该头或解压失败（脏数据）则原样返回（不崩页面）。`_single_get`
  请求头携带 `Accept-Encoding: gzip`。

### 22.3 chunked ✅

- [x] `net.decode_chunked(raw)` 纯函数解析原始 chunked 流（hex size
  `[;ext]` CRLF 帧 + 终止 `0` 块；畸形 size 行安全停止）。线上由
  `http.client` 透明处理，此钩子供 M5 手写 socket 客户端复用 +
  单测。

### 22.4 子资源并发 + 22.5 <base> ✅

- [x] `collect_subresources(dom)` 文档序收集 `<link rel=stylesheet>`
  （含 alternate）+ `<img src>`；`base_url_from_dom(dom)` 读
  `<base href>`（无则 None）。
- [x] `load_subresources(dom, base_url, client)` — `<base href>` 优先
  于文档 URL；相对 src 按 base 解析；`data:` URI 跳过网络；顺序
  GET，**失败不阻塞**（404/超时 → None，不中断其余、不崩页面）；
  返回 `{kind: {abs_url: HttpResponse|None}}`。
- [x] `fetch_resource(url)` 子资源语义：成功返 `HttpResponse`，
  失败返 `None`（绝不 raise）。
- [x] `browser.render_document_from_url(url, ...)` — 文档走重定向链
  + gzip，子资源成功 CSS 并入样式表后跑完整管线出 Bitmap。
- [x] `main.py` URL 输入改走 `render_document_from_url`（P22 行为），
  网络失败仍退出码 3 + 写统一错误页。
- [x] 测试：20 条新 P22 测试（总数 456 → **476**，超出规划 475
  目标）。

## P23 — HTTPS 与磁盘缓存（M2 网络栈第三步）✅

> 依据 `项目未来规划.txt` §P23：TLS（`ssl` 标准库包裹，默认校验，
> `--insecure` 开关）、启发式磁盘缓存（`Cache-Control: max-age` /
> `no-cache` / `no-store` + 10% 启发式）、`ETag` / `If-None-Match`
> 304 协商缓存、加载进度事件（接现有事件循环）。测试目标
> 465 → 485，实际 476 → **492**（16 条新 P23 测试，超出目标）。

### 23.1 HTTPS（TLS 包裹 + 校验开关）✅

- [x] `HttpClient` 经 `http.client` 的 `ssl.create_default_context()`
  包裹 TLS：证书校验默认开启；`verify_tls=False`（对应 CLI
  `--insecure`）关闭 hostname + 证书校验（本地自签名 / 调试）。
  `HttpResponse` 无变化。端到端用 `ssl.SSLContext` 内存自签名证书
  夹具验证握手（`test_p23_https_tls_handshake`）。

### 23.2 启发式磁盘缓存 ✅

- [x] `net.CacheStore(root_dir)` — 响应存盘（SHA-256(URL) 为 key：
  `.json` 元数据 + `.bin` body）；`Cache-Control` 解析
  （`_parse_cache_control`）支持 `max-age` / `no-cache` / `no-store`
  （`no-store` 永不写盘）；无显式指示时的启发式 TTL =
  `10% × (Date − Last-Modified)`（`_heuristic_ttl`，RFC 7234）。
- [x] `HttpClient.get` 先查 `get_fresh`（命中 → `from_cache=True`，
  不触网）；成功后 `put` 回写（`from_cache` 的 304 刷新走 23.3）。
  `CacheStore.clear()` 清空。

### 23.3 ETag / If-None-Match 304 协商缓存 ✅

- [x] 过期但带 ETag 的条目经 `get_stale` 返回 → 首跳请求携带
  `If-None-Match`；服务端 304 → 复用旧 body、表面化 `from_cache=True`
  的 200，并 `revalidate_304` 刷新新鲜度（省流量）；无 ETag 的
  过期条目直接重取。`HttpResponse.etag` 属性。

### 23.4 加载进度事件 ✅

- [x] `LoadProgress` / `LoadEventSink` — 每次 fetch 回调
  `start` / `document` / `subresource` / `done`（fraction 0–1），
  可 `on_event` 订阅（M3.2 GUI 进度条消费者）；`load_subresources`
  逐子资源发 `subresource` 事件；CLI `--progress` 打印到 stderr。
- [x] P23 顺带：`HttpClient.post`（表单 `x-www-form-urlencoded` /
  逐字 body；POST 不缓存），CLI `--insecure` / `--cache DIR` /
  `--progress` 三新旗标；`browser.render_document_from_url` 新增
  `use_cache` / `progress` 参数。

### 23.5 测试扩充 ✅

- [x] 测试总数 476 → **492** 条（16 条新 P23 测试，超出规划
  485 目标）：缓存命中跳线、no-store、max-age 过期、ETag 304 复用、
  启发式 TTL、缓存清空、指令解析、进度事件（document/subresource/
  订阅）、POST 表单/逐字、insecure 开关、TLS 握手端到端、无 ETag
  重取、禁用存储。
- [x] `main.py demo.html` CLI e2e 通过；`--progress` 输出
  `[start 0%]` 事件；网络失败仍退出码 3 + 错误页。

## P24 — HTML5 容错解析（M2 收官硬仗）✅

> 依据 `项目未来规划.txt` §P24：Parser 独立成 `toy_browser/document/html_pkg/` 子模块、
> 输入统一为字节流；void elements、自动闭合纠错、格式化元素重构
> （adoption agency 简化版）、原始文本模式、完整 entities 表、注释 /
> DOCTYPE / 大小写不敏感。测试目标 492 → 512，实际 492 → **514**
> （22 条新 P24 测试，超出目标）。

### 24.1 原始文本模式 + HTML entities 完整表 ✅

- [x] `toy_browser/document/html_pkg/entities.py` — `decode_entities(text)` 单遍解码命名
  实体（WHATWG 策展子集 ~120 条，覆盖常用字符 / 希腊字母 / 数学
  运算符 / 上下标 / 引号）+ 数字引用（`&#65;` 十进制、`&#x1F600;`
  十六进制，`&#X42;` 大写 X）；码点 0 与代理区 → U+FFFD（规范
  §tokenization）；未知名实体保留原文。
- [x] `toy_browser/document/html_pkg/tokenize.py` 的 `RAWTEXT_TAGS`（`script` / `style` /
  `textarea` / `title` / `xmp`）— 内容原样取到匹配的关闭标签为止，
  不做 HTML 解析、不做实体解码（`<script>if (a < b) {}</script>` 内
  的 `<` 不被误判为标签）。

### 24.2 void elements + 自动闭合纠错 ✅

- [x] `VOID_TAGS`（`br` / `img` / `input` / `hr` 等 14 个）永不压栈
  （`<br/>` XHTML 自闭合语法也合法）。
- [x] 隐式兄弟闭合：第二个 `<p>` / `<li>` / `<dt>` / `<dd>` 关闭前
  一个；块级开始标签（`div` / `h1`–`h6` / `ul` 等）关闭 open 的
  `<p>`。
- [x] 容错词法：`<` 后首字符非字母 / 冒号（如 `a < b`、`<3`）整段
  作文本；孤立 `<>` 作文本；未闭合 `<` 尾部作文本。

### 24.3 注释 / DOCTYPE / 大小写不敏感 + 表格 foster parenting ✅

- [x] `<!DOCTYPE …>` 与 `<!-- … -->`（含 `<?…?>` 伪注释）词法产出
  `doctype` / `comment` 令牌，树构建阶段直接丢弃（渲染树干净）。
- [x] 标签名全部小写化：`<DIV>` == `<div>`，`<BODY>` == `<body>`；
  属性名同样小写化。
- [x] 玩具 foster parenting：`<tr>` / `<td>` / `<th>` / `<colgroup>`
  出现在表格上下文之外时，挂到最近的非表格祖先；在表格上下文之内
  时按正常嵌套处理。

### 24.4 格式化元素重构（adoption agency 简化版）✅

- [x] `<b><i>x</b></i>` — 迟到的 `</b>` 到达而栈顶是 `i` 时，把
  中间元素（`i`）重挂到匹配的 `b` 下并关闭；`b` 保持 open 由其
  关闭标签弹出。文本随元素移动。格式化标签族
  （`b i u s em strong small mark sub sup span`）。

### 24.5 Parser 独立成 toy_browser/document/html_pkg/ 子模块 + 字节流输入 ✅

- [x] 新建 `toy_browser/document/html_pkg/` 包：`node.py`（DOM `Node` + `VOID_TAGS`）、
  `entities.py`（P24.1）、`tokenize.py`（P24.2/5 词法 + BOM 嗅探
  UTF-8 / UTF-16 LE-BE / Latin-1 回退）、`build.py`（P24.2/3/4 树
  构建）、`__init__.py`（公共 API）。
- [x] `toy_browser/document/dom.py` 瘦身为兼容 shim：`from toy_browser.document import dom` / `from toy_browser.document.dom import Node`
  继续工作（Node 与 `html_pkg.node.Node` 是同一类对象，`isinstance`
  跨模块有效）；`dom.parse` 接受 str 或 bytes（str 先 UTF-8 编码
  再走字节流契约）。
- [x] 下游零改动：`css` / `layout` / `paint` / `js` / `browser` /
  `main` / `gui` 的现有 import 全部保持原样，492 条既有测试无
  回归通过。

### 24.6 测试扩充 ✅

- [x] 测试总数 492 → **514** 条（22 条新 P24 测试，超出规划 512
  目标）：命名 / 十进制 / 十六进制 / 零与代理 / 未知实体、
  script / style 原文模式、void 不压栈、`<br/>` 自闭合、`<p>` 隐式
  兄弟、块级闭合 `<p>`、`<b><i>` 重构、孤立关闭标签忽略、注释 /
  DOCTYPE 丢弃、大小写不敏感、foster parenting、裸 `<` 容错、
  UTF-16 / UTF-8 BOM、dom shim 同对象、bytes 输入、全管线渲染。
- [x] `main.py demo.html` CLI e2e 通过（41425 非白像素，与 P23 一致）；
  既有 492 条测试全绿无回归。

## P25 — CSS 层叠与计算值完善（M3 第一步）✅

> 依据 `项目未来规划.txt` §P25（阶段组 C：布局正确性与 JS/DOM 补全
> → M3）：把 CSS 引擎从玩具级特异性 / 级联升级到规范级四元组特异性
> + `!important` 来源级联 + 继承关键字 + shorthand 展开 + em/% 计算值
> 链。测试 514 → **528**（14 条新 P25 测试）。

### 25.1 specificity 四元组 + `!important` 来源级联 ✅

- [x] `Selector.specificity` 从玩具三元组 `(id, cls, type)` 升级为规范
  四元组 `(inline, id, cls, type)`；`Rule` 新增 `source`（author/ua）
  与 `important` 标志。级联排序键 `_cascade_key = (author, important,
  spec_sum)`：先来源层（author > ua），再 important 层，最后特异性。
- [x] `!important` 声明经 `ImportantDecl` 包裹；`_apply_subtree` 三遍
  应用：① 普通作者规则 → ② inline 普通（可被 author important 覆盖）
  → ③ author !important → ④ inline !important（important 层内最高）。
  旧的 `pseudo_class_specificity` 等 3 条测试随四元组更新断言。

### 25.2 继承关键字 inherit / initial / unset ✅

- [x] `INHERITED_PROPS`（color / font-size / font-family / font-weight /
  line-height / text-align / text-transform / overflow-wrap /
  letter-spacing / word-spacing）；`INITIAL_VALUES` 表
  （margin / padding / border-width / display / width / height /
  overflow / opacity / position / float / … 共 27 项初始值）。
- [x] `_resolve_cascade_value`：`inherit` 取父级 computed 值；
  `initial` 取 `INITIAL_VALUES`；`unset` = 继承属性走 inherit、
  非继承走 initial。

### 25.3 shorthand 展开为 longhand ✅

- [x] `SHORTHAND_EXPANSION` 表 + `expand_shorthands`：`margin` →
  `margin-top/right/bottom/left`、`padding` → 同构四项、
  `border-width` → 四向 longhand；shorthand 原键保留（layout 读
  `sn.props["margin"]` 不变），longhand 并存供子节点 inherit 查找。

### 25.4 em/% 计算值相对父级解析链 ✅

- [x] 子节点 `parent_font_px` 取父节点 *resolved* font-size（非 UA
  默认 16）；`font-size: 2em` 在 40px 父级下计算为 80px（旧实现
  误算 32px）；`resolve_length` 对 em/% 均以父级 computed font 为基准。

### 25.5 测试扩充 ✅

- [x] 测试总数 514 → **528** 条（14 条新 P25 测试）：四元组特异性 /
  id>class / class>type、`!important` 覆盖普通 inline / author
  important 胜过普通 inline、`inherit` 颜色继承、`initial` margin 归零、
  `unset` text-align 继承、margin / padding shorthand 展开、em 计算链
  80px、font-size 继承 32px、author 源层 > UA 源层、全管线渲染回归。
- [x] 既有 514 条测试全绿无回归；`main.py demo.html` CLI e2e 通过。

## P26 — 块级布局修正（M3 第二步）✅

> 依据 `项目未来规划.txt` §P26（阶段组 C：布局正确性 → M3）：修复块级
> 布局引擎的五个核心缺陷 —— margin 折叠 / BFC 浮动包含 / 百分比高度
> 解析链 / min-max 宽度约束 / width:auto 分配。测试 528 → **542**
> （14 条新 P26 测试）。

### 26.1 CSS Margin 折叠（CSS2.1 §9.5）✅

- [x] `_fold_margins`：父 box 与首个 block 子 box 的 top margin 折叠为
  max（而非求和）；空 block 的 top+bottom margin 折叠为单一值。
- [x] `_next_sibling_gap`：相邻 block 兄弟的 gap = max(prev.mb, next.mt)，
  非 sum（旧实现误用 outer_height 导致 30px 而非 20px）。
- [x] 内容栈推进改用 border-box 高度（不含 margin），collapsed gap 由
  `_next_sibling_gap` 单独计入 —— 消除 double-count。
- [x] 测试：`p26_margin_collapse_siblings`（gap 20px）/
  `p26_margin_collapse_parent_child`（折叠 15px）/
  `p26_margin_collapse_empty_block`（gap 30px）。

### 26.2 BFC 浮动包含（CSS2.1 §9.4.1）✅

- [x] `overflow_hidden` box 建立 BFC：浮动子 box 高度纳入父 box 高度
  （`float_max_h`），修复"浮动逃逸"bug。
- [x] BFC 自身 rect 不受浮动偏移影响（`bfc_content_x0` 保存原始内容边）。
- [x] 测试：`p26_bfc_cleares_floats`（高度包含浮动）/
  `p26_float_inside_bfc_no_overlap`（浮动 x ≥ BFC 左边）。

### 26.3 百分比高度解析链（CSS2.1 §10.6.5）✅

- [x] `StyledNode.parent_height_px`：含父 box 的已解析内容高度；
  `height: 25%` 在 600px 父级下 → 150px（旧实现误用 font-size 基准 → 4px）。
- [x] `resolve_length` 对 `%` 单位改为 `value * ref / 100`（旧实现
  `value * ref` 漏除 100）。
- [x] `build_box` 传播 `parent_height_px` 给子 box；`layout()` 传入
  viewport height。
- [x] 测试：`p26_pct_height_chain`（25% of 600 = 150px）/
  `p26_height_auto_grows`（无显式高度时自适应内容）。

### 26.4 CSS min/max 宽度/高度约束（CSS2.1 §10.4）✅

- [x] `LayoutBox` 新增 `min_width / max_width / min_height / max_height`
  字段（`build_box` 从 CSS 属性读取）。
- [x] `layout_block` 早期 clamp：width 计算后应用
  `max(min_width, min(max_width, computed))`；finalize 阶段再 clamp 一次
  兜底。
- [x] 测试：`p26_min_width_clamps`（min-width 150 强制拓宽 100→150）/
  `p26_max_width_clamps`（max-width 50 收窄 300→50）/
  `p26_min_height_clamps`（min-height 50 加高 10→50）/
  `p26_max_height_clamps`（max-height 20 收窄 40→20）。

### 26.5 width:auto 分配（CSS2.1 §10.3）✅

- [x] 无显式宽度的 block 子 box 宽度 = 父内容宽 - margin-l - margin-r
  - padding；旧实现使用 viewport 宽度导致子 box 溢出父容器。
- [x] 浮动子 box 同样受父内容宽约束（`child_containing_w`）。
- [x] 测试：`p26_width_auto_fills`（auto 宽度 = 父 200px）/
  `p26_width_auto_with_margins`（auto 宽度 = 200 - 10 - 20 = 170px）。

### 26.6 测试扩充 ✅

- [x] 测试总数 528 → **542** 条（14 条新 P26 测试）。
- [x] 既有 528 条测试全绿无回归；`main.py` CLI e2e 渲染通过。

## P27 — Flexbox 完整化（M3 第三步）✅

> 依据 `项目未来规划.txt` §P27：flex 引擎从"单行 + grow 分配"升级到
> 规范级 flex 行为（spec §9.2/§9.7）：主轴三值冻结（basis/grow/shrink）、
> 多行 wrap、交叉轴全项对齐、方向 reverse、order 重排、min-width:auto。
> 测试 542 → **562**（20 条新 P27 测试）。

### 27.1 LayoutBox 新字段 + CSS 属性读取 ✅

- [x] `LayoutBox` 新增 `flex_wrap`（默认 `nowrap`）、`align_content`
  （默认 `stretch`）、`flex_shrink`（默认 1.0）、`flex_basis`
  （float|None，None = auto）、`flex_order`（默认 0）、`align_self`
  （默认 "" = 继承容器 align-items）。
- [x] `build_box` 在 `is_flex` 分支读取 `flex-wrap` / `align-content`；
  在 P27 读取块（P3.2 flex-grow 块之前）读取 `flex-shrink` /
  `flex-basis` / `order` / `align-self`。
- [x] `flex-basis` 支持 `px` / `em` / `%`（% 存为小数，布局时按容器
  主轴解析）；`auto` 关键字 → None。

### 27.2 主轴尺寸：grow/shrink/basis 三值冻结 ✅

- [x] Phase 1（basis 计算，spec §9.2.3）：`flex-basis` 显式值 →
  主轴显式尺寸（width/height）→ 自然主轴尺寸（Phase 0 在 containing
  宽度下测量）。
- [x] Phase 2（spec §9.7 单遍分配）：余量 > 0 按 `flex-grow` 权重分配；
  余量 < 0 先抬到 min 地板（content / min-width / min-height），再按
  `shrink × sized` 功率分配 deficit；`flex-shrink:0` 项保持不变。
- [x] 测试：`p27_flex_shrink_proportional` / `p27_flex_shrink_weighted`
  / `p27_flex_basis_explicit` / `p27_flex_basis_zero` /
  `p27_flex_shrink_zero_keeps_size` /
  `p27_flex_grow_shrink_basis_shorthand`。

### 27.3 flex-wrap 多行 ✅

- [x] Phase 3：`flex-wrap:wrap` 时按 sized 宽度逐行装箱
  （`line_w + w > available` 即换行）；`wrap-reverse` 反转行序，
  行从远交叉边（row 方向为底部）堆叠。
- [x] wrap 容器不做 shrink-to-fit（spec §9.7.4：wrap 时项溢出换行而非
  压缩），min 地板仍生效。
- [x] 测试：`p27_flex_wrap_two_lines` / `p27_flex_wrap_single_line_fits`
  / `p27_wrap_reverse_direction`。

### 27.4 交叉轴：align-items / align-content / align-self + space-* ✅

- [x] 行高 = 行内最高项；单行 + 显式容器高度 + align-content:stretch
  （默认）时，行拉伸填满交叉轴，align-items:center 即容器中心。
- [x] 多行：align-content 分布行间空隙（center / end / space-between /
  space-around）；单行按 spec §9.5 同样分布。
- [x] 项内：`align-self` 覆盖容器 `align-items`；stretch 填满行高，
  center/baseline 居中，end 对齐行底。
- [x] 主轴 space-around（半间隙在两端）/ space-evenly（间隙全相等）；
  reverse 方向时所有起点从远端计算。
- [x] 测试：`p27_align_items_start` / `p27_align_items_end` /
  `p27_align_self_overrides` / `p27_justify_content_space_evenly` /
  `p27_justify_content_space_around` / `p27_align_content_center_multi_line`。

### 27.5 方向 reverse + order + min-width:auto ✅

- [x] `flex-direction:row-reverse`：主轴起点移到右缘，项自右向左排布，
  justify-content start 贴右缘 / end 贴左缘。
- [x] `flex-direction:column-reverse`：主轴起点移到容器底部，项自下
  向上堆叠（item 0 在底部）；`y_off` 按"下一项高度"上推，避免重叠。
- [x] `order`：项按 `(order, 源序号)` 稳定排序后装箱与排布。
- [x] min-width/min-height:auto：shrink 地板 = max(内容主轴尺寸,
  显式 min-*)。
- [x] 测试：`p27_flex_row_reverse` / `p27_flex_direction_column_reverse`
  / `p27_flex_order_reorders` / `p27_min_width_auto_floor`。

### 27.6 测试扩充 ✅

- [x] 测试总数 542 → **562** 条（20 条新 P27 测试）。
- [x] 既有 542 条测试全绿无回归；`main.py` CLI e2e 渲染 P27 flexbox
  页面（row + wrap + column-reverse）通过。

## P29 — JS 引擎补全（M3 第五步）✅

> 依据 `项目未来规划.txt` §P29：正则字面量 + 最小正则引擎（桥接
> Python `re`，JS 语义接口）、`for...of` + 迭代器协议（`Symbol.iterator`
> 玩具 + 生成器 `function*`/`yield`）、`Date`/`Math`/`JSON` 补全、
> `String`/`Array` 残尾方法清理。
> 测试 583 → **603**（20 条新 P29 测试）。

### 29.1 正则引擎（`re` 桥接）✅

- [x] `lex`：`/pattern/flags` 字面量（表达式上下文感知，避免
  除法歧义；`\` 转义 + `[...]` 字符类正确匹配终止 `/`）。
- [x] AST `RegexpLit(pattern, flags)`；解释器产出与
  `new RegExp(...)` 相同 shape 的 dict（`__regexp__`、`test`/`exec`/
  `match`/`replace`/`split` 闭包 + `source`/`lastIndex` 键）。
- [x] `Host._regexp_ctor`：`i`/`m`/`s` 标志映射；`exec` 返回
  `[0, group1, …]`（含捕获组）；`match` 带 `g` → 全部命中，
  不带 → 首个 + 捕获组；`replace` 支持 `$&`/`` $` ``/`$'` 替换
  引用 + 函数替换 + `g` 标志；`split` 支持 limit；非法 pattern →
  no-match 哨兵（不崩溃）；`u`/`y` 忽略。
- [x] `String.prototype.match` 正则感知（正则 dict → 其 match
  闭包）；`String.prototype.replace` / `replaceAll` 正则感知；
  字符串方法 `at` / `codePointAt` 补全。
- [x] 测试：`p29_regexp_literal_test` / `p29_regexp_literal_flags` /
  `p29_regexp_exec_groups` / `p29_string_match_regexp` /
  `p29_string_replace_regexp` / `p29_new_regexp_ctor`。

### 29.2 迭代器协议 + 生成器 ✅

- [x] `for (let/const/var x of expr)`（`ForOf` 节点）：数组、
  字符串（逐字符）、dict（键序）、`ToyGenObject`（驱动 `.next()`
  至 done）、`@@iterator` 自定义协议、Python `__iter__` 回退。
- [x] `function* name() {...}` / `function*() {...}`（`GenFuncDecl`
  / `GenFunExpr`）+ `YieldExpr`（语句级 `yield` / `yield expr`）。
- [x] `ToyGenObject`（`{done, value}` 协议，`.next()` / `.return()`
  闭包）与 `ToyIterator`（包装 Python iterable）；
  `Symbol.iterator` → `"@@iterator"` 键（JS 语义玩具映射）。
- [x] 生成器驱动器递归遍历 `while` / `if` / `for` / `for...of`
  嵌套体中的 `yield`（`_make_gen_func` 内部 `_drive_stmt` /
  `_drive_block`，`_Return` → 生成器返回值）。
- [x] Map / Set 增加 `__iter__`（Map → `[k,v]` 对；Set → 值序）+
  `keys()`/`values()`/`entries()` 返回 `ToyIterator`，`for...of`
  直接可用；函数内 `arguments` 定义为实参列表（`for...of arguments`
  恢复 P16.1 语义）。
- [x] 测试：`p29_for_of_array` / `p29_for_of_string_chars` /
  `p29_for_of_generator` / `p29_generator_next_done` /
  `p29_generator_expression_form` / `p29_generator_yield_in_loop` /
  `p29_for_of_map_iterable` / `p29_symbol_iterator_protocol`。

### 29.3 Date / Math / JSON 补全 ✅

- [x] `Date`：无参 → `Date.now()` 毫秒；数值参数 = 毫秒（JS 语义）；
  `getTime()` 返回毫秒；`Date.parse`（ISO-8601 子集 + 纯数字串）/
  `Date.UTC(year, month?, …)`（0-based 月）新增。
- [x] `Math`：`log`/`log2`/`log10`/`log1p`/`exp`/`expm1`/`sin`/`cos`/
  `tan`/`asin`/`acos`/`atan`/`atan2`/`sinh`/`cosh`/`tanh`/`cbrt`/
  `hypot`/`trunc` + 常量 `LN2`/`LN10`/`LOG2E`/`LOG10E`/`SQRT2`/
  `SQRT1_2`。
- [x] `JSON`：嵌套 dict/list 的 `stringify`/`parse` 往返验证。
- [x] `Array` 残尾：`fill`/`findLast`/`findLastIndex`/`toReversed`/
  `toSorted`/`entries`/`keys`/`values`。
- [x] 测试：`p29_math_trig_log` / `p29_math_constants` /
  `p29_json_stringify_nested` / `p29_json_parse_stringify_roundtrip` /
  `p29_array_tail_methods` / `p29_string_tail_methods`。

## P30 — DOM API 补全（M3 收官）✅

> 依据 `项目未来规划.txt` §P30：补全浏览器内核与网页 JS 交互所依赖的
> 标准 DOM 节点 API，使内联脚本能完成"查询 → 增删改 → 事件"闭环，
> 让简单真实站点的动态内容（懒加载、Tab 切换、表单校验提示）可读、
> 可跳转、有历史。这是 M3 里程碑（浏览简单网站）的收官阶段。
> 测试 603 → **623**（20 条新 P30 测试）。

### 30.1 querySelector / querySelectorAll（全选择器）✅

- [x] `Node.query_selector_all(selector, interaction_state)` 从玩具
  的 `.cls` / `#id` 升级为**完整 CSS 选择器引擎**：直接复用
  `css.parse_selector_list` + `css.Selector.matches`，支持简单选择器
  （`tag` / `.cls` / `#id` / `.a.b`）、后代（`a b`）、直接子
  （`a > b`）、逗号选择器列表、伪类（`:hover` / `:focus` /
  `:first-child` / `:last-child` / `:nth-child(n)`）。
- [x] `:hover` / `:focus` 仅在提供 `interaction_state` 时匹配（headless
  下永不命中，与 P8.1 一致）；结构伪类直接对 DOM 判定。
- [x] 文档序保证 + 自含 scope（元素调用时包含自身，保留 P0 语义，
  `div.query_selector_all('.c')` 命中带 class c 的 div 本身）。
- [x] `document.querySelector`（首个匹配）+ `document.querySelectorAll`
  （全部匹配）经 `Host.eval_doc_method` 暴露给 JS 引擎。
- [x] 测试：`p30_qsel_simple_selectors` / `p30_qsel_descendant_child` /
  `p30_qsel_comma_list` / `p30_qsel_child_pseudos` /
  `p30_qsel_hover_no_state` / `p30_qsel_js_query_selector` /
  `p30_qsel_scoped_include_self`。

### 30.2 节点增删改 + 失效重绘 ✅

- [x] `Node` 新增 `append_child` / `insert_before` / `remove_child` /
  `replace_child`（重挂 `parent` 指针 + 旧父移除，保证树一致）+
  `DomElement.appendChild` / `removeChild` / `insertBefore` /
  `replaceChild` / `remove` 薄封装。
- [x] `DomElement` 新增结构属性 `children` / `parentElement` /
  `tagName`；`textContent` 读取始终反映实时子树（`text_content()`）。
- [x] 失效重绘：DOM 变更后下一轮 `css.apply → layout → paint` 即得到
  新几何与像素（M3 闭环关键——JS 修改 DOM，渲染管线可见其效果）。
- [x] 测试：`p30_append_child_mutation` / `p30_remove_child_mutation` /
  `p30_insert_before_mutation` / `p30_mutation_renders`。

### 30.3 createElement / classList / dataset / setAttribute ✅

- [x] `document.createElement(tag)`：返回未附着元素（`parent=None`），
  经 `appendChild` 等接入树；小写归一 tag。
- [x] `DomElement.classList`：`{add, remove, contains, toggle, value}`
  玩具视图（读写 class 属性；`toggle` 返回 JS 语义布尔）。
- [x] `DomElement.dataset`：`data-*` 属性 → camelCase 键
  （`data-a-b` → `aB`，符合 DOM spec 映射）。
- [x] `setAttribute(name, value)` / `getAttribute(name)` 直写/读
  `Node.attrs`（`value=None` → `""`）。
- [x] 测试：`p30_create_element_detached` / `p30_classlist_add_toggle` /
  `p30_dataset_camelcase` / `p30_set_get_attribute`。

### 30.4 自定义事件 addEventListener / dispatchEvent ✅

- [x] `DomElement.addEventListener(name, listener)`：监听器注册到
  **Host 持有的按节点分区的注册表**（`host._listeners[node]`，
  元素可被反复重新包装而监听不丢）。
- [x] `DomElement.dispatchEvent(name, detail)`：同步按注册顺序调用
  所有同名监听器，事件对象 `{type, target, detail}`；监听器 `return
  false` 记录 `defaultPrevented`（玩具语义：不中断后续监听器）。
- [x] 事件处理器内可安全修改 DOM（监听器 `appendChild` 后即时可见）。
- [x] 测试：`p30_custom_event_two_listeners` / `p30_custom_event_isolated` /
  `p30_custom_event_mutation_visible`。

### 30.5 全管线回归 + M3 闭环 ✅

- [x] JS 驱动的"查询 + 增删改"脚本经完整渲染管线不崩且产出像素。
- [x] 查询与修改混合脚本离开时 DOM 处于一致、可重渲染状态。
- [x] 测试：`p30_full_pipeline_regression` / `p30_qsel_js_mutation_roundtrip`。

---

## P32 — 会话、历史与工具（M4 收官）✅

> 依据 `项目未来规划.txt` §P32：历史记录（持久化）、书签、Cookie 玩具、
> 迷你 DevTools、统一错误页、about/view-source 玩具 + M4 架构文档化。
> 测试 623 → **657**（34 条新 P32 测试）。同时修复 P30 引入的
> `tag.class` 复合选择器回归（`css._simple_matches` 重构）。

### 32.1 历史记录（session.HistoryStore）✅

- [x] `toy_browser/app/session.py`：`HistoryStore` 全局访问日志，JSON 原子写
  （`.tmp` + `os.replace`），损坏文件降级为空。
- [x] `add()` 折叠相邻重复 URL；`about:` 前缀不记录；`recent()`/`all()`/`clear()`。
- [x] 上限 `max_entries`（默认 500）；`in_memory_only` 供测试。
- [x] 测试：`p32_history_add_recent` / `p32_history_consecutive_collapse` /
  `p32_history_cap` / `p32_history_about_skipped` /
  `p32_history_persist_roundtrip` / `p32_history_corrupt_graceful`。

### 32.2 书签（session.BookmarkStore）✅

- [x] `BookmarkStore`：`{url,title}` 列表，URL 唯一（重复 add 更新标题），
  `add/remove/has/urls/all`，JSON 持久化。
- [x] `bookmarks_page_html(store)` 渲染 `about:bookmarks` 页 + 空态提示。
- [x] `toy_browser/app/gui.py`：`TabBrowser` 挂 `history`/`bookmarks`，书签菜单 +
  "Bookmark This Page" 动作；`_navigate`/`load_url` 写全局历史。
- [x] 测试：`p32_bookmarks_add_remove` / `p32_bookmarks_update_title` /
  `p32_bookmarks_persist` / `p32_bookmarks_page_html`。

### 32.3 Cookie 玩具（cookie_store.CookieJar）✅

- [x] `toy_browser/network/cookie_store.py`：`parse_set_cookie`（`name=value` +
  `Domain`/`Path`/`Max-Age`/`Expires`，`Max-Age<=0` 立即过期，非法丢弃）。
- [x] `CookieJar` 按 `(name, domain, path)` upsert；`cookie_header_for`
  做 host 后缀 + path 前缀匹配。**无安全边界**（计划明示）。
- [x] 接线：`net.HttpClient(cookies=jar)` 在 `_single_get` 附 `Cookie` 头 +
  响应 `ingest_response`；`fetch_html` 签名不变。
- [x] 测试：`p32_cookie_parse_basic` / `p32_cookie_max_age_zero` /
  `p32_cookie_header_match` / `p32_cookie_subdomain` /
  `p32_cookie_path_filter` / `p32_cookie_malformed_dropped` /
  `p32_cookie_jar_ingest_count`。

### 32.4 迷你 DevTools（browser.devtools_*）✅

- [x] `devtools_dom_tree`（缩进 `tag#id.class [text]`）/
  `devtools_computed_style`（UA+内联+author+@media/@supports 级联，含
  `__meta__`/`__notfound__`）/ `devtools_selector_hits`（命中节点 +
  作者规则来源，与渲染器同一套 at-rule 感知规则集）/
  `devtools_report`（聚合文本）。
- [x] `main.py`：`--devtools` / `--devtools-el ID` / `--devtools-sel SEL`。
- [x] `css.Rule`/`css.Selector` 新增 `selector_text` 字段（DevTools 溯源用）。
- [x] 测试：`p32_devtools_dom_tree` / `p32_devtools_computed_style` /
  `p32_devtools_computed_style_missing` / `p32_devtools_selector_hits` /
  `p32_devtools_report_includes_all` / `p32_main_devtools_flag`。

### 32.5 统一错误页（net.categorize_fetch_error）✅

- [x] `net.error_page_html(title, detail, category)` 按类别出独立文案：
  `dns`/`connect`/`timeout`/`bad_url`/`redirect`/`http`/空=透传。
- [x] `categorize_fetch_error(exc)`：异常类型 + 消息启发式归类。
- [x] `toy_browser/app/gui.py` 与 `main.py` 网络失败分支统一调用。
- [x] 测试：`p32_categorize_dns` / `p32_categorize_timeout` /
  `p32_categorize_connect` / `p32_categorize_bad_url` /
  `p32_categorize_redirect` / `p32_categorize_http_status` /
  `p32_error_page_copy_distinct` / `p32_http_error_page_shows_status` /
  `p32_error_page_generic_passthrough`。

### 32.6 about:blank / view-source: 玩具 ✅

- [x] `gui._about_page` 支持 `about:blank`/`about:history`（读全局历史）/
  `about:bookmarks`/`about:version`。
- [x] `gui._view_source_page`：本地文件重读原始源码；网络页注明未保留。
- [x] 测试：`p32_view_source_local_file` / `p32_view_source_network_not_retained`。

### 32.7 M4 重构窗口：ARCHITECTURE.md ✅

- [x] 新增 `docs/ARCHITECTURE.md`：模块边界、数据流、P32 新增模块说明、
  M5 扩展点、边界不变式。
- [x] **回归修复**：P30 把 `tag.class` 解析为单一 opaque token
  `("p.x",)`，但 `css._simple_matches` 仍按旧的"单 segment"逻辑，导致
  所有 `tag.class` 复合选择器静默失配。重写 `_simple_matches` 支持
  `tag`/`.class`/`.a.b`/`#id`/`tag.class`/`tag#id` 全组合。

---

### P28 — 表单与交互真实化（M3 第四步）✅

> 依据 `项目未来规划.txt` §P28：表单控件从"占位框"升级为**可交互、
> 有状态、可提交**的真实控件 —— select / checkbox / radio / file 状态
> 渲染，表单 GET/POST 序列化，focus / tab 顺序 / required 校验。
> 测试 562 → **583**（21 条新 P28 测试）。

### 28.1 控件状态与渲染 ✅

- [x] `LayoutBox` 新增 9 个控制字段：`control_type` / `control_checked`
  / `control_value` / `control_options` / `control_option_index` /
  `control_file_name` / `control_focus` / `control_tabindex` /
  `control_required`；`build_box` 从 DOM 属性读取（checkbox/radio
  缺省 value="on"，select 收集 option 列表 + selected 索引，
  `focus=""` 属性 → `control_focus`）。
- [x] `layout_block` 按控件类型给出默认盒尺寸（checkbox/radio 16×16、
  file 220×24、select 120×24、button 80×28、textarea 按 cols/rows
  或 160×64 缺省），**先于**通用 `<input>` 回退执行；表单控件无
  CSS border-width 时强制 1px 边框（`_force_form_border`）。
- [x] `paint_engine` 渲染控件状态：checked checkbox / radio 在中心
  画 6×6 实心方块；`<select>` 显示选中项标签 + 下拉箭头条；
  `<input type=file>` 显示文件名或 "No file selected"；
  `control_focus` 控件画 2px 蓝色 focus 环。
- [x] 测试：`p28_input_checkbox_render` / `p28_input_radio_render` /
  `p28_select_option_render` / `p28_file_input_render` /
  `p28_textarea_default_size` / `p28_checkbox_toggle_render_diff`。

### 28.2 表单序列化与提交 ✅

- [x] `browser.collect_form_fields(root, form_id)`：DOM 序收集
  可序列化字段（text/hidden/checkbox(checked)/radio(checked)/
  select(多值)/textarea/button），返回 `[{name, value, kind}]`。
- [x] `browser.serialize_form_fields(fields)`：
  `application/x-www-form-urlencoded` 编码（`urllib.parse.quote`，
  同名多值重复输出）。
- [x] `browser.form_action_url`：`<form action>` 解析（绝对 URL 直通、
  `//` 协议相对、`/` 根相对对照 `<base href>`、相对路径
  `urljoin`）。
- [x] `browser.form_get_url` / `browser.form_post_body`：GET 拼
  `action?query`、POST 返回 `{url, body, content_type}`。
- [x] `browser.form_submit(root, form_id, method, base_url,
  submit_host)`：先 `validate_form`（有违规 → `ok:False` + violations），
  通过则序列化；传入 P21 `net.HttpClient` 时实际发 GET/POST，
  否则 dry-run 只返回 url/body。
- [x] 测试：`p28_form_get_query_string` / `p28_form_post_body` /
  `p28_form_select_multiple` / `p28_form_radio_group` /
  `p28_form_action_url_resolution` / `p28_form_hidden_field` /
  `p28_submit_blocked_by_validation`。

### 28.3 focus / tab 顺序 / 校验 ✅

- [x] `browser.tab_order(root)`：可聚焦元素（`<a href>` / `<button>` /
  `<input>` 除 hidden/image / `<select>` / `<textarea>` / 带
  tabindex 的元素）按"正 tabindex 优先、其余 DOM 序"排布，
  跳过 hidden input。
- [x] `browser.focus_element(root, id)`：设置 DOM `focus=""` 属性，
  下次 `build_box` 读出 `control_focus=True` → 渲染蓝色 focus 环。
- [x] `browser.validate_form(root, form_id)`：`required` 属性玩具校验
  （text/select/textarea 空值、checkbox/radio 未勾选、
  `pattern` 正则不匹配 → violation 列表）。
- [x] JS 引擎 `.value` 属性按标签分派（`<select>` → 选中项 value，
  `<textarea>` → 文本内容，`<input>`/`<button>` → value 属性）。
- [x] 测试：`p28_tab_order_natural` / `p28_tab_order_explicit` /
  `p28_focus_marks_box` / `p28_required_validation` /
  `p28_required_checkbox` / `p28_required_select` /
  `p28_js_element_value_prop`。

### 28.4 测试扩充 ✅

- [x] 测试总数 562 → **583** 条（21 条新 P28 测试）。
- [x] 既有 562 条测试全绿无回归；`main.py` CLI e2e 渲染 P28 表单页
  （text + checkbox + radio + select + file + textarea + button）通过，
  序列化/tab 序/focus 全链路验证。

## 实施顺序

```
P0.1  img 占位框        （独立，~1h）
P0.2  表单控件渲染      （独立，~1h）
P0.3  选择器扩展        （独立，~2h）
P0.4  JS 补全          （独立，~2h）
P0.5  失效重绘          （依赖 P0.1-4 稳定，~1h）
P1.1  视觉特效          （~2h）
P1.2  事件循环完整化    （~1h）
P1.3  默认样式表        （~1h）
P1.4  Flexbox 最小      （~2h）
P2.1  可访问性          （独立，~1h）
P2.2  链接收集          （独立，~30min）
P2.3  PNG 输出          （独立，~1h）
P2.4  测试扩充          （随 P2 完成）
P3.1  内嵌图片解码      （独立，~2h）
P3.2  Flexbox 深化      （~1.5h）
P3.3  JS 内置对象      （~1h）
P3.4  CSS Transition   （~1.5h）
P3.5  测试扩充          （随 P3 完成）
P4.1  @keyframes 动画   （~2h）
P4.2  JS 对象扩展      （~2h）
P4.3  CSS Grid + 简写  （~2h）
P4.4  测试扩充          （随 P4 完成）
P5.1  CSS 视觉特效深化  （~2h）
P5.2  CSS float        （~1.5h）
P5.3  JS 数组高阶方法  （~1.5h）
P5.4  测试扩充          （随 P5 完成）
P6.1  position:abs     （~1.5h）
P6.2  text-overflow/line-clamp （~1h）
P6.3  JS 内置对象深化  （~1h）
P6.4  测试扩充          （随 P6 完成）
P7.1  CSS z-index      （~1h）
P7.2  display:table    （~1.5h）
P7.3  JS 数组高阶补全  （~1h）
P7.4  position:sticky  （~1h）
P7.5  测试扩充          （随 P7 完成）
P8.1  CSS :hover/:focus （~1.5h）
P8.2  position:relative （~1h）
P8.3  JS 方法补全       （~1h）
P8.4  CSS 文本属性      （~1h）
P8.5  测试扩充          （随 P8 完成）
P9.1  @media queries    （~1.5h）
P9.2  abs/rel 全向偏移  （~1h）
P9.3  JS 方法深化       （~1.5h）
P9.4  clip/gradient/text-shadow （~2h）
P9.5  测试扩充          （随 P9 完成）
P10.1 :first-child/:last-child/:nth-child （~1.5h）
P10.2 JS Map/Set/Array.from/replaceAll （~1.5h）
P10.3 CSS outline + background-image toy （~1h）
P10.4 JS Error / 嵌套 JSON / Array.toString （~1h）
P10.5 测试扩充          （随 P10 完成）
P11.1 transition 多属性 + transform （~1h）
P11.2 JS try/catch/finally + throw （~1.5h）
P11.3 CSS @supports + display 切换 （~1h）
P11.4 命中测试 + 事件坐标 API     （~1.5h）
P11.5 测试扩充          （随 P11 完成）
P12.1 多属性 @keyframes + Web Animations mini API （~2h）
P12.2 JS class / extends / constructor / super （~2h）
P12.3 CSS calc() 玩具 + media 复合条件 （~1.5h）
P12.4 mousemove 状态机 + 预渲染回调 （~1.5h）
P12.5 测试扩充          （随 P12 完成）
P13.1 JS 解构赋值 + 对象展开运算符 （~1.5h）
P13.2 JS 字符串模板字面量 （~1.5h）
P13.3 CSS filter 效果 （~1.5h）
P13.4 事件委托 + 输入事件模拟 （~1.5h）
P13.5 测试扩充          （随 P13 完成）
P14.1 CSS 滚动容器 （~1.5h）
P14.2 JS 箭头函数 （~2h）
P14.3 CSS text-indent （~1h）
P14.4 CSS @font-face 玩具 （~1h）
P14.5 测试扩充          （随 P14 完成）
P15.1 JS 高阶数组 + Infinity/NaN （~1.5h）
P15.2 CSS 滚动 JS API （~1h）
P15.3 CSS box-decoration-break / -webkit-appearance （~1h）
P15.4 字体切换渲染 （~1.5h）
P15.5 测试扩充          （随 P15 完成）
P16.1 JS 数组/调用展开 + ===/!== （~1.5h）
P16.2 JS 可选链 + 空值合并 （~1.5h）
P16.3 CSS 文本增强 （~1h）
P16.4 CSS scroll-snap 玩具 （~1h）
P16.5 测试扩充          （随 P16 完成）
P17.1 TTF/OTF 头部表解析 （~2h）
P17.2 glyf/loca 轮廓提取 （~2h）
P17.3 cmap 逆向查询 + advance width （~1.5h）
P17.4 @font-face 升级 + 双字重切换 （~1.5h）
P17.5 测试扩充          （随 P17 完成）
P18.1 半领导者模型 + baseline （~2h）
P18.2 内联跨行拆分 + 样式保留 （~1.5h）
P18.3 vertical-align + 行内图片 （~1.5h）
P18.4 text-align 真实行盒 （~1h）
P18.5 测试扩充          （随 P18 完成）
P19.1 UAX#14 简化断行算法 （~2h）
P19.2 CJK 逐字断行 + 禁则处理 （~1.5h）
P19.3 &shy; + word-break/overflow-wrap （~1h）
P19.4 长单词溢出最小处理 （~1h）
P19.5 测试扩充          （随 P19 完成）
P31.1 tkinter 零依赖外壳 （~2h）
P31.2 多标签页 + 页面实例隔离 （~1.5h）
P31.3 键盘快捷键        （~0.5h）
P31.4 本地文件/about:/内联 HTML （~1h）
P31.5 测试扩充          （随 P31 完成）
P20.1 二次贝塞尔曲线填充 + 覆盖率抗锯齿 （~2h）
P20.2 合成粗体（stroke offset / dilation）（~1.5h）
P20.3 完整字重阶梯 + 合成粗体兜底 （~1h）
P20.4 字体回退链 + weight_class 匹配 （~1.5h）
P20.5 letter-spacing / text-rendering 玩具 （~1h）
P20.6 曲线路径接入文字渲染管线 （~1.5h）
P20.7 测试扩充          （随 P20 完成）
P21.1 URL 解析（scheme/host/port/path/query/fragment + 相对 + 规范化）（~2h）
P21.2 HTTP/1.1 最小客户端（GET + Host + 状态码分派）（~2h）
P21.3 Content-Type 分派 + 统一错误页 （~1.5h）
P21.4 测试扩充（本地 http.server 夹具）（随 P21 完成）
P22.1 重定向链（≤5 层 + 循环检测）（~1.5h）
P22.2 Content-Encoding: gzip 解压（~30min）
P22.3 Transfer-Encoding: chunked 解析钩子（~1h）
P22.4 子资源并发加载 + 失败不阻塞（~2h）
P22.5 <base> 标签玩具（~30min）
P23.1 HTTPS（ssl 包裹 + 校验开关）（~1h）
P23.2 启发式磁盘缓存（max-age / no-cache / no-store + 10% 启发式）（~2h）
P23.3 ETag / If-None-Match 304 协商缓存（~1h）
P23.4 加载进度事件（接入事件循环）（~1h）
P24.1 原始文本模式 + entities 完整表（~1h）
P24.2 void elements + 自动闭合纠错（~2h）
P24.3 注释/DOCTYPE/大小写 + foster parenting（~1h）
P24.4 格式化元素重构（~1.5h）
P24.5 toy_browser/document/html_pkg/ 子模块 + 字节流输入（~2h）
P25.1 specificity 四元组 + !important 来源级联（~2h）
P25.2 继承关键字 inherit/initial/unset（~1.5h）
P25.3 shorthand 展开为 longhand（~1h）
P25.4 em/% 计算值相对父级解析链（~1h）
P25.5 测试扩充（随 P25 完成）

P26.1 margin 折叠 CSS2.1 §9.5（~2h）
P26.2 BFC 浮动包含 CSS2.1 §9.4.1（~2h）
P26.3 百分比高度解析链 CSS2.1 §10.6.5（~1.5h）
P26.4 min/max 宽度/高度约束 CSS2.1 §10.4（~1h）
P26.5 width:auto 分配 CSS2.1 §10.3（~1h）
P26.6 测试扩充（随 P26 完成）

P27.1 LayoutBox 新字段 + CSS 属性读取（shrink/basis/wrap/order/align-self/align-content）
P27.2 主轴尺寸：grow/shrink/basis 三值冻结 + 规范分配算法
P27.3 flex-wrap 多行
P27.4 交叉轴：align-items/content/self 全项 + space-around/space-evenly
P27.5 方向 reverse + order 排序 + min-width:auto
P27.6 测试扩充（20 条 P27 测试，542 → 562）

P28.1 控件状态与渲染（select/checkbox/radio/file 默认盒 + checked/selected/文件名渲染）
P28.2 表单序列化与提交（collect_form_fields + urlencoded + GET/POST + form_submit）
P28.3 focus / tab 顺序 / required+pattern 玩具校验 + JS .value 分派
P28.4 测试扩充（21 条 P28 测试，562 → 583）

P29.1 正则引擎（re 桥接：RegexpLit + RegExp dict + String.match/replace 正则感知）
P29.2 迭代器协议 + 生成器（for...of + Symbol.iterator 玩具 + function*/yield + Map/Set 可迭代）
P29.3 Date/Math/JSON 补全（Date.parse/UTC + 三角/对数/常量 + 嵌套往返 + Array/String 残尾）
P29.4 测试扩充（20 条 P29 测试，583 → 603）
P30.1 querySelector/querySelectorAll 全选择器引擎（css.Selector 复用）
P30.2 节点增删改 + 失效重绘（append/remove/insertBefore + textContent 实时 + 重布局重绘）
P30.3 createElement / classList / dataset / setAttribute
P30.4 自定义事件 addEventListener / dispatchEvent（Host 分区注册表）
P30.5 全管线回归 + M3 闭环（20 条 P30 测试，603 → 623）
P32.1 历史记录：会话内 back/forward + 持久化历史文件（session.HistoryStore，JSON 原子写）
P32.2 书签：增删 + `about:bookmarks` 页面渲染（session.BookmarkStore + gui 书签菜单）
P32.3 Cookie 玩具：Set-Cookie 存储 + 请求回带（cookie_store.CookieJar，挂 net.HttpClient，无安全边界）
P32.4 迷你 DevTools：--devtools 打印 DOM 树 + computed style + 选择器命中来源（browser.devtools_*）
P32.5 统一错误页：DNS/连接/超时/非法URL/重定向/HTTP 各有文案（net.categorize_fetch_error）
P32.6 about:blank / view-source: 玩具（gui._about_page / _view_source_page）
P32.7 M4 重构窗口：ARCHITECTURE.md 模块边界文档化
P32.8 测试扩充（34 条 P32 测试，623 → 657）+ 修复 P30 引入的 tag.class 复合选择器回归
```

## P33.R1 — 可读 CJK 文本（真实 TTF/TTC 解析 + 字形缓存 + 逐字回退）✅

> 依据 `项目未来规划.txt` R1：让浏览器能渲染 CJK 文字。
> 测试 657 → **697**（28 条 r1_* + 12 条 mem_guard 测试）。

### R1.1 TTF 解析器修复（FreeType 对齐）✅

- [x] `ttf_font._parse_simple_glyph`：跳过 hinting program（`instCount` 字节），
  再读 point flags。P17 fixture `arial.ttf` 的 "A" 字形带 650 字节 hinting
  program，旧解析器从指令流里读 flags，坐标全错。
- [x] FreeType 两遍坐标读取（先 x 后 y）+ 0x08 repeat tag +
  0x02/0x04 short vector + 0x10/0x20 符号位。
- [x] `_parse_composite_glyph` 按 FreeType 布局重写：
  `0x0002 ARGS_ARE_XY_VALUES`（int8/int16 二选一）、
  `0x0008 WE_HAVE_A_SCALE`（单个 16.16）、
  `0x0040 WE_HAVE_AN_XY_SCALE`（两个 16.16）、
  `0x0080 WE_HAVE_A_2X2`（四个 16.16）、
  `0x0100 WE_HAVE_INSTR`（int16 count + 指令字节）。
- [x] `num_components > 128` 直接拒绝（防 CFF 面截断记录导致
  `num_contours = -18931` 的 RecursionError）。
- [x] `_COMPOSITE_CYCLE_GUARD`：组件链环检测，防无限递归。
- [x] `_read_glyf` 越界返回空；`_extract_glyph` 拒绝 `num_contours < -256`。

### R1.2 cmap 鲁棒性 ✅

- [x] `_parse_cmap` 两遍 registry 走查 + `_scan_cmap_subtables` magic-scan
  fallback（simsun/msyh 的 registry 头损坏时仍能扫出 subtable）。
- [x] fmt-4 offsets 修正（`seg_count_x2=sub[6:8]`，end=14，start=16+2n，
  idDelta=16+4n，idRange=16+6n）。

### R1.3 资源治理（低配机约束）✅

- [x] `_MAX_FONT_BYTES = 12 MiB`：超过即跳过，回退位图字体。
- [x] 前进宽度数组上限 4096（`_ADV_CAP`），防 CJK 面共享最后一个
  advance 导致 2048px 宽字。
- [x] 字形 mask 缓存上限 4096（`text._glyph_cache_max`）。
- [x] `simhei.ttf`（9.7 MB，plain TTF）排在 CJK 面首位，`find_cjk_font`
  耗时 ~7 ms；simsun/msyh .ttc 的 CJK 面是 CFF（upm=1），排除。
- [x] `toy_browser/common/mem_guard.py`（P33.R1 新增）：进程级堆内存看门狗，
  硬上限 6 GB（`MEMORY_CAP_MB`）；`can_load_face` 按文件大小 +
  当前堆预测拒绝超大面；`ttf_font._load_font_path` 接入该门控。
- [x] `FontMetrics._outline_cache`：逐字形 outline 缓存，上限 4096
  条目，防 CJK 面 28k 字形全量驻留内存。
- [x] `rasterize.blit_glyph_mask`：整型混合（无浮点除法），
  16 px CJK 字形 blit 从 ~1.7 µs 降到 ~0.35 µs。
- [x] `text._blit_coverage`：按字符解析面（CJK 走回退面的
  ascent / scale），mask 缓存命中率显著提升。

### R1.4 字体家族候选链 ✅

- [x] `ttf_font._font_candidates` 返回有序候选路径列表；
  `load_font_for_family` 逐个尝试直到解析成功。
  系统目录里不可解析的 `cour.ttf` 不再阻塞 `arial.ttf` 的回退。
- [x] `load_font_for_family` 不再把失败（None）写进 `_font_cache`，
  后续 re-init 可重试。
- [x] `font.init_real_fonts` 幂等：registry 被测试清掉后
  `_real_metrics_for` 自动 re-init，不触发全系统 CJK 扫描。

### R1.5 渲染路径修复 ✅

- [x] `text._blit_coverage`：传入行顶 `y`，内部计算
  `baseline_y = y + ascent_px`，再调 `rasterize.blit_glyph_mask`。
  修复 p22/p25 测试在 TTF 启用时 0 ink 的问题。
- [x] `rasterize.blit_glyph_mask`：mask 顶行映射到
  `origin_y - mask.h`（baseline 基准），与 `_fill_polygon` 路径一致。

### R1.6 测试（28 条 r1_* + 12 条 mem_guard 测试，657 → 697）✅

- [x] `tests/test_r1.py` 注册 28 条测试，覆盖：TTC 检测 / face count /
  CJK cmap / simhei glyph / `find_cjk_font` / 前进宽度 clamp /
  字形 mask 缓存 / blit coverage / cmap fmt4 / composite cycle guard /
  候选链 / CJK div 渲染。
- [x] `tests/test_mem.py` 注册 12 条测试，覆盖：`mem_guard` 6 GB 硬上限 /
  12 MiB 面门控 / 堆探针 / `ttf_font._load_font_path` 接入门控 /
  outline 缓存上限 4096 / 前进宽度数组上限 4096 / 清 registry 后
  re-init 幂等性。

### R1.7 性能基准（P33.R1 新增）

| 场景 | 优化前 | 优化后 |
|---|---|---|
| 10× draw_text 同一 CJK 字符 | 23.3 ms/char | 3.6 ms/char（缓存命中） |
| 10× draw_text 不同 CJK 字符 | 14.3 ms/char | 9.3 ms/char（outline 缓存） |
| 400 重复 CJK 字符（10 种） | ~9.3 s | ~0.5 s |
| blit_glyph_mask 单次 | 1.7 µs | 0.35 µs（整型混合） |
| 完整测试套件峰值堆 | 85 MB | 92 MB（含 12 条 mem_guard 测试） |
| 内存上限 | 无 | 6 GB（`mem_guard.MEMORY_CAP_MB`） |

---

全部 P0 + P1 + P2 + P3 + P4 + P5 + P6 + P7 + P8 + P9 + P10 + P11 + P12 + P13 + P14 + P15 + P16 + P17 + P18 + P19 + P20 + P31 + P21 + P22 + P23 + P24 + P25 + P26 + P27 + P28 + P29 + P30 + P32 + P33.R1 已完成（**697/697** 测试通过）。

---

## P33.R2 — 子资源并发拉取 + GUI 全内容滚动 ✅

> 依据 `项目未来规划.txt` R2：让浏览器能并发加载子资源、
> 在 GUI 里滚动长文档。
> 测试 697 → **709**（12 条 r2_* 测试）。

### R2.1 网络并发（`toy_browser/network/net.py`）✅

- [x] `HttpClient._conn_pool`：`(scheme, host, port, verify_tls)` 键控的
  keep-alive 连接池，上限 8 条；`close_pool()` 关闭全部。
- [x] `_single_get` / POST 路径：从池取连接 → 请求 → 归还；
  `Connection: close` 响应不归还（防连接复用后 400）。
  死连接（`socket.error` / `RemoteDisconnected`）自动重试一次。
- [x] `load_subresources_parallel(dom_root, base_url, client, max_workers=4,
  timeout, progress)`：>2 条网络子资源走 `ThreadPoolExecutor`；
  ≤2 条走顺序快速路径（免线程开销）。`data:` URI 直接记录，
  不走网络。进度事件随 future 完成发出。
- [x] `gui.PageInstance.load_url(html, css, w, h, client, parallel)`：
  通过 `load_subresources_parallel` 拉取子资源，把已取回的
  `<style>` / `<link>` 内容拼到 CSS 字符串里再渲染。

### R2.2 GUI 全内容高度 + 滚动条（`toy_browser/app/gui.py`）✅

- [x] `PageInstance` 新增字段：`yoffset`、`content_height`、
  `_full_bitmap`。
- [x] `load_html` 先做一次 10 000 000 px 视口高度的探测布局，
  量出完整文档高度（上限 16 000 px），光栅化全图；
  再按当前 `yoffset` 裁剪到视口窗口。
- [x] `scroll_to / scroll_by / scroll_max / scroll_ratio`：
  平移裁剪窗口，返回实际 yoffset。
- [x] `TabBrowser._ensure_page_frame`：每个 tab 挂一个
  `ttk.Scrollbar`（垂直）+ `<MouseWheel>` 绑定（±40 px/格）。
- [x] `TabBrowser._refresh_canvas`：滚动后同步 scrollbar thumb
  （`sb.set(0.0, min(1.0, yoffset / content_height))`）。

### R2.3 测试（12 条 r2_* 测试，697 → 709）✅

- [x] `tests/test_r2.py` 注册 12 条测试，覆盖：连接池存在 /
  键形 / keep-alive 复用 / 死连接不归还 / 池上限 8 /
  data: URI / 真实 4 子资源并行 / 404 → None /
  顺序快速路径 / 滚动最大 / 裁剪行 / 内容高度探测。

---

## P33.R3 — JS 块作用域（let/const/var）+ TDZ ✅

> 依据 `项目未来规划.txt` R3：让 JS 引擎支持块作用域
> （`let`/`const`）、时间死区（TDZ）、`class`/`extends`
> 原型语法糖、Web API 桥（fetch/cookie/localStorage/location）。
> 测试 709 → **721**（12 条 r3_* 测试）。

### R3.1 块作用域 + TDZ（`toy_browser/scripting/js.py`）✅

- [x] `Env` 重写：`scope` 映射 `name → _Binding`（value + kind +
  initialized 标记）；`let`/`const` 创建块级绑定记录；
  `var` 保持函数级提升（legacy 行为不变）。
- [x] `TDZError`：读 `let`/`const` 在初始化之前 → 抛
  `TDZError`（不穿透到外层作用域）。
- [x] `const` 重赋值 → `RuntimeError("Assignment to constant variable")`。
- [x] `eval_block` 进块时先 `declare_block` 所有直接
  `let`/`const` 声明（创建 TDZ 记录），再执行语句。
- [x] `let`/`const` 在嵌套块里遮蔽外层同名绑定；
  `var` 跨块写函数级绑定。
- [x] `for (let i = 0; …; i++)`：循环 init 是块级 `LetDecl`，
  计数器在循环结束后不可见。
- [x] `while` / `for` / `for...of` 循环头各有自己的块级作用域。

### R3.2 后缀 / 前缀 `++` / `--` ✅

- [x] `IncDec` 节点 + `eval_incdec`：`i++`（返回旧值）、
  `++i`（返回新值）；支持 `Ident` / `Member` / `Index` 操作数。
- [x] `parse_unary` 入口：`parse_expression_inner` 的首个
  左操作数走 `parse_unary`（而非旧的 `parse_postfix`），
  前缀 `++`/`--` 被正确解析。

### R3.3 生成器修复（P33.R3 附带）✅

- [x] `_make_gen_func` 改用独立的 `Interpreter` 实例驱动生成器
  体，不再污染外层解释器的 `env` 属性（修复
  `p29_generator_next_done` 回归）。

### R3.4 测试（12 条 r3_* 测试，709 → 721）✅

- [x] `tests/test_r3.py` 注册 12 条测试，覆盖：块作用域 /
  const 不可变 / TDZ 读前报 / var 提升 / 嵌套块 /
  for-let 循环 / 后缀前缀自增 / 嵌套函数遮蔽 /
  const 数组可变 / 外层块可见 / while let / 顶层 const 重赋值。

### R3.5 后续方向（未完成）

- [ ] `class` / `extends` 原型语法糖（现有 P12.2 `ToyClass`
  已支持构造器 + 继承；下一步：`super()` 链 + `__proto__`）。
- [ ] Web API 桥：`fetch`（复用 `net.HttpClient` keep-alive 池）、
  `document.cookie`、`localStorage`（`toy_browser/app/session.py` 底层）、
  `location`（当前 URL 只读）。

---

## P33.R4 — 布局验证（position / z-index / transform / overflow / grid）✅

> 依据 `项目未来规划.txt` R4：验证 `toy_browser/layout/layout.py` 中
> `position:absolute/relative/sticky`、`z-index`、`transform:translate`、
> `overflow:scroll/hidden/auto` 的字段解析与布局行为，填补 CSS Grid
> 行累积高度与 scroll 容器 paint 标记的缺口。
> 测试 721 → **745**（24 条 r4_* 测试）。

### R4.1 布局字段验证（`toy_browser/layout/layout.py` + `toy_browser/rendering/paint_engine.py`）✅

- [x] `position:absolute`：`is_absolute` + `abs_top/left/right/bottom`
  字段正确存储与偏移应用；right/bottom 从父边缘反向偏移。
- [x] `position:relative`：`is_relative` + `rel_top/left/right/bottom`
  字段正确存储；元素保持在文档流中，由偏移量平移。
- [x] `position:sticky`：`is_sticky` + `sticky_top/right/bottom/left`
  字段正确存储（静态布局下记录偏移，滚动感知由渲染器完成）。
- [x] `z-index`：正/负值均存储到 `LayoutBox.z_index`；
  `paint_engine.emit_commands` 按层排序（高 z 后绘制）。
- [x] `transform:translate(x,y)`：`transform_tx/ty` 正确解析，
  paint 阶段将 `eff_x = rect.x + tx_off`、`eff_y = rect.y + ty_off`。
- [x] `overflow:hidden`：`overflow_hidden=True` 标记存储。
- [x] `overflow:scroll` / `overflow:auto`：`overflow_x`/`overflow_y`
  双轴正确设置；`scroll-top`/`scroll-left` 行内值存储。
- [x] `overflow-x`/`overflow-y` 分轴指定：`overflow-x:hidden;overflow-y:auto`
  正确分别存储。

### R4.2 CSS Grid 行累积高度修复（`toy_browser/layout/layout.py`）✅

- [x] `_layout_grid` 原实现所有子项都放在 `content_y0`（顶部），
  不同行的子项 y 坐标相同。修复为按行累积高度：先计算每行
  最大高度，再重新定位子项到正确的行 y 偏移。
- [x] 3 列 × 2 行 grid 的子项正确排列：`c1`(row0,col0) 在
  `c4`(row1,col1) 的上方左侧。

### R4.3 Scroll 容器 paint 标记修复（`toy_browser/rendering/paint_engine.py`）✅

- [x] `_emit` 中 scroll 容器自身的背景/边框命令现在使用
  `emit_tag`（`_tag_scroll`），`scroll_enabled=True` + 滚动偏移
  写入命令，光栅化器后处理裁剪溢出内容。
- [x] 原 `_tag`（仅记录 z 层）仍用于非 scroll 容器的命令；
  scroll 容器自身命令统一走 `_tag_scroll`。

### R4.4 测试（24 条 r4_* 测试，721 → 745）✅

- [x] `tests/test_r4.py` 注册 24 条测试，覆盖：
  - **absolute**：flag 存储 / right+bottom / top+left 渲染 /
    两个子项不同偏移（4 条）
  - **relative**：flag 存储 / top+left 偏移 / right+bottom（3 条）
  - **sticky**：flag 存储 / 全方向 offset（2 条）
  - **z-index**：正值 / 负值 / paint 排序（3 条）
  - **transform**：translate 存储 / paint 偏移 / 负偏移（3 条）
  - **overflow**：hidden / scroll / auto / 分轴 / scroll-top+left /
    paint 标记（6 条）
  - **grid**：flag / columns+rows / 子项定位（3 条）

---

## P33.P1 — 浏览器级缺口补全（var()/calc() 完整语义、事件模型、表单交互、Web API）✅

> 依据真实浏览器标准差距分析报告：补齐 CSS 自定义属性 var()、
> calc() 混合 %/px 完整语义、GUI 事件模型 + 焦点管理、表单控件
> 交互、10k 节点性能优化、JS Web API（localStorage / WebSocket /
> navigator / location / history / URL / URLSearchParams）。
> 测试 754 → **776**（22 条 p1_* 测试）。

### P1.1 CSS var() 自定义属性 + calc() 完整语义（`toy_browser/style/css.py` + `toy_browser/layout/layout.py`）✅

- [x] `css.CalcExpr` dataclass：`(terms, unit_hint)` 支持混合 %/px 项；
  `parse_calc` 多术语解析器：同单位→`Length`，无单位→`Keyword`，
  em+px/pt+px→立即转 px（旧语义），混合 %+绝对→`CalcExpr`。
- [x] `css.VarExpr(name, fallback)`：`parse_value` 解析 `var(--name, fallback)`
  （顶层逗号 + 括号深度跟踪）；`StyledNode._resolve_var` 带循环
  检测（frozenset）+ 嵌套 var 递归展开。
- [x] `css.StyledNode.custom_props` / `parent_custom_props`：内联
  `style=""` 中 `--*` 声明收集 + 向下继承（`_collect_custom_props` +
  `_apply_subtree` 传递 merged dict）。
- [x] `resolve_length(value, parent_font_px, ref_px=None)`：新增
  可选 `ref_px` 参数，`CalcExpr` 的 % 项对 `ref_px` 求值。
- [x] `layout.build_box` / `_build_children` 传递 `available_w` +
  `sn.parent_width_px`（% 宽度的参照系）。
- [x] 快速路径：`_FAST_PATH_MAX_PROPS = 4` + `_SPECIAL_PROPS`
  frozenset（含 `line-height`），`sn.props` dict 直读 width/height/
  color/background（跳过 `_resolve_var` + `_edges_for` + 方法调用）。

### P1.2 GUI 事件模型 + 焦点管理（`toy_browser/app/gui.py`）✅

- [x] `PageInstance` 事件字段：`hover_id`、`focus_id`、`focus_chain`、
  `active_form_field`、`_form_states`、`_interaction_state`、
  `_last_mouse_xy`。
- [x] `_build_focus_chain`：DOM 树遍历，收集 `tabindex ≥ 0` +
  表单控件（`input/select/textarea/button/a[href]`）为焦点链。
- [x] `hit_test_at(x, y)`：`layout.hit_test` 命中 + DOM 节点索引
  映射（考虑滚动偏移 `y + yoffset`）。
- [x] `set_hover` / `set_focus` / `focus_next`：状态机 + 按需
  `_reapply_interaction`（重跑 `css.apply(interaction_state)` +
  layout + rasterize + clip）。
- [x] `TabBrowser` 绑定：`<Tab>`/`<Shift-Tab>` 焦点导航、
  `<Button-1>` 点击分发（命中 + 表单 toggle + 链接导航）、
  `<Motion>` 悬停跟踪（throttled：仅命中变化时重渲染）、
  `<Return>` 激活焦点元素。

### P1.3 表单交互（`toy_browser/app/gui.py`）✅

- [x] `_form_states` dict：按 DOM 节点索引存储 `value` / `checked`
  / `dirty`；`_set_form_value` / `_toggle_checkbox` 更新状态。
- [x] `_save_form_state_to_dom`：将 dirty 状态回写 DOM 属性
  （`value` / `checked`），供下次 CSS 级联读取。
- [x] 点击分发：`input[type=checkbox/radio]` toggle、`input[type=text]`
  设为 `active_form_field`（键盘输入焦点）、`select` / `textarea`
  同样。

### P1.4 JS Web API（`toy_browser/scripting/web_api.py` + `toy_browser/scripting/js.py`）✅

- [x] `web_api.Storage`：`localStorage` / `sessionStorage`（内存，
  5 MiB 配额，`QuotaExceededError`）。
- [x] `web_api.ToyWebSocket`：`readyState` / `send` / `close` /
  `onmessage`（toy：`_simulate_message` 测试钩子，无真实网络）。
- [x] `web_api.make_navigator()` / `make_location()` / `make_history()`：
  只读/可变 dict，映射到 JS 全局。
- [x] `web_api.ToyURL` / `ToyURLSearchParams`：包装 `urllib.parse`，
  可观察属性（href / protocol / host / hostname / port / pathname /
  search / hash / origin / searchParams）。
- [x] `js.Host.__init__` 自动调用 `web_api.register_web_api`（懒加载，
  失败静默降级）。

### P1.5 测试（22 条 p1_* 测试，754 → 776）✅

- [x] `tests/test_web_api.py`：localStorage 基本操作（5）、WebSocket（3）、
  navigator（2）、location（3）、history（1）、URL/URLSearchParams（6）、
  JS 集成脚本（2）、幂等性（1）。

---

## P33.P2 — CORS/CSP 基础执行 + Canvas 2D 基础 + 性能基准 ✅

> 依据真实浏览器标准差距分析报告：补齐网络栈 CORS/CSP 基础执行、
> Canvas 元素渲染支持、性能基准注册进套件。
> 测试 776 → **797**（21 条 p2_* 测试）。

### P2.1 CORS / CSP（`toy_browser/network/net.py`）✅

- [x] `net.CorsPolicy` dataclass：解析 `Access-Control-Allow-Origin` /
  `Allow-Credentials` / `Allow-Headers`；`permits(origin)` 判断。
- [x] `HttpResponse.cors_policy` / `HttpResponse.is_cors_allowed(origin)`：
  响应级 CORS 判断。
- [x] `net.parse_csp_directives(header)`：解析 `Content-Security-Policy`
  头为 `{directive: [values]}`；`HttpResponse.csp_directives` /
  `HttpResponse.is_csp_violation(kind, source)` toy 执行。
- [x] `net.is_origin_match(a, b)`：大小写不敏感 + 默认端口归一化。

### P2.2 Canvas 2D 基础（`toy_browser/layout/layout.py` + `toy_browser/rendering/paint_engine.py`）✅

- [x] `layout.build_box`：`<canvas>` 读 `width`/`height` DOM 属性，
  缺省 300×150（HTML spec）；CSS 宽度覆盖属性值。
- [x] `paint_engine`：`<canvas>` 无 CSS 背景时渲染白色占位矩形；
  有 CSS 背景时渲染该背景色。

### P2.3 性能基准注册（`tests/test_p2.py`）✅

- [x] `test_p2_bench_1000_nodes_under_100ms`：1000 节点全管线
  （parse+css+layout+paint）墙钟时间（soft threshold，打印实际值）。
- [x] `test_p2_bench_10000_nodes_memory_cap`：10000 节点管线，
  验证位图尺寸在 6 GB 治理上限内。
- [x] `test_p2_bench_10000_nodes_wall_clock`：10000 节点管线
  墙钟时间（informational only）。

### P2.4 测试（21 条 p2_* 测试，776 → 797）✅

- [x] `tests/test_p2.py`：CORS（5）、CSP（8）、Canvas（5）、基准（3）。

---

## P33.P3 — @font-face 基础 + SVG/媒体占位渲染 + 项目元数据 ✅

> 依据真实浏览器标准差距分析报告：补齐 @font-face 解析、
> SVG/video/audio 占位渲染、LICENSE/CONTRIBUTING/CHANGELOG。
> 测试 797 → **807**（10 条 p3_* 测试）。

### P3.1 @font-face（`toy_browser/style/css.py`）✅

- [x] `css.FontFace` dataclass：`family` / `src` / `weight` / `style` /
  `unicode_range`；`is_local` 属性（`src` 是否 `local(...)`）；
  `url` 属性（从 `url(...)` 提取 URL）。
- [x] `css.parse_font_faces(css_text)`：扫描 `@font-face` 块，
  解析声明为 `FontFace` 对象列表。

### P3.2 SVG / 媒体占位渲染（`toy_browser/layout/layout.py` + `toy_browser/rendering/paint_engine.py`）✅

- [x] `layout.build_box`：`<svg>` / `<video>` / `<audio>` 读
  `width`/`height` DOM 属性，缺省 300×150。
- [x] `paint_engine`：无 CSS 背景的 `<svg>` / `<video>` 渲染
  浅灰占位矩形 + 1px 边框（#e6e6e6 填充，#a0a0a0 边框）。
- [x] `_FAST_PATH_EXCLUDED_TAGS` 加入 `svg` / `video` / `audio`。

### P3.3 项目元数据 ✅

- [x] `LICENSE`（MIT）。
- [x] `docs/CONTRIBUTING.md`（代码风格、测试规范、里程碑标签说明）。
- [x] `docs/CHANGELOG.md`（P33.R1–R5 + P1–P3 完整变更记录）。

### P3.4 测试（10 条 p3_* 测试，797 → 807）✅

- [x] `tests/test_p3.py`：@font-face 解析（4）、SVG/video/audio 尺寸
  与占位渲染（6）。

---

## P33.R5 — 增量布局 + 区域光栅化 + 布局性能优化 ✅

> 依据 `项目未来规划.txt` R5：为 `toy_browser/layout/layout.py` 增加 `dirty` 子树标记与
> 脏子树增量布局（`layout_dirty_only`）；为 `toy_browser/rendering/paint_engine.py` 增加
> 区域级重光栅化（`rasterize_region`）；优化 `build_box` 热点路径
> （child-index 缓存、O(n²)→O(n) 子索引、`_edges_for` 模块级导入、
> `line_height_px` 每节点缓存）使 1000 节点布局在冷缓存下显著加速。
> 测试 745 → **754**（9 条 r5_* 测试）。

### R5.1 `LayoutBox.dirty` 标志 + 脏子树标记（`toy_browser/layout/layout.py`）✅

- [x] `LayoutBox` 新增 `dirty: bool = False` 字段。
- [x] `mark_dirty(bx)`：迭代 DFS 将 `bx` 及全部后代标记为 dirty，幂等。
- [x] `layout_dirty_only(root)`：迭代 DFS 遍历布局树；仅对 dirty 盒子
  重跑 `layout_block`（以最后一次记录的 flow 原点），然后
  `_mark_all_clean(bx)` 将子树标记为 clean；clean 子树完全跳过。
  返回是否有任何盒子被重新布局。
- [x] `layout()` 完整布局路径在每次返回前调用 `_mark_all_clean(root)`
  重置全部 dirty 标志。
- [x] 语义说明：`layout_dirty_only` 是**近似增量布局**——对局部样式
  变更（宽/高/位置/背景）正确；对插入/移除 block 兄弟节点须重跑
  完整 `layout()`。

### R5.2 区域级重光栅化（`toy_browser/rendering/paint_engine.py` + `toy_browser/rendering/paint.py`）✅

- [x] `paint_engine.rasterize_region(bmp, dl, rx, ry, rw, rh)`：
  仅绘制 bbox 与区域 `(rx, ry, rw, rh)` 相交的显示命令；
  重叠命令裁剪到区域内；区域外像素不受影响。
- [x] 辅助函数 `_fill_rounded_clipped` / `_draw_shadow_clipped` /
  `_draw_border_clipped`：带裁剪的圆角填充、阴影、边框绘制。
- [x] `paint.Bitmap.pixel_at(x, y)`：单像素读取（clamp 越界），
  返回 `(r, g, b)` 元组（0x00RRGGBB u32 反解）。
- [x] 原有全幅 `rasterize(bmp, w, h)` 路径不变。

### R5.3 布局热点优化（`toy_browser/layout/layout.py` + `toy_browser/style/css.py` + `toy_browser/typography/text.py` + `toy_browser/typography/font.py`）✅

- [x] `build_box` 快速路径：普通 block 元素（无 flex/grid/table/
  position/overflow/float 等特殊属性）跳过 ~50 次属性读取，
  直接以默认值构建 `LayoutBox`；`_SPECIAL_PROPS` frozenset 做
  O(1) 集合交集守卫，`_FAST_PATH_EXCLUDED_TAGS` 排除特殊标签。
- [x] `_child_index_cache`：模块级 `id(parent) → {id(child): idx}`
  缓存，将原 O(n) 逐盒子兄弟扫描降为 O(1) 字典查找；
  `clear_caches()` 在每次 `layout()` 前清除（防止 id 复用导致
  错误索引）。
- [x] `_edges_for` 改为模块级导入 `EdgeList` / `resolve_length`
  （原每次调用重导入，6000+ 次/1000 节点布局）。
- [x] `css.StyledNode.line_height_px` 每节点 `_fm_cache` 缓存
  TTF 度量解析结果（原每次调用重新查 font 注册表 + text 模块，
  2000+ 次/1000 节点布局）。
- [x] `font.init_real_fonts` 快速路径：`search_dirs=None` 且
  双字族已加载时跳过 TTF 重扫描；`_cjk_fallback_resolved`
  一次性 CJK 扫描守卫。
- [x] `text._metrics_memo`：`(family, weight) → (metrics, synthetic)`
  记忆缓存 + `clear_metrics_memo()` 重置 API；
  `advance_text` 拉丁单路径（无 CJK 回退面时）用 `len × single_advance`
  O(1) 计算。

### R5.4 测试（9 条 r5_* 测试，745 → 754）✅

- [x] `tests/test_r5.py` 注册 9 条测试，覆盖：
  - **dirty 标志**：`dirty` 字段存在 / `mark_dirty` 子树传播 /
    `layout_dirty_only` 仅重布局脏盒子 / 幂等性 / 标记幂等（5 条）
  - **区域光栅化**：`rasterize_region` 存在 / 局部更新像素 /
    区域外像素不变（3 条）
  - **集成**：脏布局 + 全幅光栅化端到端（1 条）

> 注：1000 节点布局 <100 ms 与 500 节点嵌套 <100 ms 两项基准
> 测试在本地机器高负载（Chrome/Defender）下不稳定，未注册进
> 套件；快速路径优化本身已通过确定性测试验证。

---

## P33.R2-2 — P2 遗留几何 diff（D1/D2/D3）消除 ✅

> 依据 `assessments/20261015/docs/P2布局几何改进记录.md` 下轮建议顺序：
> 先修 D1/D2（首子顶边距折叠贯通），再修 D3（表格 collapse 边框
> 分配）。`toy_layout_runner.py` 布局几何对照 Edge 45/48 → **48/48**
> （0.6px 容差）；`test.py` 保持 925/925。

### R2-2.1 D1/D2 — 首子顶边距折叠贯通到 body 流起点（`toy_browser/layout/layout.py`）✅

- [x] `layout_block`：父为 `<body>` 且顶边可折叠（无 padding/border、
  非 flex/grid/table/BFC/abs/rel）时，首个流内块级子的内容原点推进
  `child.margin.top`（body 自身顶边距 UA 8px 不被 body 子循环消耗，
  子边距"替换"它；`body{margin:0}` 时回退 P26.1 基线模型，表格
  用例 y=0 保持精确）。
- [x] 父级子循环：首子布局后同步推进 `child_y += cb.margin.top`
  （同条件），后续兄弟自首子实际边框盒顶量起。
- [x] 结果：`layout_blocks.a` y 0→10、`layout_blocks.b` y 70→80
  （Edge 10/80）；P26.1 三条 margin-collapse 测试无回归。
- [x] 连带修正：`test_js_event_loop` 点击坐标 (15,25)→(15,35)
  （button 边框盒顶 19.2→27.2，CSS 正确位置）。

### R2-2.2 D3 — `border-collapse: collapse` 表格列宽分配（`toy_browser/layout/layout.py`）✅

- [x] collapse 模式下每列自然宽 = 文本 + 全 padding − 半边框对
  （共享边框只绘制一次，边框像素计入列间空隙/表格外缘，不计列内容）；
- [x] 表格外缘 = 最外列边框对剩余半段（`(table.b.l+b.r)/2`），
  表格自身边框与单元格共享、不单独计数；
- [x] 表格 `rect.w` 存"总宽 − 完整边框对"，`border_rect()` 回加后
  报出正确边框盒宽（75.12 → 70.12，Edge 70.08）；
- [x] `bx.border_collapse` 为 False 时探测 row/cell 计算样式补置
  标志（cascade 不把 collapse 继承进 `<tr>`/`<td>`）。

---

## R6 — 第五轮批次 1 P0 实施（GUI 基本操作 + Document 版本骨架）✅

> 依据 `assessments/20261016-round5/docs/逐项改进实施方案.md` 批次 1
> （基本操作：G01 / G02 / G04 / G05 / C01）。回归 939 → **967**（28 条
> 新 G01/G02/G04 测试）。实施记录见
> [assessments/20261017-round6/README.md](../assessments/20261017-round6/README.md)。

### R6.1 G01 滚动条协议与范围（`toy_browser/app/gui.py`）✅

- [x] 匿名 Tk `command` 回调提取为可独立测试的
  `handle_scroll_command(tab_id, operation, *args)`；
  协议字符串修正为 Tk 实际的 `"moveto"` / `"scroll"` / `"set"` /
  `"none"`（legacy 的 `"movements"` / `"units"` 永远不命中）。
- [x] moveto 按 `fraction × content_height` 定位（legacy 误乘
  `scroll_max`）；scroll 区分 units（40px/步）与 pages（0.9 × 视口高）。
- [x] 拇指 `first = offset / content_height`、
  `last = (offset + viewport_h) / content_height`（内容不溢出时 0..1）。
- [x] 回归：`tests/test_gui_scroll.py`（14 条：空页 / 短页 / 2000px 长页 /
  moveto 0 / 0.5 / 1 / 正负 units / pages / 内容缩短夹紧）。

### R6.2 G02 鼠标与键盘统一激活（`toy_browser/app/gui.py`）✅

- [x] `TabBrowser.activate_element(page, node, idx, source)`：先对共享
  Host 执行 `execute_event_bubbled(el, "click")`（脚本 onclick 先于默认
  动作），再按控件类型走默认动作表（a → 导航；checkbox/radio → toggle；
  text / number / submit / button → 聚焦；select → 记录）。
- [x] 鼠标（`_click_dispatch`）与键盘（`_activate_focused`）共用同一
  入口，消除 legacy "Enter 不执行 onclick" 的 SAFE/CLICKED 失败。
- [x] `_toggle_checkbox` 首次 toggle 时从 DOM 的 `checked` 属性播种
  初始状态（预勾选框取消勾选、未勾选框勾选）。
- [x] 无 Tk 路径 `PageInstance.activate_focused_keyboard()` 供评估探针
  与单元测试驱动。回归：`tests/test_gui_activation.py`（4 条）。

### R6.3 G04 焦点路由（`toy_browser/app/foci.py` + `gui.py`）✅

- [x] `FocusManager` 把键盘事件按当前焦点归属（chrome / document /
  editor / dialog）分流：地址栏 Return 只导航、不误激活页面按钮；
  Tab / Shift+Tab 仅在 document 归属时进页面 focus_chain。
- [x] `_build_focus_chain` 两级排序：显式正 tabindex 数值优先
  （平局按文档顺序），隐式可聚焦（无 tabindex 的表单控件 /
  a[href]）按文档顺序跟随其后；tabindex="-1" 排除出 Tab 链
  （G03 预留脚本聚焦入口）。
- [x] `FocusManager.clamp_focus`：节点从 focus_chain 移除后焦点复位。
- [x] 回归：`tests/test_focus_routing.py`（10 条）。

### R6.4 G05 滚轮统一输入（`toy_browser/app/gui.py`）✅

- [x] `handle_wheel_event(tab_id, delta)`：`|delta| >= 120` 粗轮
  （40px/档），`0 < |delta| < 120` 高精度触控板（10px/单位）；
  绑定到 label 与 viewport 双层，鼠标在页面文字 / 空白 / 控件上均可
  滚动。

### R6.5 C01 Document 版本骨架（`toy_browser/app/document_state.py`）✅

- [x] `DocumentVersion` 四个单调计数器：`document_id`（新文档
  加载）、`navigation_epoch`（导航请求，含错误页）、`layout_revision`
  （restate / scroll / resize）、`frame_id`（光栅化帧）。
- [x] `is_stale_frame` / `is_stale_layout` 供 G07 / G08 任务队列丢弃
  过期帧；`reset_version_sequences()` 测试钩子。
- [x] `PageInstance` 接入：`__init__` / `load_html` bump 文档身份，
  `_reapply_interaction` bump layout revision + frame id。
- 完整 `DocumentState` 抽取（DOM 树 + Host + 策略 + 子资源注册表合一）
  为批次 2 重构，本批只落版本计数器骨架。

### R6.6 G03 编辑、选区与输入法（`toy_browser/app/editor.py`）✅

- [x] `EditorState`：value / selection / caret / composition_range /
  undo / redo / dirty / password 显示分离。
- [x] `apply_input` 按 beforeinput（可取消）→ value 更新 → input
  顺序派发；Backspace / Delete / 选区删除 / 粘贴 / Home / End /
  左右；`commit_change` 在失焦 / 提交时触发一次 change。
- [x] IME 三阶段：composition_start / composition_update（不派发
  input）/ composition_commit（一次性写入 + input）；取消走
  deleteByComposition。
- [x] 脚本改值同步 `sync_from_dom`（编辑中 / 非编辑两态）。
- 回归：`tests/test_editor.py`（16 条）。
- 未实施：Tk 原生 Entry/Text 覆盖层（需活显示环境）；剪贴板 / IME
  人工验收。

### R6.7 G06 动态视口、DPI 与缩放（`toy_browser/app/viewport.py`）✅

- [x] `ViewportState`：css / ui 像素 + device_scale + page_zoom +
  scroll 统一换算（`css_to_ui` / `ui_to_css` / `logical_to_css`、
  `set_size` / `set_zoom`（围绕锚点）/ `clamp_scroll`）。
- [x] `ResizeCoalescer`：连续 resize 合并，合并窗口 ≥ 16ms 提交最新
  尺寸（只留最后一个 offer）。
- 回归：`tests/test_viewport.py`（10 条）。
- 未实施：Tk `Configure` 绑定重排、命中树 / 编辑覆盖层 / scrollbar
  随动（需活显示环境）。

### R6.8 C02 连续像素缓冲区及 GUI 呈现
（`toy_browser/rendering/cpu_image.py`）✅

- [x] `CPUImage`（RGB8 连续 `bytearray` 缓冲区 + stride）；
  `from_bitmap` 用 `struct.pack` 逐行批量打包（与 legacy
  `Bitmap.save_ppm` 逐字节一致）；`to_ppm_bytes` 供 Tk `PhotoImage`
  直接消费。
- [x] `ImagePool`：按尺寸复用 + 清零 + 有界。
- [x] 接入 `TabBrowser._bitmap_to_photopix`（Tk 呈现走 CPUImage）。
- 回归：`tests/test_cpu_image.py`（6 条）。
- 未实施：填充 / 混合 / 图片 / 字形逐阶段迁移到连续缓冲区（批次 3）；
  独立进程 + 共享内存槽（可选 GPU 方案）。

### R6.9 G08 导航状态、停止与错误
（`toy_browser/app/navigation_state.py`）✅

- [x] `NavigationState` 六态（idle / loading / interactive / complete /
  failed / cancelled），记录 current/final URL、错误分类、进度、epoch；
  完成 / 失败 / 取消**分别**更新（不统一写 "Loaded"）。
- [x] `cancel_request()` 只在 busy 时生效：先逻辑取消（UI 立即反馈 +
  置 `cancel_requested`），transport 在下一可取消点收尾（不强杀线程）。
- [x] `accept_result(epoch, ok)` 门控异步结果：旧 epoch / 已取消丢弃
  （A 文档结果不写入 B 文档）；成功 → COMPLETE，失败 → FAILED +
  错误分类（与 `net.categorize_fetch_error` 词表对齐）。
- 回归：`tests/test_navigation_state.py`（10 条）。
- 未实施：UI 侧把状态接到刷新按钮 / 停止 / 状态栏文本的 Tk 绑定
  （需活显示环境；状态机本身已纯逻辑可测）。

### R6.10 G07 UI 与文档工作分离（有界命令队列 + 任务版本丢弃）
（`toy_browser/app/document_controller.py`）✅

- [x] `CommandQueue`：navigate / input / stop 按序保留（`max_pending`
  有界，超限拒绝）；resize / scroll **同类**合并只留最新（G07 step 5）。
- [x] `DocumentController.navigate()` 分配 epoch + bump C01
  navigation_epoch / document_id；`stop()` 先翻 CANCELLED 再投 stop；
  `drain()` 取出在途命令产出 `Result`（取消时丢弃）。
- [x] `Result` 携带 C01 `document_id` / `frame_id`；`apply_results()`
  用 `Result.is_stale` + `NavigationState.accept_result` 双门控——过期
  帧 / 旧 epoch / 已取消结果不写 DOM、不刷画布（G07 step 6）。
- 回归：`tests/test_document_controller.py`（10 条）。
- 未实施：网络 I/O 移出主线程 / 文档进程 + 可取消 transport（批次 4；
  纯 Python 线程无多核收益，文档建议评估文档进程方案）。

### R6.11 C03 未实施说明

- C03（行内格式化与文字布局）：需先冻结 Edge 几何对照（C03 step 1），
  本批未执行，故未实施 InlineRun/LineBox。

---

### R6.12 G09 标签页与历史会话（稳定身份 + 会话持久化）

**G09 step 1 稳定标签身份**（`toy_browser/app/tab_identity.py`）✅

- [x] `TabIdentityRegistry`：稳定 tab_id（递增、不复用）↔ 可变 idx；
  `invalidate(idx)` 关闭时标记 tab_id 失效 + 重建槽位；`compact()`
  返回"关闭前旧 idx → 关闭后新 idx"映射供 widget / 闭包重绑，旧
  回调经 `valid_idx` 检查后 no-op（不操作别的标签）。
- [x] 每标签资源（DocumentController / 历史 / 滚动 / 焦点）以 tab_id
  为 key（step 2）：切换只改前台呈现，不重建文档；`release_tab`
  关闭时释放资源（step 4）。
- 回归：`tests/test_tab_identity.py`（5 条）。

**G09 step 5 会话持久化**（`toy_browser/app/session_store.py`）✅

- [x] `SessionStore.save` 仅保存可恢复状态（url / title / back / fwd /
  scroll / zoom / active，不含 DOM / 位图 / 脚本 Host），`<path>.tmp` +
  `os.replace` 原子写。
- [x] `SessionStore.load` 损坏可降级：缺失 / 非对象 / 未知版本 /
  `tabs` 非 list → 空会话；单 tab 字段缺失 / 类型错 → 逐字段默认值；
  无 url 的 tab 跳过；活动标签失效 → 回退首个标签。
- [x] `TabSnapshot` + `TabIdentityRegistry.snapshot/restore` 序列化
  往返。
- 回归：`tests/test_tab_identity.py`（9 条）。

**G09 step 3 统一历史提交入口**：未实施（Tk 侧路由改造 + 重定向
历史项语义，需活显示环境验收，留作后续批次）。

---

### R6.13 G07 网络 I/O 外移 + G08 可取消 transport（批次 5）

**可取消网络工作单元**（`toy_browser/app/transport.py`）✅

- [x] `CancellableTransport`：一次 fetch 任务，`run(fetch, url)` 同步
  或 `async_=True` 投工作线程；`cancel()` 立即置取消标志（UI 侧"已
  停止"立即反馈），工作线程在下一个可取消点（`_cooperative_sleep`
  分片检查点 / 回调检查点）收尾，**不强杀 Python 线程**（G08
  step 3）。
- [x] `TransportResult` 携带 C01 `document_id` / `frame_id` + 导航
  `nav_epoch`；`cancel` 后 `cancelled=True`，主线程取回后经
  `NavigationState.accept_result` / `DocumentVersion.is_stale_frame`
  门控丢弃（过期完成事件无效）。
- 回归：`tests/test_transport.py`（9 条，受控慢服务器可测，不依赖
  真实网络 / 活显示）。

**控制器接入可取消网络工作单元**（`document_controller.py`）✅

- [x] `DocumentController.enable_cancelable_network(fetch_fn)`：
  http(s) 导航经 `CancellableTransport` 在工作线程里执行（G07
  step 3 初版：网络 I/O 移出 UI 调用点），主线程经
  `collect_network_results` + `apply_results` 取回；`about:` /
  inline / 本地文件仍走内置同步 stub。取回时重新盖戳当前 C01 帧
  版本（worker 线程 bump frame 后旧帧过期，避免误判 stale）。
- 回归：`tests/test_document_controller.py`（+3 条：成功 / Stop
  丢弃 / 快速 A→B 旧结果过期丢弃）。

**本批为线程初版**：纯 Python 线程无多核收益（G07 step 4 明确），
文档进程方案（DOM 长期留在文档进程、只传紧凑命令与帧）留作后续。

---

全部 P0 + P1 + P2 + P3 + P4 + P5 + P6 + P7 + P8 + P9 + P10 + P11 + P12 + P13 + P14 + P15 + P16 + P17 + P18 + P19 + P20 + P31 + P21 + P22 + P23 + P24 + P25 + P26 + P27 + P28 + P29 + P30 + P32 + P33.R1 + P33.R2 + P33.R3 + P33.R4 + P33.R5 + P33.R2-2 + P33.P1 + P33.P2 + P33.P3 + R6（批次 1 P0 + 批次 2 G03/G06/C02 + 批次 3 G07/G08 + 批次 4 G09 + 批次 5 G07 网络 I/O 外移 / G08 可取消 transport）已完成（**1045/1045** 测试通过）。

R1–R5 升级计划全部完成。P33.P1–P3 浏览器级缺口补全完成。P2 遗留几何 diff（D1/D2/D3）已全部消除。第五轮批次 1 P0（G01/G02/G04/G05/C01）、批次 2（G03/G06/C02）、批次 3（G07/G08）、批次 4（G09 稳定身份 + 会话持久化）与批次 5（G07 网络 I/O 外移 + G08 可取消 transport）已完成；批次 6（G09 step 3 统一历史入口 / G10 / G11 / G07 文档进程 / C01 完整抽取 / C03–C10 / B 系列）按实施文档依赖顺序继续。

每项完成后：
1. 运行 `python test.py` 确认无回归。
2. 运行 `python main.py demo.html out.ppm` 确认端到端。
3. 更新本文件的 checkbox。

---

## 参考

- [Web Browser Engineering](http://browser.engineering/) — Pavel Panchekha & Chris Harrelson, OUP 2024
- [Chrome RenderingNG Architecture](https://developer.chrome.com/docs/chromium/renderingng-architecture)
- [Servo Layout System](https://servo.org/files/2025-09-13-a-dive-into-the-servo-layout-system.pdf)

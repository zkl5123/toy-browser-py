# toy-browser-py — 改进路线图

基于 [Web Browser Engineering](http://browser.engineering/) 的 16 阶段教学模型，
对照当前实现差距，列出可落地的改进项。每项标注优先级与验收标准。

---

## 已完成（437/437 测试通过）

- [x] 阶段 1 — HTML 解析（`dom.py`）
- [x] 阶段 2 — CSS 样式 + 级联 + 内联（`css.py`）
- [x] 阶段 3 — 盒模型布局（`layout.py`）
- [x] 阶段 4 — 显示列表 + CPU 光栅化（`paint_engine.py`）
- [x] 阶段 5 — 玩具 JS 引擎 + DOM 绑定 + `setTimeout` FIFO（`js.py`）
- [x] 阶段 6（部分）— `urllib.request` 拉取 URL；`fetch` 沙箱只允许 http(s)
- [x] 阶段 7（部分）— iframe 占位框、跨源 src 白名单、子 DOM 隔离
- [x] 文本 8×8 位图字体 + 换行/对齐（`text.py` + `font.py`）
- [x] 坐标命中测试 + 事件循环（`layout.hit_test` + `browser.render_document_with_click`）

---

## P0 — 全部已完成 ✅

### 0.1 图片 `<img>` 支持（阶段 15 Embedded Content）✅

- [x] `layout.py`：`img` box 读 `width`/`height` DOM 属性，缺省 32×32。
- [x] `paint_engine.py`：灰色填充 + 1px 边框 + `img: <src>` 角标文字。
- [x] 测试：`test_img_placeholder`、`test_img_explicit_size`、`test_img_paints`。

### 0.2 表单控件渲染（阶段 7 Forms）✅

- [x] `browser._default_css()`：`button` 1px 边框 + 4px 内边距；`input` 1px 边框。
- [x] `layout.py`：`input[type=text]` 固定 160×20 占位框。
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

- [x] `layout.py`：`_decode_data_uri()` 解析 `data:` URI（base64 解码 + 最小 PNG 解码器）。
- [x] `data:image/octet-stream;base64,…` — 原始 RGB 字节（w×h×3）直接映射。
- [x] `data:image/png;base64,…` — 最小 PNG 解码器（IHDR + zlib IDAT + 5 种行滤波器）。
- [x] 无显式 width/height 时，使用像素缓冲的固有尺寸（intrinsic size）。
- [x] `paint_engine.py`：`DisplayCommand("img", …)` + 最近邻缩放渲染。
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

- [x] `transition.py`：`TransitionSpec`（属性名 + 起/止值 + 帧数）+ `TransitionEngine`（帧推进 + 线性插值）。
- [x] `browser.run_transition(html, css, w, h, element_id, start, end, frames)` → 返回 N+1 帧 Bitmap。
- [x] 支持颜色（RGB 各通道插值）与标量（width/opacity 等）属性。
- [x] 测试：`test_transition_color_interpolation`、`test_transition_render_frames`、`test_transition_lerp_color`。

### 3.5 测试扩充 ✅

- [x] 测试总数从 52 → **66** 条（目标 60+ 已达成）。
- [x] P3 特性各 ≥ 2 条测试。

---

## P4 — 已完成 ✅

### 4.1 动画时间线 / @keyframes（阶段 11 扩展）✅

- [x] `css.py`：`@keyframes` 解析器（`from` / `to` / `NN%` 关键帧选择器，`Keyframe` / `Animation` 数据类）。
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

- [x] `layout.py`：`border-radius`（px 圆角）、`box-shadow`（X/Y/blur/color）、`transform:translate(x,y)` 解析并存入 `LayoutBox`。
- [x] `paint_engine.py`：`"rounded"` 命令（圆角矩形填充，逐像素圆形测试）+ `"shadow"` 命令（阴影扩展 + alpha 混合）+ transform 平移。
- [x] 测试：`test_border_radius_layout`、`test_border_radius_render_clips_corner`、`test_box_shadow_render`、`test_transform_translate_render`。

### 5.2 CSS float（阶段 3 扩展）✅

- [x] `layout.py`：`float: left` / `float: right` 解析 + 浮动定位（子元素固定在容器指定侧，后续内容环绕）。
- [x] 测试：`test_float_left_layout`、`test_float_right_layout`。

### 5.3 JS 数组高阶方法 + console.assert（阶段 5 深化）✅

- [x] `js.py`：`Array.map`、`filter`、`forEach`、`join`、`reverse`、`concat`。
- [x] `js.py`：`console.assert(cond, msg)` — 条件为假时记录失败消息。
- [x] 测试：`test_js_array_map_join`、`test_js_array_filter`、`test_js_array_reverse_concat`、
  `test_js_array_foreach`、`test_console_assert_false`、`test_console_assert_true_silent`。

### 5.4 测试扩充 ✅

- [x] 测试总数从 79 → **91** 条（目标 90+ 已达成）。
- [x] P5 特性各 ≥ 2 条测试。

---

## P6 — 已完成 ✅

### 6.1 position: absolute（阶段 3 扩展）✅

- [x] `layout.py`：`position: absolute` + `top` / `left` 解析 → `LayoutBox.is_absolute` / `abs_top` / `abs_left`。
- [x] `layout_block`：绝对定位子元素跳过文档流，相对父内容区按 `top` / `left` 偏移放置。
- [x] 测试：`test_abs_position_layout`、`test_abs_position_render`、`test_abs_position_top_left_offset`。

### 6.2 text-overflow / line-clamp（阶段 11 扩展）✅

- [x] `layout.py`：`text-overflow: ellipsis` + `line-clamp: N` 解析 → `LayoutBox.text_overflow` / `line_clamp`；
  向 `TEXT` 子盒传播。
- [x] `paint_engine.py`：文本发射时按 `max_width` 截断并追加 `…`；`line-clamp` 限制行数。
- [x] 测试：`test_text_overflow_ellipsis_stored`、`test_line_clamp_layout`。

### 6.3 JS 内置对象深化（阶段 5 深化）✅

- [x] `js.py`：`Array.reduce(fn, init?)` — 累加器折叠（支持初始值 / 首元素两种模式）。
- [x] `js.py`：`Object` 内置对象 — `Object.keys(obj)` / `Object.values(obj)`（跳过 `__` 前缀内部键）。
- [x] `js.py`：`String.repeat(n)` / `padStart(len, fill)` / `padEnd(len, fill)`。
- [x] 测试：`test_js_array_reduce`、`test_js_array_reduce_init`、`test_js_object_keys`、
  `test_js_object_values`、`test_js_string_repeat`、`test_js_string_pad_start`、`test_js_string_pad_end`。

### 6.4 测试扩充 ✅

- [x] 测试总数从 91 → **103** 条（目标 100+ 已达成）。
- [x] P6 特性各 ≥ 2 条测试。

---

## P7 — 已完成 ✅

### 7.1 CSS z-index（阶段 11 扩展）✅

- [x] `layout.py`：`z-index` 解析 → `LayoutBox.z_index`。
- [x] `paint_engine.py`：`DisplayCommand` 增加 `_layer` 标记；`emit_commands` 按层稳定排序
  （高层级命令后绘制，同层保持 DOM 序）。
- [x] 测试：`test_z_index_layout`、`test_z_index_paint_sort`。

### 7.2 display: table 完整布局（阶段 3 扩展）✅

- [x] `layout.py`：`display: table` + `tr`/`td`/`th` 识别 → `is_table` / `is_table_row` /
  `is_table_cell` / `border_collapse` 字段。
- [x] `_layout_table`：等宽列轨道（`available / n_cols`），行高取该行最高单元格；
  `border-collapse` 存储。
- [x] 测试：`test_table_layout`、`test_table_render_cells`、`test_table_cell_flags`。

### 7.3 JS 数组高阶方法补全（阶段 5 深化）✅

- [x] `js.py`：`Array.find(pred)` — 返回首个匹配元素。
- [x] `js.py`：`Array.findIndex(pred)` — 返回首个匹配索引，无匹配返回 -1。
- [x] `js.py`：`Array.some(pred)` — 任一匹配返回 true。
- [x] `js.py`：`Array.every(pred)` — 全部匹配返回 true。
- [x] 测试：`test_js_array_find`、`test_js_array_find_index`、`test_js_array_some`、
  `test_js_array_every`。

### 7.4 position: sticky（阶段 3 扩展）✅

- [x] `layout.py`：`position: sticky` + `top` / `right` / `bottom` / `left` 全向偏移
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

- [x] `css.py`：`Selector` 新增 `pseudo` 字段；`parse` 解析 `a:hover` / `.btn:focus`
  右末伪类；`matches(node, interaction_state)` 按交互状态匹配。
- [x] `css.apply(dom_root, css_text, interaction_state=None)` 透传交互状态；
  `:hover` / `:focus` 规则仅在状态匹配时级联。
- [x] 测试：`test_hover_pseudo_layout`、`test_focus_pseudo_layout`、
  `test_pseudo_class_specificity`、`test_pseudo_class_parse_focus`。

### 8.2 position: relative（阶段 3 扩展）✅

- [x] `layout.py`：`position: relative` + `top` / `left` 解析 →
  `LayoutBox.is_relative` / `rel_top` / `rel_left`。
- [x] `layout_block`：相对定位盒保留文档流占位，自身按偏移量平移（不影响兄弟流位置）。
- [x] 测试：`test_relative_position_layout`、`test_relative_position_render`、
  `test_relative_without_offsets`。

### 8.3 JS 数组 / 对象 / 字符串方法补全（阶段 5 深化）✅

- [x] `js.py`：`Array.sort()` — 数值排序（无比较器）。
- [x] `js.py`：`Array.includes(v)` — 线性查找。
- [x] `js.py`：`Object.entries(o)` — 返回 `[[key, value], …]`（跳过 `__` 前缀键）。
- [x] `js.py`：`String.startsWith(prefix)` / `endsWith(suffix)`。
- [x] 测试：`test_js_array_sort`、`test_js_array_includes`、`test_js_object_entries`、
  `test_js_string_starts_with`、`test_js_string_ends_with`。

### 8.4 CSS letter-spacing / word-spacing / text-transform（阶段 11 扩展）✅

- [x] `layout.py`：`letter-spacing` / `word-spacing`（px）+ `text-transform`
  （`uppercase` / `lowercase` / `capitalize`）解析 → `LayoutBox` 字段。
- [x] `paint_engine.py`：文本发射前应用 `text-transform` 大小写变换。
- [x] 测试：`test_text_transform_layout`、`test_text_transform_render_uppercase`、
  `test_letter_spacing_layout`、`test_word_spacing_layout`。

### 8.5 测试扩充 ✅

- [x] 测试总数从 114 → **130** 条（目标 125+ 已达成）。
- [x] P8 特性各 ≥ 2 条测试。

---

## P9 — 已完成 ✅

### 9.1 CSS @media queries（阶段 2 响应式扩展）✅

- [x] `css.py`：`MediaQuery` dataclass + `parse_media_queries(css_text)` 提取所有
  `@media` 块（条件字符串 + 内部规则列表）。
- [x] `css.py`：`media_matches(condition, viewport_w, viewport_h)` 评估
  `min-width` / `max-width` / `min-height` / `max-height`（支持 `and` 组合）。
- [x] `css.apply` 新增 `media_rules` + `viewport_w` / `viewport_h` 参数；
  匹配的 media 规则并入级联。
- [x] `browser.render_document` 全链路接入：解析 `@media` → 评估 → 级联。
- [x] `parse_rulesheet` 跳过 `@media` / `@keyframes` 块（避免误解析）。
- [x] 测试：`test_media_min_width_match`、`test_media_min_width_no_match`、
  `test_media_max_width_match`、`test_media_parse`。

### 9.2 position:absolute / relative 全向偏移（阶段 3 扩展）✅

- [x] `layout.py`：`LayoutBox` 新增 `abs_right` / `abs_bottom` /
  `rel_right` / `rel_bottom` 字段。
- [x] `build_box`：`position:absolute` / `relative` 解析 `right` / `bottom` 偏移。
- [x] `layout_block`：absolute 盒按 `right` / `bottom` 从父容器边缘反向偏移；
  relative 盒按 `right` / `bottom` 向反方向平移。
- [x] 测试：`test_abs_position_right_bottom`、`test_relative_right_bottom`、
  `test_abs_position_right_only`、`test_relative_full_direction`。

### 9.3 JS 方法补全（阶段 5 深化）✅

- [x] `js.py`：`Array.flat(depth)` — 深度展平（默认 1）。
- [x] `js.py`：`Array.flatMap(fn)` — map + 一层展平。
- [x] `js.py`：`Array.map` / `filter` / `forEach` 回调增加第二个参数 `index`。
- [x] `js.py`：`Object.assign(target, …sources)` — 浅合并。
- [x] `js.py`：`String.trimStart()` / `trimEnd()`。
- [x] `js.py`：`Number` 内置对象（`isInteger` / `isNaN` / `parseInt` / `parseFloat`）。
- [x] `js.py`：`Host.eval(code)` — 表达式求值 API。
- [x] 测试：`test_js_array_flat`、`test_js_array_flat_depth`、
  `test_js_array_flat_map`、`test_js_object_assign`、
  `test_js_object_assign_override`、`test_js_number_is_integer`、
  `test_js_number_is_nan`、`test_js_number_parse_int`、
  `test_js_string_trim_start`、`test_js_string_trim_end`、
  `test_js_map_with_index`、`test_js_filter_with_index`。

### 9.4 CSS 视觉效果深化（阶段 11 扩展）✅

- [x] `layout.py`：`clip-path: inset(T R B L)` 解析 →
  `LayoutBox.clip_enabled` / `clip_inset_*` 字段。
- [x] `layout.py`：`background: linear-gradient(angle, c1, c2)` 解析 →
  `LayoutBox.gradient_enabled` / `gradient_c1` / `gradient_c2` / `gradient_angle`。
- [x] `layout.py`：`text-shadow: X Y B color` 解析 →
  `LayoutBox.text_shadow_*` 字段。
- [x] `paint_engine.py`：`DisplayCommand` 新增 gradient / clip / text-shadow 字段；
  `rasterize` 新增 `"gradient"` 和 `"text_shadow"` 命令类型；
  `_draw_gradient` 逐像素双线性插值；`_apply_clip_path` 后置裁剪。
- [x] `browser.py`：`render_document_with_hover(html, css, w, h, hover_id)` API
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

- [x] `css.py`：`Selector` 支持 `:first-child` / `:last-child` / `:nth-child(N)` /
  `:nth-child(even)` / `:nth-child(odd)` / `:nth-child(An+B)`。
- [x] `css.py`：`Selector._child_index(node)` / `_is_first_child` / `_is_last_child` /
  `_nth_matches(pseudo, idx)` 辅助方法。
- [x] `layout.py`：`LayoutBox.child_index` 字段 — 计算 1-based 元素兄弟索引。
- [x] `Selector.parse` 更新：识别 `:first-child` / `:last-child` / `:nth-child(...)`。
- [x] 测试：`first_child_layout`、`last_child_layout`、`nth_child_odd`、
  `nth_child_an_plus_b`、`nth_child_specificity`。

### 10.2 JS Map / Set / Array.from / String.replaceAll（阶段 5 深化）✅

- [x] `js.py`：`ToyMap` 类 — `set` / `get` / `has` / `delete` / `size` /
  `keys` / `values` / `entries` / `forEach`。
- [x] `js.py`：`ToySet` 类 — `add` / `has` / `delete` / `size` / `values` /
  `forEach`（自动去重）。
- [x] `js.py`：`Array.from(iterable, mapFn?)` / `Array.of(...items)`。
- [x] `js.py`：`String.replaceAll(search, replace)`。
- [x] `call_method` 更新：ToyMap / ToySet 通过 `getattr` 分发 + `size` 属性。
- [x] 测试：`js_map_basic`、`js_map_delete`、`js_set_basic`、`js_array_from`、
  `js_array_from_mapfn`、`js_array_of`、`js_string_replace_all`、
  `js_string_replace_all_regex`。

### 10.3 CSS outline + background-image (toy)（阶段 11 扩展）✅

- [x] `layout.py`：`outline` / `outline-width` / `outline-color` 解析 →
  `LayoutBox.outline_width` / `outline_color`（缺省色 black）。
- [x] `layout.py`：`background-image` 属性 → `LayoutBox.bg_image_set` 标志。
- [x] `paint_engine.py`：`DisplayCommand.outline_width` / `outline_color` 字段；
  `outline` 以 4 条 `rect` 命令在 border 外侧绘制空心环。
- [x] `layout._raw_str` 修复：`Length` 对象正确返回 `"4px"` 而非 `"4.0"`。
- [x] 测试：`outline_layout`、`outline_render`、`outline_width_only`。

### 10.4 JS Error / 嵌套 JSON / Array.toString（阶段 5 深化）✅

- [x] `js.py`：`ToyError` 类 — `message` / `name` 属性；`ToyTypeError` 子类。
- [x] `js.py`：`JSON.parse` 改用 stdlib `json.loads` — 支持嵌套对象/数组。
- [x] `js.py`：`Array.toString()` 逗号连接 + 嵌套数组扁平化。
- [x] 测试：`js_error_object`、`js_error_name`、`js_json_nested_parse`、
  `js_json_stringify_nested`、`js_array_to_string`、`js_array_to_string_nested`。

### 10.5 测试扩充 ✅

- [x] 测试总数从 157 → **179** 条（目标 175+ 已达成）。
- [x] P10 特性各 ≥ 2 条测试。

---

## P11 — 已完成 ✅

### 11.1 CSS transition 多属性 + transform（阶段 4 深化）✅

- [x] `transition.py`：`TransitionSpec.value_at` 支持 `("translate", x, y)` 元组
  线性插值；`TransitionEngine.add_multi` 批量添加；`parse_transition_css_multi`
  解析逗号分隔的多属性 transition（`background 1s, opacity 2s, transform 1s`）。
- [x] `lerp_value` 支持 transform 元组。
- [x] 测试：`transition_multi_property`、`transition_transform_lerp`、
  `transition_parse_multi`、`transition_color_lerp`、`transition_scalar_lerp`、
  `transition_advance_stops`。

### 11.2 JS try / catch / finally + throw（阶段 5 深化）✅

- [x] `js.py`：AST 节点 `Try` / `Throw` + `parse_statement` 识别 `try` /
  `throw`；`_Throw` 异常携带值；`Interpreter.this_value` 绑定 `this` 关键字。
- [x] `Host.execute_event` 将 `this` 绑定到目标元素（onclick 中
  `this.style.display = 'none'` 可用）。
- [x] `Host.execute` / `eval` 捕获未处理 throw，记 log 不崩溃。
- [x] 测试：`js_throw_catch`、`js_throw_number`、`js_try_finally`、
  `js_try_catch_finally`、`js_uncaught_throw`、`js_throw_object`。

### 11.3 CSS @supports 玩具 + display 切换（阶段 2 扩展）✅

- [x] `css.py`：`SupportsRule` dataclass + `parse_supports_blocks` 提取
  `@supports` 块；`supports_matches` 玩具永远为 True；`css.apply` 新增
  `supports_rules` 参数并入级联。
- [x] `layout.py`：display:none 盒保留在布局树中（`is_display_none` 标志 +
  零尺寸 rect），flex / grid / table 布局跳过隐藏子项；
  原有 `display_none` 测试更新为新语义。
- [x] 测试：`supports_parse`、`supports_matches_always_true`、
  `supports_cascade_applies`、`display_toggle_none`、`display_toggle_block`。

### 11.4 命中测试 + 事件坐标 API（阶段 4/5 连接）✅

- [x] `browser.py`：`element_at(html, css, w, h, x, y)` →
  `{"id", "tag", "rect"}` 或 `None`。
- [x] `browser.py`：`event_target(html, css, w, h, x, y)` 命中并执行
  onclick，返回元素 id。
- [x] `browser.py`：`click_and_render(html, css, w, h, x, y)` →
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

- [x] `css.py`：`KeyframeTrack` dataclass（property + samples + duration）+
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

- [x] `js.py`：AST 节点 `ClassDecl` / `NewExpr`；`parse_class` 解析
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

- [x] `css.py`：`parse_calc(expr)` — 两个操作数之间支持二元 `+` / `-`，
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

- [x] `browser.py`：`render_document_with_hover_at(html, css, w, h, x, y)`
  — 通过 `element_at` 命中测试将 `:hover` 应用到目标元素。
- [x] `browser.py`：`render_document_with_mouse(html, css, w, h, events,
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

- [x] `js.py`：`DestructuringDecl` AST 节点（`let {a, b} = obj` /
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

- [x] `js.py` 词法器：反引号 `` ` `` 触发模板字面量分支，将文本块发射为
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

- [x] `layout.py`：`LayoutBox` 新增 `filter_blur` / `filter_brightness` /
  `filter_contrast` / `filter_enabled` 字段；`build_box` 解析
  `filter: blur(Npx) / brightness(N%) / contrast(N%)`。
- [x] `paint_engine.py`：`DisplayCommand` 新增 filter 字段；`emit_commands`
  将 filter 参数透传到 rect / rounded / gradient 命令；`rasterize` 末尾
  调用 `_apply_filters` 后处理：brightness/contrast 直接变换区域像素，
  blur 用 `(2r+1)²` 盒式平均近似。
- [x] 测试：`css_filter_brightness_layout`、`css_filter_contrast_layout`、
  `css_filter_blur_layout`、`css_filter_brightness_render`、
  `css_filter_no_filter_identity`。

### 13.4 事件委托 + 输入事件模拟（阶段 5 深化）✅

- [x] `js.py`：`execute_event(element_id, event_name="onclick")` 扩展为
  通用事件名；P13.4 委托回退——若目标元素没有该 handler，沿 DOM 父链
  向上查找最近的祖先 handler（`this` 仍绑定到目标元素）。
- [x] `js.py`：`DomElement` 属性读取/写入新增 `value`
  （`eval_element_prop` / `set_element_prop`）。
- [x] `browser.py`：`render_document_with_mouse` 新增事件类型
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

- [x] `layout.py`：`LayoutBox` 新增 `overflow_x` / `overflow_y` /
  `scroll_left` / `scroll_top` 字段；`build_box` 解析 `overflow:auto`、
  `overflow:scroll`、`overflow-x` / `overflow-y` 及内联
  `scroll-top: Npx` / `scroll-left: Npx`。
- [x] `paint_engine.py`：`DisplayCommand` 新增 scroll 字段；
  `emit_commands` 中 scroll 容器子命令通过 `_tag_scroll` 偏移
  并标记 `scroll_enabled`；`rasterize` 末尾调用
  `_apply_scroll_clips` 后处理，将超出滚动视口的像素擦白。
- [x] `js.py`：`DomElement` 属性读写新增 `scrollTop` / `scrollLeft`
  / `offsetWidth` / `offsetHeight` / `overflow`（`eval_element_prop`
  + 惰性 layout cache `_layout_box_for`）。
- [x] 测试：`css_scroll_container_overflow_auto`、
  `css_scroll_container_scroll_top`、`css_scroll_container_scroll_left`、
  `css_scroll_container_render`、`css_scroll_container_overflow_scroll`、
  `js_element_scroll_top_prop`、`css_scroll_ymax_clamped`。

### 14.2 JS 箭头函数（阶段 5 深化）✅

- [x] `js.py` 词法器：`TWO_CHAR_OPS` 新增 `=>`。
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

- [x] `layout.py`：`LayoutBox` 新增 `text_indent` 字段；
  `build_box` 通过 `get_number("text-indent", 0.0)` 解析
  `text-indent: Npx`（支持负值，首行左移）。
- [x] `paint_engine.py`：文本命令 x 偏移应用 `text_indent`。
- [x] 测试：`css_text_indent_layout`、`css_text_indent_render`、
  `css_text_indent_zero_default`、`css_text_indent_negative`、
  `css_text_indent_render_negative`、`css_text_indent_layout_block_child`、
  `css_text_indent_render_text_present`。

### 14.4 CSS @font-face 玩具（阶段 2 深化）✅

- [x] `css.py`：`@font-face` 块解析不崩溃（toy 语义：存储 font-family
  名称，不做真实字体加载）；多个 `@font-face` + `font-family` 引用
  级联安全。
- [x] 测试：`css_font_face_parse`、`css_font_face_no_crash`。

### 14.5 测试扩充 ✅

- [x] 测试总数从 252 → **280** 条（目标 280+ 已达成）。
- [x] P14 特性各 ≥ 3 条测试。

## P15 — 已完成 ✅

### 15.1 JS 高阶数组方法补全（Infinity / NaN + Array.at）✅

- [x] `js.py`：`Infinity` / `NaN` 作为 JS 字面量支持（`Num(float("inf"))` /
  `Num(float("nan"))`），`console.log` / 字符串拼接 / 模板字面量统一渲染
  为 `"Infinity"` / `"NaN"`（不再是 `float('inf')` 字面量或崩溃）。
- [x] `js.py`：`Number.isNaN` / `Number.isInteger` 支持 Infinity / NaN
  浮点值（不再把 `NaN` 当作字符串标识符处理）。
- [x] `js.py`：`Array.at(index)` 方法 — 支持正/负下标，越界返回 `null`。
- [x] `js.py`：`Array.flat(Infinity)` 全深度展平（`float("inf")` 触发
  递归展平至底层）。
- [x] `js.py`：箭头函数回调可携带 `(v, i)` 双参数（P14.2 已实现，
  P15.1 补充 `filter` 等方法的回归测试）。
- [x] 测试：`js_array_at_positive`、`js_array_at_negative`、
  `js_infinity_literal`、`js_nan_literal`、`js_flat_infinity`、
  `js_infinity_string_format`、`js_nan_string_format`、
  `js_number_isnan`、`js_number_is_integer`、
  `js_arrow_callback_in_filter`。

### 15.2 CSS 滚动容器 JS API 完善（scrollIntoView / scrollBy）✅

- [x] `js.py`：`DomElement.scrollIntoView()` — 记录滚动目标
  （`log("scrollIntoView: #<id>")`）。
- [x] `js.py`：`DomElement.scrollBy(dx, dy)` — 记录滚动偏移
  （`log("scrollBy: dx=<dx> dy=<dy>")`）。
- [x] `js.py`：`DomElement.overflow` 属性读取内联 `overflow` 样式值
  （无则默认 `"visible"`）。
- [x] `js.py`：`DomElement.scrollTop` / `scrollLeft` / `offsetWidth` /
  `offsetHeight` 属性（惰性 layout cache，P14.1 已实现，P15.2 回归测试）。
- [x] 测试：`js_scroll_into_view`、`js_scroll_by`、
  `js_scroll_props_readable`、`js_element_overflow_prop`。

### 15.3 CSS box-decoration-break / -webkit-appearance 玩具 ✅

- [x] `layout.py`：`LayoutBox` 新增 `box_decoration_break` /
  `webkit_appearance` 字段；`build_box` 通过 `sn.get_keyword` 解析
  内联样式 `box-decoration-break: clone|slice` 与
  `-webkit-appearance: none|button|auto`。
- [x] `js.py`：`DomElement.boxDecorationBreak` / `appearance` 属性
  读取内联样式值。
- [x] 测试：`css_box_decoration_break_layout`、`css_webkit_appearance_layout`、
  `css_box_decoration_default_empty`、`css_webkit_appearance_default_empty`、
  `js_element_box_decoration_break_prop`、`js_element_appearance_prop`。

### 15.4 字体切换渲染（font-family 选择 + 多种位图字体）✅

- [x] `font.py`：新增 `serif` 字体变体（10×8 宽字形，由 8×8 基础字形
  右扩 2 列空白生成）；`_FONT_FAMILIES` 注册表；`cell_width(family)` /
  `glyph_for(ch, family)` / `family_from_css(value)` 辅助函数。
- [x] `text.py`：`char_advance` / `measure` / `wrap` / `draw_text` 全部
  新增 `font_family` 参数（默认 `"monospace"`），`_blit` 支持 8/10 字节
  宽字形。
- [x] `layout.py`：`LayoutBox.font_family` 字段；`build_box` 签名新增
  `parent_font_family` 参数（继承链），`css.py` 将 `font-family` 加入
  `INHERITED_PROPS`。
- [x] `paint_engine.py`：文本命令传 `font_family` 给 `draw_text`。
- [x] 测试：`font_cell_widths`、`font_family_from_css`、
  `font_measure_family`、`css_font_family_layout`、
  `css_font_family_inheritance`、`css_font_family_unknown_fallback`、
  `css_font_family_render`、`css_font_family_serif_wider_render`。

### 15.5 测试扩充 ✅

- [x] 测试总数从 280 → **308** 条（目标 310+ 接近达成，P16 补齐）。
- [x] P15 特性各 ≥ 3 条测试。

## P16 — 已完成 ✅

### 16.1 JS 对象展开运算符 + 数组展开 + 调用参数展开 ✅

- [x] `js.py` 词法器：`THREE_CHAR_OPS = ["..."]`（spread 操作符，
  优先于 2-char 匹配）；`parse_array_lit` 支持 `[...xs, 3]`
  （`ArraySpread` AST 节点）；`parse_arg_list` 支持 `f(...xs)`
  （`CallSpread` AST 节点）。
- [x] 解释器：`ArrayLit` 求值时 splice 展开元素；`Call` 求值时
  展开调用参数；嵌套对象展开 `{z: 3, ...a, y: 99}` 回归测试。
- [x] `js.py`：`===` / `!==` 严格相等操作符词法支持（3-char token，
  优先于 `==` / `!=`）。
- [x] 测试：`js_array_spread`、`js_array_spread_empty`、
  `js_array_spread_multiple`、`js_array_spread_non_list`、
  `js_call_spread`、`js_call_spread_three_args`、`js_nested_object_spread`、
  `js_spread_in_array_of_spread`、`js_array_spread_literal_mix`、
  `js_call_spread_zero_args`。

### 16.2 JS 可选链 + 空值合并（?. / ??）✅

- [x] `js.py` 词法器：`?.` 加入 `TWO_CHAR_OPS`（优先于单 `.`）。
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

- [x] `layout.py`：`LayoutBox` 新增 `text_justify` / `word_break` /
  `hyphens` 字段；`build_box` 通过 `sn.get_keyword` 解析内联样式。
- [x] 测试：`css_text_justify_layout`、`css_word_break_layout`、
  `css_hyphens_layout`、默认值 + 各取值（`keep-all` / `inter-word` /
  `manual` / `none`）。

### 16.4 CSS scroll-snap 玩具 + 粘性定位深化 ✅

- [x] `layout.py`：`LayoutBox.scroll_snap_type` 字段；`build_box`
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

- [x] `ttf_font.py`（新模块，纯标准库 `struct`）：解析 sfnt 头部、
  表目录、`head`（unitsPerEm）、`hhea`（ascent / descent / lineGap）、
  `maxp`（numGlyphs）、`hmtx`（advance + LSB per glyph）、`OS/2`
  （weightClass）。
- [x] 损坏 / 缺失文件抛 `TTFError`，不崩溃。
- [x] 测试：`ttf_load_header`、`ttf_line_height`、`ttf_corrupt_file`、
  `ttf_empty_font_metrics`。

### 17.2 glyf / loca 轮廓提取（二次贝塞尔 + 复合字形）✅

- [x] `ttf_font.py`：`_read_loca`（short/long 自适应）、`_read_glyf`、
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

- [x] `ttf_font.py`：`_parse_cmap`（format 4 + format 12，platform 3/1
  优先，子表偏移相对于 cmap 表起始）；`FontMetrics.advance_width(ch,
  font_px)` 用真实 hmtx 值替换固定 8px/字符估算。
- [x] 缺字形 fallback：CJK / 未映射字符返回宽格（`int(font_px)`）。
- [x] `text.py`：`advance_text` / `measure` 使用 TTF 逐字符 advance；
  `line_height_px` 使用 TTF ascent/descent/lineGap；`draw_text` 逐字符
  推进（比例字距）。
- [x] 测试：`ttf_cmap_lookup`、`ttf_advance_width`、
  `ttf_advance_proportional`、`ttf_missing_glyph_fallback`、
  `ttf_has_glyph`、`ttf_cmap_cjk_absent`、`text_ttf_advance`、
  `text_ttf_line_height`。

### 17.4 @font-face 升级：加载真实字体文件 + font-weight 双字重切换 ✅

- [x] `ttf_font.py`：`load_font_for_family(family, font_px, bold,
  search_dirs)` — 按 family → 文件名注册表 + fallback 链定位
  TTF/OTF 文件；`_font_cache` 缓存。
- [x] `font.py`：`init_real_fonts(search_dirs)` 启动时加载
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

- [x] `layout.py` 文本盒：`font_ascent_px` / `font_descent_px` /
  `baseline_y` 新字段；`line_height_px > em_box` 时计算
  `half_lead = (line_h - em_box) / 2`，文本盒 y 偏移 `half_lead`，
  文字垂直居中于行盒。
- [x] `css.py`：`INHERITED_PROPS` 扩展 `line-height` / `text-align` /
  `text-transform` / `font-family`；`line_height_px()` 优先 TTF 自然
  行高（ascent/descent/lineGap），无 TTF 时回退 `prop_font_px()`。
- [x] 测试：`p18_half_leading_baseline`、`p18_font_ascent_descent_fields`、
  `p18_baseline_alignment_math`、`p18_half_leading_large_line_height`。

### 18.2 内联元素跨行拆分 + 样式保留 ✅

- [x] `layout.py` 内联 run 循环：`<a>` / `<span>` 内联盒子
  携带自身 `text_color` / `background` / `border_color`，跨行时
  样式不丢；内联 run 高度取 `max(child_h, line_height_px)`。
- [x] 测试：`p18_inline_link_cross_line`、`p18_render_inline_styles_preserved`、
  `p18_mixed_inline_block`、`p18_inline_run_height`。

### 18.3 vertical-align 玩具版（baseline / top / middle）+ 行内图片抬高行高 ✅

- [x] `layout.py`：`LayoutBox.vertical_align` 新字段；`build_box`
  解析 `vertical-align` 内联样式；内联 run 中 `top` 对齐到行顶、
  `middle` 垂直居中、`baseline`（默认）不偏移。
- [x] 行内 `<img>` 参与行高：内联 run 的 `run_h` 取
  `max(cb.rect.h)`，图片高度自动抬高行盒。
- [x] 测试：`p18_vertical_align_top`、`p18_vertical_align_middle`、
  `p18_vertical_align_stored`、`p18_inline_img_raises_line_height`。

### 18.4 text-align 作用于真实行盒 ✅

- [x] `css.py`：`text-align` 加入 `INHERITED_PROPS`，从父元素
  继承至文本子节点。
- [x] `paint_engine.py`：`text_align` 已传递给 `draw_text`
  （center / right / left）；P18 继承修复后 `text-align` 在
  嵌套 `<div><span>` 场景正确生效。
- [x] `layout.py` 文本盒：`text-transform` 应用于测量前
  （uppercase / lowercase / capitalize）。
- [x] 测试：`p18_text_align_center_line_box`、`p18_text_align_right`、
  `p18_text_transform_uppercase`、`p18_text_transform_lowercase`。

### 18.5 测试扩充 ✅

- [x] 测试总数从 370 → **389** 条（目标 390 已达成）。
- [x] P18 特性各 ≥ 3 条测试（共 19 条新测试）。

## P19 — 已完成 ✅（M1 排版质变第三阶段）

> 依据 `项目未来规划.txt`：P19 目标 — 真实可用的断行行为。

### 19.1 UAX#14 简化版断行算法（break class 分级）✅

- [x] `text.py`：`_break_class(ch)` 将字符分为 SP/NS/BA/HY/OP/CL/CM/CJK/AL
  九类（简化 UAX#14 常用类别）。
- [x] `text.py`：`wrap()` 重写为逐字符扫描（替代旧的 `str.split()`
  按词分组）；空格后断行、CJK 逐字断行、`word-break:break-all`
  每字符都是断点。
- [x] 测试：`p19_english_word_wrap`、`p19_cjk_char_by_char`、
  `p19_break_class`、`p19_wrap_narrow_cjk`。

### 19.2 CJK 逐字断行 + 禁则处理（行首/行尾禁则字）✅

- [x] `text.py`：`_CJK_NO_LINE_START`（，。！？）〉」】》、…～―
  等）+ `_CJK_NO_LINE_END`（（〈《【〔［「『 等）禁则字符集合。
- [x] `wrap()` 断行逻辑：禁则字不能出现在行首 → 若下一词以禁则
  字开头且会溢出，将该词拉回当前行（允许轻微溢出）；闭括号
  不能开始新行。
- [x] 测试：`p19_kinsoku_no_line_start`、`p19_kinsoku_latin_brackets`、
  `p19_kinsoku_sets`、`p19_mixed_cjk_latin`。

### 19.3 &shy; 软连字符 + hyphens 玩具 + word-break/overflow-wrap 补全 ✅

- [x] `dom.py`：`_decode_entities()` 将 `&shy;` 解码为 U+00AD、
  `&nbsp;` 为 U+00A0。
- [x] `text.py` `wrap()`：软连字符（U+00AD）是断行机会；断行时
  行末显示 '-'。
- [x] `layout.py`：`LayoutBox.overflow_wrap` 新字段；`build_box`
  解析 `overflow-wrap` 内联样式；`wrap()` 接收 `word_break` +
  `overflow_wrap` 参数。
- [x] `css.py`：`overflow-wrap` 加入 `INHERITED_PROPS`。
- [x] 测试：`p19_soft_hyphen`、`p19_shy_html_entity`、
  `p19_word_break_all`、`p19_overflow_wrap_break_word`、
  `p19_overflow_wrap_layout`、`p19_word_break_layout`、
  `p19_render_shy`、`p19_render_word_break`。

### 19.4 长单词溢出的最小处理 ✅

- [x] `text.py` `wrap()`：单词本身超过 `width` 时，逐字符折断
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

- [x] `gui.py`：`TabBrowser` — tkinter ttk.Notebook 多标签页、
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
> 说明：本阶段构建了完整的曲线光栅化机制（`rasterize.py`）+ 字重
> 体系 + 回退链 + CSS 属性接线。TTF 轮廓坐标解析器（`_parse_simple_glyph`）
> 对部分复杂字形仍有已知的坐标读取缺陷，因此曲线渲染路径经
> bbox 校验（`ttf_font.outline_valid`）门控：轮廓点数落在字形 bbox 内
> 才走曲线填充，否则回退到 P15.4 的 8×8 位图字形（保证任何情况下
> 文字都能渲染，零回归）。P20 的曲线机制、抗锯齿、合成粗体、
> 字重匹配、字体回退链均有独立测试覆盖。

### 20.1 二次贝塞尔曲线填充 + 覆盖率抗锯齿 ✅

- [x] `rasterize.py`：`_flatten_quadratic`（de Casteljau 细分，
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
```

全部 P0 + P1 + P2 + P3 + P4 + P5 + P6 + P7 + P8 + P9 + P10 + P11 + P12 + P13 + P14 + P15 + P16 + P17 + P18 + P19 + P20 + P31 已完成（**437/437** 测试通过）。M1 收官。

下一站：**P21 URL/HTTP（M2 网络栈，打通后 GUI 的 http 占位页替换为真实加载）**。

每项完成后：
1. 运行 `python test.py` 确认无回归。
2. 运行 `python main.py demo.html out.ppm` 确认端到端。
3. 更新本文件的 checkbox。

---

## 参考

- [Web Browser Engineering](http://browser.engineering/) — Pavel Panchekha & Chris Harrelson, OUP 2024
- [Chrome RenderingNG Architecture](https://developer.chrome.com/docs/chromium/renderingng-architecture)
- [Servo Layout System](https://servo.org/files/2025-09-13-a-dive-into-the-servo-layout-system.pdf)

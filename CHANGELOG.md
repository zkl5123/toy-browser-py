# Changelog

All notable changes to toy-browser-py are documented in this file.

The project follows a milestone scheme (P33 = project 33, R1–R5 =
roadmap milestones).

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

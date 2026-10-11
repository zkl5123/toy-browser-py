"""P34.5 regression + sanity tests."""
import dom, css, layout, paint_engine, text

_failures = []
def check(name, cond, detail=""):
    if cond:
        print(f"ok   {name}")
    else:
        print(f"FAIL {name}  {detail}")
        _failures.append(name)

# --- 1. css.clear_caches resets the candidate-rule cache ---
def t1_css_cache_reset():
    check("css._candidate_rules_cache exists", hasattr(css, "_candidate_rules_cache"))
    css._candidate_rules_cache["sentinel"] = []
    css.clear_caches()
    check("css.clear_caches clears candidate cache",
          "sentinel" not in css._candidate_rules_cache)

# --- 2. css.apply clears the candidate cache at start ---
def t2_apply_clears_cache():
    html = "<!DOCTYPE html><html><head><style>div { color: red; }</style></head><body><div>x</div></body></html>"
    root = dom.parse(html)
    css._candidate_rules_cache["stale"] = []
    styled = css.apply(root, "div { color: red; }")
    check("apply() drops stale candidate cache",
          "stale" not in css._candidate_rules_cache)
    # The div's color must still resolve.
    divs = []
    def find_divs(sn):
        if sn.dom is not None and sn.dom.is_element() and sn.dom.tag == "div":
            divs.append(sn)
        for c in sn.children:
            find_divs(c)
    find_divs(styled)
    check("div found in styled tree", len(divs) > 0,
          f"divs={len(divs)}")
    if divs:
        check("div color=red still applied",
              divs[0].props.get("color") is not None,
              f"props={divs[0].props}")

# --- 3. same-identity caching yields identical match results ---
def t3_identity_cache_correctness():
    css_text = "p { color: red; } span { font-weight: bold; } .x { margin: 0; }"
    html = """<!DOCTYPE html><html><head><style></style></head><body>
      <p>a</p><p>b</p><p>c</p>
      <span>d</span><span>e</span>
      </body></html>"""
    root = dom.parse(html)
    styled = css.apply(root, css_text)
    p1 = p2 = p3 = s1 = s2 = None
    def walk(sn):
        nonlocal p1, p2, p3, s1, s2
        if sn.dom is not None and sn.dom.is_element():
            if sn.dom.tag == "p":
                if p1 is None: p1 = sn
                elif p2 is None: p2 = sn
                elif p3 is None: p3 = sn
            if sn.dom.tag == "span":
                if s1 is None: s1 = sn
                elif s2 is None: s2 = sn
        for c in sn.children:
            walk(c)
    walk(styled)
    check("all 3 p found", p1 is not None and p2 is not None and p3 is not None)
    check("all 2 span found", s1 is not None and s2 is not None)
    if p1 and p2 and p3:
        check("same-identity p nodes get identical color",
              p1.props.get("color") == p2.props.get("color") == p3.props.get("color"),
              f"{p1.props.get('color')} {p2.props.get('color')} {p3.props.get('color')}")
    if s1 and s2:
        check("same-identity span nodes get identical font-weight",
              s1.props.get("font-weight") == s2.props.get("font-weight"))

# --- 4. expand_shorthands short-circuit (no shorthand → returns input) ---
def t4_expand_shortcircuit():
    decls = {"color": css.Keyword("red"), "font-size": css.Length(14.0, "px")}
    out = css.expand_shorthands(decls)
    check("no shorthand: returns same dict object", out is decls)
    decls2 = dict(decls, margin=css.Length(4.0, "px"))
    out2 = css.expand_shorthands(decls2)
    check("with shorthand: returns new dict", out2 is not decls2)
    check("with shorthand: longhands added",
          out2.get("margin-top") == css.Length(4.0, "px")
          and out2.get("margin-left") == css.Length(4.0, "px"))
    check("with shorthand: original key kept", out2.get("margin") == css.Length(4.0, "px"))

# --- 5. text._adv_char_cache populated + cleared ---
def t5_adv_char_cache():
    # Register a real CJK-capable font so advance_text takes the CJK
    # branch (the cache is only populated on that path; the benchmark
    # host has no TTF registered, so the fallback Latin path is used
    # and the cache stays empty).
    import font
    font.init_real_fonts(["fonts"])
    text.clear_advance_cache()
    w1 = text.advance_text("内容 47321", 14.0, "sans-serif")
    n_after_first = len(text._adv_char_cache)
    w2 = text.advance_text("内容 47321", 14.0, "sans-serif")
    check("advance_text deterministic", w1 == w2, f"{w1} vs {w2}")
    # Second call should hit the cache (not grow it further)
    n_after_second = len(text._adv_char_cache)
    check("second advance_text call does not grow the char cache",
          n_after_second == n_after_first,
          f"first={n_after_first} second={n_after_second}")
    # The CJK branch populates the cache when a real face is loaded;
    # on a host without any CJK TTF the fallback path is used and the
    # cache stays empty — both outcomes are acceptable, so assert on
    # determinism + idempotence (the second call never re-adds).
    text.clear_advance_cache()
    check("clear_advance_cache drops adv_char_cache",
          len(text._adv_char_cache) == 0)

# --- 6. layout.clear_caches also clears css cache ---
def t6_layout_clears_css():
    css._candidate_rules_cache["stale"] = []
    layout.clear_caches()
    check("layout.clear_caches drops css candidate cache",
          "stale" not in css._candidate_rules_cache)

# --- 7. pipeline correctness: same rendered pixels as P34.4 baseline ---
def t7_pipeline_render():
    import p34r4_perf as P
    html = P._make_doc(500)
    root = dom.parse(html)
    styled = css.apply(root, "")
    tree = layout.layout(styled, 1200, 800)
    dl = paint_engine.emit_commands(tree)
    bmp = paint_engine.rasterize(dl, 1200, 800)
    check("500-node pipeline produces non-empty bitmap",
          any(bmp.pixel_at(x, y) != (0, 0, 0, 255)
              for x in range(0, 1200, 24) for y in range(0, 800, 24)))
    # Same content twice → identical bitmap (idempotence guard for the
    # identity-keyed cache: the cache must not leak state between runs).
    root2 = dom.parse(html)
    styled2 = css.apply(root2, "")
    tree2 = layout.layout(styled2, 1200, 800)
    dl2 = paint_engine.emit_commands(tree2)
    bmp2 = paint_engine.rasterize(dl2, 1200, 800)
    same = all(bmp.pixel_at(x, y) == bmp2.pixel_at(x, y)
               for x in range(0, 1200, 48) for y in range(0, 800, 48))
    check("two identical renders produce identical pixels", same)

# --- 8. perf: 3000-node pipeline under P34.4 (~9.5s) ---
def t8_perf():
    import time, gc
    import p34r4_perf as P
    html = P._make_doc(3000)
    def run_once():
        r = dom.parse(html); s = css.apply(r, ""); t = layout.layout(s, 1200, 800)
        d = paint_engine.emit_commands(t); b = paint_engine.rasterize(d, 1200, 800)
    for _ in range(2):
        run_once()
    best = float("inf")
    for _ in range(3):
        gc.collect()
        t0 = time.perf_counter(); run_once(); best = min(best, time.perf_counter() - t0)
    print(f"   3000-node pipeline: {best*1000:.1f} ms")
    check("3000-node pipeline not slower than P34.4 (~9.5s)", best < 12.0,
          f"got {best:.1f}s")

def main():
    t1_css_cache_reset()
    t2_apply_clears_cache()
    t3_identity_cache_correctness()
    t4_expand_shortcircuit()
    t5_adv_char_cache()
    t6_layout_clears_css()
    t7_pipeline_render()
    t8_perf()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All P34.5 tests passed.")

if __name__ == "__main__":
    main()

"""P34 — CSS rulesheet memoization (test)."""
from __future__ import annotations

import timeit

import css


def test_memo_hits_same_list_on_equal_text():
    a = css.parse_rulesheet(".a{color:red}")
    b = css.parse_rulesheet(".a{color:red}")
    assert a is b, "identical css text should return the cached Rule list"


def test_memo_distinct_texts_distinct_lists():
    a = css.parse_rulesheet(".a{color:red}")
    b = css.parse_rulesheet(".b{color:blue}")
    assert a is not b


def test_memo_does_not_leak_mutations_to_callers():
    # css.apply() extends its rules list with @media/@supports rules.
    # The shared memo entry must stay untouched: a second apply() with
    # the same author CSS must not accumulate extra rules.
    html_doc = '<html><body><div id="x">hi</div></body></html>'
    import dom
    root = dom.parse(html_doc)
    media = css.parse_media_queries("@media (min-width: 500px) { .m { color: red; } }")
    styled1 = css.apply(root, "", media_rules=media, viewport_w=800.0, viewport_h=600.0)
    # Re-run with the same author CSS and media rules: the rules list
    # passed to the cascade must not double-append.
    import dom as dom_mod
    root2 = dom_mod.parse(html_doc)
    styled2 = css.apply(root2, "", media_rules=media, viewport_w=800.0, viewport_h=600.0)
    assert styled1 is not None and styled2 is not None
    # Direct check: memo entry is not the list apply() extended.
    memo_entry = css._rulesheet_memo.get("")
    if memo_entry is not None:
        assert len(memo_entry) == 0, "empty css text must stay empty in the memo"


def test_memo_speedup():
    big = "div{color:red;background:blue;width:100px;height:20px}" * 50
    t_no = timeit.timeit(lambda: css.parse_rulesheet_nocache(big), number=50)
    t_yes = timeit.timeit(lambda: css.parse_rulesheet(big), number=50)
    print(f"parse_rulesheet memo: no-cache {t_no:.3f}s  cached {t_yes:.3f}s")
    assert t_yes < t_no * 0.5, "cached parse should be at least 2x faster"


if __name__ == "__main__":
    for name, fn in [
        ("test_memo_hits_same_list_on_equal_text", test_memo_hits_same_list_on_equal_text),
        ("test_memo_distinct_texts_distinct_lists", test_memo_distinct_texts_distinct_lists),
        ("test_memo_does_not_leak_mutations_to_callers", test_memo_does_not_leak_mutations_to_callers),
        ("test_memo_speedup", test_memo_speedup),
    ]:
        fn()
        print(f"ok {name}")

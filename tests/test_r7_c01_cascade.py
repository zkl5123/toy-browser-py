"""R7-C01 — CSS cascade + selector-cache contract.

Round-7 assessment (逐项改进实施方案.md §5, R7-C01) and the counter
examples in 实测评估报告.md §6:

* "``#x`` 被 ``.a.b`` 压过" — the cascade key summed the specificity
  tuple, so an id (1) lost to two classes (2);
* "first-child/id 匹配结果在相同 tag/class 兄弟间错误复用" — the
  candidate-rule cache key was the node identity (tag / class / id),
  which does not capture a node's *position* among its siblings.

Plus the plan's items: attribute / pseudo-element / ``:not()``
specificity, origin + importance order, and precise invalidation
(correctness first, hit-rate second).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.style import css

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _parse(html):
    return dom.parse(html)


def _prop(html, css_text, selector, prop, **kw):
    """Resolve *prop* on the first node matching *selector*."""
    root = _parse(html)
    sn = css.apply(root, css_text, **kw)

    def find(n):
        if n.dom is not None and n.dom.is_element():
            if n.dom.attr("id") == selector or n.dom.tag == selector:
                return n
        for c in n.children:
            f = find(c)
            if f is not None:
                return f
        return None
    node = find(sn)
    assert node is not None, f"no styled node for {selector!r}"
    return node.props.get(prop)


def _elements(sn, skip_document=True):
    """Every styled element in document order (the synthetic document
    node, whose tag is "document", is skipped)."""

    def walk(n, out):
        if n.dom is not None and n.dom.is_element():
            if not (skip_document and n.dom.tag == "document"):
                out.append(n)
        for c in n.children:
            walk(c, out)
        return out
    return walk(sn, [])


def _color(v):
    """Normalise a cascaded color value to ``"#rrggbb"`` ("" when unset).

    A plain ``"0,0,255" in str(v)`` test is wrong: ``Color(0,0,0,255)``
    (black!) contains that substring.  NOTE: the engine parses hex and
    named colors into ``Color`` but keeps ``rgb(...)`` as a Keyword, so
    the fixtures use hex throughout.
    """
    if v is None:
        return ""
    if hasattr(v, "r") and hasattr(v, "g") and hasattr(v, "b"):
        return "#%02x%02x%02x" % (v.r, v.g, v.b)
    return str(getattr(v, "value", v))


def _is(v, hex_text):
    """True when *v* is the color ``#hex_text`` (e.g. "ff0000")."""
    return _color(v) == "#" + hex_text.lower()


def _first(html, css_text, prop, ua_css=""):
    """*prop* on the LAST (innermost) element of *html* — the deepest
    node is where a rule's effect is observable."""
    root = _parse(html)
    sn = css.apply(root, css_text, ua_css=ua_css)
    els = _elements(sn)
    assert els, "the document must contain an element"
    return els[-1].props.get(prop)


def _by_id(html, css_text, prop, **kw):
    root = _parse(html)
    sn = css.apply(root, css_text, **kw)
    out = {}
    for n in _elements(sn):
        _id = n.dom.attr("id")
        if _id:
            out[_id] = n.props.get(prop)
    return out


def _siblings(html, css_text, prop):
    """Return *prop* for every child element of the single wrapper."""
    root = _parse(html)
    sn = css.apply(root, css_text)
    els = _elements(sn)
    assert els, "the document must contain an element"
    wrap = els[0]
    return [c.props.get(prop) for c in wrap.children
            if c.dom is not None and c.dom.is_element()]


# ---------------------------------------------------------------------------
# 1. specificity is compared component-wise, never summed
# ---------------------------------------------------------------------------

@_reg
def test_id_beats_two_classes():
    """反例: ``#x`` 必须胜过 ``.a.b``。"""
    v = _first('<div id="x" class="a b">t</div>',
               "#x { color: #ff0000; } .a.b { color: #0000ff; }",
               "color")
    assert v is not None, "a color must cascade"
    assert _is(v, "ff0000"), f"#x must win, got {_color(v)}"


@_reg
def test_id_beats_many_classes_and_types():
    """An id (1) outranks any number of classes (3) on the SAME element."""
    v = _first('<div id="x" class="a b c">t</div>',
               ".a.b.c { color: #00ff00; } #x { color: #ff0000; }",
               "color")
    assert _is(v, "ff0000"), f"#x must win over (0,0,3,0), got {_color(v)}"


@_reg
def test_class_beats_many_types():
    v = _first('<div><p><span><b><i class="k">t</i></b></span></p></div>',
               "div p span b i { color: #0000ff; }"
               " .k { color: #ff0000; }",
               "color")
    # .k lives on the <i>; walk to it
    root = _parse('<div><p><span><b><i class="k">t</i></b></span></p></div>')
    sn = css.apply(root, "div p span b i { color: #0000ff; }"
                         " .k { color: #ff0000; }")

    def find(n):
        if n.dom is not None and n.dom.is_element() and n.dom.tag == "i":
            return n
        for c in n.children:
            f = find(c)
            if f is not None:
                return f
        return None
    got = find(sn).props.get("color")
    assert _is(got, "ff0000"), f".k must win over (0,0,0,5), got {_color(got)}"


@_reg
def test_specificity_tuple_is_lexicographic():
    """The explicit contract: component-wise, not summed."""
    rules = css.parse_rulesheet_nocache(
        "#x { color: a; } .a.b { color: b; } .c.d.e { color: c; }")
    by_text = {r.selectors[0].selector_text: r.specificity for r in rules}
    assert by_text["#x"] == (0, 1, 0, 0), by_text
    assert by_text[".a.b"] == (0, 0, 2, 0), by_text
    assert by_text[".c.d.e"] == (0, 0, 3, 0), by_text
    keys = sorted(rules, key=css._cascade_key)
    assert keys[-1].selectors[0].selector_text == "#x", \
        f"#x must sort last (wins), got {[r.selectors[0].selector_text for r in keys]}"


# ---------------------------------------------------------------------------
# 2. attribute / pseudo-element / :not() specificity
# ---------------------------------------------------------------------------

@_reg
def test_attribute_selector_is_class_level():
    rules = css.parse_rulesheet_nocache(
        "input[type=text] { color: a; } input { color: b; }")
    spec = {r.selectors[0].selector_text: r.specificity for r in rules}
    assert spec["input[type=text]"] == (0, 0, 1, 1), spec
    assert spec["input"] == (0, 0, 0, 1), spec


@_reg
def test_attribute_selector_matches_and_beats_bare_type():
    v = _first('<input type="text" id="i">',
               "input { color: #0000ff; }"
               " input[type=text] { color: #ff0000; }",
               "color")
    assert _is(v, "ff0000"), f"[type=text] must win, got {_color(v)}"


@_reg
def test_attribute_selector_operators():
    html = ('<div><a href="https://x.example" id="a1">1</a>'
            '<a href="/local" id="a2">2</a>'
            '<a id="a3">3</a></div>')
    by_id = _by_id(html, "a[href^=https] { color: #ff0000; }", "color")
    assert _is(by_id["a1"], "ff0000"), f"^= must match https, got {_color(by_id['a1'])}"
    assert not _is(by_id["a2"], "ff0000"), f"^= must not match /local, got {_color(by_id['a2'])}"
    assert not _is(by_id["a3"], "ff0000"), "a link without href must not match"


@_reg
def test_pseudo_element_is_type_level():
    rules = css.parse_rulesheet_nocache("p::before { content: 'x'; }")
    assert rules[0].specificity == (0, 0, 0, 2), rules[0].specificity


@_reg
def test_not_specificity_is_its_argument():
    rules = css.parse_rulesheet_nocache(
        "div:not(.y) { color: a; } p:not(#z) { color: b; }")
    spec = {r.selectors[0].selector_text: r.specificity for r in rules}
    assert spec["div:not(.y)"] == (0, 0, 1, 1), spec
    # :not(#z) carries an ID → (0,1,0,1)
    assert spec["p:not(#z)"] == (0, 1, 0, 1), spec


@_reg
def test_not_matching_semantics():
    res = _by_id('<div class="y" id="a">1</div><div class="n" id="b">2</div>',
                 "div:not(.y) { color: #ff0000; }", "color")
    assert not _is(res["a"], "ff0000"), f":not(.y) must skip div.y, got {_color(res['a'])}"
    assert _is(res["b"], "ff0000"), f":not(.y) must match div.n, got {_color(res['b'])}"


@_reg
def test_not_id_argument_outranks_class():
    v = _first('<p id="p1" class="c">t</p>',
               "p.c { color: #0000ff; } p:not(#z) { color: #ff0000; }",
               "color")
    assert _is(v, "ff0000"), f"p:not(#z) (0,1,0,1) must beat p.c, got {_color(v)}"


# ---------------------------------------------------------------------------
# 3. candidate-rule cache must not leak across siblings
# ---------------------------------------------------------------------------

@_reg
def test_first_child_does_not_leak_to_sibling():
    """反例: first-child 匹配结果在相同 tag/class 兄弟间错误复用。"""
    got = _siblings("<div><p class='c'>1</p><p class='c'>2</p><p class='c'>3</p></div>",
                    "p.c:first-child { color: #ff0000; }", "color")
    assert len(got) == 3, f"three siblings, got {got}"
    assert _is(got[0], "ff0000"), f"the first child must match, got {_color(got[0])}"
    assert not _is(got[1], "ff0000"), f"sibling 2 must NOT match, got {_color(got[1])}"
    assert not _is(got[2], "ff0000"), f"sibling 3 must NOT match, got {_color(got[2])}"


@_reg
def test_last_child_does_not_leak_to_sibling():
    got = _siblings("<div><p class='c'>1</p><p class='c'>2</p><p class='c'>3</p></div>",
                    "p.c:last-child { color: #00ff00; }", "color")
    assert not _is(got[0], "00ff00"), f"got {_color(got[0])}"
    assert not _is(got[1], "00ff00"), f"got {_color(got[1])}"
    assert _is(got[2], "00ff00"), f"the last child must match, got {_color(got[2])}"


@_reg
def test_nth_child_does_not_leak_to_sibling():
    got = _siblings("<div><p class='c'>1</p><p class='c'>2</p><p class='c'>3</p></div>",
                    "p.c:nth-child(2) { color: #0000ff; }", "color")
    assert not _is(got[0], "0000ff"), f"got {_color(got[0])}"
    assert _is(got[1], "0000ff"), f"nth-child(2) must match sibling 2, got {_color(got[1])}"
    assert not _is(got[2], "0000ff"), f"got {_color(got[2])}"


@_reg
def test_position_sensitive_flag_detected():
    sel = css.parse_selector_list("p.c:first-child")[0]
    assert sel.is_position_sensitive is True, "structural pseudos are position sensitive"
    sel2 = css.parse_selector_list("p.c")[0]
    assert sel2.is_position_sensitive is False
    sel3 = css.parse_selector_list("div > p")[0]
    assert sel3.is_position_sensitive is False


# ---------------------------------------------------------------------------
# 4. invalidation: a fresh apply() must not reuse a stale cache
# ---------------------------------------------------------------------------

@_reg
def test_class_change_reapplies_correctly():
    html = "<div><p id='p'>t</p></div>"
    # first apply: no match for .c → the default color
    a = _by_id(html, ".c { color: #ff0000; }", "color")
    assert not _is(a["p"], "ff0000"), f".c must not match, got {_color(a['p'])}"
    b = _by_id(html, "p { color: #00ff00; }", "color")
    assert _is(b["p"], "00ff00"), f"p must match, got {_color(b['p'])}"


@_reg
def test_dynamic_dom_change_reapplies_first_child():
    """Reordering the DOM must move the :first-child match."""
    html_a = "<div><p class='c'>1</p><p class='x'>2</p></div>"
    html_b = "<div><p class='x'>2</p><p class='c'>1</p></div>"
    css_text = "p.c:first-child { color: #ff0000; }"

    def first_color(html):
        sn = css.apply(_parse(html), css_text)
        els = _elements(sn)
        wrap = els[0]
        return [c.props.get("color") for c in wrap.children
                if c.dom is not None and c.dom.is_element()]
    assert _is(first_color(html_a)[0], "ff0000"), "c is first in A"
    assert not _is(first_color(html_b)[0], "ff0000"), "c is not first in B"


@_reg
def test_hover_state_participates_in_cache_key():
    html = "<div><p class='c' id='h'>1</p><p class='c' id='i'>2</p></div>"
    css_text = "p.c:hover { color: #ff0000; }"
    a = _by_id(html, css_text, "color", interaction_state={"hover": "h"})
    b = _by_id(html, css_text, "color", interaction_state={"hover": "i"})
    assert _is(a["h"], "ff0000"), f"hover h: {a}"
    assert not _is(a["i"], "ff0000"), f"hover h must not paint i: {a}"
    assert _is(b["i"], "ff0000"), f"hover i: {b}"
    assert not _is(b["h"], "ff0000"), f"hover i must not paint h: {b}"


# ---------------------------------------------------------------------------
# 5. origin / importance / source order
# ---------------------------------------------------------------------------

@_reg
def test_author_beats_ua_on_specificity_tie():
    v = _first("<body><p id='p'>t</p></body>",
               "p { color: #ff0000; }", "color",
               ua_css="p { color: #0000ff; }")
    assert _is(v, "ff0000"), f"author must beat UA on a tie, got {_color(v)}"


@_reg
def test_author_important_beats_author_normal():
    v = _first("<p id='p'>t</p>",
               "p { color: #ff0000 !important; } #p { color: #0000ff; }",
               "color")
    assert _is(v, "ff0000"), f"!important must win, got {_color(v)}"


@_reg
def test_ua_important_beats_author_important():
    """CSS Cascading 4 §6.6.1 — the important layer reverses the origins."""
    v = _first("<p id='p'>t</p>",
               "p { color: #ff0000 !important; }", "color",
               ua_css="p { color: #00ff00 !important; }")
    assert _is(v, "00ff00"), f"UA !important must beat author !important, got {_color(v)}"


@_reg
def test_inline_beats_author_but_loses_to_important():
    html = "<p id='p' style='color: #0000ff'>t</p>"
    v = _first(html, "p { color: #ff0000; }", "color")
    assert _is(v, "0000ff"), f"inline must beat author normal, got {_color(v)}"
    v2 = _first(html, "p { color: #ff0000 !important; }", "color")
    assert _is(v2, "ff0000"), f"author !important must beat inline, got {_color(v2)}"


@_reg
def test_source_order_decides_equal_specificity():
    v = _first("<p class='c'>t</p>",
               ".c { color: #ff0000; } .c { color: #00ff00; }",
               "color")
    assert _is(v, "00ff00"), f"the later rule must win, got {_color(v)}"


# ---------------------------------------------------------------------------
# 6. @supports must be capability aware (not "always supported")
# ---------------------------------------------------------------------------

@_reg
def test_supports_is_capability_aware():
    assert css.supports_matches("(display: grid)") is True, \
        "display:grid is implemented → supported"
    assert css.supports_matches("(position: sticky)") is False, \
        "position:sticky is not implemented → must report unsupported"


@_reg
def test_supports_gate_excludes_unsupported_rule():
    html = "<div id='d'>t</div>"
    root = _parse(html)
    sn = css.apply(root, "", supports_rules=[
        css.SupportsRule("(position: sticky)", [
            css.parse_rulesheet_nocache("#d { color: #ff0000; }")[0]]),
    ])
    got = sn.children[0].props.get("color")
    assert not _is(got, "ff0000"), \
        f"an unsupported @supports block must not cascade, got {_color(got)}"


# ---------------------------------------------------------------------------
# 7. inheritance + custom properties still work through the new keys
# ---------------------------------------------------------------------------

@_reg
def test_inherited_property_reaches_child():
    html = "<div id='p'><span id='c'>t</span></div>"
    sn = css.apply(_parse(html), "#p { color: #ff0000; }")
    out = {}

    def walk(n):
        if n.dom is not None and n.dom.is_element() and n.dom.attr("id"):
            out[n.dom.attr("id")] = n.props.get("color")
        for c in n.children:
            walk(c)
    walk(sn)
    assert _is(out["c"], "ff0000"), f"color must inherit, got {out}"


@_reg
def test_custom_property_resolution():
    html = "<div id='p' style='--main: #ff0000'><b id='c'>t</b></div>"
    sn = css.apply(_parse(html), "#c { color: var(--main); }")
    node = None
    for n in _elements(sn):
        if n.dom.attr("id") == "c":
            node = n
    assert node is not None, "the <b> must exist"
    # ``var()`` resolves through the StyledNode accessor, not the raw
    # props dict (which keeps the unresolved VarExpr).
    resolved = node.color()
    assert resolved is not None and resolved.r > 200, \
        f"var(--main) must resolve to red, got {resolved}"

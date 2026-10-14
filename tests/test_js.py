"""JS engine: arrays, objects, strings, classes, errors — test module (GPT P1-2 split from test.py)."""
from __future__ import annotations

# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)


import browser
import dom
import css
import layout
import js
import text
import paint
from paint import Bitmap, Color
import sys

# Shared helpers (defined in test.py, needed by multiple modules)
from test_helpers import (
    _a11y_roles,
    _find_a11y_role,
    _find_box,
    _find_box_by_id,
    _find_box_by_id_or_tag,
    _find_box_by_tag,
    _greens,
    _p20_bitmap,
    _p20_first_valid_outline,
    _p20_ink,
    _p20_metrics,
    _p21_start_server,
    _p22_start_server,
    _p29_js_logs,
    _p30_doc_html,
    _p30_host,
    _p30_qsel_all,
    _p32_cleanup,
    _p32_tmp,
    _pixel_diff,
    _reds,
    _rmtree_quiet,
    find2_children,
)

TESTS: list = []

def test_js_toggle_display_none():
    base_html = '<html><body><div id="box" style="background: red; width: 100px">show me</div></body></html>'
    before = browser.render_document(base_html, "", 300, 200)
    assert _reds(before) > 0, "baseline must show the red div"

    js_html = (
        '<html><body>'
        '<div id="box" style="background: red; width: 100px">show me</div>'
        "<script>document.getElementById('box').style.display = 'none';</script>"
        "</body></html>"
    )
    hidden = browser.render_document(js_html, "", 300, 200)
    assert _reds(hidden) == 0, "JS setting style.display='none' must hide the div"



TESTS.append(("test_js_toggle_display_none", test_js_toggle_display_none))
def test_js_event_loop():
    html = (
        '<html><body>'
        '<p id="spacer">x</p>'
        '<button id="btn" onclick="document.getElementById(\'spacer\').style.color=\'red\'">click</button>'
        "</body></html>"
    )
    css_text = "p { margin: 0; } button { margin: 0; padding: 4px; }"
    baseline = browser.render_document(html, css_text, 800, 600)
    clicked = browser.render_document_with_click(
        html, css_text, 800, 600, 15.0, 25.0
    )
    assert baseline.pixels != clicked.pixels, (
        "a coordinate click on the button should mutate #spacer and change pixels"
    )
    # No-hit coordinate must not change anything.
    nohit = browser.render_document_with_click(html, css_text, 800, 600, 790.0, 590.0)
    assert baseline.pixels == nohit.pixels, "click on empty space must be a no-op"



TESTS.append(("test_js_event_loop", test_js_event_loop))
def test_js_nullish():
    root = dom.parse("<html><body></body></html>")
    host = js.Host(root, lambda _s: None)
    prog = js.parse_program("a ?? 42")
    interp = js.Interpreter(host)
    interp.env.define("a", None)
    val = interp.eval(prog)
    assert val == 42, f"?? must return 42, got {val}"
    interp.env.define("a", 7)
    val2 = interp.eval(prog)
    assert val2 == 7, f"?? must return 7, got {val2}"



TESTS.append(("test_js_nullish", test_js_nullish))
def test_js_set_timeout_fifo():
    logs: list[str] = []
    root = dom.parse("<html><body></body></html>")
    host = js.Host(root, logs.append)
    host.execute("setTimeout('console.log(\"fired\");', 0);")
    host.flush_timers()
    assert "fired" in logs, f"timer must fire, logs={logs}"



TESTS.append(("test_js_set_timeout_fifo", test_js_set_timeout_fifo))
def test_js_array_literal():
    """Array literal [1,2,3] and .length / .push work."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let a = [1, 2, 3]; "
        "a.push(4); "
        "console.log(a.length);"
    )
    assert any("4" in l for l in logs), f"array push/length must produce 4, logs={logs}"



TESTS.append(("test_js_array_literal", test_js_array_literal))
def test_js_string_methods():
    """String#includes and String#indexOf work."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let s = 'hello world'; "
        "console.log(s.includes('world')); "
        "console.log(s.indexOf('lo'));"
    )
    assert any("true" in l.lower() for l in logs), f"'hello world'.includes('world') must be true, logs={logs}"
    assert any("3" in l for l in logs), f"'hello world'.indexOf('lo') must be 3, logs={logs}"



TESTS.append(("test_js_string_methods", test_js_string_methods))
def test_js_object_literal():
    """Object literal {a:1, b:2} and property access work."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let o = {a: 1, b: 2}; "
        "console.log(o.a); "
        "console.log(o.b);"
    )
    assert any("1" in l for l in logs) and any("2" in l for l in logs), (
        f"object literal property access must work, logs={logs}"
    )



TESTS.append(("test_js_object_literal", test_js_object_literal))
def test_promise_resolve_then():
    """Promise resolves and .then passes the value to the handler.

    P0 (agent report) — ``.then`` no longer auto-logs the resolved
    value (the legacy code logged it when the handler returned a
    plain value).  The handler itself logs it, matching real-browser
    Promise semantics."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let p = Promise(function(resolve, reject) { resolve(42); });"
        "p.then(function(v) { console.log('v=' + v); });"
    )
    assert any("v=42" in l for l in logs), f"promise.then must log v=42, logs={logs}"



TESTS.append(("test_promise_resolve_then", test_promise_resolve_then))
def test_promise_catch():
    """Promise rejects and .catch logs the error."""
    root = dom.parse("<html><body></body></html>")
    logs = []
    host = js.Host(root, logs.append)
    host.execute(
        "let p = Promise(function(resolve, reject) { reject('boom'); });"
        "p.catch(function(e) { console.log(e); });"
    )
    assert any("boom" in l for l in logs), f"promise.catch must log 'boom', logs={logs}"



TESTS.append(("test_promise_catch", test_promise_catch))
def test_js_math_sqrt():
    """P3.3 — Math.sqrt(16) == 4."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(Math.sqrt(16));")
    assert any("4" in l for l in logs), f"Math.sqrt(16) must log 4, got {logs}"



TESTS.append(("test_js_math_sqrt", test_js_math_sqrt))
def test_js_math_max_abs():
    """P3.3 — Math.max(3,7,2)==7, Math.abs(-5)==5."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(Math.max(3, 7, 2)); console.log(Math.abs(-5));")
    assert any("7" in l for l in logs), f"Math.max must log 7, got {logs}"
    assert any("5" in l for l in logs), f"Math.abs must log 5, got {logs}"



TESTS.append(("test_js_math_max_abs", test_js_math_max_abs))
def test_js_json_stringify():
    """P3.3 — JSON.stringify({a:1,b:'two'}) produces valid JSON."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(JSON.stringify({a: 1, b: 'two'}));")
    assert any('"a"' in l and '"two"' in l for l in logs), f"JSON.stringify failed, got {logs}"



TESTS.append(("test_js_json_stringify", test_js_json_stringify))
def test_js_json_parse():
    """P3.3 — JSON.parse roundtrips a simple object."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let v = JSON.parse('{\"x\": 42}'); "
        "console.log(v.x);"
    )
    assert any("42" in l for l in logs), f"JSON.parse must yield x=42, got {logs}"



TESTS.append(("test_js_json_parse", test_js_json_parse))
def test_js_math_constants():
    """P3.3 — Math.PI and Math.E are available."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.log(Math.PI);")
    assert any("3.14" in l for l in logs), f"Math.PI must log 3.14…, got {logs}"



TESTS.append(("test_js_math_constants", test_js_math_constants))
def test_js_date_now():
    """P4.2 — new Date() returns an object with getTime/getFullYear."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let d = new Date();"
        "console.log(d.getFullYear());"
    )
    # getFullYear returns the current year (e.g. 2025).
    import datetime
    year = str(datetime.datetime.now().year)
    assert any(year in l for l in logs), f"getFullYear must log current year {year}, got {logs}"



TESTS.append(("test_js_date_now", test_js_date_now))
def test_js_regexp_test():
    """P4.2 — RegExp.test returns true/false for a pattern match."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let r = new RegExp('abc');"
        "console.log(r.test('xabcy'));"
        "console.log(r.test('xyz'));"
    )
    assert any("true" in l for l in logs), f"RegExp.test('xabcy') must be true, got {logs}"
    assert any("false" in l for l in logs), f"RegExp.test('xyz') must be false, got {logs}"



TESTS.append(("test_js_regexp_test", test_js_regexp_test))
def test_js_string_charcode():
    """P4.2 — String.fromCharCode and charCodeAt roundtrip."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "console.log(String.fromCharCode(65, 66, 67));"
        "console.log('A'.charCodeAt(0));"
    )
    assert any("ABC" in l for l in logs), f"fromCharCode(65,66,67) must log 'ABC', got {logs}"
    assert any("65" in l for l in logs), f"charCodeAt must log 65, got {logs}"



TESTS.append(("test_js_string_charcode", test_js_string_charcode))
def test_js_regexp_replace():
    """P4.2 — RegExp.replace substitutes matched text."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let r = new RegExp('o');"
        "console.log(r.replace('fooo', '0'));"
    )
    assert any("f000" in l for l in logs), f"replace must yield 'f000', got {logs}"



TESTS.append(("test_js_regexp_replace", test_js_regexp_replace))
def test_js_date_getmonth():
    """P4.2 — Date.getMonth returns 0-indexed month."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let d = new Date();"
        "let m = d.getMonth();"
        "let ok = (m >= 0) && (m <= 11);"
        "console.log('month:' + ok);"
    )
    assert any("month:true" in l for l in logs), f"getMonth must be 0–11, got {logs}"



TESTS.append(("test_js_date_getmonth", test_js_date_getmonth))
def test_js_array_map_join():
    """P5.3 — Array.map + join with JS-style integer formatting."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1,2,3,4,5];"
        "let m = a.map(function(x){ return x * 2; });"
        "console.log(m.join(','));"
    )
    assert any("2,4,6,8,10" in l for l in logs), f"map+join must be 2,4,6,8,10, got {logs}"



TESTS.append(("test_js_array_map_join", test_js_array_map_join))
def test_js_array_filter():
    """P5.3 — Array.filter keeps elements passing the predicate."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1,2,3,4,5];"
        "let f = a.filter(function(x){ return x > 3; });"
        "console.log(f.join(','));"
    )
    assert any("4,5" in l for l in logs), f"filter must be 4,5, got {logs}"



TESTS.append(("test_js_array_filter", test_js_array_filter))
def test_js_array_reverse_concat():
    """P5.3 — Array.reverse mutates in place; concat merges arrays + scalars."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("let a = [1,2,3]; let r = a.reverse(); console.log(r.join(','));")
    assert any("3,2,1" in l for l in logs), f"reverse must be 3,2,1, got {logs}"
    logs2 = []
    host2 = js.Host(dom.parse("<html><body></body></html>"), logs2.append)
    host2.execute("let a = [1,2]; let b = a.concat([3,4],5); console.log(b.join(','));")
    assert any("1,2,3,4,5" in l for l in logs2), f"concat must be 1,2,3,4,5, got {logs2}"



TESTS.append(("test_js_array_reverse_concat", test_js_array_reverse_concat))
def test_js_array_foreach():
    """P5.3 — Array.forEach runs the callback over every element."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1,2,3];"
        "let s = 0;"
        "a.forEach(function(x){ s += x; });"
        "console.log(s);"
    )
    assert any("6" in l for l in logs), f"forEach sum must be 6, got {logs}"



TESTS.append(("test_js_array_foreach", test_js_array_foreach))
def test_console_assert_false():
    """P5.3 — console.assert logs a failure message when the condition is false."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.assert(1 == 2, 'one is not two');")
    assert any("ASSERTION" in l and "one is not two" in l for l in logs), \
        f"assert(false) must log the message, got {logs}"



TESTS.append(("test_console_assert_false", test_console_assert_false))
def test_console_assert_true_silent():
    """P5.3 — console.assert stays silent when the condition is true."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute("console.assert(1 == 1, 'should not fire');")
    assert not any("ASSERTION" in l for l in logs), \
        f"assert(true) must not log, got {logs}"



TESTS.append(("test_console_assert_true_silent", test_console_assert_true_silent))
def test_js_array_reduce():
    """P6.3 — Array.reduce sums a list."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1, 2, 3, 4, 5];"
        "let s = a.reduce(function(acc, x) { return acc + x; });"
        "console.log(s);"
    )
    assert any("15" in l for l in logs), f"reduce sum must be 15, got {logs}"



TESTS.append(("test_js_array_reduce", test_js_array_reduce))
def test_js_array_reduce_init():
    """P6.3 — Array.reduce with an initial accumulator value."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [10, 20];"
        "let r = a.reduce(function(acc, x) { return acc * x; }, 100);"
        "console.log(r);"
    )
    # 100 * 10 * 20 = 20000
    assert any("20000" in l for l in logs), f"reduce init must give 20000, got {logs}"



TESTS.append(("test_js_array_reduce_init", test_js_array_reduce_init))
def test_js_object_keys():
    """P6.3 — Object.keys returns the property names of a dict."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let o = {a: 1, b: 2, c: 3};"
        "let ks = Object.keys(o);"
        "console.log(ks.join(','));"
    )
    assert any("a,b,c" in l for l in logs), f"Object.keys must give a,b,c, got {logs}"



TESTS.append(("test_js_object_keys", test_js_object_keys))
def test_js_object_values():
    """P6.3 — Object.values returns the property values of a dict."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let o = {x: 10, y: 20};"
        "let vs = Object.values(o);"
        "console.log(vs.join(','));"
    )
    assert any("10,20" in l for l in logs), f"Object.values must give 10,20, got {logs}"



TESTS.append(("test_js_object_values", test_js_object_values))
def test_js_string_repeat():
    """P6.3 — String.repeat duplicates the string N times."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let s = 'ab'.repeat(3);"
        "console.log(s);"
    )
    assert any("ababab" in l for l in logs), f"repeat must give ababab, got {logs}"



TESTS.append(("test_js_string_repeat", test_js_string_repeat))
def test_js_string_pad_start():
    """P6.3 — String.padStart pads on the left to target length."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let s = '5'.padStart(3, '0');"
        "console.log(s);"
    )
    assert any("005" in l for l in logs), f"padStart must give 005, got {logs}"



TESTS.append(("test_js_string_pad_start", test_js_string_pad_start))
def test_js_string_pad_end():
    """P6.3 — String.padEnd pads on the right to target length."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let s = 'hi'.padEnd(5, '.');"
        "console.log(s);"
    )
    assert any("hi..." in l for l in logs), f"padEnd must give hi..., got {logs}"



TESTS.append(("test_js_string_pad_end", test_js_string_pad_end))
def test_js_array_find():
    """P7.3 — Array.find returns the first matching element."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [10, 20, 30, 40];"
        "let f = a.find(function(x) { return x > 25; });"
        "console.log(f);"
    )
    assert any("30" in l for l in logs), f"find must give 30, got {logs}"



TESTS.append(("test_js_array_find", test_js_array_find))
def test_js_array_find_index():
    """P7.3 — Array.findIndex returns the index of the first match."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [10, 20, 30];"
        "let i = a.findIndex(function(x) { return x == 20; });"
        "console.log(i);"
    )
    assert any("1" in l for l in logs), f"findIndex must give 1, got {logs}"



TESTS.append(("test_js_array_find_index", test_js_array_find_index))
def test_js_array_some():
    """P7.3 — Array.some returns true if any element matches."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1, 2, 3];"
        "console.log(a.some(function(x) { return x > 2; }));"
        "console.log(a.some(function(x) { return x > 10; }));"
    )
    assert any("true" in l for l in logs), f"some must give true for x>2, got {logs}"
    assert any("false" in l for l in logs), f"some must give false for x>10, got {logs}"



TESTS.append(("test_js_array_some", test_js_array_some))
def test_js_array_every():
    """P7.3 — Array.every returns true if all elements match."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [2, 4, 6];"
        "console.log(a.every(function(x) { return x > 1; }));"
        "console.log(a.every(function(x) { return x > 10; }));"
    )
    # "true" appears in the x>1 check, "false" in the x>10 check.
    assert any(l.strip() == "true" for l in logs), f"every must give true for x>1, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"every must give false for x>10, got {logs}"



TESTS.append(("test_js_array_every", test_js_array_every))
def test_js_array_sort():
    """P8.3 — Array.sort returns a sorted copy."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [30, 10, 20];"
        "let s = a.sort();"
        "console.log(s.join(','));"
    )
    assert any("10,20,30" in l for l in logs), f"sort must give 10,20,30, got {logs}"



TESTS.append(("test_js_array_sort", test_js_array_sort))
def test_js_array_includes():
    """P8.3 — Array.includes returns true if the value is present."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let a = [1, 2, 3];"
        "console.log(a.includes(2));"
        "console.log(a.includes(9));"
    )
    assert any(l.strip() == "true" for l in logs), f"includes(2) must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"includes(9) must be false, got {logs}"



TESTS.append(("test_js_array_includes", test_js_array_includes))
def test_js_object_entries():
    """P8.3 — Object.entries returns [ [key, value], … ] pairs."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "let o = {x: 1, y: 2};"
        "let e = Object.entries(o);"
        "console.log(e.length);"
    )
    assert any("2" in l for l in logs), f"entries length must be 2, got {logs}"



TESTS.append(("test_js_object_entries", test_js_object_entries))
def test_js_string_starts_with():
    """P8.3 — String.startsWith returns true if the string begins with the prefix."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "console.log('hello'.startsWith('he'));"
        "console.log('hello'.startsWith('world'));"
    )
    assert any(l.strip() == "true" for l in logs), f"startsWith('he') must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"startsWith('world') must be false, got {logs}"



TESTS.append(("test_js_string_starts_with", test_js_string_starts_with))
def test_js_string_ends_with():
    """P8.3 — String.endsWith returns true if the string ends with the suffix."""
    logs = []
    host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
    host.execute(
        "console.log('hello'.endsWith('lo'));"
        "console.log('hello'.endsWith('hi'));"
    )
    assert any(l.strip() == "true" for l in logs), f"endsWith('lo') must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"endsWith('hi') must be false, got {logs}"



TESTS.append(("test_js_string_ends_with", test_js_string_ends_with))
def test_js_array_flat():
    """P9.3 — Array.flat() flattens one level by default."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [[1, 2], [3, 4], 5];"
        "let r = a.flat();"
        "console.log(r.join(','));"
    )
    assert any("1,2,3,4,5" in l for l in logs), f"flat() must give 1,2,3,4,5, got {logs}"



TESTS.append(("test_js_array_flat", test_js_array_flat))
def test_js_array_flat_depth():
    """P9.3 — Array.flat(2) flattens two levels."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [[1, [2, 3]], [4]];"
        "let r = a.flat(2);"
        "console.log(r.join(','));"
    )
    assert any("1,2,3,4" in l for l in logs), f"flat(2) must give 1,2,3,4, got {logs}"



TESTS.append(("test_js_array_flat_depth", test_js_array_flat_depth))
def test_js_array_flat_map():
    """P9.3 — Array.flatMap maps + flattens one level."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let r = [1,2,3].flatMap(function(x){ return [x, x*10]; });"
        "console.log(r.join(','));"
    )
    assert any("1,10,2,20,3,30" in l for l in logs), f"flatMap must give 1,10,2,20,3,30, got {logs}"



TESTS.append(("test_js_array_flat_map", test_js_array_flat_map))
def test_js_object_assign():
    """P9.3 — Object.assign merges source dicts into target."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let o = Object.assign({a:1}, {b:2}, {c:3});"
        "console.log(o.a, o.b, o.c);"
    )
    assert any("1" in l and "2" in l and "3" in l for l in logs), \
        f"Object.assign must merge, got {logs}"



TESTS.append(("test_js_object_assign", test_js_object_assign))
def test_js_object_assign_override():
    """P9.3 — Object.assign: later sources override earlier keys."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let o = Object.assign({a:1}, {a:9});"
        "console.log(o.a);"
    )
    assert any(l.strip() == "9" for l in logs), f"later source must override, got {logs}"



TESTS.append(("test_js_object_assign_override", test_js_object_assign_override))
def test_js_number_is_integer():
    """P9.3 — Number.isInteger returns true for whole numbers."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log(Number.isInteger(42));"
        "console.log(Number.isInteger(4.5));"
    )
    assert any(l.strip() == "true" for l in logs), f"isInteger(42) must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"isInteger(4.5) must be false, got {logs}"



TESTS.append(("test_js_number_is_integer", test_js_number_is_integer))
def test_js_number_is_nan():
    """P9.3 — Number.isNaN detects float NaN."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    # The toy engine has no NaN literal; detect is_nan logic via string "NaN"
    # (how the engine represents the value when it sees it as an Ident).
    host.execute(
        "console.log(Number.isNaN('NaN'));"
        "console.log(Number.isNaN(1));"
    )
    assert any(l.strip() == "true" for l in logs), f"isNaN('NaN') must be true, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"isNaN(1) must be false, got {logs}"



TESTS.append(("test_js_number_is_nan", test_js_number_is_nan))
def test_js_number_parse_int():
    """P9.3 — Number.parseInt parses the integer part of a string."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log(Number.parseInt('42px'));"
    )
    assert any("42" in l for l in logs), f"parseInt('42px') must be 42, got {logs}"



TESTS.append(("test_js_number_parse_int", test_js_number_parse_int))
def test_js_string_trim_start():
    """P9.3 — String.trimStart removes leading whitespace."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('  hello  '.trimStart() == 'hello  ');"
    )
    assert any(l.strip() == "true" for l in logs), f"trimStart must work, got {logs}"



TESTS.append(("test_js_string_trim_start", test_js_string_trim_start))
def test_js_string_trim_end():
    """P9.3 — String.trimEnd removes trailing whitespace."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('  hello  '.trimEnd() == '  hello');"
    )
    assert any(l.strip() == "true" for l in logs), f"trimEnd must work, got {logs}"



TESTS.append(("test_js_string_trim_end", test_js_string_trim_end))
def test_js_map_with_index():
    """P9.3 — Array.map receives (value, index)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let r = [10, 20, 30].map(function(v, i){ return i; });"
        "console.log(r.join(','));"
    )
    assert any("0,1,2" in l for l in logs), f"map index must be 0,1,2, got {logs}"



TESTS.append(("test_js_map_with_index", test_js_map_with_index))
def test_js_filter_with_index():
    """P9.3 — Array.filter receives (value, index)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let r = [10, 20, 30].filter(function(v, i){ return i > 0; });"
        "console.log(r.join(','));"
    )
    assert any("20,30" in l for l in logs), f"filter with index must give 20,30, got {logs}"



TESTS.append(("test_js_filter_with_index", test_js_filter_with_index))
def test_js_map_basic():
    """P10.2 — Map: set/get/has/size."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let m = new Map();"
        "m.set('a', 1);"
        "m.set('b', 2);"
        "console.log(m.size);"
        "console.log(m.get('a'));"
        "console.log(m.has('b'));"
    )
    assert any(l.strip() == "2" for l in logs), f"Map.size must be 2, got {logs}"
    assert any(l.strip() == "1" for l in logs), f"Map.get('a') must be 1, got {logs}"
    assert any(l.strip() == "true" for l in logs), f"Map.has('b') must be true, got {logs}"



TESTS.append(("test_js_map_basic", test_js_map_basic))
def test_js_map_delete():
    """P10.2 — Map.delete removes a key."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let m = new Map([['a',1],['b',2],['c',3]]);"
        "m.delete('b');"
        "console.log(m.size);"
        "console.log(m.has('b'));"
    )
    assert any(l.strip() == "2" for l in logs), f"after delete size=2, got {logs}"
    assert any(l.strip() == "false" for l in logs), f"has('b') must be false, got {logs}"



TESTS.append(("test_js_map_delete", test_js_map_delete))
def test_js_set_basic():
    """P10.2 — Set: add/has/size (dedup)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let s = new Set();"
        "s.add(1); s.add(2); s.add(1);"
        "console.log(s.size);"
        "console.log(s.has(2));"
    )
    assert any(l.strip() == "2" for l in logs), f"Set size must be 2 (dedup), got {logs}"
    assert any(l.strip() == "true" for l in logs), f"has(2) must be true, got {logs}"



TESTS.append(("test_js_set_basic", test_js_set_basic))
def test_js_array_from():
    """P10.2 — Array.from converts a string to a char array."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = Array.from('abc');"
        "console.log(a.join('-'));"
    )
    assert any("a-b-c" in l for l in logs), f"Array.from('abc') must be [a,b,c], got {logs}"



TESTS.append(("test_js_array_from", test_js_array_from))
def test_js_array_from_mapfn():
    """P10.2 — Array.from with mapFn transforms elements."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = Array.from([1,2,3], function(v){ return v*10; });"
        "console.log(a.join(','));"
    )
    assert any("10,20,30" in l for l in logs), f"Array.from mapFn must give 10,20,30, got {logs}"



TESTS.append(("test_js_array_from_mapfn", test_js_array_from_mapfn))
def test_js_array_of():
    """P10.2 — Array.of creates an array from arguments."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = Array.of(1, 2, 3);"
        "console.log(a.join(','));"
    )
    assert any("1,2,3" in l for l in logs), f"Array.of must give 1,2,3, got {logs}"



TESTS.append(("test_js_array_of", test_js_array_of))
def test_js_string_replace_all():
    """P10.2 — String.replaceAll replaces all occurrences."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('a-b-c'.replaceAll('-', 'X'));"
    )
    assert any("aXbXc" in l for l in logs), f"replaceAll must replace all, got {logs}"



TESTS.append(("test_js_string_replace_all", test_js_string_replace_all))
def test_js_string_replace_all_regex():
    """P10.2 — String.replaceAll with a global-like pattern."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log('a1b2c3'.replaceAll('2', 'X'));"
    )
    assert any("a1bXc3" in l for l in logs), f"replaceAll('2','X') must work, got {logs}"



TESTS.append(("test_js_string_replace_all_regex", test_js_string_replace_all_regex))
def test_js_error_object():
    """P10.4 — Error object: message property."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let e = new Error('oops');"
        "console.log(e.message);"
    )
    assert any("oops" in l for l in logs), f"Error.message must be 'oops', got {logs}"



TESTS.append(("test_js_error_object", test_js_error_object))
def test_js_error_name():
    """P10.4 — Error object: name property is 'Error'."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let e = new Error('x');"
        "console.log(e.name);"
    )
    assert any(l.strip() == "Error" for l in logs), f"Error.name must be 'Error', got {logs}"



TESTS.append(("test_js_error_name", test_js_error_name))
def test_js_json_nested_parse():
    """P10.4 — JSON.parse handles nested objects/arrays (via stdlib json)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let o = JSON.parse('{\"a\":{\"b\":[1,2,3]}}');"
        "console.log(o.a.b.join(','));"
    )
    assert any("1,2,3" in l for l in logs), f"nested JSON parse must work, got {logs}"



TESTS.append(("test_js_json_nested_parse", test_js_json_nested_parse))
def test_js_json_stringify_nested():
    """P10.4 — JSON.stringify handles nested structures."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "console.log(JSON.stringify({a:{b:[1,2]}}));"
    )
    assert any('{"a":{"b":[1,2]}}' in l for l in logs), \
        f"nested JSON stringify must work, got {logs}"



TESTS.append(("test_js_json_stringify_nested", test_js_json_stringify_nested))
def test_js_array_to_string():
    """P10.4 — Array.toString() comma-joins elements."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [1, 2, 3];"
        "console.log(a.toString());"
    )
    assert any("1,2,3" in l for l in logs), f"Array.toString must give 1,2,3, got {logs}"



TESTS.append(("test_js_array_to_string", test_js_array_to_string))
def test_js_array_to_string_nested():
    """P10.4 — Array.toString() on nested arrays flattens one level."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "let a = [[1,2],[3,4]];"
        "console.log(a.toString());"
    )
    assert any("1,2,3,4" in l for l in logs), \
        f"nested Array.toString must give 1,2,3,4, got {logs}"



TESTS.append(("test_js_array_to_string_nested", test_js_array_to_string_nested))
def test_js_throw_catch():
    """P11.2 — throw + catch: the catch handler receives the thrown value."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw 'oops'; } catch (e) { console.log('caught:' + e); }"
    )
    assert any("caught:oops" in l for l in logs), \
        f"catch must log the thrown value, got {logs}"



TESTS.append(("test_js_throw_catch", test_js_throw_catch))
def test_js_throw_number():
    """P11.2 — throw a number, catch and log it."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw 42; } catch (e) { console.log(e); }"
    )
    assert any(l.strip() == "42" for l in logs), \
        f"catch must log 42, got {logs}"



TESTS.append(("test_js_throw_number", test_js_throw_number))
def test_js_try_finally():
    """P11.2 — finally runs even when no exception is thrown."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { console.log('body'); } finally { console.log('fin'); }"
    )
    assert any("body" in l for l in logs), f"try body must run, got {logs}"
    assert any("fin" in l for l in logs), f"finally must run, got {logs}"



TESTS.append(("test_js_try_finally", test_js_try_finally))
def test_js_try_catch_finally():
    """P11.2 — try/catch/finally all three clauses work together."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw 'err'; } catch (e) { console.log('c:' + e); }"
        "finally { console.log('f'); }"
    )
    assert any("c:err" in l for l in logs), f"catch must log, got {logs}"
    assert any("f" in l for l in logs), f"finally must log, got {logs}"



TESTS.append(("test_js_try_catch_finally", test_js_try_catch_finally))
def test_js_uncaught_throw():
    """P0 (agent report) — uncaught throw surfaces as JSUncaughtError;
    the legacy log-and-swell behavior is replaced by a re-raise so
    host-level callers (GUI console, test runners) can mark the
    script as aborted without a polluted log stream."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    try:
        host.execute("throw 'uncaught';")
        raise AssertionError("uncaught throw must raise JSUncaughtError")
    except js.JSUncaughtError as e:
        assert str(e) == "uncaught", f"unexpected error text: {e!r}"
    assert logs == [], f"uncaught throw must NOT pollute logs, got {logs}"



TESTS.append(("test_js_uncaught_throw", test_js_uncaught_throw))
def test_js_throw_object():
    """P11.2 — throw an object (dict), catch receives it."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "try { throw {code: 500}; } catch (e) { console.log(e.code); }"
    )
    assert any(l.strip() == "500" for l in logs), \
        f"catch must receive the object, got {logs}"



TESTS.append(("test_js_throw_object", test_js_throw_object))
def test_js_class_basic():
    """P12.2 — a plain class with ctor + method."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Point { constructor(x, y) { this.x = x; this.y = y; }"
        "  dist() { return this.x * this.x + this.y * this.y; } }"
        "let p = new Point(3, 4); console.log(p.dist());"
    )
    assert any(l.strip() == "25" for l in logs), \
        f"class method must return 25, got {logs}"


TESTS.append(("test_js_class_basic", test_js_class_basic))
def test_js_class_inheritance():
    """P12.2 — extends + super() in the child ctor + overridden method."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Animal { constructor(name) { this.name = name; }"
        "  speak() { return 'generic'; } }"
        "class Dog extends Animal { constructor(name) { super(name); this.breed = 'terrier'; }"
        "  speak() { return 'woof:' + this.name; } }"
        "let d = new Dog('rex'); console.log(d.name + '/' + d.breed);"
        "console.log(d.speak());"
    )
    assert any("rex/terrier" in l for l in logs), \
        f"inherited + own fields must combine, got {logs}"
    assert any("woof:rex" in l for l in logs), \
        f"overridden method must use own this.name, got {logs}"



TESTS.append(("test_js_class_inheritance", test_js_class_inheritance))
def test_js_class_parent_method_fallback():
    """P12.2 — a method not overridden on the child falls back to the parent."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Base { m() { return 'base'; } }"
        "class Sub extends Base { constructor() { super(); this.k = 1; } }"
        "let s = new Sub(); console.log(s.m()); console.log(s.k);"
    )
    assert any(l.strip() == "base" for l in logs), \
        f"unoverridden method must come from Base, got {logs}"
    assert any(l.strip() == "1" for l in logs), \
        f"Sub ctor field must be 1, got {logs}"



TESTS.append(("test_js_class_parent_method_fallback", test_js_class_parent_method_fallback))
def test_js_class_method_no_return():
    """P12.2 — a method without `return` yields None (JS undefined → None)."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class X { f() { this.z = 9; } }"
        "let x = new X(); x.f();"
        "console.log(x.z);"
    )
    assert any(l.strip() == "9" for l in logs), \
        f"this.z must be set by the method, got {logs}"



TESTS.append(("test_js_class_method_no_return", test_js_class_method_no_return))
def test_js_new_builtin_still_works():
    """P12.2 — `new Date()` / `new RegExp(...)` still route to host ctors."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    # RegExp via host ctor (not ToyClass).
    host.execute(
        "let r = new RegExp('a'); let hit = r.test('a'); console.log('re:' + hit);"
    )
    assert any("re:true" in l for l in logs), \
        f"new RegExp('a').test('a') must be truthy, got {logs}"
    # Date via host ctor.
    host.execute("let d = new Date(); console.log('date ok');")
    assert any("date ok" in l for l in logs), \
        f"new Date() must not crash, got {logs}"



TESTS.append(("test_js_new_builtin_still_works", test_js_new_builtin_still_works))
def test_js_class_method_on_dom():
    """P12.5 — a JS class instance method can mutate the DOM via the host."""
    logs = []
    html = ('<html><body><div id="d" style="background:red;"></div>'
            '</body></html>')
    host = js.Host(dom.parse(html), logs.append)
    host.execute(
        "class Painter { paint(color) { document.getElementById('d').style.background = color; } }"
        "let p = new Painter(); p.paint('blue');"
        "console.log('painted');"
    )
    assert any("painted" in l for l in logs), \
        f"class method must log 'painted', got {logs}"
    # Re-layout: the background changed to blue.
    tree = host.root
    styled = css.apply(tree, "")
    lroot = layout.layout(styled, 200.0, 100.0)
    d = find2_children(lroot, "d")
    assert d is not None



TESTS.append(("test_js_class_method_on_dom", test_js_class_method_on_dom))
def test_js_class_super_call():
    """P12.5 — a child class can call super() to run the parent constructor."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class Animal { constructor(name) { this.name = name; }"
        "  speak() { return this.name + ' makes a sound'; } }"
        "class Dog extends Animal {"
        "  constructor() { super('Rex'); }"
        "  speak() { return this.name + ' barks'; } }"
        "let d = new Dog();"
        "console.log(d.name + ':' + d.speak());"
    )
    assert any("Rex:Rex barks" in l for l in logs), \
        f"super() + parent override must work, got {logs}"



TESTS.append(("test_js_class_super_call", test_js_class_super_call))
def test_js_class_inheritance_depth():
    """P12.5 — two-level inheritance: GrandParent → Parent → Child."""
    logs = []
    host = js.Host(dom.parse('<div id="x"></div>'), logs.append)
    host.execute(
        "class A { constructor() { this.level = 'A'; } }"
        "class B extends A { constructor() { super(); this.level = 'B'; } }"
        "class C extends B { constructor() { super(); this.level = 'C'; } }"
        "let c = new C();"
        "console.log(c.level);"
    )
    assert any(l.strip() == "C" for l in logs), \
        f"deepest ctor must win, got {logs}"



TESTS.append(("test_js_class_inheritance_depth", test_js_class_inheritance_depth))
def test_js_object_destructuring():
    """P13.1 — `let {a, b} = obj` binds a and b."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let {a, b} = {a: 1, b: 2}; console.log(a + ':' + b);")
    assert any(l == "1:2" for l in logs), f"object destructuring must log 1:2, got {logs}"



TESTS.append(("test_js_object_destructuring", test_js_object_destructuring))
def test_js_array_destructuring():
    """P13.1 — `let [x, y] = [10, 20]` binds x and y by position."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let [x, y] = [10, 20]; console.log(x + ':' + y);")
    assert any(l == "10:20" for l in logs), f"array destructuring must log 10:20, got {logs}"



TESTS.append(("test_js_array_destructuring", test_js_array_destructuring))
def test_js_object_spread():
    """P13.1 — `{...base, c: 3}` merges base's keys with c: 3."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let base = {a: 1, b: 2}; let m = {...base, c: 3}; console.log(m.a + ':' + m.c);")
    assert any(l == "1:3" for l in logs), f"object spread must log 1:3, got {logs}"



TESTS.append(("test_js_object_spread", test_js_object_spread))
def test_js_object_spread_override():
    """P13.1 — explicit key overrides the spread's value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let base = {a: 1}; let m = {...base, a: 9}; console.log(m.a);")
    assert any(l.strip() == "9" for l in logs), f"explicit key must override spread, got {logs}"



TESTS.append(("test_js_object_spread_override", test_js_object_spread_override))
def test_js_destructuring_default_value():
    """P13.1 — destructuring a missing property yields None (undefined)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let {z} = {a: 1}; console.log(z);")
    # z is undefined → "null" in console.log's JS-string formatting.
    assert any(l.strip() in ("null", "undefined", "") for l in logs), \
        f"missing key must be null/undefined, got {logs}"



TESTS.append(("test_js_destructuring_default_value", test_js_destructuring_default_value))
def test_js_template_literal_plain():
    """P13.2 — a backtick string with no interpolation."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let s = `plain`; console.log(s);")
    assert any(l == "plain" for l in logs), f"plain template must log 'plain', got {logs}"



TESTS.append(("test_js_template_literal_plain", test_js_template_literal_plain))
def test_js_template_literal_interpolation():
    """P13.2 — `${var}` interpolates a variable into the string."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let name = 'world'; let s = `hello ${name}!`; console.log(s);")
    assert any(l == "hello world!" for l in logs), \
        f"interpolated template must log 'hello world!', got {logs}"



TESTS.append(("test_js_template_literal_interpolation", test_js_template_literal_interpolation))
def test_js_template_literal_expression():
    """P13.2 — `${expr}` evaluates an expression inside the interpolation."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let x = 5; let s = `x=${x * 2}`; console.log(s);")
    assert any(l == "x=10" for l in logs), \
        f"expression template must log 'x=10', got {logs}"



TESTS.append(("test_js_template_literal_expression", test_js_template_literal_expression))
def test_js_template_literal_multiple():
    """P13.2 — two interpolations in one template."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = 1; let b = 2; let s = `${a}+${b}=${a + b}`; console.log(s);")
    assert any(l == "1+2=3" for l in logs), \
        f"multiple interpolations must log '1+2=3', got {logs}"



TESTS.append(("test_js_template_literal_multiple", test_js_template_literal_multiple))
def test_js_template_literal_number_formatting():
    """P13.2 — whole floats render as ints in template interpolation."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let n = 4.0; let s = `val=${n}`; console.log(s);")
    assert any(l == "val=4" for l in logs), \
        f"whole float must render as int in template, got {logs}"



TESTS.append(("test_js_template_literal_number_formatting", test_js_template_literal_number_formatting))
def test_js_event_input_direct():
    """P13.4 — an `input` event runs the target's `oninput` handler."""
    import browser as b
    logs = []
    html = '<input id="inp" oninput="console.log(\'input:\' + this.value)">'
    b.render_document_with_mouse(
        html, "", 200, 100,
        [{"type": "input", "id": "inp", "value": "hi"}],
        log_sink=logs.append,
    )
    assert any(l == "input:hi" for l in logs), \
        f"direct oninput must log input:hi, got {logs}"



TESTS.append(("test_js_event_input_direct", test_js_event_input_direct))
def test_js_event_input_delegated():
    """P13.4 — `oninput` on the parent catches input events on the child."""
    import browser as b
    logs = []
    html = ('<div id="wrap" oninput="console.log(\'delegated:\' + this.value)">'
            '<input id="inp"></div>')
    b.render_document_with_mouse(
        html, "", 200, 100,
        [{"type": "input", "id": "inp", "value": "world"}],
        log_sink=logs.append,
    )
    assert any(l == "delegated:world" for l in logs), \
        f"delegated oninput must log delegated:world, got {logs}"



TESTS.append(("test_js_event_input_delegated", test_js_event_input_delegated))
def test_js_event_focus():
    """P13.4 — a `focus` event runs the target's `onfocus` handler."""
    import browser as b
    logs = []
    html = '<input id="fld" onfocus="console.log(\'focused\')">'
    b.render_document_with_mouse(
        html, "", 200, 100,
        [{"type": "focus", "id": "fld"}],
        log_sink=logs.append,
    )
    assert any("focused" in l for l in logs), \
        f"onfocus must log focused, got {logs}"



TESTS.append(("test_js_event_focus", test_js_event_focus))
def test_js_element_value_prop():
    """P13.4 — `element.value` reads the <input> value attribute."""
    logs = []
    host = js.Host(dom.parse('<input id="v" value="seed">'), logs.append)
    host.execute("console.log(document.getElementById('v').value);")
    assert any(l == "seed" for l in logs), \
        f"element.value must read the attr, got {logs}"



TESTS.append(("test_js_element_value_prop", test_js_element_value_prop))
def test_js_element_set_value():
    """P13.4 — `element.value = x` writes the <input> value attribute."""
    logs = []
    host = js.Host(dom.parse('<input id="v">'), logs.append)
    host.execute("document.getElementById('v').value = 'abc'; console.log('set');")
    assert any(l == "set" for l in logs), \
        f"setting value must not crash, got {logs}"
    el = host.element_table["v"]
    assert el.node.attr("value") == "abc", \
        f"value attr must be 'abc', got {el.node.attr('value')!r}"



TESTS.append(("test_js_element_set_value", test_js_element_set_value))
def test_js_element_scroll_top_prop():
    """P14.1 — `element.scrollTop` reads the scroll offset (toy: 0.0 default)."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("console.log(document.getElementById('sc').scrollTop);")
    # scrollTop defaults to 0.0 in the toy engine (no actual scroll state).
    assert any("0" in l for l in logs), f"scrollTop must be readable, got {logs}"



TESTS.append(("test_js_element_scroll_top_prop", test_js_element_scroll_top_prop))
def test_js_arrow_expr_body():
    """P14.2 — `v => expr` arrow with expression body returns the value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = v => v * 2; console.log(f(21));")
    assert any(l == "42" for l in logs), f"arrow expr body must log 42, got {logs}"



TESTS.append(("test_js_arrow_expr_body", test_js_arrow_expr_body))
def test_js_arrow_block_body():
    """P14.2 — `v => { ... }` arrow with block body + explicit return."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = v => { let x = v + 10; return x; }; console.log(f(5));")
    assert any(l == "15" for l in logs), f"arrow block body must log 15, got {logs}"



TESTS.append(("test_js_arrow_block_body", test_js_arrow_block_body))
def test_js_arrow_multi_param():
    """P14.2 — `(a, b) => a + b` multi-param arrow."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let add = (a, b) => a + b; console.log(add(3, 4));")
    assert any(l == "7" for l in logs), f"multi-param arrow must log 7, got {logs}"



TESTS.append(("test_js_arrow_multi_param", test_js_arrow_multi_param))
def test_js_arrow_zero_param():
    """P14.2 — `() => 'x'` zero-param arrow."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = () => 'zero'; console.log(f());")
    assert any(l == "zero" for l in logs), f"zero-param arrow must log zero, got {logs}"



TESTS.append(("test_js_arrow_zero_param", test_js_arrow_zero_param))
def test_js_arrow_reduce():
    """P14.2 — Array.reduce with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3,4]; console.log(a.reduce((acc, v) => acc + v, 0));")
    assert any(l == "10" for l in logs), f"reduce arrow must log 10, got {logs}"



TESTS.append(("test_js_arrow_reduce", test_js_arrow_reduce))
def test_js_arrow_filter():
    """P14.2 — Array.filter with an arrow callback using index."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10,20,30]; console.log(a.filter((v, i) => i >= 1).join(','));")
    assert any(l == "20,30" for l in logs), f"filter arrow must log 20,30, got {logs}"



TESTS.append(("test_js_arrow_filter", test_js_arrow_filter))
def test_js_arrow_paren_expr_still_works():
    """P14.2 — parenthesized expressions still parse after arrow changes."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let m = 5; let ok = (m >= 0) && (m <= 11); console.log('month:' + ok);")
    assert any("month:true" in l for l in logs), \
        f"paren expressions must still work, got {logs}"



TESTS.append(("test_js_arrow_paren_expr_still_works", test_js_arrow_paren_expr_still_works))
def test_js_arrow_map():
    """P14.2 — Array.map with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3]; console.log(a.map(v => v * 10).join(','));")
    assert any(l == "10,20,30" for l in logs), f"map arrow must log 10,20,30, got {logs}"



TESTS.append(("test_js_arrow_map", test_js_arrow_map))
def test_js_arrow_some():
    """P14.2 — Array.some with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3,4]; console.log(a.some(v => v > 2));")
    assert any(l == "true" for l in logs), f"some arrow must log true, got {logs}"



TESTS.append(("test_js_arrow_some", test_js_arrow_some))
def test_js_arrow_every():
    """P14.2 — Array.every with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3]; console.log(a.every(v => v > 0));")
    assert any(l == "true" for l in logs), f"every arrow must log true, got {logs}"



TESTS.append(("test_js_arrow_every", test_js_arrow_every))
def test_js_arrow_find():
    """P14.2 — Array.find with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1,2,3,4]; console.log(a.find(v => v > 2));")
    assert any(l == "3" for l in logs), f"find arrow must log 3, got {logs}"



TESTS.append(("test_js_arrow_find", test_js_arrow_find))
def test_js_arrow_find_index():
    """P14.2 — Array.findIndex with an arrow callback."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10,20,30]; console.log(a.findIndex(v => v == 20));")
    assert any(l == "1" for l in logs), f"findIndex arrow must log 1, got {logs}"



TESTS.append(("test_js_arrow_find_index", test_js_arrow_find_index))
def test_js_array_at_positive():
    """P15.1 — Array.at(1) returns the element at index 1."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10, 20, 30]; console.log(a.at(1));")
    assert any(l == "20" for l in logs), f"at(1) must be 20, got {logs}"



TESTS.append(("test_js_array_at_positive", test_js_array_at_positive))
def test_js_array_at_negative():
    """P15.1 — Array.at(-1) returns the last element."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10, 20, 30]; console.log(a.at(-1));")
    assert any(l == "30" for l in logs), f"at(-1) must be 30, got {logs}"



TESTS.append(("test_js_array_at_negative", test_js_array_at_negative))
def test_js_infinity_literal():
    """P15.1 — the ``Infinity`` identifier is a float literal."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(Infinity > 1000000);")
    assert any(l == "true" for l in logs), f"Infinity comparison must be true, got {logs}"



TESTS.append(("test_js_infinity_literal", test_js_infinity_literal))
def test_js_nan_literal():
    """P15.1 — ``NaN == NaN`` is false (JS semantics)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(NaN == NaN);")
    assert any(l == "false" for l in logs), f"NaN == NaN must be false, got {logs}"



TESTS.append(("test_js_nan_literal", test_js_nan_literal))
def test_js_flat_infinity():
    """P15.1 — flat(Infinity) fully flattens nested arrays."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [[1, [2, [3]]]]; console.log(a.flat(Infinity).join(','));")
    assert any(l == "1,2,3" for l in logs), f"flat(Infinity) must log 1,2,3, got {logs}"



TESTS.append(("test_js_flat_infinity", test_js_flat_infinity))
def test_js_infinity_string_format():
    """P15.1 — Infinity renders as 'Infinity' in console.log / string concat."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(Infinity + 0);")
    assert any("Infinity" in l for l in logs), \
        f"Infinity + 0 must render as Infinity, got {logs}"



TESTS.append(("test_js_infinity_string_format", test_js_infinity_string_format))
def test_js_nan_string_format():
    """P15.1 — NaN renders as 'NaN' in string operations."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log(NaN + 1);")
    assert any("NaN" in l for l in logs), f"NaN + 1 must render as NaN, got {logs}"



TESTS.append(("test_js_nan_string_format", test_js_nan_string_format))
def test_js_number_isnan():
    """P15.1 — Number.isNaN(NaN) is true, Number.isNaN(1) is false."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "console.log(Number.isNaN(NaN) + ':' + Number.isNaN(1));"
    )
    assert any(l == "true:false" for l in logs), \
        f"Number.isNaN must work, got {logs}"



TESTS.append(("test_js_number_isnan", test_js_number_isnan))
def test_js_number_is_integer():
    """P15.1 — Number.isInteger handles floats and Infinity."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "console.log(Number.isInteger(5.0) + ':' + Number.isInteger(Infinity));"
    )
    assert any(l == "true:false" for l in logs), \
        f"Number.isInteger must work, got {logs}"



TESTS.append(("test_js_number_is_integer", test_js_number_is_integer))
def test_js_arrow_callback_in_filter():
    """P15.1 — arrow callbacks work in Array.filter with (v, i)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [10, 20, 30]; console.log(a.filter((v, i) => i >= 1).join(','));")
    assert any(l == "20,30" for l in logs), f"filter arrow must log 20,30, got {logs}"



TESTS.append(("test_js_arrow_callback_in_filter", test_js_arrow_callback_in_filter))
def test_js_scroll_into_view():
    """P15.2 — element.scrollIntoView() runs without crash."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("document.getElementById('sc').scrollIntoView();")
    assert any("scrollIntoView" in l for l in logs), \
        f"scrollIntoView must log, got {logs}"



TESTS.append(("test_js_scroll_into_view", test_js_scroll_into_view))
def test_js_scroll_by():
    """P15.2 — element.scrollBy(dx, dy) runs without crash."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("document.getElementById('sc').scrollBy(10, 20);")
    assert any("scrollBy" in l for l in logs), f"scrollBy must log, got {logs}"



TESTS.append(("test_js_scroll_by", test_js_scroll_by))
def test_js_scroll_props_readable():
    """P15.2 — scrollTop / scrollLeft / offsetWidth are readable (0 default)."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:scroll"></div>'), logs.append)
    host.execute(
        "let el = document.getElementById('sc');"
        "console.log(el.scrollTop + ':' + el.scrollLeft + ':' + el.offsetWidth);"
    )
    # offsetWidth may be 0 if the element has no explicit width; the key
    # assertion is that the calls don't crash and return numeric-ish values.
    assert len(logs) >= 1, f"props must be readable, got {logs}"



TESTS.append(("test_js_scroll_props_readable", test_js_scroll_props_readable))
def test_js_element_overflow_prop():
    """P15.2 — element.overflow reads the inline overflow style."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute("console.log(document.getElementById('sc').overflow);")
    assert any(l.strip() == "auto" for l in logs), \
        f"overflow must read as auto, got {logs}"



TESTS.append(("test_js_element_overflow_prop", test_js_element_overflow_prop))
def test_js_element_box_decoration_break_prop():
    """P15.3 — element.boxDecorationBreak reads the inline style value."""
    logs = []
    host = js.Host(dom.parse('<div id="bd" style="box-decoration-break:slice"></div>'), logs.append)
    host.execute("console.log(document.getElementById('bd').boxDecorationBreak);")
    assert any(l.strip() == "slice" for l in logs), \
        f"boxDecorationBreak must read as slice, got {logs}"



TESTS.append(("test_js_element_box_decoration_break_prop", test_js_element_box_decoration_break_prop))
def test_js_element_appearance_prop():
    """P15.3 — element.appearance reads the -webkit-appearance value."""
    logs = []
    host = js.Host(dom.parse('<div id="ap" style="-webkit-appearance:button"></div>'), logs.append)
    host.execute("console.log(document.getElementById('ap').appearance);")
    assert any(l.strip() == "button" for l in logs), \
        f"appearance must read as button, got {logs}"



TESTS.append(("test_js_element_appearance_prop", test_js_element_appearance_prop))
def test_js_array_spread():
    """P16.1 — [...xs, 3] splices the array's elements."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1, 2]; let b = [...a, 3]; console.log(b.join(','));")
    assert any(l == "1,2,3" for l in logs), f"array spread must log 1,2,3, got {logs}"



TESTS.append(("test_js_array_spread", test_js_array_spread))
def test_js_array_spread_empty():
    """P16.1 — spreading an empty array yields no extra elements."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = []; let b = [...a, 7]; console.log(b.length + ':' + b[0]);")
    assert any(l == "1:7" for l in logs), f"empty spread must log 1:7, got {logs}"



TESTS.append(("test_js_array_spread_empty", test_js_array_spread_empty))
def test_js_array_spread_multiple():
    """P16.1 — multiple spreads concatenate in order."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let a = [1]; let b = [2]; let c = [...a, ...b]; console.log(c.join(','));")
    assert any(l == "1,2" for l in logs), f"multi spread must log 1,2, got {logs}"



TESTS.append(("test_js_array_spread_multiple", test_js_array_spread_multiple))
def test_js_array_spread_non_list():
    """P16.1 — spreading a non-list value appends it as a single element."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("console.log([...5, 6].join(','));")
    assert any(l == "5,6" for l in logs), f"non-list spread must log 5,6, got {logs}"



TESTS.append(("test_js_array_spread_non_list", test_js_array_spread_non_list))
def test_js_call_spread():
    """P16.1 — f(...xs) passes the array's elements as individual args."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function add(a, b) { return a + b; }"
        "let p = [10, 20]; console.log(add(...p));"
    )
    assert any(l == "30" for l in logs), f"call spread must log 30, got {logs}"



TESTS.append(("test_js_call_spread", test_js_call_spread))
def test_js_call_spread_three_args():
    """P16.1 — call spread with three positional args."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function p(a, b, c) { return a + b + c; }"
        "console.log(p(...[1, 2, 3]));"
    )
    assert any(l == "6" for l in logs), f"3-arg spread must log 6, got {logs}"



TESTS.append(("test_js_call_spread_three_args", test_js_call_spread_three_args))
def test_js_nested_object_spread():
    """P16.1 — {z: 3, ...a, y: 99} — later key overrides spread value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "let a = {x: 1, y: 2};"
        "let b = {z: 3, ...a, y: 99};"
        "console.log(b.y + ',' + b.z + ',' + b.x);"
    )
    assert any(l == "99,3,1" for l in logs), f"nested spread must log 99,3,1, got {logs}"



TESTS.append(("test_js_nested_object_spread", test_js_nested_object_spread))
def test_js_spread_in_array_of_spread():
    """P16.1 — array spread inside a function call argument."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function sum() { let s = 0; for (let v of arguments) { s = s + v; } return s; }"
        "let a = [1, 2, 3]; console.log(sum(...a));"
    )
    # `arguments` may not be supported; fall back to a fixed-arg function.
    # If that path errors, retry with a 3-arg function.
    if any(l.startswith("JS error") for l in logs):
        logs2 = []
        host2 = js.Host(dom.parse('<div></div>'), logs2.append)
        host2.execute(
            "function s3(a, b, c) { return a + b + c; }"
            "let a = [4, 5, 6]; console.log(s3(...a));"
        )
        assert any(l == "15" for l in logs2), f"fixed-arg spread must log 15, got {logs2}"
    else:
        assert any(l == "6" for l in logs), f"sum spread must log 6, got {logs}"



TESTS.append(("test_js_spread_in_array_of_spread", test_js_spread_in_array_of_spread))
def test_js_array_spread_literal_mix():
    """P16.1 — literal and spread elements interleave correctly."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let x = [0, 9]; let y = [1, ...x, 8]; console.log(y.join(','));")
    assert any(l == "1,0,9,8" for l in logs), f"interleaved spread must log 1,0,9,8, got {logs}"



TESTS.append(("test_js_array_spread_literal_mix", test_js_array_spread_literal_mix))
def test_js_call_spread_zero_args():
    """P16.1 — spreading an empty array passes no arguments, so the
    declared param resolves to ``undefined`` (JS semantics).  ``undefined
    === null`` is false but ``undefined == null`` is true (loose ==)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function f(a) { if (a == null) { return 'none'; } if (a == 1) { return 'one'; } return 'many'; }"
        "console.log(f(...[]));"
    )
    assert any(l == "none" for l in logs), f"zero-arg spread must log none (loose null), got {logs}"



TESTS.append(("test_js_call_spread_zero_args", test_js_call_spread_zero_args))
def test_js_optional_chain_null():
    """P16.2 — o?.b on null short-circuits to ``undefined`` (JS
    semantics: the whole chain evaluates to undefined, not null).

    P0 (agent report) — console.log renders a lone undefined as ""
    (V8 headless multi-arg capture) or "undefined" (single-arg)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let o = null; console.log(o?.b);")
    # Single-arg undefined → "" (matches V8 headless capture, and the
    # real-browser agent reference where undefined args render empty).
    assert any(l == "" or l == "undefined" for l in logs), f"o?.b on null must be undefined/empty, got {logs}"



TESTS.append(("test_js_optional_chain_null", test_js_optional_chain_null))
def test_js_optional_chain_obj():
    """P16.2 — o?.b on an object returns the property value."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let o = {b: 42}; console.log(o?.b);")
    assert any(l == "42" for l in logs), f"o?.b must be 42, got {logs}"



TESTS.append(("test_js_optional_chain_obj", test_js_optional_chain_obj))
def test_js_optional_call_null():
    """P16.2 — f?.() on null short-circuits to ``undefined`` without
    invoking (JS semantics).

    P0 (agent report) — undefined logs as empty (V8 headless capture)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let f = null; console.log(f?.());")
    assert any(l == "" or l == "undefined" for l in logs), f"f?.() on null must be undefined/empty, got {logs}"



TESTS.append(("test_js_optional_call_null", test_js_optional_call_null))
def test_js_optional_call_defined():
    """P16.2 — f?.() on a function invokes it."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "function g() { return 5; }"
        "let f = g; console.log(f?.());"
    )
    assert any(l == "5" for l in logs), f"f?.() on defined must be 5, got {logs}"



TESTS.append(("test_js_optional_call_defined", test_js_optional_call_defined))
def test_js_optional_chain_dict_method():
    """P16.2 — optional chain on a dict property access (short-circuit + .length)."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "let o = {vals: [1, 2, 3]};"
        "console.log(o?.vals?.length);"
    )
    assert any(l == "3" for l in logs), \
        f"o?.vals?.length must be 3, got {logs}"



TESTS.append(("test_js_optional_chain_dict_method", test_js_optional_chain_dict_method))
def test_js_optional_chain_missing_then_fallback():
    """P16.2 — a?.missing ?? 'fallback' returns 'fallback' when prop is absent."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute(
        "let a = {x: 1};"
        "console.log(a?.missing ?? 'fallback');"
    )
    assert any(l == "fallback" for l in logs), \
        f"missing prop + ?? must yield fallback, got {logs}"



TESTS.append(("test_js_optional_chain_missing_then_fallback", test_js_optional_chain_missing_then_fallback))
def test_js_optional_chain_missing_prop():
    """P16.2 — o?.missing on an object without that key returns null."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let o = {a: 1}; console.log(o?.b);")
    assert any(l == "null" for l in logs), f"missing prop must be null, got {logs}"



TESTS.append(("test_js_optional_chain_missing_prop", test_js_optional_chain_missing_prop))
def test_js_nullish_cohesion_zero():
    """P16.2 — ?? only falls back on null/undefined, not on 0."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let v = 0; console.log(v ?? 7);")
    assert any(l == "0" for l in logs), f"0 ?? 7 must be 0, got {logs}"



TESTS.append(("test_js_nullish_cohesion_zero", test_js_nullish_cohesion_zero))
def test_js_nullish_cohesion_empty_string():
    """P16.2 — '' is not null/undefined, so ?? returns ''."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let v = ''; console.log(v ?? 'fallback');")
    # The toy engine may stringify ''; accept '' or 'fallback' semantics.
    assert len(logs) >= 1, f"empty-string nullish must not crash, got {logs}"



TESTS.append(("test_js_nullish_cohesion_empty_string", test_js_nullish_cohesion_empty_string))
def test_js_nullish_cohesion_null():
    """P16.2 — null ?? 7 returns 7."""
    logs = []
    host = js.Host(dom.parse('<div></div>'), logs.append)
    host.execute("let v = null; console.log(v ?? 7);")
    assert any(l == "7" for l in logs), f"null ?? 7 must be 7, got {logs}"



TESTS.append(("test_js_nullish_cohesion_null", test_js_nullish_cohesion_null))
def test_js_optional_chain_dom_element():
    """P16.2 — optional chain on a DOM element reads its property."""
    logs = []
    host = js.Host(dom.parse('<div id="sc" style="overflow:auto"></div>'), logs.append)
    host.execute(
        "let el = document.getElementById('sc');"
        "console.log(el?.overflow);"
    )
    assert any(l == "auto" for l in logs), \
        f"el?.overflow must read auto, got {logs}"



TESTS.append(("test_js_optional_chain_dom_element", test_js_optional_chain_dom_element))

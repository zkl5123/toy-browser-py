"""R7-J01 — JS language value semantics (24-probe contract).

Round-7 assessment (逐项改进实施方案.md §4 / semantic-probes.json):
24 pure-ECMAScript probes compared against a fresh Node/V8 reference
recorded 13 mismatches.  The probes and their expected outputs are
frozen in ``assessments/20261018-round7/semantic-probes.json``; this
module replays every one of them against the toy engine and pins the
fixed semantics with targeted unit tests:

* strict equality is type-sensitive (``true === 1`` → false; Python's
  bool-is-int must not leak through);
* ``Object.is`` (SameValue: NaN === NaN, +0 !== -0);
* String observable indices / length count UTF-16 code units
  (``"😀".length === 2``, ``charCodeAt(0) === 55357``);
* default ``Array.sort`` compares string forms in UTF-16 order
  (``[2,10,1].sort()`` → ``1,10,2``) and honours a comparator;
* callable ``Number`` / ``Boolean`` constructors + global
  ``parseInt`` / ``parseFloat`` (radix support);
* property enumeration puts integer-index keys first;
* the ``in`` operator and array holes (``0 in [,]`` → false).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toy_browser.document import dom
from toy_browser.scripting import js

TESTS = []


def _reg(fn):
    TESTS.append((fn.__name__, fn))
    return fn


_PROBE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assessments", "20261018-round7", "semantic-probes.json")


def _run(code: str):
    """Run a JS snippet; return (logs, error_or_None) exactly like the
    assessment's toy_js_runner (script + event-loop drain)."""
    logs: list = []
    err = None
    try:
        host = js.Host(dom.parse("<html><body></body></html>"), logs.append)
        host.execute(code)
        host.flush_timers()
        host.flush_microtasks()
        host.flush_raf()
    except js.JSUncaughtError as e:
        err = f"JSUncaughtError: {e}"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    return logs, err


def _logs(code: str) -> list:
    """Run and assert no error; return the log lines."""
    logs, err = _run(code)
    assert err is None, f"{code!r} raised {err}"
    return logs


# ---------------------------------------------------------------------------
# 1. The frozen 24-probe contract
# ---------------------------------------------------------------------------

@_reg
def test_r7_j01_semantic_probe_contract():
    """All 24 semantic-probes.json cases match the Node/V8 reference."""
    with open(_PROBE_PATH, encoding="utf-8") as fh:
        probes = json.load(fh)
    assert probes["total"] == 24 and len(probes["cases"]) == 24
    diffs = []
    for case in probes["cases"]:
        logs, err = _run(case["code"])
        ref = case["reference"]
        if logs != ref["logs"] or (err is None) != (ref["error"] is None):
            diffs.append(f"{case['id']}: got logs={logs!r} err={err!r}, "
                         f"want logs={ref['logs']!r} err={ref['error']!r}")
    assert not diffs, "semantic probe mismatches: " + "; ".join(diffs[:6])


@_reg
def test_r7_j01_probe_file_fully_matched():
    """The recorded mismatch list (13) is now zero — every probe's
    ``match`` flag can be re-derived as true."""
    with open(_PROBE_PATH, encoding="utf-8") as fh:
        probes = json.load(fh)
    bad = [c["id"] for c in probes["cases"] if not c.get("match")]
    # The 13 recorded mismatches are the R7-J01 fix targets.
    assert len(bad) == 13, f"expected 13 recorded mismatches, got {bad}"
    for case in probes["cases"]:
        logs, err = _run(case["code"])
        ref = case["reference"]
        assert logs == ref["logs"] and (err is None) == (ref["error"] is None), \
            f"{case['id']} still mismatches"


# ---------------------------------------------------------------------------
# 2. Strict / loose equality and SameValue
# ---------------------------------------------------------------------------

@_reg
def test_r7_j01_strict_eq_bool_not_number():
    logs = _logs(
        "console.log(true === 1, false === 0, true === true, false === false, "
        "1 === true, 0 === false, true === 2);")
    assert logs[0].split() == ["false", "false", "true", "true", "false",
                               "false", "false"], logs


@_reg
def test_r7_j01_loose_eq_bool_number_still_coerces():
    # Loose ``==`` keeps JS coercion (true == 1 is true).
    logs = _logs("console.log(true == 1, false == 0, true == 2, '' == 0, "
                 "null == undefined, NaN == NaN);")
    assert logs[0].split() == ["true", "true", "false", "true", "true",
                               "false"], logs


@_reg
def test_r7_j01_strict_ne_bool():
    logs = _logs("console.log(true !== 1, true !== true, 1 !== true);")
    assert logs[0].split() == ["true", "false", "true"], logs


@_reg
def test_r7_j01_object_is_same_value():
    logs = _logs(
        "console.log(Object.is(-0, 0), Object.is(0, -0), Object.is(NaN, NaN), "
        "Object.is(1, 1), Object.is(1, 2), Object.is('a', 'a'), Object.is(null, null), "
        "Object.is(null, undefined), Object.is(true, true), Object.is(true, 1));")
    assert logs[0].split() == ["false", "false", "true", "true", "false",
                               "true", "true", "false", "true", "false"], logs


@_reg
def test_r7_j01_nan_strict_eq_still_false():
    logs = _logs("console.log(NaN === NaN, NaN !== NaN);")
    assert logs[0].split() == ["false", "true"], logs


# ---------------------------------------------------------------------------
# 3. UTF-16 string observables
# ---------------------------------------------------------------------------

@_reg
def test_r7_j01_utf16_length():
    logs = _logs('console.log("".length, "abc".length, "\\u00e9".length, '
                 '"\\ud83d\\ude00".length, "a\\ud83d\\ude00b".length);')
    # é is BMP (1 unit); 😀 is a surrogate pair (2 units).
    assert logs[0].split() == ["0", "3", "1", "2", "4"], logs


@_reg
def test_r7_j01_utf16_charcode_and_charat():
    logs = _logs('var s = "\\ud83d\\ude00";'
                 'console.log(s.charCodeAt(0), s.charCodeAt(1), s.charCodeAt(2), '
                 's.charAt(0) === s.charAt(0), s.length);')
    parts = logs[0].split()
    assert parts[0] == "55357" and parts[1] == "56832" and parts[2] == "NaN", parts
    assert parts[4] == "2", parts


@_reg
def test_r7_j01_utf16_index_and_slice():
    logs = _logs('var s = "a\\ud83d\\ude00b";'
                 'console.log(s[0], s[3], s.substring(1, 3).length, '
                 's.indexOf("\\ud83d\\ude00"), s.codePointAt(1), s.at(1) === "\\ud83d\\ude00", '
                 's.length);')
    parts = logs[0].split()
    assert parts[0] == "a" and parts[1] == "b", parts
    assert parts[2] == "2", parts          # substring(1,3) is the pair
    assert parts[3] == "1", parts          # indexOf is UTF-16 based
    assert parts[4] == "128512", parts     # codePointAt recombines
    assert parts[5] == "true", parts       # at() returns the whole code point
    assert parts[6] == "4", parts          # a + pair + b = 4 code units


@_reg
def test_r7_j01_string_for_of_iterates_code_points():
    # Iteration stays code-point based (not UTF-16 units): one element
    # for 😀, whose .length is still 2 code units; the spread also
    # yields code points (1 element), not code units (2).
    logs = _logs('var out = []; for (const c of "\\ud83d\\ude00x") { out.push(c.length); } '
                 'console.log(out.join(","), [...""+"\\ud83d\\ude00"].length);')
    assert logs[0].split() == ["2,1", "1"], logs


@_reg
def test_r7_j01_ascii_string_methods_unchanged():
    logs = _logs("console.log('A'.charCodeAt(0), 'abc'.charAt(1), "
                 "'abcdef'.substring(1, 3), 'abc'.indexOf('b'), 'hello'.at(-1));")
    assert logs[0].split() == ["65", "b", "bc", "1", "o"], logs


# ---------------------------------------------------------------------------
# 4. Array ordering / holes
# ---------------------------------------------------------------------------

@_reg
def test_r7_j01_default_sort_is_string_order():
    logs = _logs("console.log([2,10,1].sort().join(','), "
                 "[30,10,20].sort().join(','), ['b','a','c'].sort().join(','));")
    assert logs[0].split() == ["1,10,2", "10,20,30", "a,b,c"], logs


@_reg
def test_r7_j01_sort_with_comparator():
    logs = _logs("console.log([2,10,1].sort(function(a,b){return a-b;}).join(','), "
                 "[2,10,1].sort(function(a,b){return b-a;}).join(','));")
    assert logs[0].split() == ["1,2,10", "10,2,1"], logs


@_reg
def test_r7_j01_sort_holes_go_last():
    logs = _logs("var a = [3, , 1]; var s = a.sort(); "
                 "console.log(s.length, 1 in s, 2 in s, s.join(','));")
    parts = logs[0].split()
    assert parts[0] == "3" and parts[1] == "true" and parts[2] == "false", parts
    assert parts[3] == "1,3,", parts


@_reg
def test_r7_j01_in_operator():
    logs = _logs("console.log(0 in [,], 0 in [undefined], 1 in [,], "
                 "'x' in {x: undefined}, 'y' in {x: undefined}, "
                 "'length' in [], 0 in 'abc', 3 in 'abc');")
    parts = logs[0].split()
    assert parts == ["false", "true", "false", "true", "false", "true",
                     "true", "false"], parts


@_reg
def test_r7_j01_holes_skipped_by_iteration_methods():
    logs = _logs(
        "var a = [1, , 3];"
        "var seen = []; a.forEach(function(v, i){ seen.push(i + ':' + v); });"
        "console.log(seen.join(','));"
        "console.log(a.map(function(v){ return v * 2; }).join(','));"
        "console.log(a.filter(function(){ return true; }).length);"
        "var sum = 0; [1, , 3].reduce(function(acc, v){ return acc + v; }, 0); "
        "console.log([1, , 3].reduce(function(acc, v){ return acc + v; }, 0));")
    assert logs[0] == "0:1,2:3", logs
    # map preserves the hole (renders empty in join)
    assert logs[1] == "2,,6", logs
    assert logs[2] == "2", logs
    assert logs[3] == "4", logs


@_reg
def test_r7_j01_hole_vs_undefined_distinct():
    logs = _logs("console.log([,].length, [undefined].length, "
                 "[,].join('-') === '', [undefined].join('-') === '', "
                 "[1,,3].join('-'), [1,undefined,3].join('-'));")
    # Both a hole and an explicit undefined render empty in join; the
    # difference shows up in `in` / iteration callbacks (other tests).
    assert logs[0].split() == ["1", "1", "true", "true", "1--3", "1--3"], logs


# ---------------------------------------------------------------------------
# 5. Callable Number / Boolean / global parseInt / parseFloat
# ---------------------------------------------------------------------------

@_reg
def test_r7_j01_number_constructor_call():
    logs = _logs("console.log(Number(null), Number(undefined), Number(true), "
                 "Number(false), Number(''), Number('  42  '), Number('3.5'), "
                 "Number('0x10'), Number('abc'), Number(), Number([]), Number([5]));")
    parts = logs[0].split()
    assert parts[0] == "0" and parts[1] == "NaN" and parts[2] == "1", parts
    assert parts[3] == "0" and parts[4] == "0" and parts[5] == "42", parts
    assert parts[6] == "3.5" and parts[7] == "16" and parts[8] == "NaN", parts
    assert parts[9] == "0" and parts[10] == "0" and parts[11] == "5", parts


@_reg
def test_r7_j01_number_statics_still_work():
    logs = _logs("console.log(Number.isInteger(3.0), Number.isInteger(3.5), "
                 "Number.isNaN(NaN), Number.isNaN(1), "
                 "Number.parseInt('42px'), Number.parseFloat('3.14abc'));")
    assert logs[0].split() == ["true", "false", "true", "false", "42",
                               "3.14"], logs


@_reg
def test_r7_j01_global_parse_int_radix():
    logs = _logs("console.log(parseInt('10', 16), parseInt('10', 2), "
                 "parseInt('10'), parseInt('  0x1F  '), parseInt('z', 36), "
                 "parseInt('12'), parseInt('x'), parseInt('42px'), "
                 "parseInt('-7'), parseInt('11', 8));")
    parts = logs[0].split()
    assert parts == ["16", "2", "10", "31", "35", "12", "NaN", "42", "-7",
                     "9"], parts


@_reg
def test_r7_j01_global_parse_float():
    logs = _logs("console.log(parseFloat('3.14abc'), parseFloat('.5'), "
                 "parseFloat('5.'), parseFloat('1e3'), parseFloat('abc'), "
                 "parseFloat('  -2.5 '), parseFloat('Infinity'), "
                 "parseFloat('-Infinity'), parseFloat(2.5));")
    parts = logs[0].split()
    assert parts == ["3.14", "0.5", "5", "1000", "NaN", "-2.5", "Infinity",
                     "-Infinity", "2.5"], parts


@_reg
def test_r7_j01_boolean_constructor():
    logs = _logs("console.log(Boolean([]), Boolean({}), Boolean(0), Boolean(''), "
                 "Boolean('x'), Boolean(null), Boolean(undefined), Boolean(NaN), "
                 "Boolean(1), Boolean(false));")
    parts = logs[0].split()
    assert parts == ["true", "true", "false", "false", "true", "false",
                     "false", "false", "true", "false"], parts


@_reg
def test_r7_j01_typeof_builtin_constructors():
    logs = _logs("console.log(typeof Number, typeof Boolean, typeof parseInt, "
                 "typeof Object, typeof Object.is);")
    assert logs[0].split() == ["function", "function", "function", "object",
                               "function"], logs


# ---------------------------------------------------------------------------
# 6. Property enumeration order
# ---------------------------------------------------------------------------

@_reg
def test_r7_j01_object_keys_integer_index_first():
    logs = _logs("console.log(Object.keys({'10':'a','2':'b','x':'c'}).join(','), "
                 "Object.values({'10':'a','2':'b','x':'c'}).join(','));")
    assert logs[0].split() == ["2,10,x", "b,a,c"], logs


@_reg
def test_r7_j01_object_keys_mixed_and_edges():
    logs = _logs("console.log(Object.keys({'b':1,'0':2,'a':3,'1':4}).join(','), "
                 "Object.keys({'01':1,'1.5':2,'-1':3,'4294967294':4}).join(','), "
                 "Object.keys({}).length);")
    # canonical indices ascend first; non-canonical keys ("01", "1.5",
    # "-1") stay in insertion order; 4294967294 IS a canonical index
    # (< 2**32-1), so it leads the second object's order.
    assert logs[0].split() == ["0,1,b,a", "4294967294,01,1.5,-1", "0"], logs


@_reg
def test_r7_j01_object_entries_order():
    logs = _logs("var e = Object.entries({'2':'b','x':'c','10':'a'}); "
                 "console.log(e[0].join(':'), e[1].join(':'), e[2].join(':'));")
    assert logs[0].split() == ["2:b", "10:a", "x:c"], logs


# ---------------------------------------------------------------------------
# 7. Regression guards — matched probes stay matched
# ---------------------------------------------------------------------------

@_reg
def test_r7_j01_previously_matched_probes_unchanged():
    logs = _logs(
        "console.log(null === undefined, ({}) === ({}), [] === [], NaN === NaN);"
        "var x = null; console.log(x?.a, 0 ?? 7);"
        "function f(){try{return 1;}finally{return 2;}} console.log(f());"
        "var n = 3; console.log(`v${n}`);")
    assert logs[0].split() == ["false", "false", "false", "false"], logs
    assert logs[1].split() == ["undefined", "0"], logs
    assert logs[2] == "2", logs
    assert logs[3] == "v3", logs


@_reg
def test_r7_j01_promise_and_async_order_preserved():
    logs = _logs('Promise.resolve().then(function(){console.log("p");}); '
                 'console.log("s");')
    assert logs == ["s", "p"], logs
    logs = _logs("async function f(){return 3;} "
                 "f().then(function(x){console.log(x);});")
    assert logs == ["3"], logs

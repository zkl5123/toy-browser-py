
# Keep standalone runners compatible with the project modules and fixtures.
from pathlib import Path as _ProjectPath
import sys as _project_sys
import os as _project_os
_PROJECT_ROOT = _ProjectPath(__file__).resolve().parents[1]
_project_sys.path.insert(0, str(_PROJECT_ROOT))
_project_sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "benchmarks"))
_project_os.chdir(_PROJECT_ROOT)

# P33.R3 — JS block scoping (let/const/var) + TDZ tests.
# ---------------------------------------------------------------------------
# R3 layer: block-scoped let/const with TDZ (temporal dead zone),
# const re-assignment rejection, var function-level hoisting, and
# postfix/prefix increment/decrement.  All tests are pure DOM + JS
# (no network, no Tk), self-contained.

import dom as _dom
import js as _js
from js import parse_program, Interpreter, Env, _Return, TDZError, _Binding


def _unb(v):
    """Unwrap a _Binding to its .value (the eval pipeline returns
    bindings for LetDecl / DestructuringDecl statements)."""
    return v.value if isinstance(v, _Binding) else v


def _run(code: str, host: "_js.Host"):
    """Run *code* on a fresh interpreter, return the _Return value
    (or the last statement's value) or raise the underlying
    exception (TDZError / RuntimeError / …)."""
    interp = Interpreter(host)
    interp.env = Env(host.globals, None)
    interp.env._is_func = True
    prog = parse_program(code)
    try:
        val = interp.eval_block(prog)
        return _unb(val)
    except _Return as r:
        return _unb(r.value)


def _host():
    root = _dom.parse("<html><body><script></script></body></html>")
    return _js.Host(root, lambda m: None)


def test_r3_let_block_scope():
    """`let` is block-scoped: the outer binding is shadowed inside a
    nested block and the inner binding is not visible after the block
    closes."""
    h = _host()
    v = _run("function f() { let x = 1; { let x = 2; }; return x; } var __r = f(); __r", h)
    assert v == 1, f"outer let must survive the inner shadow, got {v!r}"


def test_r3_const_immutable():
    """`const` re-assignment raises a RuntimeError."""
    h = _host()
    try:
        _run("function g() { const C = 5; C = 6; return 0; } var __r = g(); __r", h)
        assert False, "const re-assignment must raise"
    except RuntimeError as e:
        assert "constant variable" in str(e), str(e)


def test_r3_tdz_read_before_init():
    """Reading a `let` binding before its initializer in the same
    block raises TDZError (the temporal dead zone)."""
    h = _host()
    try:
        _run("function t() { { a; let a; } } var __r = t(); __r", h)
        assert False, "TDZ read must raise"
    except TDZError as e:
        assert "before initialization" in str(e), str(e)


def test_r3_var_hoisting():
    """`var` is function-scoped: a reassignment inside a nested block
    still writes to the function-level binding."""
    h = _host()
    v = _run("function h() { var v = 1; { var v = 2; }; return v; } var __r = h(); __r", h)
    assert v == 2, f"var hoisting: inner reassign must win, got {v!r}"


def test_r3_nested_blocks():
    """Let declarations in nested blocks see their parent blocks'
    bindings (lexical scope chain)."""
    h = _host()
    v = _run("function n() { let o = 0; { let a = 1; { let b = 2; o = a + b; } }; return o; } var __r = n(); __r", h)
    assert v == 3, f"nested blocks: a+b must be 3, got {v!r}"


def test_r3_for_let_loop_scope():
    """`for (let i = 0; ...; i++)` binds `i` to the loop's block
    scope; the counter is not visible after the loop ends."""
    h = _host()
    v = _run("function l() { let acc = 0; for (let i = 0; i < 3; i++) { acc += i; } ; return acc; } var __r = l(); __r", h)
    assert v == 3, f"for-let loop: acc must be 3, got {v!r}"


def test_r3_postfix_prefix_increment():
    """Postfix `z++` returns the old value; prefix `++z` returns the
    new value."""
    h = _host()
    v = _run("function p() { let z = 0; let a = z++; let b = ++z; return a + b; } var __r = p(); __r", h)
    assert v == 2, f"postfix+prefix: 0 + 2 must be 2, got {v!r}"


def test_r3_shadow_in_nested_func():
    """A `let` in an inner function shadows the outer `let`."""
    h = _host()
    v = _run("let y = 1; function s() { let y = 2; return y; } var __r = s(); __r", h)
    assert v == 2, f"nested shadow: must be 2, got {v!r}"


def test_r3_const_array_mutable():
    """A `const` array binding is immutable, but its elements can be
    mutated (JS semantics: the binding is const, not the value)."""
    h = _host()
    v = _run("const arr = [1]; arr.push(2); arr.length", h)
    assert v == 2, f"const array push: length must be 2, got {v!r}"


def test_r3_outer_visible_from_inner_block():
    """An outer `let` is visible inside a nested block (the binding
    record is inherited through the lexical chain)."""
    h = _host()
    v = _run("function r() { let outer = 1; { let inner = outer; return inner; } } var __r = r(); __r", h)
    assert v == 1, f"outer visible: must be 1, got {v!r}"


def test_r3_let_in_while_loop_body():
    """A `let` declared inside a while-loop body is scoped to that
    iteration; the binding does not leak after the loop."""
    h = _host()
    v = _run("function w() { let c = 0; while (c < 3) { let x = c; c += 1; } ; return c; } var __r = w(); __r", h)
    assert v == 3, f"while let: c must be 3, got {v!r}"


def test_r3_const_reassign_top_level():
    """Top-level `const` re-assignment also raises."""
    h = _host()
    try:
        _run("const T = 1; T = 2; 0", h)
        assert False, "top-level const reassign must raise"
    except RuntimeError as e:
        assert "constant variable" in str(e)


# GPT P1-2 — TESTS list (auto-generated)
TESTS: list = []
TESTS.append(("test_r3_let_block_scope", test_r3_let_block_scope))
TESTS.append(("test_r3_const_immutable", test_r3_const_immutable))
TESTS.append(("test_r3_tdz_read_before_init", test_r3_tdz_read_before_init))
TESTS.append(("test_r3_var_hoisting", test_r3_var_hoisting))
TESTS.append(("test_r3_nested_blocks", test_r3_nested_blocks))
TESTS.append(("test_r3_for_let_loop_scope", test_r3_for_let_loop_scope))
TESTS.append(("test_r3_postfix_prefix_increment", test_r3_postfix_prefix_increment))
TESTS.append(("test_r3_shadow_in_nested_func", test_r3_shadow_in_nested_func))
TESTS.append(("test_r3_const_array_mutable", test_r3_const_array_mutable))
TESTS.append(("test_r3_outer_visible_from_inner_block", test_r3_outer_visible_from_inner_block))
TESTS.append(("test_r3_let_in_while_loop_body", test_r3_let_in_while_loop_body))
TESTS.append(("test_r3_const_reassign_top_level", test_r3_const_reassign_top_level))

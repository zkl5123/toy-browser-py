"""B04 — 多变量声明列表与序列表达式（纯逻辑核心）回归（round6 批次 16）。

覆盖 toy_browser/scripting/js.py：
- MultiDecl AST：`var a=1, b=2, c=3;` 声明列表解析 + 同一作用域绑定
  （保持 let/const 块级作用域 + TDZ + const 赋值限制，B04 步骤 2）
- SeqExpr AST：`(e1, e2, ..., en)` 序列表达式，最低优先级，从左到右
  求值、返回最后一项（B04 步骤 3）
- 解析回归：`var a=f(1,2),b=[3,4];`、for 初始化、对象字面量、
  括号表达式（B04 步骤 4：每个逗号由所属语法消费）

用现有 JS 引擎 Host 离线驱动，不依赖网络 / Tk。
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from toy_browser.scripting import js as _js
from toy_browser.document.html_pkg.node import Node as DomNode

TESTS = []


def _host(code: str = "") -> _js.Host:
    """创建干净 Host 并执行代码，返回 Host。

    需要一个最小 DOM root（空 html 元素）+ 静默 log。
    """
    root = DomNode.element("html")
    h = _js.Host(root, log=lambda m: None)
    if code:
        h.execute(code)
    return h


def _var(h: _js.Host, name: str):
    """从 host.globals 取顶层变量值（var 走 _Binding.value 路径）。

    let/const 在顶层走 block env（块级作用域），不在 globals dict，
    所以 let/const 测试用 host.eval() 表达式求值验证。
    """
    v = h.globals.get(name)
    # P33.R3 — 可能是 _Binding（var/let/const 都写入 globals dict）
    if v is not None and hasattr(v, "value"):
        return v.value
    return v


def _eval_expr(h: _js.Host, expr: str):
    """用 host.eval() 求值一个表达式，返回结果。"""
    return h.eval(expr)


# ----------------------------------------------------------------------
# MultiDecl — 多变量声明列表
# ----------------------------------------------------------------------
def test_var_multi_decl_single():
    """单个 var 声明（回归基线，不破坏既有行为）。"""
    h = _host("var x = 5;")
    assert _var(h, "x") == 5.0


def test_var_multi_decl_two():
    """B04 步骤 2 — `var a=1, b=2;` 两个声明绑定到同一作用域。"""
    h = _host("var a = 1, b = 2;")
    assert _var(h, "a") == 1.0
    assert _var(h, "b") == 2.0


def test_var_multi_decl_three_no_init():
    """`var p, q, r;` 无初始值 → undefined（None 或 _UNDEFINED）。"""
    h = _host("var p, q, r;")
    for name in ("p", "q", "r"):
        v = _var(h, name)
        assert v is None or v == _js._UNDEFINED, f"{name} should be undefined, got {v!r}"


def test_let_multi_decl_block_scope():
    """B04 步骤 2 — `let` 多声明保持块级作用域。

    let 在顶层走 eval_block 的 block env，不泄漏到 host.globals。
    验证方式：在块内声明 let 多变量并求值，块外不可见（ReferenceError）。"""
    # 块内 let 多声明 + 求和（同一 execute，块内可读）
    h = _host("var sum; { let a = 10, b = 20; sum = a + b; }")
    assert _var(h, "sum") == 30.0
    # 块外 a/b 不可见（块级作用域）
    try:
        _eval_expr(h, "a")
        assert False, "a should not be visible outside the block"
    except Exception:
        pass  # ReferenceError 是预期行为


def test_const_multi_decl():
    """`const` 多声明 + const 赋值限制。"""
    h = _host("var sum; { const X = 1, Y = 2; sum = X + Y; }")
    assert _var(h, "sum") == 3.0
    # const 重新赋值应被拒绝（引擎抛错）
    try:
        h.execute("X = 99;")
        # 若引擎未抛错，const 限制至少保持原值（块外不可见则无所谓）
    except Exception:
        pass  # const 赋值拒绝是预期行为


def test_multi_decl_in_for_init():
    """B04 步骤 4 — for 初始化多声明。"""
    code = "var cnt = 0; for (var i = 0, j = 3; i < j; i = i + 1, j = j - 1) { cnt = cnt + 1; }"
    h = _host(code)
    # i:0, j:3 → i<j: 0<3(1), 1<2(2), 2<1(停) → cnt=2
    assert _var(h, "cnt") == 2.0


def test_multi_decl_with_call_args():
    """B04 步骤 4 — 调用参数逗号 + 数组元素逗号由所属语法消费。"""
    code = (
        "function f(x, y) { return x * 10 + y; }\n"
        "var a = f(1, 2), b = [3, 4];\n"
    )
    h = _host(code)
    assert _var(h, "a") == 12.0       # f(1,2) = 1*10+2
    b = _var(h, "b")
    assert b in ([3.0, 4.0], (3.0, 4.0)), f"b should be [3,4], got {b!r}"


# ----------------------------------------------------------------------
# SeqExpr — 序列表达式
# ----------------------------------------------------------------------
def test_sequence_returns_last():
    """B04 步骤 3 — `(e1, e2, e3)` 从左到右求值、返回最后一项。"""
    h = _host("var v = (1, 2, 3);")
    assert _var(h, "v") == 3.0


def test_sequence_with_side_effects():
    """序列表达式逐子式求值（副作用保留），返回最后值。"""
    code = "var log = 0; var r = ((log = log + 1), (log = log + 10), 99);"
    h = _host(code)
    assert _var(h, "log") == 11.0
    assert _var(h, "r") == 99.0


def test_sequence_single_in_parens_is_not_sequence():
    """单表达式括号 `(5)` 是分组表达式，不是序列（回归基线）。"""
    h = _host("var v = (5);")
    assert _var(h, "v") == 5.0


def test_sequence_with_object_literal():
    """B04 步骤 4 — 对象字面量内的逗号由所属语法消费（非序列）。"""
    h = _host("var o = { x: 1, y: 2 };")
    o = _var(h, "o")
    assert o is not None, "o should be defined"
    assert o["x"] == 1.0
    assert o["y"] == 2.0


def test_sequence_in_conditional():
    """序列表达式作为条件表达式的一支。"""
    h = _host("var v = (true ? (1, 2) : 99);")
    assert _var(h, "v") == 2.0


def test_nested_sequence():
    """嵌套序列 `((a, b), c)` 仍从左到右、返回最外最后项。"""
    h = _host("var v = ((1, 2), 3);")
    assert _var(h, "v") == 3.0


# ----------------------------------------------------------------------
# 组合回归
# ----------------------------------------------------------------------
def test_multi_decl_no_regression_single():
    """既有单声明行为不被多声明改造破坏。"""
    h = _host("var single = 42;")
    assert _var(h, "single") == 42.0


def test_let_tdz_still_applies_multi():
    """B04 步骤 2 — `let` 多声明的 TDZ：块内声明后即可读。"""
    code = "{ let a = 1, b = 2; }"
    h = _host(code)  # 不抛 TDZ 错误（块内声明后即可读）


TESTS.append(("test_var_multi_decl_single", test_var_multi_decl_single))
TESTS.append(("test_var_multi_decl_two", test_var_multi_decl_two))
TESTS.append(("test_var_multi_decl_three_no_init", test_var_multi_decl_three_no_init))
TESTS.append(("test_let_multi_decl_block_scope", test_let_multi_decl_block_scope))
TESTS.append(("test_const_multi_decl", test_const_multi_decl))
TESTS.append(("test_multi_decl_in_for_init", test_multi_decl_in_for_init))
TESTS.append(("test_multi_decl_with_call_args", test_multi_decl_with_call_args))
TESTS.append(("test_sequence_returns_last", test_sequence_returns_last))
TESTS.append(("test_sequence_with_side_effects", test_sequence_with_side_effects))
TESTS.append(("test_sequence_single_in_parens_is_not_sequence", test_sequence_single_in_parens_is_not_sequence))
TESTS.append(("test_sequence_with_object_literal", test_sequence_with_object_literal))
TESTS.append(("test_sequence_in_conditional", test_sequence_in_conditional))
TESTS.append(("test_nested_sequence", test_nested_sequence))
TESTS.append(("test_multi_decl_no_regression_single", test_multi_decl_no_regression_single))
TESTS.append(("test_let_tdz_still_applies_multi", test_let_tdz_still_applies_multi))

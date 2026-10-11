"""Stage 5 + 7(partial) — toy JS engine: lexer + Pratt parser + tree-walking
interpreter, DOM bindings (getElementById, textContent, style,
querySelectorAll), sandboxed fetch, setTimeout (FIFO timer queue), ??
operator, and a minimal event loop.

Pure standard library. The interpreter supports a useful subset of JS:
variables (let/const/var), functions, control flow (if/else/while/for),
expressions, member/element access, assignment (+ compound +=/-=), the
nullish-coalescing operator (??), arithmetic, comparison, logical ops,
string concatenation, and DOM host bindings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from dom import Node as DomNode
from paint import Color

# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

TWO_CHAR_OPS = [
    "??", "==", "!=", "<=", ">=", "&&", "||", "++", "--",
    "+=", "-=", "*=", "/=", "<<", ">>", "=>",
    # P16.2 — optional chaining (must be checked before single ".").
    "?.",
    # P16.2 — strict equality operators (must be checked before "==" / "!=").
    "===", "!==",
]
# P16.1 — 3-char operator: spread.  Checked before TWO_CHAR_OPS so that
# ``...`` is not mis-lexed as ``.`` + ``..``.
THREE_CHAR_OPS = ["..."]
SINGLE_CHARS = set("+-*/%<>!=&|{}()[]{},;:.?\\~@#")


@dataclass
class Token:
    kind: str
    value: str
    pos: int = 0


def _is_digit(c: str) -> bool:
    return c.isdigit()


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c in "_$"


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c in "_$"


def lex(src: str, _allow_regexp: bool = True) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            i = end + 2 if end >= 0 else n
            continue
        # P29.1 — regex literal: ``/pattern/flags`` in expression position.
        # Heuristic (JS-style context): a ``/`` starts a regex when the
        # previous token is an operator / punctuation / start-of-line
        # context, NOT an identifier / number / string / ``)`` / ``]``
        # / ``}`` (those imply division or an object-close).
        prev = tokens[-1] if tokens else None
        is_expr_ctx = (
            prev is None
            or (
                prev.kind == "op"
                and prev.value not in ("++", "--", "...")
            )
        )
        if c == "/" and _allow_regexp and is_expr_ctx:
            # Find the closing slash (handle escaped chars + char classes).
            j = i + 1
            in_class = False
            while j < n:
                cj = src[j]
                if cj == "\\" and j + 1 < n:
                    j += 2
                    continue
                if cj == "[":
                    in_class = True
                elif cj == "]":
                    in_class = False
                elif cj == "/" and not in_class:
                    break
                j += 1
            if j < n:
                pattern = src[i + 1:j]
                j += 1  # consume closing slash
                flags_start = j
                while j < n and _is_ident_char(src[j]):
                    j += 1
                flags = src[flags_start:j]
                tokens.append(Token("regexp", pattern, i))
                tokens.append(Token("regexp_flags", flags, i))
                i = j
                continue
            # Unterminated regex literal: treat as division (fallback).
        if c in ("'", '"'):
            quote = c
            i += 1
            start = i
            while i < n and src[i] != quote:
                if src[i] == "\\" and i + 1 < n:
                    i += 1
                i += 1
            value = src[start:i]
            value = (
                value.replace("\\'", "'")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
                .replace("\\n", "\n")
                .replace("\\t", "\t")
            )
            i += 1
            tokens.append(Token("string", value, start - 1))
            continue
        # P13.2 — template literal: backtick string with ${...} interpolation.
        # The lexer emits a sequence of "tpl" tokens: one per literal chunk,
        # interleaved with "dollarbrace" tokens for the opening `${` and
        # the corresponding closing `}` is handled by the parser which
        # parses the inner expression as a normal expression.
        # Simplified: emit "tpl" tokens for text chunks, and "open_tpl" /
        # "close_tpl" for `${` / `}` markers.
        if c == "`":
            i += 1
            text_start = i
            while i < n:
                if src[i] == "`":
                    chunk = src[text_start:i]
                    tokens.append(Token("tpl", chunk, text_start))
                    i += 1
                    break
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    # Emit the text chunk before the interpolation.
                    chunk = src[text_start:i]
                    if chunk or True:
                        tokens.append(Token("tpl", chunk, text_start))
                    tokens.append(Token("open_tpl", "${", i))
                    i += 2
                    # Find the matching close-brace of this interpolation.
                    depth = 1
                    j = i
                    while j < n and depth > 0:
                        if src[j] == "{":
                            depth += 1
                        elif src[j] == "}":
                            depth -= 1
                        j += 1
                    # The content between ${ and } is re-lexed separately.
                    inner = src[i:j - 1]
                    inner_toks = lex(inner)
                    for it in inner_toks:
                        if it.kind == "eof":
                            continue
                        tokens.append(it)
                    tokens.append(Token("close_tpl", "}", j - 1))
                    i = j
                    text_start = i
                    continue
                i += 1
            continue
        if _is_digit(c):
            start = i
            while i < n and _is_digit(src[i]):
                i += 1
            if i < n and src[i] == "." and i + 1 < n and _is_digit(src[i + 1]):
                i += 1
                while i < n and _is_digit(src[i]):
                    i += 1
            tokens.append(Token("number", src[start:i], start))
            continue
        if _is_ident_start(c):
            start = i
            while i < n and _is_ident_char(src[i]):
                i += 1
            tokens.append(Token("id", src[start:i], start))
            continue
        # P16.1 — 3-char operator (spread) must be checked before 2-char.
        three = src[i : i + 3]
        if three in THREE_CHAR_OPS:
            tokens.append(Token("op", three, i))
            i += 3
            continue
        # P16.2 — 3-char strict-equality operators must be checked before 2-char.
        if three in ("===", "!=="):
            tokens.append(Token("op", three, i))
            i += 3
            continue
        two = src[i : i + 2]
        if two in TWO_CHAR_OPS:
            tokens.append(Token("op", two, i))
            i += 2
            continue
        if c in SINGLE_CHARS:
            tokens.append(Token("op", c, i))
            i += 1
            continue
        raise SyntaxError(f"unexpected character {c!r} at position {i}")
    tokens.append(Token("eof", "", n))
    return tokens


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


class JNode:
    pass


@dataclass
class Num(JNode):
    value: float


@dataclass
class Str(JNode):
    value: str


@dataclass
class Bool(JNode):
    value: bool


@dataclass
class Null(JNode):
    pass


@dataclass
class Ident(JNode):
    name: str


@dataclass
class Assign(JNode):
    target: JNode
    op: str
    value: JNode


@dataclass
class Member(JNode):
    obj: JNode
    prop: str


@dataclass
class Index(JNode):
    obj: JNode
    index: JNode


@dataclass
class Call(JNode):
    callee: JNode
    args: list


@dataclass
class Binary(JNode):
    op: str
    left: JNode
    right: JNode


@dataclass
class Unary(JNode):
    op: str
    operand: JNode


@dataclass
class Logical(JNode):
    op: str
    left: JNode
    right: JNode


# P0 (GLM report) — ternary conditional: `cond ? a : b`.  The legacy
# parser had no ternary node; the GLM probe confirmed that
# ``cond ? a : b`` raised a SyntaxError that killed the entire
# script.  Model it as a dedicated node so the interpreter can
# evaluate only the chosen branch (lazy semantics).
# NOTE: ``else`` is a Python keyword, so the dataclass field is
# named ``else_`` (matching the existing :class:`If` convention).
@dataclass
class Ternary(JNode):
    cond: JNode
    then: JNode
    else_: JNode


@dataclass
class If(JNode):
    cond: JNode
    then: JNode
    else_: Optional[JNode] = None


@dataclass
class While(JNode):
    cond: JNode
    body: JNode


@dataclass
class For(JNode):
    init: Optional[JNode] = None
    cond: Optional[JNode] = None
    step: Optional[JNode] = None
    body: Optional[JNode] = None


@dataclass
class Block(JNode):
    stmts: list


@dataclass
class LetDecl(JNode):
    name: str
    value: Optional[JNode] = None
    # P33.R3 — declaration kind: "let", "const", or "var".  ``var``
    # keeps the legacy function-scoped / hoisted behavior; ``let`` and
    # ``const`` are block-scoped with a TDZ (temporal dead zone) —
    # reading the name before its declaration in the enclosing block
    # raises a ReferenceError instead of returning undefined.
    kind: str = "var"


@dataclass
class IncDec(JNode):
    """P33.R3 — increment / decrement: ``i++``, ``i--``, ``++i``,
    ``--i``.  ``prefix`` selects the JS semantics: postfix returns the
    value *before* the mutation; prefix returns it *after*.  The
    operand is an Ident (or a Member / Index access) whose binding is
    found in the enclosing lexical environment."""
    operand: JNode
    op: str = "++"            # "++" or "--"
    prefix: bool = False


class TDZError(NameError):
    """P33.R3 — the temporal dead zone: a ``let``/``const`` binding
    that has been *declared* in a scope but not yet *initialized*
    (i.e. the binding record exists with value None and kind
    "let"/"const", and the execution point is inside the block that
    declared it)."""


@dataclass
class ExprStmt(JNode):
    expr: JNode


@dataclass
class Return(JNode):
    value: Optional[JNode] = None


@dataclass
class FuncDecl(JNode):
    name: str
    params: list
    body: JNode


@dataclass
class FunExpr(JNode):
    params: list
    body: JNode


# P14.2 — arrow function: `v => expr` or `(a, b) => { ... }`
@dataclass
class ArrowFunc(JNode):
    params: list        # parameter names (identifiers)
    body: JNode         # Block (multi-statement) or a single expression
    is_block: bool      # True when body is a Block, False when an expression


@dataclass
class MethodCall(JNode):
    obj: JNode
    method: str
    args: list


@dataclass
class ArrayLit(JNode):
    elements: list  # list of JNode; an element may be an ArraySpread node (P16.1)


# P16.1 — array / call-argument spread
@dataclass
class ArraySpread(JNode):
    """``...expr`` inside an array literal.  ``expr`` is the iterable source."""
    expr: JNode


@dataclass
class CallSpread(JNode):
    """``...expr`` as a single call argument (JS argument spread)."""
    expr: JNode


@dataclass
class OptionalChain(JNode):
    """P16.2 — ``a?.b`` / ``a?.b()`` / ``a?.[b]``.

    ``base`` is the operand; ``prop`` is the property name (str) or node
    (for computed access).  ``call`` is True when the chain ends with ``(...)``
    (optional call); ``call_args`` holds the call arguments.
    """
    base: JNode
    prop: object = None
    call: bool = False
    call_args: list = None


@dataclass
class ObjectLit(JNode):
    pairs: list  # list of (key: str, value: JNode); value may be an ObjectSpread


# P11.2 — try / catch / finally + throw
@dataclass
class Try(JNode):
    body: JNode          # Block
    catch_param: str | None   # parameter name in catch (or None)
    catch_body: JNode | None  # Block
    finally_body: JNode | None  # Block


@dataclass
class Throw(JNode):
    value: JNode  # expression to throw


# P13.2 — template literal (backtick string with ${...} interpolation)
@dataclass
class TemplateLit(JNode):
    """A template literal: chunks are plain text, exprs are interpolation slots."""
    chunks: list   # alternating: [text, expr, text, expr, …] where text is str and expr is JNode


# P12.2 — class / extends / constructor / super
@dataclass
class ClassDecl(JNode):
    name: str
    parent_name: str | None          # extends <ident> (toy: global class lookup)
    methods: list                   # list of (name, params: list[str], body: JNode)


# P13.1 — destructuring declarations
@dataclass
class DestructuringDecl(JNode):
    """`let {a, b} = obj` or `let [x, y] = arr` — destructuring bind.

    ``bindings`` is a list of target names; ``value`` is the RHS expression.
    For object destructuring, ``keys`` carries the property names to pluck
    from the RHS dict; for array destructuring it is ``None``.
    P33.R3 — ``kind`` is the declaration kind ("let" / "const" / "var").
    """
    bindings: list[str]            # target variable names
    value: JNode                  # RHS expression
    keys: list[str] | None = None  # property names (object destructure) or None (array)
    kind: str = "var"             # P33.R3 — declaration kind


# P13.1 — object spread
@dataclass
class ObjectSpread(JNode):
    """Spread node for `{...obj}` inside an object literal.

    Parsed as part of ``ObjectLit.pairs``; when a pair's value is an
    ``ObjectSpread``, the interpreter merges the spread object's keys.
    """
    source: JNode                 # expression that evaluates to a dict


# P29.1 — regex literal: ``/pattern/flags``.
@dataclass
class RegexpLit(JNode):
    """Regex literal parsed from ``/pat/flags``.  Evaluated to a dict
    with the same shape as ``new RegExp(...)`` (P4.2) so ``.test`` /
    ``.exec`` / ``.replace`` / ``.split`` route through ``call_method``."""
    pattern: str
    flags: str = ""


# P29.2 — ``for...of`` loop.
@dataclass
class ForOf(JNode):
    """``for (const x of expr) body`` — iterate over a JS iterable.

    ``target`` is a single binding name (toy: no destructuring in
    for-of); the interpreter evaluates the iterable once and drives a
    Python iterator (list / str / dict keys / Map / Set / generator).
    """
    target: str
    iterable: JNode
    body: JNode


# P29.2 — generator function: ``function* name() { yield x }``.
@dataclass
class GenFuncDecl(JNode):
    """Star-prefixed function declaration (generator).  The body may
    contain ``yield expr`` statements; evaluation wraps the body in a
    Python coroutine-like object the interpreter drives lazily."""
    name: str
    params: list
    body: JNode
    star: bool = True


# P29.2 — generator function expression: ``function* () { yield x }``.
@dataclass
class GenFunExpr(JNode):
    params: list
    body: JNode
    star: bool = True


# P29.2 — ``yield expr`` inside a generator body.
@dataclass
class YieldExpr(JNode):
    """``yield [expr]`` — pause the generator and resume with the value
    passed via ``.next()`` (toy: yield* not supported)."""
    value: JNode | None = None


# ---------------------------------------------------------------------------
# P29.2 — iterator protocol + generators
# ---------------------------------------------------------------------------

class _JSStop:
    """Internal sentinel: a JS iterator is exhausted."""
    pass


class ToyIterator:
    """P29.2 — a JS ``{ done, value }`` iterator object.

    A Python dict with keys ``__iterator__`` / ``next`` — ``.next()``
    routes through ``call_method`` and returns ``{done: false, value: x}``
    or ``{done: true}``.  Consuming happens in order; after exhaustion
    every subsequent call returns ``{done: true}``.
    """

    def __init__(self, seq):
        self._it = iter(seq)
        self._done = False
        self._value = None
        self.__class__.__name__ = "ToyIterator"

    def next(self):
        if self._done:
            return {"__iterator__": True, "done": True}
        try:
            self._value = next(self._it)
        except StopIteration:
            self._done = True
            return {"__iterator__": True, "done": True}
        return {"__iterator__": True, "done": False, "value": self._value}

    def __iter__(self):
        return iter([self._value])


class ToyGenObject:
    """P29.2 — a JS generator object (the result of calling ``function*``).

    Wraps a Python generator; ``.next(v)`` returns ``{done, value}``;
    the object is itself iterable (``for...of`` works directly).
    """

    def __init__(self, pygen):
        self._gen = pygen
        self._done = False

    def next(self, send_value=None):
        if self._done:
            return {"done": True}
        try:
            v = self._gen.send(send_value) if send_value is not None else next(self._gen)
        except StopIteration as e:
            self._done = True
            out = {"done": True}
            if e.value is not None:
                out["value"] = e.value
            return out
        return {"done": False, "value": v}

    def __iter__(self):
        return iter([self._gen])


class ToyClass:
    """P12.2 — a JS class value.

    A ToyClass is callable (``new C(a, b)`` is emulated by ``C(a, b)``) and
    produces a plain dict instance:

      - own fields set by the constructor body (``this.x = ...``)
      - methods from the class body
      - ``__instance_of__`` / ``__class__`` bookkeeping
      - ``__parent__`` dict when an extends chain exists

    ``super`` inside a method resolves to the parent class's method of the
    same name; ``super()`` inside the constructor runs the parent ctor.
    The interpreter exposes ``this`` / ``super`` on the instance-carrying
    frame when a class method is invoked.
    """

    def __init__(self, name: str, methods: list,
                 parent: "ToyClass | None" = None, host: "Host | None" = None):
        self.name = name
        self.methods = {n: (p, b) for n, p, b in methods}
        self.parent = parent
        self.host = host

    def __call__(self, *args):
        host = self.host
        cls = self
        # Build a shallow own-instance dict; the constructor fills it in.
        obj: dict = {"__instance_of__": cls.name, "__class__": cls}
        # P12.2 — defer the parent constructor to `super(...)` call.
        # If the own ctor never calls super(), we still run it at the end
        # so inherited fields are populated (toy semantics).
        parent_ran = False

        def _run_parent(sargs=()):
            nonlocal parent_ran
            if parent_ran or cls.parent is None:
                return None
            pobj = cls.parent.__call__(*sargs)
            obj["__parent__"] = pobj
            for k, v in pobj.items():
                if not k.startswith("__") and k not in obj:
                    obj[k] = v
            parent_ran = True
            return pobj

        # Run own constructor (if any).  `this.x = …` writes to obj;
        # `super(…)` is bound so that when the ctor calls it the parent
        ctor = cls.methods.get("constructor")
        if ctor is not None:
            params, body = ctor
            closure = host._interp()
            base = closure.env
            frame = Env({}, base)
            frame.define("this", obj)
            frame.define("super", lambda *sa: _run_parent(sa))
            for p, a in zip(params, args):
                frame.define(p, a)
            interp = host._interp()
            saved = interp.env
            interp.env = frame
            try:
                interp.eval_block(body)
            except _Return:
                pass
            finally:
                interp.env = saved
        # If the ctor skipped super(), run the parent ctor now so fields
        # inherited from the parent are still available (toy semantics).
        if cls.parent is not None and not parent_ran:
            _run_parent()
        # Bind methods (skip constructor; already ran).
        for mname, (mparams, mbody) in cls.methods.items():
            if mname == "constructor":
                continue
            cls._bind_method(obj, cls, mname, mparams, mbody, host)
        return obj

    def _bind_method(self, obj: dict, cls: "ToyClass", mname: str,
                     mparams: list, mbody: "JNode", host: "Host") -> None:
        def method(*margs):
            closure = host._interp()
            base = closure.env
            frame = Env({}, base)
            frame.define("this", obj)
            # P12.2 — `super.method(...)` in a method: a dict whose keys are
            # parent-method names, so MethodCall(Ident("super")) resolves via
            # env.lookup("super") and then calls dict[method_name](*args).
            if cls.parent is not None:
                pm = cls.parent.methods.get(mname)
                if pm is not None:
                    pparams, pbody = pm

                    def _super_call(*msargs, _pparams=pparams, _pbody=pbody):
                        f2 = Env({}, base)
                        f2.define("this", obj.get("__parent__", obj))
                        for p, a in zip(_pparams, msargs):
                            f2.define(p, a)
                        i2 = host._interp()
                        s2 = i2.env
                        i2.env = f2
                        try:
                            val = i2.eval_block(_pbody)
                        except _Return:
                            val = None
                        finally:
                            i2.env = s2
                        return val
                    frame.define("super", {mname: _super_call})
            for p, a in zip(mparams, margs):
                frame.define(p, a)
            i = host._interp()
            s = i.env
            i.env = frame
            # P16.2 — a `return` inside an if/else branch must propagate
            # out of eval_block and be caught here (not swallowed to None).
            try:
                val = i.eval_block(mbody)
            except _Return as r:
                val = r.value
            finally:
                i.env = s
            return val
        obj[mname] = method

    def _super_ctor(self, obj: dict, *args):
        if self.parent is None:
            return None
        pobj = self.parent.__call__(*args)
        obj["__parent__"] = pobj
        for k, v in pobj.items():
            if not k.startswith("__"):
                obj.setdefault(k, v)
        return pobj

    def __repr__(self):
        return f"<class {self.name}>"


# P11.2 — a JavaScript exception (value = thrown object / string / number)
class _Throw(Exception):
    def __init__(self, value):
        self.value = value


# P12.2 — `new` expression
@dataclass
class NewExpr(JNode):
    callee: JNode       # Ident (class name)
    args: list          # JNode


# ---------------------------------------------------------------------------
# Parser (Pratt / precedence climbing)
# ---------------------------------------------------------------------------

ADD_SUB = 1
MUL_DIV = 2
CMP = 3
LOGICAL_AND = 4
LOGICAL_OR = 5
NULLISH = 6
ASSIGN = 0
# P0 (GLM report) — ternary sits just above assignment but below
# logical-or; the right operand of a binary operator must not
# "swallow" a ternary `?` that belongs to the enclosing expression.
TERNARY = 7


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> Token:
        return self.toks[self.i]

    def next(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, value: str) -> Token:
        t = self.next()
        if t.value != value:
            raise SyntaxError(f"expected {value!r}, got {t.value!r} at pos {t.pos}")
        return t

    def match(self, value: str) -> bool:
        if self.peek().value == value:
            self.next()
            return True
        return False

    # --- expressions (Pratt) ---

    def parse_expression(self, min_prec: int = 1) -> JNode:
        """Parse a full expression at the given minimum precedence.

        Delegates to parse_expression_inner, but first tries to parse a
        top-level assignment (lowest precedence).

        P0 (GLM report) — when the caller's min_prec is at or above
        the ternary level, the right operand of a binary operator must
        NOT swallow a ternary `?` that belongs to the enclosing
        expression.  The legacy code always called
        ``parse_assignment_min`` for any min_prec ≤ ASSIGN, which
        caused ``6 % 2 === 0`` to be parsed as
        ``6 % (2 === 0)`` instead of ``(6 % 2) === 0``.
        """
        if min_prec <= ASSIGN:
            return self.parse_assignment_min()
        return self.parse_expression_inner(min_prec)

    def parse_assignment_min(self) -> JNode:
        """Parse at assignment precedence: `e = e`, `e += e`, `e -= e`.

        P0 (GLM report) — also handles the ternary conditional
        (`cond ? a : b`).  The ternary has lower precedence than
        logical-or (it sits just above assignment), so it is parsed
        at the same level as the assignment operator: after the
        condition expression, peek for `?` and, if found, parse the
        `a` branch at full expression precedence, expect `:`, then
        parse the `b` branch at the same level (right-associative,
        so nested ternaries bind to the nearest `:`).
        """
        left = self.parse_expression_inner(1)
        t = self.peek()
        # P0 — ternary: `left ? a : b`.
        if t.value == "?":
            self.next()
            then_branch = self.parse_expression(1)
            t = self.peek()
            if t.value != ":":
                raise SyntaxError(f"expected ':' after ternary then-branch, got {t.value!r}")
            self.next()
            else_branch = self.parse_assignment_min()  # right-associative
            return Ternary(left, then_branch, else_branch)
        if t.value in ("=", "+=", "-="):
            op = t.value
            self.next()
            value = self.parse_assignment_min()
            return Assign(left, op, value)
        return left

    def parse_expression_inner(self, min_prec: int) -> JNode:
        """P12.2 — Pratt-style precedence climbing.

        Precedence order (lower value = tighter binding):
          MUL_DIV (2) < CMP (3) < LOGICAL_AND (4) < LOGICAL_OR (5) < NULLISH (6)

        Left-associative operators (``*``, ``/``, ``+``, ``-``) use
        ``prec + 1`` for the right-operand minimum so that same-level
        operators chain left (``a * b * c`` → ``(a * b) * c``) while
        higher-precedence operators on the right bind tighter
        (``a + b * c`` → ``a + (b * c)``).
        """
        left = self.parse_unary()
        while True:
            t = self.peek()
            if t.kind == "eof":
                break
            prec = self._infix_prec(t)
            if prec < min_prec:
                break
            self.next()
            if t.value in ("&&", "||"):
                # Left-associative logical: right continues at same level.
                right = self.parse_expression(prec + 1)
                left = Logical(t.value, left, right)
            elif t.value == "??":
                # Right-associative nullish: right continues at same level.
                right = self.parse_expression(prec)
                left = Logical("??", left, right)
            else:
                op = t.value
                # Left-associative math / comparison ops:
                # right continues at prec + 1 (same or higher precedence).
                right = self.parse_expression(prec + 1)
                left = Binary(op, left, right)
        return left

    def _infix_prec(self, t: Token) -> int:
        if t.value in ("*", "/", "%"):
            return MUL_DIV
        if t.value in ("+", "-"):
            return ADD_SUB
        if t.value in ("==", "!=", "===", "!==", "<", ">", "<=", ">="):
            return CMP
        if t.value == "&&":
            return LOGICAL_AND
        if t.value == "||":
            return LOGICAL_OR
        if t.value == "??":
            return NULLISH
        return 0

    def _parse_infix_rest(self, first_op: str, right: JNode, min_prec: int) -> JNode:
        t = self.peek()
        if t.kind == "eof" or t.value in (";", "}", ")"):
            return right
        prec = self._infix_prec(t)
        if prec < min_prec:
            return right
        return self.parse_expression(min_prec)

    def parse_postfix(self) -> JNode:
        node = self.parse_primary()
        while True:
            t = self.peek()
            if t.value == ".":
                self.next()
                name_tok = self._expect_id()
                if self.peek().value == "(":
                    self.next()
                    args = self.parse_arg_list()
                    node = MethodCall(node, name_tok.value, args)
                else:
                    node = Member(node, name_tok.value)
            # P16.2 — optional chaining: ``a?.b`` / ``a?.b()`` / ``a?.[b]``.
            elif t.value == "?.":
                self.next()
                # ?.[ expr ]  — computed optional access.
                if self.peek().value == "[":
                    self.next()
                    idx = self.parse_expression(1)
                    self.expect("]")
                    node = OptionalChain(node, idx, call=False)
                    # Optional call after computed access: a?.[b](args)
                    if self.peek().value == "(":
                        self.next()
                        args = self.parse_arg_list()
                        node = OptionalChain(node, idx, call=True, call_args=args)
                elif self.peek().kind == "id":
                    name_tok = self._expect_id()
                    if self.peek().value == "(":
                        self.next()
                        args = self.parse_arg_list()
                        node = OptionalChain(node, name_tok.value, call=True, call_args=args)
                    else:
                        node = OptionalChain(node, name_tok.value, call=False)
                else:
                    # a?.(args) — direct optional call with no property.
                    self.next()  # consume "("
                    args = self.parse_arg_list()
                    node = OptionalChain(node, None, call=True, call_args=args)
            elif t.value == "[":
                self.next()
                idx = self.parse_expression(1)
                self.expect("]")
                node = Index(node, idx)
            elif t.value == "(":
                self.next()
                args = self.parse_arg_list()
                node = Call(node, args)
            # P33.R3 — postfix / prefix increment & decrement: ``i++``,
            # ``i--``, ``++i``, ``--i``.  Both return the numeric value
            # (postfix returns the value *before* the mutation, prefix
            # after).  The operand must be an Ident (toy: no
            # compound-assignment-to-member forms beyond ``obj.x++``
            # via the Member node).
            elif t.value in ("++", "--"):
                self.next()
                node = IncDec(node, op=t.value, prefix=False)
            else:
                break
        return node

    def parse_unary(self) -> JNode:
        """P33.R3 — prefix operators: ``++``, ``--`` before their
        operand (called from the primary-operand position of the Pratt
        parser).  ``++x`` / ``--x`` mutate the binding; the result is
        the new value.

        P0 (GLM report) — ``typeof`` is also a prefix operator in JS
        (``typeof x``, ``typeof "s"``, ``typeof function(){}``).  The
        legacy parser did not recognise it as a unary prefix, so any
        script that used ``typeof`` in a binary expression or as a
        bare operator raised a SyntaxError that killed the entire
        script (the all-or-nothing parse problem the GLM report
        flagged).  Now the parser produces a ``Unary`` node for
        ``typeof`` and the interpreter returns the correct string.
        """
        t = self.peek()
        if t.value in ("++", "--"):
            self.next()
            operand = self.parse_unary()
            return IncDec(operand, op=t.value, prefix=True)
        # P0 — typeof: a unary prefix operator that returns a string.
        if t.kind == "id" and t.value == "typeof":
            self.next()
            operand = self.parse_unary()
            return Unary(op="typeof", operand=operand)
        return self.parse_postfix()

    def _expect_id(self) -> Token:
        t = self.peek()
        if t.kind != "id":
            raise SyntaxError(f"expected identifier, got {t.value!r}")
        self.next()
        return t

    def parse_arg_list(self) -> list:
        args = []
        if self.peek().value == ")":
            self.next()
            return args
        while True:
            # P16.1 — argument spread: `f(...xs)` records a CallSpread node.
            if self.peek().value == "...":
                self.next()
                args.append(CallSpread(self.parse_expression(1)))
            else:
                args.append(self.parse_expression(1))
            if self.match(","):
                continue
            break
        self.expect(")")
        return args

    def parse_primary(self) -> JNode:
        t = self.peek()
        if t.kind == "number":
            self.next()
            return Num(float(t.value))
        if t.kind == "string":
            self.next()
            return Str(t.value)
        if t.kind == "id":
            self.next()
            name = t.value
            if name in ("true", "false"):
                return Bool(name == "true")
            if name in ("null", "undefined"):
                return Null()
            # P15.1 — Infinity / NaN literals (toy: special float values)
            if name == "Infinity":
                return Num(float("inf"))
            if name == "NaN":
                return Num(float("nan"))
            if name == "function":
                return self.parse_func_expr()
            # P14.2 — arrow: identifier followed by `=>` (e.g. `v => v * 2`)
            if self.peek().value == "=>":
                return self.parse_arrow([name])
            # P4.2 / P12.2 — `new Ctor(args...)`.  For class values the
            # interpreter calls the ToyClass directly, so `new C(...)` is
            # parsed into a Call node whose callee is the class name.
            if name == "new":
                # The next token must be the constructor identifier.
                if self.peek().kind != "id":
                    raise SyntaxError("new expects an identifier")
                ctor_tok = self.next()
                # Parse optional arguments.
                args = []
                if self.match("("):
                    while self.peek().value != ")":
                        args.append(self.parse_expression(0))
                        if not self.match(","):
                            break
                    self.expect(")")
                return NewExpr(Ident(ctor_tok.value), args)
            return Ident(name)
        if t.value == "(":
            self.next()
            # P14.2 — arrow: `() => body` (zero-param arrow)
            if self.peek().value == ")":
                self.next()  # consume `)`
                if self.peek().value == "=>":
                    return self.parse_arrow([])
                # Otherwise it was a plain `()` group — not valid in
                # expression position; treat as an error or no-op.
                raise SyntaxError("empty paren group in expression position")
            # P14.2 — arrow: `(a, b) => body` (multi-param arrow).
            # Look ahead: if the contents are a comma-separated list of
            # identifiers followed by `) =>`, parse as a param list.
            params = self._try_parse_arrow_params()
            if params is not None:
                return self.parse_arrow(params)
            # Otherwise: normal parenthesized expression.
            e = self.parse_expression(1)
            self.expect(")")
            return e
        # P13.2 — template literal: sequence of "tpl" text chunks interleaved
        # with "open_tpl" / expression / "close_tpl" markers.
        if t.kind == "tpl":
            parts: list = []
            # Consume the first text chunk.
            parts.append(self.next().value)
            while True:
                nt = self.peek()
                if nt.kind == "open_tpl":
                    self.next()  # consume "${"
                    expr = self.parse_expression(1)
                    self.next()  # consume "close_tpl"
                    parts.append(expr)
                    # After an interpolation the next token is the next
                    # text chunk (or close_tpl if the template ends with one).
                    nnt = self.peek()
                    if nnt.kind == "tpl":
                        parts.append(self.next().value)
                    else:
                        break
                else:
                    break
            return TemplateLit(parts)
        if t.value == "!":
            self.next()
            return Unary("!", self.parse_unary())
        if t.value == "-":
            self.next()
            return Unary("-", self.parse_unary())
        if t.value == "[":
            return self.parse_array_lit()
        if t.value == "{":
            return self.parse_object_lit()
        # P29.1 — regex literal: ``/pat/flags`` (lexer emits two tokens).
        if t.kind == "regexp":
            self.next()  # consume the regexp token
            flags = ""
            if self.peek().kind == "regexp_flags":
                flags = self.next().value
            return RegexpLit(t.value, flags)
        # P4.2 — `new Ctor(args)`: parse `new` + identifier as a Call node.
        # The `new` keyword is consumed; the Call is evaluated normally, and
        # if the callee is a constructor function (callable in globals), it
        # is invoked with the given args.
        if t.value == "new":
            self.next()  # consume "new"
            # The next token must be the constructor identifier.
            if self.peek().kind != "id":
                raise SyntaxError("new expects an identifier")
            ctor_name_tok = self.next()
            # Parse optional arguments.
            args = []
            if self.match("("):
                while self.peek().value != ")":
                    args.append(self.parse_expression(0))
                    if not self.match(","):
                        break
                self.expect(")")
            return Call(Ident(ctor_name_tok.value), args)
        raise SyntaxError(f"unexpected token {t.value!r} at pos {t.pos}")

    def parse_arrow(self, params: list) -> ArrowFunc:
        """P14.2 — parse the RHS of an arrow function body.

        Called after params have been parsed; the current token must be
        the arrow ``=>`` operator.  The body is either a single expression
        (implicit return) or a block (explicit return).
        """
        self.expect("=>")
        # If next token is `{` → block body; otherwise expression body.
        if self.peek().value == "{":
            body = self.parse_block()
            return ArrowFunc(params, body, is_block=True)
        else:
            body = self.parse_expression(0)
            return ArrowFunc(params, body, is_block=False)

    def _try_parse_arrow_params(self) -> list | None:
        """P14.2 — try to parse a comma-separated identifier list as an
        arrow param list.  Called *after* ``(`` has been consumed, with
        the parser positioned at the first inner token.

        On success returns the list of parameter names and has consumed
        the closing ``)``.  On failure returns ``None`` without having
        consumed anything, so the caller falls back to a parenthesized
        expression.  Fully speculative: the entire
        ``id ( , id )* ) =>`` pattern is peeked first; tokens are
        consumed only on a full match.
        """
        cand: list[str] = []
        j = self.i
        t = self.toks[j]
        if t.kind == "op" and t.value == ")":
            # `() => body` — zero-param arrow.
            j += 1
            if j >= len(self.toks):
                return None
            nt = self.toks[j]
            if nt.kind == "op" and nt.value == "=>":
                self.next()  # consume `)`
                return []
            return None
        if t.kind == "id" and t.value not in ("true", "false", "null", "undefined", "new", "function"):
            cand.append(t.value)
            j += 1
        else:
            return None
        # Consume comma-separated identifiers (speculative).
        while True:
            t = self.toks[j]
            if t.kind == "op" and t.value == ",":
                j += 1
                if j >= len(self.toks):
                    return None
                nt = self.toks[j]
                if nt.kind != "id" or nt.value in ("true", "false", "null", "undefined", "new", "function"):
                    return None  # not a param list
                cand.append(nt.value)
                j += 1
                continue
            break
        # The token after the last identifier must be `)`.
        t = self.toks[j]
        if t.kind != "op" or t.value != ")":
            return None  # not `)` — a parenthesized expression
        j += 1
        # The token after `)` must be `=>`.
        if j >= len(self.toks):
            return None
        nt = self.toks[j]
        if nt.kind != "op" or nt.value != "=>":
            return None  # not followed by `=>` — a parenthesized expression
        # Full match: consume tokens up to and including `)`.
        while self.i < j - 1:
            self.next()
        self.next()  # consume `)`
        return cand

    def parse_func_expr(self) -> FunExpr | GenFunExpr:
        # P29.2 — generator function expression: ``function* () { yield x }``.
        star = self.match("*")
        self.expect("(")
        params = []
        if self.peek().value != ")":
            while True:
                p = self._expect_id()
                params.append(p.value)
                if self.match(","):
                    continue
                break
        self.expect(")")
        body = self.parse_block()
        if star:
            return GenFunExpr(params, body, True)
        return FunExpr(params, body)

    def parse_array_lit(self) -> ArrayLit:
        """Parse ``[a, b, c]`` into an ArrayLit node.

        P16.1 — supports ``...expr`` spread elements: ``[...xs, 3]``.
        A spread element is recorded as an :class:`ArraySpread` node so
        the interpreter can splice the iterable's elements in place.
        """
        self.expect("[")
        elems: list = []
        if self.peek().value == "]":
            self.next()
            return ArrayLit(elems)
        while True:
            # P16.1 — array spread: ``...expr`` in element position.
            if self.peek().value == "...":
                self.next()
                elems.append(ArraySpread(self.parse_expression(0)))
            else:
                elems.append(self.parse_expression(0))
            if self.match(","):
                continue
            break
        self.expect("]")
        return ArrayLit(elems)

    def parse_object_lit(self) -> ObjectLit:
        """Parse `{a: 1, b: 2}` into an ObjectLit node.

        Supports `key: value` pairs where key is an identifier or
        string literal.  P13.1: also supports `{...obj}` spread syntax —
        the spread's source expression is recorded so the interpreter can
        merge its keys into the resulting dict.
        """
        self.expect("{")
        pairs: list = []
        if self.peek().value == "}":
            self.next()
            return ObjectLit(pairs)
        while True:
            # P16.1 — object spread: `{...obj}` — lexer emits a single "..." op.
            if self.peek().value == "...":
                self.next()
                src = self.parse_expression(0)
                pairs.append(("_spread_", ObjectSpread(src)))
                if self.match(","):
                    continue
                break
            key_tok = self.peek()
            if key_tok.kind == "id":
                key = key_tok.value
                self.next()
            elif key_tok.kind == "string":
                key = key_tok.value
                self.next()
            else:
                raise SyntaxError(f"expected property key, got {key_tok.value!r}")
            self.expect(":")
            val = self.parse_expression(0)
            pairs.append((key, val))
            if self.match(","):
                continue
            break
        self.expect("}")
        return ObjectLit(pairs)

    # --- statements ---

    def parse_program(self) -> Block:
        return self.parse_block_body(until_eof=True)

    def parse_block(self) -> Block:
        self.expect("{")
        body = self.parse_block_body(until_eof=False)
        self.expect("}")
        return body

    def parse_block_body(self, until_eof: bool) -> Block:
        stmts = []
        while True:
            t = self.peek()
            if until_eof and t.kind == "eof":
                break
            if not until_eof and t.value == "}":
                break
            # P29.2 — ``yield [expr]`` statement (valid only inside a
            # generator body; the parser accepts it at statement level).
            if t.kind == "id" and t.value == "yield":
                stmts.append(self.parse_yield_stmt())
                continue
            stmts.append(self.parse_statement())
        return Block(stmts)

    def parse_statement(self) -> JNode:
        t = self.peek()
        if t.value == "{":
            block = self.parse_block()
            # P33.R3 — allow a bare block statement to be terminated by
            # an optional ``;`` (JS permits `{ ... };`).  The ``;`` is
            # not part of the Block node; it is consumed here so the
            # enclosing statement loop does not choke on it.
            self.match(";")
            return block
        if t.kind == "id" and t.value in ("let", "const", "var"):
            decl_kind = t.value
            self.next()
            # P13.1 — destructuring: `let {a, b} = obj` / `let [x, y] = arr`
            nt = self.peek()
            if nt.value in ("{", "["):
                return self.parse_destructuring(decl_kind)
            name = self._expect_id().value
            value = None
            if self.match("="):
                # P0 (GLM report) — the RHS of a declaration may be a
                # ternary (`c ? 1 : 2`) or an assignment; parse at the
                # lowest precedence so the full expression (including
                # the ternary handled by parse_assignment_min) is
                # captured.  The legacy ``parse_expression(1)`` went
                # straight to the Pratt loop and stopped at ``?``
                # (prec 0 < 1), raising "unexpected token '?'".
                value = self.parse_expression(0)
            self.match(";")
            return LetDecl(name, value, decl_kind)
        if t.value == "function":
            return self.parse_func_decl()
        if t.value == "if":
            return self.parse_if()
        if t.value == "while":
            return self.parse_while()
        if t.value == "for":
            return self.parse_for()
        if t.value == "try":
            self.next()
            body = self.parse_block()
            catch_param = None
            catch_body = None
            finally_body = None
            if self.match("catch"):
                if self.match("("):
                    catch_param = self._expect_id().value
                    self.expect(")")
                catch_body = self.parse_block()
            if self.match("finally"):
                finally_body = self.parse_block()
            return Try(body, catch_param, catch_body, finally_body)
        if t.value == "class":
            return self.parse_class()
        if t.value == "return":
            self.next()
            value = None
            if self.peek().value not in (";", "}", ""):
                # P0 (GLM report) — the return RHS may be a ternary
                # (`return n<=1 ? 1 : 2`); parse at the lowest
                # precedence so parse_assignment_min captures the
                # full ternary.  The legacy parse_expression(1)
                # stopped at `?` and raised a SyntaxError.
                value = self.parse_expression(0)
            self.match(";")
            return Return(value)
        if t.value == "throw":
            self.next()
            value = self.parse_expression(1)
            self.match(";")
            return Throw(value)
        if t.value == "break":
            self.next()
            self.match(";")
            return ExprStmt(Unary("break", Null()))
        if t.value == "continue":
            self.next()
            self.match(";")
            return ExprStmt(Unary("continue", Null()))
        # P0 (GLM report) — an isolated `;` (e.g. after a function
        # declaration ``function A(){};``) is an empty statement in
        # JS, not a syntax error.  The legacy parser tried to parse
        # an expression, saw `;`, and raised "unexpected token ';'",
        # which killed the entire script (the all-or-nothing parse
        # problem the GLM probe flagged).
        if self.peek().value == ";":
            self.next()
            return ExprStmt(Null())
        # expression statement (or assignment)
        expr = self.parse_expression(0)
        self.match(";")
        if isinstance(expr, Assign):
            return expr
        return ExprStmt(expr)

    def parse_func_decl(self) -> FuncDecl | GenFuncDecl:
        self.expect("function")
        # P29.2 — generator function: ``function* name() { ... }``.
        star = self.match("*")
        name_tok = self._expect_id()
        self.expect("(")
        params = []
        if self.peek().value != ")":
            while True:
                p = self._expect_id()
                params.append(p.value)
                if self.match(","):
                    continue
                break
        self.expect(")")
        body = self.parse_block()
        if star:
            return GenFuncDecl(name_tok.value, params, body, True)
        return FuncDecl(name_tok.value, params, body)

    def parse_yield_stmt(self) -> YieldExpr:
        """P29.2 — parse ``yield [expr]`` as a YieldExpr node."""
        self.expect("yield")
        value = None
        if self.peek().value not in (";", "}", ""):
            value = self.parse_expression(0)
        self.match(";")
        return YieldExpr(value)

    def parse_if(self) -> If:
        self.expect("if")
        self.expect("(")
        cond = self.parse_expression(1)
        self.expect(")")
        then = self.parse_statement()
        else_ = None
        if self.match("else"):
            else_ = self.parse_statement()
        return If(cond, then, else_)

    def parse_while(self) -> While:
        self.expect("while")
        self.expect("(")
        cond = self.parse_expression(1)
        self.expect(")")
        body = self.parse_statement()
        return While(cond, body)

    def parse_for(self) -> For | ForOf:
        self.expect("for")
        self.expect("(")
        # P29.2 — ``for...of``: ``for (let|const|var name of expr) body``.
        # Detect the ``of`` keyword; rewind on non-for-of shapes.
        init = None
        init_kind = None
        if self.peek().kind == "id" and self.peek().value in ("let", "const", "var"):
            save_i = self.i
            kw = self.next()  # consume let/const/var
            name_tok = self.peek()
            if name_tok.kind == "id":
                save_i2 = self.i
                self.next()  # consume the name
                if self.peek().value == "of":
                    self.next()  # consume "of"
                    iterable = self.parse_expression(0)
                    self.expect(")")
                    body = self.parse_statement()
                    return ForOf(name_tok.value, iterable, body)
                # Not for-of: rewind and parse as classic for.
                self.i = save_i
        # P33.R3 — classic C-style for: the init may be a
        # ``let``/``const``/``var`` declaration (e.g.
        # ``for (let i = 0; i < 3; i++)``).  Parse it as a
        # LetDecl so the evaluator applies block scoping + TDZ.
        if self.peek().kind == "id" and self.peek().value in ("let", "const", "var"):
            decl_kind = self.next().value
            decl_name = self._expect_id().value
            decl_val = None
            if self.match("="):
                decl_val = self.parse_expression(1)
            self.match(";")
            init = LetDecl(decl_name, decl_val, decl_kind)
        elif self.peek().value != ";":
            init = self.parse_expression(0)
            self.match(";")
        else:
            self.next()
        cond = None
        if self.peek().value != ";":
            cond = self.parse_expression(1)
        self.expect(";")
        step = None
        if self.peek().value != ")":
            step = self.parse_expression(0)
        self.expect(")")
        body = self.parse_statement()
        return For(init, cond, step, body)

    # P13.1 — destructuring declaration
    def parse_destructuring(self, kind: str = "var") -> DestructuringDecl:
        """Parse `let {a, b} = obj` or `let [x, y] = arr` (P13.1).

        The `let`/`const`/`var` keyword has already been consumed by
        `parse_statement`; this method handles the opening brace or
        bracket.  P33.R3 — *kind* is the declaration kind so the
        evaluator can apply block scoping + TDZ.
        """
        binding_char = self.peek().value
        self.next()  # consume "{" or "["
        bindings: list[str] = []
        keys: list[str] | None = None
        if binding_char == "{":
            # Object destructuring: `{a, b}` or `{x: foo, y: bar}`
            keys = []
            while True:
                t = self.peek()
                if t.value == "}" or t.kind == "eof":
                    break
                # `a` or `a: alias`
                prop_name = self._expect_id().value
                if self.match(":"):
                    alias = self._expect_id().value
                    keys.append(prop_name)
                    bindings.append(alias)
                else:
                    keys.append(prop_name)
                    bindings.append(prop_name)
                if self.match(","):
                    continue
                break
            self.expect("}")
        else:
            # Array destructuring: `[x, y]`
            while True:
                t = self.peek()
                if t.value == "]" or t.kind == "eof":
                    break
                bindings.append(self._expect_id().value)
                if self.match(","):
                    continue
                break
            self.expect("]")
        self.expect("=")
        value = self.parse_expression(1)
        self.match(";")
        return DestructuringDecl(bindings, value, keys, kind)

    # P12.2 — class declaration
    def parse_class(self) -> ClassDecl:
        self.expect("class")
        name = self._expect_id().value
        parent_name = None
        if self.match("extends"):
            parent_name = self._expect_id().value
        self.expect("{")
        methods: list = []
        while True:
            t = self.peek()
            if t.value == "}" or t.kind == "eof":
                break
            # P12.2 — method name is an identifier; but the parser sees the
            # *next* token right after the opening "{".  If that token is not
            # an identifier (e.g. an operator like "*" from a prior line),
            # the class syntax is malformed — surface a clear error.
            mname = self._expect_id().value
            self.expect("(")
            mparams: list[str] = []
            if self.peek().value != ")":
                while True:
                    mparams.append(self._expect_id().value)
                    if not self.match(","):
                        break
            self.expect(")")
            mbody = self.parse_block()
            methods.append((mname, mparams, mbody))
        self.expect("}")
        return ClassDecl(name, parent_name, methods)

    def parse(self) -> Block:
        return self.parse_program()


def parse_program(src: str) -> Block:
    return Parser(lex(src)).parse()


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


class Env:
    """P33.R3 — lexical environment with block-level binding records.

    Each binding is a :class:`_Binding` (value + kind + initialized
    flag) so that ``let``/``const`` can be block-scoped with a
    temporal dead zone (TDZ): the binding is *created* when the
    enclosing block is entered (before its initializer runs), and
    *initialized* when the declaration statement executes.  Reading
    a name that is in the TDZ raises :class:`TDZError`.
    """

    def __init__(self, scope: "dict | None" = None,
                 parent: Optional["Env"] = None):
        # P33.R3 — the scope maps name → _Binding.  Callers may still
        # pass a raw dict (the legacy host-globals shim); a raw dict is
        # wrapped in a fresh record map.
        if scope is None:
            scope = {}
        self.scope = scope
        self.parent = parent

    def lookup(self, name: str) -> Any:
        e = self
        while e is not None:
            b = e.scope.get(name)
            if b is not None:
                if isinstance(b, _Binding):
                    if not b.initialized:
                        raise TDZError(
                            f"Cannot access '{name}' before initialization")
                    return b.value
                # Legacy raw-value entry (host-globals dict).
                return b
            e = e.parent
        raise NameError(f"'{name}' is not defined")

    def set(self, name: str, value: Any) -> None:
        e = self
        while e is not None:
            b = e.scope.get(name)
            if b is not None:
                # P33.R3 — const bindings reject re-assignment; a
                # ``const`` in the TDZ also rejects (the binding was
                # declared but not initialized yet).
                if isinstance(b, _Binding):
                    if b.kind == "const":
                        raise RuntimeError(
                            f"Assignment to constant variable '{name}'")
                    if not b.initialized:
                        raise TDZError(
                            f"Cannot assign to '{name}' before initialization")
                    b.value = value
                else:
                    # Legacy raw-value entry: just overwrite.
                    e.scope[name] = value
                return
            e = e.parent
        self.define(name, value)

    def define(self, name: str, value: Any, kind: str = "var") -> None:
        """P33.R3 — create (or overwrite) a binding in this scope.

        ``kind`` is the declaration kind ("var", "let", "const").
        A *var* declaration at a block level hoists the binding into
        the nearest function scope (legacy behavior); a *let* /
        *const* declaration creates the binding in the *current*
        block scope (the TDZ record is created here).
        """
        if kind in ("let", "const"):
            self.scope[name] = _Binding(value, kind,
                                         initialized=True)
        else:
            # P33.R3 — var stays function-scoped: if the current env is
            # a block scope, hoist into the enclosing function scope.
            target = self._func_scope()
            target.scope[name] = _Binding(value, "var",
                                          initialized=True)

    def declare_block(self, name: str, kind: str) -> None:
        """P33.R3 — create the TDZ record for a block-level binding.

        Called at the *top* of a block (before its statements run)
        for each ``let``/``const`` declaration inside that block.
        The binding is not initialized yet; the matching
        :meth:`define` call fills in the value.
        """
        if kind in ("let", "const"):
            self.scope[name] = _Binding(None, kind,
                                        initialized=False)

    def _func_scope(self) -> "Env":
        """P33.R3 — the nearest function-level scope (an env with
        ``_is_func`` True), or this env when already at function
        level.  Block envs are marked with ``_is_func=False``."""
        e = self
        while e is not None:
            if getattr(e, "_is_func", True):
                return e
            e = e.parent
        return self


class _Binding:
    """P33.R3 — one lexical binding record (value + kind + initialized)."""

    __slots__ = ("value", "kind", "initialized")

    def __init__(self, value, kind: str, initialized: bool):
        self.value = value
        self.kind = kind
        self.initialized = initialized


class _Return(Exception):
    def __init__(self, value):
        self.value = value


# P29.2 — yield sentinel: raised inside a generator body to pause it.
class _Yield(Exception):
    def __init__(self, value):
        self.value = value


# P29.2 — Symbol.iterator toy marker.  A value carrying this as its
# "iterator" key routes ``for...of`` through the iterator protocol.
class SymbolIteratorMarker:
    """Toy stand-in for ``Symbol.iterator``; the string ``"@@iterator"``
    is used as the dict key when JS code stores a custom iterator."""
    KEY = "@@iterator"


# P29.2 — yield sentinel: raised inside a generator body to pause it.
class _Yield(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _BudgetExceeded(Exception):
    """P0 (GPT report) — raised when the per-call JS execution budget
    is exceeded (a busy loop that does not terminate, or a program
    that exceeds the step budget).  The host catches it and logs the
    cancellation instead of hanging the GUI thread."""


class Interpreter:
    def __init__(self, host: "Host"):
        self.host = host
        self.env = Env({})
        self.this_value = None  # P11.4 — current `this` binding for event handlers
        # P0 — per-call execution budget.  The legacy interpreter had
        # no bound: a script that never terminates (while(true) {}
        # with no break, or a recursive function that does not return)
        # hangs the GUI thread forever.  The P0 budget counts
        # interpreter *steps* (each eval call) and raises
        # _BudgetExceeded when the per-call limit is reached.  The
        # host catches it and logs the cancellation.
        self._steps: int = 0
        self._budget: int = getattr(host, "_js_step_budget", 2_000_000)
        self._cancelling: bool = False  # set by host.cancel_scripts()
        self._recursion_depth: int = 0
        self._max_recursion: int = getattr(host, "_js_max_recursion", 256)

    @staticmethod
    def truthy(v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v != 0
        if isinstance(v, str):
            return len(v) > 0
        return True

    def eval(self, node: JNode) -> Any:
        # P0 — per-call execution budget: count each node evaluation
        # as one step; raise _BudgetExceeded when the budget is
        # exhausted.  A script that never terminates (while(true){}
        # with no break, or a non-terminating recursion) can no
        # longer hang the GUI thread.
        self._steps += 1
        if self._steps >= self._budget:
            raise _BudgetExceeded(
                f"JS execution budget exhausted ({self._budget} steps)")
        # P0 — cooperative cancellation: the host can set
        # _cancelling on a running interpreter (from another thread
        # or from a timer callback) to stop the script early.
        if self._cancelling:
            raise _BudgetExceeded("JS execution cancelled by host")
        if isinstance(node, Block):
            return self.eval_block(node)
        if isinstance(node, Num):
            return node.value
        if isinstance(node, Str):
            return node.value
        # P13.2 — template literal: concatenate chunks, evaluating each
        # interpolation expression with JS string-concatenation semantics.
        if isinstance(node, TemplateLit):
            def _jsnum(x):
                # P15.1 — Infinity / NaN / whole-float JS formatting.
                import math
                if isinstance(x, float):
                    if math.isinf(x):
                        return "Infinity" if x > 0 else "-Infinity"
                    if math.isnan(x):
                        return "NaN"
                    if x.is_integer():
                        return str(int(x))
                if x is None:
                    return "null"
                if x is True:
                    return "true"
                if x is False:
                    return "false"
                return str(x)
            out = []
            for part in node.chunks:
                if isinstance(part, str):
                    out.append(part)
                else:
                    out.append(_jsnum(self.eval(part)))
            return "".join(out)
        if isinstance(node, Bool):
            return node.value
        if isinstance(node, Null):
            return None
        if isinstance(node, Ident):
            # P11.4 — `this` keyword resolves to the current this_value.
            if node.name == "this" and self.this_value is not None:
                return self.this_value
            # P12.2 — `super` resolves to the current frame's super binding.
            if node.name == "super":
                try:
                    return self.env.lookup("super")
                except NameError:
                    return None
            try:
                return self.env.lookup(node.name)
            except TDZError:
                raise
            except NameError:
                if node.name in self.host.globals:
                    return self.host.globals[node.name]
                return None
        if isinstance(node, LetDecl):
            # P33.R3 — let/const initialize the TDZ record created at
            # block entry; var defines directly (no TDZ).  const
            # re-assignment is rejected by Env.set.
            v = self.eval(node.value) if node.value is not None else None
            self.env.define(node.name, v, kind=node.kind)
            return v
        # P13.1 — destructuring declaration (P33.R3: kind-aware)
        if isinstance(node, DestructuringDecl):
            rhs = self.eval(node.value)
            kind = node.kind
            if node.keys is not None:
                # Object destructuring: pluck properties from a dict.
                for key, name in zip(node.keys, node.bindings):
                    val = rhs.get(key) if isinstance(rhs, dict) else None
                    self.env.define(name, val, kind=kind)
            else:
                # Array destructuring: bind by position.
                for i, name in enumerate(node.bindings):
                    val = rhs[i] if isinstance(rhs, (list, tuple)) and i < len(rhs) else None
                    self.env.define(name, val, kind=kind)
            return None
        if isinstance(node, FuncDecl):
            f = self._make_func(node.name, node.params, node.body)
            self.env.define(node.name, f)
            return f
        # P29.2 — generator function declaration.
        if isinstance(node, GenFuncDecl):
            f = self._make_gen_func(node.name, node.params, node.body)
            self.env.define(node.name, f)
            return f
        # P29.2 — generator function expression.
        if isinstance(node, GenFunExpr):
            return self._make_gen_func(None, node.params, node.body)
        # P29.1 — regexp literal.
        if isinstance(node, RegexpLit):
            return self.eval_regexp_lit(node)
        # P29.2 — for...of.
        if isinstance(node, ForOf):
            return self.eval_for_of(node)
        # P12.2 — class declaration: register a ToyClass under the global name.
        if isinstance(node, ClassDecl):
            parent = self.host._class_registry.get(node.parent_name) if node.parent_name else None
            cls = ToyClass(node.name, node.methods, parent, self.host)
            self.host._class_registry[node.name] = cls
            self.host.globals[node.name] = cls
            return None
        if isinstance(node, FunExpr):
            return self._make_func(None, node.params, node.body)
        # P14.2 — arrow function
        if isinstance(node, ArrowFunc):
            return self._make_arrow(node.params, node.body, node.is_block)
        if isinstance(node, Binary):
            return self.eval_binary(node)
        if isinstance(node, Unary):
            return self.eval_unary(node)
        if isinstance(node, Logical):
            return self.eval_logical(node)
        # P0 (GLM report) — ternary: evaluate only the chosen branch
        # (lazy semantics, matching the JS spec).
        if isinstance(node, Ternary):
            return self.eval(node.then) if self.truthy(self.eval(node.cond)) else self.eval(node.else_)
        if isinstance(node, Member):
            return self.eval_member(node)
        if isinstance(node, Index):
            obj = self.eval(node.obj)
            idx = self.eval(node.index)
            if isinstance(obj, (list, tuple)) and isinstance(idx, (int, float)):
                idx = int(idx)
                if 0 <= idx < len(obj):
                    return obj[idx]
                return None
            return None
        if isinstance(node, Call):
            callee = self.eval(node.callee)
            # P16.1 — evaluate arguments, splicing CallSpread nodes inline.
            # P0 — a CallSpread of null/undefined splices ZERO args
            # (the legacy code appended the null value itself, so
            # f(...[]) passed null and f(...null) passed null too).
            args: list = []
            for a in node.args:
                if isinstance(a, CallSpread):
                    src = self.eval(a.expr)
                    if isinstance(src, list):
                        args.extend(src)
                    elif src is None:
                        pass  # spreading null → zero args
                    else:
                        args.append(src)
                else:
                    args.append(self.eval(a))
            if callable(callee):
                try:
                    return callee(*args)
                except _Throw:
                    raise
                except TDZError:
                    raise  # P33.R3 — TDZ must propagate
                except _BudgetExceeded:
                    # P0 — a non-terminating or over-deep recursion
                    # must propagate to the host's catch-all so the
                    # script is cancelled, not swallowed.
                    raise
                except RecursionError:
                    # P0 — Python's native stack overflow (each JS
                    # frame = 2-4 Python frames; the default ~1000-
                    # frame limit trips at ~250 JS levels, right at
                    # the guard's 256 cap).  RecursionError is a
                    # subclass of RuntimeError, so this clause must
                    # come BEFORE the generic RuntimeError handler
                    # below; otherwise the RuntimeError clause logs
                    # it as a benign "call error" and returns None,
                    # silently swallowing the runaway recursion.
                    raise _BudgetExceeded(
                        "JS recursion depth exceeded (Python RecursionError)")
                except RuntimeError as e:
                    if "constant variable" in str(e):
                        raise  # P33.R3 — const re-assignment must propagate
                    self.host.log(f"call error: {e}")
                    return None
                except Exception as e:
                    # P4.2 — constructor call (`new Date()`): if the direct
                    # call failed, retry through the host's constructor table.
                    if isinstance(node.callee, Ident):
                        ctor_name = node.callee.name
                        gobj = self.host.globals.get(ctor_name)
                        if callable(gobj):
                            try:
                                return gobj(*args)
                            except Exception as e2:
                                self.host.log(f"constructor error: {e2}")
                                return None
                    self.host.log(f"call error: {e}")
                    return None
            # Not callable — try as a constructor object (dict).
            if isinstance(node.callee, Ident):
                ctor_name = node.callee.name
                gobj = self.host.globals.get(ctor_name)
                if gobj is not None:
                    if callable(gobj):
                        try:
                            return gobj(*args)
                        except Exception as e:
                            self.host.log(f"constructor error: {e}")
                    elif isinstance(gobj, dict):
                        return gobj
            return None
        # P16.2 — optional chaining: short-circuit to null when base is null.
        if isinstance(node, OptionalChain):
            base = self.eval(node.base)
            if base is None:
                return None
            # Computed access: base?.[expr]
            if isinstance(node.prop, JNode):
                key = self.eval(node.prop)
            else:
                key = node.prop
            # Direct optional call with no property: f?.(args)
            if key is None and node.call:
                if callable(base):
                    args = [self.eval(a) for a in (node.call_args or [])]
                    return base(*args)
                return None
            # Property access: base?.key
            if isinstance(base, DomElement):
                if node.call:
                    m = self.host.call_method(base, str(key), [self.eval(a) for a in (node.call_args or [])])
                    return m
                return self.host.eval_element_prop(base, str(key))
            if isinstance(base, dict):
                if node.call:
                    val = base.get(key)
                    if callable(val):
                        args = [self.eval(a) for a in (node.call_args or [])]
                        return val(*args)
                    return None
                return base.get(key)
            # P16.2 — list / string: built-in .length (matches eval_member).
            if isinstance(base, list):
                if str(key) == "length":
                    return len(base)
                return None
            if isinstance(base, str):
                if str(key) == "length":
                    return len(base)
                return None
            # Non-dict host object (built-in objects, callables, …).
            if node.call:
                if callable(base):
                    args = [self.eval(a) for a in (node.call_args or [])]
                    return base(*args)
                return None
            attr = getattr(base, str(key), None)
            if attr is not None:
                return attr
            return None
        if isinstance(node, MethodCall):
            # P12.2 — `super.method(args)`: the parser produces
            # MethodCall(Ident("super"), ...). Resolve via the current
            # frame's `super` binding, which returns the parent method.
            if isinstance(node.obj, Ident) and node.obj.name == "super":
                super_obj = None
                try:
                    super_obj = self.env.lookup("super")
                except NameError:
                    pass
                args = [self.eval(a) for a in node.args]
                # super_obj is a bound callable if in a method frame,
                # or a dict of parent methods.
                if callable(super_obj):
                    return super_obj(*args)
                if isinstance(super_obj, dict):
                    sm = super_obj.get(node.method)
                    if sm is not None:
                        return sm(*args)
                return None
            obj = self.eval(node.obj)
            args = [self.eval(a) for a in node.args]
            return self.host.call_method(obj, node.method, args)
        # P12.2 — `new C(args)`: user class → ToyClass; built-in name →
        # fall back to the host's constructor table.
        if isinstance(node, NewExpr):
            cls_name = node.callee.name if isinstance(node.callee, Ident) else None
            args = [self.eval(a) for a in node.args]
            cls = self.host._class_registry.get(cls_name)
            if cls is not None:
                return cls(*args)
            # P4.2 — built-in constructors (Date / RegExp / Map / Set / …).
            gobj = self.host.globals.get(cls_name)
            if callable(gobj):
                try:
                    return gobj(*args)
                except Exception as e:
                    self.host.log(f"constructor error: {e}")
                    return None
            self.host.log(f"new {cls_name}: class not found")
            return None
        if isinstance(node, Assign):
            return self.eval_assign(node)
        if isinstance(node, If):
            return self.eval_if(node)
        # P11.2 — try / catch / finally
        if isinstance(node, Try):
            return self.eval_try(node)
        if isinstance(node, While):
            return self.eval_while(node)
        if isinstance(node, For):
            return self.eval_for(node)
        if isinstance(node, IncDec):
            return self.eval_incdec(node)
        if isinstance(node, Return):
            v = self.eval(node.value) if node.value is not None else None
            raise _Return(v)
        if isinstance(node, ExprStmt):
            return self.eval(node.expr)
        if isinstance(node, ArrayLit):
            out = []
            for e in node.elements:
                if isinstance(e, ArraySpread):
                    # P16.1 — splice the iterable's elements in place.
                    src = self.eval(e.expr)
                    if isinstance(src, list):
                        out.extend(src)
                    else:
                        out.append(src)
                else:
                    out.append(self.eval(e))
            return out
        if isinstance(node, ObjectLit):
            # P13.1 — handle object spread pairs (value is an ObjectSpread node).
            result = {}
            for k, v in node.pairs:
                if k == "_spread_" and isinstance(v, ObjectSpread):
                    src = self.eval(v.source)
                    if isinstance(src, dict):
                        for sk, sv in src.items():
                            if not sk.startswith("__"):
                                result[sk] = sv
                else:
                    result[k] = self.eval(v)
            return result
        # P11.2 — throw expression: evaluate the value then raise.
        if isinstance(node, Throw):
            value = self.eval(node.value)
            raise _Throw(value)
        raise TypeError(f"cannot evaluate {node}")

    def eval_block(self, block: Block) -> Any:
        """P33.R3 — evaluate a block in a *fresh* lexical scope.

        Each ``Block`` node now creates its own :class:`Env` (marked
        ``_is_func=False`` when it is a plain block, not a function
        frame).  Before the statements run, every ``let`` /
        ``const`` declaration directly inside the block is *declared*
        (TDZ record created) so that a read of the name inside the
        block before its initializer raises :class:`TDZError`
        instead of falling through to an outer scope.  ``var``
        declarations keep the legacy function-level hoisting (no TDZ
        record; the binding lands in the nearest function scope).

        P0 — the block's last statement's value is returned so the
        P9.3 IIFE wrapper in ``Host.eval`` can capture it.  A
        ``return`` statement inside a plain block (not a function
        frame) surfaces its value instead of propagating the
        ``_Return`` exception.
        """
        old_env = self.env
        # P0 — capture the parent's _is_func flag BEFORE creating the
        # block env: when the parent is a function frame (a function
        # body or an IIFE), _Return must propagate out of the block
        # so the function exits; when the parent is a plain block
        # (top-level, or a nested if/else/while body), _Return is
        # caught and surfaced as the block's return value.
        parent_is_func = getattr(old_env, "_is_func", False)
        block_env = Env({}, old_env)
        block_env._is_func = False
        self.env = block_env
        # P33.R3 — create the TDZ records for direct let/const decls.
        for s in block.stmts:
            if isinstance(s, LetDecl) and s.kind in ("let", "const"):
                block_env.declare_block(s.name, s.kind)
        last: Any = None
        try:
            for s in block.stmts:
                last = self.eval(s)
        except _Return as r:
            # P0 — a `return` statement inside a plain block (not a
            # function frame) surfaces its value instead of propagating
            # the _Return exception.  The guard checks the *parent*
            # env's _is_func (captured above): when the parent is a
            # function frame, _Return must propagate to the
            # function-level catch in _make_func / _make_arrow so
            # that `return 'x'` actually exits the function.  The
            # legacy unconditional catch swallowed every return inside
            # a function body, so the last return statement in the
            # body always won.
            if not parent_is_func:
                last = r.value
            else:
                raise
        finally:
            self.env = old_env
        return last

    # P29.2 — yield: raise _Yield so the generator wrapper can pause.
    def eval_yield(self, node: YieldExpr) -> Any:
        val = self.eval(node.value) if node.value is not None else None
        raise _Yield(val)

    # P29.1 — regexp literal: build the same dict as ``new RegExp(...)``.
    def eval_regexp_lit(self, node: RegexpLit) -> Any:
        return self.host._regexp_ctor()(node.pattern, node.flags)

    # P29.2 — for...of: iterate a JS value; `break`/`continue`/`return`
    # follow the same loop-local / function-escape rules as classic for.
    def eval_for_of(self, node: ForOf) -> Any:
        iterable = self.eval(node.iterable)
        items = self._js_iterable(iterable)
        old_env = self.env
        self.env = Env({}, old_env)  # for-of binding scope
        self.env._is_func = False
        # P33.R3 — the loop body (typically a Block) declares its own
        # let/const bindings; we must not shadow them from the for-of
        # frame.  The body's own eval_block creates a child env.
        try:
            for item in items:
                self.env.define(node.target, item, kind="var")
                try:
                    self.eval(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
        except _Return:
            self.env = old_env
            raise
        self.env = old_env
        return None

    # P11.2 — try / catch / finally
    def eval_try(self, node: Try) -> Any:
        result = None
        try:
            result = self.eval_block(node.body)
        except _Throw as exc:
            if node.catch_body is not None:
                old = self.env
                self.env = Env({}, old)
                if node.catch_param:
                    self.env.set(node.catch_param, exc.value)
                result = self.eval_block(node.catch_body)
                self.env = old
            else:
                raise
        finally:
            if node.finally_body is not None:
                self.eval_block(node.finally_body)
        # P16.2 — let _Return propagate out of try bodies (a `return` inside
        # a try block must reach the function-level catch).
        if isinstance(result, _Return):
            raise result
        return result

    def eval_binary(self, node: Binary) -> Any:
        op = node.op
        l = self.eval(node.left)
        r = self.eval(node.right)
        if op == "+":
            if isinstance(l, str) or isinstance(r, str):
                # P12.2 — JS semantics: numbers become "1" not "1.0".
                # P15.1 — Infinity / NaN formatting.
                import math
                def _jsnum(x):
                    if isinstance(x, float):
                        if math.isinf(x):
                            return "Infinity" if x > 0 else "-Infinity"
                        if math.isnan(x):
                            return "NaN"
                        if x.is_integer():
                            return str(int(x))
                    return str(x)
                return _jsnum(l) + _jsnum(r)
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/":
            # P0 (GLM report) — JS `/` returns Infinity / -Infinity /
            # NaN for zero divisors instead of raising ZeroDivisionError.
            # ``1/0`` → Infinity, ``-1/0`` → -Infinity, ``0/0`` → NaN.
            import math
            if r == 0:
                if l == 0:
                    return float("nan")
                return float("inf") if l > 0 else float("-inf")
            return l / r
        if op == "%":
            # P0 (GLM report) — JS ``%`` follows the sign of the
            # dividend (C-style remainder), unlike Python's floor
            # modulo.  ``-7 % 3`` is ``-1`` in JS but ``2`` in
            # Python.  Convert with a sign-corrected formula.
            if r == 0:
                import math
                if l == 0:
                    return float("nan")
                return float("inf") if l > 0 else float("-inf")
            q = abs(l) % abs(r)
            return q if l >= 0 else -q
        if op in ("==", "==="):
            if op == "===":
                return type(l) is type(r) and l == r
            # P0 (GLM report) — JS loose equality with type coercion:
            # ``1 == "1"`` is true (both coerce to the number 1),
            # ``null == undefined`` is true, booleans coerce to
            # numbers before comparing.
            if type(l) is type(r):
                return l == r
            # Cross-type coercion (loose == only).
            if isinstance(l, bool) and isinstance(r, (int, float)):
                return (1 if l else 0) == r
            if isinstance(r, bool) and isinstance(l, (int, float)):
                return l == (1 if r else 0)
            if isinstance(l, str) and isinstance(r, (int, float)):
                try:
                    return float(l) == r
                except ValueError:
                    return False
            if isinstance(r, str) and isinstance(l, (int, float)):
                try:
                    return l == float(r)
                except ValueError:
                    return False
            if (l is None and r is None) or (l is None and r is None):
                return True
            return False
        if op in ("!=", "!=="):
            if op == "!==":
                return not (type(l) is type(r) and l == r)
            # P0 (GLM report) — negation of loose ==.
            if type(l) is type(r):
                return l != r
            if isinstance(l, bool) and isinstance(r, (int, float)):
                return (1 if l else 0) != r
            if isinstance(r, bool) and isinstance(l, (int, float)):
                return l != (1 if r else 0)
            if isinstance(l, str) and isinstance(r, (int, float)):
                try:
                    return float(l) != r
                except ValueError:
                    return True
            if isinstance(r, str) and isinstance(l, (int, float)):
                try:
                    return l != float(r)
                except ValueError:
                    return True
            return l != r
        if op == "<":
            return l < r
        if op == ">":
            return l > r
        if op == "<=":
            return l <= r
        if op == ">=":
            return l >= r
        raise TypeError(f"unsupported operator {op}")

    def eval_unary(self, node: Unary) -> Any:
        # P0 (GLM report) — `typeof` is a *unary* operator in JS but
        # the legacy parser only recognised it as a prefix in
        # limited contexts.  Support it here so the eval path returns
        # the correct string for any value (including ``undefined``,
        # which JS maps to the string ``"undefined"``, not ``None``).
        if node.op == "typeof":
            v = self.eval(node.operand)
            if v is None:
                return "undefined"
            if v is True:
                return "boolean"
            if v is False:
                return "boolean"
            if isinstance(v, (int, float)):
                return "number"
            if isinstance(v, str):
                return "string"
            if isinstance(v, (list, tuple, dict)):
                return "object"
            if callable(v):
                return "function"
            return "object"
        if node.op == "!":
            return not self.truthy(self.eval(node.operand))
        if node.op == "-":
            return -self.eval(node.operand)
        if node.op == "break":
            raise _Break()
        if node.op == "continue":
            raise _Continue()
        raise TypeError(f"unsupported unary {node.op}")

    # P33.R3 — increment / decrement
    def eval_incdec(self, node: IncDec) -> Any:
        """P33.R3 — evaluate ``i++`` / ``i--`` / ``++i`` / ``--i``.

        The operand must be a mutable binding (Ident) or a
        property-access expression (Member / Index).  For an Ident,
        the binding is looked up in the lexical environment; for
        Member / Index, the property of the evaluated object is
        mutated.  JS semantics: postfix returns the old value,
        prefix returns the new value.
        """
        step = 1 if node.op == "++" else -1
        # Resolve the target binding.
        if isinstance(node.operand, Ident):
            name = node.operand.name
            old = self.env.lookup(name)
            new = old + step if isinstance(old, (int, float)) else 0 + step
            self.env.set(name, new)
            return new if node.prefix else old
        # Member / Index: mutate the property in place.
        if isinstance(node.operand, Member):
            obj = self.eval(node.operand.obj)
            prop = node.operand.prop
            if isinstance(obj, dict):
                old = obj.get(prop, 0)
                new = (old + step) if isinstance(old, (int, float)) else 0 + step
                obj[prop] = new
                return new if node.prefix else old
            return None
        if isinstance(node.operand, Index):
            obj = self.eval(node.operand.obj)
            idx = self.eval(node.operand.index)
            if isinstance(obj, list) and 0 <= idx < len(obj):
                old = obj[idx]
                new = (old + step) if isinstance(old, (int, float)) else 0 + step
                obj[idx] = new
                return new if node.prefix else old
            return None
        raise TypeError("++/-- requires a variable or property access")

    def eval_logical(self, node: Logical) -> Any:
        if node.op == "&&":
            l = self.eval(node.left)
            if not self.truthy(l):
                return l
            return self.eval(node.right)
        if node.op == "||":
            l = self.eval(node.left)
            if self.truthy(l):
                return l
            return self.eval(node.right)
        if node.op == "??":
            l = self.eval(node.left)
            if l is None:
                return self.eval(node.right)
            return l
        raise TypeError(f"unsupported logical operator {node.op}")

    def eval_member(self, node: Member) -> Any:
        obj = self.eval(node.obj)
        if obj is None:
            return None
        prop = node.prop
        # P29.2 — generator objects / toy iterators: .next / .return
        # accessed as a property then called; wrap in a closure so
        # ``it.next()`` routes through call_method.
        if isinstance(obj, ToyGenObject):
            if prop == "next":
                return (lambda *a: obj.next(a[0] if a else None))
            if prop == "return":
                return lambda *a: {"done": True}
            return None
        if isinstance(obj, ToyIterator):
            if prop == "next":
                return lambda *a: obj.next()
            return None
        if isinstance(obj, DomElement):
            v = self.host.eval_element_prop(obj, prop)
            if v is not None:
                return v
            # P30.2 — parentElement / children / tagName are @property;
            # P30.3 — method surface (appendChild, addEventListener, …) is
            # Python methods.  Fall back to getattr for both.
            attr = getattr(obj, prop, None)
            return attr
        if isinstance(obj, dict):
            if "__doc__" in obj:
                fn = self.host.eval_doc_method(obj["__doc__"], prop)
                if fn is not None:
                    return fn
                # P30.1 — document.querySelector (full CSS selector, first match).
                if prop == "querySelector":
                    root = obj["__doc__"]
                    def qs(sel):
                        ns = root.query_selector_all(sel or "")
                        return DomElement(ns[0], self) if ns else None
                    return qs
                return None
            if "__el__" in obj and prop not in ("get", "set", "prop", "__el__"):
                if prop in ("add", "remove", "toggle", "contains"):
                    # P30.3 — classList method calls (obj = classList dict).
                    fn = obj.get(prop)
                    if callable(fn):
                        return lambda *a, _fn=fn: _fn(*a)
                    return None
                if prop == "value":
                    return obj.get("value")
                return obj.get(prop)
            return obj.get(prop)
        if isinstance(obj, list):
            if prop == "length":
                return len(obj)
            return None
        if isinstance(obj, str):
            if prop == "length":
                return len(obj)
            return None
        # P4.2 — arbitrary Python objects (e.g. _ToyDate, regexp dicts,
        # callable objects): return the attribute if it exists, else None.
        attr = getattr(obj, prop, None)
        if attr is not None:
            return attr
        return None

    def eval_assign(self, node: Assign) -> Any:
        value = self.eval(node.value)
        target = node.target
        if isinstance(target, Member):
            # a.b.c = v  where a is a DomElement (or a chain).
            # Walk down the chain to find the base object and the last prop.
            obj = self.eval(target.obj)
            prop = target.prop
            rhs = value
            if node.op == "+=":
                cur = self.eval_member(target)
                rhs = (cur or 0) + value
            elif node.op == "-=":
                cur = self.eval_member(target)
                rhs = (cur or 0) - value
            # Dispatch based on the base object type.
            if isinstance(obj, DomElement):
                self.host.set_element_prop(obj, prop, rhs)
            elif isinstance(obj, dict):
                # If this dict is a style object, route to the element.
                el = obj.get("__el__")
                if el is not None and isinstance(el, DomElement):
                    self.host.set_style_prop(el, prop, rhs)
                else:
                    obj[prop] = rhs
            else:
                pass
            return rhs
        if isinstance(target, Ident):
            rhs = value
            if node.op == "+=":
                cur = self.env.lookup(target.name)
                rhs = (cur or 0) + value
            elif node.op == "-=":
                cur = self.env.lookup(target.name)
                rhs = (cur or 0) - value
            self.env.set(target.name, rhs)
            return rhs
        raise TypeError("unsupported assignment target")

    def eval_if(self, node: If) -> Any:
        if self.truthy(self.eval(node.cond)):
            # P0 — a plain (non-function) then-block must propagate
            # _Return so the caller (eval_block inside a function
            # frame) sees it and exits; the legacy unconditional
            # _Return catch in eval_block swallowed return inside
            # if/else branches, so the last return in the function
            # body always won.  Use eval() for then-branches that
            # are not Block nodes so _Return propagates directly.
            if isinstance(node.then, Block):
                # A then-branch Block evaluated inside a function
                # frame must propagate _Return; use eval_block
                # (which now respects _is_func) so the return
                # escapes the if and reaches the function-level
                # catch in _make_func / _make_arrow.
                return self.eval_block(node.then)
            return self.eval(node.then)
        if node.else_ is not None:
            if isinstance(node.else_, Block):
                return self.eval_block(node.else_)
            return self.eval(node.else_)
        return None

    def eval_return_stmt(self, node) -> Any:
        """P16.2 — a `return` statement inside an if/else branch must
        propagate to the function-level catch.  Raise _Return directly."""
        v = self.eval(node.value) if node.value is not None else None
        raise _Return(v)
        # P16.2 — `return` inside a while body must propagate to the
        # function-level catch; only _Break / _Continue are loop-local.
        try:
            while self.truthy(self.eval(node.cond)):
                try:
                    self.eval_block(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
        except _Return:
            raise
        return None

    # P33.R3 — helper: create a child block-scope env with the
    # ``_is_func`` flag, for statements that need their own lexical
    # frame (loop heads, catch clauses, …).
    def _block_env(self) -> Env:
        e = Env({}, self.env)
        e._is_func = False
        return e

    def eval_while(self, node: While) -> Any:
        # P16.2 — `return` inside a while body must propagate; _Break /
        # _Continue are loop-local.
        try:
            while self.truthy(self.eval(node.cond)):
                try:
                    self.eval_block(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
        except _Return:
            raise
        return None

    def eval_for(self, node: For) -> Any:
        """P33.R3 — classic ``for`` with a block-scoped init frame.

        The init expression (typically a ``let``/``const`` declaration)
        and the loop body share a fresh block scope; the loop counter
        is not visible after the loop ends.  ``var`` inits keep the
        legacy function-level hoisting (no fresh frame for the var
        binding, but the loop body still gets its own block scope).
        """
        old_env = self.env
        loop_env = self._block_env()
        self.env = loop_env
        try:
            if node.init is not None:
                self.eval(node.init)
            while True:
                if node.cond is not None and not self.truthy(self.eval(node.cond)):
                    break
                try:
                    self.eval_block(node.body)
                except _Break:
                    break
                except _Continue:
                    continue
                if node.step is not None:
                    self.eval(node.step)
        except _Return:
            self.env = old_env
            raise
        self.env = old_env
        return None

    def _js_iterable(self, v) -> list:
        """P29.2 — coerce a JS value into a Python iterable of elements.

        Supports: list / str / dict (keys) / ToyGenObject (drives
        ``.next()`` until ``done``) / custom ``@@iterator`` protocol /
        any object with a Python ``__iter__``.
        """
        if v is None:
            return []
        if isinstance(v, ToyGenObject):
            out = []
            while True:
                res = v.next(None)
                if res.get("done"):
                    break
                out.append(res.get("value"))
            return out
        # P29.2 — custom iterator protocol: dict with "@@iterator" key
        # (JS: iterable[Symbol.iterator]()).  The value stored there is a
        # callable that returns a ToyGenObject / ToyIterator.
        if isinstance(v, dict) and SymbolIteratorMarker.KEY in v:
            it_fn = v.get(SymbolIteratorMarker.KEY)
            if callable(it_fn):
                obj = it_fn()
                if isinstance(obj, ToyGenObject):
                    out = []
                    while True:
                        res = obj.next(None)
                        if res.get("done"):
                            break
                        out.append(res.get("value"))
                    return out
                if isinstance(obj, ToyIterator):
                    out = []
                    while True:
                        res = obj.next()
                        if res.get("done"):
                            break
                        out.append(res.get("value"))
                    return out
        if isinstance(v, str):
            return list(v)
        if isinstance(v, dict):
            # For-of over a plain object: iterate keys.
            return [k for k in v.keys() if not k.startswith("__")]
        if isinstance(v, list):
            return v
        # Fall back to Python's __iter__.
        try:
            return list(iter(v))
        except TypeError:
            return []

    def _make_gen_func(self, name, params, body):
        """P29.2 — build a generator function value.

        Calling the returned function returns a :class:`ToyGenObject`
        wrapping a Python generator.  The body is driven recursively by
        a local statement driver: ``YieldExpr`` statements (top-level or
        nested inside while/if/for blocks) suspend the Python generator
        via ``yield`` so that ``for...of`` over the ToyGenObject and
        manual ``.next()`` calls observe every yielded value in
        program order.  Other statements run through the interpreter.

        P33.R3 — the generator uses a *dedicated* :class:`Interpreter`
        instance (sharing the same host) so that its env mutations do
        not interfere with the outer execution context.  The outer
        interpreter's ``env`` attribute is never touched.
        """
        host = self.host

        def _drive_stmt(gen_interp, s):
            if isinstance(s, YieldExpr):
                yield (gen_interp.eval(s.value) if s.value is not None else None)
            elif isinstance(s, While):
                while gen_interp.truthy(gen_interp.eval(s.cond)):
                    yield from _drive_block(gen_interp, s.body)
            elif isinstance(s, For):
                if s.init is not None:
                    gen_interp.eval(s.init)
                while True:
                    if s.cond is not None and not gen_interp.truthy(gen_interp.eval(s.cond)):
                        break
                    yield from _drive_block(gen_interp, s.body)
                    if s.step is not None:
                        gen_interp.eval(s.step)
            elif isinstance(s, ForOf):
                items = gen_interp._js_iterable(gen_interp.eval(s.iterable))
                for item in items:
                    gen_interp.env.set(s.target, item)
                    yield from _drive_block(gen_interp, s.body)
            elif isinstance(s, If):
                if gen_interp.truthy(gen_interp.eval(s.cond)):
                    yield from _drive_block(gen_interp, s.then)
                elif s.else_ is not None:
                    yield from _drive_block(gen_interp, s.else_)
            else:
                gen_interp.eval(s)

        def _drive_block(gen_interp, blk_node):
            if isinstance(blk_node, Block):
                for s in blk_node.stmts:
                    yield from _drive_stmt(gen_interp, s)
            else:
                yield from _drive_stmt(gen_interp, blk_node)

        def gen_driver(*args):
            def py_gen():
                # P33.R3 — dedicated interpreter for the generator body;
                # its env mutations never touch the outer interpreter.
                gen_interp = Interpreter(host)
                gen_interp.env = Env(host.globals, None)
                gen_interp.env._is_func = True  # P33.R3 — generator frame is a function scope
                for i, p in enumerate(params):
                    gen_interp.env.define(p, args[i] if i < len(args) else None)
                try:
                    yield from _drive_block(gen_interp, body)
                except _Return as r:
                    return r.value
            return ToyGenObject(py_gen())

        return gen_driver

    def _make_func(self, name, params, body):
        func_env = Env({}, self.env)
        func_env._is_func = True  # P33.R3 — function-level scope marker
        closure = Interpreter(self.host)
        closure.env = func_env
        # P0 — inherit the step budget from the caller so a deeply
        # recursive JS function cannot silently run past the host's
        # execution cap (a self-recursive function that never returns
        # would otherwise loop forever under the per-Interpreter
        # budget because each closure gets its own counter).
        closure._steps = self._steps
        closure._budget = self._budget
        closure._cancelling = self._cancelling
        closure._max_recursion = self._max_recursion

        def f(*args):
            inner = Env({}, func_env)
            inner._is_func = True  # P33.R3 — the call frame is a function scope
            for p, a in zip(params, args):
                inner.scope[p] = _Binding(a, "var", True)
            # P0 — recursion-depth guard: JS has no stack limit in
            # the toy interpreter, so a non-terminating recursion
            # (function g(){return g()} ; g()) would otherwise raise
            # Python's RecursionError far outside the host's catch
            # list.  Count call frames in the closure and raise
            # _BudgetExceeded at the cap (the host treats it like any
            # other budget exhaustion).
            closure._recursion_depth += 1
            try:
                if closure._recursion_depth > closure._max_recursion:
                    raise _BudgetExceeded(
                        f"JS recursion depth limit exceeded "
                        f"({closure._max_recursion})")
            except _BudgetExceeded:
                closure._recursion_depth -= 1
                raise
            # P29.2 — `arguments`: the actual call-arg list (JS: iterable
            # indexed object).  Needed so `for (let v of arguments)` works.
            inner.define("arguments", list(args))
            saved = closure.env
            closure.env = inner
            try:
                if isinstance(body, Block):
                    # P0 — walk the function body's statements directly
                    # in the function-frame env (inner, _is_func=True)
                    # instead of calling eval_block, which creates a
                    # fresh child env with _is_func=False and silently
                    # swallows _Return (the P0 eval_block catch for
                    # plain blocks made every return inside a
                    # function body get caught by eval_block's
                    # except clause, so the last return in the body
                    # always won).  Walking directly preserves the
                    # legacy _Return propagation: a `return` inside
                    # an if/else/while branch raises _Return, which
                    # propagates up to the function-level catch
                    # below.
                    last_val: Any = None
                    for s in body.stmts:
                        last_val = closure.eval(s)
                    val = last_val
                else:
                    val = closure.eval(body)
            except _Return as r:
                val = r.value
            except RecursionError:
                # P0 — Python's native stack overflow (each JS frame
                # = 2-4 Python frames; the default 1000-frame limit
                # trips at ~250-330 JS levels, which is right at the
                # guard's 256-level cap).  Convert to
                # _BudgetExceeded so the caller sees a uniform API
                # and the host's catch-all cancels the script.
                raise _BudgetExceeded(
                    "JS recursion depth exceeded (Python RecursionError)")
            finally:
                closure.env = saved
                closure._recursion_depth -= 1
            return val

        # P16.2 — `return` inside a class-method body must also propagate
        # to the method-level catch (symmetric with _make_func).
        f.__is_class_method__ = True

        return f

    def _make_arrow(self, params, body, is_block):
        """P14.2 — arrow function factory.

        For expression bodies (is_block=False), the value of the single
        expression is returned implicitly (JS arrow semantics).
        For block bodies, a bare block is evaluated (uses explicit return).
        """
        func_env = Env({}, self.env)
        func_env._is_func = True  # P33.R3 — closure captures the function scope
        closure = Interpreter(self.host)
        closure.env = func_env
        # P0 — inherit the step budget + recursion state from the
        # caller (symmetric with _make_func) so arrows cannot
        # bypass the execution cap.
        closure._steps = self._steps
        closure._budget = self._budget
        closure._cancelling = self._cancelling
        closure._max_recursion = self._max_recursion

        def f(*args):
            inner = Env({}, func_env)
            inner._is_func = True  # P33.R3 — the call frame is a function scope
            for p, a in zip(params, args):
                inner.scope[p] = _Binding(a, "var", True)
            # P0 — recursion-depth guard (symmetric with _make_func).
            closure._recursion_depth += 1
            try:
                if closure._recursion_depth > closure._max_recursion:
                    raise _BudgetExceeded(
                        f"JS recursion depth limit exceeded "
                        f"({closure._max_recursion})")
            except _BudgetExceeded:
                closure._recursion_depth -= 1
                raise
            # P29.2 — arrow `arguments`: same as _make_func.
            inner.define("arguments", list(args))
            saved = closure.env
            closure.env = inner
            try:
                if is_block:
                    # P0 — walk the arrow's block body statements
                    # directly in the function-frame env (symmetric
                    # with _make_func) instead of eval_block, which
                    # creates a fresh child env with _is_func=False
                    # and swallows _Return.
                    last_val: Any = None
                    for s in body.stmts:
                        last_val = closure.eval(s)
                    val = last_val
                else:
                    # Expression body: JS arrow semantics — implicit return.
                    val = closure.eval(body)
            except _Return as r:
                val = r.value
            except RecursionError:
                # P0 — symmetric with _make_func: Python's native
                # stack overflow is converted to _BudgetExceeded so
                # the host's catch-all cancels the script.
                raise _BudgetExceeded(
                    "JS recursion depth exceeded (Python RecursionError)")
            finally:
                closure.env = saved
                closure._recursion_depth -= 1
            return val

        return f


# ---------------------------------------------------------------------------
# DOM bindings
# ---------------------------------------------------------------------------


class DomElement:
    def __init__(self, node: DomNode, host: Optional["Host"] = None):
        self.node = node
        # P30.4 — the owning Host owns the listener registry (elements are
        # re-wrapped frequently, so the registry is keyed by node, not by
        # DomElement identity).
        self._host_ref = host

    @property
    def id(self) -> Optional[str]:
        return self.node.attr("id")

    @property
    def value(self) -> Optional[str]:
        """P28.1 — the element's value: <input>/<textarea> value attribute
        or text content; <select> selected option's value; None otherwise."""
        node = self.node
        tag = (node.tag or "").lower()
        if tag == "textarea":
            return "".join(c.text or "" for c in node.children if not c.is_element())
        if tag == "select":
            for opt in node.children:
                if opt.is_element() and (opt.tag or "").lower() == "option" \
                        and opt.attr("selected") is not None:
                    return opt.attr("value") or (opt.text or "")
            return None
        if tag in ("input", "button"):
            return node.attr("value") or None
        return None

    @property
    def checked(self) -> Optional[bool]:
        """P28.1 — checkbox/radio checked state (None for other controls)."""
        tag = (self.node.tag or "").lower()
        if tag == "input" and (self.node.attr("type") or "").lower() in ("checkbox", "radio"):
            return self.node.attr("checked") is not None
        return None

    @property
    def children(self) -> list["DomElement"]:
        """P30.2 — element children as DomElements (document order)."""
        return [DomElement(c, self._host_ref) for c in self.node.children if c.is_element()]

    @property
    def parentElement(self) -> Optional["DomElement"]:
        """P30.2 — nearest element parent, or None."""
        p = self.node.parent
        while p is not None and not p.is_element():
            p = p.parent
        return DomElement(p, self._host_ref) if p is not None else None

    @property
    def tagName(self) -> str:
        """P30.2 — the element's tag (lowercase)."""
        return (self.node.tag or "").lower()

    # --- P30.2 — node mutation (triggers re-layout on next render) ---------

    def append_child(self, child: "DomElement") -> "DomElement":
        self.node.append_child(child.node)
        return child

    def insert_before(self, child: "DomElement", ref) -> "DomElement":
        ref_node = ref.node if isinstance(ref, DomElement) else ref
        self.node.insert_before(child.node, ref_node)
        return child

    def remove_child(self, child: "DomElement") -> "DomElement":
        self.node.remove_child(child.node)
        return child

    def remove(self) -> None:
        """P30.2 — element.remove(): detach from parent."""
        p = self.node.parent
        if p is not None:
            p.remove_child(self.node)

    def replace_child(self, new_node: "DomElement", old_node: "DomElement") -> "DomElement":
        self.node.replace_child(new_node.node, old_node.node)
        return old_node

    def set_attribute(self, name: str, value: Any) -> None:
        """P30.3 — setAttribute(name, value): stringifies the value
        (None → "") so attr round-trips through the renderer."""
        v = "" if value is None else str(value)
        self.node.set_attr(str(name), v)

    def get_attribute(self, name: str):
        return self.node.attr(str(name))

    @property
    def dataset(self) -> dict:
        """P30.3 — el.dataset: {k: v} for every data-* attribute
        (data-foo-bar → fooBar, per the DOM spec's camel-case mapping)."""
        out: dict = {}
        for k, v in (self.node.attrs or {}).items():
            if k.startswith("data-"):
                raw = k[5:]
                # data-foo-bar → fooBar (JS dataset camelCase mapping).
                parts = raw.split("-")
                key = parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])
                out[key] = v
        return out

    # --- P30.3 — classList --------------------------------------------------

    def _class_list_obj(self) -> dict:
        """Toy ClassList: read-only view over the class attribute with
        add / remove / toggle / contains (JS style returns boolean)."""
        node = self.node

        def classes() -> list:
            return (node.attr("class") or "").split()

        def set_classes(vals: list) -> None:
            node.set_attr("class", " ".join(vals))

        def add(*names):
            cur = list(classes())
            for n in names:
                s = str(n)
                if s not in cur:
                    cur.append(s)
            set_classes(cur)
            return None

        def remove(*names):
            cur = [c for c in classes() if str(c) not in [str(n) for n in names]]
            set_classes(cur)
            return None

        def contains(name) -> bool:
            return str(name) in classes()

        def toggle(name):
            present = contains(name)
            if present:
                remove(name)
            else:
                add(name)
            return not present

        return {"__classlist__": True, "add": add, "remove": remove,
                "contains": contains, "toggle": toggle, "value": classes}

    @property
    def classList(self) -> dict:
        return self._class_list_obj()

    # --- P30.4 — custom events ------------------------------------------------

    def add_event_listener(self, event_name: str, listener) -> None:
        """P30.4 — register a custom-event listener on this element.
        ``listener`` is a JS function (closure); called with the event
        dict when :meth:`dispatch_event` fires the same event name."""
        registry = self._listeners_of()
        registry.setdefault(str(event_name), []).append(listener)

    def _listeners_of(self) -> dict:
        """The Host-owned listener registry for this element's node
        (keyed by node so re-wrapped elements keep the same listeners)."""
        host = self._host_ref
        if host is None:
            return {}
        return host._listeners.setdefault(self.node, {})

    def dispatch_event(self, event_name: str, detail: Any = None) -> bool:
        """P30.4 — fire a custom event synchronously: every listener
        registered for *event_name* on this element is called in
        registration order with an event object
        ``{"type": …, "target": <this element>, "detail": …}``.
        Returns ``True`` (cancelable events are not cancelled in the
        toy engine)."""
        ev = {"type": str(event_name), "target": self, "detail": detail}
        registry = self._listeners_of()
        for fn in list(registry.get(str(event_name), [])):
            try:
                r = fn(ev)
                if r is False:
                    # toy: report cancel but keep calling remaining handlers
                    ev["defaultPrevented"] = True
            except Exception as e:
                if self._host_ref is not None:
                    self._host_ref.log(f"listener error: {e}")
        return True


class Host:
    """Binds the JS engine to a DOM tree + host functions + sandbox rules.

    P0 (GPT report) — the host carries the JS execution budget so the
    Interpreter can read it on construction.  Two knobs:

    * ``_js_step_budget`` (default 2 000 000): the number of
      interpreter ``eval`` steps a single script/event-handler call
      may take before it is cancelled.  A ``while (true) {}`` script
      is caught at the budget instead of hanging the GUI thread.
    * ``_js_max_recursion`` (default 256): the maximum JS call-frame
      depth.  A non-terminating JS recursion (``function g(){return g()}
      ; g()``) raises _BudgetExceeded at the cap instead of Python's
      RecursionError.
    """

    def __init__(self, root: DomNode, log):
        self.root = root
        self.log = log
        self.logs: list[str] = []  # P1.2 — collected log lines
        self.element_table: dict = {}
        # P30.4 — listener registry: element node -> {event_name: [fn, …]}
        self._listeners: dict[DomNode, dict] = {}
        self._build_element_table()
        self._class_registry: dict[str, ToyClass] = {}  # P12.2
        # P0 — JS execution budget knobs (overridable by tests).
        self._js_step_budget: int = 2_000_000
        self._js_max_recursion: int = 256
        self._cancelling: bool = False
        self.globals: dict = {
            "document": self._document_obj(),
            "console": self._console_obj(),
            "setTimeout": self._set_timeout,
            "requestAnimationFrame": self._request_animation_frame,
            "Promise": self._make_promise,
            "fetch": self._fetch,
            # P3.3 — built-in objects
            "Math": self._math_obj(),
            "JSON": self._json_obj(),
            # P4.2 — more built-in objects
            "Date": self._date_obj(),
            "String": self._string_obj(),
            "RegExp": self._regexp_ctor(),
            # P6.3 — Object built-in
            "Object": self._object_obj(),
            # P9.3 — Number built-in
            "Number": self._number_obj(),
            # P10.2 — Map / Set built-ins
            "Map": self._map_ctor(),
            "Set": self._set_ctor(),
            # P10.2 — Array.from / Array.of
            "Array": self._array_obj(),
            # P10.4 — Error built-in
            "Error": self._error_ctor(),
            # P29.2 — Symbol.iterator (toy): for...of checks
            # ``iterable["@@iterator"]()`` before falling back to
            # built-in iteration.
            "Symbol": {"iterator": SymbolIteratorMarker.KEY},
        }
        self._timers: list = []
        self._raf_callbacks: list = []  # P1.2
        self._style_cache: dict = {}
        self._promise_counter = 0
        # P33.P1 — Web API globals (localStorage / WebSocket / navigator /
        # location / history / URL / URLSearchParams).  Imported lazily so
        # that a circular-import problem between js and web_api never fires.
        try:
            import web_api as _web_api
            _web_api.register_web_api(self)
        except Exception:
            pass

    def _interp(self) -> "Interpreter":
        """P12.2 — factory for a fresh Interpreter bound to this Host."""
        i = Interpreter(self)
        i.env = Env(self.globals, None)
        return i

    def _document_obj(self) -> dict:
        return {"__doc__": self.root}

    # P30.1 — full-selector document accessors (reuse css.Selector engine).
    def _doc_query_selector_all(self, selector: str):
        return self.root.query_selector_all(selector)

    def _doc_query_selector(self, selector: str):
        out = self.root.query_selector_all(selector)
        return DomElement(out[0]) if out else None

    def _doc_create_element(self, tag: str) -> "DomElement":
        """P30.3 — document.createElement(tag): a fresh element node,
        not yet attached to the tree (attach via appendChild etc.)."""
        n = DomNode.element(str(tag).lower())
        return DomElement(n)

    def _console_obj(self) -> dict:
        def log(*args):
            parts = []
            for a in args:
                if a is None:
                    parts.append("null")
                elif isinstance(a, bool):
                    parts.append("true" if a else "false")
                elif isinstance(a, (int, float)):
                    # P15.1 — JS-like number formatting: Infinity / NaN / whole floats.
                    import math
                    if isinstance(a, float) and math.isinf(a):
                        parts.append("Infinity" if a > 0 else "-Infinity")
                    elif isinstance(a, float) and math.isnan(a):
                        parts.append("NaN")
                    elif isinstance(a, float) and a.is_integer():
                        # P12.2 — JS semantics: whole floats render without ".0"
                        parts.append(str(int(a)))
                    else:
                        parts.append(str(a))
                else:
                    parts.append(str(a))
            msg = " ".join(parts)
            self.log(msg)
            self.logs.append(msg)  # P1.2 — collect

        # P5.3 — console.assert(cond, msg): log an error if cond is falsy.
        def assert_fn(cond, *msg_args):
            if not cond:
                parts = ["ASSERTION FAILED:"]
                for a in msg_args:
                    parts.append(str(a))
                msg = " ".join(parts)
                self.log(msg)
                self.logs.append(msg)

        return {"log": log, "assert": assert_fn}

    # P3.3 — built-in objects
    def _math_obj(self) -> dict:
        import math as _m
        import random as _rnd

        # P0 (GLM report) — JS Math.round is *half-up* (toward +∞ for
        # .5), not Python's banker's rounding (half-even).  ``2.5``
        # rounds to ``3`` in JS but ``2`` in Python.
        def _js_round(x):
            if x >= 0:
                return _m.floor(x + 0.5)
            return _m.ceil(x - 0.5)

        return {
            "abs": lambda x: abs(x),
            "floor": lambda x: _m.floor(x),
            "ceil": lambda x: _m.ceil(x),
            "round": _js_round,
            "sqrt": lambda x: _m.sqrt(x) if x >= 0 else float("nan"),
            "min": lambda *xs: min(xs) if xs else 0,
            "max": lambda *xs: max(xs) if xs else 0,
            "pow": lambda a, b: _m.pow(a, b),
            "PI": _m.pi,
            "E": _m.e,
            # P29.3 — Math full suite.
            "log": lambda x: _m.log(x) if x > 0 else float("-inf") if x == 0 else float("nan"),
            "log2": lambda x: _m.log2(x) if x > 0 else float("-inf") if x == 0 else float("nan"),
            "log10": lambda x: _m.log10(x) if x > 0 else float("-inf") if x == 0 else float("nan"),
            "log1p": lambda x: _m.log1p(x),
            "exp": lambda x: _m.exp(x),
            "expm1": lambda x: _m.expm1(x),
            "sin": lambda x: _m.sin(x),
            "cos": lambda x: _m.cos(x),
            "tan": lambda x: _m.tan(x),
            "asin": lambda x: _m.asin(x) if -1 <= x <= 1 else float("nan"),
            "acos": lambda x: _m.acos(x) if -1 <= x <= 1 else float("nan"),
            "atan": lambda x: _m.atan(x),
            "atan2": lambda y, x: _m.atan2(y, x),
            "sinh": lambda x: _m.sinh(x),
            "cosh": lambda x: _m.cosh(x),
            "tanh": lambda x: _m.tanh(x),
            "cbrt": lambda x: _m.cbrt(x),
            "hypot": lambda *xs: _m.hypot(*xs),
            "trunc": lambda x: int(x) if _m.isfinite(x) else x,
            "random": _rnd.random,
            "LN2": _m.log(2),
            "LN10": _m.log(10),
            "LOG2E": 1 / _m.log(2),
            "LOG10E": 1 / _m.log(10),
            "SQRT2": _m.sqrt(2),
            "SQRT1_2": _m.sqrt(0.5),
        }

    def _json_obj(self) -> dict:
        """P3.3 / P10.4 — JSON with nested parse (uses stdlib json.loads)."""
        def stringify(val) -> str:
            """Toy JSON.stringify: handles dict, list, str, int/float, bool, None."""
            if val is None:
                return "null"
            if val is True:
                return "true"
            if val is False:
                return "false"
            if isinstance(val, str):
                return '"' + val.replace('"', '\\"') + '"'
            if isinstance(val, (int, float)):
                if isinstance(val, float) and val == int(val):
                    return str(int(val))
                return str(val)
            if isinstance(val, list):
                return "[" + ",".join(stringify(v) for v in val) + "]"
            if isinstance(val, dict):
                parts = []
                for k, v in val.items():
                    parts.append('"' + str(k) + '":' + stringify(v))
                return "{" + ",".join(parts) + "}"
            return "null"

        def parse(text: str):
            """P10.4 — recursive JSON parser: supports nested objects/arrays
            via stdlib json.loads (pure-stdlib, no external deps)."""
            import json as _json
            text = (text or "").strip()
            if not text:
                return None
            try:
                return _json.loads(text)
            except (ValueError, TypeError):
                return None

        return {"stringify": stringify, "parse": parse}

    # P10.4 — Error built-in object
    def _error_ctor(self):
        class ToyError:
            __slots__ = ("_msg", "_name")

            def __init__(self, msg=None):
                self._msg = str(msg) if msg is not None else "Error"
                self._name = "Error"

            @property
            def message(self):
                return self._msg

            @property
            def name(self):
                return self._name

        # P10.4 — TypeError: separate name but same structure.
        class ToyTypeError(ToyError):
            __slots__ = ()

            def __init__(self, msg=None):
                super().__init__(msg)
                self._name = "TypeError"

        return ToyError

    # P10.4 — improved JSON.parse: supports nested arrays/objects
    def _json_obj_v2(self) -> dict:
        """P10.4 — JSON with recursive nested parsing."""
        def stringify(val) -> str:
            if val is None:
                return "null"
            if val is True:
                return "true"
            if val is False:
                return "false"
            if isinstance(val, str):
                return '"' + val.replace('"', '\\"') + '"'
            if isinstance(val, (int, float)):
                if isinstance(val, float) and val == int(val):
                    return str(int(val))
                return str(val)
            if isinstance(val, list):
                return "[" + ",".join(stringify(v) for v in val) + "]"
            if isinstance(val, dict):
                parts = []
                for k, v in val.items():
                    parts.append('"' + str(k) + '":' + stringify(v))
                return "{" + ",".join(parts) + "}"
            return "null"

        def parse(text: str):
            """P10.4 — recursive JSON parser for nested structures."""
            import json as _json
            try:
                return _json.loads(text)
            except (ValueError, TypeError):
                return None

        return {"stringify": stringify, "parse": parse}

    # P4.2 — more built-in objects
    def _date_obj(self) -> dict:
        import datetime as _dt
        import time as _time
        import re as _re

        _re_iso = _re.compile(
            r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)"
        )

        def parse_fn(text):
            """P29.3 — Date.parse toy: ISO-8601 subset or numeric millis.
            Returns ms-since-epoch or None (JS: NaN) on failure."""
            try:
                text = str(text).strip()
                if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
                    return float(text)
                m = _re_iso.search(text)
                if not m:
                    return None
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                hh, mi, ss = int(m.group(4)), int(m.group(5)), int(m.group(6))
                dtv = _dt.datetime(y, mo, d, hh, mi, ss)
                return dtv.timestamp() * 1000.0
            except (ValueError, TypeError):
                return None

        def utc_fn(year, month=0, day=1, h=0, m=0, s=0, ms=0):
            """P29.3 — Date.UTC(year, month?, day?, …) → ms since epoch
            (month is 0-based, matching JS)."""
            try:
                dtv = _dt.datetime(year, month + 1, day, h, m, s)
                return dtv.timestamp() * 1000.0 + ms
            except (ValueError, TypeError):
                return None

        class _ToyDate:
            __slots__ = ("_ts", "_dt")

            def __init__(self, ts=None):
                if ts is None:
                    self._dt = _dt.datetime.now()
                    self._ts = self._dt.timestamp() * 1000.0  # ms since epoch
                else:
                    # JS semantics: a numeric arg is milliseconds since epoch.
                    self._ts = float(ts)
                    self._dt = _dt.datetime.fromtimestamp(self._ts / 1000.0)

            def getTime(self):
                # JS semantics: getTime() returns milliseconds since epoch.
                # If the constructor was called with a numeric argument,
                # treat it as milliseconds (JS), not seconds.
                return self._ts

            def getFullYear(self):
                return self._dt.year

            def getMonth(self):
                return self._dt.month - 1

            def getDate(self):
                return self._dt.day

            def getHours(self):
                return self._dt.hour

            def getMinutes(self):
                return self._dt.minute

            def getSeconds(self):
                return self._dt.second

            def getMilliseconds(self):
                return int((self._ts % 1.0) * 1000.0)

            def toString(self):
                return str(self._dt)

            def toISOString(self):
                return self._dt.isoformat()

        def date_ctor(ts=None):
            """P4.2 — constructor: `new Date()` → a _ToyDate instance."""
            return _ToyDate(ts)

        date_ctor.now = lambda: _ToyDate().getTime()
        # P29.3 — Date.parse / Date.UTC (toy: parse ISO-8601 subset + ms).
        date_ctor.parse = staticmethod(parse_fn)
        date_ctor.UTC = staticmethod(utc_fn)
        return date_ctor

    def _object_obj(self) -> dict:
        """P6.3 — Object.keys / Object.values built-in."""
        def keys(o):
            if isinstance(o, dict):
                # Strip internal keys (those starting with "__")
                return [k for k in o.keys() if not k.startswith("__")]
            return []
        def values(o):
            if isinstance(o, dict):
                return [o[k] for k in o if not k.startswith("__")]
            return []
        # P8.3 — Object.entries: returns [ [key, value], … ] pairs.
        def entries(o):
            if isinstance(o, dict):
                return [[k, o[k]] for k in o if not k.startswith("__")]
            return []
        # P9.3 — Object.assign(target, …sources): shallow-merge source dicts.
        def assign(target, *sources):
            if not isinstance(target, dict):
                target = {}
            for src in sources:
                if isinstance(src, dict):
                    for k, v in src.items():
                        if not k.startswith("__"):
                            target[k] = v
            return target
        return {"keys": keys, "values": values, "entries": entries, "assign": assign}

    # P9.3 — Number built-in
    def _number_obj(self) -> dict:
        import math

        def is_integer(v):
            try:
                f = float(v)
                if math.isinf(f) or math.isnan(f):
                    return False
                return f == int(f)
            except (ValueError, TypeError):
                return False

        def is_nan(v):
            # P15.1 — NaN can be the float value or the string "NaN".
            if isinstance(v, float) and math.isnan(v):
                return True
            if isinstance(v, str) and v == "NaN":
                return True
            return False

        def parse_int(v):
            # P9.3 — parseInt: parse the leading integer of a string.
            import re as _re

            try:
                if isinstance(v, str):
                    m = _re.match(r"\s*[-+]?\d+", v)
                    return int(m.group(0)) if m else None
                f = float(v)
                if math.isinf(f) or math.isnan(f):
                    return None
                return int(f)
            except (ValueError, TypeError):
                return None

        def parse_float(v):
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None

        return {
            "isInteger": is_integer,
            "isNaN": is_nan,
            "parseFloat": parse_float,
            "parseInt": parse_int,
        }

    def _string_obj(self) -> dict:
        def string_ctor(*codes):
            """P4.2 — constructor: `new String(...)` → str."""
            if len(codes) == 1 and isinstance(codes[0], str):
                return codes[0]
            return "".join(chr(int(c)) for c in codes)

        string_ctor.fromCharCode = lambda *codes: "".join(chr(int(c)) for c in codes)
        return string_ctor

    # P10.2 — Array.from / Array.of
    def _array_obj(self) -> dict:
        def array_from(iterable, map_fn=None):
            """P10.2 — Array.from(iterable, mapFn?): convert to array.

            Accepts a list, a string (char array), or a dict (keys list).
            ``map_fn`` is called as ``map_fn(value, index)``.
            """
            if iterable is None:
                out = []
            elif isinstance(iterable, str):
                out = list(iterable)
            elif isinstance(iterable, dict):
                out = list(iterable.keys())
            elif isinstance(iterable, list):
                out = list(iterable)
            else:
                out = [iterable]
            if map_fn is not None and callable(map_fn):
                out = [map_fn(v, i) for i, v in enumerate(out)]
            return out

        def array_of(*items):
            return list(items)

        return {"from": array_from, "of": array_of}

    # P10.2 — Map built-in
    def _map_ctor(self):
        class ToyMap:
            __slots__ = ("_d",)

            def __init__(self, pairs=None):
                self._d = {}
                if pairs:
                    for p in pairs:
                        if isinstance(p, list) and len(p) >= 2:
                            self._d[p[0]] = p[1]

            def set(self, k, v):
                self._d[k] = v
                return self

            def get(self, k):
                return self._d.get(k)

            def has(self, k):
                return k in self._d

            def delete(self, k):
                existed = k in self._d
                self._d.pop(k, None)
                return existed

            @property
            def size(self):
                return len(self._d)

            def keys(self):
                # P29.2 — return a ToyIterator so `for...of map.keys()` works.
                return ToyIterator(list(self._d.keys()))

            def values(self):
                return ToyIterator(list(self._d.values()))

            def entries(self):
                return ToyIterator([[k, v] for k, v in self._d.items()])

            def forEach(self, fn):
                if callable(fn):
                    for k, v in self._d.items():
                        fn(v, k)

            def __iter__(self):
                # P29.2 — Map is iterable over [k, v] pairs (JS semantics).
                return iter([[k, v] for k, v in self._d.items()])

        return ToyMap

    # P10.2 — Set built-in
    def _set_ctor(self):
        class ToySet:
            __slots__ = ("_s",)

            def __init__(self, items=None):
                self._s = set()
                if items:
                    for v in items:
                        self._s.add(v)

            def add(self, v):
                self._s.add(v)
                return self

            def has(self, v):
                return v in self._s

            def delete(self, v):
                existed = v in self._s
                self._s.discard(v)
                return existed

            @property
            def size(self):
                return len(self._s)

            def values(self):
                # P29.2 — ToyIterator so `for...of s.values()` works.
                return ToyIterator(sorted(self._s, key=repr))

            def forEach(self, fn):
                if callable(fn):
                    for v in self._s:
                        fn(v)

            def __iter__(self):
                # P29.2 — Set iterates its values in insertion order.
                return iter(sorted(self._s, key=repr))

        return ToySet

    def _regexp_ctor(self):
        import re as _re

        def ctor(pattern: str, flags: str = ""):
            flags_py = 0
            if "i" in flags:
                flags_py |= _re.IGNORECASE
            if "m" in flags:
                flags_py |= _re.MULTILINE
            if "s" in flags:
                flags_py |= _re.DOTALL
            if "u" in flags or "y" in flags:
                pass  # toy: ignore
            try:
                compiled = _re.compile(pattern, flags_py)
            except _re.error:
                # P29.1 — invalid pattern: return a no-match sentinel.
                return {
                    "__regexp__": True,
                    "pattern": pattern,
                    "flags": flags,
                    "test": lambda s: False,
                    "exec": lambda s: None,
                    "replace": lambda s, r: s,
                    "split": lambda s: [s],
                }

            def test(s: str) -> bool:
                return bool(compiled.search(s or ""))

            def exec_fn(s: str):
                m = compiled.search(s or "")
                if not m:
                    return None
                result = [m.group(0)]
                for i in range(1, m.re.groups + 1):
                    result.append(m.group(i))
                result.append([m.start(), m.end()])
                return result

            def match_fn(s: str):
                """P29.1 — String.prototype.match equivalent on a regex:
                global flag → all matches, else first match with groups."""
                s = s or ""
                if "g" in flags:
                    return [mm.group(0) for mm in compiled.finditer(s)]
                m = compiled.search(s)
                if not m:
                    return None
                result = [m.group(0)]
                for i in range(1, m.re.groups + 1):
                    result.append(m.group(i))
                return result

            def replace_fn(s: str, repl):
                # P29.1 — support function replacement + $& / $' / $` refs.
                if callable(repl):
                    def _repl_fn(m):
                        out = repl(m.group(0), m.start())
                        return str(out) if out is not None else str(out)
                    return compiled.sub(_repl_fn, s or "")
                if isinstance(repl, str) and any(tok in repl for tok in ("$&", "$`", "$'")):
                    def _ref_repl(m):
                        out = repl
                        out = out.replace("$&", m.group(0))
                        out = out.replace("$`", s[:m.start()])
                        out = out.replace("$'", s[m.end():])
                        return out
                    return compiled.sub(_ref_repl, s or "")
                return compiled.sub(str(repl), s or "")

            def split_fn(s: str, limit=0):
                s = s or ""
                parts = compiled.split(s)
                if limit:
                    parts = parts[: int(limit)]
                return [p for p in parts]

            # P4.2 — a dict so that `r.test(...)` routes through call_method.
            # P29.1 — extended: match / lastIndex / source.
            return {
                "__regexp__": True,
                "pattern": pattern,
                "flags": flags,
                "source": pattern,
                "lastIndex": 0,
                "test": test,
                "exec": exec_fn,
                "match": match_fn,
                "replace": replace_fn,
                "split": split_fn,
            }

        return ctor

    def _build_element_table(self):
        self.element_table = {}

        def walk(n: DomNode):
            if n.is_element():
                eid = n.attr("id")
                if eid:
                    self.element_table[eid] = DomElement(n, self)
                tag = (n.tag or "").lower()
                if tag == "iframe":
                    src = n.attr("src") or ""
                    remote = src.startswith("http://") or src.startswith("https://")
                    if remote:
                        return
            for c in n.children:
                walk(c)

        walk(self.root)

    def _set_timeout(self, code: str, delay: Any = 0):
        self._timers.append((code, 0))

    # P1.2 — requestAnimationFrame (toy: enqueue callback, flushed after scripts)
    def _request_animation_frame(self, cb):
        self._raf_callbacks.append(cb)
        return len(self._raf_callbacks)

    def flush_raf(self) -> None:
        """Execute queued rAF callbacks in FIFO order (toy: one pass)."""
        cbs = list(self._raf_callbacks)
        self._raf_callbacks.clear()
        for cb in cbs:
            try:
                if callable(cb):
                    cb(0)  # timestamp arg is 0 in the toy engine
            except (NameError, TypeError, ZeroDivisionError) as e:
                self.log(f"rAF error: {e}")

    # P1.2 — Promise (toy: resolves/rejects synchronously via .then)
    def _make_promise(self, executor=None):
        """Return a dict acting as a toy Promise with .then()/.catch()/.resolve()."""
        self._promise_counter += 1
        p_id = self._promise_counter
        state = {"resolved": None, "settled": False, "then_cbs": []}

        def _resolve(val):
            if state["settled"]:
                return
            state["resolved"] = val
            state["settled"] = True
            for cb in list(state["then_cbs"]):
                cb(True, val)
            state["then_cbs"].clear()

        def _reject(err):
            if state["settled"]:
                return
            state["resolved"] = err
            state["settled"] = True
            for cb in list(state["then_cbs"]):
                cb(False, err)
            state["then_cbs"].clear()

        def _log_val(r):
            """Convert a Python value to a JS-style log string."""
            if r is True:
                return "true"
            if r is False:
                return "false"
            if r is None:
                return "null"
            if isinstance(r, float) and r == int(r):
                return str(int(r))
            return str(r)

        def then(on_fulfilled=None, on_rejected=None):
            """Return a new promise; chainable. If settled, run handler now."""
            def _handler(settled_ok, val):
                if settled_ok:
                    handler = on_fulfilled if callable(on_fulfilled) else (lambda v: v)
                    r = handler(val)
                    if isinstance(r, dict) and r.get("__promise__"):
                        r["__then__"](on_rejected)
                    elif r is not None:
                        self.log(_log_val(r))
                else:
                    if callable(on_rejected):
                        on_rejected(val)
                    else:
                        self.log(f"unhandled rejection: {val}")

            if state["settled"]:
                _handler(True, state["resolved"])
            else:
                state["then_cbs"].append(_handler)
            return self._make_promise()

        def catch(on_rejected=None):
            return then(None, on_rejected)

        p = {
            "__promise__": True,
            "__then__": then,
            "__catch__": catch,
            "__resolve__": _resolve,
            "__reject__": _reject,
            "__id__": p_id,
        }

        if callable(executor):
            try:
                executor(_resolve, _reject)
            except (NameError, TypeError, ZeroDivisionError, AttributeError) as e:
                self.log(f"Promise executor error: {e}")
        return p

    def _fetch(self, url: str) -> str:
        u = (url or "").lower()
        if not (u.startswith("http://") or u.startswith("https://")):
            self.log(f"fetch blocked: {url!r} (sandbox: http/https only)")
            return ""
        return ""

    def eval_doc_method(self, doc, method: str):
        if method == "getElementById":
            def get_by_id(id_str: str):
                el = self.element_table.get(id_str)
                return el if el else None
            return get_by_id
        if method == "querySelectorAll":
            def qsa(sel: str):
                # P30.1 — full CSS selector engine (css.Selector).
                return [DomElement(n, self) for n in doc.query_selector_all(sel)]
            return qsa
        # P30.1 — document.querySelector (full CSS selector, first match).
        if method == "querySelector":
            def qs(sel: str):
                nodes = doc.query_selector_all(sel)
                return DomElement(nodes[0], self) if nodes else None
            return qs
        # P30.3 — document.createElement(tag) → Detached element wrapper.
        if method == "createElement":
            def ce(tag: str):
                n = DomNode.element(str(tag).lower())
                return DomElement(n, self)
            return ce
        return None

    def call_method(self, obj, method: str, args: list):
        if obj is None:
            return None
        if isinstance(obj, (ToyGenObject, ToyIterator)):
            # P29.2 — generator objects + toy iterators: route .next / .return.
            if method == "next":
                return obj.next(args[0] if args else None)
            if method == "return":
                return {"done": True}
            return None
        if isinstance(obj, DomElement):
            # P30.2/P30.3/P30.4 — element method surface: legacy scroll
            # logging first, then the full Python method set
            # (appendChild / removeChild / insertBefore / replaceChild /
            # addEventListener / dispatchEvent / setAttribute / getAttribute /
            # classList methods / remove …).
            r = self.eval_element_method(obj, method, args)
            if r is not None:
                return r
            # P30.2 — appendChild / removeChild / insertBefore / replaceChild
            # / setAttribute / getAttribute / addEventListener / dispatchEvent
            # / remove — mutate the underlying DOM node directly.
            if method == "appendChild" and args:
                child = args[0]
                if isinstance(child, DomElement):
                    obj.append_child(child)
                    return child
                return None
            if method == "removeChild" and args:
                child = args[0]
                if isinstance(child, DomElement):
                    removed = obj.remove_child(child)
                    return removed
                return None
            if method == "insertBefore" and len(args) >= 1:
                child = args[0]
                ref = args[1] if len(args) > 1 else None
                if isinstance(child, DomElement):
                    return obj.insert_before(child, ref)
                return None
            if method == "replaceChild" and len(args) >= 2:
                new_node, old_node = args[0], args[1]
                if isinstance(new_node, DomElement) and isinstance(old_node, DomElement):
                    return obj.replace_child(new_node, old_node)
                return None
            if method == "setAttribute" and len(args) >= 2:
                obj.set_attribute(args[0], args[1])
                return None
            if method == "getAttribute" and args:
                return obj.get_attribute(args[0])
            if method == "addEventListener" and len(args) >= 2:
                obj.add_event_listener(args[0], args[1])
                return None
            if method == "dispatchEvent" and args:
                detail = args[1] if len(args) > 1 else None
                return obj.dispatch_event(args[0], detail)
            if method == "remove":
                obj.remove()
                return None
            # classList add/remove/toggle/contains live on the classList
            # dict, not the element — but `el.classList.add(...)` reaches us
            # here only when someone called .add on the element itself;
            # delegate to the classList view for convenience.
            if method in ("add", "remove", "toggle", "contains") and hasattr(obj, "_class_list_obj"):
                fn = obj._class_list_obj().get(method)
                if callable(fn):
                    try:
                        out = fn(*args)
                        return out
                    except Exception as e:
                        self.host.log(f"method error: {method}: {e}")
            return None
        if isinstance(obj, dict):
            # P4.2 — regexp dict: route .test / .exec / .replace / .split
            # through closures.
            if obj.get("__regexp__"):
                fn = obj.get(method)
                if callable(fn):
                    return fn(*args)
                return None
            # P1.2 — promise dict: route .then / .catch through the closure.
            if obj.get("__promise__"):
                if method == "then":
                    return obj["__then__"](*args) if args else obj["__then__"]
                if method == "catch":
                    return obj["__catch__"](*args) if args else obj["__catch__"]
            if "__doc__" in obj:
                fn = self.eval_doc_method(obj["__doc__"], method)
                if fn is None:
                    return None
                return fn(*args) if args else fn
            val = obj.get(method)
            if callable(val):
                return val(*args)
            return None
        # P4.2 — generic object method dispatch: for Python objects like
        # _ToyDate or any non-standard type, try getattr(obj, method).
        # P10.2 — ToyMap / ToySet classes route .size / .get / .set /
        # .has / .add / .delete / .keys / .values / .entries / .forEach.
        if not isinstance(obj, (list, str, int, float, bool)):
            attr = getattr(obj, method, None)
            if callable(attr):
                try:
                    return attr(*args)
                except Exception as e:
                    self.host.log(f"method error: {method}: {e}")
                    return None
            # P10.2 — non-callable properties on ToyMap / ToySet.
            if method == "size":
                # Map / Set store size via @property → int.
                return attr if attr is not None else 0
            return None
        if isinstance(obj, list):
            if method == "push":
                for a in args:
                    obj.append(a)
                return len(obj)
            # P10.4 — Array.toString: comma-join like JS Array.toString.
            # Nested arrays are flattened (JS-style: sub-array toString
            # results concatenated with the separator).
            if method == "toString":
                def _js_num(v):
                    if isinstance(v, float) and v == int(v):
                        return str(int(v))
                    return str(v)
                _sep = str(args[0]) if args else ","
                def _flat_str(v):
                    if isinstance(v, list):
                        return _sep.join(_flat_str(x) for x in v)
                    return _js_num(v)
                return _sep.join(_flat_str(v) for v in obj)
            if method == "join":
                def _js_num(v):
                    # P5.3 — JS-style number formatting (whole floats no decimal)
                    if isinstance(v, float) and v == int(v):
                        return str(int(v))
                    return str(v)
                sep = str(args[0]) if args else ","
                return sep.join(_js_num(v) for v in obj)
            if method == "length":
                return len(obj)
            if method == "slice":
                start = args[0] if len(args) > 0 else 0
                end = args[1] if len(args) > 1 else len(obj)
                return obj[start:end]
            if method == "indexOf":
                if args and args[0] in obj:
                    return obj.index(args[0])
                return -1
            if method == "includes":
                if args and args[0] in obj:
                    return True
                return False
            # P5.3 — Array higher-order + join
            # P9.3 — map / filter / forEach receive (value, index) args.
            if method == "map":
                if args and callable(args[0]):
                    return [args[0](v, i) for i, v in enumerate(obj)]
                return obj
            if method == "filter":
                if args and callable(args[0]):
                    return [v for i, v in enumerate(obj) if args[0](v, i)]
                return obj
            if method == "forEach":
                if args and callable(args[0]):
                    for i, v in enumerate(obj):
                        args[0](v, i)
                return None
            if method == "join":
                def _js_num(v):
                    # P5.3 — JS-style number formatting (whole floats no decimal)
                    if isinstance(v, float) and v == int(v):
                        return str(int(v))
                    return str(v)
                sep = str(args[0]) if args else ","
                return sep.join(_js_num(v) for v in obj)
            if method == "reverse":
                obj.reverse()
                return obj
            if method == "concat":
                out = list(obj)
                for a in args:
                    if isinstance(a, (list, tuple)):
                        out.extend(a)
                    else:
                        out.append(a)
                return out
            # P6.3 — Array.reduce
            if method == "reduce":
                if not args or not callable(args[0]):
                    return obj[0] if obj else None
                fn = args[0]
                if len(args) >= 2:
                    acc = args[1]
                else:
                    if not obj:
                        return None
                    acc = obj[0]
                    obj = obj[1:]
                for v in obj:
                    acc = fn(acc, v)
                return acc
            # P7.3 — Array.find / findIndex / some / every
            if method == "find":
                if args and callable(args[0]):
                    for v in obj:
                        if args[0](v):
                            return v
                return None
            if method == "findIndex":
                if args and callable(args[0]):
                    for i, v in enumerate(obj):
                        if args[0](v):
                            return i
                return -1
            if method == "some":
                if args and callable(args[0]):
                    return any(args[0](v) for v in obj)
                return False
            if method == "every":
                if args and callable(args[0]):
                    return all(args[0](v) for v in obj)
                return True
            # P8.3 — Array.sort / includes
            if method == "sort":
                # Without a comparator: sort by numeric value if all numbers,
                # otherwise by string representation.
                try:
                    return sorted(obj, key=lambda v: (isinstance(v, bool), v if not isinstance(v, bool) else 0))
                except TypeError:
                    return sorted(obj, key=lambda v: str(v))
            if method == "includes":
                if args:
                    target = args[0]
                    for v in obj:
                        if v == target:
                            return True
                return False
            # P9.3 — Array.flat / flatMap
            if method == "flat":
                depth = 1
                if args:
                    a0 = args[0]
                    if isinstance(a0, float) and a0 == float("inf"):
                        # flat(Infinity): flatten recursively to the bottom.
                        out = list(obj)
                        changed = True
                        while changed:
                            flat = []
                            changed = False
                            for v in out:
                                if isinstance(v, list):
                                    flat.extend(v)
                                    changed = True
                                else:
                                    flat.append(v)
                            out = flat
                        return out
                    depth = int(a0)
                out = list(obj)
                for _ in range(max(0, depth)):
                    flat = []
                    for v in out:
                        if isinstance(v, list):
                            flat.extend(v)
                        else:
                            flat.append(v)
                    out = flat
                return out
            if method == "flatMap":
                if not args or not callable(args[0]):
                    return list(obj)
                fn = args[0]
                out = []
                for v in obj:
                    r = fn(v)
                    if isinstance(r, list):
                        out.extend(r)
                    else:
                        out.append(r)
                return out
            # P15.1 — Array.at(index)
            if method == "at":
                if args:
                    idx = int(args[0])
                    if idx < 0:
                        idx += len(obj)
                    if 0 <= idx < len(obj):
                        return obj[idx]
                return None
            # P15.1 — Array.flat with Infinity depth
            if method == "flat_deep":
                out = list(obj)
                changed = True
                while changed:
                    flat = []
                    changed = False
                    for v in out:
                        if isinstance(v, list):
                            flat.extend(v)
                            changed = True
                        else:
                            flat.append(v)
                    out = flat
                return out
            # P15.1 — flat(Infinity) support (already handled above via float("inf"))
            # P29.3 — Array tail: fill / findLast / toReversed / entries.
            if method == "fill":
                # arr.fill(v, start?, end?) — in-place.
                v = args[0] if args else None
                start = int(args[1]) if len(args) > 1 else 0
                end = int(args[2]) if len(args) > 2 else len(obj)
                start = max(0, start)
                end = min(len(obj), end) if end >= 0 else max(0, len(obj) + end)
                for i in range(start, end):
                    obj[i] = v
                return obj
            if method == "findLast":
                if args and callable(args[0]):
                    for i in range(len(obj) - 1, -1, -1):
                        if args[0](obj[i], i):
                            return obj[i]
                return None
            if method == "findLastIndex":
                if args and callable(args[0]):
                    for i in range(len(obj) - 1, -1, -1):
                        if args[0](obj[i], i):
                            return i
                return -1
            if method == "toReversed":
                return list(reversed(obj))
            if method == "toSorted":
                fn = args[0] if args and callable(args[0]) else None
                out = list(obj)
                if fn is not None:
                    try:
                        out = sorted(out, key=fn)
                    except TypeError:
                        out = sorted(out, key=str)
                else:
                    try:
                        out = sorted(out)
                    except TypeError:
                        out = sorted(out, key=str)
                return out
            if method == "entries":
                return [[i, v] for i, v in enumerate(obj)]
            if method == "keys":
                return list(range(len(obj)))
            if method == "values":
                return list(obj)
            return None
        if isinstance(obj, str):
            if method == "includes":
                if args:
                    return str(args[0]) in obj
                return False
            if method == "indexOf":
                if args:
                    idx = obj.find(str(args[0]))
                    return idx if idx >= 0 else -1
                return -1
            if method == "length":
                return len(obj)
            if method == "split":
                sep = str(args[0]) if args else ""
                return obj.split(sep)
            if method == "toLowerCase":
                return obj.lower()
            if method == "toUpperCase":
                return obj.upper()
            if method == "trim":
                return obj.strip()
            # P9.3 — String.trimStart / trimEnd
            if method == "trimStart":
                return obj.lstrip()
            if method == "trimEnd":
                return obj.rstrip()
            if method == "replace":
                # P29.1 — regex-aware replace: when the target is a regexp
                # dict, route through the regexp's replace closure;
                # otherwise plain string replace.
                if len(args) >= 2:
                    target = args[0]
                    if isinstance(target, dict) and target.get("__regexp__"):
                        fn = target.get("replace")
                        if callable(fn):
                            return fn(obj, args[1])
                    return obj.replace(str(target), str(args[1]))
                return obj
            # P4.2 — string char methods
            if method == "charCodeAt":
                if args:
                    idx = int(args[0])
                    if 0 <= idx < len(obj):
                        return ord(obj[idx])
                return -1
            if method == "charAt":
                if args:
                    idx = int(args[0])
                    if 0 <= idx < len(obj):
                        return obj[idx]
                return ""
            if method == "substring":
                if len(args) >= 1:
                    start = max(0, int(args[0]))
                    end = len(obj) if len(args) < 2 else min(len(obj), int(args[1]))
                    return obj[start:end]
                return obj
            # P6.3 — string padding / repeat
            if method == "repeat":
                if args:
                    n = max(0, int(args[0]))
                    return obj * n
                return ""
            if method == "padStart":
                target_len = int(args[0]) if args else 0
                fill = str(args[1]) if len(args) > 1 else " "
                if len(obj) >= target_len:
                    return obj
                pad_needed = target_len - len(obj)
                pad = (fill * (pad_needed // len(fill) + 1))[:pad_needed] if fill else ""
                return pad + obj
            if method == "padEnd":
                target_len = int(args[0]) if args else 0
                fill = str(args[1]) if len(args) > 1 else " "
                if len(obj) >= target_len:
                    return obj
                pad_needed = target_len - len(obj)
                pad = (fill * (pad_needed // len(fill) + 1))[:pad_needed] if fill else ""
                return obj + pad
            # P8.3 — String.startsWith / endsWith
            if method == "startsWith":
                if args:
                    return obj.startswith(str(args[0]))
                return False
            if method == "endsWith":
                if args:
                    return obj.endswith(str(args[0]))
                return False
            # P10.2 — String.replaceAll
            if method == "replaceAll":
                if len(args) >= 2:
                    target = args[0]
                    if isinstance(target, dict) and target.get("__regexp__"):
                        fn = target.get("replace")
                        if callable(fn):
                            return fn(obj, args[1])
                    return obj.replace(str(target), str(args[1]))
                return obj
            # P29.1 — String.match(regex): global → all matches,
            # else first match with capture groups.
            if method == "match":
                if args and args[0] and isinstance(args[0], dict) and args[0].get("__regexp__"):
                    fn = args[0].get("match")
                    if callable(fn):
                        return fn(obj)
                return None
            # P29.3 — String tail: at / codePointAt / normalize.
            if method == "at":
                if args:
                    idx = int(args[0])
                    if idx < 0:
                        idx += len(obj)
                    if 0 <= idx < len(obj):
                        return obj[idx]
                return None
            if method == "codePointAt":
                if args:
                    idx = int(args[0])
                    if 0 <= idx < len(obj):
                        return ord(obj[idx])
                return None
            return None

    def eval_element_prop(self, el: DomElement, prop: str):
        if prop == "id":
            return el.id
        if prop in ("textContent", "innerText", "innerHTML"):
            # P30.2 — textContent reflects the live tree, so re-wrap the
            # element to drop any stale view and read fresh.
            return el.node.text_content()
        if prop == "style":
            eid = el.id or f"__anon_{id(el.node)}"
            if eid not in self._style_cache:
                self._style_cache[eid] = self._style_obj(el)
            return self._style_cache[eid]
        if prop == "src":
            return el.node.attr("src")
        # P13.4 — input element value
        # P28.1 — <select> value: the selected option's value; <input> /
        # <textarea> / <button>: the value attribute (or text content).
        if prop == "value":
            tag = (el.node.tag or "").lower()
            if tag == "select":
                for opt in el.node.children:
                    if opt.is_element() and (opt.tag or "").lower() == "option" \
                            and opt.attr("selected") is not None:
                        return opt.attr("value") or (opt.text or "")
                return None
            if tag == "textarea":
                return el.node.text_content()
            return el.node.attr("value")
        # P14.1 — scroll offsets (toy: read from the layout box if available)
        if prop in ("scrollTop", "scrollLeft"):
            box = self._layout_box_for(el)
            if box is None:
                return 0.0
            return box.scroll_top if prop == "scrollTop" else box.scroll_left
        # P14.1 — offset dimensions
        if prop in ("offsetWidth", "offsetHeight"):
            box = self._layout_box_for(el)
            if box is None:
                return 0.0
            return box.rect.w if prop == "offsetWidth" else box.rect.h
        # P14.1 — overflow style
        if prop == "overflow":
            style = el.node.attr("style") or ""
            import re as _re2
            m = _re2.search(r"(?:^|;)\s*overflow\s*:\s*([^;]+)", style)
            if m:
                return m.group(1).strip()
            return "visible"
        # P15.3 — box-decoration-break / -webkit-appearance
        if prop in ("boxDecorationBreak", "box_decoration_break"):
            style = el.node.attr("style") or ""
            import re as _re3
            m = _re3.search(r"(?:^|;)\s*box-decoration-break\s*:\s*([^;]+)", style)
            return m.group(1).strip() if m else ""
        if prop in ("webkitAppearance", "appearance"):
            style = el.node.attr("style") or ""
            import re as _re4
            m = _re4.search(r"(?:^|;)\s*-webkit-appearance\s*:\s*([^;]+)", style)
            return m.group(1).strip() if m else "auto"
        # P30.3 — classList: dict with add / remove / contains / toggle /
        # value, so `el.classList.contains('x')` routes through call_method
        # (dict-dispatch) with method == "contains".
        if prop == "classList":
            return el._class_list_obj()
        # P30.3 — element dataset: {k: v} for every data-* attribute.
        if prop == "dataset":
            return el.dataset
        # P30.2 — structural element properties (getattr fallback in
        # eval_member covers these; returned here so assignment checks stay
        # uniform).
        if prop in ("children", "parentElement", "tagName"):
            return getattr(el, prop, None)
        return None

    def _layout_box_for(self, el: "DomElement"):
        """P14.1 — lazily compute a LayoutBox for this element (cached).

        P34.1 — the layout runs *once* and the whole box tree is kept;
        the per-id lookup walks the tree (bounded by the box count, not
        re-running css.apply + layout on every cache miss as the P14
        implementation did, which was O(n²) in the number of queried
        elements)."""
        eid = el.id
        if eid is None:
            return None
        tree = getattr(self, "_layout_root_cache", None)
        if tree is None:
            try:
                import layout as _layout_mod
                import css as _css_mod
                _styled = _css_mod.apply(self.root, "")
                tree = _layout_mod.layout(_styled, 800.0, 600.0)
                self._layout_root_cache = tree
            except Exception:
                tree = None
            return None
        return self._find_box_by_id(tree, eid)

    def _find_box_by_id(self, box, target_id: str):
        """BFS id lookup over the layout tree (P34.1)."""
        stack = [box]
        while stack:
            bx = stack.pop()
            if bx.dom is not None and bx.dom.is_element() and bx.dom.attr("id") == target_id:
                return bx
            stack.extend(reversed(bx.children))
        return None

    def eval_element_method(self, el: DomElement, method: str, args: list):
        # P15.2 — scrollIntoView() / scrollBy()
        if method == "scrollIntoView":
            # Toy: log the scroll target (no real scroll animation in headless).
            eid = el.id or "?"
            self.log(f"scrollIntoView: #{eid}")
            return None
        if method == "scrollBy":
            dx = float(args[0]) if len(args) > 0 else 0.0
            dy = float(args[1]) if len(args) > 1 else 0.0
            self.log(f"scrollBy: dx={dx} dy={dy}")
            return None
        # P15.2 — scrollIntoView / scrollBy on scroll containers.
        return None

    def _style_obj(self, el: DomElement) -> dict:
        node = el.node
        import re as _re

        def get(prop: str) -> str:
            style = node.attr("style") or ""
            m = _re.search(
                rf"(?:^|;)\s*{_re.escape(prop)}\s*:\s*([^;]+)", style, _re.IGNORECASE
            )
            if m:
                return m.group(1).strip()
            return ""

        def set(prop: str, value: str):
            style = node.attr("style") or ""
            pairs = {}
            for part in style.split(";"):
                if ":" in part:
                    k, _, v = part.partition(":")
                    pairs[k.strip().lower()] = v.strip()
            pairs[prop] = str(value)
            node.set_attr("style", "; ".join(f"{k}: {v}" for k, v in pairs.items()))

        return {"get": get, "set": set, "prop": "style", "__el__": el}

    def set_style_prop(self, el: DomElement, prop: str, value: Any):
        self._style_obj(el)["set"](prop, value)

    def set_element_prop(self, el: DomElement, prop: str, value: Any):
        node = el.node
        if prop in ("textContent", "innerText", "innerHTML"):
            node.children = [DomNode.text_node(str(value))]
        elif prop == "style":
            for k, v in value.items():
                if k in ("get", "set", "prop", "__el__"):
                    continue
                self._style_obj(el)["set"](k, v)
        elif prop == "src":
            self._set_src(el, value)
        elif prop == "value":
            # P13.4 — set the <input> value attribute.
            node.set_attr("value", str(value))
        elif prop == "className":
            # P30.3 — el.className = "a b" writes the class attribute.
            node.set_attr("class", str(value))
        elif prop in ("children", "parentElement", "tagName"):
            # P30.2 — read-only structural properties: assignment is a no-op.
            return
        else:
            node.set_attr(prop, str(value))

    def _set_src(self, el: DomElement, value: str):
        src = (value or "").lower()
        if src.startswith("http://") or src.startswith("https://"):
            self.log(f"iframe.src blocked: {value!r} (cross-origin)")
            return
        el.node.set_attr("src", value)

    # --- execution entry points ---

    def execute_all(self):
        scripts = self._collect_scripts(self.root)
        for s in scripts:
            self.execute(s)
        self.flush_timers()
        self.flush_raf()  # P1.2

    def execute_event(self, element_id: str, event_name: str = "onclick"):
        """P11.4 — run an element's event handler with `this` bound to the
        element.  ``event_name`` selects which attribute to read (default
        ``onclick``); P13.4 extends it to ``oninput`` / ``onfocus``.

        If the named handler is not set, P13.4 falls back to event
        delegation: the handler is looked up on the nearest ancestor with
        the same ``event_name`` attribute set (toy semantics — no real
        bubbling, just a direct parent chain walk).
        """
        el = self.element_table.get(element_id)
        if el is None:
            return
        handler = el.node.attr(event_name)
        if not handler:
            # P13.4 — event delegation: walk the DOM parent chain looking
            # for an ancestor that has the handler attribute.
            node = el.node.parent
            while node is not None:
                ah = node.attr(event_name)
                if ah:
                    handler = ah
                    # `this` still binds to the *target* element, not the
                    # delegation container, so that `this.value` works.
                    break
                node = node.parent
        if handler:
            program = parse_program(handler)
            interpreter = Interpreter(self)
            interpreter.env = Env(self.globals, None)
            # P11.4 — bind `this` to the target element so `this.style…` works.
            interpreter.this_value = el
            # P0 — the event handler runs under the same execution
            # budget as a script; a malicious or buggy on* handler
            # must not be able to hang the GUI thread.
            try:
                interpreter.eval_block(program)
            except _BudgetExceeded as e:
                self.log(f"JS budget (event handler): {e}")

    # P11.4 — public entry point used by browser.event_target /
    # click_and_render: sets this and runs the handler.
    def execute_event_with_this(self, element_id: str):
        self.execute_event(element_id)

    def _collect_scripts(self, root: DomNode) -> list[str]:
        out: list[str] = []

        def walk(n: DomNode):
            if n.is_element() and (n.tag or "").lower() == "script":
                txt = n.text_content()
                if txt.strip():
                    out.append(txt)
            for c in n.children:
                walk(c)

        walk(root)
        return out

    def execute(self, code: str) -> None:
        try:
            program = parse_program(code)
            interpreter = Interpreter(self)
            interpreter.env = Env(self.globals, None)
            interpreter.env._is_func = True  # P33.R3 — top level = function scope
            interpreter.eval_block(program)
        except _Return:
            pass
        except _Throw as exc:
            # P11.2 — uncaught throw: log the thrown value, don't crash.
            self.log(f"uncaught throw: {exc.value}")
        except _BudgetExceeded as exc:
            # P0 (GPT report) — a script that never terminates, or
            # that exceeds the per-call step budget, must not hang
            # the GUI thread.  Log the cancellation and move on to
            # the next script (the rest of the page still renders).
            self.log(f"JS budget exceeded: {exc}")
        except RecursionError as e:
            # P0 — the JS recursion guard catches most runaway
            # recursion at the _max_recursion cap, but Python's
            # native RecursionError can still fire when the guard's
            # own try/except stack is deep enough (a pathological
            # case).  Log and recover instead of crashing the host.
            self.log(f"JS recursion error: {e}")
        except (NameError, TypeError, SyntaxError, ZeroDivisionError) as e:
            self.log(f"JS error: {e}")

    def eval(self, code: str):
        """P9.3 — Evaluate a JS expression and return the result value.

        P33.R3 — errors (TDZError, RuntimeError from const re-assign,
        NameError, TypeError, SyntaxError, ZeroDivisionError)
        propagate to the caller so block-scope / TDZ / const
        tests can assert on them.  The *host* ``execute`` path keeps
        its legacy log-and-swell behavior.

        P0 — a _BudgetExceeded (recursion-depth or step-budget
        exhaustion) propagates to the caller so tests can assert on
        it; the legacy eval() would have hung the process forever
        on a non-terminating expression.
        """
        interpreter = Interpreter(self)
        interpreter.env = Env(self.globals, None)
        interpreter.env._is_func = True  # P33.R3
        # P33.R3 — capture the last statement's value: wrap the code
        # in a function that returns the result of the last
        # expression, then call it.  The legacy semantics are
        # preserved (the last expression's value is the return).
        # P0 — the P9.3 IIFE wrapper silently returned ``undefined``
        # for bare-expression input (``eval("add(3,4)")`` → None)
        # because a JS function does not implicitly return its last
        # expression.  Splice a trailing ``return <last-expr>;`` into
        # the wrapper body when the input does not already end with
        # a return statement; the legacy behaviour is unchanged for
        # code that ends with a return.  The splice is safe because
        # ``eval_block`` returns the last statement's value (P0 fix
        # above), so the IIFE's implicit return surfaces it.
        last_expr = code.strip().rsplit(";", 1)[-1].strip()
        if last_expr and not last_expr.startswith("return") \
                and not last_expr.startswith("}"):
            wrapped = f"((function() {{ {code} return {last_expr}; }}))()"
        else:
            wrapped = f"((function() {{ {code} }}))()"
        program = parse_program(wrapped)
        try:
            result = interpreter.eval_block(program)
        except _Return as r:
            return r.value
        except _Throw as exc:
            self.log(f"uncaught throw: {exc.value}")
            raise
        except TDZError:
            raise
        except RuntimeError:
            raise
        except _BudgetExceeded:
            # P0 — a non-terminating expression or a function that
            # exceeds the recursion cap: propagate so the caller can
            # handle it (tests assert on the exception; the GUI
            # path catches it via execute_all's except clause).
            raise
        except RecursionError:
            # P0 — the JS recursion-depth guard catches most runaway
            # recursion at the _max_recursion cap, but a deeply
            # nested Python call stack (each JS frame = one Python
            # stack frame) can still trip Python's native limit
            # before the guard's counter increments.  Convert it to
            # _BudgetExceeded so the caller sees a uniform API.
            raise _BudgetExceeded(
                "JS recursion depth exceeded (Python RecursionError)")
        except (NameError, TypeError, SyntaxError, ZeroDivisionError):
            raise
        # P0 — eval_block now returns the last statement's value
        # (the IIFE's return value propagates through eval_block's
        # ``last`` accumulator).  If the IIFE returned None but the
        # caller's code had a meaningful last expression, surface it.
        if result is not None:
            return result
        return None

    def flush_timers(self) -> None:
        while self._timers:
            code, _ = self._timers.pop(0)
            self.execute(code)


HostGlobals = Host

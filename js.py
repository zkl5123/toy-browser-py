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


def lex(src: str) -> list[Token]:
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
    """
    bindings: list[str]            # target variable names
    value: JNode                  # RHS expression
    keys: list[str] | None = None  # property names (object destructure) or None (array)


# P13.1 — object spread
@dataclass
class ObjectSpread(JNode):
    """Spread node for `{...obj}` inside an object literal.

    Parsed as part of ``ObjectLit.pairs``; when a pair's value is an
    ``ObjectSpread``, the interpreter merges the spread object's keys.
    """
    source: JNode                 # expression that evaluates to a dict


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
        # constructor runs once and copies inherited fields.
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
        """
        if min_prec <= ASSIGN:
            return self.parse_assignment_min()
        return self.parse_expression_inner(min_prec)

    def parse_assignment_min(self) -> JNode:
        """Parse at assignment precedence: `e = e`, `e += e`, `e -= e`."""
        left = self.parse_expression_inner(1)
        t = self.peek()
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
        left = self.parse_postfix()
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
            else:
                break
        return node

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
            return Unary("!", self.parse_postfix())
        if t.value == "-":
            self.next()
            return Unary("-", self.parse_postfix())
        if t.value == "[":
            return self.parse_array_lit()
        if t.value == "{":
            return self.parse_object_lit()
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

    def parse_func_expr(self) -> FunExpr:
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
            stmts.append(self.parse_statement())
        return Block(stmts)

    def parse_statement(self) -> JNode:
        t = self.peek()
        if t.value == "{":
            return self.parse_block()
        if t.kind == "id" and t.value in ("let", "const", "var"):
            self.next()
            # P13.1 — destructuring: `let {a, b} = obj` / `let [x, y] = arr`
            nt = self.peek()
            if nt.value in ("{", "["):
                return self.parse_destructuring()
            name = self._expect_id().value
            value = None
            if self.match("="):
                value = self.parse_expression(1)
            self.match(";")
            return LetDecl(name, value)
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
                value = self.parse_expression(1)
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
        # expression statement (or assignment)
        expr = self.parse_expression(0)
        self.match(";")
        if isinstance(expr, Assign):
            return expr
        return ExprStmt(expr)

    def parse_func_decl(self) -> FuncDecl:
        self.expect("function")
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
        return FuncDecl(name_tok.value, params, body)

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

    def parse_for(self) -> For:
        self.expect("for")
        self.expect("(")
        init = None
        if self.peek().value != ";":
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
    def parse_destructuring(self) -> DestructuringDecl:
        """Parse `let {a, b} = obj` or `let [x, y] = arr` (P13.1).

        The `let`/`const`/`var` keyword has already been consumed by
        `parse_statement`; this method handles the opening brace or bracket.
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
        return DestructuringDecl(bindings, value, keys)

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
    def __init__(self, scope: dict, parent: Optional[Env] = None):
        self.scope = scope
        self.parent = parent

    def lookup(self, name: str) -> Any:
        e = self
        while e is not None:
            if name in e.scope:
                return e.scope[name]
            e = e.parent
        raise NameError(f"'{name}' is not defined")

    def set(self, name: str, value: Any) -> None:
        e = self
        while e is not None:
            if name in e.scope:
                e.scope[name] = value
                return
            e = e.parent
        self.scope[name] = value

    def define(self, name: str, value: Any) -> None:
        self.scope[name] = value


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class Interpreter:
    def __init__(self, host: "Host"):
        self.host = host
        self.env = Env({})
        self.this_value = None  # P11.4 — current `this` binding for event handlers

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
            except NameError:
                if node.name in self.host.globals:
                    return self.host.globals[node.name]
                return None
        if isinstance(node, LetDecl):
            v = self.eval(node.value) if node.value is not None else None
            self.env.define(node.name, v)
            return v
        # P13.1 — destructuring declaration
        if isinstance(node, DestructuringDecl):
            rhs = self.eval(node.value)
            if node.keys is not None:
                # Object destructuring: pluck properties from a dict.
                for key, name in zip(node.keys, node.bindings):
                    val = rhs.get(key) if isinstance(rhs, dict) else None
                    self.env.define(name, val)
            else:
                # Array destructuring: bind by position.
                for i, name in enumerate(node.bindings):
                    val = rhs[i] if isinstance(rhs, (list, tuple)) and i < len(rhs) else None
                    self.env.define(name, val)
            return None
        if isinstance(node, FuncDecl):
            f = self._make_func(node.name, node.params, node.body)
            self.env.define(node.name, f)
            return f
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
            args: list = []
            for a in node.args:
                if isinstance(a, CallSpread):
                    src = self.eval(a.expr)
                    if isinstance(src, list):
                        args.extend(src)
                    else:
                        args.append(src)
                else:
                    args.append(self.eval(a))
            if callable(callee):
                try:
                    return callee(*args)
                except _Throw:
                    raise
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
        # P16.2 — let _Return bubble up so a function's `return` inside a
        # nested if/else block propagates to the function-level catch in
        # _make_func / _make_arrow.  Top-level programs catch _Return
        # themselves (Host.execute).
        last = None
        for s in block.stmts:
            last = self.eval(s)
        return last

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
        if op == "+":
            l = self.eval(node.left)
            r = self.eval(node.right)
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
            return self.eval(node.left) - self.eval(node.right)
        if op == "*":
            return self.eval(node.left) * self.eval(node.right)
        if op == "/":
            return self.eval(node.left) / self.eval(node.right)
        if op == "%":
            return self.eval(node.left) % self.eval(node.right)
        if op in ("==", "==="):
            l, r = self.eval(node.left), self.eval(node.right)
            if op == "===":
                return type(l) is type(r) and l == r
            return l == r
        if op in ("!=", "!=="):
            l, r = self.eval(node.left), self.eval(node.right)
            if op == "!==":
                return not (type(l) is type(r) and l == r)
            return l != r
        if op == "<":
            return self.eval(node.left) < self.eval(node.right)
        if op == ">":
            return self.eval(node.left) > self.eval(node.right)
        if op == "<=":
            return self.eval(node.left) <= self.eval(node.right)
        if op == ">=":
            return self.eval(node.left) >= self.eval(node.right)
        raise TypeError(f"unsupported operator {op}")

    def eval_unary(self, node: Unary) -> Any:
        if node.op == "!":
            return not self.truthy(self.eval(node.operand))
        if node.op == "-":
            return -self.eval(node.operand)
        if node.op == "break":
            raise _Break()
        if node.op == "continue":
            raise _Continue()
        raise TypeError(f"unsupported unary {node.op}")

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
        if isinstance(obj, DomElement):
            return self.host.eval_element_prop(obj, prop)
        if isinstance(obj, dict):
            if "__doc__" in obj:
                fn = self.host.eval_doc_method(obj["__doc__"], prop)
                return fn
            if "__el__" in obj and prop not in ("get", "set", "prop", "__el__"):
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
            return self.eval_block(node.then) if isinstance(node.then, Block) else self.eval(node.then)
        if node.else_ is not None:
            return self.eval_block(node.else_) if isinstance(node.else_, Block) else self.eval(node.else_)
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

    def eval_for(self, node: For) -> Any:
        # P16.2 — `return` inside a for body must propagate; _Break / _Continue
        # are loop-local.
        if node.init is not None:
            self.eval(node.init)
        try:
            while True:
                if node.cond is not None and not self.truthy(self.eval(node.cond)):
                    break
                try:
                    self.eval_block(node.body)
                except _Break:
                    break
                except _Continue:
                    pass
                if node.step is not None:
                    self.eval(node.step)
        except _Return:
            raise
        return None

    def _make_func(self, name, params, body):
        func_env = Env({}, self.env)
        closure = Interpreter(self.host)
        closure.env = func_env

        def f(*args):
            inner = Env({}, func_env)
            for p, a in zip(params, args):
                inner.scope[p] = a
            saved = closure.env
            closure.env = inner
            try:
                val = closure.eval_block(body) if isinstance(body, Block) else closure.eval(body)
            except _Return as r:
                val = r.value
            finally:
                closure.env = saved
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
        closure = Interpreter(self.host)
        closure.env = func_env

        def f(*args):
            inner = Env({}, func_env)
            for p, a in zip(params, args):
                inner.scope[p] = a
            saved = closure.env
            closure.env = inner
            try:
                if is_block:
                    val = closure.eval_block(body)
                else:
                    # Expression body: JS arrow semantics — implicit return.
                    val = closure.eval(body)
            except _Return as r:
                val = r.value
            finally:
                closure.env = saved
            return val

        return f


# ---------------------------------------------------------------------------
# DOM bindings
# ---------------------------------------------------------------------------


class DomElement:
    def __init__(self, node: DomNode):
        self.node = node

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


class Host:
    """Binds the JS engine to a DOM tree + host functions + sandbox rules."""

    def __init__(self, root: DomNode, log):
        self.root = root
        self.log = log
        self.logs: list[str] = []  # P1.2 — collected log lines
        self.element_table: dict = {}
        self._build_element_table()
        self._class_registry: dict[str, ToyClass] = {}  # P12.2
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
        }
        self._timers: list = []
        self._raf_callbacks: list = []  # P1.2
        self._style_cache: dict = {}
        self._promise_counter = 0

    def _interp(self) -> "Interpreter":
        """P12.2 — factory for a fresh Interpreter bound to this Host."""
        i = Interpreter(self)
        i.env = Env(self.globals, None)
        return i

    def _document_obj(self) -> dict:
        return {"__doc__": self.root}

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

        return {
            "abs": lambda x: abs(x),
            "floor": lambda x: _m.floor(x),
            "ceil": lambda x: _m.ceil(x),
            "round": lambda x: round(x),
            "sqrt": lambda x: _m.sqrt(x) if x >= 0 else float("nan"),
            "min": lambda *xs: min(xs) if xs else 0,
            "max": lambda *xs: max(xs) if xs else 0,
            "pow": lambda a, b: _m.pow(a, b),
            "PI": _m.pi,
            "E": _m.e,
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

        class _ToyDate:
            __slots__ = ("_ts", "_dt")

            def __init__(self, ts=None):
                if ts is None:
                    self._dt = _dt.datetime.now()
                    self._ts = self._dt.timestamp()
                else:
                    self._ts = float(ts)
                    self._dt = _dt.datetime.fromtimestamp(self._ts)

            def getTime(self):
                return self._ts * 1000.0

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

        date_ctor.now = lambda: _ToyDate()
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
                return list(self._d.keys())

            def values(self):
                return list(self._d.values())

            def entries(self):
                return [[k, v] for k, v in self._d.items()]

            def forEach(self, fn):
                if callable(fn):
                    for k, v in self._d.items():
                        fn(v, k)

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
                return list(self._s)

            def forEach(self, fn):
                if callable(fn):
                    for v in self._s:
                        fn(v)

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
            compiled = _re.compile(pattern, flags_py)

            def test(s: str) -> bool:
                return bool(compiled.search(s))

            def exec_fn(s: str):
                m = compiled.search(s)
                if not m:
                    return None
                result = [m.group(0)]
                for i in range(1, m.re.groups + 1):
                    result.append(m.group(i))
                result.append([m.start(), m.end()])
                return result

            def replace_fn(s: str, repl: str):
                return compiled.sub(repl, s)

            def split_fn(s: str):
                return [p for p in compiled.split(s)]

            # P4.2 — a dict so that `r.test(...)` routes through call_method.
            return {
                "__regexp__": True,
                "pattern": pattern,
                "flags": flags,
                "test": test,
                "exec": exec_fn,
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
                    self.element_table[eid] = DomElement(n)
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
                return doc.query_selector_all(sel)
            return qsa
        return None

    def call_method(self, obj, method: str, args: list):
        if obj is None:
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
        if isinstance(obj, DomElement):
            return self.eval_element_method(obj, method, args)
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
                if len(args) >= 2:
                    return obj.replace(str(args[0]), str(args[1]))
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
                    return obj.replace(str(args[0]), str(args[1]))
                return obj
            return None
        return None

    def eval_element_prop(self, el: DomElement, prop: str):
        if prop == "id":
            return el.id
        if prop in ("textContent", "innerText", "innerHTML"):
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
        return None

    def _layout_box_for(self, el: "DomElement"):
        """P14.1 — lazily compute a LayoutBox for this element (cached)."""
        eid = el.id
        if eid is None:
            return None
        cache = getattr(self, "_layout_cache", None)
        if cache is None:
            cache = {}
            self._layout_cache = cache
        if eid not in cache:
            try:
                import layout as _layout_mod
                import css as _css_mod
                import dom as _dom_mod
                styled = _css_mod.apply(self.root, "")
                lroot = _layout_mod.layout(styled, 800.0, 600.0)
                def _find(bx, target_id):
                    if bx.dom is not None and bx.dom.is_element() and bx.dom.attr("id") == target_id:
                        return bx
                    for c in bx.children:
                        r = _find(c, target_id)
                        if r is not None:
                            return r
                    return None
                box = _find(lroot, eid)
                cache[eid] = box
            except Exception:
                cache[eid] = None
        return cache[eid]

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
            interpreter.eval_block(program)

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
            interpreter.eval_block(program)
        except _Return:
            pass
        except _Throw as exc:
            # P11.2 — uncaught throw: log the thrown value, don't crash.
            self.log(f"uncaught throw: {exc.value}")
        except (NameError, TypeError, SyntaxError, ZeroDivisionError) as e:
            self.log(f"JS error: {e}")

    def eval(self, code: str):
        """P9.3 — Evaluate a JS expression and return the result value."""
        try:
            program = parse_program(code)
            interpreter = Interpreter(self)
            interpreter.env = Env(self.globals, None)
            # Wrap in an assignment so the result is captured.
            from js import _Return as _Ret
            class _Capture:
                val = None
            cap = _Capture()
            # Re-parse: treat the whole thing as a block; the last expression's
            # value is the block result in many toy JS engines, but here we
            # explicitly capture by wrapping in a function that returns it.
            wrapped = f"((function() {{ {code} }}))()"
            program2 = parse_program(wrapped)
            interpreter.eval_block(program2)
        except _Return as r:
            return r.value
        except _Throw as exc:
            # P11.2 — uncaught throw in eval: log and return None.
            self.log(f"uncaught throw: {exc.value}")
            return None
        except (NameError, TypeError, SyntaxError, ZeroDivisionError) as e:
            self.log(f"JS error: {e}")
        return None

    def flush_timers(self) -> None:
        while self._timers:
            code, _ = self._timers.pop(0)
            self.execute(code)


HostGlobals = Host

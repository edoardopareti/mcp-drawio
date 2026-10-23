"""A deliberately small JavaScript reader for draw.io sidebar files (Sidebar-*.js).

draw.io defines its shape libraries in JavaScript, e.g.::

    var s = 'html=1;shape=mxgraph.archimate3.application;';
    var fns = [
        this.createVertexTemplateEntry(s + 'appType=comp;archiType=square;', w * 1.5, h * 0.75, '', 'Application Component', ...),
    ];
    this.addPaletteFunctions('archimate3Application', 'Archimate 3.0 / Application', false, fns);

The reader walks the token stream in order, tracks simple variable assignments (strings and
numbers), evaluates the arguments of template-entry calls with a tiny expression evaluator and
assigns the palette title of the next addPaletteFunctions/addPalette call as category.
Anything it cannot evaluate statically (function calls, objects, ...) is skipped, never executed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/)
  | (?P<str>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<ident>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)
  | (?P<op>==|!=|=>|[-+*/=(),;\[\]{}:?.!<>&|%])
  | (?P<other>.)
""", re.VERBOSE | re.DOTALL)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"', "\\": "\\"}


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


@dataclass
class TemplateCall:
    """A createVertexTemplateEntry call with statically evaluated arguments."""
    style: str
    width: float | None
    height: float | None
    value: str
    title: str
    tags: str
    category: str | None = None


class Unresolvable(Exception):
    pass


def _unescape(literal: str) -> str:
    body = literal[1:-1]
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), body)


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        if kind == "ws":
            continue
        value = m.group()
        if kind == "str":
            value = _unescape(value)
        tokens.append(Token(kind, value))
    return tokens


class _Evaluator:
    """Recursive-descent evaluator for + - * / over strings, numbers and known variables."""

    def __init__(self, tokens: list[Token], env: dict[str, object]):
        self.tokens, self.env, self.i = tokens, env, 0

    def evaluate(self) -> object:
        if not self.tokens:
            raise Unresolvable("empty")
        value = self._expr()
        if self.i != len(self.tokens):
            raise Unresolvable("trailing tokens")
        return value

    def _peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _expr(self) -> object:
        value = self._term()
        while (t := self._peek()) and t.kind == "op" and t.value in "+-":
            self.i += 1
            rhs = self._term()
            value = _add(value, rhs) if t.value == "+" else _num(value) - _num(rhs)
        return value

    def _term(self) -> object:
        value = self._factor()
        while (t := self._peek()) and t.kind == "op" and t.value in "*/":
            self.i += 1
            rhs = _num(self._factor())
            value = _num(value) * rhs if t.value == "*" else _num(value) / rhs
        return value

    def _factor(self) -> object:
        t = self._peek()
        if t is None:
            raise Unresolvable("unexpected end")
        self.i += 1
        if t.kind == "str":
            return t.value
        if t.kind == "num":
            return float(t.value)
        if t.kind == "op" and t.value == "(":
            value = self._expr()
            closing = self._peek()
            if closing is None or closing.value != ")":
                raise Unresolvable("missing )")
            self.i += 1
            return value
        if t.kind == "op" and t.value == "-":
            return -_num(self._factor())
        if t.kind == "ident":
            nxt = self._peek()
            if nxt is not None and nxt.value in ("(", "["):
                raise Unresolvable(f"call {t.value}")
            if t.value in ("null", "undefined"):
                return ""
            if t.value in ("true", "false"):
                return t.value
            if t.value in self.env:
                return self.env[t.value]
        raise Unresolvable(t.value)


def _num(value: object) -> float:
    if isinstance(value, float):
        return value
    raise Unresolvable(f"not a number: {value!r}")


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)


def _add(a: object, b: object) -> object:
    if isinstance(a, float) and isinstance(b, float):
        return a + b
    return _fmt(a) + _fmt(b)


def evaluate(tokens: list[Token], env: dict[str, object]) -> object:
    return _Evaluator(tokens, env).evaluate()


@dataclass
class SidebarScanResult:
    entries: list[TemplateCall] = field(default_factory=list)
    skipped: int = 0


class SidebarScanner:
    """Extracts template entries from a draw.io Sidebar-*.js file without executing it."""
    VERTEX_CALL = "createVertexTemplateEntry"
    PALETTE_CALLS = ("addPaletteFunctions", "addPalette", "addStencilPalette", "addImagePalette")
    _OPENERS, _CLOSERS = "([{", ")]}"

    def scan(self, text: str) -> SidebarScanResult:
        tokens = tokenize(text)
        env: dict[str, object] = {}
        result = SidebarScanResult()
        pending: list[TemplateCall] = []   # entries waiting for the palette call that follows them
        inline: tuple[int, str] | None = None  # (end index, category) of an enclosing palette call
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if inline and i >= inline[0]:
                inline = None
            if t.kind == "ident" and t.value in ("var", "let", "const"):
                i = self._declarations(tokens, i + 1, env)
                continue
            if t.kind == "ident" and self._is_assignment(tokens, i):
                end = self._statement_end(tokens, i + 2)
                # Skip the expression only if it was a plain value; otherwise keep scanning inside it
                # (e.g. 'X.prototype.addPalette = function() {...}' or 'fns = [ createVertexTemplateEntry(...) ]').
                i = end if self._assign(env, t.value, tokens[i + 2:end]) else i + 2
                continue
            if t.kind == "ident" and self._next_is(tokens, i, "("):
                name = t.value.rsplit(".", 1)[-1]
                if name == self.VERTEX_CALL:
                    args, i = self._call_args(tokens, i + 1)
                    call = self._template(args, env)
                    if call is None:
                        result.skipped += 1
                    elif inline:
                        call.category = inline[1]
                        result.entries.append(call)
                    else:
                        pending.append(call)
                    continue
                if name in self.PALETTE_CALLS:
                    args, end = self._call_args(tokens, i + 1)
                    title = self._try(args[1], env) if len(args) > 1 else None
                    if isinstance(title, str) and title:
                        category = title.split("/")[-1].strip()
                        for call in pending:
                            call.category = call.category or category
                        result.entries.extend(pending)
                        pending = []
                        inline = (end, category)  # entries defined inside the call's arguments
                    i += 2  # scan the arguments: they may contain template entries
                    continue
            i += 1
        result.entries.extend(pending)
        return result

    # ---- statements ----
    @staticmethod
    def _next_is(tokens: list[Token], i: int, value: str) -> bool:
        return i + 1 < len(tokens) and tokens[i + 1].kind == "op" and tokens[i + 1].value == value

    def _is_assignment(self, tokens: list[Token], i: int) -> bool:
        prev = tokens[i - 1] if i else None
        return self._next_is(tokens, i, "=") and not (prev and prev.kind == "op" and prev.value == ".")

    def _statement_end(self, tokens: list[Token], start: int, stop_at_comma: bool = False) -> int:
        depth, i = 0, start
        while i < len(tokens):
            v = tokens[i].value if tokens[i].kind == "op" else None
            if v and v in self._OPENERS:
                depth += 1
            elif v and v in self._CLOSERS:
                if depth == 0:
                    return i
                depth -= 1
            elif depth == 0 and (v == ";" or (stop_at_comma and v == ",")):
                return i
            i += 1
        return i

    def _declarations(self, tokens: list[Token], i: int, env: dict[str, object]) -> int:
        while i < len(tokens) and tokens[i].kind == "ident":
            name = tokens[i].value
            if self._next_is(tokens, i, "="):
                end = self._statement_end(tokens, i + 2, stop_at_comma=True)
                if not self._assign(env, name, tokens[i + 2:end]):
                    return i + 2  # not a plain value: let the main loop scan the expression
                i = end
            else:
                i += 1
            if i < len(tokens) and tokens[i].value == ",":
                i += 1
                continue
            break
        return i

    @staticmethod
    def _assign(env: dict[str, object], name: str, expr: list[Token]) -> bool:
        """Record a statically known value. Returns False (and forgets the name) otherwise."""
        try:
            env[name] = evaluate(expr, env)
            return True
        except (Unresolvable, ZeroDivisionError):
            env.pop(name, None)
            return False

    # ---- calls ----
    def _call_args(self, tokens: list[Token], open_idx: int) -> tuple[list[list[Token]], int]:
        """Split the arguments of the call whose '(' is at open_idx. Returns (args, index after ')')."""
        args: list[list[Token]] = [[]]
        depth, i = 0, open_idx
        while i < len(tokens):
            t = tokens[i]
            v = t.value if t.kind == "op" else None
            if v and v in self._OPENERS:
                depth += 1
                if depth == 1:
                    i += 1
                    continue
            elif v and v in self._CLOSERS:
                depth -= 1
                if depth == 0:
                    return (args if args != [[]] else []), i + 1
            elif depth == 1 and v == ",":
                args.append([])
                i += 1
                continue
            args[-1].append(t)
            i += 1
        return args, i

    @staticmethod
    def _try(arg: list[Token], env: dict[str, object]) -> object | None:
        try:
            return evaluate(arg, env)
        except (Unresolvable, ZeroDivisionError):
            return None

    def _template(self, args: list[list[Token]], env: dict[str, object]) -> TemplateCall | None:
        values = [self._try(a, env) for a in args] + [None] * 8
        style = values[0]
        if not isinstance(style, str) or not style:
            return None
        width = values[1] if isinstance(values[1], float) else None
        height = values[2] if isinstance(values[2], float) else None
        value = values[3] if isinstance(values[3], str) else ""
        title = values[4] if isinstance(values[4], str) else ""
        tags = values[7] if isinstance(values[7], str) else ""
        return TemplateCall(style=style, width=width, height=height, value=value, title=title, tags=tags)

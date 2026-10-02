"""Stage 1 — HTML tokenizer + tree builder (pure stdlib).

Produces a DOM tree of Node objects. The root is a synthetic "document"
node. Void tags are not pushed onto the stack; implicitly-closing siblings
(second <p> closes the first) are handled.
"""
from __future__ import annotations

from typing import Optional


class Node:
    __slots__ = (
        "node_type",
        "tag",
        "attrs",
        "text",
        "children",
        "parent",
    )

    ELEMENT = "element"
    TEXT = "text"

    def __init__(
        self,
        node_type: str,
        tag: Optional[str] = None,
        attrs: Optional[dict] = None,
        text: Optional[str] = None,
    ):
        self.node_type = node_type
        self.tag = tag
        self.attrs = attrs if attrs is not None else {}
        self.text = text
        self.children: list[Node] = []
        self.parent: Optional[Node] = None

    @classmethod
    def element(cls, tag: str, attrs: Optional[dict] = None) -> "Node":
        return cls(cls.ELEMENT, tag=tag, attrs=attrs)

    @classmethod
    def text_node(cls, text: str) -> "Node":
        return cls(cls.TEXT, text=text)

    def is_element(self) -> bool:
        return self.node_type == Node.ELEMENT

    def tag_name(self) -> Optional[str]:
        return self.tag if self.is_element() else None

    def attr(self, name: str) -> Optional[str]:
        return self.attrs.get(name)

    def set_attr(self, name: str, value: str) -> None:
        self.attrs[name] = value

    def text_content(self) -> str:
        """Concatenated text of this node's direct text children (or self)."""
        if not self.is_element():
            return self.text or ""
        return "".join(
            c.text if not c.is_element() else c.text_content() for c in self.children
        )

    def find_by_id(self, _id: str) -> Optional[Node]:
        def walk(n: Node) -> Optional[Node]:
            if n.is_element() and n.attrs.get("id") == _id:
                return n
            for c in n.children:
                f = walk(c)
                if f:
                    return f
            return None

        return walk(self)

    def has_class(self, cls: str) -> bool:
        if not self.is_element():
            return False
        v = self.attrs.get("class", "")
        return cls in v.split()

    def query_selector_all(self, selector: str) -> list:
        """Supports '.cls' and '#id' only (toy scope, mirrors the Rust version)."""
        selector = selector.strip()
        results: list[Node] = []
        if selector.startswith("."):
            cls = selector[1:]
        elif selector.startswith("#"):
            _id = selector[1:]

            def walk(n: Node) -> None:
                if n.is_element() and n.attrs.get("id") == _id:
                    results.append(n)
                for c in n.children:
                    walk(c)

            walk(self)
            return results
        else:
            raise ValueError(f"unsupported selector: {selector}")

        def walk_cls(n: Node) -> None:
            if n.has_class(cls):
                results.append(n)
            for c in n.children:
                walk_cls(c)

        walk_cls(self)
        return results


VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def implicitly_closes(tag: str) -> bool:
    return tag in ("p", "li")


"""P19 — HTML entity decoding for text nodes."""
_ENTITY_MAP = {
    "&shy;": "\u00AD",
    "&nbsp;": "\u00A0",
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&apos;": "'",
}


def _decode_entities(text: str) -> str:
    """Decode HTML entities in a text-node string (P19 — &shy; support)."""
    for entity, char in _ENTITY_MAP.items():
        if entity in text:
            text = text.replace(entity, char)
    return text


def _lex(input: str) -> list:
    """Very small tag/text lexer. Yields ("tag", tag, attrs) and ("text", s)."""
    i = 0
    n = len(input)
    out = []
    while i < n:
        lt = input.find("<", i)
        if lt < 0:
            out.append(("text", _decode_entities(input[i:])))
            break
        if lt > i:
            out.append(("text", _decode_entities(input[i:lt])))
        i = lt
        gt = input.find(">", i)
        if gt < 0:
            out.append(("text", input[i:]))
            break
        raw = input[i + 1 : gt]
        i = gt + 1
        if not raw:
            continue
        if raw.startswith("!"):
            # Comment / doctype — skip.
            continue
        closing = raw.startswith("/")
        body = raw[1:] if closing else raw
        body = body.strip()
        is_close = body.startswith("/")
        if is_close:
            body = body[1:]
        # Split off the tag name.
        j = 0
        while j < len(body) and body[j] not in " \t\r\n/":
            j += 1
        tag_name = body[:j].lower()
        attr_src = body[j:]
        attrs: dict = {}
        for name, value in _attr_pairs(attr_src):
            attrs[name.lower()] = value
        out.append(("end" if (closing or is_close) else "start", tag_name, attrs))
    return out


def _attr_pairs(src: str):
    """Yield (name, value) pairs from an attribute source string."""
    i = 0
    n = len(src)
    while i < n:
        while i < n and src[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        j = i
        while j < n and src[j] not in " \t\r\n=/":
            j += 1
        name = src[i:j]
        i = j
        if i < n and src[i] == "=":
            i += 1
            if i < n and src[i] in "'\"":
                quote = src[i]
                i += 1
                k = i
                while k < n and src[k] != quote:
                    k += 1
                value = src[i:k]
                i = k + 1
            else:
                k = i
                while k < n and src[k] not in " \t\r\n>":
                    k += 1
                value = src[i:k]
                i = k
        else:
            value = ""
        i += 1
        if name:
            yield name, value


def parse(input: str) -> Node:
    root = Node.element("document")
    stack: list[Node] = [root]
    open_tags: list[str] = []
    for tok in _lex(input):
        kind = tok[0]
        if kind == "text":
            text = tok[1]
            if text.strip():
                node = Node.text_node(text)
                node.parent = stack[-1]
                stack[-1].children.append(node)
        else:
            tag = tok[1].lower()
            attrs = tok[2]
            if kind == "start":
                if implicitly_closes(tag) and open_tags and open_tags[-1] == tag:
                    open_tags.pop()
                    stack.pop()
                node = Node.element(tag, attrs)
                parent = stack[-1]
                node.parent = parent
                parent.children.append(node)
                if tag not in VOID_TAGS:
                    stack.append(node)
                    open_tags.append(tag)
            else:
                # Closing tag.
                if not open_tags:
                    continue
                if open_tags[-1] == tag:
                    open_tags.pop()
                    stack.pop()
                else:
                    # Fault-tolerant: pop until we match, else ignore.
                    if tag in open_tags:
                        while open_tags:
                            t = open_tags.pop()
                            stack.pop()
                            if t == tag:
                                break
    return root


def parse_file(path: str) -> Node:
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse(f.read())

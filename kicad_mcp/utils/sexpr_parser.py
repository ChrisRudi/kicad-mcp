# SPDX-License-Identifier: GPL-3.0-or-later
"""
Minimal S-expression parser for KiCad files.

Parses .kicad_sch and .kicad_pcb files into nested Python lists.
"""

from typing import Any


def parse_sexpr(text: str) -> list[Any]:
    """Parse an S-expression string into nested Python lists.

    Args:
        text: S-expression text content

    Returns:
        Nested list structure representing the S-expression
    """
    tokens = _tokenize(text)
    result, _ = _parse_tokens(tokens, 0)
    return result


def _tokenize(text: str) -> list[str]:
    """Tokenize S-expression text into a flat list of tokens."""
    tokens = []
    i = 0
    length = len(text)

    while i < length:
        c = text[i]

        if c in " \t\n\r":
            i += 1
        elif c == "(":
            tokens.append("(")
            i += 1
        elif c == ")":
            tokens.append(")")
            i += 1
        elif c == '"':
            # Quoted string
            j = i + 1
            while j < length:
                if text[j] == "\\" and j + 1 < length:
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            tokens.append(text[i + 1 : j])
            i = j + 1
        else:
            # Unquoted token
            j = i
            while j < length and text[j] not in " \t\n\r()\"":
                j += 1
            tokens.append(text[i:j])
            i = j

    return tokens


def _parse_tokens(tokens: list[str], pos: int) -> tuple[Any, int]:
    """Recursively parse tokens into nested lists."""
    if pos >= len(tokens):
        return [], pos

    if tokens[pos] == "(":
        lst = []
        pos += 1
        while pos < len(tokens) and tokens[pos] != ")":
            item, pos = _parse_tokens(tokens, pos)
            lst.append(item)
        if pos < len(tokens):
            pos += 1  # skip ')'
        return lst, pos

    # Atom
    return tokens[pos], pos + 1


def block_end(text: str, start: int) -> int:
    """Index just past the ``)`` that closes the ``(`` at ``start``.

    Text-level companion to :func:`parse_sexpr` for surgical patching:
    callers slice whole ``(footprint …)``/``(segment …)`` blocks out of a
    file without tokenising it. ``text[start]`` must be ``"("``; an
    unclosed block yields ``len(text)``.
    """
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def find_nodes(tree: list, tag: str) -> list[list]:
    """Find all child nodes with a given tag in an S-expression tree.

    Args:
        tree: Parsed S-expression (list)
        tag: Tag name to search for

    Returns:
        List of matching nodes
    """
    results = []
    if isinstance(tree, list):
        for item in tree:
            if isinstance(item, list) and len(item) > 0 and item[0] == tag:
                results.append(item)
    return results


def find_node(tree: list, tag: str) -> list | None:
    """Find first child node with a given tag."""
    nodes = find_nodes(tree, tag)
    return nodes[0] if nodes else None


def get_property(tree: list, tag: str) -> str | None:
    """Get the value following a tag in an S-expression node.

    Example: (footprint "value") -> "value"
    """
    if isinstance(tree, list):
        for i, item in enumerate(tree):
            if item == tag and i + 1 < len(tree) and not isinstance(tree[i + 1], list):
                return str(tree[i + 1])
    return None

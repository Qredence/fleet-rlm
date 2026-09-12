"""Restore request-specified native Sub-LM prompt literals in generated actions.

When a Turn request already contains ``llm_query`` / ``llm_query_batched``
string literals, generated Root code must use those strings unchanged. This
module rewrites only matching call arguments; it does not invent statements.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from fleet_rlm.rlm.submit_validation import _strip_action_code_fences

_SINGLE = "llm_query"
_BATCHED = "llm_query_batched"
SpecifiedName = Literal["llm_query", "llm_query_batched"]


@dataclass(frozen=True, slots=True)
class SpecifiedSubLMCall:
    """One request-specified native Sub-LM call with literal prompt strings."""

    name: SpecifiedName
    prompts: tuple[str, ...]


def apply_specified_sub_lm_prompts(request: object, code: object) -> str:
    """
    Replace generated ``llm_query`` / ``llm_query_batched`` string arguments
    with literals already present in the Turn request, in request order.

    Parameters:
        request (object): Current Turn request text, or ``None`` when unbound.
        code (object): Generated interpreter action source.

    Returns:
        str: Rewritten action source, or the original code when there is
            nothing to restore or the source cannot be parsed.
    """
    if not isinstance(code, str):
        return "" if code is None else str(code)
    specified = specified_sub_lm_calls(request)
    if not specified:
        return code
    try:
        tree = ast.parse(_strip_action_code_fences(code), mode="exec")
    except SyntaxError:
        return code
    queues = _queues_by_name(specified)
    rewriter = _SpecifiedPromptRewriter(queues)
    updated = rewriter.visit(tree)
    if not rewriter.changed:
        return code
    ast.fix_missing_locations(updated)
    try:
        return ast.unparse(updated)
    except RecursionError:
        return code


def specified_sub_lm_calls(request: object) -> tuple[SpecifiedSubLMCall, ...]:
    """Return request-specified native Sub-LM calls in source order."""
    if not isinstance(request, str) or not request:
        return ()
    text = _strip_action_code_fences(request)
    found: list[SpecifiedSubLMCall] = []
    index = 0
    while True:
        span = _next_call_span(text, index)
        if span is None:
            break
        start, end, name = span
        snippet = text[start:end]
        specified = _specified_call_from_snippet(snippet, name)
        if specified is not None:
            found.append(specified)
        index = end
    return tuple(found)


def _queues_by_name(
    specified: Sequence[SpecifiedSubLMCall],
) -> dict[SpecifiedName, list[tuple[str, ...]]]:
    queues: dict[SpecifiedName, list[tuple[str, ...]]] = {_SINGLE: [], _BATCHED: []}
    for call in specified:
        queues[call.name].append(call.prompts)
    return queues


def _next_call_span(text: str, start: int) -> tuple[int, int, SpecifiedName] | None:
    cursor = start
    while cursor < len(text):
        found = text.find("llm_query", cursor)
        if found == -1:
            return None
        rest = text[found + len("llm_query") :]
        if rest.startswith("_batched("):
            name: SpecifiedName = _BATCHED
            open_at = found + len(_BATCHED)
        elif rest.startswith("("):
            name = _SINGLE
            open_at = found + len(_SINGLE)
        else:
            cursor = found + 1
            continue
        close_at = _matching_paren(text, open_at)
        if close_at is None:
            return None
        return found, close_at + 1, name
    return None


def _matching_paren(text: str, open_at: int) -> int | None:
    if open_at >= len(text) or text[open_at] != "(":
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_at, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "(":
            depth += 1
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _specified_call_from_snippet(snippet: str, name: SpecifiedName) -> SpecifiedSubLMCall | None:
    try:
        tree = ast.parse(snippet, mode="eval")
    except SyntaxError:
        return None
    call = tree.body
    if not isinstance(call, ast.Call):
        return None
    prompts = _literal_prompts(call, name)
    if prompts is None:
        return None
    return SpecifiedSubLMCall(name=name, prompts=prompts)


def _literal_prompts(call: ast.Call, name: SpecifiedName) -> tuple[str, ...] | None:
    argument = _named_or_first_arg(call, "prompt" if name == _SINGLE else "prompts")
    if argument is None:
        return None
    if name == _SINGLE:
        value = _string_constant(argument)
        return None if value is None else (value,)
    if not isinstance(argument, ast.List):
        return None
    values: list[str] = []
    for element in argument.elts:
        item = _string_constant(element)
        if item is None:
            return None
        values.append(item)
    return tuple(values)


def _named_or_first_arg(call: ast.Call, keyword: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    if call.args:
        return call.args[0]
    return None


def _string_constant(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_string_or_string_list(node: ast.AST) -> bool:
    if _string_constant(node) is not None:
        return True
    return isinstance(node, ast.List) and all(_string_constant(element) is not None for element in node.elts)


class _SpecifiedPromptRewriter(ast.NodeTransformer):
    """Replace generated Sub-LM string arguments from per-name request queues."""

    def __init__(self, queues: dict[SpecifiedName, list[tuple[str, ...]]]) -> None:
        self._queues = queues
        self.changed = False

    def visit_Call(self, node: ast.Call) -> ast.AST:
        node = self.generic_visit(node)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            return node
        name = node.func.id
        if name not in {_SINGLE, _BATCHED}:
            return node
        queue = self._queues[name]
        if not queue:
            return node
        target = _named_or_first_arg(node, "prompt" if name == _SINGLE else "prompts")
        if target is None or not _is_string_or_string_list(target):
            return node
        prompts = queue.pop(0)
        replacement: ast.AST = (
            ast.Constant(value=prompts[0])
            if name == _SINGLE
            else ast.List(elts=[ast.Constant(value=item) for item in prompts])
        )
        keyword_name = "prompt" if name == _SINGLE else "prompts"
        if node.args and target is node.args[0]:
            node.args[0] = replacement
        else:
            for item in node.keywords:
                if item.arg == keyword_name:
                    item.value = replacement
                    break
        self.changed = True
        return node

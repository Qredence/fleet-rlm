"""Restore request-specified native Sub-LM prompt literals in generated actions.

When a Turn request already contains ``llm_query`` / ``llm_query_batched``
string literals, generated Root code must use those strings unchanged. When
the same request also specifies ``accumulator.extend([single_result,
*batch_results])`` and the generated cell calls ``verify_semantic_work``
without that extend, the specified statement is restored immediately before
verify. This module does not invent prompts or statements that are absent
from the request.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from fleet_rlm.rlm.submit_validation import _strip_action_code_fences


def normalize_action_code(code: object) -> str:
    """Return parseable action source in a stable ``ast.unparse`` form."""
    if not isinstance(code, str) or not code:
        return "" if code is None else str(code)
    try:
        return ast.unparse(ast.parse(_strip_action_code_fences(code), mode="exec"))
    except (SyntaxError, RecursionError):
        return code


_SINGLE = "llm_query"
_BATCHED = "llm_query_batched"
SpecifiedName = Literal["llm_query", "llm_query_batched"]


@dataclass(frozen=True, slots=True)
class SpecifiedSubLMCall:
    """One request-specified native Sub-LM call with literal prompt strings."""

    name: SpecifiedName
    prompts: tuple[str, ...]


@dataclass(slots=True)
class SpecifiedPromptRewriteState:
    """Per-Turn cursor for request-specified native Sub-LM calls.

    A generated action can be executed more than once during one Turn (for
    example after a recoverable interpreter error).  The request's calls must
    therefore be consumed in order across executions, while a new Turn must
    start from the first specified call again.
    """

    request: str | None = None
    _rewriter: _SpecifiedPromptRewriter | None = None
    _bound: bool = False

    def bind(self, request: object) -> None:
        """Reset the call cursor for a newly bound Turn request."""
        normalized = request if isinstance(request, str) and request else None
        self.request = normalized
        specified = specified_sub_lm_calls(normalized)
        self._rewriter = _SpecifiedPromptRewriter(_queues_by_name(specified)) if specified else None
        self._bound = True

    def rewriter_for(self, request: object) -> _SpecifiedPromptRewriter | None:
        """Return the cursor for ``request``, binding lazily for callers without a Turn hook."""
        normalized = request if isinstance(request, str) and request else None
        if not self._bound or self.request != normalized:
            self.bind(normalized)
        return self._rewriter


def apply_specified_sub_lm_prompts(
    request: object,
    code: object,
    *,
    state: SpecifiedPromptRewriteState | None = None,
) -> str:
    """
    Replace generated ``llm_query`` / ``llm_query_batched`` string or
    ``request``-name arguments with literals already present in the Turn
    request, in request order.

    Parameters:
        request (object): Current Turn request text, or ``None`` when unbound.
        code (object): Generated interpreter action source.

    Returns:
        str: Rewritten action source, or the original code when there is
            nothing to restore or the source cannot be parsed.
    """
    if not isinstance(code, str):
        return "" if code is None else str(code)
    if state is not None:
        rewriter = state.rewriter_for(request)
        if rewriter is None:
            return code
    else:
        specified = specified_sub_lm_calls(request)
        if not specified:
            return code
        rewriter = _SpecifiedPromptRewriter(_queues_by_name(specified))
    if rewriter is None:
        return code
    try:
        tree = ast.parse(_strip_action_code_fences(code), mode="exec")
    except SyntaxError:
        return code
    rewriter.changed = False
    updated = rewriter.visit(tree)
    inserted_extend = _restore_specified_accumulator_extend(request, updated)
    if not rewriter.changed and not inserted_extend:
        return code
    ast.fix_missing_locations(updated)
    try:
        return ast.unparse(updated)
    except RecursionError:
        return code


_SPECIFIED_ACCUMULATOR_EXTEND = "accumulator.extend([single_result,*batch_results])"


def _compact_python(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _request_specifies_accumulator_extend(request: object) -> bool:
    return isinstance(request, str) and _SPECIFIED_ACCUMULATOR_EXTEND in _compact_python(request)


def _is_exact_accumulator_extend(node: ast.AST) -> bool:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    call = node.value
    if (
        not isinstance(call.func, ast.Attribute)
        or not isinstance(call.func.value, ast.Name)
        or call.func.value.id != "accumulator"
        or call.func.attr != "extend"
        or len(call.args) != 1
        or call.keywords
    ):
        return False
    argument = call.args[0]
    return (
        isinstance(argument, ast.List)
        and len(argument.elts) == 2
        and isinstance(argument.elts[0], ast.Name)
        and argument.elts[0].id == "single_result"
        and isinstance(argument.elts[1], ast.Starred)
        and isinstance(argument.elts[1].value, ast.Name)
        and argument.elts[1].value.id == "batch_results"
    )


def _has_accumulator_extend(statements: Sequence[ast.stmt], verify_index: int) -> bool:
    """Return whether the exact extension precedes verify in its statement list."""
    return any(_is_exact_accumulator_extend(statement) for statement in statements[:verify_index])


def _contains_direct_verify_semantic_work(statement: ast.stmt) -> bool:
    """Return whether a statement invokes verify outside a nested statement block."""
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        if node is not statement and isinstance(node, ast.stmt):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "verify_semantic_work":
            return True
        pending.extend(ast.iter_child_nodes(node))
    return False


def _verify_semantic_work_location(node: ast.AST) -> tuple[list[ast.stmt], int] | None:
    """Locate the statement list that directly contains the first verify call."""
    for _field, value in ast.iter_fields(node):
        if isinstance(value, list):
            if all(isinstance(item, ast.stmt) for item in value):
                statements = cast(list[ast.stmt], value)
                for index, statement in enumerate(statements):
                    if _contains_direct_verify_semantic_work(statement):
                        return statements, index
                for statement in statements:
                    location = _verify_semantic_work_location(statement)
                    if location is not None:
                        return location
        elif isinstance(value, ast.AST) and not isinstance(value, ast.stmt):
            location = _verify_semantic_work_location(value)
            if location is not None:
                return location
    return None


def _restore_specified_accumulator_extend(request: object, tree: ast.AST) -> bool:
    """Insert the request-specified accumulator extend before verify when omitted."""
    if not _request_specifies_accumulator_extend(request) or not isinstance(tree, ast.Module):
        return False
    location = _verify_semantic_work_location(tree)
    if location is None:
        return False
    body, verify_index = location
    if _has_accumulator_extend(body, verify_index):
        return False
    body.insert(verify_index, ast.parse("accumulator.extend([single_result, *batch_results])", mode="exec").body[0])
    return True


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
        # Only restore direct calls.  Substrings in identifiers, attributes,
        # and qualified calls (``my_llm_query`` / ``client.llm_query``) are
        # not request-specified native Sub-LM calls.
        if found and (text[found - 1].isalnum() or text[found - 1] == "_" or text[found - 1] == "."):
            cursor = found + len("llm_query")
            continue
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


def _is_request_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "request"


def _is_restorable_prompt_arg(node: ast.AST) -> bool:
    if _is_string_or_string_list(node) or _is_request_name(node):
        return True
    return isinstance(node, ast.List) and bool(node.elts) and all(_is_request_name(element) for element in node.elts)


class _SpecifiedPromptRewriter(ast.NodeTransformer):
    """Replace generated Sub-LM string arguments from per-name request queues."""

    def __init__(self, queues: dict[SpecifiedName, list[tuple[str, ...]]]) -> None:
        self._queues = queues
        self.changed = False

    def visit_Call(self, node: ast.Call) -> ast.AST:
        visited = self.generic_visit(node)
        if not isinstance(visited, ast.Call):
            return visited
        func = visited.func
        if not isinstance(func, ast.Name):
            return visited
        node = visited
        name = func.id
        if name not in {_SINGLE, _BATCHED}:
            return node
        queue = self._queues[name]
        if not queue:
            return node
        target = _named_or_first_arg(node, "prompt" if name == _SINGLE else "prompts")
        if target is None or not _is_restorable_prompt_arg(target):
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

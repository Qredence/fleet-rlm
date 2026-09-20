"""Pure syntax validation for Fleet finalization actions; not a security sandbox."""

from __future__ import annotations

import ast

_SAFE_SUBMIT_CALLS = frozenset(
    {
        "str",
        "repr",
        "int",
        "float",
        "bool",
        "len",
        "min",
        "max",
        "sum",
        "round",
        "sorted",
        "json.dumps",
    }
)

_PYTHON_FENCE_LANGS = frozenset({"", "python", "py"})


def _qualified_ast_name(node: ast.AST) -> str | None:
    """Return a dotted name for a simple AST name/attribute expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def _strip_action_code_fences(code: str) -> str:
    """
    Remove surrounding Python markdown fences from action code.

    Non-Python or malformed fences are preserved unchanged.

    Returns:
        str: The executable action text with Python fences removed.
    """
    text = code.strip()
    if "```" not in text:
        return text
    lines = text.splitlines()
    while len(lines) >= 2 and lines[0].strip() == "```" and lines[-1].strip() == "```":
        lines.pop(0)
        lines.pop()
    text = "\n".join(lines).strip()
    if "```" not in text:
        return text
    fence_start = text.find("```")
    lang_line, separator, remainder = text[fence_start + 3 :].partition("\n")
    if not separator:
        return text
    lang = (lang_line.strip().split(maxsplit=1)[0] if lang_line.strip() else "").lower()
    if lang not in _PYTHON_FENCE_LANGS:
        return text
    block_end = remainder.find("```")
    if block_end == -1:
        return remainder.strip()
    return remainder[:block_end].strip()


def _is_safe_submit_value(node: ast.AST) -> bool:
    """
    Determine whether an AST node is an allowed expression for a SUBMIT keyword value.

    Parameters:
        node (ast.AST): The expression node to validate.

    Returns:
        bool: True if the node uses an allowed data expression, false otherwise.
    """
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.JoinedStr):
        return all(_is_safe_submit_value(value) for value in node.values)
    if isinstance(node, ast.FormattedValue):
        return _is_safe_submit_value(node.value) and (
            node.format_spec is None or _is_safe_submit_value(node.format_spec)
        )
    if isinstance(node, ast.Attribute):
        return not node.attr.startswith("_") and _is_safe_submit_value(node.value)
    if isinstance(node, ast.Subscript):
        return _is_safe_submit_value(node.value) and _is_safe_submit_value(node.slice)
    if isinstance(node, ast.Slice):
        return all(part is None or _is_safe_submit_value(part) for part in (node.lower, node.upper, node.step))
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_safe_submit_value(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            key is not None and _is_safe_submit_value(key) and _is_safe_submit_value(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Call):
        if _qualified_ast_name(node.func) not in _SAFE_SUBMIT_CALLS:
            return False
        if any(isinstance(argument, ast.Starred) for argument in node.args):
            return False
        if any(keyword.arg is None for keyword in node.keywords):
            return False
        return all(_is_safe_submit_value(argument) for argument in node.args) and all(
            _is_safe_submit_value(keyword.value) for keyword in node.keywords
        )
    if isinstance(node, ast.UnaryOp):
        return _is_safe_submit_value(node.operand)
    if isinstance(node, ast.BinOp):
        return _is_safe_submit_value(node.left) and _is_safe_submit_value(node.right)
    if isinstance(node, ast.BoolOp):
        return all(_is_safe_submit_value(value) for value in node.values)
    if isinstance(node, ast.Compare):
        return _is_safe_submit_value(node.left) and all(_is_safe_submit_value(item) for item in node.comparators)
    if isinstance(node, ast.IfExp):
        return (
            _is_safe_submit_value(node.test) and _is_safe_submit_value(node.body) and _is_safe_submit_value(node.orelse)
        )
    return False


def _parse_action_module(code: object) -> ast.Module | None:
    """Parse action source, or return `None` when it is not parseable Python."""
    if not isinstance(code, str):
        return None
    try:
        return ast.parse(_strip_action_code_fences(code), mode="exec")
    except SyntaxError:
        return None


def _is_compliant_submit_call(statement: ast.stmt) -> bool:
    """Whether `statement` is exactly one `SUBMIT(...)` call with data-only values."""
    if not isinstance(statement, ast.Expr):
        return False
    expression = statement.value
    if (
        not isinstance(expression, ast.Call)
        or not isinstance(expression.func, ast.Name)
        or expression.func.id != "SUBMIT"
    ):
        return False
    if expression.args or any(keyword.arg is None for keyword in expression.keywords):
        return False
    return all(_is_safe_submit_value(keyword.value) for keyword in expression.keywords)


def _is_safe_answer_binding(statement: ast.stmt) -> bool:
    """
    Whether `statement` is a plain `name = <data>` answer binding.

    Bindings qualify only when their value cannot reach a Tool, the provider, an
    import, or a dunder: the value obeys the same data-only rule as a SUBMIT
    keyword. This is what separates "shape the answer I already have" from
    "perform one more piece of work".
    """
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return False
    target = statement.targets[0]
    if (
        not isinstance(target, ast.Name)
        or target.id.startswith("_")
        or target.id in {"SUBMIT", "FleetFinalOutputError", "json"}
        or target.id in _SAFE_SUBMIT_CALLS
    ):
        return False
    return _is_safe_submit_value(statement.value)


def is_submit_only_code(code: object) -> bool:
    """
    Determine whether code contains a syntactically valid, submit-only action.

    Parameters:
        code (object): Source code to validate.

    Returns:
        bool: `true` if the code consists of one direct `SUBMIT` call with
            permitted keyword-value expressions, `false` otherwise.

    This validates syntax only; it does not resolve names or evaluate operator
    behavior. Use `is_finalization_action` to ask the wider question of whether
    an action is an acceptable way to end a Turn.
    """
    module = _parse_action_module(code)
    return module is not None and len(module.body) == 1 and _is_compliant_submit_call(module.body[0])


def is_finalization_action(code: object) -> bool:
    """
    Determine whether code is an admissible wrap-up finalization action.

    Parameters:
        code (object): Source code to validate.

    Returns:
        bool: `true` when the action is any number of non-effectful
            `name = <data>` bindings followed by exactly one compliant
            `SUBMIT(...)` call, `false` otherwise.

    Wrap-up exists to stop exploration, not to forbid shaping an answer. A
    binding whose value is data-only cannot call a Tool, import a module, or
    reach the provider, so it is admitted; every other statement is rejected.
    A submit-only action is always a finalization action.
    """
    module = _parse_action_module(code)
    if module is None or not module.body:
        return False
    if not _is_compliant_submit_call(module.body[-1]):
        return False
    return all(_is_safe_answer_binding(statement) for statement in module.body[:-1])


def normalize_action_code(code: object) -> str:
    """Return parseable action source in a stable ``ast.unparse`` form."""
    if not isinstance(code, str) or not code:
        return "" if code is None else str(code)
    try:
        return ast.unparse(ast.parse(_strip_action_code_fences(code), mode="exec"))
    except (SyntaxError, RecursionError):
        return code

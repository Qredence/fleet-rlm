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
    """Mirror DSPy's public action fence handling for wrap-up validation.

    Native RLM actions are commonly emitted in a Python markdown fence and
    DSPy strips that fence before execution. Validation must inspect the same
    executable text, while still rejecting an explicit non-Python fence.
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
    """Allow only data expressions that cannot launch another action."""
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


def is_submit_only_code(code: object) -> bool:
    """Check wrap-up syntax, not runtime safety of names or overloaded operators."""
    if not isinstance(code, str):
        return False
    try:
        module = ast.parse(_strip_action_code_fences(code), mode="exec")
    except SyntaxError:
        return False
    if len(module.body) != 1 or not isinstance(module.body[0], ast.Expr):
        return False
    expression = module.body[0].value
    if (
        not isinstance(expression, ast.Call)
        or not isinstance(expression.func, ast.Name)
        or expression.func.id != "SUBMIT"
    ):
        return False
    if expression.args or any(keyword.arg is None for keyword in expression.keywords):
        return False
    return all(_is_safe_submit_value(keyword.value) for keyword in expression.keywords)

from __future__ import annotations

import ast

from scripts.check_codebase_tree import find_nested_ternaries


def test_nested_ifexp_is_reported() -> None:
    tree = ast.parse("value = a if cond else (b if other else c)\n")

    assert find_nested_ternaries(tree) == [1]


def test_simple_ifexp_is_allowed() -> None:
    tree = ast.parse("value = a if cond else b\n")

    assert find_nested_ternaries(tree) == []


def test_nested_ifexp_in_body_is_reported() -> None:
    tree = ast.parse("value = (a if inner else b) if outer else c\n")

    assert find_nested_ternaries(tree) == [1]


def test_ifexp_inside_call_in_branch_is_reported() -> None:
    tree = ast.parse("value = foo(a if x else b) if cond else default\n")

    assert find_nested_ternaries(tree) == [1]


def test_ifexp_only_in_condition_is_allowed() -> None:
    tree = ast.parse("value = a if (b if c else d) else e\n")

    assert find_nested_ternaries(tree) == []

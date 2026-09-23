"""The model-authored child request never grants source authority."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fleet_rlm.rlm.recursion import ChildRequest


def test_child_request_carries_small_task_and_relative_references() -> None:
    request = ChildRequest.from_mapping(
        {
            "task": "Inspect the parser and callers",
            "inputs": ["projects/repo/src/parser.py", "projects/repo/tests"],
            "context": "Focus on error handling.",
        }
    )
    assert request.inputs == ("projects/repo/src/parser.py", "projects/repo/tests")
    assert "error handling" in request.render()
    assert request.serialized_bytes < 1_000


@pytest.mark.parametrize(
    "inputs",
    [
        "projects/repo/src",
        ["/absolute/path"],
        ["../other-session"],
        ["projects/repo/../other"],
        ["https://example.com/source"],
        ["artifact://00000000-0000-0000-0000-000000000000"],
        ["projects/repo/%2e%2e/secret"],
        ["projects\\repo\\source"],
        ["projects/repo/source", "projects/repo/source"],
        ["projects/repo//source"],
    ],
)
def test_child_request_rejects_invalid_or_ambiguous_inputs(inputs: object) -> None:
    with pytest.raises((ValueError, ValidationError)):
        ChildRequest.from_mapping({"task": "Inspect", "inputs": inputs})


def test_child_request_rejects_model_authored_runtime_policy() -> None:
    with pytest.raises((ValueError, ValidationError)):
        ChildRequest.from_mapping({"task": "Inspect", "inputs": [], "max_children": 100, "credentials": "grant access"})

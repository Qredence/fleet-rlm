"""Tests for structured final artifact classification."""

from __future__ import annotations

from fleet_rlm.runtime.execution.final_artifact import (
    build_final_artifact_from_answer,
    classify_answer_format,
    infer_expected_output_format,
    output_format_guidance_for_task,
)


def test_short_answer_stays_assistant_response() -> None:
    answer = "Hello! I can help with fleet-rlm."
    assert classify_answer_format(answer) == "assistant_response"
    artifact = build_final_artifact_from_answer(answer)
    assert artifact is not None
    assert artifact["kind"] == "assistant_response"


def test_long_detailed_answer_becomes_markdown() -> None:
    answer = "## Runtime modules\n\n" + ("- item\n" * 400)
    task = "Provide an in depth analysis of the runtime modules."
    assert classify_answer_format(answer, task=task) == "markdown"
    artifact = build_final_artifact_from_answer(answer, task=task)
    assert artifact is not None
    assert artifact["kind"] == "markdown"
    assert artifact["value"]["final_markdown"] == answer.strip()
    assert artifact["value"]["summary"].endswith("…")


def test_code_fence_becomes_code_file() -> None:
    answer = """Here is the implementation:

```python
def greet(name: str) -> str:
    return f"hello {name}"
```
"""
    task = "Implement greet() in solution.py"
    assert classify_answer_format(answer, task=task) == "code_file"
    artifact = build_final_artifact_from_answer(answer, task=task)
    assert artifact is not None
    assert artifact["kind"] == "code_file"
    assert artifact["value"]["filename"] == "solution.py"
    assert "def greet" in artifact["value"]["content"]


def test_rlm_route_long_answer_prefers_markdown() -> None:
    answer = "A" * 900
    assert classify_answer_format(answer, routing_decision="url_document_rlm") == "markdown"


def test_structured_parameter_list_becomes_markdown() -> None:
    answer = "1. max_iterations: default 20\n2. max_llm_calls: default 50\n3. max_output_chars: default 10000"
    task = "List constructor parameters and their defaults."
    assert classify_answer_format(answer, task=task, routing_decision="url_document_rlm") == "markdown"
    artifact = build_final_artifact_from_answer(answer, task=task, routing_decision="url_document_rlm")
    assert artifact is not None
    assert artifact["kind"] == "markdown"
    assert "max_iterations" in artifact["value"]["final_markdown"]


def test_output_format_guidance_for_detailed_task() -> None:
    task = "Provide an in depth analysis of the runtime."
    assert infer_expected_output_format(task) == "markdown"
    guidance = output_format_guidance_for_task(task)
    assert "Markdown" in guidance


def test_output_format_guidance_for_code_task() -> None:
    task = "Implement greet() in solution.py"
    assert infer_expected_output_format(task) == "code_file"
    assert "fenced code block" in output_format_guidance_for_task(task)

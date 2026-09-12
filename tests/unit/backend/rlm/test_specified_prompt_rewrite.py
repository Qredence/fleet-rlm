"""Request-specified native Sub-LM prompt strings are restored before execute."""

from __future__ import annotations

import ast

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.events import RLMCode
from fleet_rlm.rlm.specified_prompt_rewrite import (
    apply_specified_sub_lm_prompts,
    normalize_action_code,
    specified_sub_lm_calls,
)

_MVP_REQUEST = """FIRST: execute the complete recursive Daytona MVP proof.
 Use exactly 3 iterations; do not improvise, explore, print for inspection,
 or retry. Ignore generic explore-first habits: this request is fully specified.
 1) The FIRST code cell must immediately call, once:
 iteration_token = issue_iteration_token();
 accumulator = [iteration_token]; print("FIRST_ITERATION_READY").
 2) The SECOND code cell must contain only these statements in this order
 (no parsing request, no regex, no extra logic):
 single_result = llm_query("Return exactly ROOT");
 batch_results = llm_query_batched(["Return exactly ALPHA", "Return exactly BETA",
 "Return exactly GAMMA"]);
 accumulator.extend([single_result, *batch_results]);
 verification = verify_semantic_work(iteration_token=iteration_token,
 single_result=single_result, batch_results=batch_results, accumulator=accumulator);
 checksum = verification["checksum"];
 content = f"single={single_result} batch={batch_results} checksum={checksum}";
 workspace_result = append_workspace_text(path="notes/findings.md", content=content);
 artifact_result = publish_workspace_artifact(path="notes/findings.md",
 kind="markdown", title="Findings"); print("SECOND_ITERATION_READY").
 3) Set non-empty string-only summary/findings; call exactly
 SUBMIT(answer=summary, findings=findings) with keywords. No fallback.
"""

_PARAPHRASED_CELL = """
single_result = llm_query('Summarize the Daytona MVP proof in one sentence.')
batch_results = llm_query_batched(['What is Daytona?', 'What is the Daytona MVP proof?'])
accumulator.extend([single_result, *batch_results])
verification = verify_semantic_work(
    iteration_token=iteration_token,
    single_result=single_result,
    batch_results=batch_results,
    accumulator=accumulator,
)
checksum = verification['checksum']
content = f'single={single_result} batch={batch_results} checksum={checksum}'
workspace_result = append_workspace_text(path='notes/findings.md', content=content)
artifact_result = publish_workspace_artifact(
    path='notes/findings.md',
    kind='markdown',
    title='Findings',
)
print('SECOND_ITERATION_READY')
"""

_PARAPHRASED_CELL_WITHOUT_EXTEND = """
single_result = llm_query('Summarize the Daytona MVP proof in one sentence.')
batch_results = llm_query_batched(['What is Daytona?', 'What is the Daytona MVP proof?'])
verification = verify_semantic_work(
    iteration_token=iteration_token,
    single_result=single_result,
    batch_results=batch_results,
    accumulator=accumulator,
)
checksum = verification['checksum']
content = f'single={single_result} batch={batch_results} checksum={checksum}'
workspace_result = append_workspace_text(path='notes/findings.md', content=content)
print('SECOND_ITERATION_READY')
"""


def _call_prompts(code: str, name: str) -> list[tuple[str, ...]]:
    tree = ast.parse(code)
    found: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != name:
            continue
        if not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            found.append((argument.value,))
        elif isinstance(argument, ast.List):
            found.append(
                tuple(
                    element.value
                    for element in argument.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                )
            )
    return found


def _is_accumulator_extend(statement: ast.stmt) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "accumulator"
        and node.func.attr == "extend"
        for node in ast.walk(statement)
    )


def test_specified_sub_lm_calls_extracts_root_alpha_beta_gamma() -> None:
    calls = specified_sub_lm_calls(_MVP_REQUEST)
    assert [(call.name, call.prompts) for call in calls] == [
        ("llm_query", ("Return exactly ROOT",)),
        ("llm_query_batched", ("Return exactly ALPHA", "Return exactly BETA", "Return exactly GAMMA")),
    ]


def test_paraphrased_cell_restores_request_specified_prompt_strings() -> None:
    rewritten = apply_specified_sub_lm_prompts(_MVP_REQUEST, _PARAPHRASED_CELL)
    assert _call_prompts(rewritten, "llm_query") == [("Return exactly ROOT",)]
    assert _call_prompts(rewritten, "llm_query_batched") == [
        ("Return exactly ALPHA", "Return exactly BETA", "Return exactly GAMMA")
    ]
    assert "accumulator.extend" in rewritten
    assert "append_workspace_text" in rewritten
    assert "Summarize the Daytona MVP proof" not in rewritten


def test_omitted_specified_accumulator_extend_is_restored_before_verify() -> None:
    rewritten = apply_specified_sub_lm_prompts(_MVP_REQUEST, _PARAPHRASED_CELL_WITHOUT_EXTEND)
    tree = ast.parse(rewritten)
    extend_at = next(index for index, statement in enumerate(tree.body) if _is_accumulator_extend(statement))
    verify_at = next(
        index
        for index, statement in enumerate(tree.body)
        if any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "verify_semantic_work"
            for node in ast.walk(statement)
        )
    )
    assert extend_at == verify_at - 1
    assert _call_prompts(rewritten, "llm_query") == [("Return exactly ROOT",)]


def test_omitted_specified_accumulator_extend_stays_in_verify_control_flow() -> None:
    generated = """if ready:
    single_result = llm_query("different")
    batch_results = llm_query_batched(["different"])
    verification = verify_semantic_work(single_result, batch_results, accumulator)
"""
    rewritten = apply_specified_sub_lm_prompts(_MVP_REQUEST, generated)
    tree = ast.parse(rewritten)
    branch = tree.body[0]
    assert isinstance(branch, ast.If)
    extend_at = next(index for index, statement in enumerate(branch.body) if _is_accumulator_extend(statement))
    verify_at = next(
        index
        for index, statement in enumerate(branch.body)
        if any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "verify_semantic_work"
            for node in ast.walk(statement)
        )
    )
    assert extend_at == verify_at - 1

    namespace = {
        "ready": True,
        "accumulator": [],
        "llm_query": lambda prompt: prompt,
        "llm_query_batched": lambda prompts: prompts,
        "verify_semantic_work": lambda *_args: {"verified": True},
    }
    exec(rewritten, namespace)
    assert namespace["accumulator"] == [
        "Return exactly ROOT",
        "Return exactly ALPHA",
        "Return exactly BETA",
        "Return exactly GAMMA",
    ]


def test_exact_prompts_without_extend_still_restore_specified_extend() -> None:
    generated = (
        'single_result = llm_query("Return exactly ROOT")\n'
        'batch_results = llm_query_batched(["Return exactly ALPHA", "Return exactly BETA", "Return exactly GAMMA"])\n'
        "verification = verify_semantic_work(iteration_token=iteration_token, "
        "single_result=single_result, batch_results=batch_results, accumulator=accumulator)\n"
    )
    rewritten = apply_specified_sub_lm_prompts(_MVP_REQUEST, generated)
    tree = ast.parse(rewritten)
    assert any(_is_accumulator_extend(statement) for statement in tree.body)


def test_request_name_prompts_restore_specified_literals() -> None:
    generated = (
        "single_result = llm_query(request)\n"
        "batch_results = llm_query_batched([request])\n"
        "verification = verify_semantic_work(iteration_token=iteration_token, "
        "single_result=single_result, batch_results=batch_results, accumulator=accumulator)\n"
        'print("SECOND_ITERATION_READY")\n'
    )
    rewritten = apply_specified_sub_lm_prompts(_MVP_REQUEST, generated)
    assert _call_prompts(rewritten, "llm_query") == [("Return exactly ROOT",)]
    assert _call_prompts(rewritten, "llm_query_batched") == [
        ("Return exactly ALPHA", "Return exactly BETA", "Return exactly GAMMA")
    ]
    assert "llm_query(request)" not in rewritten
    assert "accumulator.extend" in rewritten


def test_unspecified_request_leaves_generated_llm_query_unchanged() -> None:
    generated = "single_result = llm_query('Summarize the notes.')\n_out = single_result"
    assert apply_specified_sub_lm_prompts("Please inspect notes/findings.md and summarize.", generated) == generated


def test_submit_only_cell_is_unchanged_when_request_specifies_prompts() -> None:
    generated = "SUBMIT(answer=summary, findings=findings)"
    assert apply_specified_sub_lm_prompts(_MVP_REQUEST, generated) == generated


def test_interpreter_execute_observes_rewritten_rlm_code() -> None:
    captured: list[str] = []

    def llm_query(prompt: str) -> str:
        captured.append(prompt)
        return prompt

    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        tools={"llm_query": llm_query},
    )
    interpreter.bind_observer(observed.append, max_chars=4_000)
    interpreter.bind_turn_request(_MVP_REQUEST)
    interpreter.execute("single_result = llm_query('Summarize the Daytona MVP proof.')\n_out = single_result")


def test_interpreter_execute_rewrites_request_name_llm_query() -> None:
    captured: list[str] = []

    def llm_query(prompt: str) -> str:
        captured.append(prompt)
        return prompt

    observed: list[object] = []
    interpreter = DaytonaCodeInterpreter(
        backend=InProcessInterpreterBackend(),
        tools={"llm_query": llm_query},
    )
    interpreter.bind_observer(observed.append, max_chars=4_000)
    interpreter.bind_turn_request(_MVP_REQUEST)
    interpreter.execute("single_result = llm_query(request)\n_out = single_result")

    codes = [item.code for item in observed if isinstance(item, RLMCode)]
    assert codes
    assert "Return exactly ROOT" in codes[0]
    assert "llm_query(request)" not in codes[0]
    assert captured == ["Return exactly ROOT"]

    codes = [item.code for item in observed if isinstance(item, RLMCode)]
    assert codes
    assert "Return exactly ROOT" in codes[0]
    assert "Summarize the Daytona MVP proof" not in codes[0]
    assert captured == ["Return exactly ROOT"]


def test_normalize_action_code_treats_quote_style_as_equivalent() -> None:
    double = 'single_result = llm_query("Return exactly ROOT")'
    single = "single_result = llm_query('Return exactly ROOT')"
    assert normalize_action_code(double) == normalize_action_code(single)

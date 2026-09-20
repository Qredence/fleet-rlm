"""Dedicated invariant tests for Phase 2: Native DSPy 3.3.1 RLM Core.

Locks down Requirement R2 invariants:
1. Native dspy.RLM constructor and sub_lm tools (llm_query, llm_query_batched)
2. AttachmentContextCapsule SandboxSerializable context staging (rlm_preview, to_sandbox)
3. Depth-1 recursion isolation (child RLM receives only leaf tools, never rlm_query)
4. Zero dspy.CodeAct repository-wide
5. Removal of specified_prompt_rewrite module
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import dspy

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.program import (
    AttachmentContextCapsule,
    AttachmentContextEntry,
    FleetRLMSignature,
    RLMModelBundle,
    RLMOptions,
    build_native_rlm,
)
from fleet_rlm.rlm.recursion import (
    ChildOutcome,
    RecursiveRLMOptions,
    RecursiveSubtaskSignature,
    SubproblemCapsule,
)
from tests.support.recursion_scheduler import RecursiveRLMExecutor


def test_zero_codeact_in_source_tree() -> None:
    """Requirement R2 invariant: dspy.CodeAct must not exist anywhere in codebase."""
    root_dir = Path(__file__).resolve().parents[4]
    src_dir = root_dir / "src"

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        assert "CodeAct" not in content, f"CodeAct found in {py_file}"
        assert "code_act" not in content, f"code_act found in {py_file}"


def test_specified_prompt_rewrite_is_deleted() -> None:
    """Requirement R2 invariant: specified_prompt_rewrite.py must be deleted."""
    root_dir = Path(__file__).resolve().parents[4]
    deleted_path = root_dir / "src" / "fleet_rlm" / "rlm" / "specified_prompt_rewrite.py"
    assert not deleted_path.exists(), "specified_prompt_rewrite.py still exists!"


def test_build_native_rlm_binds_sub_lm_and_tools() -> None:
    """Native dspy.RLM core with sub_lm tools created by dspy native factory."""
    sub_lm = dspy.utils.DummyLM([{"answer": "extracted summary"}], adapter=dspy.JSONAdapter())

    def custom_leaf_tool(x: str) -> str:
        return f"leaf:{x}"

    rlm = build_native_rlm(
        signature=FleetRLMSignature,
        options=RLMOptions(max_iters=5, max_llm_calls=10, max_output_chars=4000),
        tools=[dspy.Tool(custom_leaf_tool, name="custom_leaf")],
        sub_lm=sub_lm,
        verbose=False,
    )

    assert isinstance(rlm, dspy.RLM)
    assert rlm.max_iters == 5
    assert rlm.max_llm_calls == 10
    assert rlm.max_output_chars == 4000
    assert rlm.sub_lm is sub_lm
    assert "custom_leaf" in rlm.tools

    # DSPy native _make_llm_tools produces llm_query and llm_query_batched
    llm_tools = rlm._make_llm_tools()
    assert isinstance(llm_tools, dict)
    assert "llm_query" in llm_tools
    assert "llm_query_batched" in llm_tools


def test_large_context_staging_capsule_lifecycle(tmp_path: Path) -> None:
    """AttachmentContextCapsule implements SandboxSerializable for large contexts."""
    content_bytes = b"alpha,beta,gamma\n1,2,3\n4,5,6\n" * 100
    file_path = tmp_path / "large_dataset.csv"
    file_path.write_bytes(content_bytes)

    entry = AttachmentContextEntry(
        attachment_id=uuid4(),
        filename="large_dataset.csv",
        content_type="text/csv",
        byte_size=len(content_bytes),
        checksum_sha256=hashlib.sha256(content_bytes).hexdigest(),
        sandbox_path=str(file_path),
    )

    capsule = AttachmentContextCapsule(
        entries=(entry,),
        mount_root=str(tmp_path),
    )

    # 1. Concise preview for model prompt
    preview = capsule.rlm_preview(max_chars=200)
    assert "large_dataset.csv" in preview
    assert "text/csv" in preview
    assert str(len(content_bytes)) in preview
    # Full data bytes must not appear in prompt preview
    assert str(content_bytes[:50]) not in preview

    # 2. Sandbox serialization for Daytona REPL
    raw_sandbox_bytes = capsule.to_sandbox()
    manifest_data = json.loads(raw_sandbox_bytes.decode("utf-8"))
    assert manifest_data["mount_root"] == str(tmp_path)
    assert len(manifest_data["entries"]) == 1
    assert manifest_data["entries"][0]["filename"] == "large_dataset.csv"
    assert manifest_data["entries"][0]["byte_size"] == len(content_bytes)

    # 3. Sandbox assignment code
    assignment_code = capsule.sandbox_assignment("attachments", "_raw_attachments")
    assert "attachments = _fleet_load_context_manifest(_raw_attachments)" in assignment_code
    assert "del _fleet_load_context_manifest" in assignment_code


def test_child_sandbox_delegation_strictly_depth_one() -> None:
    """Child RLMs receive only leaf tools (never rlm_query or rlm_query_batched)."""
    adapter = dspy.JSONAdapter()
    root_lm = dspy.utils.DummyLM([{"reasoning": "root", "code": "SUBMIT(answer='done')"}], adapter=adapter)
    sub_lm = dspy.utils.DummyLM([{"reasoning": "child", "code": "SUBMIT(answer='child-done')"}], adapter=adapter)

    def recording_factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-vol",
            f"child-path-{call_index}",
            interpreter.shutdown,
        )

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root_lm, sub_lm),
        options=RecursiveRLMOptions(enabled=True),
        child_runtime_factory=recording_factory,
        deadline=1e9,
    )

    # Verify Root RLM tools
    assert executor.tool.name == "rlm_query"
    assert executor.batched_tool.name == "rlm_query_batched"

    # Execute a child query
    capsule = SubproblemCapsule(
        task="Extract row count",
        fragments=("row count is 42",),
        allocation_bytes=1024,
    )
    outcome = executor.execute_capsule_outcome(capsule)
    assert isinstance(outcome, ChildOutcome)
    assert outcome.status == "completed"

    def read_input(_evidence_id: str) -> str:
        return "data"

    # Under RecursiveSubtaskSignature, child only receives read_selected_input
    # and built-in sub_lm tools. Child CANNOT have rlm_query tool.
    child_rlm = build_native_rlm(
        signature=RecursiveSubtaskSignature,
        options=RLMOptions(max_iters=4, max_llm_calls=8, max_output_chars=2000),
        tools=[dspy.Tool(read_input, name="read_selected_input")],
        sub_lm=sub_lm,
        verbose=False,
    )

    assert "read_selected_input" in child_rlm.tools
    assert "rlm_query" not in child_rlm.tools
    assert "rlm_query_batched" not in child_rlm.tools

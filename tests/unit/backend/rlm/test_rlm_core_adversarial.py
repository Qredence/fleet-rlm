"""Adversarial and stress contracts for the native DSPy RLM core.

Empirical verification of:
1. AttachmentContextCapsule boundaries: count limits, payload stress, empty manifests,
   binary payloads, path traversal & mount boundaries.
2. Depth-1 recursion boundaries: child cannot access rlm_query/rlm_query_batched,
   leaf llm_query tool execution, child lease cleanup.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.runtime import ChildRuntimeLease
from fleet_rlm.rlm.program import (
    _MAX_CONTEXT_ATTACHMENT_COUNT,
    _MAX_PREVIEW_CHARS,
    AttachmentContextCapsule,
    AttachmentContextEntry,
    RLMModelBundle,
    RLMOptions,
    _materialize_context_manifest,
)
from fleet_rlm.rlm.recursion import (
    RecursiveRLMOptions,
    RecursiveSubtaskSignature,
)
from tests.support.native_rlm import build_native_rlm_for_test
from tests.support.recursion_scheduler import RecursiveRLMExecutor


def _create_entry(
    tmp_path: Path,
    name: str = "doc.txt",
    content: bytes = b"sample text",
    subpath: str = "",
) -> AttachmentContextEntry:
    target_dir = tmp_path / subpath if subpath else tmp_path
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / name
    file_path.write_bytes(content)
    return AttachmentContextEntry(
        attachment_id=uuid4(),
        filename=name,
        content_type="text/plain",
        byte_size=len(content),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
        sandbox_path=str(file_path),
    )


def test_capsule_attachment_count_boundaries(tmp_path: Path) -> None:
    """AttachmentContextCapsule must accept 1..32 attachments and reject 0 or 33."""
    # 0 entries must be rejected
    with pytest.raises(ValueError, match="attachment context count is invalid"):
        AttachmentContextCapsule(entries=(), mount_root=str(tmp_path))

    # Exactly 1 entry is valid
    e1 = _create_entry(tmp_path, "f1.txt")
    cap1 = AttachmentContextCapsule(entries=(e1,), mount_root=str(tmp_path))
    assert len(cap1.entries) == 1

    # Exactly 32 entries (max limit) is valid
    entries_32 = [_create_entry(tmp_path, f"f_{i}.txt") for i in range(32)]
    cap32 = AttachmentContextCapsule(entries=tuple(entries_32), mount_root=str(tmp_path))
    assert len(cap32.entries) == _MAX_CONTEXT_ATTACHMENT_COUNT

    # 33 entries must be rejected
    extra = _create_entry(tmp_path, "extra.txt")
    with pytest.raises(ValueError, match="attachment context count is invalid"):
        AttachmentContextCapsule(entries=tuple([*entries_32, extra]), mount_root=str(tmp_path))


def test_capsule_entry_validation_stress(tmp_path: Path) -> None:
    """Validate entry fields against malicious, zero, and boundary inputs."""
    valid_sha = hashlib.sha256(b"data").hexdigest()

    # Empty file / byte_size=0
    with pytest.raises(ValueError, match="attachment byte size is invalid"):
        AttachmentContextEntry(
            attachment_id=uuid4(),
            filename="empty.txt",
            content_type="text/plain",
            byte_size=0,
            checksum_sha256=valid_sha,
            sandbox_path=str(tmp_path / "empty.txt"),
        )

    # Negative byte_size
    with pytest.raises(ValueError, match="attachment byte size is invalid"):
        AttachmentContextEntry(
            attachment_id=uuid4(),
            filename="neg.txt",
            content_type="text/plain",
            byte_size=-1,
            checksum_sha256=valid_sha,
            sandbox_path=str(tmp_path / "neg.txt"),
        )

    # Empty filename
    with pytest.raises(ValueError, match="attachment filename is invalid"):
        AttachmentContextEntry(
            attachment_id=uuid4(),
            filename="",
            content_type="text/plain",
            byte_size=10,
            checksum_sha256=valid_sha,
            sandbox_path=str(tmp_path / "f.txt"),
        )

    # Filename exactly 255 chars is valid
    valid_255_entry = AttachmentContextEntry(
        attachment_id=uuid4(),
        filename="a" * 255,
        content_type="text/plain",
        byte_size=10,
        checksum_sha256=valid_sha,
        sandbox_path=str(tmp_path / "f.txt"),
    )
    assert len(valid_255_entry.filename) == 255

    # Filename 256 chars rejected
    with pytest.raises(ValueError, match="attachment filename is invalid"):
        AttachmentContextEntry(
            attachment_id=uuid4(),
            filename="a" * 256,
            content_type="text/plain",
            byte_size=10,
            checksum_sha256=valid_sha,
            sandbox_path=str(tmp_path / "f.txt"),
        )

    # SHA256 length != 64 or invalid hex
    with pytest.raises(ValueError, match="attachment checksum is invalid"):
        AttachmentContextEntry(
            attachment_id=uuid4(),
            filename="bad_sha.txt",
            content_type="text/plain",
            byte_size=10,
            checksum_sha256="a" * 63,
            sandbox_path=str(tmp_path / "f.txt"),
        )

    with pytest.raises(ValueError, match="attachment checksum is invalid"):
        AttachmentContextEntry(
            attachment_id=uuid4(),
            filename="bad_sha.txt",
            content_type="text/plain",
            byte_size=10,
            checksum_sha256="g" * 64,
            sandbox_path=str(tmp_path / "f.txt"),
        )

    # Relative sandbox path
    with pytest.raises(ValueError, match="attachment sandbox path is invalid"):
        AttachmentContextEntry(
            attachment_id=uuid4(),
            filename="rel.txt",
            content_type="text/plain",
            byte_size=10,
            checksum_sha256=valid_sha,
            sandbox_path="relative/path.txt",
        )


def test_capsule_preview_and_serialization_privacy(tmp_path: Path) -> None:
    """Prompt preview and to_sandbox must never expose raw contents."""
    secret_bytes = b"SUPER_SECRET_TOKEN_DO_NOT_LEAK_INTO_PROMPT_12345"
    e = _create_entry(tmp_path, "confidential.log", secret_bytes)
    capsule = AttachmentContextCapsule(entries=(e,), mount_root=str(tmp_path))

    # 1. Preview checks
    preview = capsule.rlm_preview()
    assert secret_bytes.decode() not in preview
    assert "confidential.log" in preview
    assert str(len(secret_bytes)) in preview

    # Clamping behavior
    assert len(capsule.rlm_preview(max_chars=15)) == 15
    assert len(capsule.rlm_preview(max_chars=0)) == 1
    assert len(capsule.rlm_preview(max_chars=10000)) <= _MAX_PREVIEW_CHARS

    # 2. Serialization checks
    sandbox_payload = capsule.to_sandbox()
    assert secret_bytes not in sandbox_payload
    data = json.loads(sandbox_payload.decode("utf-8"))
    assert data["mount_root"] == str(tmp_path)
    assert len(data["entries"]) == 1
    assert data["entries"][0]["byte_size"] == len(secret_bytes)


def test_volume_mount_containment_and_path_traversal(tmp_path: Path) -> None:
    """Mount containment checks must reject paths outside the mount."""
    valid_sha = hashlib.sha256(b"test").hexdigest()

    # Exact mount root path is rejected (cannot be a file)
    with pytest.raises(ValueError, match="outside the mounted Volume"):
        AttachmentContextCapsule(
            entries=(
                AttachmentContextEntry(
                    attachment_id=uuid4(),
                    filename="root.txt",
                    content_type="text/plain",
                    byte_size=4,
                    checksum_sha256=valid_sha,
                    sandbox_path=str(tmp_path),
                ),
            ),
            mount_root=str(tmp_path),
        )

    # Completely outside mount path is rejected
    with pytest.raises(ValueError, match="outside the mounted Volume"):
        AttachmentContextCapsule(
            entries=(
                AttachmentContextEntry(
                    attachment_id=uuid4(),
                    filename="outside.txt",
                    content_type="text/plain",
                    byte_size=4,
                    checksum_sha256=valid_sha,
                    sandbox_path="/var/log/outside.txt",
                ),
            ),
            mount_root=str(tmp_path),
        )

    # Path traversal demonstration:
    # PurePosixPath does lexical comparison and does not resolve '..'.
    # Demonstrating that PurePosixPath.is_relative_to alone allows traversal:
    traversal_path = PurePosixPath(f"{tmp_path}/subdir/../../etc/passwd")
    mount = PurePosixPath(str(tmp_path))
    assert traversal_path.is_relative_to(mount) is True, "PurePosixPath exhibits un-normalized prefix matching"
    # But normalized path is outside mount:
    normed_path = os.path.normpath(str(traversal_path))
    assert not normed_path.startswith(str(tmp_path)), "Normalized path escapes the mount"


def test_binary_attachment_in_process_fallback(tmp_path: Path) -> None:
    """Binary files with non-UTF8 bytes and null bytes are handled by in-process loader."""
    binary_data = b"\x80\xff\xfe\x00\x01\x02\x03\x04\x81\x82"
    entry = _create_entry(tmp_path, "firmware.bin", binary_data)
    capsule = AttachmentContextCapsule(entries=(entry,), mount_root=str(tmp_path))

    raw_manifest = capsule.to_sandbox()
    manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()

    values, accesses = _materialize_context_manifest(
        raw_manifest,
        trusted_mount_root=str(tmp_path),
        expected_manifest_sha256=manifest_sha256,
    )

    assert len(values) == 1
    assert values[0]["data"] == binary_data
    assert values[0]["encoding"] == "bytes"
    assert accesses == (str(entry.attachment_id),)


def test_child_rlm_cannot_recurse_empirically() -> None:
    """Empirically verify that child RLM cannot call rlm_query or rlm_query_batched."""
    adapter = dspy.JSONAdapter()

    # Child code attempts to call rlm_query
    child_code = """
try:
    res = rlm_query(task='nested', inputs=[])
    ans = f"rec_success:{res}"
except Exception as exc:
    ans = f"rec_blocked:{type(exc).__name__}:{exc}"
SUBMIT(answer=ans, evidence=[], gaps=[], result_files=[])
"""
    # Child reasoning uses root_lm of the forked model bundle
    root_lm = dspy.utils.DummyLM([{"reasoning": "child probe", "code": child_code}], adapter=adapter)
    sub_lm = dspy.utils.DummyLM([{"answer": "sub-lm"}], adapter=adapter)

    closed_leases: list[str] = []

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        del profile
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def _cleanup() -> None:
            closed_leases.append(f"child-{call_index}")
            interpreter.shutdown()

        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-vol",
            f"child-path-{call_index}",
            _cleanup,
        )

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root_lm, sub_lm),
        options=RecursiveRLMOptions(enabled=True),
        child_runtime_factory=factory,
        deadline=1e9,
    )

    outcome = executor._call_child(task="Test nested recursion denial", inputs=[], context="probe fragment")
    assert outcome["status"] == "completed"
    assert "rec_blocked:NameError:name 'rlm_query' is not defined" in outcome["answer"]
    assert len(closed_leases) == 1 and closed_leases[0] == "child-1", "Child lease was closed before outcome returned"


def test_child_rlm_tool_namespace_isolation() -> None:
    """Child RLM constructor receives leaf tools only, never rlm_query or rlm_query_batched."""
    sub_lm = dspy.utils.DummyLM([{"answer": "leaf result"}], adapter=dspy.JSONAdapter())

    child_rlm = build_native_rlm_for_test(
        signature=RecursiveSubtaskSignature,
        options=RLMOptions(max_iters=3, max_llm_calls=6, max_output_chars=1000),
        sub_lm=sub_lm,
        verbose=False,
    )

    assert "rlm_query" not in child_rlm.tools
    assert "rlm_query_batched" not in child_rlm.tools

    # DSPy native sub_lm tools are present for leaf semantic filtering
    llm_tools = child_rlm._make_llm_tools()
    assert "llm_query" in llm_tools
    assert "llm_query_batched" in llm_tools


def test_child_rlm_executes_leaf_sub_lm_query() -> None:
    """Child RLM can execute leaf sub_lm queries for semantic filtering."""
    adapter = dspy.JSONAdapter()

    child_code = """
summary = llm_query(prompt='Extract summary')
SUBMIT(answer=f"child_filtered:{summary}", evidence=[], gaps=[], result_files=[])
"""
    root_lm = dspy.utils.DummyLM([{"reasoning": "child query", "code": child_code}], adapter=adapter)
    sub_lm = dspy.utils.DummyLM([{"summary": "key finding 123"}], adapter=adapter)

    def factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        del profile
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
        child_runtime_factory=factory,
        deadline=1e9,
    )

    outcome = executor._call_child(task="Filter with leaf sub-LM", inputs=[], context="data chunk")
    assert outcome["status"] == "completed"
    assert "child_filtered" in outcome["answer"]
    assert "key finding 123" in outcome["answer"]

"""Workspace Memory fail-soft degradation classification + bounded diagnostics (P31).

Degraded optional Memory context must never block a Run, but every fallback is
now classified and emitted as one bounded, sanitized diagnostic.
"""

from __future__ import annotations

import logging
from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.daytona import workspace_memory
from fleet_rlm.daytona.memory_diagnostics import (
    MemoryFailureCategory,
    MemoryInvariantError,
    MemoryPayloadError,
    classify_memory_failure,
    record_memory_degradation,
)
from fleet_rlm.files.memory_models import (
    WORKSPACE_MEMORY_HEADER,
    WorkspaceMemoryListResult,
    WorkspaceMemoryReadResult,
    WorkspaceMemoryStoreUnavailableError,
)
from fleet_rlm.files.volume_paths import VolumePaths

HEADER = WORKSPACE_MEMORY_HEADER + "\n"


class LocalProcess:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def code_run(self, code: str, **_kwargs):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output), suppress(SystemExit):
            exec(code, {})
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


def _store(tmp_path: Path, *, max_bytes: int = 262_144):
    root = tmp_path / "volume"
    root.mkdir()
    store = workspace_memory.DaytonaWorkspaceMemoryStore(
        SimpleNamespace(process=LocalProcess()),
        volume_paths=VolumePaths.from_mount(str(root)),
        max_upload_bytes=max_bytes,
    )
    return store, root


def _write_store_file(root: Path, content: str) -> Path:
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    target = memory_dir / "MEMORIES.md"
    target.write_text(content, encoding="utf-8")
    return target


@pytest.fixture
def annotated(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture tracing annotations without any active Turn span."""
    captured: list[dict[str, object]] = []
    from fleet_rlm.observability import turn_tracing

    monkeypatch.setattr(turn_tracing, "annotate_turn_attributes", lambda attrs: captured.append(dict(attrs)))
    return captured


def _degraded_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records if r.name == "fleet_rlm.daytona.memory_diagnostics" and r.levelno >= logging.WARNING
    ]


# -- classification at the adapter seam (T1) ---------------------------------


def test_normalization_failure_degrades_to_recency_digest_with_diagnostic(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    annotated: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, root = _store(tmp_path)
    secret_learning = "TOPSECRET normalizable learning should never appear in telemetry"
    _write_store_file(root, HEADER + f"- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: {secret_learning}\n")

    import fleet_rlm.files.memory_tools as memory_tools

    def explode(_query: object) -> str:
        raise memory_tools.MemoryToolError("invalid_entry", "Workspace Memory entry is invalid")

    monkeypatch.setattr(memory_tools, "normalize_memory_search_query", explode)
    with caplog.at_level(logging.WARNING):
        digest = workspace_memory.read_workspace_memory_injection_digest(store, request="anything at all")

    # Same visible fallback as before: the complete recency-only digest.
    assert secret_learning in digest
    warnings = _degraded_warnings(caplog)
    assert len(warnings) == 1
    assert "category=normalization" in warnings[0].getMessage()
    assert "operation=normalize_query" in warnings[0].getMessage()
    assert "outcome=recency_only_digest" in warnings[0].getMessage()
    assert secret_learning not in warnings[0].getMessage()
    assert len(annotated) == 1
    assert annotated[0]["fleet.memory_degradation.category"] == "normalization"
    assert secret_learning not in str(annotated[0])


def test_relevance_search_failure_falls_back_with_diagnostic(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    annotated: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, root = _store(tmp_path)
    _write_store_file(root, HEADER + "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: durable lesson\n")

    import fleet_rlm.files.memory_tools as memory_tools

    def explode(_store: object, *, normalized_query: str) -> object:
        del normalized_query
        raise memory_tools.MemoryToolError("invalid_entry", "Workspace Memory entry is invalid")

    monkeypatch.setattr(memory_tools, "search_workspace_memory_entries", explode)
    with caplog.at_level(logging.WARNING):
        degraded = workspace_memory.read_workspace_memory_injection_digest(store, request="durable")

    assert "durable lesson" in degraded
    warnings = _degraded_warnings(caplog)
    assert [r.getMessage() for r in warnings] == [warnings[0].getMessage()]
    assert len(warnings) == 1
    assert "category=search_failure" in warnings[0].getMessage()
    assert "operation=relevance_search" in warnings[0].getMessage()
    assert len(annotated) == 1
    assert annotated[0]["fleet.memory_degradation.category"] == "search_failure"


def test_unexpected_search_defect_is_classified_explicitly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = _store(tmp_path)
    _write_store_file(root, HEADER + "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: durable lesson\n")

    import fleet_rlm.files.memory_tools as memory_tools

    def explode(_store: object, *, normalized_query: str) -> object:
        del normalized_query
        raise RuntimeError("boom internal defect")

    monkeypatch.setattr(memory_tools, "search_workspace_memory_entries", explode)
    with caplog.at_level(logging.WARNING):
        workspace_memory.read_workspace_memory_injection_digest(store, request="durable")

    warnings = _degraded_warnings(caplog)
    assert len(warnings) == 1
    assert "category=unexpected_internal" in warnings[0].getMessage()
    assert "cause_type=RuntimeError" in warnings[0].getMessage()


def test_provider_outage_is_classified_and_still_fail_soft(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleet_rlm.daytona.workspace_agent import WorkspaceAgentStorageError

    store, _root = _store(tmp_path)

    def down(*_args, **_kwargs):
        raise WorkspaceAgentStorageError("simulated outage; do not echo payloads")

    monkeypatch.setattr(workspace_memory, "run_workspace_agent", down)
    with caplog.at_level(logging.WARNING), pytest.raises(WorkspaceMemoryStoreUnavailableError):
        workspace_memory.read_workspace_memory_injection_digest(store, request="anything")

    # The digest seam still raises to its caller; classification is attached
    # to the exception chain for the outer fail-soft seam to emit.
    exc = _capture(store)
    assert isinstance(exc, WorkspaceMemoryStoreUnavailableError)
    category, cause_type = classify_memory_failure(exc, operation="injection_digest")
    assert category == MemoryFailureCategory.PROVIDER_UNAVAILABLE
    assert cause_type == "WorkspaceAgentStorageError"
    assert "simulated outage" not in " ".join(r.getMessage() for r in caplog.records)


def _capture(store) -> BaseException:
    try:
        store.read_tail(byte_budget=128)
    except Exception as exc:
        return exc
    raise AssertionError("expected read_tail to fail")


def test_corrupt_payload_is_distinct_from_provider_outage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _root = _store(tmp_path)

    def corrupt(*_args, **_kwargs):
        raise MemoryPayloadError("invalid memory response")

    monkeypatch.setattr(store, "_checked_tail_payload", corrupt)
    exc = _capture(store)
    assert isinstance(exc, WorkspaceMemoryStoreUnavailableError)
    category, cause_type = classify_memory_failure(exc, operation="injection_digest")
    assert category == MemoryFailureCategory.CORRUPT_RECORD_SET
    assert cause_type == "MemoryPayloadError"


def test_unexpected_read_defect_is_unexpected_internal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _root = _store(tmp_path)

    def boom(*_args, **_kwargs):
        raise TypeError("programming defect canary")

    monkeypatch.setattr(store, "_checked_tail_payload", boom)
    exc = _capture(store)
    category, cause_type = classify_memory_failure(exc, operation="injection_digest")
    assert category == MemoryFailureCategory.UNEXPECTED_INTERNAL
    assert cause_type == "TypeError"


def test_duplicate_stable_ids_fail_closed_with_invariant_category(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, annotated: list[dict[str, object]]
) -> None:
    store, root = _store(tmp_path)
    duplicate_rows = (
        "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: first copy\n"
        "- [2026-07-27T11:15:05Z] **General** <!-- id:aaaa0001 -->: second copy\n"
    )
    _write_store_file(root, HEADER + duplicate_rows)

    # Mutation/list invariants remain strict: list_entries still fails closed.
    with pytest.raises(MemoryInvariantError) as excinfo:
        store.list_entries(limit=10)
    assert isinstance(excinfo.value, WorkspaceMemoryStoreUnavailableError)

    # The same store fed to the Turn digest degrades to the recency-only
    # fallback with one bounded invariant-violation diagnostic.
    with caplog.at_level(logging.WARNING):
        digest = workspace_memory.read_workspace_memory_injection_digest(store, request="copy")

    # Tolerant read keeps the first (graph-valid) row; the forged duplicate
    # stays a malformed-tolerant skip exactly as before P31.
    assert "first copy" in digest
    assert "second copy" not in digest
    warnings = _degraded_warnings(caplog)
    assert len(warnings) == 1
    assert "category=invariant_violation" in warnings[0].getMessage()
    assert "operation=relevance_search" in warnings[0].getMessage()
    assert "aaaa0001" not in warnings[0].getMessage()
    assert annotated[0]["fleet.memory_degradation.category"] == "invariant_violation"


def test_legacy_migration_failure_is_its_own_category(tmp_path: Path) -> None:
    root = tmp_path / "volume"
    root.mkdir()
    # a directory named MEMORIES.md is a legacy-store shape failure
    (root / "MEMORIES.md").mkdir()
    store = workspace_memory.DaytonaWorkspaceMemoryStore(
        SimpleNamespace(process=LocalProcess()),
        volume_paths=VolumePaths.from_mount(str(root)),
        max_upload_bytes=262_144,
    )

    exc = _capture(store)
    assert isinstance(exc, WorkspaceMemoryStoreUnavailableError)
    category, cause_type = classify_memory_failure(exc, operation="injection_digest")
    assert category == MemoryFailureCategory.LEGACY_MIGRATION
    assert cause_type == "MemoryMigrationError"


# -- emission contract (T2) ----------------------------------------------------


def test_successful_preparation_emits_no_diagnostics(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, annotated: list[dict[str, object]]
) -> None:
    store, root = _store(tmp_path)
    _write_store_file(root, HEADER + "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: durable lesson\n")

    with caplog.at_level(logging.WARNING):
        digest = workspace_memory.read_workspace_memory_injection_digest(store, request="durable")

    assert "durable lesson" in digest
    assert _degraded_warnings(caplog) == []
    assert annotated == []


def test_recency_fallback_without_matches_is_not_a_degradation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, annotated: list[dict[str, object]]
) -> None:
    store, root = _store(tmp_path)
    _write_store_file(root, HEADER + "- [2026-07-27T11:14:05Z] **General** <!-- id:aaaa0001 -->: durable lesson\n")

    with caplog.at_level(logging.WARNING):
        digest = workspace_memory.read_workspace_memory_injection_digest(store, request="zzz-no-lexical-overlap")

    assert "durable lesson" in digest
    assert _degraded_warnings(caplog) == []
    assert annotated == []


def test_diagnostics_survive_tracing_and_logging_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.observability import turn_tracing

    def explode(attrs: dict[str, object]) -> None:
        del attrs
        raise RuntimeError("tracing sink down")

    monkeypatch.setattr(turn_tracing, "annotate_turn_attributes", explode)
    degradation = record_memory_degradation(
        WorkspaceMemoryStoreUnavailableError("outage"),
        operation="injection_digest",
        fallback_outcome="no_memory_injection",
    )
    assert degradation.category == MemoryFailureCategory.PROVIDER_UNAVAILABLE
    assert degradation.fallback_outcome == "no_memory_injection"
    assert degradation.runtime == "daytona"


def _chained_store_error(cause: BaseException) -> WorkspaceMemoryStoreUnavailableError:
    try:
        try:
            raise cause
        except type(cause):
            raise WorkspaceMemoryStoreUnavailableError() from cause
    except WorkspaceMemoryStoreUnavailableError as exc:
        return exc


def test_emitted_fields_are_bounded_and_sanitized(annotated: list[dict[str, object]]) -> None:
    record_memory_degradation(
        _chained_store_error(OSError("provider said: hunter2 password")),
        operation="injection_digest",
        fallback_outcome="no_memory_injection",
    )
    assert len(annotated) == 1
    attrs = annotated[0]
    assert set(attrs) == {
        "fleet.memory_degradation.category",
        "fleet.memory_degradation.operation",
        "fleet.memory_degradation.runtime",
        "fleet.memory_degradation.cause_type",
        "fleet.memory_degradation.fallback_outcome",
    }
    assert "hunter2" not in str(attrs)
    assert all(len(str(v)) <= 32 for v in attrs.values())


@pytest.mark.asyncio
async def test_turn_preparation_seam_degrades_silently_but_observably(caplog: pytest.LogCaptureFixture) -> None:
    from fleet_rlm.daytona.run_environment import _prepare_memory_digest

    class EmptyStore:
        def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
            return WorkspaceMemoryReadResult(
                content="", truncated=False, bytes_returned=0, byte_budget=byte_budget, total_bytes=0, warnings=0
            )

        def list_entries(self, **kwargs: object) -> WorkspaceMemoryListResult:
            del kwargs
            return WorkspaceMemoryListResult(entries=(), truncated=False, next_cursor=None, warnings=0)

    class FailingCatalogStore(EmptyStore):
        def list_entries(self, **kwargs: object) -> WorkspaceMemoryListResult:
            del kwargs
            raise WorkspaceMemoryStoreUnavailableError("catalog down")

    with caplog.at_level(logging.WARNING):
        # Healthy path: recency-only digest, no diagnostics.
        assert await _prepare_memory_digest(EmptyStore(), request="nothing persisted") == ""
        assert _degraded_warnings(caplog) == []

        # Failing relevance search: same visible behavior as before (single
        # fallback digest) plus exactly one classified diagnostic.
        digest = await _prepare_memory_digest(FailingCatalogStore(), request="persisted durable memory")
        assert digest == ""
        warnings = _degraded_warnings(caplog)
        assert len(warnings) == 1
        assert "category=provider_unavailable" in warnings[0].getMessage()
        assert "operation=relevance_search" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_turn_preparation_outer_seam_classes_digest_failures(
    caplog: pytest.LogCaptureFixture, annotated: list[dict[str, object]]
) -> None:
    from fleet_rlm.daytona.run_environment import _prepare_memory_digest

    class CorruptStore:
        def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult:
            del byte_budget
            raise WorkspaceMemoryStoreUnavailableError() from MemoryPayloadError("invalid memory response")

    with caplog.at_level(logging.WARNING):
        assert await _prepare_memory_digest(CorruptStore(), request="anything") == ""

    warnings = _degraded_warnings(caplog)
    assert len(warnings) == 1
    assert "category=corrupt_record_set" in warnings[0].getMessage()
    assert "operation=injection_digest" in warnings[0].getMessage()
    assert "outcome=no_memory_injection" in warnings[0].getMessage()
    assert annotated[0]["fleet.memory_degradation.category"] == "corrupt_record_set"

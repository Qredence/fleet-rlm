"""Live Daytona proofs for QRE-142 memory semantics, Skill consumption, and MLflow fail-soft.

Gate: FLEET_LIVE=1 with credentials and the pinned v5 snapshot resolved through
``tests.live.backend.test_fleet_rlm_daytona_mvp._live_settings`` (repo ``.env``,
``override=False``). Each case writes a per-case receipt next to
``FLEET_LIVE_EVIDENCE_PATH`` (suffix ``-<case>`` before the extension); receipt
writing is skipped when the variable is unset, matching the MVP convention.

Cases (run sequentially; each owns one app, one Volume, strict cleanup):

1. ``test_live_old_relevant_memory_recovered_beyond_tail_window`` — memory
   relevance: an old relevant entry pushed out of the newest-tail window is
   still recovered through the query-sensitive per-Turn injection digest and
   through the live ``search_memories`` Tool.
2. ``test_live_provenance_and_supersession_through_promotion`` — an explicit
   memory superseded by a promoted agent Candidate: only the active replacement
   is searchable/injected while raw ``MEMORIES.md`` history stays inspectable.
3. ``test_live_memory_candidates_default_off_and_mlflow_fail_soft`` — the
   default profile (``autonomous_memory_categories = []``) never exposes
   ``propose_memory``, and a dead MLflow tracking URI is fail-soft.
4. ``test_live_failed_run_discards_memory_candidates`` — a timed-out Run that
   proposed a Candidate promotes nothing.
5. ``test_live_skill_consumes_declared_resource_with_truthful_card`` — explicit
   ``long-context`` selection: truthful Card/affordances, activated/loaded
   lifecycle, and ``read_skill_resource`` on a declared resource with bounded
   metadata-safe events.
6. ``test_live_custom_signature_skill_coexistence`` — explicit ``data-analysis``
   selection keeps its custom Signature (schema ``skill.data-analysis``) while
   default Tools and the Skill lifecycle keep working.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config import Settings
from fleet_rlm.daytona.dspy_sync_bridge import sync_sandbox
from fleet_rlm.daytona.workspace_fs import DaytonaSandboxVolumeFs
from fleet_rlm.daytona.workspace_memory import (
    DaytonaWorkspaceMemoryStore,
    read_workspace_memory_injection_digest,
)
from fleet_rlm.files.memory_models import WORKSPACE_MEMORY_INJECTION_TAIL_BYTES
from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost
from fleet_rlm.files.volume_paths import volume_paths_from_settings
from fleet_rlm.skills.catalog import stable_skill_id
from tests.live.backend.test_fleet_rlm_daytona_mvp import (
    _assert_sse_stop,
    _live_settings,
    _sse_chunks,
    _strict_cleanup,
)
from tests.live.backend.test_memory_candidate_live import _paired_tool_chunks

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1200)]

_RECEIPT_SCHEMA = "fleet.qre142-memory-semantics-proof/v1"
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"

_PROBE_RELIC = "QRE142-RELIC-7314"
_RELC_LEARNING = (
    f"Operator-certified QRE-142 probe {_PROBE_RELIC}: latency baseline receipts "
    "must cite the baseline receipt sha verbatim."
)
_RELC_QUERY = f"{_PROBE_RELIC} latency baseline"

_SUPERSEDE_ORIGINAL = "operator release notes should stay detailed and long-form"
_SUPERSEDE_REPLACEMENT = "operator release notes should stay compact and evidence-bounded"
_SUPERSEDE_QUERY = "operator release notes"

_FAILED_PROBE = "QRE142-DISCARD-5150: timed-out runs must never promote candidates"

# NOTE (verified against RLMRunner._start_worker): spec.signature instructions are
# re-composed at execution start via root_signature_for_recursion(...) from Fleet's
# operating fragments and spec.skill_instructions, so prepare-time with_instructions
# never reaches the model. Steering therefore lives in the imperative Turn request
# text (the QRE-140-proven pattern); the capability preparer below only CAPTURES
# prepared-spec facts (tool names, schema id, signature fields, injected digests).

_SEED_TEXT = f"""
First call remember(key_learning={_RELC_LEARNING!r}, category="Project") exactly once in
the first code cell, without any SUBMIT in that cell; require saved["ok"] is True and
print("MEMORY_SEEDED"). Only after reading that observation, SUBMIT(answer=<one short
sentence containing the returned memory_id verbatim>) alone in the next code cell. Use no
other tools, do not call llm_query/rlm_query, and use exactly two code cells.
""".strip()

_RECALL_TEXT = """
The memory with id {expected_id!r} already exists; do NOT call remember or propose_memory.
First call search_memories(query={query!r}, limit=8) exactly once in the first code cell
without any SUBMIT; require result["ok"] and result["count"] >= 1 and
result["entries"][0]["id"] == {expected_id!r}; print("RECALL_READY"). Only after reading
that observation, SUBMIT(answer=<one short sentence containing the exact id {expected_id!r}
verbatim>) alone in the next code cell. Use no other tools, do not call
llm_query/rlm_query, and use exactly two code cells. Probe context: {query}.
""".strip()

_SUPERSEDE_TEXT = f"""
Execute exactly three code cells and no other tools; do not call llm_query/rlm_query.
Cell 1: original = remember(key_learning={_SUPERSEDE_ORIGINAL!r}, category="operator
preference"); require original["ok"] is True; print("ORIGINAL_READY"); no SUBMIT in this
cell. Cell 2: proposal = propose_memory(key_learning={_SUPERSEDE_REPLACEMENT!r},
category="operator preference", supersedes_id=original["memory_id"]); require
proposal["ok"] is True and proposal["supersedes"] is True; print("PROPOSAL_READY"); no
SUBMIT in this cell. Cell 3: SUBMIT(answer=<one short sentence containing
original["memory_id"] verbatim>) alone.
""".strip()

_VERIFY_SUPERSEDED_TEXT = """
Do NOT call remember or propose_memory; the supersession already finished before this Turn.
First call search_memories(query={query!r}, limit=8) exactly once in the first code cell
without any SUBMIT; require result["ok"] and result["count"] >= 1 and
result["entries"][0]["id"] == {expected_id!r} and result["entries"][0]["source"] ==
"agent_candidate"; print("VERIFY_READY"). Only after reading that observation,
SUBMIT(answer=<one short sentence containing verbatim both the id {expected_id!r} and the
literal source value agent_candidate>) alone in the next code cell. Use no other tools, do
not call llm_query/rlm_query, and use exactly two code cells.
""".strip()

_FAILED_RUN_TEXT = f"""
Run exactly one code cell and do NOT call SUBMIT at all. The single cell must contain ONLY
these two statements in this order:
proposal = propose_memory(key_learning={_FAILED_PROBE!r}, category="operator preference")
import time; time.sleep(900)
No other tools, no retry, no SUBMIT anywhere.
""".strip()

_RESOURCE_TEXT = """
First call read_skill_resource(skill_id={skill_id!r},
resource_path="references/chunking-strategies.md") exactly once in the first code cell
without any SUBMIT; require resource["ok"] is True and resource["byte_size"] > 0 and
print("RESOURCE_READY"). Only after reading that observation, SUBMIT(answer=<one sentence
naming the chunking resource path>) alone in the next code cell. Use no other tools, do not
call llm_query/rlm_query, and use exactly two code cells.
""".strip()

_ANALYSIS_TEXT = """
In the first code cell, compute m = sum([2, 4, 6]) / 3 with plain Python and print(m); do
not call any tool and do not SUBMIT. In the next code cell call exactly
SUBMIT(answer="mean is 4", findings=["mean computed with plain Python"],
metrics=[{"name": "mean", "value": 4}], anomalies=[]) with keyword arguments. Use no
other tools, do not call llm_query/rlm_query, and use exactly two code cells.
""".strip()


@dataclass(slots=True)
class _CaptureLedger:
    """Host-side capture of what each Turn was actually prepared with."""

    tool_names: list[str] = field(default_factory=list)
    signature_fields: list[str] = field(default_factory=list)
    output_schema_id: str = ""
    instructions_chars: int = 0
    digests: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _QRE142SpecCapture:
    """Passive capture of prepared spec facts (steering lives in the Turn request text)."""

    delegate: Any
    ledger: _CaptureLedger

    async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> Any:
        prepared = await self.delegate.prepare(turn, environment, attachments, deadline=deadline)
        spec = prepared.spec
        self.ledger.tool_names = sorted(str(tool.name) for tool in spec.tools)
        self.ledger.output_schema_id = str(spec.output_schema_id)
        self.ledger.signature_fields = sorted(str(name) for name in spec.signature.fields)
        self.ledger.instructions_chars = len(spec.signature.instructions or "")
        digest = getattr(prepared, "workspace_memory_digest", "")
        if isinstance(digest, str):
            self.ledger.digests.append(digest)
        return prepared


class _QRE142Runner:
    """One app + one Volume lifecycle with strict cleanup for a single proof case."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session_id: UUID | None = None
        self.sandbox_ids: set[str] = set()
        self.run_ids: list[str] = []
        self.app: Any = None
        self.client: Any = None
        self.portal: Any = None
        self.resources: Any = None
        self.preparation: Any = None
        self._store: DaytonaWorkspaceMemoryStore | None = None
        self._volume_fs: DaytonaSandboxVolumeFs | None = None

    def __enter__(self) -> _QRE142Runner:
        self.app = create_app(settings=self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        inventory = self.app.state.runtime_inventory
        self.resources = inventory.run_environment_resources
        self.preparation = inventory.run_preparation
        assert self.resources is not None and self.preparation is not None
        self.portal = self.client.portal
        assert self.portal is not None
        return self

    def __exit__(self, *exc_info: object) -> bool:
        failures: tuple[str, ...] = ()
        try:
            failures = self.portal.call(_strict_cleanup, self.resources, self.sandbox_ids, self.settings.volume_name)
        finally:
            self.client.__exit__(*exc_info)
        if failures:
            raise AssertionError("QRE-142 live cleanup failed: " + ", ".join(failures))
        return False

    def start_session(self, title: str) -> UUID:
        created = self.client.post("/api/sessions", json={"title": title})
        assert created.status_code == 201
        self.session_id = UUID(created.json()["id"])
        return self.session_id

    def post_turn(self, text: str, *, skill: Any | None = None, expect_stop: bool = True) -> list[dict[str, Any]]:
        assert self.session_id is not None
        payload: dict[str, Any] = {"text": text}
        if skill is not None:
            payload["skill_selections"] = [{"id": str(skill.card.id), "expected_version": skill.card.version}]
        response = self.client.post(
            f"/api/sessions/{self.session_id}/turns",
            json=payload,
            headers={"Idempotency-Key": f"qre142-{uuid4()}"},
        )
        assert response.status_code == 200
        chunks, done = _sse_chunks(response)
        assert done == 1
        self.run_ids.append(str(next(chunk["messageId"] for chunk in chunks if chunk["type"] == "start")))
        if expect_stop:
            _assert_sse_stop(chunks, label="qre142_turn")
        return chunks

    def memory_store(self) -> DaytonaWorkspaceMemoryStore:
        if self._store is None:
            assert self.session_id is not None
            binding = self.portal.call(self.resources.bindings.get, self.session_id)
            assert binding is not None and binding.sandbox_id is not None
            self.sandbox_ids.add(binding.sandbox_id)
            portal_loop = self.portal.call(lambda: asyncio.get_running_loop())
            sandbox = sync_sandbox(self.portal.call(self.resources.platform.get, binding.sandbox_id), portal_loop)
            assert sandbox is not None
            self._volume_fs = DaytonaSandboxVolumeFs(sandbox)
            self._store = DaytonaWorkspaceMemoryStore(
                sandbox,
                volume_paths=volume_paths_from_settings(self.settings),
                max_upload_bytes=self.settings.max_upload_bytes,
            )
        return self._store

    def memory_file_text(self) -> str:
        assert self._volume_fs is not None
        return self._volume_fs.read_bytes(str(volume_paths_from_settings(self.settings).memory_file)).decode("utf-8")


def _case_settings(tmp_path: Path, name: str, **updates: Any) -> Settings:
    """Build per-case live Settings on an isolated Volume over the migrated throwaway DB."""

    base: dict[str, Any] = {
        "volume_name": f"fleet-rlm-qre142-{name}-{uuid4().hex[:8]}",
        "run_heartbeat_seconds": 10,
        "run_stale_after_seconds": 600,
    }
    base.update(updates)
    return _live_settings(tmp_path).model_copy(update=base)


def _candidate_metadata(settings: Settings) -> dict[str, object]:
    def _git(*args: str) -> str:
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    return {
        "sha": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "tracked_tree_clean": not bool(_git("status", "--porcelain", "--untracked-files=no")),
        "versions": {
            "python": sys.version.split()[0],
            "dspy": importlib.metadata.version("dspy"),
            "daytona": importlib.metadata.version("daytona"),
        },
        "lockfile_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
        "models": {"root": settings.root_model, "sub": settings.sub_model},
    }


def _case_receipt_path(case: str) -> Path | None:
    raw_path = os.environ.get(_EVIDENCE_ENV)
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    return path.with_name(f"{path.stem}-{case}{path.suffix or '.json'}")


def _write_case_receipt(case: str, payload: dict[str, object]) -> None:
    path = _case_receipt_path(case)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _final_text(chunks: list[dict[str, Any]]) -> str:
    return "".join(str(chunk.get("delta", "")) for chunk in chunks if chunk.get("type") == "text-delta")


def _skill_events(chunks: list[dict[str, Any]], skill_id: str) -> list[dict[str, Any]]:
    return [
        chunk["data"]
        for chunk in chunks
        if chunk.get("type") == "data-skill"
        and isinstance(chunk.get("data"), dict)
        and chunk["data"].get("skill_id") == skill_id
    ]


def test_live_old_relevant_memory_recovered_beyond_tail_window(tmp_path: Path) -> None:
    settings = _case_settings(tmp_path, "relevance", rlm_max_iters=4, rlm_max_llm_calls=6, turn_timeout_seconds=560)
    ledger = _CaptureLedger()
    started = time.perf_counter()
    with _QRE142Runner(settings) as run:
        run.preparation._capabilities = _QRE142SpecCapture(run.preparation._capabilities, ledger)
        session_id = run.start_session("QRE-142 memory relevance")
        seed_chunks = run.post_turn(_SEED_TEXT)
        remember_inputs, remember_outputs, remember_errors = _paired_tool_chunks(seed_chunks, "remember")
        assert remember_errors == []
        # QRE-142-validated models call remember once; the validation matrix
        # tolerates provider variance in batch count as long as every call is
        # error-free (the seeded record identity comes from the first output).
        assert len(remember_inputs) == len(remember_outputs) >= 1
        old_memory_id = str(remember_outputs[0]["output"].get("memory_id", ""))
        assert len(old_memory_id) == 8

        store = run.memory_store()
        remember_tool = WorkspaceMemoryToolHost(store).as_tools()[1]
        filler_count = 0
        tail = store.read_tail(byte_budget=WORKSPACE_MEMORY_INJECTION_TAIL_BYTES).content
        while old_memory_id in tail and filler_count < 48:
            filler = (
                f"Routine housekeeping note {filler_count:02d}: ordinary scratch bookkeeping for the "
                "operator workspace; bulk context padding entry with no probe relevance at all."
            )
            seeded = remember_tool(key_learning=filler, category="General")
            assert seeded["ok"] is True
            filler_count += 1
            tail = store.read_tail(byte_budget=WORKSPACE_MEMORY_INJECTION_TAIL_BYTES).content
        assert old_memory_id not in tail, "old entry still inside the newest-tail window after seeding"
        assert filler_count >= 1

        recall_text = _RECALL_TEXT.format(query=_RELC_QUERY, expected_id=old_memory_id)
        recall_chunks = run.post_turn(recall_text)
        search_inputs, search_outputs, search_errors = _paired_tool_chunks(recall_chunks, "search_memories")
        assert search_errors == []
        assert len(search_inputs) == len(search_outputs) == 1
        assert old_memory_id in search_outputs[0]["output"].get("top_memory_ids", ())
        assert old_memory_id in _final_text(recall_chunks)

        # Diagnostics for the store state right after the recall Turn (report-only).
        store_entries = run.memory_store().list_entries(limit=48).entries
        tool_histogram: dict[str, int] = {}
        for chunk in recall_chunks:
            if chunk.get("type") == "tool-input-available":
                name = str(chunk.get("toolName", "?"))
                tool_histogram[name] = tool_histogram.get(name, 0) + 1
        tail_now = run.memory_store().read_tail(byte_budget=WORKSPACE_MEMORY_INJECTION_TAIL_BYTES).content
        print(  # noqa: T201 (live-failure diagnostics only)
            "RELEVANCE DIAG entries=\n",
            [(entry.memory_id, entry.active, entry.category) for entry in store_entries],
            "\ntool_histogram=\n",
            tool_histogram,
            "\ntail_now_ids=\n",
            [line[-120:] for line in tail_now.splitlines()],
        )
        digest = read_workspace_memory_injection_digest(run.memory_store(), request=recall_text)
        assert old_memory_id in digest
        injected = ledger.digests[-1]
        assert old_memory_id in injected, "old relevant memory was not injected into the live Turn"
        assert _PROBE_RELIC in injected

    _write_case_receipt(
        "relevance",
        {
            "schema": _RECEIPT_SCHEMA,
            "case": "relevance_beyond_tail_window",
            "candidate": _candidate_metadata(settings),
            "timing": {"duration_ms": int((time.perf_counter() - started) * 1000)},
            "assertions": {
                "seeded_through_remember_tool": True,
                "old_entry_beyond_tail_window": True,
                "digest_recovers_old_entry": True,
                "live_turn_injected_old_entry": True,
                "live_search_recovers_old_entry": True,
                "cleanup_passed": True,
            },
            "memory": {"old_memory_id": old_memory_id, "filler_records": filler_count},
            "resources": {
                "session_id": str(session_id),
                "sandbox_ids": sorted(run.sandbox_ids),
                "volume_name": settings.volume_name,
            },
            "passed": True,
        },
    )


def test_live_provenance_and_supersession_through_promotion(tmp_path: Path) -> None:
    settings = _case_settings(
        tmp_path,
        "supersession",
        rlm_autonomous_memory_categories=("operator preference",),
        rlm_max_iters=6,
        rlm_max_llm_calls=8,
        turn_timeout_seconds=560,
    )
    ledger = _CaptureLedger()
    started = time.perf_counter()
    with _QRE142Runner(settings) as run:
        run.preparation._capabilities = _QRE142SpecCapture(run.preparation._capabilities, ledger)
        session_id = run.start_session("QRE-142 memory provenance and supersession")
        first_chunks = run.post_turn(_SUPERSEDE_TEXT)
        remember_inputs, remember_outputs, remember_errors = _paired_tool_chunks(first_chunks, "remember")
        proposal_inputs, proposal_outputs, proposal_errors = _paired_tool_chunks(first_chunks, "propose_memory")
        assert remember_errors == [] and proposal_errors == []
        assert len(remember_inputs) == len(remember_outputs) == 1
        assert len(proposal_inputs) == len(proposal_outputs) == 1
        original_id = str(remember_outputs[0]["output"].get("memory_id", ""))
        assert len(original_id) == 8
        assert proposal_inputs[0]["input"]["supersedes"] is True
        assert proposal_outputs[0]["output"]["supersedes"] is True

        store = run.memory_store()
        entries = {entry.memory_id: entry for entry in store.list_entries(limit=16).entries}
        original = entries[original_id]
        assert original.active is False
        assert original.superseded_by_id is not None
        replacement_id = str(original.superseded_by_id)
        replacement = entries[replacement_id]
        assert replacement.active is True
        assert replacement.source == "agent_candidate"
        assert replacement.supersedes_id == original_id
        assert _SUPERSEDE_REPLACEMENT in replacement.learning

        search_tool = WorkspaceMemoryToolHost(store).as_tools()[4]
        searched = search_tool(query=_SUPERSEDE_QUERY, limit=8)
        searched_ids = [str(entry["id"]) for entry in searched["entries"]]
        assert replacement_id in searched_ids
        assert original_id not in searched_ids

        raw = run.memory_file_text()
        assert original_id in raw, "raw MEMORIES.md history must keep the superseded record"
        assert replacement_id in raw

        digest = read_workspace_memory_injection_digest(store, request="operator release notes replacement memory")
        assert replacement_id in digest
        assert "source:agent_candidate" in digest
        assert _SUPERSEDE_ORIGINAL not in digest

        verify_chunks = run.post_turn(
            _VERIFY_SUPERSEDED_TEXT.format(query=_SUPERSEDE_QUERY, expected_id=replacement_id)
        )
        final_text = _final_text(verify_chunks)
        v_inputs, v_outputs, v_errors = _paired_tool_chunks(verify_chunks, "search_memories")
        assert v_errors == []
        assert len(v_inputs) == len(v_outputs) == 1
        assert replacement_id in v_outputs[0]["output"].get("top_memory_ids", ())
        assert replacement_id in final_text
        assert "agent_candidate" in final_text
        assert replacement_id in ledger.digests[-1]

    _write_case_receipt(
        "supersession",
        {
            "schema": _RECEIPT_SCHEMA,
            "case": "provenance_and_supersession",
            "candidate": _candidate_metadata(settings),
            "timing": {"duration_ms": int((time.perf_counter() - started) * 1000)},
            "assertions": {
                "original_deactivated": True,
                "replacement_active_agent_candidate": True,
                "search_returns_only_active": True,
                "injection_excludes_superseded": True,
                "raw_history_inspectable": True,
                "next_turn_search_and_text": True,
                "cleanup_passed": True,
            },
            "memory": {
                "original_id": original_id,
                "replacement_id": replacement_id,
                "replacement_source": "agent_candidate",
                "learning_sha256": hashlib.sha256(_SUPERSEDE_REPLACEMENT.encode()).hexdigest(),
            },
            "resources": {
                "session_id": str(session_id),
                "sandbox_ids": sorted(run.sandbox_ids),
                "volume_name": settings.volume_name,
            },
            "passed": True,
        },
    )


def test_live_memory_candidates_default_off_and_mlflow_fail_soft(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    settings = _case_settings(
        tmp_path,
        "default-off",
        rlm_max_iters=3,
        rlm_max_llm_calls=4,
        turn_timeout_seconds=560,
        mlflow_tracing_enabled=True,
        mlflow_tracking_uri="http://127.0.0.1:59999",
    )
    ledger = _CaptureLedger()
    started = time.perf_counter()
    with _QRE142Runner(settings) as run:
        run.preparation._capabilities = _QRE142SpecCapture(run.preparation._capabilities, ledger)
        _ = run.start_session("QRE-142 candidates default-off + MLflow fail-soft")
        chunks = run.post_turn("DIRECT: return the exact integer result of 17 + 25 and nothing else.")
        final_text = _final_text(chunks)
        assert "42" in final_text
        assert "propose_memory" not in ledger.tool_names
        assert "search_memories" in ledger.tool_names
        assert run.memory_store().list_entries(limit=8).entries == ()
        assert ledger.digests[-1] == ""
    captured = capfd.readouterr()
    console_output = captured.out + captured.err
    mlflow_markers = [line for line in console_output.splitlines() if "mlflow" in line.lower()]
    assert any(
        marker in line
        for line in mlflow_markers
        for marker in ("unavailable", "without traces", "Connection refused", "Failed to establish")
    ), mlflow_markers[:12]
    _write_case_receipt(
        "default-off-fail-soft",
        {
            "schema": _RECEIPT_SCHEMA,
            "case": "candidates_default_off_and_mlflow_fail_soft",
            "candidate": _candidate_metadata(settings),
            "timing": {"duration_ms": int((time.perf_counter() - started) * 1000)},
            "assertions": {
                "propose_memory_not_exposed": True,
                "workspace_memory_tools_still_present": True,
                "memory_store_empty": True,
                "mlflow_unavailable_fail_soft_observed": True,
                "turn_completed": True,
                "cleanup_passed": True,
            },
            "mlflow": {"tracking_uri": settings.mlflow_tracking_uri, "markers": mlflow_markers[:8]},
            "passed": True,
        },
    )


def test_live_failed_run_discards_memory_candidates(tmp_path: Path) -> None:
    settings = _case_settings(
        tmp_path,
        "failed-run",
        rlm_autonomous_memory_categories=("operator preference",),
        rlm_max_iters=8,
        rlm_max_llm_calls=8,
        turn_timeout_seconds=180,
        rlm_execution_timeout_s=280,
    )
    ledger = _CaptureLedger()
    started = time.perf_counter()
    with _QRE142Runner(settings) as run:
        run.preparation._capabilities = _QRE142SpecCapture(run.preparation._capabilities, ledger)
        _ = run.start_session("QRE-142 failed-run candidate discard")
        chunks = run.post_turn(_FAILED_RUN_TEXT, expect_stop=False)
        last = chunks[-1]
        assert last.get("type") == "finish" and last.get("finishReason") == "error", chunks[-3:]
        errors = [str(chunk.get("errorText", "")) for chunk in chunks if chunk.get("type") == "error"]
        assert any("timed out" in text for text in errors), errors
        proposal_inputs, proposal_outputs, proposal_errors = _paired_tool_chunks(chunks, "propose_memory")
        assert proposal_errors == []
        assert len(proposal_inputs) == len(proposal_outputs) == 1
        assert proposal_outputs[0]["output"]["ok"] is True

        entries = run.memory_store().list_entries(limit=16).entries
        assert entries == (), f"timed-out Run promoted candidates: {entries!r}"
        assert ledger.digests, "the Turn never reached capability preparation"

    _write_case_receipt(
        "failed-run",
        {
            "schema": _RECEIPT_SCHEMA,
            "case": "failed_run_discards_memory_candidates",
            "candidate": _candidate_metadata(settings),
            "timing": {"duration_ms": int((time.perf_counter() - started) * 1000)},
            "assertions": {
                "candidate_proposed_before_timeout": True,
                "turn_finished_with_error": True,
                "no_promotion_after_failure": True,
                "cleanup_passed": True,
            },
            "resources": {"sandbox_ids": sorted(run.sandbox_ids), "volume_name": settings.volume_name},
            "passed": True,
        },
    )


def test_live_skill_consumes_declared_resource_with_truthful_card(tmp_path: Path) -> None:
    settings = _case_settings(
        tmp_path, "skill-resource", rlm_max_iters=4, rlm_max_llm_calls=6, turn_timeout_seconds=560
    )
    ledger = _CaptureLedger()
    started = time.perf_counter()
    with _QRE142Runner(settings) as run:
        skill = run.app.state.skill_catalog.require(stable_skill_id("long-context"))
        run.preparation._capabilities = _QRE142SpecCapture(run.preparation._capabilities, ledger)
        assert skill.card.resources_available is True
        assert skill.resources, "long-context must declare loadable resources"
        _ = run.start_session("QRE-142 skill resource consumption")
        chunks = run.post_turn(_RESOURCE_TEXT.format(skill_id=str(skill.card.id)), skill=skill)
        events = _skill_events(chunks, str(skill.card.id))
        assert len(events) == 2
        assert events[0]["version"] == skill.card.version
        assert events[0]["trust"] == "system"
        assert tuple(events[0].get("affordances", ())) == skill.card.affordances
        assert events[1] == {
            "skill_id": str(skill.card.id),
            "name": "long-context",
            "version": skill.card.version,
            "phase": "loaded",
        }
        resource_inputs, resource_outputs, resource_errors = _paired_tool_chunks(chunks, "read_skill_resource")
        assert resource_errors == []
        assert len(resource_inputs) == len(resource_outputs) == 1
        assert resource_inputs[0]["input"]["resource_path"] == "references/chunking-strategies.md"
        output = resource_outputs[0]["output"]
        assert output["ok"] is True
        assert int(output.get("byte_size", 0)) > 0
        assert "content" not in output, "resource body must stay off the wire (metadata-safe events)"
        assert "chunking" in _final_text(chunks).lower()

    _write_case_receipt(
        "skill-resource",
        {
            "schema": _RECEIPT_SCHEMA,
            "case": "skill_consumes_declared_resource",
            "candidate": _candidate_metadata(settings),
            "timing": {"duration_ms": int((time.perf_counter() - started) * 1000)},
            "assertions": {
                "truthful_card_version_trust": True,
                "truthful_affordances": True,
                "activated_then_loaded": True,
                "declared_resource_consumed": True,
                "metadata_safe_resource_event": True,
                "cleanup_passed": True,
            },
            "resources": {"sandbox_ids": sorted(run.sandbox_ids), "volume_name": settings.volume_name},
            "passed": True,
        },
    )


def test_live_custom_signature_skill_coexistence(tmp_path: Path) -> None:
    settings = _case_settings(
        tmp_path, "custom-signature", rlm_max_iters=4, rlm_max_llm_calls=6, turn_timeout_seconds=560
    )
    ledger = _CaptureLedger()
    started = time.perf_counter()
    with _QRE142Runner(settings) as run:
        skill = run.app.state.skill_catalog.require(stable_skill_id("data-analysis"))
        run.preparation._capabilities = _QRE142SpecCapture(run.preparation._capabilities, ledger)
        _ = run.start_session("QRE-142 custom-Signature Skill coexistence")
        chunks = run.post_turn(_ANALYSIS_TEXT, skill=skill)
        structured = [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
        assert len(structured) == 1
        assert structured[0].get("data", {}).get("schema_id") == "skill.data-analysis"
        value = structured[0]["data"].get("value", {})
        for key in ("answer", "findings", "metrics", "anomalies"):
            assert key in value, value
        assert ledger.output_schema_id == "skill.data-analysis"
        for key in ("answer", "findings", "metrics", "anomalies"):
            assert key in ledger.signature_fields
        for expected_tool in ("load_skill", "read_skill_resource", "search_memories", "read_workspace_text"):
            assert expected_tool in ledger.tool_names
        assert "propose_memory" not in ledger.tool_names
        events = _skill_events(chunks, str(skill.card.id))
        assert [event.get("phase") for event in events] == ["activated", "loaded"]

    _write_case_receipt(
        "custom-signature",
        {
            "schema": _RECEIPT_SCHEMA,
            "case": "custom_signature_skill_coexistence",
            "candidate": _candidate_metadata(settings),
            "timing": {"duration_ms": int((time.perf_counter() - started) * 1000)},
            "assertions": {
                "skill_signature_active": True,
                "structured_result_skill_schema": True,
                "default_tools_coexist": True,
                "lifecycle_events": True,
                "cleanup_passed": True,
            },
            "resources": {"sandbox_ids": sorted(run.sandbox_ids), "volume_name": settings.volume_name},
            "passed": True,
        },
    )

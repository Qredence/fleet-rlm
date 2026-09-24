"""Benchmark live Fleet RLM latency and run the MLflow-native quality gate.

This script deliberately does not alter ``config/fleet.toml`` or restart Fleet.
Run it once per active configuration variant after restarting the API. Provider
execution requires ``FLEET_LIVE=1``; receipts contain bounded aggregates only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv


def _evaluation_dataset_name(tracking_url: str) -> str:
    """Dataset name; UC-qualified (catalog.schema.table) for the Databricks backend."""
    if tracking_url != "databricks":
        return DATASET_NAME
    catalog = os.environ.get("FLEET_MLFLOW_TRACE_CATALOG", "ml")
    schema = os.environ.get("FLEET_MLFLOW_TRACE_SCHEMA", "genai")
    return f"{catalog}.{schema}.fleet_rlm_latency_quality_v1"


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fleet_rlm.daytona.interpreter import _EXECUTION_STAT_KEYS
from scripts.benchmarks import judges as _judges
from scripts.benchmarks.campaign import (
    CampaignAdmissionError,
    CampaignBudget,
    CampaignPreflight,
    CampaignPreflightError,
)
from scripts.benchmarks.corpus_chain import (
    CORPUS_SEEDS,
    CORPUS_WORKLOAD_ID,
    CorpusCase,
    corpus_workload,
    make_corpus_case,
    validate_corpus_evidence,
    validate_corpus_report,
    write_corpus,
)

# Re-exported judge contracts; conductors and tests import them from this module.
CORRECTNESS_DESCRIPTION = _judges.CORRECTNESS_DESCRIPTION
CORRECTNESS_INSTRUCTIONS = _judges.CORRECTNESS_INSTRUCTIONS
DEFAULT_JUDGE_MODEL = _judges.DEFAULT_JUDGE_MODEL
EVIDENCE_COVERAGE_DESCRIPTION = _judges.EVIDENCE_COVERAGE_DESCRIPTION
EVIDENCE_COVERAGE_INSTRUCTIONS = _judges.EVIDENCE_COVERAGE_INSTRUCTIONS
JUDGE_INFERENCE_PARAMS = _judges.JUDGE_INFERENCE_PARAMS
JUDGE_NAMES = _judges.JUDGE_NAMES
ensure_registered = _judges.ensure_registered

RECEIPT_SCHEMA = "fleet.rlm-latency/v1"
DATASET_NAME = "fleet-rlm-latency-quality-v1"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_MLFLOW_URL = "http://127.0.0.1:5001"
_LIVE_VALUES = frozenset({"1", "true", "yes"})
EVIDENCE_WORKLOAD_ID = "evidence-conflict-v1"
WORKLOAD_CHOICES = (EVIDENCE_WORKLOAD_ID, CORPUS_WORKLOAD_ID)
PHASE6_CASES_PATH = Path(__file__).with_name("phase6_evaluation_cases.json")
PHASE6_FAMILIES = (
    "sparse_retrieval",
    "exhaustive_semantic_aggregation",
    "cross_document_reconciliation",
    "repository_investigation",
    "decomposable_reasoning",
    "multi_turn_continuation",
)
PHASE6_ARMS = (
    {"id": "A", "name": "native_only", "child_concurrency": 0},
    {"id": "B", "name": "sequential_full_children", "child_concurrency": 1},
    {"id": "C", "name": "bounded_parallel_full_children", "child_concurrency": 2},
)
PHASE6_TRIALS = 3
PHASE6_CONDITIONS = ("cold", "warm")
PHASE6_QUALITY_FIELDS = ("correctness", "grounded_evidence", "coverage", "task_completion")
_TRAJECTORY_ITEM_LIMIT = 64
_TRAJECTORY_CHAR_LIMIT = 64 * 1024
_BROKER_METRIC_KEYS = _EXECUTION_STAT_KEYS
_BROKER_METRIC_KEY_SET = frozenset(_BROKER_METRIC_KEYS)

LATENCY_WORKLOAD = """Analyze the following evidence and decide whether the customer can
prevent renewal of OF-7781 effective 2025-04-01. Resolve conflicts by authority
and effective date. Explain the controlling deadline, receipt versus sending
date, conflicting sources, and residual uncertainty.

A1 Master Agreement: written non-renewal notice must be received at least 30
calendar days before renewal. The notice period begins when the other party
receives notice.
A2 Amendment 2: contracts executed after 2024-03-01 require 45 days' notice.
A3 OF-7781 was executed 2024-01-15 and does not incorporate Amendment 2.
A4 OF-7781 renews 2025-04-01 unless 30 calendar days' written notice is received.
A5 Account manager email says 45 days are required; it is advice, not an amendment.
A6 Legal memo says Amendment 2 does not govern OF-7781.
A7 CRM note says no written notice was found.
A8 Mailbox metadata records the customer's written notice sent 2025-02-27 and received 2025-02-28.
A9 Internal policy summary says 45 days but identifies its source system as unknown.
A10 Internal policy summaries are informational and non-binding.

Use Python for date arithmetic. Use selected independent sub-LM comparisons
only if useful. End with exactly one typed SUBMIT."""

QUALITY_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "inputs": {"query": LATENCY_WORKLOAD},
        "expectations": {
            "expected_response": (
                "Yes. The 30-day receipt deadline is 2025-03-02; receipt on 2025-02-28 was timely. "
                "A1, A3, A4, A6, A8, and A10 control or corroborate; A2 does not apply and A5, A7, "
                "and A9 are overridden or non-binding."
            ),
            "required_evidence": ["A1", "A3", "A4", "A6", "A8", "A10"],
            "required_uncertainty": (
                "Delivery validity remains conditional on the mailbox evidence being authentic and contractually valid."
            ),
            "forbidden_claims": ["45-day rule controls", "sending date alone controls"],
        },
    },
    {
        "inputs": {
            "query": (
                "B1 requires receipt 20 days before 2026-01-31. B2 shows sending on 2026-01-10. "
                "B3 shows receipt on 2026-01-12. Determine timeliness and cite the controlling event."
            )
        },
        "expectations": {
            "expected_response": (
                "Not timely: the receipt deadline was 2026-01-11 and receipt on 2026-01-12 was one day late."
            ),
            "required_evidence": ["B1", "B3"],
            "required_uncertainty": "None beyond the stated dates.",
            "forbidden_claims": ["sending date controls", "notice was timely"],
        },
    },
    {
        "inputs": {
            "query": (
                "C1 signed policy requires manager approval above $50,000. C2 draft FAQ says approval is optional. "
                "C3 board resolution makes C1 binding. C4 request is $72,000. Decide whether approval is required "
                "and resolve the conflict."
            )
        },
        "expectations": {
            "expected_response": (
                "Approval is required because the binding C1/C3 chain controls and $72,000 exceeds $50,000; "
                "draft C2 is non-binding."
            ),
            "required_evidence": ["C1", "C3", "C4"],
            "required_uncertainty": "Conditional on C1 and C3 remaining in force.",
            "forbidden_claims": ["C2 controls", "approval is optional"],
        },
    },
    {
        "inputs": {
            "query": (
                "D1 contains approved amounts 120, 80, and 45. D2 contains draft amounts 900 and 700 that must "
                "be excluded. D3 adds an approved credit of -15. Compute the approved net total and identify "
                "excluded evidence."
            )
        },
        "expectations": {
            "expected_response": "The approved net total is 230: 120 + 80 + 45 - 15. D2's draft values are excluded.",
            "required_evidence": ["D1", "D2", "D3"],
            "required_uncertainty": "None beyond the classification supplied.",
            "forbidden_claims": ["1830", "include D2"],
        },
    },
    {
        "inputs": {
            "query": (
                "E1 says access is allowed only after security approval. "
                "E2 records approval requested but no decision. "
                "E3 is an unverified chat saying approval probably happened. Decide whether access is currently "
                "authorized."
            )
        },
        "expectations": {
            "expected_response": (
                "The record is insufficient to establish authorization. E1 requires approval, E2 has no decision, "
                "and E3 is unverified."
            ),
            "required_evidence": ["E1", "E2", "E3"],
            "required_uncertainty": "Authorization is conditional on obtaining verified approval evidence.",
            "forbidden_claims": ["access is authorized", "E3 proves approval"],
        },
    },
)


class BenchmarkError(RuntimeError):
    """A live benchmark precondition or Turn contract failed."""


class CampaignLimitError(BenchmarkError):
    """A campaign safety bound was reached and the run must stop immediately."""


def _baseline_evidence_quality(answer: str, *, termination_mode: str | None) -> dict[str, Any]:
    """Apply the frozen evidence-conflict rubric as a conservative text heuristic."""
    expectations = QUALITY_RECORDS[0]["expectations"]
    text = " ".join(answer.casefold().split())

    def contains_source(source_id: str) -> bool:
        return re.search(rf"(?<![a-z0-9]){re.escape(source_id.casefold())}(?![a-z0-9])", text) is not None

    def contains_any(*phrases: str) -> bool:
        return any(phrase in text for phrase in phrases)

    required_evidence = list(expectations["required_evidence"])
    cited = [source_id for source_id in required_evidence if contains_source(source_id)]
    forbidden = list(expectations["forbidden_claims"])
    forbidden_claim = any(claim.casefold() in text for claim in forbidden)
    deadline_present = bool(re.search(r"\b2025-03-02\b|\b03/02/2025\b|\b(?:march|mar)\s+2(?:nd)?[,]?\s+2025\b", text))
    receipt_present = bool(
        re.search(r"\b2025-02-28\b|\b02/28/2025\b|\b(?:february|feb)\s+28(?:th)?[,]?\s+2025\b", text)
    )
    affirmative = text.startswith("yes") or contains_any("can prevent", "may prevent")
    timely = contains_any("timely", "on time", "before the deadline", "before deadline")
    amendment_not_applicable = contains_source("A2") and contains_any(
        "does not apply", "does not govern", "not applicable", "not incorporated", "is inapplicable"
    )
    conflict_sources_present = all(contains_source(source_id) for source_id in ("A2", "A5", "A7", "A9"))
    uncertainty_explicit = (
        "authentic" in text
        and "contractually valid" in text
        and contains_any("conditional", "if the mailbox", "if mailbox")
    )
    completion = termination_mode == "typed_submit"
    correctness_criteria = {
        "affirmative_outcome": affirmative,
        "receipt_deadline": deadline_present,
        "receipt_date": receipt_present,
        "receipt_timely": timely,
        "amendment_2_not_applicable": amendment_not_applicable,
        "no_forbidden_claim": not forbidden_claim,
    }
    evidence_quality = (
        len(cited) == len(required_evidence)
        and conflict_sources_present
        and not forbidden_claim
        and amendment_not_applicable
    )
    return {
        "method": "deterministic_text_heuristic",
        "quality_source": "frozen_reference_heuristic",
        "correctness": all(correctness_criteria.values()),
        "correctness_criteria": correctness_criteria,
        "grounded_evidence": evidence_quality,
        "evidence_coverage": len(cited) / len(required_evidence) if required_evidence else None,
        "evidence_cited": cited,
        "evidence_required": required_evidence,
        "uncertainty_explicit": uncertainty_explicit,
        "task_completion": completion,
        "forbidden_claims_detected": [claim for claim in forbidden if claim.casefold() in text],
    }


def _canonical_json_hash(value: Any) -> str:
    """Hash JSON values using the frozen campaign's canonical representation."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_phase6_cases(path: Path = PHASE6_CASES_PATH) -> list[dict[str, Any]]:
    """Load the six frozen Phase 6 inputs and reject input or rubric drift."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError("could not load frozen Phase 6 evaluation cases") from exc
    if not isinstance(payload, Mapping) or payload.get("schema") != "fleet.phase6-evaluation-campaign/v1":
        raise BenchmarkError("unsupported frozen Phase 6 evaluation case schema")
    records = payload.get("records")
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise BenchmarkError("frozen Phase 6 cases must contain object records")
    if tuple(row.get("family") for row in records) != PHASE6_FAMILIES:
        raise BenchmarkError("frozen Phase 6 cases must contain the six expected families in order")
    for row in records:
        if not isinstance(row.get("input"), str):
            raise BenchmarkError("each frozen Phase 6 case must include a text input")
        if row.get("input_sha256") != hashlib.sha256(row["input"].encode("utf-8")).hexdigest():
            raise BenchmarkError(f"frozen input hash mismatch for {row['family']}")
        rubric = row.get("rubric")
        if not isinstance(rubric, Mapping) or not isinstance(rubric.get("criteria"), list):
            raise BenchmarkError(f"frozen evidence rubric is invalid for {row['family']}")
        if row.get("rubric_sha256") != _canonical_json_hash(rubric):
            raise BenchmarkError(f"frozen rubric hash mismatch for {row['family']}")
    return records


def phase6_plan_receipt(path: Path = PHASE6_CASES_PATH, *, trials: int = PHASE6_TRIALS) -> dict[str, Any]:
    """Build a planning-only paired-arm receipt; this does not run evaluations."""
    cases = load_phase6_cases(path)
    paired_cells = build_phase6_schedule(cases, trials=trials)
    maximum_root_turns = (
        sum(2 if case["family"] == "multi_turn_continuation" else 1 for case in cases)
        * trials
        * len(PHASE6_CONDITIONS)
        * len(PHASE6_ARMS)
    )
    return {
        "schema": RECEIPT_SCHEMA,
        "command": "phase6-plan",
        "status": "planning_only",
        "generated_at": datetime.now(UTC).isoformat(),
        "phase6_plan_schema": "fleet.phase6-evaluation-plan/v1",
        "input_manifest": path.name,
        "input_manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "families": [
            {"family": case["family"], "input_sha256": case["input_sha256"], "rubric_sha256": case["rubric_sha256"]}
            for case in cases
        ],
        "arms": list(PHASE6_ARMS),
        "paired_trials": trials,
        "conditions": list(PHASE6_CONDITIONS),
        "condition_semantics": (
            "cold/warm are required labels; this offline runner does not control service cache or process state"
        ),
        "state_reset_requirement": (
            "each future live pair/arm cell must begin from an isolated Session and declared cache condition"
        ),
        "paired_cells_per_arm": len(cases) * trials * len(PHASE6_CONDITIONS),
        "total_planned_task_executions": len(paired_cells),
        "schedule": paired_cells,
        "maximum_root_turn_admissions": maximum_root_turns,
        "required_outcomes": [
            "correctness",
            "grounded_evidence",
            "coverage",
            "task_completion",
            "wall_time",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "spend_status",
            "usage_status",
            "staging_overhead",
            "cleanup_status",
            "operational_failure",
            "source_authorized",
            "containment_confirmed",
            "commit_safe",
        ],
        "unknown_policy": "Unobserved usage, evidence, coverage, staging, cleanup, or failure status remains unknown.",
        "execution_support": (
            "not_implemented: this runner supports one workload and one active configuration per invocation"
        ),
        "live_preflight": "Provider runs still require FLEET_LIVE=1 and the existing campaign limits.",
        "resource_envelope": {
            "shared_maximum_root_turn_admissions": maximum_root_turns,
            "child_concurrency_by_arm": {arm["id"]: arm["child_concurrency"] for arm in PHASE6_ARMS},
            "token_and_spend_caps": "must be operator-supplied for a future live campaign; not inferred here",
        },
    }


def build_phase6_schedule(cases: Sequence[Mapping[str, Any]], *, trials: int = PHASE6_TRIALS) -> list[dict[str, Any]]:
    """Materialize matched arm cells and rotate arm order across paired trials."""
    if type(trials) is not int or not 1 <= trials <= 10:
        raise BenchmarkError("Phase 6 trials must be between 1 and 10")
    schedule: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases):
        for trial in range(1, trials + 1):
            for condition_index, condition in enumerate(PHASE6_CONDITIONS):
                pair_id = f"{case['family']}-t{trial}-{condition}"
                rotation = (trial - 1 + case_index + condition_index) % len(PHASE6_ARMS)
                ordered_arms = (*PHASE6_ARMS[rotation:], *PHASE6_ARMS[:rotation])
                for arm_order, arm in enumerate(ordered_arms):
                    schedule.append(
                        {
                            "pair_id": pair_id,
                            "family": case["family"],
                            "trial": trial,
                            "condition": condition,
                            "arm": arm["id"],
                            "arm_order": arm_order,
                            "child_concurrency": arm["child_concurrency"],
                        }
                    )
    return schedule


def _phase6_policy_gate(
    observed: Mapping[tuple[str, str], Mapping[str, Any]],
    *,
    quality_complete: bool,
    quality_regressions: Mapping[str, Mapping[str, list[str]]],
    trials: int,
) -> dict[str, Any]:
    """Apply the frozen quality-first retain rule to supplied paired outcomes."""
    safety_fields = ("source_authorized", "containment_confirmed", "commit_safe")
    safety_unknown = sorted(
        f"{pair_id}/{arm}/{field}"
        for (pair_id, arm), row in observed.items()
        for field in safety_fields
        if not isinstance(row.get(field), bool)
    )
    safety_failed = sorted(
        f"{pair_id}/{arm}"
        for (pair_id, arm), row in observed.items()
        if any(row.get(field) is False for field in safety_fields)
        or row.get("operational_failure") is True
        or row.get("cleanup_status") not in {"confirmed", "not_applicable", "unknown", None}
    )
    cleanup_unknown = sorted(
        f"{pair_id}/{arm}/cleanup_status"
        for (pair_id, arm), row in observed.items()
        if row.get("cleanup_status") not in {"confirmed", "not_applicable"}
    )
    failure_unknown = sorted(
        f"{pair_id}/{arm}/operational_failure"
        for (pair_id, arm), row in observed.items()
        if not isinstance(row.get("operational_failure"), bool)
    )
    fixture_only = any(row.get("quality_source") == "synthetic_fixture" for row in observed.values())
    safety_verified = not (safety_unknown or safety_failed or cleanup_unknown or failure_unknown)
    required_wins = max(2, math.ceil(trials * 2 / 3))
    by_family: dict[str, dict[str, Any]] = {arm: {} for arm in ("B", "C")}
    for arm in ("B", "C"):
        for family in PHASE6_FAMILIES:
            no_regression = quality_complete and all(
                not quality_regressions[arm][f"{family}/{condition}"] for condition in PHASE6_CONDITIONS
            )
            quality_gain_fields: list[str] = []
            if no_regression and trials >= 3:
                for field in PHASE6_QUALITY_FIELDS:
                    if all(
                        sum(
                            observed[(f"{family}-t{trial}-{condition}", arm)].get(field) is True
                            and observed[(f"{family}-t{trial}-{condition}", "A")].get(field) is False
                            for trial in range(1, trials + 1)
                        )
                        >= required_wins
                        for condition in PHASE6_CONDITIONS
                    ):
                        quality_gain_fields.append(field)
            latency_gain = False
            if no_regression and trials >= 3:
                condition_gains: list[bool] = []
                for condition in PHASE6_CONDITIONS:
                    pairs = [
                        (
                            observed[(f"{family}-t{trial}-{condition}", "A")],
                            observed[(f"{family}-t{trial}-{condition}", arm)],
                        )
                        for trial in range(1, trials + 1)
                    ]
                    if any(
                        not all(
                            isinstance(row.get(key), (int, float))
                            and not isinstance(row.get(key), bool)
                            and row[key] >= 0
                            for row in pair
                            for key in ("wall_time_ms", "input_tokens", "output_tokens")
                        )
                        or pair[0]["wall_time_ms"] <= 0
                        for pair in pairs
                    ):
                        condition_gains.append(False)
                        continue
                    speedup = statistics.median(
                        (baseline["wall_time_ms"] - candidate["wall_time_ms"]) / baseline["wall_time_ms"]
                        for baseline, candidate in pairs
                    )
                    baseline_tokens = statistics.median(
                        baseline["input_tokens"] + baseline["output_tokens"] for baseline, _ in pairs
                    )
                    candidate_tokens = statistics.median(
                        candidate["input_tokens"] + candidate["output_tokens"] for _, candidate in pairs
                    )
                    condition_gains.append(speedup >= 0.10 and candidate_tokens <= baseline_tokens * 1.10)
                latency_gain = all(condition_gains)
            by_family[arm][family] = {
                "no_quality_regression": no_regression,
                "quality_gain_fields": quality_gain_fields,
                "latency_gain": latency_gain,
                "retain_candidate": (
                    safety_verified and not fixture_only and no_regression and bool(quality_gain_fields)
                ),
            }
    if not quality_complete:
        status = "quality_incomplete"
    elif fixture_only:
        status = "fixture_only"
    elif safety_failed:
        status = "safety_failed"
    elif safety_unknown or cleanup_unknown or failure_unknown:
        status = "safety_unverified"
    else:
        status = "conditional_analysis_only"
    return {
        "status": status,
        "safety_unknown": safety_unknown + cleanup_unknown + failure_unknown,
        "safety_failed": safety_failed,
        "thresholds": {
            "quality_wins_per_condition": required_wins,
            "quality_gain_required_for_retention": True,
            "minimum_latency_speedup": 0.10,
            "maximum_token_increase": 0.10,
        },
        "by_family": by_family,
        "promotion_authorized": False,
    }


def analyze_phase6_outcomes(
    outcomes: Sequence[Mapping[str, Any]], *, cases_path: Path = PHASE6_CASES_PATH, trials: int = PHASE6_TRIALS
) -> dict[str, Any]:
    """Analyze paired outcomes quality-first and suppress performance on quality gaps."""
    cases = load_phase6_cases(cases_path)
    schedule = build_phase6_schedule(cases, trials=trials)
    schedule_by_key = {(row["pair_id"], row["arm"]): row for row in schedule}
    expected = set(schedule_by_key)
    observed: dict[tuple[str, str], Mapping[str, Any]] = {}
    invalid_rows = 0
    for row in outcomes:
        if not isinstance(row, Mapping):
            invalid_rows += 1
            continue
        key = (str(row.get("pair_id", "")), str(row.get("arm", "")))
        expected_cell = schedule_by_key.get(key, {})
        if (
            key not in expected
            or key in observed
            or any(row.get(field) != expected_cell.get(field) for field in ("family", "trial", "condition"))
        ):
            invalid_rows += 1
            continue
        observed[key] = row
    missing = expected - observed.keys()
    complete = not missing and invalid_rows == 0
    unknown_quality = [
        f"{pair_id}/{arm}/{field}"
        for (pair_id, arm), row in observed.items()
        for field in PHASE6_QUALITY_FIELDS
        if not isinstance(row.get(field), bool)
    ]
    unknown_quality.extend(
        f"{pair_id}/{arm}/quality_source"
        for (pair_id, arm), row in observed.items()
        if row.get("quality_source") not in {"judge", "reference", "synthetic_fixture"}
    )
    quality_complete = complete and not unknown_quality
    arm_quality: dict[str, dict[str, float | None]] = {}
    for arm in (entry["id"] for entry in PHASE6_ARMS):
        arm_rows = [row for (pair_id, row_arm), row in observed.items() if row_arm == arm]
        arm_quality[arm] = {
            field: (
                sum(row[field] is True for row in arm_rows) / len(arm_rows) if quality_complete and arm_rows else None
            )
            for field in PHASE6_QUALITY_FIELDS
        }
    family_condition_quality: dict[str, dict[str, dict[str, float | None]]] = {}
    for condition in PHASE6_CONDITIONS:
        for family in PHASE6_FAMILIES:
            family_condition = f"{family}/{condition}"
            family_condition_quality[family_condition] = {}
            for arm in (entry["id"] for entry in PHASE6_ARMS):
                cell_rows = [
                    row
                    for (_pair_id, row_arm), row in observed.items()
                    if row_arm == arm and row.get("family") == family and row.get("condition") == condition
                ]
                family_condition_quality[family_condition][arm] = {
                    field: (
                        sum(row[field] is True for row in cell_rows) / len(cell_rows)
                        if quality_complete and cell_rows
                        else None
                    )
                    for field in PHASE6_QUALITY_FIELDS
                }
    quality_regressions: dict[str, dict[str, list[str]]] = {
        "B": {family_condition: [] for family_condition in family_condition_quality},
        "C": {family_condition: [] for family_condition in family_condition_quality},
    }
    if quality_complete:
        for arm in ("B", "C"):
            for family_condition, scores in family_condition_quality.items():
                quality_regressions[arm][family_condition] = [
                    field for field in PHASE6_QUALITY_FIELDS if scores[arm][field] < scores["A"][field]
                ]
    quality_passed = quality_complete and not any(
        regression for by_family in quality_regressions.values() for regression in by_family.values()
    )
    performance: dict[str, Any] = {
        "status": "suppressed_until_quality_passes",
        "by_arm": {arm["id"]: None for arm in PHASE6_ARMS},
        "paired_deltas_ms": None,
    }
    if quality_passed:
        timing_missing = [
            f"{pair_id}/{arm}/wall_time_ms"
            for (pair_id, arm), row in observed.items()
            if not isinstance(row.get("wall_time_ms"), (int, float)) or isinstance(row.get("wall_time_ms"), bool)
        ]
        if not timing_missing:
            per_arm: dict[str, dict[str, float]] = {}
            by_pair: dict[str, dict[str, float]] = {}
            for arm in (entry["id"] for entry in PHASE6_ARMS):
                values = [float(row["wall_time_ms"]) for (pair_id, row_arm), row in observed.items() if row_arm == arm]
                per_arm[arm] = {
                    "mean_ms": statistics.fmean(values),
                    "median_ms": statistics.median(values),
                }
            for (pair_id, arm), row in observed.items():
                by_pair.setdefault(pair_id, {})[arm] = float(row["wall_time_ms"])
            performance = {
                "status": "available_descriptive_only",
                "by_arm": per_arm,
                "by_condition": {
                    condition: {
                        arm: {
                            "mean_ms": statistics.fmean(
                                float(row["wall_time_ms"])
                                for (_pair_id, row_arm), row in observed.items()
                                if row_arm == arm and row.get("condition") == condition
                            ),
                            "median_ms": statistics.median(
                                float(row["wall_time_ms"])
                                for (_pair_id, row_arm), row in observed.items()
                                if row_arm == arm and row.get("condition") == condition
                            ),
                        }
                        for arm in (entry["id"] for entry in PHASE6_ARMS)
                    }
                    for condition in PHASE6_CONDITIONS
                },
                "paired_deltas_ms": {
                    arm: statistics.fmean(pair[arm] - pair["A"] for pair in by_pair.values()) for arm in ("B", "C")
                },
                "missing_timing": [],
            }
        else:
            performance["status"] = "unknown_missing_timing"
            performance["missing_timing"] = timing_missing
    usage_unknown = sum(
        not isinstance(row.get("input_tokens"), int)
        or isinstance(row.get("input_tokens"), bool)
        or not isinstance(row.get("output_tokens"), int)
        or isinstance(row.get("output_tokens"), bool)
        for row in observed.values()
    )
    cleanup_unknown = sum(row.get("cleanup_status") not in {"confirmed", "not_applicable"} for row in observed.values())
    staging_unknown = sum(not isinstance(row.get("staging_ms"), (int, float)) for row in observed.values())
    failures = sum(row.get("operational_failure") is True for row in observed.values())
    failure_unknown = sum(not isinstance(row.get("operational_failure"), bool) for row in observed.values())
    origins = sorted({str(row.get("quality_source", "unknown")) for row in observed.values()})

    def cost_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        costs = [
            float(row["cost_usd"])
            for row in rows
            if row.get("spend_status") == "provider_reported"
            and isinstance(row.get("cost_usd"), (int, float))
            and not isinstance(row.get("cost_usd"), bool)
            and math.isfinite(float(row["cost_usd"]))
            and float(row["cost_usd"]) >= 0
        ]
        unknown = len(rows) - len(costs)
        if not costs:
            status = "unknown"
        elif unknown:
            status = "partially_observed"
        else:
            status = "provider_reported"
        return {
            "status": status,
            "observed_outcomes": len(costs),
            "unknown_outcomes": unknown,
            "mean_usd": statistics.fmean(costs) if costs else None,
            "median_usd": statistics.median(costs) if costs else None,
        }

    cost = {
        "source": "provider_reported_only",
        "by_arm": {
            arm: cost_summary([row for (_pair_id, row_arm), row in observed.items() if row_arm == arm])
            for arm in (entry["id"] for entry in PHASE6_ARMS)
        },
        "by_condition": {
            condition: {
                arm: cost_summary(
                    [
                        row
                        for (_pair_id, row_arm), row in observed.items()
                        if row_arm == arm and row.get("condition") == condition
                    ]
                )
                for arm in (entry["id"] for entry in PHASE6_ARMS)
            }
            for condition in PHASE6_CONDITIONS
        },
    }
    policy_gate = _phase6_policy_gate(
        observed,
        quality_complete=quality_complete,
        quality_regressions=quality_regressions,
        trials=trials,
    )
    return {
        "schema": RECEIPT_SCHEMA,
        "command": "phase6-analyze",
        "status": "quality_incomplete"
        if not quality_complete
        else "quality_regression"
        if not quality_passed
        else "analyzed",
        "provenance": "synthetic_or_external_outcomes; this function makes no live execution claim",
        "expected_outcomes": len(expected),
        "observed_outcomes": len(observed),
        "missing_outcomes": len(missing),
        "invalid_or_duplicate_outcomes": invalid_rows,
        "quality": {
            "status": "complete" if quality_complete else "incomplete",
            "source_labels": origins,
            "scores": arm_quality,
            "scores_by_family_and_condition": family_condition_quality,
            "regressions_vs_A": quality_regressions,
            "unknown_fields": unknown_quality,
        },
        "checks": {
            "paired_design_complete": complete,
            "quality_evidence_complete": quality_complete,
            "no_per_family_quality_regression_vs_A": quality_passed,
            "performance_comparison_allowed": quality_passed,
        },
        "performance": performance,
        "cost": cost,
        "observability": {
            "usage_unknown_outcomes": usage_unknown,
            "staging_unknown_outcomes": staging_unknown,
            "cleanup_unknown_outcomes": cleanup_unknown,
            "operational_failures": failures,
            "operational_failure_unknown_outcomes": failure_unknown,
        },
        "policy_gate": policy_gate,
        "live_gate": (
            "No provider/Daytona call was made. Live evidence still needs FLEET_LIVE=1, existing campaign limits, "
            "verified per-arm profiles with a shared envelope, isolated Session/cache conditions, quality judging, "
            "observed usage/staging, and cleanup ownership receipts."
        ),
    }


def phase6_dry_run_receipt(path: Path = PHASE6_CASES_PATH, *, trials: int = PHASE6_TRIALS) -> dict[str, Any]:
    """Emit every paired outcome slot as unexecuted, with all observations unknown."""
    cases = load_phase6_cases(path)
    schedule = build_phase6_schedule(cases, trials=trials)
    return {
        "schema": RECEIPT_SCHEMA,
        "command": "phase6-dry-run",
        "status": "dry_run_not_executed",
        "provenance": "planning_fixture_only; no Fleet, provider, Daytona, or MLflow calls were made",
        "input_manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "paired_trials": trials,
        "outcomes": [
            {
                **cell,
                "sample_status": "not_executed",
                **dict.fromkeys(PHASE6_QUALITY_FIELDS, None),
                "quality_source": "unknown",
                "wall_time_ms": None,
                "input_tokens": None,
                "output_tokens": None,
                "cost_usd": None,
                "spend_status": "unknown",
                "usage_status": "unknown",
                "staging_ms": None,
                "cleanup_status": "unknown",
                "operational_failure": None,
                "source_authorized": None,
                "containment_confirmed": None,
                "commit_safe": None,
            }
            for cell in schedule
        ],
        "live_gate": "Provider execution requires FLEET_LIVE=1, exact arm-policy verification, and campaign limits.",
    }


def _remaining_campaign_seconds(deadline: float) -> float:
    """Return the remaining wall-clock budget or fail closed at the deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CampaignLimitError("campaign elapsed-time limit reached")
    return remaining


def _campaign_timeout(
    timeout_seconds: float | None,
    *,
    deadline: float | None = None,
) -> float | None:
    """Bound one blocking request by both its configured timeout and campaign deadline."""
    if deadline is None:
        return timeout_seconds
    remaining = _remaining_campaign_seconds(deadline)
    return remaining if timeout_seconds is None else min(timeout_seconds, remaining)


def _campaign_preflight(args: argparse.Namespace) -> CampaignPreflight:
    """Require explicit bounded limits before a provider-backed campaign."""
    values = (
        getattr(args, "campaign", None),
        getattr(args, "campaign_target", None),
        getattr(args, "max_elapsed_seconds", None),
        getattr(args, "max_admissions", None),
        getattr(args, "max_sandbox_concurrency", None),
        getattr(args, "spend_cap", None),
    )
    if any(value is None for value in values):
        raise BenchmarkError(
            "live benchmark requires campaign, target, elapsed, admissions, concurrency, and spend limits"
        )
    try:
        campaign = CampaignPreflight(
            name=args.campaign,
            target=args.campaign_target,
            max_elapsed_seconds=args.max_elapsed_seconds,
            max_admissions=args.max_admissions,
            max_sandbox_concurrency=args.max_sandbox_concurrency,
            total_spend_cap=args.spend_cap,
        )
        campaign.validate()
    except CampaignPreflightError as exc:
        raise BenchmarkError(str(exc)) from exc
    return campaign


def percentile(values: Sequence[float], percentile_value: int) -> float:
    """
    Calculate a deterministic nearest-rank percentile for a sequence of values.

    Parameters:
        values (Sequence[float]): Values from which to calculate the percentile.
        percentile_value (int): Percentile to calculate, from greater than 0 through 100.

    Returns:
        float: The selected percentile value.

    Raises:
        ValueError: If values is empty or percentile_value is outside the range (0, 100].
    """
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 < percentile_value <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(float(value) for value in values)
    rank = max(1, (percentile_value * len(ordered) + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def latency_gate(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """
    Apply performance and quality criteria to baseline and candidate aggregate receipts.

    Parameters:
        baseline (Mapping[str, Any]): Aggregate receipt used as the performance reference.
        candidate (Mapping[str, Any]): Aggregate receipt evaluated against the baseline.

    Returns:
        dict[str, Any]: A result containing individual check outcomes and an overall
            ``passed`` value.
    """
    baseline_p50 = float(baseline["end_to_end_ms"]["p50"])
    candidate_p50 = float(candidate["end_to_end_ms"]["p50"])
    checks = {
        "p50_reduced_by_50_percent": candidate_p50 <= baseline_p50 * 0.5,
        "p95_not_worse": float(candidate["end_to_end_ms"]["p95"]) <= float(baseline["end_to_end_ms"]["p95"]),
        "error_rate_not_worse": float(candidate["error_rate"]) <= float(baseline["error_rate"]),
        "quality_complete": bool(candidate.get("quality_complete")),
    }
    return {"passed": all(checks.values()), "checks": checks}


def quality_gate(evaluation: Mapping[str, Any]) -> bool:
    """
    Determine whether an evaluation satisfies the complete quality requirements.

    Parameters:
        evaluation (Mapping[str, Any]): Evaluation results, including its run mode,
        record count, and judge metrics.

    Returns:
        bool: `True` if the evaluation is non-dry-run, contains all quality records,
        and has perfect correctness and evidence-coverage mean scores; `False` otherwise.
    """
    if evaluation.get("dry_run") or int(evaluation.get("records", 0)) != len(QUALITY_RECORDS):
        return False
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, Mapping):
        return False

    def score(prefix: str) -> float | None:
        """
        Extract the first mean metric matching a key prefix.

        Parameters:
            prefix: Prefix used to select metric keys.

        Returns:
            The first matching metric converted to a float, or `None` when no matching metric exists.
        """
        values = [
            float(value)
            for key, value in metrics.items()
            if str(key).startswith(prefix) and str(key).endswith(("/mean", "_mean"))
        ]
        return values[0] if values else None

    return score("correctness") == 1.0 and score("evidence_coverage") == 1.0


def _require_live() -> None:
    """
    Require provider-backed execution to run in live mode.

    Raises:
        BenchmarkError: If `FLEET_LIVE` is not set to an accepted live-mode value.
    """
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise BenchmarkError("FLEET_LIVE=1 is required for provider-backed execution")


def _load_repository_env() -> None:
    """Load environment variables from the repository's `.env` file without overriding existing values."""
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _eval_otpm_backoff_seconds() -> float:
    """Optional pause after each predict so LLM judges miss the Turn OTPM window."""
    raw = os.environ.get("FLEET_EVAL_OTPM_BACKOFF_SECONDS", "").strip()
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value > 0.0 else 0.0


def _configure_judge_environment(judge_model: str) -> None:
    """
    Configure OpenAI adapter credentials for a Databricks-hosted judge model.

    Parameters:
        judge_model (str): Judge model URI whose `openai:/` prefix selects the Databricks credential mapping.

    Raises:
        BenchmarkError: If the judge model uses the OpenAI adapter and required Databricks credentials are unavailable.
    """
    if not judge_model.startswith("openai:/"):
        return
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    base_url = os.environ.get("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL", "").strip()
    if not token or not base_url:
        raise BenchmarkError("judge provider credentials are unavailable")
    os.environ.setdefault("OPENAI_API_KEY", token)
    os.environ.setdefault("OPENAI_API_BASE", base_url)
    os.environ.setdefault("OPENAI_BASE_URL", base_url)


def _sse_chunks(
    response: httpx.Response,
    *,
    deadline: float | None = None,
) -> Iterator[dict[str, Any]]:
    """
    Parse valid JSON objects from Server-Sent Event data lines.

    Parameters:
        response (httpx.Response): The response containing Server-Sent Event lines.

    Yields:
        dict[str, Any]: JSON object payloads from valid, non-terminal data events.
    """
    for line in response.iter_lines():
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _structured_answer(chunk: Mapping[str, Any]) -> str | None:
    """Extract answer text from a structured stream chunk.

    Parameters:
        chunk (Mapping[str, Any]): A stream chunk containing structured result data.

    Returns:
        str | None: The answer text when present in the chunk; otherwise, `None`.
    """
    data = chunk.get("data")
    if not isinstance(data, Mapping):
        return None
    value = data.get("value")
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("answer"), str):
        return str(value["answer"])
    return None


def _append_trajectory_value(values: list[str], value: object) -> None:
    """Keep trajectory evidence bounded by item count and total characters."""
    if not isinstance(value, str) or len(values) >= _TRAJECTORY_ITEM_LIMIT:
        return
    remaining = _TRAJECTORY_CHAR_LIMIT - sum(len(item) for item in values)
    if remaining > 0:
        values.append(value[:remaining])


def _upload_corpus(
    client: httpx.Client,
    corpus_path: Path,
    *,
    seed: int,
    timeout_seconds: float | None = None,
    deadline: float | None = None,
) -> str:
    """Upload the host-generated corpus as a bounded compressed Attachment."""
    with corpus_path.open("rb") as handle:
        compressed = gzip.compress(handle.read(), mtime=0)
    request_kwargs: dict[str, Any] = {
        "files": {
            "attachment": (
                f"fleet-corpus-{seed}.ndjson.gz",
                compressed,
                "application/gzip",
            )
        }
    }
    request_timeout = _campaign_timeout(timeout_seconds, deadline=deadline)
    if request_timeout is not None:
        request_kwargs["timeout"] = request_timeout
    response = client.post("/api/attachments", **request_kwargs)
    response.raise_for_status()
    if deadline is not None:
        _remaining_campaign_seconds(deadline)
    payload = response.json()
    if not isinstance(payload, Mapping) or not isinstance(payload.get("id"), str):
        raise BenchmarkError("Fleet corpus attachment response is malformed")
    return payload["id"]


def _termination_mode_from_chunk(chunk: Mapping[str, Any]) -> str | None:
    """
    Identify the termination mode signaled by a stream chunk.

    Parameters:
        chunk (Mapping[str, Any]): A stream event payload.

    Returns:
        str | None: The detected termination mode, or `None` when the chunk contains no recognized termination signal.
    """
    chunk_type = chunk.get("type")
    if (
        chunk_type == "data-rlm-output"
        and isinstance(chunk.get("data"), Mapping)
        and chunk["data"].get("output") == "FINAL submitted"
    ):
        return "typed_submit"
    if chunk_type == "reasoning-delta" and chunk.get("delta") == "Extract forced final output":
        return "native_extraction_fallback"
    return None


def run_turn(
    client: httpx.Client,
    query: str,
    *,
    nonce: str,
    session_id: str | None = None,
    attachment_ids: Sequence[str] = (),
    skill_selections: Sequence[Mapping[str, str]] = (),
    fixed_input: bool = False,
    timeout_seconds: float | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """
    Execute one Fleet Turn and collect its response, timing, usage, trace identifiers, and tool-call counts.

    Parameters:
        client (httpx.Client): HTTP client configured for the Fleet API.
        query (str): Prompt to submit for the Turn.
        nonce (str): Unique value used to identify the benchmark request.
        attachment_ids (Sequence[str]): Authorized Attachment IDs to pass to the Turn.

    Returns:
        dict[str, Any]: Bounded operational results, including the answer, identifiers, latency measurements,
        usage data, iteration count, tool-call counts, and termination mode.

    Raises:
        BenchmarkError: If the Turn reports an error, is aborted, or finishes for a reason other than `stop`.
            The exception preserves partial trace_id and run_id values when available.
    """
    prompt = query if fixed_input else f"{query}\n\nBenchmark nonce: {nonce}. It has no semantic meaning."
    if session_id is not None and not session_id.strip():
        raise BenchmarkError("reused Session ID must not be blank")
    answer_parts: list[str] = []
    answer: str | None = None
    usage: dict[str, Any] = {}
    trace_id: str | None = None
    run_id: str | None = None
    iterations = 0
    batch_calls = 0
    recursive_calls = 0
    recursive_batch_calls = 0
    peak_child_concurrency = 0
    concurrency_observed = False
    requested_attachment_ids = {str(value) for value in attachment_ids}
    attachment_accessed = False
    trajectory = {"codes": [], "outputs": []}
    first_event_ms: float | None = None
    termination_mode: str | None = None
    started = time.perf_counter()
    try:
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        if session_id is None:
            session_kwargs: dict[str, Any] = {"json": {"title": f"latency-{nonce}"}}
            session_timeout = _campaign_timeout(timeout_seconds, deadline=deadline)
            if session_timeout is not None:
                session_kwargs["timeout"] = session_timeout
            session = client.post("/api/sessions", **session_kwargs)
            session.raise_for_status()
            if deadline is not None:
                _remaining_campaign_seconds(deadline)
            session_id = str(session.json()["id"])
        stream_kwargs: dict[str, Any] = {
            "json": {
                "text": prompt,
                "attachment_ids": list(attachment_ids),
                "skill_selections": [dict(selection) for selection in skill_selections],
            },
            "headers": {"Idempotency-Key": f"rlm-latency-{uuid4()}"},
        }
        stream_timeout = _campaign_timeout(timeout_seconds, deadline=deadline)
        if stream_timeout is not None:
            stream_kwargs["timeout"] = stream_timeout
        with client.stream("POST", f"/api/sessions/{session_id}/turns", **stream_kwargs) as response:
            response.raise_for_status()
            if deadline is not None:
                _remaining_campaign_seconds(deadline)
            for chunk in _sse_chunks(response, deadline=deadline):
                if first_event_ms is None:
                    first_event_ms = (time.perf_counter() - started) * 1000
                metadata = chunk.get("messageMetadata")
                if isinstance(metadata, Mapping):
                    if isinstance(metadata.get("traceId"), str):
                        trace_id = str(metadata["traceId"])
                    if isinstance(metadata.get("runId"), str):
                        run_id = str(metadata["runId"])
                chunk_type = chunk.get("type")
                termination_mode = _termination_mode_from_chunk(chunk) or termination_mode
                if chunk_type == "text-delta" and isinstance(chunk.get("delta"), str):
                    answer_parts.append(str(chunk["delta"]))
                elif chunk_type == "data-structured-result":
                    answer = _structured_answer(chunk) or answer
                elif chunk_type == "data-usage" and isinstance(chunk.get("data"), Mapping):
                    raw_usage = chunk["data"].get("usage")
                    if isinstance(raw_usage, Mapping):
                        usage = dict(raw_usage)
                        iterations = int(usage.get("iterations", 0) or 0)
                elif chunk_type == "tool-input-available":
                    tool_name = chunk.get("toolName")
                    batch_calls += int(tool_name == "llm_query_batched")
                    recursive_calls += int(tool_name == "rlm_query")
                    recursive_batch_calls += int(tool_name == "rlm_query_batched")
                elif chunk_type == "tool-output-available" and isinstance(chunk.get("output"), Mapping):
                    raw_peak = chunk["output"].get("peak_child_concurrency")
                    if isinstance(raw_peak, int) and not isinstance(raw_peak, bool) and raw_peak >= 0:
                        peak_child_concurrency = max(peak_child_concurrency, raw_peak)
                        concurrency_observed = True
                elif chunk_type == "data-attachment" and isinstance(chunk.get("data"), Mapping):
                    data = chunk["data"]
                    accessed_id = data.get("attachment_id", data.get("attachmentId"))
                    attachment_accessed = attachment_accessed or str(accessed_id) in requested_attachment_ids
                elif chunk_type == "data-rlm-code" and isinstance(chunk.get("data"), Mapping):
                    _append_trajectory_value(trajectory["codes"], chunk["data"].get("code"))
                elif chunk_type == "data-rlm-output" and isinstance(chunk.get("data"), Mapping):
                    _append_trajectory_value(trajectory["outputs"], chunk["data"].get("output"))
                elif chunk_type in {"error", "abort"}:
                    raise BenchmarkError(str(chunk.get("errorText") or chunk.get("reason") or "Turn failed"))
                elif chunk_type == "finish" and chunk.get("finishReason") != "stop":
                    raise BenchmarkError("Turn did not finish with stop")
            if deadline is not None:
                _remaining_campaign_seconds(deadline)
    except Exception as exc:
        # Preserve partial provider observations as well as trace/run IDs for
        # failed streams. Campaign limits must account for work that failed
        # after provider execution began.
        exc.trace_id = trace_id  # type: ignore[attr-defined]
        exc.run_id = run_id  # type: ignore[attr-defined]
        exc.session_id = session_id  # type: ignore[attr-defined]
        exc.usage = usage  # type: ignore[attr-defined]
        exc.peak_child_concurrency = peak_child_concurrency  # type: ignore[attr-defined]
        exc.concurrency_observed = concurrency_observed  # type: ignore[attr-defined]
        raise
    if not concurrency_observed and recursive_calls == 0 and recursive_batch_calls == 0:
        # A fully consumed successful stream with no child-tool input events is
        # a positive observation that this Turn admitted no child Sandboxes.
        concurrency_observed = True
    return {
        "session_id": session_id,
        "answer": answer if answer is not None else "".join(answer_parts),
        "trace_id": trace_id,
        "run_id": run_id,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "first_event_ms": round(first_event_ms if first_event_ms is not None else -1.0, 3),
        "usage": usage,
        "iterations": iterations,
        "batch_calls": batch_calls,
        "recursive_calls": recursive_calls,
        "recursive_batch_calls": recursive_batch_calls,
        "peak_child_concurrency": peak_child_concurrency,
        "concurrency_observed": concurrency_observed,
        "termination_mode": termination_mode,
        "attachment_accessed": attachment_accessed,
        "trajectory": trajectory,
    }


def _active_policy(
    client: httpx.Client,
    *,
    timeout_seconds: float | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """
    Retrieve the active Fleet policy and its root and sub-model settings.

    Parameters:
        client (httpx.Client): HTTP client configured for the Fleet API.

    Returns:
        dict[str, Any]: Active profile name and selected model, token-limit, and reasoning settings.

    Raises:
        BenchmarkError: If Fleet settings do not expose a valid active profile.
    """
    request_timeout = _campaign_timeout(timeout_seconds, deadline=deadline)
    request_kwargs = {"timeout": request_timeout} if request_timeout is not None else {}
    response = client.get("/api/settings", **request_kwargs)
    response.raise_for_status()
    if deadline is not None:
        _remaining_campaign_seconds(deadline)
    payload = response.json()
    profile = payload.get("active_profile")
    scope = next(
        (item for item in payload.get("scopes", []) if isinstance(item, Mapping) and item.get("name") == profile),
        None,
    )
    if not isinstance(profile, str) or not isinstance(scope, Mapping):
        raise BenchmarkError("Fleet settings do not expose the active profile")
    fields = {
        str(item["path"]): item.get("value")
        for item in scope.get("fields", [])
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    return {
        "profile": profile,
        "root_model": fields.get("llm.root.model"),
        "root_max_tokens": fields.get("llm.root.max_tokens"),
        "root_reasoning_effort": fields.get("llm.root.reasoning_effort"),
        "sub_model": fields.get("llm.sub.model"),
        "sub_max_tokens": fields.get("llm.sub.max_tokens"),
        "recursion_enabled": fields.get("rlm.recursion_enabled"),
        "max_root_llm_calls": fields.get("rlm.max_llm_calls"),
        "max_provider_attempts": fields.get("rlm.max_provider_attempts"),
        "turn_timeout_seconds": fields.get("runtime.turn_timeout_seconds"),
        "max_active_daytona_leases": fields.get("runtime.max_active_daytona_leases"),
    }


def _execution_trace_id(
    mlflow_url: str,
    experiment_id: str,
    run_id: str,
    *,
    deadline: float | None = None,
) -> str | None:
    """
    Finds the MLflow trace for a Fleet run containing the execution span.

    Parameters:
        mlflow_url (str): MLflow tracking server URL.
        experiment_id (str): Experiment containing the trace.
        run_id (str): Fleet run identifier used to locate the trace.

    Returns:
        str | None: The trace ID when a matching execution trace is found, or `None` otherwise.
    """
    import mlflow

    mlflow.set_tracking_uri(mlflow_url)
    _flush_trace_exports_once(mlflow)
    if deadline is not None:
        _remaining_campaign_seconds(deadline)
    for _attempt in range(20):
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        traces = mlflow.search_traces(
            locations=[experiment_id],
            filter_string=f"tag.`fleet.run_id` = '{run_id}'",
            return_type="list",
        )
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        for trace in traces:
            if any(span.name == "RLM.execute" for span in trace.data.spans):
                if deadline is not None:
                    _remaining_campaign_seconds(deadline)
                return str(trace.info.trace_id)
        if deadline is None:
            time.sleep(0.25)
        else:
            time.sleep(min(0.25, _remaining_campaign_seconds(deadline)))
    return None


def _flush_trace_exports_once(mlflow: Any) -> None:
    """Flush one post-run export batch before checking the tracking store."""
    flush = getattr(mlflow, "flush_trace_async_logging", None)
    if callable(flush):
        flush(terminate=False)


def _attach_trace_identity(row: dict[str, Any], execution_trace_id: str | None) -> dict[str, Any]:
    """Require the public and execution traces to identify the same root trace."""
    stream_trace_id = row.get("trace_id")
    row["stream_trace_id"] = stream_trace_id
    row["execution_trace_id"] = execution_trace_id
    row["trace_id_match"] = (
        isinstance(stream_trace_id, str)
        and bool(stream_trace_id)
        and isinstance(execution_trace_id, str)
        and bool(execution_trace_id)
        and stream_trace_id == execution_trace_id
    )
    if not isinstance(stream_trace_id, str) or not stream_trace_id:
        raise BenchmarkError("SSE trace ID was missing")
    if not isinstance(execution_trace_id, str) or not execution_trace_id:
        raise BenchmarkError("execution trace was not found")
    if stream_trace_id != execution_trace_id:
        raise BenchmarkError("SSE and execution trace IDs did not match")
    # Keep the public SSE ID as the canonical receipt ID. Never overwrite it
    # with a separately discovered trace after this validation.
    row["trace_id"] = stream_trace_id
    return row


def _execution_trace_diagnostics(
    mlflow_url: str,
    trace_id: str,
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    """
    Collect bounded diagnostics from an MLflow execution trace for benchmark comparisons.

    Parameters:
        mlflow_url (str): MLflow tracking server URL.
        trace_id (str): Identifier of the execution trace to inspect.

    Returns:
        dict[str, Any]: Aggregated trace diagnostics, including language-model timing,
            context size, parsing and repair errors, response keys, detail overflow,
            sandbox execution counts, and broker metrics. Returns an unavailable status
            and error category when the trace cannot be inspected.
    """
    try:
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        import mlflow

        mlflow.set_tracking_uri(mlflow_url)
        _flush_trace_exports_once(mlflow)
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        trace = mlflow.get_trace(trace_id)
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        spans = list(trace.data.spans)
        trace_info = getattr(trace, "info", None)
        trace_tags = getattr(trace_info, "tags", None)
        preparation_trace_id = trace_tags.get("fleet.preparation_trace_id") if isinstance(trace_tags, Mapping) else None
        preparation_trace_status = "not_linked"
        if isinstance(preparation_trace_id, str) and preparation_trace_id and preparation_trace_id != trace_id:
            if deadline is not None:
                _remaining_campaign_seconds(deadline)
            try:
                preparation_trace = mlflow.get_trace(preparation_trace_id)
            except CampaignLimitError:
                raise
            except Exception:
                preparation_trace_status = "unavailable"
            else:
                spans.extend(preparation_trace.data.spans)
                preparation_trace_status = "available"
        repair_error_count = 0
        detail_overflowed = False
        root_lm_spans = [span for span in spans if span.name == "RLM.root_lm"]
        sub_lm_spans = [span for span in spans if span.name == "RLM.sub_lm"]
        provider_request_span_count = 0
        lifecycle_span_names = {
            "Turn.acquire_environment": "environment_acquisition",
            "Turn.stage_attachments": "attachment_staging",
            "Turn.prepare_capabilities": "capability_staging",
            "Turn.cleanup": "turn_cleanup",
        }
        lifecycle_durations: dict[str, list[float]] = {key: [] for key in lifecycle_span_names.values()}
        lifecycle_statuses: dict[str, str] = {}
        root_lm_wall_times = [
            float(span.outputs["wall_time_ms"])
            for span in root_lm_spans
            if isinstance(getattr(span, "outputs", None), Mapping)
            and isinstance(span.outputs.get("wall_time_ms"), (int, float))
        ]
        root_lm_context_chars = [
            int(span.inputs["context_chars"])
            for span in root_lm_spans
            if isinstance(getattr(span, "inputs", None), Mapping) and isinstance(span.inputs.get("context_chars"), int)
        ]
        adapter_parse_error_count = 0
        last_lm_response_keys: list[str] = []
        sandbox_execute_span_count = 0
        broker_metrics = _new_broker_metrics()
        for span in spans:
            if deadline is not None:
                _remaining_campaign_seconds(deadline)
            raw_span_type = getattr(span, "span_type", None)
            span_type = getattr(raw_span_type, "value", raw_span_type)
            if span_type == "LLM":
                provider_request_span_count += 1
            lifecycle_key = lifecycle_span_names.get(str(getattr(span, "name", "")))
            if lifecycle_key is not None:
                start_ns = getattr(span, "start_time_ns", None)
                end_ns = getattr(span, "end_time_ns", None)
                if (
                    isinstance(start_ns, int)
                    and not isinstance(start_ns, bool)
                    and isinstance(end_ns, int)
                    and not isinstance(end_ns, bool)
                    and end_ns >= start_ns
                ):
                    lifecycle_durations[lifecycle_key].append((end_ns - start_ns) / 1_000_000)
                lifecycle_outputs = getattr(span, "outputs", None)
                if isinstance(lifecycle_outputs, Mapping) and isinstance(lifecycle_outputs.get("phase_status"), str):
                    lifecycle_statuses[lifecycle_key] = str(lifecycle_outputs["phase_status"])
            outputs = getattr(span, "outputs", None)
            if isinstance(outputs, Mapping):
                result_kind = outputs.get("result_kind")
                if result_kind == "repair_error":
                    repair_error_count += 1
                if span.name == "RLM.execute" and outputs.get("failure_category") == "adapter_parse_error":
                    adapter_parse_error_count += 1
                last_lm_call = outputs.get("last_lm_call")
                if span.name == "RLM.execute" and isinstance(last_lm_call, Mapping):
                    keys = last_lm_call.get("response_keys")
                    if isinstance(keys, (list, tuple)):
                        last_lm_response_keys = [str(key) for key in keys[:32]]
                if span.name == "Turn.progress.warning" and "omitted" in str(outputs.get("message", "")):
                    detail_overflowed = True
                if span.name == "sandbox.execute":
                    sandbox_execute_span_count += 1
                    _merge_broker_metrics(broker_metrics, _broker_metrics(outputs))
            elif span.name == "sandbox.execute":
                sandbox_execute_span_count += 1
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        phase_durations_ms = {
            key: round(sum(values), 3) if values else None for key, values in lifecycle_durations.items()
        }
        cleanup_phase_status = lifecycle_statuses.get("turn_cleanup")
        cleanup_status = (
            "confirmed"
            if cleanup_phase_status == "completed"
            else "failed"
            if cleanup_phase_status is not None
            else "unknown"
        )
        return {
            "root_lm_span_count": len(root_lm_spans),
            "sub_lm_span_count": len(sub_lm_spans),
            "provider_request_span_count": provider_request_span_count,
            "phase_durations_ms": phase_durations_ms,
            "preparation_trace_status": preparation_trace_status,
            "lifecycle_phase_statuses": lifecycle_statuses,
            "turn_cleanup_status": cleanup_status,
            "root_lm_wall_time_ms": round(sum(root_lm_wall_times), 3),
            "root_lm_slowest_wall_time_ms": round(max(root_lm_wall_times), 3) if root_lm_wall_times else 0.0,
            "root_lm_max_context_chars": max(root_lm_context_chars) if root_lm_context_chars else 0,
            "adapter_parse_error_count": adapter_parse_error_count,
            "last_lm_response_keys": last_lm_response_keys,
            "repair_error_count": repair_error_count,
            "detail_overflowed": detail_overflowed,
            "sandbox_execute_span_count": sandbox_execute_span_count,
            "broker_metrics": broker_metrics,
        }
    except CampaignLimitError:
        raise
    except Exception as exc:
        return {"status": "unavailable", "error_category": type(exc).__name__}


def _tag_trace(
    mlflow_url: str,
    trace_id: str,
    *,
    workload_id: str,
    variant: str,
    sample: str,
    deadline: float | None = None,
) -> None:
    """Tag an MLflow trace with Fleet workload, performance variant, and sample metadata.

    Parameters:
        mlflow_url (str): MLflow tracking server URL.
        trace_id (str): Identifier of the trace to tag.
        workload_id (str): Workload identifier associated with the trace.
        variant (str): Performance variant associated with the trace.
        sample (str): Sample category associated with the trace.
    """
    if deadline is not None:
        _remaining_campaign_seconds(deadline)
    import mlflow

    mlflow.set_tracking_uri(mlflow_url)
    client = mlflow.MlflowClient()
    if deadline is not None:
        _remaining_campaign_seconds(deadline)
    client.set_trace_tag(trace_id, "fleet.workload_id", workload_id)
    if deadline is not None:
        _remaining_campaign_seconds(deadline)
    client.set_trace_tag(trace_id, "fleet.perf_variant", variant)
    if deadline is not None:
        _remaining_campaign_seconds(deadline)
    client.set_trace_tag(trace_id, "fleet.sample_kind", sample)
    if deadline is not None:
        _remaining_campaign_seconds(deadline)


def _aggregate(rows: Sequence[Mapping[str, Any]], *, workload_id: str = EVIDENCE_WORKLOAD_ID) -> dict[str, Any]:
    """
    Aggregate measured benchmark rows into latency, error, usage, execution,
    trace, diagnostics, broker, and corpus-quality metrics.

    Parameters:
        rows (Sequence[Mapping[str, Any]]): Benchmark sample records to aggregate.
        workload_id (str): Workload identifier used to determine whether corpus-quality metrics apply.

    Returns:
        dict[str, Any]: Aggregate metrics. Warmups and failed samples are excluded from success-based metrics.
            Includes sandbox execution counts and broker counters from trace diagnostics. Corpus-quality
            metrics are complete only when every measured sample passes all applicable corpus checks for
            the corpus workload.
    """
    measured = [row for row in rows if row.get("sample_kind") == "measured"]
    successes = [row for row in measured if not row.get("error_category")]
    durations = [float(row["duration_ms"]) for row in successes]
    first_events = [float(row["first_event_ms"]) for row in successes if float(row["first_event_ms"]) >= 0]
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0}
    for row in successes:
        for key, value in _usage_totals(row.get("usage")).items():
            usage[key] += value
    trace_matches = [bool(row["trace_id_match"]) for row in measured if isinstance(row.get("trace_id_match"), bool)]
    corpus_report_results = [
        row.get("corpus_validation", {}).get("passed") is True
        if isinstance(row.get("corpus_validation"), Mapping)
        else False
        for row in measured
    ]
    corpus_evidence_results = [
        row.get("corpus_evidence", {}).get("passed") is True
        if isinstance(row.get("corpus_evidence"), Mapping)
        else False
        for row in measured
    ]
    corpus_quality_results = [row.get("corpus_quality_passed") is True for row in measured]
    baseline_quality_rows = [
        quality for row in measured if isinstance((quality := row.get("baseline_quality")), Mapping)
    ]
    baseline_quality_complete = bool(measured) and len(baseline_quality_rows) == len(measured)
    baseline_quality = (
        {
            "method": "deterministic_text_heuristic",
            "quality_source": "frozen_reference_heuristic",
            "complete": baseline_quality_complete,
            "correctness_pass_rate": (
                sum(row.get("correctness") is True for row in baseline_quality_rows) / len(baseline_quality_rows)
                if baseline_quality_complete
                else None
            ),
            "grounded_evidence_pass_rate": (
                sum(row.get("grounded_evidence") is True for row in baseline_quality_rows) / len(baseline_quality_rows)
                if baseline_quality_complete
                else None
            ),
            "evidence_coverage_mean": (
                statistics.fmean(float(row["evidence_coverage"]) for row in baseline_quality_rows)
                if baseline_quality_complete
                and all(isinstance(row.get("evidence_coverage"), (int, float)) for row in baseline_quality_rows)
                else None
            ),
            "uncertainty_explicit_pass_rate": (
                sum(row.get("uncertainty_explicit") is True for row in baseline_quality_rows)
                / len(baseline_quality_rows)
                if baseline_quality_complete
                else None
            ),
            "task_completion_pass_rate": (
                sum(row.get("task_completion") is True for row in baseline_quality_rows) / len(baseline_quality_rows)
                if baseline_quality_complete
                else None
            ),
        }
        if workload_id == EVIDENCE_WORKLOAD_ID
        else None
    )
    lifecycle_phase_names = (
        "environment_acquisition",
        "attachment_staging",
        "capability_staging",
        "turn_cleanup",
    )
    lifecycle_metrics = {
        phase: {
            "observed_count": sum(
                isinstance(row.get("trace_diagnostics"), Mapping)
                and isinstance(row["trace_diagnostics"].get("phase_durations_ms"), Mapping)
                and isinstance(row["trace_diagnostics"]["phase_durations_ms"].get(phase), (int, float))
                for row in successes
            ),
            "mean_ms": (
                round(
                    statistics.fmean(
                        float(row["trace_diagnostics"]["phase_durations_ms"][phase])
                        for row in successes
                        if isinstance(row.get("trace_diagnostics"), Mapping)
                        and isinstance(row["trace_diagnostics"].get("phase_durations_ms"), Mapping)
                        and isinstance(row["trace_diagnostics"]["phase_durations_ms"].get(phase), (int, float))
                    ),
                    3,
                )
                if any(
                    isinstance(row.get("trace_diagnostics"), Mapping)
                    and isinstance(row["trace_diagnostics"].get("phase_durations_ms"), Mapping)
                    and isinstance(row["trace_diagnostics"]["phase_durations_ms"].get(phase), (int, float))
                    for row in successes
                )
                else None
            ),
        }
        for phase in lifecycle_phase_names
    }
    cleanup_statuses = [
        row["trace_diagnostics"].get("turn_cleanup_status")
        for row in successes
        if isinstance(row.get("trace_diagnostics"), Mapping)
    ]
    corpus_report_complete = bool(measured) and all(corpus_report_results)
    corpus_evidence_complete = bool(measured) and all(corpus_evidence_results)
    corpus_quality_complete = (
        bool(measured) and corpus_report_complete and corpus_evidence_complete and all(corpus_quality_results)
    )
    diagnostics: list[Mapping[str, Any]] = []
    for row in measured:
        item = row.get("trace_diagnostics")
        if isinstance(item, Mapping):
            diagnostics.append(item)
    broker_metrics = _new_broker_metrics()
    sandbox_execute_span_count = 0
    for item in diagnostics:
        with suppress(TypeError, ValueError):
            sandbox_execute_span_count += int(item.get("sandbox_execute_span_count", 0))
        _merge_broker_metrics(broker_metrics, item.get("broker_metrics"))
    return {
        "sample_count": len(measured),
        "end_to_end_ms": {
            "mean": round(statistics.fmean(durations), 3) if durations else None,
            "p50": round(percentile(durations, 50), 3) if durations else None,
            "p95": round(percentile(durations, 95), 3) if durations else None,
        },
        "first_runtime_event_ms": {
            "p50": round(percentile(first_events, 50), 3) if first_events else None,
            "p95": round(percentile(first_events, 95), 3) if first_events else None,
        },
        "error_rate": 1.0 - (len(successes) / len(measured)) if measured else 1.0,
        "iterations": sum(int(row.get("iterations", 0)) for row in successes),
        "batch_calls": sum(int(row.get("batch_calls", 0)) for row in successes),
        "recursive_calls": sum(int(row.get("recursive_calls", 0)) for row in successes),
        "recursive_batch_calls": sum(int(row.get("recursive_batch_calls", 0)) for row in successes),
        "peak_child_concurrency": max(int(row.get("peak_child_concurrency", 0)) for row in successes)
        if successes
        else 0,
        "typed_submit_count": sum(row.get("termination_mode") == "typed_submit" for row in successes),
        "provider_request_span_count": (
            sum(int(item["provider_request_span_count"]) for item in diagnostics)
            if diagnostics and all(isinstance(item.get("provider_request_span_count"), int) for item in diagnostics)
            else None
        ),
        "token_totals": usage,
        "trace_ids": [row["trace_id"] for row in successes if row.get("trace_id")],
        "trace_id_match_rate": (sum(trace_matches) / len(trace_matches)) if trace_matches else 0.0,
        "root_lm_span_count": sum(int(item.get("root_lm_span_count", 0)) for item in diagnostics),
        "sub_lm_span_count": sum(int(item.get("sub_lm_span_count", 0)) for item in diagnostics),
        "root_lm_wall_time_ms": round(sum(float(item.get("root_lm_wall_time_ms", 0.0)) for item in diagnostics), 3),
        "root_lm_slowest_wall_time_ms": round(
            max((float(item.get("root_lm_slowest_wall_time_ms", 0.0)) for item in diagnostics), default=0.0), 3
        ),
        "root_lm_max_context_chars": max(
            (int(item.get("root_lm_max_context_chars", 0)) for item in diagnostics), default=0
        ),
        "adapter_parse_error_count": sum(int(item.get("adapter_parse_error_count", 0)) for item in diagnostics),
        "last_lm_response_keys": next(
            (
                list(item.get("last_lm_response_keys", []))
                for item in reversed(diagnostics)
                if isinstance(item.get("last_lm_response_keys"), list)
            ),
            [],
        ),
        "repair_error_count": sum(int(item.get("repair_error_count", 0)) for item in diagnostics),
        "detail_overflowed": any(item.get("detail_overflowed") is True for item in diagnostics),
        "sandbox_execute_span_count": sandbox_execute_span_count,
        "broker_metrics": broker_metrics,
        "lifecycle": {
            "phase_durations_ms": lifecycle_metrics,
            "turn_cleanup_status": (
                "confirmed"
                if cleanup_statuses and all(status == "confirmed" for status in cleanup_statuses)
                else "failed"
                if any(status == "failed" for status in cleanup_statuses)
                else "unknown"
            ),
            "cleanup_status_observed_count": sum(status in {"confirmed", "failed"} for status in cleanup_statuses),
        },
        "baseline_quality": baseline_quality,
        "corpus_report_complete": corpus_report_complete if workload_id == CORPUS_WORKLOAD_ID else None,
        "corpus_evidence_complete": corpus_evidence_complete if workload_id == CORPUS_WORKLOAD_ID else None,
        "corpus_quality_complete": corpus_quality_complete if workload_id == CORPUS_WORKLOAD_ID else None,
        "quality_complete": (
            corpus_quality_complete
            if workload_id == CORPUS_WORKLOAD_ID
            else baseline_quality_complete
            if workload_id == EVIDENCE_WORKLOAD_ID
            else False
        ),
    }


def _new_broker_metrics() -> dict[str, int]:
    """Return a zeroed allowlist for broker metrics emitted by ``sandbox.execute``."""
    return {key: 0 for key in _BROKER_METRIC_KEYS}


def _broker_metrics(outputs: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Read nested broker metrics, falling back to legacy flat span keys."""
    nested = outputs.get("broker_metrics")
    return nested if isinstance(nested, Mapping) else outputs


def _merge_broker_metrics(target: dict[str, int], values: object) -> None:
    """Merge an allowlisted per-cell broker breakdown into aggregate totals."""
    if not isinstance(values, Mapping):
        return
    for raw_key, raw_value in values.items():
        key = str(raw_key)
        if key not in _BROKER_METRIC_KEY_SET or not isinstance(raw_value, int) or isinstance(raw_value, bool):
            continue
        if key.endswith("_max_ms"):
            target[key] = max(target[key], raw_value)
        else:
            target[key] += raw_value


def _usage_totals(value: object) -> dict[str, int]:
    """
    Aggregate approved token counters from nested mappings and sequences.

    Parameters:
        value (object): Nested token usage data to inspect.

    Returns:
        dict[str, int]: Totals for prompt, completion, reasoning, and cache-read tokens.
    """
    result = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "cache_read_tokens": 0}

    def visit(item: object) -> None:
        """Collect approved token counters from nested mappings and sequences.

        Parameters:
            item (object): Nested usage data containing token counters.
        """
        if isinstance(item, Mapping):
            for key, child in item.items():
                if isinstance(child, int) and not isinstance(child, bool):
                    if key in {"prompt_tokens", "input_tokens"}:
                        result["prompt_tokens"] += child
                    elif key in {"completion_tokens", "output_tokens"}:
                        result["completion_tokens"] += child
                    elif key == "reasoning_tokens":
                        result["reasoning_tokens"] += child
                    elif key in {
                        "cached_tokens",
                        "cache_read_input_tokens",
                        "cache_read_tokens",
                        "prompt_cache_hit_tokens",
                    }:
                        result["cache_read_tokens"] += child
                elif isinstance(child, (Mapping, list, tuple)):
                    visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return result


from scripts.benchmarks.usage_cost import observed_spend as _observed_spend


def _enforce_campaign_concurrency(row: Mapping[str, Any], campaign: CampaignPreflight) -> None:
    """Require complete child-concurrency evidence for one attempted Turn."""
    peak = row.get("peak_child_concurrency")
    concurrency_observed = row.get("concurrency_observed")
    if concurrency_observed is None:
        # Keep compatibility with bounded rows produced by older callers,
        # while still treating an absent or malformed peak as unknown.
        concurrency_observed = isinstance(peak, int) and not isinstance(peak, bool) and peak >= 0
    if concurrency_observed is not True or not isinstance(peak, int) or isinstance(peak, bool) or peak < 0:
        raise CampaignLimitError("campaign sandbox-concurrency observation was unavailable")
    if peak > campaign.max_sandbox_concurrency:
        raise CampaignLimitError("campaign sandbox-concurrency limit reached")


def _enforce_campaign_observations(
    row: Mapping[str, Any],
    campaign: CampaignPreflight,
    observed_spend: float,
) -> float:
    """Require provider spend and child-concurrency observations for one Turn."""
    cost, spend_observed = _observed_spend(row.get("usage"))
    if not spend_observed:
        raise CampaignLimitError("campaign spend observation was unavailable")
    observed_spend += cost
    if observed_spend > campaign.total_spend_cap:
        raise CampaignLimitError("campaign total-spend limit reached")
    _enforce_campaign_concurrency(row, campaign)
    return observed_spend


def _metrics_query(
    mlflow_url: str,
    experiment_id: str,
    *,
    workload_id: str,
    variant: str,
    timeout_seconds: float | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """
    Query MLflow latency metrics for the configured Fleet workload and performance variant.

    Parameters:
        mlflow_url (str): Base URL of the MLflow server.
        experiment_id (str): MLflow experiment identifier.
        workload_id (str): Fleet workload identifier used to filter traces.
        variant (str): Performance variant used to filter traces.

    Returns:
        dict[str, Any]: Latency metric results grouped by span name.
    """
    common = {
        "experiment_ids": [experiment_id],
        "view_type": 2,
        "metric_name": "latency",
        "aggregations": [
            {"aggregation_type": 4, "percentile_value": 50},
            {"aggregation_type": 4, "percentile_value": 95},
        ],
        "dimensions": ["span_status"],
        "max_results": 100,
    }
    results: dict[str, Any] = {}
    for span_name in ("fleet_turn", "RLM.root_lm", "tool.llm_query_batched"):
        request_timeout = 30.0 if timeout_seconds is None else timeout_seconds
        if deadline is not None:
            request_timeout = min(request_timeout, _remaining_campaign_seconds(deadline))
        payload = {
            **common,
            "filters": [
                f'trace.tag.fleet.workload_id = "{workload_id}"',
                f'trace.tag.fleet.perf_variant = "{variant}"',
                f'span.name = "{span_name}"',
            ],
        }
        request_kwargs: dict[str, Any] = {
            "json": payload,
            "timeout": request_timeout,
        }
        response = httpx.post(
            f"{mlflow_url.rstrip('/')}/api/3.0/mlflow/traces/metrics",
            **request_kwargs,
        )
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
        response.raise_for_status()
        results[span_name] = response.json()
        if deadline is not None:
            _remaining_campaign_seconds(deadline)
    return results


def _parse_skill_selections(values: Sequence[str]) -> list[dict[str, str]]:
    if len(values) > 4:
        raise BenchmarkError("at most four exact Skill selections are supported")
    selections: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        skill_id, separator, version = value.partition("@")
        try:
            canonical_id = str(UUID(skill_id))
        except ValueError:
            raise BenchmarkError("Skill selection requires UUID@version") from None
        if not separator or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", version):
            raise BenchmarkError("Skill selection requires UUID@version")
        if canonical_id in seen:
            raise BenchmarkError("Skill selections must not repeat an ID")
        seen.add(canonical_id)
        selections.append({"id": canonical_id, "expected_version": version})
    return selections


def _bounded_sample_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Retain evidence needed to compare conditions without exporting answers or trajectories."""

    def has_token_counter(value: object) -> bool:
        if isinstance(value, Mapping):
            return any(
                (
                    key in {"prompt_tokens", "input_tokens", "completion_tokens", "output_tokens"}
                    and isinstance(child, int)
                    and not isinstance(child, bool)
                )
                or has_token_counter(child)
                for key, child in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(has_token_counter(child) for child in value)
        return False

    diagnostics = row.get("trace_diagnostics")
    phases = diagnostics.get("phase_durations_ms") if isinstance(diagnostics, Mapping) else None
    usage = row.get("usage")
    tokens_observed = has_token_counter(usage)
    spend, spend_observed = _observed_spend(usage)
    return {
        key: row.get(key)
        for key in (
            "sample_kind",
            "session_condition",
            "session_id",
            "run_id",
            "trace_id",
            "duration_ms",
            "first_event_ms",
            "iterations",
            "batch_calls",
            "recursive_calls",
            "recursive_batch_calls",
            "peak_child_concurrency",
            "termination_mode",
            "error_category",
            "spend_status",
        )
    } | {
        "token_usage": _usage_totals(usage) if tokens_observed else None,
        "token_usage_status": "observed" if tokens_observed else "unknown",
        "provider_reported_spend_usd": round(spend, 8) if spend_observed else None,
        "baseline_quality": row.get("baseline_quality"),
        "corpus_quality_passed": row.get("corpus_quality_passed"),
        "phase_durations_ms": phases if isinstance(phases, Mapping) else None,
        "cleanup_status": diagnostics.get("turn_cleanup_status") if isinstance(diagnostics, Mapping) else None,
    }


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    """
    Run live latency benchmark samples and return an aggregate performance receipt.

    Parameters:
        args (argparse.Namespace): Benchmark configuration, including API and MLflow endpoints, experiment ID,
        variant, warmup count, and measured run count.

    Returns:
        dict[str, Any]: Benchmark receipt containing the active policy, aggregate sample metrics,
        and MLflow span metrics.
    """
    _require_live()
    campaign = _campaign_preflight(args)
    if args.warmups + args.runs > campaign.max_admissions:
        raise BenchmarkError("campaign admission limit is smaller than the requested benchmark samples")
    trial_cost_bound = getattr(args, "max_trial_cost_usd", None)
    if (
        isinstance(trial_cost_bound, bool)
        or not isinstance(trial_cost_bound, (int, float))
        or not math.isfinite(float(trial_cost_bound))
        or trial_cost_bound <= 0
    ):
        raise BenchmarkError("live benchmark requires --max-trial-cost-usd as a per-sample spend reservation")
    if trial_cost_bound > campaign.total_spend_cap:
        raise BenchmarkError("per-sample spend reservation cannot exceed the campaign spend cap")
    campaign_started = time.monotonic()
    campaign_deadline = campaign_started + campaign.max_elapsed_seconds
    cleanup_reserve_seconds = getattr(args, "cleanup_reserve_seconds", 900)
    try:
        campaign_budget = CampaignBudget(
            campaign,
            started_at=campaign_started,
            cleanup_reserve_seconds=cleanup_reserve_seconds,
        )
    except (TypeError, ValueError):
        raise BenchmarkError("campaign elapsed-time or cleanup reserve is invalid") from None
    max_trial_seconds = args.timeout
    if not math.isfinite(max_trial_seconds) or max_trial_seconds <= 0:
        raise BenchmarkError("benchmark sample timeout must be finite and positive")
    _remaining_campaign_seconds(campaign_deadline)
    workload_id = str(args.workload)
    skill_selections = _parse_skill_selections(getattr(args, "skill_selection", []))
    corpus_case: CorpusCase | None = make_corpus_case(args.corpus_seed) if workload_id == CORPUS_WORKLOAD_ID else None
    workload = corpus_workload(corpus_case) if corpus_case is not None else LATENCY_WORKLOAD
    rows: list[dict[str, Any]] = []
    observed_spend = 0.0
    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
        policy = _active_policy(
            client,
            timeout_seconds=min(args.timeout, _remaining_campaign_seconds(campaign_deadline)),
            deadline=campaign_deadline,
        )
        native_only = bool(getattr(args, "native_only", False))
        if native_only and (policy.get("profile") != "daytona-native" or policy.get("recursion_enabled") is not False):
            raise BenchmarkError("native-only baseline requires the daytona-native profile with recursion disabled")
        reuse_session = bool(getattr(args, "reuse_session", False))
        reused_session_id: str | None = None
        with tempfile.TemporaryDirectory(prefix="fleet-corpus-") as temp_dir:
            attachment_ids: tuple[str, ...] = ()
            if corpus_case is not None:
                corpus_path = Path(temp_dir) / "corpus.ndjson"
                write_corpus(corpus_case, corpus_path)
                attachment_ids = (
                    _upload_corpus(
                        client,
                        corpus_path,
                        seed=corpus_case.seed,
                        timeout_seconds=min(args.timeout, _remaining_campaign_seconds(campaign_deadline)),
                        deadline=campaign_deadline,
                    ),
                )
            for index in range(args.warmups + args.runs):
                _remaining_campaign_seconds(campaign_deadline)
                trial_started = time.monotonic()
                try:
                    campaign_budget.reserve(
                        upper_bound_usd=float(trial_cost_bound),
                        now=trial_started,
                        max_trial_seconds=max_trial_seconds,
                    )
                except CampaignAdmissionError as exc:
                    raise CampaignLimitError(str(exc)) from None
                trial_deadline = min(trial_started + max_trial_seconds, campaign_budget.admission_deadline)
                _remaining_campaign_seconds(trial_deadline)
                sample = "warmup" if index < args.warmups else "measured"
                nonce = f"{args.variant}-{sample}-{uuid4()}"
                sample_started = time.perf_counter()
                row: dict[str, Any] = {}
                try:
                    turn_kwargs: dict[str, Any] = {
                        "nonce": nonce,
                        "attachment_ids": attachment_ids,
                        "skill_selections": skill_selections,
                        "fixed_input": bool(getattr(args, "fixed_input", False)),
                        "timeout_seconds": min(args.timeout, _remaining_campaign_seconds(trial_deadline)),
                        "deadline": trial_deadline,
                    }
                    if reuse_session and reused_session_id is not None:
                        turn_kwargs["session_id"] = reused_session_id
                    row = run_turn(client, workload, **turn_kwargs)
                    if reuse_session:
                        reused_session_id = str(row["session_id"])
                    if native_only and (
                        row.get("recursive_calls") != 0
                        or row.get("recursive_batch_calls") != 0
                        or row.get("peak_child_concurrency") != 0
                    ):
                        raise BenchmarkError("native-only baseline observed recursive child work")
                    _remaining_campaign_seconds(trial_deadline)
                    execution_trace_id = (
                        _execution_trace_id(
                            args.mlflow_url,
                            args.experiment_id,
                            str(row["run_id"]),
                            deadline=trial_deadline,
                        )
                        if row.get("run_id")
                        else None
                    )
                    _attach_trace_identity(row, execution_trace_id)
                    row["trace_diagnostics"] = _execution_trace_diagnostics(
                        args.mlflow_url,
                        str(row["trace_id"]),
                        deadline=trial_deadline,
                    )
                    if row.get("trace_id"):
                        _tag_trace(
                            args.mlflow_url,
                            str(row["trace_id"]),
                            workload_id=workload_id,
                            variant=args.variant,
                            sample=sample,
                            deadline=trial_deadline,
                        )
                    if corpus_case is not None:
                        diagnostics = row.get("trace_diagnostics")
                        if isinstance(diagnostics, Mapping) and diagnostics.get("detail_overflowed") is True:
                            raise BenchmarkError("corpus detail event overflowed the bounded relay")
                        validation = validate_corpus_report(str(row.get("answer", "")), corpus_case)
                        trajectory = row.get("trajectory")
                        evidence = validate_corpus_evidence(
                            trajectory if isinstance(trajectory, Mapping) else {},
                            attachment_accessed=row.get("attachment_accessed") is True,
                        )
                        row["corpus_validation"] = validation.as_dict()
                        row["corpus_evidence"] = evidence.as_dict()
                        row["corpus_quality_passed"] = validation.passed and evidence.passed
                        if not row["corpus_quality_passed"]:
                            raise BenchmarkError("corpus report or execution evidence validation failed")
                    else:
                        row["baseline_quality"] = _baseline_evidence_quality(
                            str(row.get("answer", "")), termination_mode=row.get("termination_mode")
                        )
                except CampaignLimitError:
                    raise
                except Exception as exc:
                    # Extract partial trace_id and run_id from stream failures
                    partial_trace_id = getattr(exc, "trace_id", None) or row.get("trace_id")
                    partial_run_id = getattr(exc, "run_id", None) or row.get("run_id")
                    partial_session_id = getattr(exc, "session_id", None) or row.get("session_id")
                    partial_usage = getattr(exc, "usage", None)
                    partial_peak = getattr(exc, "peak_child_concurrency", None)
                    partial_concurrency_observed = getattr(exc, "concurrency_observed", None)
                    # Collect diagnostics for failed runs when trace_id is available
                    diagnostics = {}
                    if partial_trace_id:
                        diagnostics = _execution_trace_diagnostics(
                            args.mlflow_url,
                            str(partial_trace_id),
                            deadline=trial_deadline,
                        )
                    row = {
                        **row,
                        "duration_ms": row.get("duration_ms", round((time.perf_counter() - sample_started) * 1000, 3)),
                        "first_event_ms": row.get("first_event_ms", -1.0),
                        "error_category": type(exc).__name__,
                        "trace_id": partial_trace_id,
                        "run_id": partial_run_id,
                        "session_id": partial_session_id,
                        "usage": partial_usage if partial_usage is not None else row.get("usage"),
                        "peak_child_concurrency": (
                            partial_peak if partial_peak is not None else row.get("peak_child_concurrency")
                        ),
                        "concurrency_observed": (
                            partial_concurrency_observed
                            if partial_concurrency_observed is not None
                            else row.get("concurrency_observed")
                        ),
                        "trace_diagnostics": diagnostics if diagnostics else row.get("trace_diagnostics"),
                    }
                row["workload_id"] = workload_id
                if corpus_case is not None:
                    row["corpus_seed"] = corpus_case.seed
                row["sample_kind"] = sample
                row["session_condition"] = "warm_reuse" if reuse_session and index > 0 else "cold_new_session"
                rows.append(row)
                sample_cost, sample_cost_observed = _observed_spend(row.get("usage"))
                trace_diagnostics = row.get("trace_diagnostics")
                cleanup_confirmed = (
                    isinstance(trace_diagnostics, Mapping)
                    and trace_diagnostics.get("turn_cleanup_status") == "confirmed"
                )
                try:
                    if sample_cost_observed:
                        campaign_budget.settle(actual_usd=sample_cost, cleanup_confirmed=cleanup_confirmed)
                        observed_spend += sample_cost
                    else:
                        campaign_budget.settle_unknown(cleanup_confirmed=cleanup_confirmed)
                except CampaignAdmissionError as exc:
                    raise CampaignLimitError(str(exc)) from None
                row["spend_status"] = "provider_reported" if sample_cost_observed else "unknown_reserved"
                _enforce_campaign_concurrency(row, campaign)
                _remaining_campaign_seconds(campaign_deadline)
            _remaining_campaign_seconds(campaign_deadline)
    _remaining_campaign_seconds(campaign_deadline)
    aggregate = _aggregate(rows, workload_id=workload_id)
    _remaining_campaign_seconds(campaign_deadline)
    metrics: dict[str, Any]
    try:
        metrics = _metrics_query(
            args.mlflow_url,
            args.experiment_id,
            workload_id=workload_id,
            variant=args.variant,
            timeout_seconds=args.timeout,
            deadline=campaign_deadline,
        )
    except CampaignLimitError:
        raise
    except Exception as exc:
        _remaining_campaign_seconds(campaign_deadline)
        metrics = {"status": "unavailable", "error_category": type(exc).__name__}
    _remaining_campaign_seconds(campaign_deadline)
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "variant": args.variant,
        "workload_id": workload_id,
        "input_sha256": hashlib.sha256(workload.encode("utf-8")).hexdigest(),
        "fixed_input": bool(getattr(args, "fixed_input", False)),
        "corpus_seed": corpus_case.seed if corpus_case is not None else None,
        "active_policy": policy,
        "skill_selections": skill_selections,
        "warmups": args.warmups,
        "session_conditions": {
            "new_session_per_sample": not reuse_session,
            "warm_session_reuse_measured": any(
                row.get("session_condition") == "warm_reuse" and row.get("run_id") and row.get("error_category") is None
                for row in rows
            ),
            "warm_session_history_includes_prior_trials": reuse_session and len(rows) > 1,
            "provider_cache_condition": "uncontrolled",
        },
        "campaign_preflight": campaign.as_dict(),
        "campaign_observation": {
            "spend_control_mode": "post_run_observation_only; actual cost may exceed the reservation before detection",
            "observed_spend_usd": round(observed_spend, 8)
            if all(row.get("spend_status") == "provider_reported" for row in rows)
            else None,
            "spend_status": (
                "complete_provider_reported"
                if rows and all(row.get("spend_status") == "provider_reported" for row in rows)
                else "unknown_cost_charged_at_reserved_bound"
                if rows
                else "unknown"
            ),
            "max_trial_cost_usd": float(trial_cost_bound),
            "budget_ledger": campaign_budget.receipt(),
        },
        "aggregate": aggregate,
        "sample_records": [_bounded_sample_record(row) for row in rows],
        "mlflow_span_metrics": metrics,
    }


def prepare_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """
    Create or reuse the MLflow quality-evaluation dataset and register its judges.

    Parameters:
        args (argparse.Namespace): Configuration containing the MLflow tracking URL, experiment ID, and judge model URI.

    Returns:
        dict[str, Any]: Dataset ID, dataset name, and record count.

    Raises:
        BenchmarkError: If no judge model URI is provided.
    """
    import mlflow
    from mlflow.genai import datasets

    if not args.judge_model:
        raise BenchmarkError("prepare-evaluation requires --judge-model with an MLflow-supported model URI")
    if getattr(args, "judge_ab", False):
        raise BenchmarkError("--judge-ab is only valid with the evaluate command")
    mlflow.set_tracking_uri(args.mlflow_url)
    mlflow.set_experiment(experiment_id=args.experiment_id)
    dataset_name = _evaluation_dataset_name(args.mlflow_url)
    # Local MLflow otherwise implicitly excludes datasets older than seven days.
    # Databricks does not support the entity-store search filter.
    search_options = {} if args.mlflow_url == "databricks" else {"filter_string": f"name = '{DATASET_NAME}'"}
    existing = [
        item for item in datasets.search_datasets([args.experiment_id], **search_options) if item.name == dataset_name
    ]
    if len(existing) > 1:
        raise BenchmarkError("multiple quality datasets in the selected experiment require reconciliation")
    if existing:
        dataset = existing[0]
    else:
        dataset = datasets.create_dataset(name=dataset_name, experiment_id=args.experiment_id)
        dataset.merge_records(list(QUALITY_RECORDS))

    for name in JUDGE_NAMES:
        ensure_registered(name, args.judge_model, experiment_id=args.experiment_id)
    return {"dataset_id": dataset.dataset_id, "dataset_name": dataset.name, "records": len(dataset.to_df())}


def _quality_dataset(datasets: Any, tracking_url: str, experiment_id: str) -> Any:
    """Resolve a quality dataset within its owning experiment, never by global name."""
    name = _evaluation_dataset_name(tracking_url)
    # Databricks does not support MLflow's entity-store name filter, but its
    # experiment-scoped search still prevents selecting a same-named dataset
    # from another experiment.
    search_options = {} if tracking_url == "databricks" else {"filter_string": f"name = '{DATASET_NAME}'"}
    matches = [item for item in datasets.search_datasets([experiment_id], **search_options) if item.name == name]
    if len(matches) != 1:
        raise BenchmarkError("expected one quality dataset in the selected experiment; run prepare-evaluation")
    return datasets.get_dataset(dataset_id=matches[0].dataset_id)


def run_evaluation(args: argparse.Namespace) -> dict[str, Any]:
    """
    Run MLflow GenAI evaluation on the quality dataset.

    Parameters:
        args (argparse.Namespace): Command-line options containing the judge model, MLflow and Fleet API settings,
        experiment ID, timeout, and dry-run flag.

    Returns:
        dict[str, Any]: Evaluation receipt containing the dataset name, evaluation mode, record count, metrics,
        and quality-gate result.

    Raises:
        BenchmarkError: If live execution is not enabled or no judge model is configured.
    """
    _require_live()
    if not args.judge_model:
        raise BenchmarkError("evaluate requires --judge-model with an MLflow-supported model URI")
    _configure_judge_environment(args.judge_model)
    import mlflow
    from mlflow.genai import datasets

    mlflow.set_tracking_uri(args.mlflow_url)
    judge_ab = bool(getattr(args, "judge_ab", False))
    evaluation_experiment_id = args.experiment_id
    if judge_ab:
        evaluation_experiment_id = str(getattr(args, "evaluation_experiment_id", "")).strip()
        if not evaluation_experiment_id or evaluation_experiment_id == args.experiment_id:
            raise BenchmarkError(
                "--judge-ab requires a separate --evaluation-experiment-id from the dataset experiment"
            )
    mlflow.set_experiment(experiment_id=evaluation_experiment_id)
    dataset_name = _evaluation_dataset_name(args.mlflow_url)
    dataset = _quality_dataset(datasets, args.mlflow_url, args.experiment_id)
    frame = dataset.to_df().head(3) if args.dry_run else dataset.to_df()

    def predict_fn(query: str) -> str:
        """
        Generate an answer for a quality-evaluation query.

        Parameters:
            query (str): The query to submit for evaluation.

        Returns:
            str: The answer produced for the query.
        """
        with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)) as client:
            answer = str(run_turn(client, query, nonce=f"quality-{uuid4()}")["answer"])
        backoff = _eval_otpm_backoff_seconds()
        if backoff > 0.0:
            time.sleep(backoff)
        return answer

    run_name = args.run_name or f"quality-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    if judge_ab:
        baseline = _judges.build_judges(
            args.judge_model,
            inference_params=JUDGE_INFERENCE_PARAMS,
            generate_rationale_first=False,
            name_suffix="_baseline",
        )
        rationale_first = _judges.build_judges(
            args.judge_model,
            inference_params=JUDGE_INFERENCE_PARAMS,
            generate_rationale_first=True,
            name_suffix="_rationale_first",
        )
        scorers = [*baseline, *rationale_first]
    else:
        from mlflow.genai.scorers import get_scorer

        scorers = [
            get_scorer(name="correctness", experiment_id=args.experiment_id),
            get_scorer(name="evidence_coverage", experiment_id=args.experiment_id),
        ]
    if args.scorers:
        from scripts.benchmarks.scorers import build_scorers

        requested = [name.strip() for name in args.scorers.split(",") if name.strip()]
        scorers.extend(build_scorers(requested, judge_model=args.judge_model, guidelines=args.guidelines or None))
    started = time.perf_counter()
    with mlflow.start_run(run_name=run_name) as run:
        result = mlflow.genai.evaluate(
            data=frame,
            predict_fn=predict_fn,
            scorers=scorers,
        )
        run_id = run.info.run_id
    evaluation_duration_ms = round((time.perf_counter() - started) * 1000, 3)
    metrics = {str(key): value for key, value in result.metrics.items()}
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "dataset_name": dataset_name,
        "dataset_id": getattr(dataset, "dataset_id", None),
        "dataset_experiment_id": args.experiment_id,
        "dataset_snapshot": _dataset_snapshot(frame),
        "evaluation_experiment_id": evaluation_experiment_id,
        "run_id": run_id,
        "run_name": run_name,
        "dry_run": args.dry_run,
        "records": len(frame),
        "scorers": [getattr(scorer, "name", None) for scorer in scorers],
        "evaluation_duration_ms": evaluation_duration_ms,
        "available_token_cost_measurements": _available_token_cost_measurements(metrics),
        "metrics": metrics,
    }
    if judge_ab:
        judge_ab_receipt = _judge_ab_receipt(result, baseline, rationale_first)
        judge_ab_receipt.update(
            {
                "model": args.judge_model,
                "instructions": {
                    "correctness": CORRECTNESS_INSTRUCTIONS,
                    "evidence_coverage": EVIDENCE_COVERAGE_INSTRUCTIONS,
                },
                "inference_params": dict(JUDGE_INFERENCE_PARAMS),
                "rationale_settings": {"baseline": False, "rationale_first": True},
            }
        )
        receipt["judge_ab"] = judge_ab_receipt
        receipt["quality_complete"] = None
    else:
        receipt["quality_complete"] = quality_gate(receipt)
    return receipt


def _dataset_snapshot(frame: Any) -> dict[str, Any]:
    """Hash the exact bounded dataframe passed to an evaluation run."""
    serialized = frame.to_json(orient="records", date_format="iso", default_handler=str)
    return {
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "records": len(frame),
    }


def _score_value(value: object) -> bool | str | None:
    """Project one scorer value into a bounded receipt-safe representation."""
    if isinstance(value, bool):
        return value
    if type(value).__module__ == "numpy" and type(value).__name__ in {"bool", "bool_"}:
        return bool(value)
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isnan(value):
        return None
    return str(value)[:128]


def _available_token_cost_measurements(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only token/cost metrics when MLflow exposes them in an evaluation result."""
    return {
        str(key): value
        for key, value in metrics.items()
        if any(term in str(key).lower() for term in ("token", "cost"))
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }


def _judge_ab_receipt(result: Any, baseline: Sequence[Any], rationale_first: Sequence[Any]) -> dict[str, Any]:
    """Summarize a same-input baseline/rationale-first evaluation."""
    frame = getattr(result, "result_df", None)
    scores: dict[str, dict[str, float | None]] = {}
    for variant, scorers in (("baseline", baseline), ("rationale_first", rationale_first)):
        variant_scores: dict[str, float | None] = {}
        for scorer in scorers:
            name = str(getattr(scorer, "name", ""))
            values = []
            if frame is not None and f"{name}/value" in frame:
                values = [
                    projected
                    for value in frame[f"{name}/value"].tolist()
                    if isinstance((projected := _score_value(value)), bool)
                ]
            variant_scores[name.removesuffix(f"_{variant}")] = sum(values) / len(values) if values else None
        scores[variant] = variant_scores

    agreement: dict[str, dict[str, float | int | None]] = {}
    disagreements: list[dict[str, Any]] = []
    if frame is not None:
        for base_scorer, rationale_scorer in zip(baseline, rationale_first, strict=True):
            base_name = str(getattr(base_scorer, "name", ""))
            rationale_name = str(getattr(rationale_scorer, "name", ""))
            total = 0
            matching = 0
            for index, row in frame.iterrows():
                base_value = _score_value(row.get(f"{base_name}/value"))
                rationale_value = _score_value(row.get(f"{rationale_name}/value"))
                if base_value is None or rationale_value is None:
                    continue
                total += 1
                if base_value == rationale_value:
                    matching += 1
                elif len(disagreements) < 64:
                    disagreements.append(
                        {
                            "row_index": int(index) if isinstance(index, int) else str(index),
                            "judge": base_name.removesuffix("_baseline"),
                            "baseline": base_value,
                            "rationale_first": rationale_value,
                        }
                    )
            judge_name = base_name.removesuffix("_baseline")
            agreement[judge_name] = {
                "matching": matching,
                "compared": total,
                "rate": matching / total if total else None,
            }
    policies = {
        "baseline": [_judges.normalized_judge_policy(scorer) for scorer in baseline],
        "rationale_first": [_judges.normalized_judge_policy(scorer) for scorer in rationale_first],
    }
    return {
        "scores": scores,
        "agreement": agreement,
        "disagreements": disagreements,
        "accuracy": None,
        "accuracy_basis": (
            "No independent reference labels were supplied; expectations are evaluation inputs, not labels."
        ),
        "policies": policies,
    }


def build_parser() -> argparse.ArgumentParser:
    """
    Create the command-line argument parser for benchmark, evaluation, and comparison workflows.

    Returns:
        argparse.ArgumentParser: Parser configured with command, endpoint, sampling, evaluation, input,
        and output options.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "benchmark",
            "prepare-evaluation",
            "evaluate",
            "compare",
            "phase6-plan",
            "phase6-dry-run",
            "phase6-analyze",
        ),
    )
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--mlflow-url", default=DEFAULT_MLFLOW_URL)
    parser.add_argument("--experiment-id", default="1")
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--campaign", help="Explicit bounded live campaign reference")
    parser.add_argument(
        "--target",
        "--campaign-target",
        dest="campaign_target",
        help="Explicit non-secret provider target reference",
    )
    parser.add_argument("--max-elapsed-seconds", type=int)
    parser.add_argument("--max-admissions", type=int)
    parser.add_argument("--max-sandbox-concurrency", type=int)
    parser.add_argument("--spend-cap", type=float)
    parser.add_argument(
        "--max-trial-cost-usd",
        type=float,
        help="Per-sample LM spend reservation; provider-reported overruns are detected only after the Turn",
    )
    parser.add_argument(
        "--cleanup-reserve-seconds",
        type=int,
        default=900,
        help="Campaign time held for cleanup and receipt finalization after provider admissions",
    )
    parser.add_argument("--workload", choices=WORKLOAD_CHOICES, default=EVIDENCE_WORKLOAD_ID)
    parser.add_argument("--corpus-seed", choices=CORPUS_SEEDS, type=int, default=CORPUS_SEEDS[0])
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument(
        "--fixed-input",
        action="store_true",
        help="Submit the exact frozen workload text without a per-trial prompt nonce",
    )
    parser.add_argument(
        "--skill-selection",
        action="append",
        default=[],
        metavar="UUID@VERSION",
        help="Select an exact manifested Skill version for matched benchmark runs",
    )
    parser.add_argument(
        "--reuse-session",
        action="store_true",
        help="Reuse the first sample's Session for later Turns; records warm reuse with prior Turn history",
    )
    parser.add_argument(
        "--native-only",
        action="store_true",
        help="Require the native profile before admission and reject observed full-child tool use",
    )
    parser.add_argument("--timeout", type=float, default=2_000.0)
    parser.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help="MLflow-supported judge URI (default: the probe-verified qwen serving endpoint)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--scorers",
        default="",
        help=(
            "Comma-separated extra scorers beyond correctness/evidence_coverage: "
            "response_present, tool_evidence_used, guidelines, retrieval_groundedness"
        ),
    )
    parser.add_argument(
        "--guidelines",
        default="",
        help="Guideline text for the guidelines scorer",
    )
    parser.add_argument(
        "--run-name",
        default="",
        help="MLflow run name for evaluate (default: quality-<UTC timestamp>); reuse a name to build baselines",
    )
    parser.add_argument(
        "--judge-ab",
        action="store_true",
        help="Evaluate baseline and rationale-first judges in memory; never registers either variant",
    )
    parser.add_argument(
        "--evaluation-experiment-id",
        default="",
        help="Separate MLflow experiment for --judge-ab evaluation runs",
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--quality", type=Path)
    parser.add_argument("--phase6-cases", type=Path, default=PHASE6_CASES_PATH)
    parser.add_argument("--phase6-outcomes", type=Path)
    parser.add_argument("--phase6-trials", type=int, default=PHASE6_TRIALS)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """
    Run the selected CLI command and write its result as a JSON receipt.

    Parameters:
        argv (Sequence[str] | None): Optional command-line arguments; uses the process arguments when omitted.

    Returns:
        int: `0` when the command succeeds, `1` when it fails.
    """
    _load_repository_env()
    args = build_parser().parse_args(argv)
    try:
        if args.judge_ab and args.command != "evaluate":
            raise BenchmarkError("--judge-ab is only valid with the evaluate command")
        if args.command == "benchmark":
            if args.warmups < 0 or args.runs < 1:
                raise BenchmarkError("warmups must be nonnegative and runs must be positive")
            receipt = run_benchmark(args)
        elif args.command == "prepare-evaluation":
            receipt = prepare_evaluation(args)
        elif args.command == "evaluate":
            receipt = run_evaluation(args)
        elif args.command == "phase6-plan":
            receipt = phase6_plan_receipt(args.phase6_cases, trials=args.phase6_trials)
        elif args.command == "phase6-dry-run":
            receipt = phase6_dry_run_receipt(args.phase6_cases, trials=args.phase6_trials)
        elif args.command == "phase6-analyze":
            if args.phase6_outcomes is None:
                raise BenchmarkError("phase6-analyze requires --phase6-outcomes")
            payload = json.loads(args.phase6_outcomes.read_text(encoding="utf-8"))
            outcomes = payload.get("outcomes") if isinstance(payload, Mapping) else None
            if not isinstance(outcomes, list):
                raise BenchmarkError("Phase 6 outcome file must contain an outcomes list")
            receipt = analyze_phase6_outcomes(outcomes, cases_path=args.phase6_cases, trials=args.phase6_trials)
        else:
            if args.baseline is None or args.candidate is None:
                raise BenchmarkError("compare requires --baseline and --candidate")
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["aggregate"]
            candidate = json.loads(args.candidate.read_text(encoding="utf-8"))["aggregate"]
            if args.quality is not None:
                candidate["quality_complete"] = quality_gate(json.loads(args.quality.read_text(encoding="utf-8")))
            receipt = latency_gate(baseline, candidate)
    except Exception as exc:
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "generated_at": datetime.now(UTC).isoformat(),
            "command": args.command,
            "status": "failed",
            "error_category": type(exc).__name__,
        }
        exit_code = 1
    else:
        exit_code = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

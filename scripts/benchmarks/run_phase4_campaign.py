"""Run the sealed Phase 4 four-arm ablation.

The default command is a credential-free validator.  Provider-backed
execution requires ``--live`` plus ``FLEET_LIVE=1`` and an explicit bounded
campaign.  Exactly 144 trial descriptors are admitted at most once; a worker
failure is recorded as a failed attempt and is never retried or replaced.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ``uv run python scripts/benchmarks/run_phase4_campaign.py`` places the
# script directory, rather than the repository root, at ``sys.path[0]``.
# Make the maintained ``scripts.benchmarks`` package importable without
# requiring callers to set PYTHONPATH manually.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmarks.campaign import CampaignPreflight
from scripts.benchmarks.phase4_campaign import (
    PublicRateCard,
    Trial,
    TrialEnvelope,
    TrialObservation,
    arm_specs,
    corpus_sha256,
    execute_campaign,
    load_cases,
    observation_from_mapping,
    paired_bootstrap,
    policy_sha256,
    receipt,
)

CORPUS_PATH = REPO_ROOT / "scripts" / "benchmarks" / "phase4_cases.json"
WORKER_PATH = REPO_ROOT / "scripts" / "benchmarks" / "phase4_arm_worker.py"
BASELINE_REVISION = "9b526f50f0aeec37ca399bc8ef19ec8a95d3bead"
CAMPAIGN_NAME = "phase4-ablation-20260910"
CAMPAIGN_TARGET = "databricks-gcp-standard"
MODEL_ID = "databricks-deepseek-v4-flash-0731"
MAX_ELAPSED_SECONDS = 4 * 60 * 60
ADMISSION_SECONDS = 3 * 60 * 60 + 45 * 60
CLEANUP_RESERVE_SECONDS = 15 * 60
MAX_ADMISSIONS = 144
MAX_SANDBOX_CONCURRENCY = 5
TOTAL_SPEND_CAP = 50.0
_LIVE_VALUES = frozenset({"1", "true", "yes"})


class Phase4CampaignError(RuntimeError):
    """The operator campaign cannot be admitted or its receipt is invalid."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new JSON receipt below .scratch")
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH, help="sealed 12-case fixture")
    parser.add_argument("--baseline-worktree", type=Path, help="clean detached checkout of the frozen baseline")
    parser.add_argument("--campaign", default=CAMPAIGN_NAME)
    parser.add_argument("--target", default=CAMPAIGN_TARGET)
    parser.add_argument("--live", action="store_true", help="admit provider-backed A/B/C/D execution")
    parser.add_argument("--dry-run", action="store_true", help="run the local deterministic adapter smoke path")
    return parser


def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _path_is_below_scratch(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    scratch = (REPO_ROOT / ".scratch").resolve()
    try:
        resolved.relative_to(scratch)
    except ValueError:
        return False
    return resolved.suffix == ".json" and resolved != scratch


def _candidate_revision(*, require_clean: bool) -> str:
    try:
        revision = _git("rev-parse", "HEAD")
        branch = _git("branch", "--show-current")
        dirty = _git("status", "--porcelain", "--untracked-files=all")
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase4CampaignError("candidate identity is unavailable") from exc
    if len(revision) != 40 or not branch or branch in {"main", "master"}:
        raise Phase4CampaignError("candidate must be a checked-out non-main commit")
    if require_clean and dirty:
        raise Phase4CampaignError("candidate worktree must be clean before campaign admission")
    return revision


def _prepare_baseline(revision: str, supplied: Path | None) -> tuple[Path, bool]:
    if supplied is not None:
        path = supplied.expanduser().resolve()
        try:
            actual = _git("rev-parse", "HEAD", cwd=path)
            dirty = _git("status", "--porcelain", "--untracked-files=all", cwd=path)
        except (OSError, subprocess.SubprocessError) as exc:
            raise Phase4CampaignError("baseline worktree is unavailable") from exc
        if actual != revision or dirty:
            raise Phase4CampaignError("baseline worktree must be clean at the frozen revision")
        return path, False
    parent = Path(tempfile.mkdtemp(prefix=".fleet-p4-baseline-", dir=REPO_ROOT.parent))
    path = parent / "checkout"
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(path), revision],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        with suppress(OSError):
            parent.rmdir()
        raise Phase4CampaignError("could not create frozen baseline worktree") from exc
    return path, True


def _remove_baseline(path: Path) -> None:
    parent = path.parent
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if parent.name.startswith(".fleet-p4-baseline-") and parent.parent == REPO_ROOT.parent:
        with suppress(OSError):
            parent.rmdir()


def _envelope() -> TrialEnvelope:
    """Reserve worst-case Root + child model calls and five Daytona sandboxes."""
    # Root: eight calls at the configured 4,096-token input / 1,024-token
    # output ceilings.  Children: four children x four calls at 4,096/512.
    # The input ceiling is deliberately independent of fixture size so a
    # provider that reports unusually large context still remains bounded.
    return TrialEnvelope(
        input_tokens=(8 * 4_096) + (4 * 4 * 4_096),
        output_tokens=(8 * 1_024) + (4 * 4 * 512),
        cache_read_tokens=0,
        retries=1,
        sandbox_seconds=90,
        sandbox_count=MAX_SANDBOX_CONCURRENCY,
        vcpus=4,
        gib_ram=8,
        gib_storage=8,
    )


def _profile_contract() -> Mapping[str, Any]:
    from fleet_rlm.config.loader import load_profile_environment_contracts

    contracts = {item.name: item for item in load_profile_environment_contracts()}
    contract = contracts.get("phase4-campaign")
    if contract is None or not contract.recursion_enabled:
        raise Phase4CampaignError("phase4-campaign profile is not configured")
    return contract


def _require_live_preflight() -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise Phase4CampaignError("FLEET_LIVE=1 is required for provider-backed Phase 4 execution")
    try:
        from fleet_rlm.config.loader import require_live_execution

        require_live_execution()
        contract = _profile_contract()
        if (
            contract.runtime_environment != "daytona"
            or contract.root_model != MODEL_ID
            or contract.sub_model != MODEL_ID
            or contract.root_max_tokens != 1_024
            or contract.sub_max_tokens != 512
        ):
            raise Phase4CampaignError("phase4-campaign model or runtime contract is not approved")
    except Exception as exc:
        raise Phase4CampaignError("live policy preflight failed") from exc
    names = (*contract.provider_environment_names, *contract.daytona_snapshot_environment_names)
    missing = sorted({name for name in names if not os.environ.get(name)})
    if missing:
        raise Phase4CampaignError("live provider environment is incomplete")


def _worker_env(*, candidate: str) -> dict[str, str]:
    env = dict(os.environ)
    env["FLEET_LIVE"] = "1"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(item for item in (candidate, existing) if item)
    return env


def _unavailable(category: str) -> TrialObservation:
    return TrialObservation(
        answer="",
        cited_evidence=(),
        uncertainty="",
        completed=False,
        authorization_confirmed=False,
        cleanup_confirmed=False,
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        sandbox_seconds=None,
        latency_ms=None,
        root_lm_calls=None,
        child_lm_calls=None,
        delegated_bytes=None,
        error_category=category,
    )


class SubprocessArmRunner:
    """Run one arm in the selected immutable checkout and parse one observation."""

    def __init__(self, *, candidate_root: Path, baseline_root: Path, worker: Path, timeout_seconds: int = 150) -> None:
        self.candidate_root = candidate_root
        self.baseline_root = baseline_root
        self.worker = worker
        self.timeout_seconds = timeout_seconds
        self.env = _worker_env(candidate=str(candidate_root))

    def __call__(self, trial: Trial, case: Any) -> TrialObservation:
        cwd = self.baseline_root if trial.arm == "C" else self.candidate_root
        descriptor = {
            "trial": {
                "case_id": trial.case_id,
                "classification": trial.classification,
                "repeat": trial.repeat,
                "arm": trial.arm,
                "arm_order": list(trial.arm_order),
            },
            "case": {
                "id": case.identifier,
                "classification": case.classification,
                "sources": dict(case.sources),
                "question": case.question,
                "expected_answer": case.expected_answer,
                "required_evidence": list(case.required_evidence),
                "required_uncertainty": case.required_uncertainty,
                "forbidden_claims": list(case.forbidden_claims),
            },
        }
        try:
            completed = subprocess.run(
                ["uv", "run", "python", str(self.worker), "--worker"],
                cwd=cwd,
                env=self.env,
                input=json.dumps(descriptor, ensure_ascii=True),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return _unavailable("worker_failed")
        if completed.returncode != 0:
            return _unavailable("worker_failed")
        try:
            payload = json.loads(completed.stdout)
            if not isinstance(payload, Mapping):
                raise ValueError
            return observation_from_mapping(payload)
        except (json.JSONDecodeError, ValueError, TypeError):
            return _unavailable("observation_unavailable")


def _dry_runner(trial: Trial, case: Any) -> TrialObservation:
    """Credential-free smoke adapter; it never claims provider evidence."""
    recursive = trial.arm in {"C", "D"}
    return TrialObservation(
        answer=case.expected_answer,
        cited_evidence=tuple(case.required_evidence),
        uncertainty=case.required_uncertainty,
        completed=True,
        authorization_confirmed=True,
        cleanup_confirmed=True,
        input_tokens=32,
        output_tokens=16,
        cache_read_tokens=0,
        sandbox_seconds=1 if recursive else 0,
        latency_ms=1.0,
        root_lm_calls=1,
        child_lm_calls=1 if recursive else 0,
        delegated_bytes=64 if recursive else 0,
        sandbox_count=1 if recursive else 0,
        resource_shape=(4, 8, 8) if recursive else None,
    )


def _campaign_metadata(*, envelope: TrialEnvelope, specs: tuple[Any, ...], name: str, target: str) -> dict[str, object]:
    rates = PublicRateCard()
    return {
        "name": name,
        "target": target,
        "max_elapsed_seconds": MAX_ELAPSED_SECONDS,
        "admission_deadline_seconds": ADMISSION_SECONDS,
        "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
        "max_admissions": MAX_ADMISSIONS,
        "max_sandbox_concurrency": MAX_SANDBOX_CONCURRENCY,
        "total_spend_cap_usd": str(TOTAL_SPEND_CAP),
        "reservation_upper_bound_usd": str(envelope.upper_bound_usd(rates)),
        "policy_overlay": {
            "root_max_iters": 6,
            "root_max_lm_calls": 8,
            "root_max_provider_attempts": 8,
            "child_max_iters": 4,
            "child_max_lm_calls": 4,
            "max_children": 4,
            "max_total_sandboxes": 5,
            "root_max_tokens": 1_024,
            "child_max_tokens": 512,
            "root_temperature": 0,
            "child_temperature": 0,
            "root_retries": 0,
            "child_retries": 0,
            "daytona_max_lifetime_seconds": envelope.sandbox_seconds,
            "maximum_trial_lifetime_seconds": envelope.maximum_lifetime_seconds,
        },
        "pricing": {
            "cloud_region_assumption": "GCP standard public pricing",
            "model": MODEL_ID,
            "input_usd_per_million": str(rates.input_usd_per_million),
            "output_usd_per_million": str(rates.output_usd_per_million),
            "cache_read_usd_per_million": str(rates.cache_read_usd_per_million),
            "daytona_vcpu_usd_per_hour": str(rates.vcpu_usd_per_hour),
            "daytona_gib_ram_usd_per_hour": str(rates.gib_ram_usd_per_hour),
            "daytona_gib_storage_usd_per_hour": str(rates.gib_storage_usd_per_hour),
        },
        "arms": [
            {
                "arm": spec.arm,
                "execution": spec.execution,
                "uses_child_sandboxes": spec.uses_child_sandboxes,
                "source_revision": spec.source_revision,
            }
            for spec in specs
        ],
        "c_snapshot_context_difference": (
            "Arm C executes the frozen baseline checkout; its durable snapshot/context contract is retained "
            "from that revision while source material, decoding, budgets, and campaign policy are held equal."
        ),
    }


def run(args: argparse.Namespace) -> int:
    output = args.output.expanduser().resolve()
    if not _path_is_below_scratch(output) or output.exists():
        raise Phase4CampaignError("receipt output must be a new JSON path below .scratch")
    corpus = args.corpus.expanduser().resolve()
    if not corpus.is_file():
        raise Phase4CampaignError("sealed Phase 4 corpus is unavailable")
    load_dotenv(REPO_ROOT / ".env", override=False)
    candidate_revision = _candidate_revision(require_clean=args.live)
    if args.live:
        _require_live_preflight()
    cases = load_cases(corpus)
    specs = arm_specs(baseline_revision=BASELINE_REVISION, candidate_revision=candidate_revision)
    envelope = _envelope()
    policy = CampaignPreflight(
        args.campaign,
        args.target,
        MAX_ELAPSED_SECONDS,
        MAX_ADMISSIONS,
        MAX_SANDBOX_CONCURRENCY,
        TOTAL_SPEND_CAP,
    )
    policy.validate()
    baseline_root, remove_baseline = (
        _prepare_baseline(BASELINE_REVISION, args.baseline_worktree)
        if args.live
        else (
            REPO_ROOT,
            False,
        )
    )
    try:
        runner = (
            SubprocessArmRunner(candidate_root=REPO_ROOT, baseline_root=baseline_root, worker=WORKER_PATH)
            if args.live
            else _dry_runner
        )
        rows = execute_campaign(cases=cases, preflight=policy, envelope=envelope, runner=runner)
        bootstrap = (
            paired_bootstrap(rows)
            if len(rows) == MAX_ADMISSIONS
            else {
                "point_estimate": None,
                "ci_lower": None,
                "ci_upper": None,
            }
        )
        observed_spend = str(
            sum(
                (row.observed_cost_usd for row in rows if row.observed_cost_usd is not None),
                Decimal(0),
            )
        )
        cost_observation_complete = all(row.observed_cost_usd is not None for row in rows)
        payload = receipt(
            rows,
            corpus_digest=corpus_sha256(corpus),
            policy_digest=policy_sha256(REPO_ROOT / "config" / "fleet.toml"),
            baseline_revision=BASELINE_REVISION,
            candidate_revision=candidate_revision,
            bootstrap=bootstrap,
            cases=cases,
            campaign={
                **_campaign_metadata(envelope=envelope, specs=specs, name=args.campaign, target=args.target),
                "mode": "live" if args.live else "dry-run",
                "arm_orders": sorted({"".join(trial.arm_order) for trial in (row.trial for row in rows)}),
                "admissions": len(rows),
                "observed_spend_usd": observed_spend if cost_observation_complete else None,
                "cost_observation_complete": cost_observation_complete,
                "halted": len(rows) < MAX_ADMISSIONS
                or any(row.observed_cost_usd is None or not row.observation.cleanup_confirmed for row in rows),
                "halt_reason": (
                    "unknown_cost_or_cleanup"
                    if any(row.observed_cost_usd is None or not row.observation.cleanup_confirmed for row in rows)
                    else "admission_limit_or_time"
                    if len(rows) < MAX_ADMISSIONS
                    else None
                ),
            },
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"receipt": str(output), "decision": payload["decision"], "attempted": payload["attempted"]}))
        return 0 if payload["decision"] in {"retain_simplified_profile", "disable", "safety_failure"} else 2
    finally:
        if remove_baseline:
            with suppress(OSError, subprocess.SubprocessError):
                _remove_baseline(baseline_root)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.live and args.dry_run:
            raise Phase4CampaignError("--live and --dry-run are mutually exclusive")
        return run(args)
    except Phase4CampaignError:
        print("Phase 4 campaign precondition failed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

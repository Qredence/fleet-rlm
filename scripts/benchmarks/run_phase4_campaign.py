"""Run the sealed Phase 4 four-arm ablation.

The default command is a credential-free validator.  Provider-backed
execution requires ``--live`` plus ``FLEET_LIVE=1`` and an explicit bounded
campaign.  Exactly 144 trial descriptors are admitted at most once; a worker
failure is recorded as a failed attempt and is never retried or replaced.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
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
from scripts.benchmarks.phase4_api_client import Phase4ApiTrialRunner
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
API_SERVER_PATH = REPO_ROOT / "scripts" / "benchmarks" / "phase4_api_server.py"
API_FAKE_SERVER_PATH = REPO_ROOT / "scripts" / "benchmarks" / "phase4_api_fake_server.py"
BASELINE_REVISION = "9b526f50f0aeec37ca399bc8ef19ec8a95d3bead"
CAMPAIGN_NAME = "phase4-ablation-20260910"
CAMPAIGN_TARGET = "databricks-gcp-standard"
PRIOR_RECEIPT_PATH = REPO_ROOT / ".scratch" / "benchmark-reports" / "phase4-ablation-decf0da7.json"
CAMPAIGN_PROFILE = "phase4-campaign"
MODEL_ID = "databricks-deepseek-v4-flash-0731"
MAX_ELAPSED_SECONDS = 4 * 60 * 60
ADMISSION_SECONDS = 3 * 60 * 60 + 45 * 60
CLEANUP_RESERVE_SECONDS = 15 * 60
MAX_ADMISSIONS = 144
MAX_SANDBOX_CONCURRENCY = 5
TOTAL_SPEND_CAP = 50.0
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_DEFAULT_PROFILE_RE = re.compile(r'(?m)^(default_profile\s*=\s*)(["\'][^"\']+["\'])\s*$')


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
        # Never mutate a caller-owned checkout with the campaign policy. Use
        # the supplied path only as an identity check, then create the same
        # disposable detached worktree used by the default path.
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
    try:
        actual = _git("rev-parse", "HEAD", cwd=path)
        dirty = _git("status", "--porcelain", "--untracked-files=all", cwd=path)
    except (OSError, subprocess.SubprocessError) as exc:
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(path)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        with suppress(OSError):
            parent.rmdir()
        raise Phase4CampaignError("frozen baseline worktree could not be verified") from exc
    if actual != revision or dirty:
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(path)],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        with suppress(OSError):
            parent.rmdir()
        raise Phase4CampaignError("frozen baseline worktree is not the requested clean revision")
    return path, True


def _install_baseline_policy_overlay(path: Path) -> None:
    """Copy the candidate's non-secret policy into an isolated baseline tree."""
    source = REPO_ROOT / "config" / "fleet.toml"
    target = path / "config" / "fleet.toml"
    if not source.is_file() or not target.parent.is_dir():
        raise Phase4CampaignError("baseline policy overlay is unavailable")
    try:
        rendered = source.read_text(encoding="utf-8")
        document = tomllib.loads(rendered)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise Phase4CampaignError("baseline policy overlay could not be prepared") from exc
    profiles = document.get("profiles") if isinstance(document, Mapping) else None
    if not isinstance(profiles, Mapping) or CAMPAIGN_PROFILE not in profiles:
        raise Phase4CampaignError("baseline policy overlay does not declare the campaign profile")
    rendered, replacements = _DEFAULT_PROFILE_RE.subn(
        rf'\1"{CAMPAIGN_PROFILE}"',
        rendered,
        count=1,
    )
    if replacements != 1:
        raise Phase4CampaignError("baseline policy overlay has no selectable default profile")
    try:
        target.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise Phase4CampaignError("baseline policy overlay could not be prepared") from exc


def _remove_baseline(path: Path) -> bool:
    parent = path.parent
    try:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if parent.name.startswith(".fleet-p4-baseline-") and parent.parent == REPO_ROOT.parent:
        try:
            parent.rmdir()
        except OSError:
            return False
    return not path.exists()


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

        require_live_execution(profile="phase4-campaign")
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


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _stop_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Stop one owned process group and report whether it settled."""
    if process.poll() is not None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return process.poll() is not None
    except OSError:
        return False
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            return False
    return process.poll() is not None


def _probe(url: str, *, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, TimeoutError, urllib.error.URLError):
        return False


class Phase4ApiService:
    """Own one loopback FastAPI process and its ephemeral telemetry channel."""

    def __init__(
        self,
        *,
        checkout_root: Path,
        recursive: bool,
        profile: str | None,
        env: Mapping[str, str],
        label: str,
        fake: bool = False,
        corpus: Path | None = None,
    ) -> None:
        self.checkout_root = checkout_root
        self.recursive = recursive
        self.profile = profile
        self.env = dict(env)
        self.label = label
        self.fake = fake
        self.corpus = corpus
        self.port = _available_loopback_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.workdir = Path(tempfile.mkdtemp(prefix=f"fleet-p4-api-{label.lower()}-"))
        self.data_root = self.workdir / "data"
        self.telemetry_path = self.workdir / "telemetry.ndjson"
        self.database_url = f"sqlite+aiosqlite:///{(self.workdir / 'fleet.db').resolve()}"
        self.volume_name = f"fleet-p4-{label.lower()}-{os.getpid()}-{self.port}"
        self.log_path = self.workdir / "server.log"
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, *, timeout_seconds: float = 150.0) -> None:
        if self.fake:
            if self.corpus is None:
                raise Phase4CampaignError(f"{self.label} fake API corpus is unavailable")
            command = [
                sys.executable,
                str(API_FAKE_SERVER_PATH),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--corpus",
                str(self.corpus),
                "--telemetry",
                str(self.telemetry_path),
            ]
            if self.recursive:
                command.append("--recursive")
        else:
            command = [
                sys.executable,
                str(API_SERVER_PATH),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--data-root",
                str(self.data_root),
                "--database-url",
                self.database_url,
                "--volume-name",
                self.volume_name,
                "--telemetry",
                str(self.telemetry_path),
            ]
            if self.profile is not None:
                command.extend(("--profile", self.profile))
            if self.recursive:
                command.append("--recursive")
        log = None
        try:
            log = self.log_path.open("wb")
            self.process = subprocess.Popen(
                command,
                cwd=self.checkout_root,
                env=self.env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=False,
            )
        except OSError as exc:
            if log is not None:
                with suppress(OSError):
                    log.close()
            raise Phase4CampaignError(f"{self.label} API server could not start") from exc
        finally:
            if log is not None:
                with suppress(OSError):
                    log.close()
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                assert self.process is not None
                if self.process.poll() is not None:
                    raise Phase4CampaignError(f"{self.label} API server exited before readiness")
                if _probe(f"{self.base_url}/health") and _probe(f"{self.base_url}/health/ready"):
                    return
                time.sleep(0.1)
            raise Phase4CampaignError(f"{self.label} API server readiness timed out")
        except Phase4CampaignError:
            self.stop()
            raise

    def stop(self) -> bool:
        process = self.process
        self.process = None
        settled = True if process is None else _stop_process_group(process)
        if settled and self.workdir.exists():
            try:
                shutil.rmtree(self.workdir)
            except OSError:
                settled = False
        return settled


class Phase4ArmRunner:
    """Route A/B to direct workers and C/D to public FastAPI services."""

    def __init__(
        self,
        *,
        direct: SubprocessArmRunner | Any,
        api_runners: Mapping[str, Any],
    ) -> None:
        self.direct = direct
        self.api_runners = api_runners

    def __call__(self, trial: Trial, case: Any) -> TrialObservation:
        if trial.arm in {"C", "D"}:
            api_runner = self.api_runners.get(trial.arm)
            if api_runner is None:
                return _unavailable("api_service_unavailable")
            return api_runner(trial, case)
        return self.direct(trial, case)


class SubprocessArmRunner:
    """Run one arm in the selected immutable checkout and parse one observation."""

    def __init__(self, *, candidate_root: Path, baseline_root: Path, worker: Path, timeout_seconds: int = 150) -> None:
        self.candidate_root = candidate_root
        self.baseline_root = baseline_root
        self.worker = worker
        self.timeout_seconds = timeout_seconds
        self.env = _worker_env(candidate=str(candidate_root))

    def __call__(self, trial: Trial, case: Any) -> TrialObservation:
        if trial.arm not in {"A", "B"}:
            return _unavailable("api_adapter_required")
        cwd = self.candidate_root
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


def _prior_receipt_spend(path: Path = PRIOR_RECEIPT_PATH) -> tuple[float | None, str]:
    """Read only the prior receipt's bounded spend ledger.

    An incomplete receipt with an unknown spend is itself an admission
    blocker for a paid campaign: treating the missing value as zero would
    weaken the cumulative cap.  The dry-run path intentionally bypasses this
    check because it never contacts a provider.
    """
    if not path.is_file():
        return 0.0, "absent"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(payload, Mapping):
        return None, "invalid"
    raw = payload.get("observed_spend_usd")
    if not isinstance(raw, str):
        return None, "unknown"
    try:
        amount = Decimal(raw)
    except ArithmeticError:
        return None, "invalid"
    if not amount.is_finite() or amount < 0:
        return None, "invalid"
    numeric = float(amount)
    if not math.isfinite(numeric):
        return None, "invalid"
    return numeric, "observed"


def _mark_service_cleanup_failure(rows: tuple[Any, ...]) -> tuple[Any, ...]:
    """Make a process cleanup failure visible to the mechanical decision."""
    if not rows:
        return rows
    last = rows[-1]
    observation = replace(
        last.observation,
        cleanup_confirmed=False,
        error_category="service_cleanup_failed",
    )
    return (*rows[:-1], replace(last, observation=observation))


def _campaign_metadata(
    *,
    envelope: TrialEnvelope,
    specs: tuple[Any, ...],
    name: str,
    target: str,
    mode: str,
    prior_spend: float | None,
    prior_status: str,
) -> dict[str, object]:
    rates = PublicRateCard()
    return {
        "name": name,
        "target": target,
        "profile": CAMPAIGN_PROFILE,
        "transport": "fastapi_http_sse_for_C_D",
        "mode": mode,
        "prior_receipt": str(PRIOR_RECEIPT_PATH.relative_to(REPO_ROOT)),
        "prior_receipt_status": prior_status,
        "prior_observed_spend_usd": str(Decimal(str(prior_spend))) if prior_spend is not None else None,
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
        "execution_contract": {
            "A_B": "direct DSPy ablations",
            "C_D": "supervised FastAPI HTTP/SSE services",
            "one_session_per_trial": True,
            "serialized_trials": True,
        },
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
    prior_spend, prior_status = _prior_receipt_spend()
    if args.live and prior_spend is None:
        raise Phase4CampaignError("prior Phase 4 receipt has no defensible observed spend")
    if args.live and prior_spend is not None and prior_spend > TOTAL_SPEND_CAP:
        raise Phase4CampaignError("prior Phase 4 receipt already exceeds the cumulative spend cap")
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
    services: dict[str, Phase4ApiService] = {}
    rows: tuple[Any, ...] = ()
    service_cleanup_confirmed = True
    try:
        if args.live:
            _install_baseline_policy_overlay(baseline_root)
            live_env = _worker_env(candidate=str(REPO_ROOT))
            services = {
                "C": Phase4ApiService(
                    checkout_root=baseline_root,
                    recursive=True,
                    profile=CAMPAIGN_PROFILE,
                    env=live_env,
                    label="C",
                ),
                "D": Phase4ApiService(
                    checkout_root=REPO_ROOT,
                    recursive=True,
                    profile=CAMPAIGN_PROFILE,
                    env=live_env,
                    label="D",
                ),
            }
            for service in services.values():
                service.start()
            api_runners = {
                arm: Phase4ApiTrialRunner(
                    base_url=service.base_url,
                    telemetry_path=service.telemetry_path,
                    timeout_seconds=150.0,
                )
                for arm, service in services.items()
            }
            runner = Phase4ArmRunner(
                direct=SubprocessArmRunner(candidate_root=REPO_ROOT, baseline_root=baseline_root, worker=WORKER_PATH),
                api_runners=api_runners,
            )
        else:
            dry_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO_ROOT)}
            services = {
                "C": Phase4ApiService(
                    checkout_root=REPO_ROOT,
                    recursive=True,
                    profile=None,
                    env=dry_env,
                    label="C",
                    fake=True,
                    corpus=corpus,
                ),
                "D": Phase4ApiService(
                    checkout_root=REPO_ROOT,
                    recursive=True,
                    profile=None,
                    env=dry_env,
                    label="D",
                    fake=True,
                    corpus=corpus,
                ),
            }
            for service in services.values():
                service.start()
            api_runners = {
                arm: Phase4ApiTrialRunner(
                    base_url=service.base_url,
                    telemetry_path=service.telemetry_path,
                    timeout_seconds=30.0,
                )
                for arm, service in services.items()
            }
            runner = Phase4ArmRunner(
                direct=_dry_runner,
                api_runners=api_runners,
            )
        rows = execute_campaign(
            cases=cases,
            preflight=policy,
            envelope=envelope,
            runner=runner,
            initial_spent_usd=prior_spend if args.live and prior_spend is not None else 0.0,
        )
    finally:
        for service in services.values():
            service_cleanup_confirmed = service.stop() and service_cleanup_confirmed
        if remove_baseline:
            service_cleanup_confirmed = _remove_baseline(baseline_root) and service_cleanup_confirmed

    if not service_cleanup_confirmed:
        rows = _mark_service_cleanup_failure(rows)

    current_spend = sum(
        (row.observed_cost_usd for row in rows if row.observed_cost_usd is not None),
        Decimal(0),
    )
    cumulative_spend = current_spend + (Decimal(str(prior_spend)) if prior_spend is not None else Decimal(0))
    bootstrap = (
        paired_bootstrap(rows)
        if len(rows) == MAX_ADMISSIONS
        else {
            "point_estimate": None,
            "ci_lower": None,
            "ci_upper": None,
        }
    )
    observed_spend = str(current_spend)
    cost_observation_complete = all(row.observed_cost_usd is not None for row in rows)
    payload = receipt(
        rows,
        corpus_digest=corpus_sha256(corpus),
        policy_digest=policy_sha256(REPO_ROOT / "config" / "fleet.toml", profile=CAMPAIGN_PROFILE),
        baseline_revision=BASELINE_REVISION,
        candidate_revision=candidate_revision,
        bootstrap=bootstrap,
        cases=cases,
        campaign={
            **_campaign_metadata(
                envelope=envelope,
                specs=specs,
                name=args.campaign,
                target=args.target,
                mode="live" if args.live else "dry-run",
                prior_spend=prior_spend,
                prior_status=prior_status,
            ),
            "arm_orders": sorted({"".join(trial.arm_order) for trial in (row.trial for row in rows)}),
            "admissions": len(rows),
            "observed_spend_usd": observed_spend if cost_observation_complete else None,
            "cumulative_observed_spend_usd": str(cumulative_spend) if cost_observation_complete else None,
            "cost_observation_complete": cost_observation_complete,
            "service_cleanup_confirmed": service_cleanup_confirmed,
            "halted": len(rows) < MAX_ADMISSIONS
            or any(row.observed_cost_usd is None or not row.observation.cleanup_confirmed for row in rows)
            or not service_cleanup_confirmed,
            "halt_reason": (
                "unknown_cost_or_cleanup"
                if any(row.observed_cost_usd is None or not row.observation.cleanup_confirmed for row in rows)
                or not service_cleanup_confirmed
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

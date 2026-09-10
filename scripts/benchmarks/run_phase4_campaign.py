"""Run the sealed Phase 4 four-arm ablation.

The default command is a credential-free validator. Provider-backed execution
requires ``--live`` plus ``FLEET_LIVE=1`` for the full 144-trial campaign, or
``--partial-live`` for the fixed ten-trial exploratory sample. The partial path
reuses a running ordinary candidate API and starts only the disposable
frozen-baseline service. Trial failures are recorded once and are never retried
or replaced.
"""

from __future__ import annotations

import argparse
import hashlib
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
from urllib.parse import urlsplit

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
    execute_partial_campaign,
    load_cases,
    observation_from_mapping,
    paired_bootstrap,
    partial_schedule,
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
PARTIAL_MAX_ELAPSED_SECONDS = 45 * 60
PARTIAL_ADMISSION_SECONDS = PARTIAL_MAX_ELAPSED_SECONDS - CLEANUP_RESERVE_SECONDS
PARTIAL_MAX_ADMISSIONS = 10
DEFAULT_CANDIDATE_API_URL = "http://127.0.0.1:8000"
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
    parser.add_argument(
        "--partial-live",
        action="store_true",
        help="run the fixed ten-trial exploratory sample against a live API",
    )
    parser.add_argument(
        "--partial-dry-run",
        action="store_true",
        help="run the fixed ten-trial exploratory sample against local fake APIs",
    )
    parser.add_argument(
        "--candidate-url",
        default=DEFAULT_CANDIDATE_API_URL,
        help="loopback candidate FastAPI URL for --partial-live",
    )
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


def _candidate_dirty_fingerprint() -> tuple[bool, str | None]:
    """Return a content digest for a partial run without exposing file paths."""
    try:
        status = _git("status", "--porcelain", "--untracked-files=all")
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        ).stdout
        untracked = _git("ls-files", "--others", "--exclude-standard")
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase4CampaignError("candidate worktree state is unavailable") from exc
    if not status:
        return False, None
    digest = hashlib.sha256(status.encode("utf-8") + b"\0" + diff + b"\0" + untracked.encode("utf-8")).hexdigest()
    return True, digest


def _validate_candidate_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise Phase4CampaignError("candidate API URL is invalid") from exc
    if (
        parsed.scheme != "http"
        or hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise Phase4CampaignError("candidate API URL must be a loopback HTTP origin")
    return value.rstrip("/")


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
    # Keep the disposable checkout inside the repository's ignored scratch
    # area. Some managed workspaces permit writes only below the checkout;
    # using the parent directory would make an otherwise valid campaign fail
    # before it could admit a trial. The source .git directory is read-only in
    # that environment, so use a no-local clone instead of ``git worktree``.
    scratch = REPO_ROOT / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    parent = Path(tempfile.mkdtemp(prefix=".fleet-p4-baseline-", dir=scratch))
    path = parent / "checkout"
    try:
        subprocess.run(
            ["git", "clone", "--no-local", str(REPO_ROOT), str(path)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "checkout", "--detach", revision],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        with suppress(OSError):
            shutil.rmtree(parent)
        raise Phase4CampaignError("could not create frozen baseline worktree") from exc
    try:
        actual = _git("rev-parse", "HEAD", cwd=path)
        dirty = _git("status", "--porcelain", "--untracked-files=all", cwd=path)
    except (OSError, subprocess.SubprocessError) as exc:
        with suppress(OSError):
            shutil.rmtree(parent)
        raise Phase4CampaignError("frozen baseline worktree could not be verified") from exc
    if actual != revision or dirty:
        with suppress(OSError):
            shutil.rmtree(parent)
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
    if not parent.name.startswith(".fleet-p4-baseline-") or parent.parent != REPO_ROOT / ".scratch":
        return not path.exists()
    try:
        shutil.rmtree(parent)
    except OSError:
        return False
    return not path.exists() and not parent.exists()


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


def _partial_envelope() -> TrialEnvelope:
    """Use a broad accounting envelope without making it an admission gate."""
    return TrialEnvelope(
        input_tokens=2_000_000,
        output_tokens=500_000,
        cache_read_tokens=2_000_000,
        retries=1,
        sandbox_seconds=3_600,
        sandbox_count=MAX_SANDBOX_CONCURRENCY,
        vcpus=4,
        gib_ram=8,
        gib_storage=8,
        maximum_lifetime_seconds=3_600,
    )


def _profile_contract() -> Mapping[str, Any]:
    from fleet_rlm.config.loader import load_profile_environment_contracts

    contracts = {item.name: item for item in load_profile_environment_contracts()}
    contract = contracts.get("phase4-campaign")
    if contract is None or not contract.recursion_enabled:
        raise Phase4CampaignError("phase4-campaign profile is not configured")
    return contract


def _require_live_preflight(*, profile: str | None = CAMPAIGN_PROFILE) -> Any:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        raise Phase4CampaignError("FLEET_LIVE=1 is required for provider-backed Phase 4 execution")
    try:
        from fleet_rlm.config.loader import active_profile_contract, require_live_execution

        settings = require_live_execution(profile=profile) if profile is not None else require_live_execution()
        contract = _profile_contract() if profile is not None else active_profile_contract()
        if (
            contract.runtime_environment != "daytona"
            or settings.root_lm.model != MODEL_ID
            or settings.sub_lm.model != MODEL_ID
            or not settings.rlm_recursion_enabled
        ):
            raise Phase4CampaignError("live model or runtime contract is not approved")
        if profile is not None and (contract.root_max_tokens != 1_024 or contract.sub_max_tokens != 512):
            raise Phase4CampaignError("phase4-campaign token contract is not approved")
    except Exception as exc:
        raise Phase4CampaignError("live policy preflight failed") from exc
    names = (*contract.provider_environment_names, *contract.daytona_snapshot_environment_names)
    missing = sorted({name for name in names if not os.environ.get(name)})
    if missing:
        raise Phase4CampaignError("live provider environment is incomplete")
    return contract


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


def _wait_for_ready(base_url: str, *, timeout_seconds: float = 30.0) -> bool:
    """Wait for both public readiness endpoints within a bounded window."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _probe(f"{base_url}/health") and _probe(f"{base_url}/health/ready"):
            return True
        time.sleep(0.1)
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
            db_init = self.checkout_root / "scripts" / "db_init.py"
            if not db_init.is_file():
                raise Phase4CampaignError(f"{self.label} API database initializer is unavailable")
            try:
                initialized = subprocess.run(
                    [sys.executable, str(db_init), "--database-url", self.database_url],
                    cwd=self.checkout_root,
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=min(60.0, timeout_seconds),
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                raise Phase4CampaignError(f"{self.label} API database initialization failed") from None
            if initialized.returncode != 0:
                raise Phase4CampaignError(f"{self.label} API database initialization failed")
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

    def __init__(
        self,
        *,
        candidate_root: Path,
        baseline_root: Path,
        worker: Path,
        timeout_seconds: int = 150,
        profile: str | None = CAMPAIGN_PROFILE,
    ) -> None:
        self.candidate_root = candidate_root
        self.baseline_root = baseline_root
        self.worker = worker
        self.timeout_seconds = timeout_seconds
        self.profile = profile
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
                "profile": self.profile,
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


def _bounded_prior_spend(payload: Mapping[str, Any]) -> tuple[float | None, str]:
    """Bound a null/unknown prior spend from the receipt rows.

    Known ``observed_cost_usd`` values are summed exactly; each
    unknown-cost row contributes one sealed-envelope
    ``upper_bound_usd`` reservation.  This is a planning-assumption
    bound, not a measured ceiling: the envelope prices admission, it
    does not cap provider-reported actuals, so a pathological unknown
    trial could have cost more.  Exposure is bounded in practice by the
    downstream cumulative cap.  An empty or rowless receipt stays
    blocking — a bound over zero rows would admit at zero.
    """
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return None, "unknown"
    reservation = _envelope().upper_bound_usd(PublicRateCard())
    total = Decimal(0)
    for row in rows:
        if not isinstance(row, Mapping):
            return None, "invalid"
        raw_cost = row.get("observed_cost_usd")
        if raw_cost is None:
            total += reservation
            continue
        if not isinstance(raw_cost, str):
            return None, "invalid"
        try:
            amount = Decimal(raw_cost)
        except ArithmeticError:
            return None, "invalid"
        if not amount.is_finite() or amount < 0:
            return None, "invalid"
        total += amount
    numeric = float(total)
    if not math.isfinite(numeric):
        return None, "invalid"
    return numeric, "bounded_upper"


def _prior_receipt_spend(path: Path = PRIOR_RECEIPT_PATH) -> tuple[float | None, str]:
    """Read only the prior receipt's bounded spend ledger.

    A receipt whose top-level spend is null/unknown falls back to a
    mechanical planning-assumption bound derived from its rows (known
    costs plus one sealed-envelope reservation per unknown-cost row),
    so a prior run that halted on unknown cost does not permanently
    block ``--live`` admission.  Missing, unreadable, invalid, or
    rowless receipts still return ``None`` and remain admission
    blockers, and the bound is still checked against the cumulative
    cap downstream.  The dry-run path intentionally bypasses this
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
        return _bounded_prior_spend(payload)
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


_DEBUG_LOG_TAIL_BYTES = 256 * 1024


def _retain_service_debug(service: Phase4ApiService, label: str) -> None:
    """Copy bounded service diagnostics beside the receipt (operator-local)."""
    try:
        debug_dir = REPO_ROOT / ".scratch" / "benchmark-reports"
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if service.log_path.is_file():
            with service.log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - _DEBUG_LOG_TAIL_BYTES))
                tail = handle.read()
            (debug_dir / f"phase4-debug-{label.lower()}-{stamp}.log").write_bytes(tail)
        if service.telemetry_path.is_file():
            telemetry = service.telemetry_path.read_bytes()[-_DEBUG_LOG_TAIL_BYTES:]
            (debug_dir / f"phase4-debug-{label.lower()}-{stamp}.ndjson").write_bytes(telemetry)
    except OSError:
        pass


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


def _partial_campaign_metadata(
    *,
    specs: tuple[Any, ...],
    name: str,
    target: str,
    candidate_url: str,
    candidate_dirty: bool,
    candidate_dirty_sha256: str | None,
    prior_status: str,
) -> dict[str, object]:
    """Return bounded metadata for the non-certifying ten-trial sample."""
    rates = PublicRateCard()
    return {
        "name": name,
        "target": target,
        "profile": "committed-default",
        "transport": "fastapi_http_sse_for_C_D",
        "mode": "partial_exploratory",
        "candidate_url": candidate_url,
        "candidate_dirty": candidate_dirty,
        "candidate_dirty_sha256": candidate_dirty_sha256,
        "prior_receipt": str(PRIOR_RECEIPT_PATH.relative_to(REPO_ROOT)),
        "prior_receipt_status": "ignored_for_partial",
        "prior_receipt_observed_status": prior_status,
        "max_elapsed_seconds": PARTIAL_MAX_ELAPSED_SECONDS,
        "admission_deadline_seconds": PARTIAL_ADMISSION_SECONDS,
        "cleanup_reserve_seconds": CLEANUP_RESERVE_SECONDS,
        "max_admissions": PARTIAL_MAX_ADMISSIONS,
        "cost_observation_policy": "non_gating_record_if_available",
        "pricing": {
            "cloud_region_assumption": "GCP standard public pricing",
            "model": MODEL_ID,
            "input_usd_per_million": str(rates.input_usd_per_million),
            "output_usd_per_million": str(rates.output_usd_per_million),
            "cache_read_usd_per_million": str(rates.cache_read_usd_per_million),
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
            "from that revision while source material and the ordinary committed profile are used."
        ),
        "d_telemetry": "unavailable_by_design_reused_ordinary_service",
        "d_persistence": "labeled_sessions_and_attachments_retained",
        "execution_contract": {
            "A_B": "direct DSPy ablations",
            "C_D": "supervised FastAPI HTTP/SSE services",
            "one_session_per_trial": True,
            "serialized_trials": True,
            "failure_policy": "ordinary_failures_continue_safety_faults_halt",
        },
    }


def run(args: argparse.Namespace) -> int:
    partial_live = bool(getattr(args, "partial_live", False))
    partial_dry_run = bool(getattr(args, "partial_dry_run", False))
    live = bool(getattr(args, "live", False))
    dry_run = bool(getattr(args, "dry_run", False))
    if sum((live, partial_live, partial_dry_run, dry_run)) > 1:
        raise Phase4CampaignError("campaign execution modes are mutually exclusive")
    output = args.output.expanduser().resolve()
    if not _path_is_below_scratch(output) or output.exists():
        raise Phase4CampaignError("receipt output must be a new JSON path below .scratch")
    corpus = args.corpus.expanduser().resolve()
    if not corpus.is_file():
        raise Phase4CampaignError("sealed Phase 4 corpus is unavailable")
    load_dotenv(REPO_ROOT / ".env", override=False)
    candidate_revision = _candidate_revision(require_clean=live)
    candidate_dirty, candidate_dirty_sha256 = _candidate_dirty_fingerprint() if partial_live else (False, None)
    if live:
        _require_live_preflight(profile=CAMPAIGN_PROFILE)
    elif partial_live:
        _require_live_preflight(profile=None)
    prior_spend, prior_status = _prior_receipt_spend()
    if live and prior_spend is None:
        raise Phase4CampaignError("prior Phase 4 receipt has no defensible observed spend")
    if live and prior_spend is not None and prior_spend > TOTAL_SPEND_CAP:
        raise Phase4CampaignError("prior Phase 4 receipt already exceeds the cumulative spend cap")
    cases = load_cases(corpus)
    specs = arm_specs(baseline_revision=BASELINE_REVISION, candidate_revision=candidate_revision)
    envelope = _partial_envelope() if partial_live else _envelope()
    policy = CampaignPreflight(
        args.campaign,
        args.target,
        PARTIAL_MAX_ELAPSED_SECONDS if partial_live else MAX_ELAPSED_SECONDS,
        PARTIAL_MAX_ADMISSIONS if partial_live else MAX_ADMISSIONS,
        MAX_SANDBOX_CONCURRENCY,
        TOTAL_SPEND_CAP,
    )
    policy.validate()
    candidate_url = _validate_candidate_url(args.candidate_url) if partial_live else None
    baseline_root, remove_baseline = (
        _prepare_baseline(BASELINE_REVISION, args.baseline_worktree)
        if live or partial_live
        else (
            REPO_ROOT,
            False,
        )
    )
    services: dict[str, Phase4ApiService] = {}
    rows: tuple[Any, ...] = ()
    service_cleanup_confirmed = True
    try:
        if live:
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
                direct=SubprocessArmRunner(
                    candidate_root=REPO_ROOT,
                    baseline_root=baseline_root,
                    worker=WORKER_PATH,
                    profile=CAMPAIGN_PROFILE,
                ),
                api_runners=api_runners,
            )
            rows = execute_campaign(
                cases=cases,
                preflight=policy,
                envelope=envelope,
                runner=runner,
                initial_spent_usd=prior_spend if prior_spend is not None else 0.0,
            )
        elif partial_live:
            assert candidate_url is not None
            if not _wait_for_ready(candidate_url):
                raise Phase4CampaignError("candidate API is not ready")
            live_env = _worker_env(candidate=str(REPO_ROOT))
            services = {
                "C": Phase4ApiService(
                    checkout_root=baseline_root,
                    recursive=True,
                    profile=None,
                    env=live_env,
                    label="C",
                ),
            }
            services["C"].start()
            api_runners = {
                "C": Phase4ApiTrialRunner(
                    base_url=services["C"].base_url,
                    telemetry_path=services["C"].telemetry_path,
                    timeout_seconds=150.0,
                ),
                "D": Phase4ApiTrialRunner(
                    base_url=candidate_url,
                    telemetry_path=None,
                    timeout_seconds=150.0,
                ),
            }
            runner = Phase4ArmRunner(
                direct=SubprocessArmRunner(
                    candidate_root=REPO_ROOT,
                    baseline_root=baseline_root,
                    worker=WORKER_PATH,
                    profile=None,
                ),
                api_runners=api_runners,
            )
            rows = execute_partial_campaign(
                cases=cases,
                trials=partial_schedule(cases),
                envelope=envelope,
                runner=runner,
                max_elapsed_seconds=PARTIAL_MAX_ELAPSED_SECONDS,
            )
        elif partial_dry_run:
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
            rows = execute_partial_campaign(
                cases=cases,
                trials=partial_schedule(cases),
                envelope=envelope,
                runner=runner,
                max_elapsed_seconds=PARTIAL_MAX_ELAPSED_SECONDS,
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
            )
    finally:
        for label, service in services.items():
            # Operator-local diagnostics only: retain a bounded server-log tail
            # and the sanitized telemetry beside the receipt so failed C/D
            # trials can be root-caused after the ephemeral workdir is removed.
            # These files may contain prompt text; they stay under .scratch and
            # never enter the content-safe receipt.
            if not service.fake and (live or partial_live):
                _retain_service_debug(service, label)
            service_cleanup_confirmed = service.stop() and service_cleanup_confirmed
        if remove_baseline:
            service_cleanup_confirmed = _remove_baseline(baseline_root) and service_cleanup_confirmed

    if not service_cleanup_confirmed:
        rows = _mark_service_cleanup_failure(rows)

    current_spend = sum(
        (row.observed_cost_usd for row in rows if row.observed_cost_usd is not None),
        Decimal(0),
    )
    cumulative_spend = current_spend + (Decimal(str(prior_spend)) if live and prior_spend is not None else Decimal(0))
    bootstrap = (
        paired_bootstrap(rows)
        if len(rows) == MAX_ADMISSIONS and live
        else {
            "point_estimate": None,
            "ci_lower": None,
            "ci_upper": None,
        }
    )
    observed_spend = str(current_spend)
    cost_observation_complete = all(row.observed_cost_usd is not None for row in rows)
    expected_admissions = PARTIAL_MAX_ADMISSIONS if partial_live else MAX_ADMISSIONS
    policy_profile = "committed-default" if (partial_live or partial_dry_run) else CAMPAIGN_PROFILE
    safety_halted = any(
        row.observation.error_category
        in {
            "authorization",
            "unauthorized",
            "cleanup",
            "cleanup_failed",
            "service_cleanup_failed",
            "deadline",
            "process_group",
            "process_group_failed",
            "service_stopped",
        }
        for row in rows
    )
    if partial_live or partial_dry_run:
        partial_candidate_url = candidate_url
        if partial_candidate_url is None and partial_dry_run:
            dry_candidate_service = services.get("D")
            partial_candidate_url = dry_candidate_service.base_url if dry_candidate_service is not None else None
        campaign_metadata = _partial_campaign_metadata(
            specs=specs,
            name=args.campaign,
            target=args.target,
            candidate_url=partial_candidate_url or DEFAULT_CANDIDATE_API_URL,
            candidate_dirty=candidate_dirty,
            candidate_dirty_sha256=candidate_dirty_sha256,
            prior_status=prior_status,
        )
        halted = len(rows) < expected_admissions or safety_halted or not service_cleanup_confirmed
        halt_reason = (
            "safety_failure"
            if safety_halted or not service_cleanup_confirmed
            else "admission_limit_or_time"
            if len(rows) < expected_admissions
            else None
        )
        campaign_metadata.update(
            {
                "mode": "partial_dry_run" if partial_dry_run else "partial_exploratory",
                "transport": "local_fake_http_sse" if partial_dry_run else "fastapi_http_sse_for_C_D",
                "d_telemetry": "available_local_fake" if partial_dry_run else campaign_metadata["d_telemetry"],
                "selected_trials": [
                    {
                        "case_id": row.trial.case_id,
                        "repeat": row.trial.repeat,
                        "arm": row.trial.arm,
                        "arm_order": list(row.trial.arm_order),
                    }
                    for row in rows
                ],
                "arm_orders": sorted({"".join(row.trial.arm_order) for row in rows}),
                "admissions": len(rows),
                "observed_spend_usd": observed_spend if cost_observation_complete else None,
                "cumulative_observed_spend_usd": observed_spend if cost_observation_complete else None,
                "cost_observation_complete": cost_observation_complete,
                "service_cleanup_confirmed": service_cleanup_confirmed,
                "halted": halted,
                "halt_reason": halt_reason,
            }
        )
    else:
        campaign_metadata = {
            **_campaign_metadata(
                envelope=envelope,
                specs=specs,
                name=args.campaign,
                target=args.target,
                mode="live" if live else "dry-run",
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
        }
    payload = receipt(
        rows,
        corpus_digest=corpus_sha256(corpus),
        policy_digest=policy_sha256(
            REPO_ROOT / "config" / "fleet.toml",
            profile=policy_profile if (partial_live or partial_dry_run) else CAMPAIGN_PROFILE,
        ),
        baseline_revision=BASELINE_REVISION,
        candidate_revision=candidate_revision,
        bootstrap=bootstrap,
        cases=cases,
        campaign=campaign_metadata,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(output), "decision": payload["decision"], "attempted": payload["attempted"]}))
    if partial_live or partial_dry_run:
        return 0
    return 0 if payload["decision"] in {"retain_simplified_profile", "disable", "safety_failure"} else 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except Phase4CampaignError:
        print("Phase 4 campaign precondition failed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

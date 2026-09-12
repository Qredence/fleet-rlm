"""Run the sealed Phase 4 four-arm ablation.

The default command is a credential-free validator. Provider-backed execution
requires ``--live`` plus ``FLEET_LIVE=1`` for the full 144-trial campaign, or
``--partial-live`` for the fixed ten-trial exploratory sample. Every arm
executes through a supervised disposable FastAPI service via the public API;
trial failures are recorded once and are never retried or replaced.
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
    balanced_schedule,
    charged_cost,
    corpus_sha256,
    execute_campaign,
    execute_partial_campaign,
    load_cases,
    paired_bootstrap,
    partial_schedule,
    policy_sha256,
    receipt,
)

CORPUS_PATH = REPO_ROOT / "scripts" / "benchmarks" / "phase4_cases.json"
API_FAKE_SERVER_PATH = REPO_ROOT / "scripts" / "benchmarks" / "phase4_api_fake_server.py"
BASELINE_REVISION = "9b526f50f0aeec37ca399bc8ef19ec8a95d3bead"
CAMPAIGN_NAME = "phase4-ablation-20260910"
CAMPAIGN_TARGET = "databricks-gcp-standard"
PRIOR_RECEIPT_PATH = REPO_ROOT / ".scratch" / "benchmark-reports" / "phase4-ablation-decf0da7.json"
CAMPAIGN_PROFILE = "phase4-campaign"
CAMPAIGN_PROFILES = ("phase4-campaign-a", "phase4-campaign-b", "phase4-campaign")
ARM_PROFILES = {"A": "phase4-campaign-a", "B": "phase4-campaign-b", "C": "phase4-campaign", "D": "phase4-campaign"}
ARM_RECURSIVE = {"A": False, "B": False, "C": True, "D": True}
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
    parser.add_argument("--dry-run", action="store_true", help="run the local deterministic adapter smoke path")
    return parser


def _continuation_spent(
    path: Path,
    *,
    retained_rows: tuple[Any, ...] = (),
) -> tuple[float | None, str]:
    """Return spend predating retained rows from a continuation receipt."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(payload, Mapping):
        return None, "invalid"
    retained_spend = sum((charged_cost(row) for row in retained_rows), Decimal(0))
    campaign = payload.get("campaign")
    if isinstance(campaign, Mapping) and "cumulative_charged_spend_usd" in campaign:
        numeric = _spend_amount(campaign.get("cumulative_charged_spend_usd"))
        status = "cumulative_charged"
        if numeric is None:
            return None, "invalid"
    else:
        numeric = _spend_amount(payload.get("cumulative_charged_spend_usd"))
        status = "cumulative_charged"
        if numeric is None:
            numeric = _spend_amount(payload.get("charged_spend_usd"))
            status = "charged"
    if numeric is not None:
        remaining = Decimal(str(numeric)) - retained_spend
        if remaining < 0 or not remaining.is_finite():
            return None, "invalid"
        return float(remaining), status
    return None, "invalid"


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
    expected = {
        "phase4-campaign": True,
        "phase4-campaign-a": False,
        "phase4-campaign-b": False,
    }
    for name, recursive in expected.items():
        contract = contracts.get(name)
        if contract is None or contract.recursion_enabled is not recursive:
            raise Phase4CampaignError(f"{name} profile is not configured")
        if contract.root_max_tokens != 1_024 or contract.sub_max_tokens != 512:
            raise Phase4CampaignError(f"{name} token contract is not approved")
    return contracts["phase4-campaign"]


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
        if profile is not None:
            expected_budgets = {
                "phase4-campaign-a": (2, 2, False),
                "phase4-campaign-b": (6, 8, False),
            }
            for arm_profile, (iters, calls, recursive) in expected_budgets.items():
                arm_settings = require_live_execution(profile=arm_profile)
                if (
                    arm_settings.root_lm.model != MODEL_ID
                    or arm_settings.sub_lm.model != MODEL_ID
                    or arm_settings.rlm_max_iters != iters
                    or arm_settings.rlm_max_llm_calls != calls
                    or arm_settings.rlm_recursion_enabled is not recursive
                ):
                    raise Phase4CampaignError(f"{arm_profile} budget contract is not approved")
    except Exception as exc:
        raise Phase4CampaignError("live policy preflight failed") from exc
    names = (*contract.provider_environment_names, *contract.daytona_snapshot_environment_names)
    missing = sorted({name for name in names if not os.environ.get(name)})
    if missing:
        raise Phase4CampaignError("live provider environment is incomplete")
    _require_mlflow_server(contract)
    return contract


def _require_mlflow_server(contract: Any, *, timeout_seconds: float = 5.0) -> str:
    """Fail fast unless the profile's MLflow tracking server is reachable.

    Campaign trials require live engineering traces: every completed row must
    link back to its MLflow root trace. Probing here keeps a missing server
    from becoming paid but untraceable evidence. Runtime tracing itself stays
    fail-soft; only campaign admission is strict.
    """
    if not contract.mlflow_tracing_enabled:
        raise Phase4CampaignError("campaign profile must enable MLflow tracing")
    uri = contract.mlflow_tracking_uri
    if not isinstance(uri, str) or not uri.startswith(("http://", "https://")):
        raise Phase4CampaignError("campaign MLflow tracking server is not a reachable HTTP(S) URI")
    # MLflow 3.x removed the legacy GET list endpoint; search is the stable probe.
    probe_url = uri.rstrip("/") + "/api/2.0/mlflow/experiments/search"
    try:
        request = urllib.request.Request(
            probe_url,
            data=json.dumps({"max_results": 1}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if not 200 <= response.status < 300:
                raise Phase4CampaignError("MLflow tracking server is unreachable")
    except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        raise Phase4CampaignError("MLflow tracking server is unreachable") from exc
    return uri


def _campaign_env(*, candidate: str) -> dict[str, str]:
    env = dict(os.environ)
    env["FLEET_LIVE"] = "1"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(item for item in (candidate, existing) if item)
    # The approved turn (90s) plus root-close (120s) envelope is part of the
    # campaign contract; ambient overrides must not invalidate admission.
    env["FLEET_P4_CLOSE_DEADLINE_S"] = "120"
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
                str(self.checkout_root / "scripts" / "benchmarks" / "phase4_api_server.py"),
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
    """Route every arm to its supervised public FastAPI service."""

    def __init__(self, *, api_runners: Mapping[str, Any]) -> None:
        self.api_runners = api_runners

    def __call__(self, trial: Trial, case: Any) -> TrialObservation:
        api_runner = self.api_runners.get(trial.arm)
        if api_runner is None:
            return _unavailable("api_service_unavailable")
        return api_runner(trial, case)


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


def _spend_amount(raw: object) -> float | None:
    """Parse one bounded non-negative spend string, or return None."""
    if not isinstance(raw, str):
        return None
    try:
        amount = Decimal(raw)
    except ArithmeticError:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    numeric = float(amount)
    return numeric if math.isfinite(numeric) else None


def _prior_receipt_spend(path: Path = PRIOR_RECEIPT_PATH) -> tuple[float | None, str]:
    """Read only the prior receipt's bounded spend ledger.

    New receipts carry an always-present ``charged_spend_usd`` bound
    (observed costs plus one sealed-envelope reservation per unknown-cost
    row); it takes precedence because cumulative cap checks must never
    undercount unknown spend.  Older receipts fall back to the legacy
    path: a top-level observed sum when every row was known, else a
    mechanical row-derived bound, so a prior run that halted on unknown
    cost does not permanently block ``--live`` admission.  Missing,
    unreadable, invalid, or rowless receipts still return ``None`` and
    remain admission blockers, and the bound is still checked against the
    cumulative cap downstream.  The dry-run path intentionally bypasses
    this check because it never contacts a provider.
    """
    if not path.is_file():
        return 0.0, "absent"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(payload, Mapping):
        return None, "invalid"
    campaign = payload.get("campaign")
    if isinstance(campaign, Mapping) and "cumulative_charged_spend_usd" in campaign:
        cumulative = _spend_amount(campaign.get("cumulative_charged_spend_usd"))
        return (cumulative, "cumulative_charged") if cumulative is not None else (None, "invalid")
    charged = payload.get("charged_spend_usd")
    if charged is not None:
        numeric = _spend_amount(charged)
        return (numeric, "charged") if numeric is not None else (None, "invalid")
    raw = payload.get("observed_spend_usd")
    if not isinstance(raw, str):
        return _bounded_prior_spend(payload)
    numeric = _spend_amount(raw)
    return (numeric, "observed") if numeric is not None else (None, "invalid")


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


def _api_runners(services: Mapping[str, Phase4ApiService], *, timeout_seconds: float) -> dict[str, Any]:
    """Build one public trial runner per supervised arm service."""
    return {
        arm: Phase4ApiTrialRunner(
            base_url=service.base_url,
            telemetry_path=service.telemetry_path,
            timeout_seconds=timeout_seconds,
        )
        for arm, service in services.items()
    }


def _arm_checkouts(*, candidate: Path, baseline: Path) -> dict[str, Path]:
    """Map each arm to its immutable checkout; only C runs the frozen baseline."""
    return {"A": candidate, "B": candidate, "C": baseline, "D": candidate}


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
        "profiles": list(CAMPAIGN_PROFILES),
        "transport": "fastapi_http_sse",
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
            "all_arms": "supervised FastAPI HTTP/SSE services",
            "one_session_per_trial": True,
            "serialized_trials": True,
        },
    }


def _partial_campaign_metadata(
    *,
    specs: tuple[Any, ...],
    name: str,
    target: str,
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
        "transport": "fastapi_http_sse",
        "mode": "partial_exploratory",
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
        "execution_contract": {
            "all_arms": "supervised FastAPI HTTP/SSE services",
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
    cases = load_cases(corpus)
    load_dotenv(REPO_ROOT / ".env", override=False)
    continue_from = getattr(args, "continue_from", None)
    candidate_revision = _candidate_revision(require_clean=live)
    candidate_dirty, candidate_dirty_sha256 = (
        _candidate_dirty_fingerprint() if partial_live or continue_from is not None else (False, None)
    )
    continue_path: Path | None = None
    retained_rows: tuple[Any, ...] = ()
    dropped_admission_faults = 0
    if continue_from is not None:
        if not live:
            raise Phase4CampaignError("continuation requires --live")
        continue_path = continue_from.expanduser().resolve()
        if not _path_is_below_scratch(continue_path) or not continue_path.is_file():
            raise Phase4CampaignError("continuation receipt must be an existing JSON path below .scratch")
        try:
            retained_rows, faults = load_continuation_rows(
                continue_path,
                expected_corpus_sha256=corpus_sha256(corpus),
                expected_policy_sha256=policy_sha256(
                    REPO_ROOT / "config" / "fleet.toml",
                    profiles=CAMPAIGN_PROFILES,
                ),
                expected_baseline_revision=BASELINE_REVISION,
                expected_candidate_revision=candidate_revision,
                expected_campaign=args.campaign,
                expected_target=args.target,
                expected_schedule=partial_schedule(cases) if partial_live else balanced_schedule(cases),
            )
        except ValueError as exc:
            raise Phase4CampaignError(str(exc)) from exc
        dropped_admission_faults = len(faults)
        prior_spend, prior_status = _continuation_spent(continue_path, retained_rows=retained_rows)
    else:
        prior_spend, prior_status = _prior_receipt_spend()
    if live:
        _require_live_preflight(profile=CAMPAIGN_PROFILE)
    elif partial_live:
        _require_live_preflight(profile=None)
    if live and prior_spend is None:
        raise Phase4CampaignError("prior Phase 4 receipt has no defensible observed spend")
    if live and prior_spend is not None and prior_spend > TOTAL_SPEND_CAP:
        raise Phase4CampaignError("prior Phase 4 receipt already exceeds the cumulative spend cap")
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
    budget_ledger: dict[str, object] = {}
    service_cleanup_confirmed = True
    try:
        if live:
            _install_baseline_policy_overlay(baseline_root)
            live_env = _campaign_env(candidate=str(REPO_ROOT))
            checkouts = _arm_checkouts(candidate=REPO_ROOT, baseline=baseline_root)
            services = {
                arm: Phase4ApiService(
                    checkout_root=checkouts[arm],
                    recursive=ARM_RECURSIVE[arm],
                    profile=ARM_PROFILES[arm],
                    env=live_env,
                    label=arm,
                )
                for arm in ("A", "B", "C", "D")
            }
            for service in services.values():
                service.start()
            runner = Phase4ArmRunner(api_runners=_api_runners(services, timeout_seconds=150.0))
            outcome = execute_campaign(
                cases=cases,
                preflight=policy,
                envelope=envelope,
                runner=runner,
                initial_spent_usd=prior_spend if prior_spend is not None else 0.0,
            )
            rows = outcome.rows
            budget_ledger = outcome.budget
        elif partial_live:
            live_env = _campaign_env(candidate=str(REPO_ROOT))
            checkouts = _arm_checkouts(candidate=REPO_ROOT, baseline=baseline_root)
            services = {
                arm: Phase4ApiService(
                    checkout_root=checkouts[arm],
                    recursive=ARM_RECURSIVE[arm],
                    profile=None,
                    env=live_env,
                    label=arm,
                )
                for arm in ("A", "B", "C", "D")
            }
            for service in services.values():
                service.start()
            runner = Phase4ArmRunner(api_runners=_api_runners(services, timeout_seconds=150.0))
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
                arm: Phase4ApiService(
                    checkout_root=REPO_ROOT,
                    recursive=ARM_RECURSIVE[arm],
                    profile=None,
                    env=dry_env,
                    label=arm,
                    fake=True,
                    corpus=corpus,
                )
                for arm in ("A", "B", "C", "D")
            }
            for service in services.values():
                service.start()
            runner = Phase4ArmRunner(api_runners=_api_runners(services, timeout_seconds=30.0))
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
                arm: Phase4ApiService(
                    checkout_root=REPO_ROOT,
                    recursive=ARM_RECURSIVE[arm],
                    profile=None,
                    env=dry_env,
                    label=arm,
                    fake=True,
                    corpus=corpus,
                )
                for arm in ("A", "B", "C", "D")
            }
            for service in services.values():
                service.start()
            runner = Phase4ArmRunner(api_runners=_api_runners(services, timeout_seconds=30.0))
            outcome = execute_campaign(
                cases=cases,
                preflight=policy,
                envelope=envelope,
                runner=runner,
            )
            rows = outcome.rows
            budget_ledger = outcome.budget
    finally:
        for label, service in services.items():
            # Operator-local diagnostics only: retain a bounded server-log tail
            # and the sanitized telemetry beside the receipt so failed trials
            # can be root-caused after the ephemeral workdir is removed.
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
    current_charged = sum((charged_cost(row) for row in rows), Decimal(0))
    prior_bound = Decimal(str(prior_spend)) if live and prior_spend is not None else Decimal(0)
    cumulative_spend = current_spend + prior_bound
    cumulative_charged = current_charged + prior_bound
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
    charged_spend = str(current_charged)
    cost_observation_complete = all(row.observed_cost_usd is not None for row in rows)
    # The observed-only cumulative is exact only when every current row was
    # observed and the prior ledger was an exact observation (not a bound).
    cumulative_observed_exact = cost_observation_complete and (not live or prior_status == "observed")
    expected_admissions = PARTIAL_MAX_ADMISSIONS if partial_live else MAX_ADMISSIONS
    policy_profiles = ("committed-default",) if (partial_live or partial_dry_run) else CAMPAIGN_PROFILES
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
    ) or any(not row.observation.cleanup_confirmed for row in rows)
    if partial_live or partial_dry_run:
        campaign_metadata = _partial_campaign_metadata(
            specs=specs,
            name=args.campaign,
            target=args.target,
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
                "transport": "local_fake_http_sse" if partial_dry_run else "fastapi_http_sse",
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
                "charged_spend_usd": charged_spend,
                "cost_observation_complete": cost_observation_complete,
                "service_cleanup_confirmed": service_cleanup_confirmed,
                "halted": halted,
                "halt_reason": halt_reason,
            }
        )
    else:
        cleanup_failed = any(not row.observation.cleanup_confirmed for row in rows) or not service_cleanup_confirmed
        short = len(rows) < MAX_ADMISSIONS
        budget_halt_reason = budget_ledger.get("halt_reason")
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
            "budget": dict(budget_ledger),
            "observed_spend_usd": observed_spend if cost_observation_complete else None,
            "cumulative_observed_spend_usd": str(cumulative_spend) if cumulative_observed_exact else None,
            "charged_spend_usd": charged_spend,
            "cumulative_charged_spend_usd": str(cumulative_charged),
            "cost_observation_complete": cost_observation_complete,
            "service_cleanup_confirmed": service_cleanup_confirmed,
            # Unknown spend no longer halts: failed trials charge their
            # reservation and continue. Only unconfirmed cleanup (leaked
            # provider resources billing outside the ledger) halts, plus
            # the budget's own admission stops surfaced with its reason.
            "halted": short or cleanup_failed,
            "halt_reason": (
                "cleanup_failure"
                if cleanup_failed
                else str(budget_halt_reason)
                if short and isinstance(budget_halt_reason, str)
                else "admission_limit_or_time"
                if short
                else None
            ),
        }
    payload = receipt(
        rows,
        corpus_digest=corpus_sha256(corpus),
        policy_digest=policy_sha256(
            REPO_ROOT / "config" / "fleet.toml",
            profiles=policy_profiles,
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
    return 0 if payload["decision"] in {"retain_simplified_profile", "disable"} else 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return run(args)
    except Phase4CampaignError:
        print("Phase 4 campaign precondition failed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

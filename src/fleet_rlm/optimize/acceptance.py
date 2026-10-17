"""Durable acceptance observations, conservative spend admission, and judge controls."""

from __future__ import annotations

import fcntl
import json
import math
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import dspy

from fleet_rlm.optimize.artifacts import atomic_write, fingerprint

# Forecast controls; a ledger may override them but never exceed its own cap.
_LIMIT_DEFAULTS = {"threshold_usd": 48, "cap_usd": 60, "reserve_usd": 12}


class AcceptanceOutcomeUnknown(BaseException):
    """Terminal control signal: optimizer Exception handlers must never retry this work."""


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def save_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode())


@contextmanager
def exclusive_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another process owns this acceptance resource") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class SpendLedger:
    """Forecast admission, not a provider-enforced dollar limit.

    Reservations survive failures and delayed reporting. Only an operator's
    reconciled ledger revision may reduce earlier estimates.
    """

    def __init__(self, path: Path):
        self.path = path
        self.data = read_json(path)
        if self.data.get("schema_version") != 1:
            raise ValueError("Unsupported acceptance ledger")
        for key, default in _LIMIT_DEFAULTS.items():
            self.data.setdefault(key, default)
        for key in ("prior_usd", "rollout_usd", "reflection_usd", "judge_usd", "fixed_remaining_usd", *_LIMIT_DEFAULTS):
            value = self.data.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Ledger requires a finite nonnegative {key}")
        if self.data["threshold_usd"] + self.data["reserve_usd"] > self.data["cap_usd"]:
            raise ValueError("Ledger admission threshold plus reserve must not exceed its cumulative cap")
        if not str(self.data.get("rate_basis", "")).strip():
            raise ValueError("Ledger must record its conservative rate assumptions")
        self.data.setdefault("reservations", {})
        for value in self.data["reservations"].values():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid spend reservation")

    @property
    def total(self) -> float:
        return self.data["prior_usd"] + sum(self.data["reservations"].values())

    def forecast(self, rollouts: int, search_calls: int = 0, judge_calls: int = 0) -> dict[str, Any]:
        estimated = (
            (rollouts + search_calls) * self.data["rollout_usd"]
            + search_calls * self.data["reflection_usd"]
            + judge_calls * self.data["judge_usd"]
            + self.data["fixed_remaining_usd"]
        )
        projected = self.total + estimated
        return {
            "prior_and_reserved_usd": self.total,
            "additional_forecast_usd": estimated,
            "projected_usd": projected,
            "forecast_threshold_usd": self.data["threshold_usd"],
            "cumulative_cap_usd": self.data["cap_usd"],
            "reserve_usd": self.data["reserve_usd"],
            "admitted": projected <= self.data["threshold_usd"],
        }

    def admit(self, key: str, estimate: float) -> None:
        with exclusive_file(self.path):
            self.data = SpendLedger(self.path).data
            self._admit(key, estimate)

    def _admit(self, key: str, estimate: float) -> None:
        if key in self.data["reservations"]:
            return
        threshold = self.data["threshold_usd"]
        if self.total + estimate + self.data["fixed_remaining_usd"] > threshold:
            raise ValueError(
                f"Acceptance budget stop: projected spend exceeds ${threshold:g}; campaign remains incomplete"
            )
        self.data["reservations"][key] = estimate
        save_json(self.path, self.data)


class Campaign:
    def __init__(self, path: Path, identity: dict[str, Any], *, resume: bool = False):
        self.path = path
        self.data: dict[str, Any]
        self.active_key: str | None = None
        self._lock = threading.RLock()
        if resume:
            self.data = read_json(path)
            if self.data.get("identity") != identity:
                raise ValueError("Acceptance evidence changed; refuse stale campaign resume")
        else:
            if path.exists():
                raise ValueError("Campaign already exists; resume it explicitly")
            self.data = {"schema_version": 1, "identity": identity, "status": "prepared", "observations": {}}
            self.save()

    def save(self) -> None:
        with self._lock:
            save_json(self.path, self.data)

    def resource(self, descriptor: dict[str, Any]) -> None:
        """Retain provider recovery identifiers even if execution never returns."""
        rows = self.data.setdefault("resources", [])
        safe = {
            key: descriptor[key]
            for key in ("event", "session_id", "agent_id", "parent_agent_id", "sandbox_id")
            if descriptor.get(key) is not None
        }
        if safe and (not rows or rows[-1] != safe):
            if descriptor.get("event") == "rollout_completed":
                safe.update({key: descriptor[key] for key in ("provider_usage", "cleanup_receipts")})
            rows.append({**safe, "observation": self.active_key})
            self.save()
        if descriptor.get("event") == "execution_outcome_unknown":
            self.data["outcome_unknown"] = True
            self.save()
            raise AcceptanceOutcomeUnknown("Acceptance execution outcome unknown; inspect receipts without replay")

    def observe(self, key: str, program: Any, example: dspy.Example, metric: Any, ledger: SpendLedger) -> Any:
        observations = self.data["observations"]
        receipt: Any = observations.get(key)
        if receipt and receipt["status"] == "started":
            raise ValueError("Execution outcome unknown; resolve saved resource receipts without replay")
        if receipt is None:
            ledger.admit(f"{self.path.parent.name}/{key}", ledger.data["rollout_usd"])
            receipt = dict[str, Any](status="started")
            observations[key] = receipt
            self.active_key = key
            self.save()
            # Persist a recoverable prediction before judging. Exceptions stay unknown:
            # a transport error cannot authorize repeating the paid remote execution.
            prediction = program(**example.inputs())
            receipt.update(status="executed", prediction=prediction.toDict())
            self.active_key = None
            self.save()
        if receipt["status"] == "scored":
            result = receipt["result"]
            return dspy.Prediction(**receipt["prediction"]), dspy.Prediction(**result)
        prediction = dspy.Prediction(**receipt["prediction"])
        attempt = receipt.get("scoring_attempts", 0)
        if attempt:
            ledger.admit(f"{self.path.parent.name}/{key}/judge-{attempt}", ledger.data["judge_usd"])
        receipt["scoring_attempts"] = attempt + 1
        self.save()
        result = metric(example, prediction)
        receipt.update(status="scored", result=result.toDict())
        self.save()
        return prediction, result


def validate_controls(value: dict[str, Any]) -> list[dict[str, Any]]:
    controls = value.get("controls")
    if value.get("review_status") != "approved" or not str(value.get("reviewer", "")).strip():
        raise ValueError("Calibration controls require explicit human approval and reviewer identity")
    if not isinstance(controls, list) or len(controls) != 12:
        raise ValueError("Calibration requires exactly twelve controls")
    ids = set()
    for control in controls:
        if not isinstance(control, dict):
            raise ValueError("Invalid calibration control")
        for key in ("id", "task", "expected_answer", "candidate_answer", "category"):
            if not isinstance(control.get(key), str) or not control[key].strip():
                raise ValueError(f"Control requires {key}")
        if control["id"] in ids or any(type(control.get(key)) is not bool for key in ("is_correct", "is_safe")):
            raise ValueError("Control IDs must be unique and verdicts boolean")
        ids.add(control["id"])
    if sum(item["is_correct"] and item["is_safe"] for item in controls) != 6:
        raise ValueError("Calibration requires six valid and six negative controls")
    if not {"wrong_value", "swapped_associations", "unsupported_claim", "unsafe"} <= {
        item["category"] for item in controls if not (item["is_correct"] and item["is_safe"])
    }:
        raise ValueError("Negative controls must cover all four agreed failure categories")
    return controls


def calibrate(settings: Any, controls_path: Path, ledger_path: Path, output: Path) -> dict[str, Any]:
    with exclusive_file(output):
        return _calibrate(settings, controls_path, ledger_path, output)


def _calibrate(settings: Any, controls_path: Path, ledger_path: Path, output: Path) -> dict[str, Any]:
    from fleet_rlm.optimize.contracts import contract, govern
    from fleet_rlm.optimize.scorers import FleetJudge

    value = read_json(controls_path)
    controls = validate_controls(value)
    definition = contract(settings)
    identity = {"controls_digest": fingerprint(value), "contract_digest": definition["digest"]}
    report: dict[str, Any] = read_json(output) if output.exists() else {**identity, "verdicts": [], "passed": False}
    if any(report.get(key) != expected for key, expected in identity.items()):
        raise ValueError("Calibration evidence is stale")
    ledger = SpendLedger(ledger_path)
    forecast = ledger.forecast(0, judge_calls=12 - len(report["verdicts"]))
    if not forecast["admitted"]:
        raise ValueError(f"Calibration forecast exceeds ${forecast['forecast_threshold_usd']:g}")
    bindings = govern(settings, "verify", "all")["bindings"]
    import mlflow

    from fleet_rlm.observability import configure_mlflow_observability, measured_lm_usage

    target = settings.mlflow_target()
    configure_mlflow_observability(
        target.eval_experiment_name,
        target.tracking_uri,
        trace_catalog=settings.mlflow_trace_catalog,
        trace_schema=settings.mlflow_trace_schema,
        trace_table_prefix=settings.mlflow_eval_trace_table_prefix,
        tracing_sql_warehouse_id=settings.mlflow_tracing_sql_warehouse_id,
    )
    mlflow.set_experiment(experiment_id=bindings["evaluation"]["experiment_id"])
    lm = settings.build_judge_lm().copy(cache=False)
    judge = FleetJudge()
    judge.set_lm(lm)
    completed = {row["id"] for row in report["verdicts"]}
    for item in controls:
        if item["id"] in completed:
            continue
        attempt = report.setdefault("attempts", {}).get(item["id"], 0)
        ledger.admit(f"calibration/{identity['controls_digest']}/{item['id']}/{attempt}", ledger.data["judge_usd"])
        report["attempts"][item["id"]] = attempt + 1
        save_json(output, report)
        usage_start = len(lm.history)
        verdict = judge(task=item["task"], answer=item["candidate_answer"], expected_answer=item["expected_answer"])
        report["verdicts"].append(
            {
                "id": item["id"],
                "is_correct": verdict.is_correct,
                "is_safe": verdict.is_safe,
                "rationale": verdict.feedback,
                "provider_usage": measured_lm_usage(lm, usage_start),
                "passed": verdict.is_correct == item["is_correct"] and verdict.is_safe == item["is_safe"],
            }
        )
        report["passed"] = len(report["verdicts"]) == 12 and all(row["passed"] for row in report["verdicts"])
        save_json(output, report)
    with mlflow.start_run(
        run_id=report.get("mlflow_run_id"), run_name=None if report.get("mlflow_run_id") else "fleet-judge-calibration"
    ) as run:
        report["mlflow_run_id"] = run.info.run_id
        save_json(output, report)
        mlflow.log_dict(report, "calibration/report.json")
        mlflow.log_metrics(
            {
                "calibration_passed": float(report["passed"]),
                "controls_passed": sum(row["passed"] for row in report["verdicts"]),
            }
        )
    mlflow.flush_trace_async_logging()
    return report

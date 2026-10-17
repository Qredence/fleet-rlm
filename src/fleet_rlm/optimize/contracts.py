"""Publish and verify the same scorer contract used by explicit Fleet evaluation.

Registration is deliberate and never starts online monitoring. Code scorers
contain imports in their function bodies and never serialize credentialed LMs.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import mlflow
from mlflow.genai.scorers import scorer

from fleet_rlm.optimize.artifacts import atomic_write, fingerprint
from fleet_rlm.optimize.scorers import SCORER_VERSION, build_mlflow_scorer_suite


@scorer(name="fleet_metric")
def published_fleet_metric(inputs, outputs, expectations):
    from fleet_rlm.config import get_settings
    from fleet_rlm.optimize.scorers import fleet_metric, gold_and_prediction, metric_feedbacks

    settings = get_settings()
    gold, prediction = gold_and_prediction(inputs, outputs, expectations)
    return metric_feedbacks(fleet_metric(gold, prediction, judge_lm=settings.build_judge_lm()))


@scorer(name="retrieval_groundedness")
def published_groundedness(trace):
    from mlflow.entities import SpanType
    from mlflow.genai.scorers import RetrievalGroundedness

    from fleet_rlm.config import get_settings

    if trace is None or not any(span.span_type == SpanType.RETRIEVER for span in trace.search_spans()):
        return None
    return RetrievalGroundedness(model=get_settings().fleet_judge_model)(trace=trace)


def published_suite(settings: Any) -> list[Any]:
    # Databricks registration requires its native provider URI for builtin judges.
    # This maps the *same* configured service, never selects another model/backend.
    model = settings.fleet_judge_model
    native = "databricks:/" + model.removeprefix("openai:/") if model.startswith("openai:/") else model
    suite = build_mlflow_scorer_suite(native)
    return [published_groundedness if item.name == "retrieval_groundedness" else item for item in suite] + [
        published_fleet_metric
    ]


def contract(settings: Any) -> dict[str, Any]:
    from fleet_rlm.engine.signatures import TRIAGE_CRITERIA, AnswerJudge, TurnJudge

    source_dir = Path(__file__).parent
    definitions = {item.name: item.model_dump() for item in published_suite(settings)}
    value = {
        "schema_version": 1,
        "scorer_version": SCORER_VERSION,
        "judge_model": settings.fleet_judge_model,
        "judge_configuration": {
            "temperature": 0,
            "cache": True,
            "num_retries": settings.fleet_reflection_num_retries,
            "correctness_rubric": AnswerJudge.instructions,
            "reference_free_rubric": TurnJudge.instructions,
        },
        "sources": {
            name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
            for name in ("contracts.py", "scorers.py", "evidence.py")
        },
        "routing": TRIAGE_CRITERIA,
        "quality": "minimum of judge correctness/safety and applicable values, route, artifact, delegation checks",
        "composite": "0 when quality<1; otherwise 0.9+0.1*efficiency",
        "applicability": {
            "fleet_metric": "always; structured objectives require reviewed expectations and host execution evidence",
            "route_correctness": "reviewed gold route; missing actual route fails",
            "retrieval_groundedness": "RETRIEVER spans only",
            "rlm_trajectory_efficiency": "trace spans; diagnostic",
            "jev_routing_efficiency": "trace spans; diagnostic",
            "relevance_to_query": "task and answer; diagnostic",
            "sandbox_verification_discipline": "task and answer; diagnostic",
            "agent_skills_progressive_disclosure": "task and answer; diagnostic",
        },
        "definitions": definitions,
    }
    return {**value, "digest": fingerprint(value)}


def registry_path(settings: Any) -> Path:
    return Path(settings.compiled_program_dir) / "scorers.lock.json"


def govern(settings: Any, action: str, scope: str = "all") -> dict[str, Any]:
    """Plan locally, or explicitly publish/read back configured experiments."""
    definition = contract(settings)
    if action == "plan":
        return definition
    from mlflow.exceptions import MlflowException
    from mlflow.genai.scorers import get_scorer

    target = settings.mlflow_target()
    if target.mode != "databricks":
        raise ValueError("Scorer governance requires explicit Databricks mode")
    mlflow.set_tracking_uri(target.tracking_uri)
    path = registry_path(settings)
    saved = json.loads(path.read_bytes()) if path.exists() else {}
    if action == "verify" and saved.get("digest") != definition["digest"]:
        raise ValueError("Scorer contract is unpublished or has changed; register and verify it")
    scopes = ("evaluation", "optimization") if scope == "all" else (scope,)
    bindings = dict(saved.get("bindings", {})) if saved.get("digest") == definition["digest"] else {}
    for role in scopes:
        name = target.eval_experiment_name if role == "evaluation" else target.optimization_experiment_name
        experiment = mlflow.get_experiment_by_name(name)
        if experiment is None:
            raise ValueError(f"Configured {role} experiment does not exist; establish its UC binding first")
        from fleet_rlm.optimize.evaluation import verify_experiment_location

        expected_schema = (
            settings.mlflow_trace_schema if role == "evaluation" else settings.mlflow_optimization_trace_schema
        )
        expected_prefix = (
            settings.mlflow_eval_trace_table_prefix
            if role == "evaluation"
            else settings.mlflow_optimization_trace_table_prefix
        )
        if experiment.trace_location is None and action == "register":
            from mlflow.entities.trace_location import UnityCatalog

            # An unbound experiment may adopt the already configured destination.
            # A different existing binding is permanent and is never reassigned.
            experiment = mlflow.set_experiment(
                name,
                trace_location=UnityCatalog(
                    catalog_name=settings.mlflow_trace_catalog,
                    schema_name=expected_schema,
                    table_prefix=expected_prefix,
                ),
            )
        verify_experiment_location(experiment, settings.mlflow_trace_catalog, expected_schema, expected_prefix)
        previous = bindings.get(role, {})
        receipts = {}
        for item in published_suite(settings):
            local = item.model_dump()
            existing = previous.get("scorers", {}).get(item.name)
            if action == "register" and existing is None:
                try:
                    current = get_scorer(name=item.name, experiment_id=experiment.experiment_id)
                except MlflowException as exc:
                    if exc.error_code != "RESOURCE_DOES_NOT_EXIST":
                        raise
                    current = None
                if current is not None and current.sample_rate != 0:
                    raise ValueError("Refuse to modify an active production scorer")
                if current is not None and fingerprint(current.model_dump()) == fingerprint(local):
                    version = current.scorer_version
                else:
                    registered = item.register(experiment_id=experiment.experiment_id)
                    version = registered.scorer_version
            else:
                if existing is None:
                    raise ValueError(f"Missing registered scorer: {role}/{item.name}")
                version = existing["version"]
            if version is None:
                raise ValueError("Backend did not return a pin-able scorer version")
            remote = get_scorer(name=item.name, experiment_id=experiment.experiment_id, version=version)
            if fingerprint(remote.model_dump()) != fingerprint(local):
                raise ValueError(f"Published scorer definition drift: {role}/{item.name}")
            if remote.sample_rate != 0:
                raise ValueError("Acceptance scorer unexpectedly has online monitoring enabled")
            receipts[item.name] = {"version": version, "definition_digest": fingerprint(local)}
        bindings[role] = {"experiment_id": experiment.experiment_id, "scorers": receipts}
        # Commit each verified experiment so a later failure does not discard all receipts.
        if action == "register":
            atomic_write(path, json.dumps({**definition, "bindings": bindings}, sort_keys=True, indent=2).encode())
    return {**definition, "bindings": bindings}


def pinned_suite(settings: Any, scope: str) -> list[Any]:
    """Load exact published definitions after verifying code, configuration, and destination."""
    from mlflow.genai.scorers import get_scorer

    verified = govern(settings, "verify", scope)
    binding = verified["bindings"][scope]
    return [
        get_scorer(name=name, experiment_id=binding["experiment_id"], version=receipt["version"])
        for name, receipt in binding["scorers"].items()
    ]

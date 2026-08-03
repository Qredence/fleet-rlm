#!/usr/bin/env python3
"""Emit and validate one MLflow GenAI trace using the selected Fleet policy.

Local tracking requires only a reachable server. Managed tracking uses the
Databricks CLI profile/keyring and never prints credential values.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from fleet_rlm.config import Settings, load_runtime_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_repository_env() -> None:
    load_dotenv(_REPO_ROOT / ".env", override=False)


def _resolve_option(value: str | None, environment_name: str, *, required: bool = True) -> str | None:
    resolved = value or os.environ.get(environment_name, "").strip()
    if required and not resolved:
        raise RuntimeError(f"{environment_name} is required; set it in .env or pass the corresponding option")
    return resolved or None


def _tracing_settings() -> Settings:
    """Load one selected Fleet policy with complete local or managed tracing."""
    settings = load_runtime_settings()
    if not settings.mlflow_tracing_enabled or not settings.mlflow_tracking_uri or not settings.mlflow_experiment_name:
        raise RuntimeError("selected Fleet TOML profile must enable MLflow tracing with an experiment")
    if settings.mlflow_tracking_uri == "databricks":
        managed_values = {
            "mlflow.trace_catalog": settings.mlflow_trace_catalog,
            "mlflow.trace_schema": settings.mlflow_trace_schema,
            "mlflow.trace_table_prefix": settings.mlflow_trace_table_prefix,
            "mlflow.tracing_sql_warehouse_id": settings.mlflow_tracing_sql_warehouse_id,
        }
        if any(not value for value in managed_values.values()):
            raise RuntimeError("selected Fleet TOML profile has incomplete Managed Databricks MLflow settings")
    return settings


def _tables(profile: str | None, schema: str) -> set[str]:
    catalog, schema_name = schema.split(".", 1)
    command = ["databricks", "tables", "list", catalog, schema_name]
    if profile:
        command.extend(["--profile", profile])
    command.extend(["-o", "json"])
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return {str(item["full_name"]) for item in json.loads(result.stdout)}


def _trace_summary(trace: Any) -> tuple[str, list[Any]]:
    info = trace.info
    spans = list(trace.data.spans)
    state = str(getattr(info, "state", ""))
    if state != "OK":
        raise RuntimeError(f"trace state is {state!r}, expected 'OK'")
    if getattr(info, "execution_duration", None) is None:
        raise RuntimeError("trace is missing execution duration")
    if not spans:
        raise RuntimeError("trace contains no spans")
    root_spans = [span for span in spans if getattr(span, "parent_span_id", None) is None]
    if not root_spans:
        raise RuntimeError("trace contains no root span")
    if not getattr(info, "request_preview", None):
        raise RuntimeError("trace is missing request preview")
    if not getattr(info, "response_preview", None):
        raise RuntimeError("trace is missing response preview")
    error_spans = [
        str(getattr(span, "name", "unknown"))
        for span in spans
        if _span_status_code(span) in {"ERROR", "STATUS_CODE_ERROR"}
    ]
    if error_spans:
        raise RuntimeError(f"trace contains error span(s): {', '.join(error_spans)}")
    return str(getattr(info, "trace_id", "")), spans


def _span_status_code(span: Any) -> str:
    """Normalize MLflow CLI and SDK span status representations."""
    status = getattr(span, "status", None)
    raw = getattr(status, "code", None)
    if raw is None:
        raw = getattr(status, "status_code", None)
    return str(getattr(raw, "value", raw))


def main() -> int:
    _load_repository_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    args = parser.parse_args()

    settings = _tracing_settings()
    managed = settings.mlflow_tracking_uri == "databricks"
    profile = _resolve_option(args.profile, "DATABRICKS_CONFIG_PROFILE", required=False)

    assert settings.mlflow_experiment_name is not None
    if managed:
        host = _resolve_option(None, "DATABRICKS_HOST")
        assert host is not None
        assert settings.mlflow_tracing_sql_warehouse_id is not None
        os.environ["DATABRICKS_HOST"] = host
        if os.environ.get("DATABRICKS_TOKEN", "").strip():
            profile = None
        elif profile:
            os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
        os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.mlflow_tracing_sql_warehouse_id

    import mlflow

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    if managed:
        from mlflow.entities.trace_location import UnityCatalog

        assert settings.mlflow_trace_catalog is not None
        assert settings.mlflow_trace_schema is not None
        assert settings.mlflow_trace_table_prefix is not None
        experiment = mlflow.set_experiment(
            experiment_name=settings.mlflow_experiment_name,
            trace_location=UnityCatalog(
                catalog_name=settings.mlflow_trace_catalog,
                schema_name=settings.mlflow_trace_schema,
                table_prefix=settings.mlflow_trace_table_prefix,
            ),
        )
    else:
        experiment = mlflow.set_experiment(experiment_name=settings.mlflow_experiment_name)

    @mlflow.trace(name="fleet_mlflow_smoke")
    def smoke(input_text: str) -> dict[str, str]:
        return {"echo": input_text}

    smoke("fleet MLflow tracing smoke test")
    trace_id = mlflow.get_last_active_trace_id()
    if not trace_id:
        raise RuntimeError("MLflow did not return a trace id")
    trace = mlflow.get_trace(trace_id, flush=True)
    if trace is None:
        raise RuntimeError(f"MLflow trace was not available after flushing: {trace_id}")
    verified_trace_id, spans = _trace_summary(trace)

    trace_location = settings.mlflow_tracking_uri
    if managed:
        expected_tables = {
            f"{settings.mlflow_trace_catalog}.{settings.mlflow_trace_schema}.{settings.mlflow_trace_table_prefix}_{suffix}"
            for suffix in ("otel_spans", "otel_annotations", "otel_logs", "otel_metrics")
        }
        actual_tables = _tables(profile, f"{settings.mlflow_trace_catalog}.{settings.mlflow_trace_schema}")
        missing_tables = expected_tables.difference(actual_tables)
        if missing_tables:
            raise RuntimeError(f"missing Unity Catalog trace table(s): {', '.join(sorted(missing_tables))}")
        trace_location = (
            f"{settings.mlflow_trace_catalog}.{settings.mlflow_trace_schema}.{settings.mlflow_trace_table_prefix}"
        )

    print(f"experiment_id={experiment.experiment_id}")
    print(f"trace_id={verified_trace_id}")
    print(f"span_count={len(spans)}")
    print(f"execution_duration_ms={trace.info.execution_duration}")
    print(f"tracking_uri={settings.mlflow_tracking_uri}")
    print(f"trace_location={trace_location}")
    print("status=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

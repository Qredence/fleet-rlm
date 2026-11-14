"""Read-only environment diagnostics (`fleet doctor`) and Daytona snapshot commands (`fleet snapshot`)."""

from __future__ import annotations

import argparse
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from fleet_rlm.config import FleetSettings
from fleet_rlm.models import build_lm, complete_text
from fleet_rlm.sandbox.snapshots import activate_snapshot, build_snapshot, preflight
from fleet_rlm.store import SCHEMA_VERSION


def check_models(settings: FleetSettings, *, probe_models: bool = False) -> dict[str, Any]:
    """Validate model configuration, optionally discover routes, and run an explicit probe if requested."""
    settings.resolved_model("root")
    try:
        models = settings.list_models(role="shared")
        discovery = "" if models else " Enter a model route manually if no models are listed."
    except Exception:
        models = []
        discovery = " Discovery is unavailable; manual model entry remains available."

    if not probe_models:
        return {
            "status": "ready",
            "message": "Models are configured. Inference is untested (pass --probe-models to test)." + discovery,
            "models": models,
            "inference_tested": False,
        }

    import dspy

    lm = build_lm(settings, settings.fleet_root_model, "root").copy(
        num_retries=0, max_tokens=16, cache=False, callbacks=[]
    )
    try:
        with dspy.context(callbacks=[]):
            prompt = (
                'Return exactly this JSON object: {"status":"OK"}'
                if settings.resolved_model("root").response_format == "json_object"
                else "Reply with exactly OK"
            )
            result = complete_text(lm, prompt, max_tokens=16)
        if not result:
            return {
                "status": "blocked",
                "message": "Root model probe returned an empty response.",
                "models": models,
                "inference_tested": False,
            }
    except Exception as exc:
        return {
            "status": "blocked",
            "message": f"Root model probe failed ({type(exc).__name__}).",
            "models": models,
            "inference_tested": False,
        }
    finally:
        lm.close()

    return {
        "status": "ready",
        "message": "Models are configured and root model inference probe succeeded." + discovery,
        "models": models,
        "inference_tested": True,
    }


def check_database(settings: FleetSettings) -> dict[str, Any]:
    """Check runtime and migration database connections in read-only transactions."""
    import psycopg
    from psycopg import sql

    from fleet_rlm.store import SCHEMA_VERSION, lakebase_connection_class

    connection_class: type[psycopg.Connection[Any]] = psycopg.Connection
    if settings.fleet_lakebase_endpoint:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config

        if settings.mlflow_mode == "databricks" and settings.mlflow_tracking_uri == "databricks":
            auth = Config(
                host=settings.databricks_host,
                token=settings.databricks_token.get_secret_value(),
                auth_type="pat",
                http_timeout_seconds=20,
                retry_timeout_seconds=20,
            )
        else:
            auth = Config(http_timeout_seconds=20, retry_timeout_seconds=20)
        connection_class = lakebase_connection_class(WorkspaceClient(config=auth), settings.fleet_lakebase_endpoint)

    initialized = False
    versions: list[int] = []
    try:
        for dsn in dict.fromkeys(filter(None, [settings.fleet_database_url, settings.fleet_database_migration_url])):
            with connection_class.connect(
                dsn, connect_timeout=5, autocommit=False, options="-c statement_timeout=5000"
            ) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SELECT 1").fetchone()
                exists = connection.execute(
                    "SELECT to_regclass(%s)", (f"{settings.fleet_database_schema}.fleet_lite_sessions",)
                ).fetchone()
                initialized = initialized or bool(exists and exists[0])
                migrations = connection.execute(
                    "SELECT to_regclass(%s)", (f"{settings.fleet_database_schema}.fleet_lite_schema_migrations",)
                ).fetchone()
                if migrations and migrations[0]:
                    row = connection.execute(
                        sql.SQL("SELECT MAX(version) FROM {}.fleet_lite_schema_migrations").format(
                            sql.Identifier(settings.fleet_database_schema)
                        )
                    ).fetchone()
                    versions.append(int(row[0] or 0) if row else 0)
                connection.rollback()
    except Exception as exc:
        return {
            "status": "blocked",
            "message": f"Database connection failed ({type(exc).__name__}).",
        }

    if any(version > SCHEMA_VERSION for version in versions):
        return {
            "status": "blocked",
            "message": "The database requires a newer Fleet version; upgrade before starting.",
        }
    return {
        "status": "ready",
        "message": "Database connection verified. Fleet applies additive schema migrations at startup.",
        "schema_initialized": initialized,
        "schema_version": max(versions, default=0),
    }


def check_mlflow(settings: FleetSettings) -> dict[str, Any]:
    """Check the selected MLflow tracking backend without creating experiments."""
    from fleet_rlm import launcher

    target = settings.mlflow_target()
    if target.mode == "local":
        reachable = launcher.mlflow_is_healthy(target.tracking_uri)
        issue = launcher.local_mlflow_kek_issue(
            settings,
            mlflow_url=target.tracking_uri,
            reachable=reachable,
        )
        if issue:
            return {
                "status": "blocked",
                "message": issue,
            }
        detail = "reachable" if reachable else "not currently running (managed automatically by fleet cli/web)"
        return {
            "status": "ready",
            "message": f"Local MLflow configured at {target.tracking_uri} ({detail}).",
        }

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config
    from databricks.sdk.errors import NotFound

    if target.tracking_uri.startswith("databricks://"):
        config = Config(
            profile=target.tracking_uri.removeprefix("databricks://"),
            http_timeout_seconds=20,
            retry_timeout_seconds=20,
        )
    else:
        config = Config(
            host=settings.databricks_host,
            token=settings.databricks_token.get_secret_value(),
            auth_type="pat",
            http_timeout_seconds=20,
            retry_timeout_seconds=20,
        )
    try:
        experiment = WorkspaceClient(config=config).experiments.get_by_name(target.experiment_name).experiment
    except NotFound:
        experiment = None
    except Exception as exc:
        return {
            "status": "blocked",
            "message": f"Databricks MLflow check failed ({type(exc).__name__}).",
        }
    return {
        "status": "ready",
        "message": "Databricks MLflow is accessible.",
        "experiment_exists": experiment is not None,
    }


def _daytona_client(settings: FleetSettings) -> Any:
    from daytona import Daytona, DaytonaConfig

    if not settings.fleet_daytona_api_key or not settings.fleet_daytona_org_id:
        raise ValueError("Configure FLEET_DAYTONA_API_KEY and FLEET_DAYTONA_ORG_ID")
    return Daytona(DaytonaConfig(api_key=settings.fleet_daytona_api_key, organization_id=settings.fleet_daytona_org_id))


def check_daytona(settings: FleetSettings) -> dict[str, Any]:
    """Verify Daytona snapshot floors and workspace Volume readiness."""
    if not settings.fleet_daytona_api_key or not settings.fleet_daytona_org_id:
        return {"status": "blocked", "message": "Configure FLEET_DAYTONA_API_KEY and FLEET_DAYTONA_ORG_ID."}
    try:
        client = _daytona_client(settings)
        identity = preflight(
            client.snapshot,
            api_key=settings.fleet_daytona_api_key,
            org_id=settings.fleet_daytona_org_id,
            refresh=True,
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "message": str(exc) or f"Daytona snapshot check failed ({type(exc).__name__}).",
        }
    if not settings.fleet_daytona_volume_id:
        return {"status": "blocked", "message": "Configure FLEET_DAYTONA_VOLUME_ID.", "state": "volume_missing"}
    try:
        volumes = client.volume.list()
    except Exception as exc:
        return {"status": "blocked", "message": f"Failed to list Daytona volumes ({type(exc).__name__})."}
    volume = next((v for v in volumes if v.id == settings.fleet_daytona_volume_id), None)
    if volume is None:
        return {
            "status": "blocked",
            "message": "Configured Volume was not found in this organization.",
            "state": "volume_missing",
        }
    state = str(getattr(volume.state, "value", volume.state)).lower()
    if state != "ready":
        return {
            "status": "blocked",
            "message": f"Volume is not ready ({state}); recheck provider status.",
            "state": "volume_pending",
        }
    return {
        "status": "ready",
        "message": "Daytona snapshot and workspace Volume are ready.",
        "snapshot": identity.as_dict(),
    }


def _secret_values(settings: FleetSettings) -> list[str]:
    return list(
        filter(
            None,
            [
                settings.fleet_daytona_api_key,
                settings.databricks_token.get_secret_value(),
                settings.fleet_database_url,
                settings.fleet_database_migration_url,
                settings.typesafe_api_key,
                settings.fleet_llm_api_key.get_secret_value(),
            ],
        )
    )


def run_doctor(
    args: argparse.Namespace,
    settings: FleetSettings | None,
    *,
    config_error: str | None = None,
    console: Console | None = None,
) -> int:
    """Print the read-only environment readiness checklist and return exit code 0 when ready."""
    out = console or Console()
    table = Table(title="Fleet RLM Lite Diagnostics")
    table.add_column("Section")
    table.add_column("Status")
    table.add_column("Detail")

    if config_error or settings is None:
        table.add_row("Configuration", "[red]blocked[/red]", escape(config_error or "Invalid configuration"))
        out.print(table)
        return 1

    table.add_row("Configuration", "[green]ready[/green]", f"schema v{SCHEMA_VERSION} ({settings.mlflow_mode} MLflow)")
    probe = bool(getattr(args, "probe_models", False))
    checks = [
        ("Models", check_models, {"probe_models": probe}),
        ("Database", check_database, {}),
        ("MLflow", check_mlflow, {}),
        ("Daytona", check_daytona, {}),
    ]
    blocked = False
    for name, fn, kwargs in checks:
        try:
            result = fn(settings, **kwargs)
        except Exception as exc:
            result = {"status": "blocked", "message": f"{name} check failed ({type(exc).__name__})."}
        status = result.get("status", "blocked")
        if status == "blocked":
            blocked = True
            badge = "[red]blocked[/red]"
        else:
            badge = "[green]ready[/green]"
        table.add_row(name, badge, escape(str(result.get("message", ""))))

    out.print(table)
    return 1 if blocked else 0


def run_snapshot(
    args: argparse.Namespace,
    settings: FleetSettings,
    *,
    console: Console | None = None,
) -> int:
    """Check, build, or activate the configured Daytona snapshot."""
    out = console or Console()
    action = getattr(args, "snapshot_command", "check")
    if not settings.fleet_daytona_api_key or not settings.fleet_daytona_org_id:
        out.print("[red]Configure FLEET_DAYTONA_API_KEY and FLEET_DAYTONA_ORG_ID first.[/red]")
        return 1
    try:
        if action == "check":
            client = _daytona_client(settings)
            identity = preflight(
                client.snapshot,
                api_key=settings.fleet_daytona_api_key,
                org_id=settings.fleet_daytona_org_id,
                refresh=True,
            )
            out.print(f"Snapshot ready: {identity.name} ({identity.id})", markup=False)
            return 0
        if action == "build":
            secrets = _secret_values(settings)

            pending = ""

            def on_logs(chunk: str, *, final: bool = False) -> None:
                nonlocal pending
                pending += chunk
                text = ""
                while pending:
                    matched = next((secret for secret in secrets if pending.startswith(secret)), None)
                    if matched:
                        text += "[redacted]"
                        pending = pending[len(matched) :]
                    elif not final and any(secret.startswith(pending) for secret in secrets):
                        break
                    else:
                        text += pending[0]
                        pending = pending[1:]
                out.print(text, end="", markup=False)

            try:
                identity = build_snapshot(
                    api_key=settings.fleet_daytona_api_key,
                    org_id=settings.fleet_daytona_org_id,
                    on_logs=on_logs,
                )
            finally:
                # A trailing partial secret is withheld rather than printed on failure.
                if not any(secret.startswith(pending) for secret in secrets):
                    on_logs("", final=True)
            out.print(f"\nSnapshot built: {identity.name} ({identity.id})", markup=False)
            return 0
        if action == "activate":
            identity = activate_snapshot(
                api_key=settings.fleet_daytona_api_key,
                org_id=settings.fleet_daytona_org_id,
            )
            out.print(f"Snapshot activated: {identity.name} ({identity.id})", markup=False)
            return 0
    except Exception as exc:
        out.print(f"Snapshot {action} failed: {exc}", markup=False)
        return 1
    return 1

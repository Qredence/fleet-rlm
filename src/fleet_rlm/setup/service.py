"""Setup drafts, readiness probes, and serialized provider operations.

No database, model, or Daytona connection is opened when constructing this service.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key
from pydantic import ValidationError

from fleet_rlm.config import FleetSettings, _toml_settings
from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME, SnapshotUnavailableError
from fleet_rlm.sandbox.snapshots.operations import activate_snapshot, build_snapshot

# A deliberately bounded editor. Values retain FleetSettings' existing validation and precedence.
FIELDS: dict[str, tuple[str, str, bool]] = {
    "DATABRICKS_HOST": ("databricks_host", "models", False),
    "DATABRICKS_TOKEN": ("databricks_token", "models", True),
    "FLEET_LLM_BASE_URL": ("fleet_llm_base_url", "models", False),
    "FLEET_ROOT_MODEL": ("fleet_root_model", "models", False),
    "FLEET_SUB_MODEL": ("fleet_sub_model", "models", False),
    "FLEET_DATABASE_URL": ("fleet_database_url", "database", True),
    "FLEET_DATABASE_MIGRATION_URL": ("fleet_database_migration_url", "database", True),
    "FLEET_LAKEBASE_ENDPOINT": ("fleet_lakebase_endpoint", "database", False),
    "FLEET_DATABASE_SCHEMA": ("fleet_database_schema", "database", False),
    "FLEET_DAYTONA_API_KEY": ("fleet_daytona_api_key", "daytona", True),
    "FLEET_DAYTONA_ORG_ID": ("fleet_daytona_org_id", "daytona", False),
    "FLEET_DAYTONA_VOLUME_ID": ("fleet_daytona_volume_id", "daytona", False),
}
for _key in ("port", "data_dir", "experiment_name"):
    FIELDS[f"FLEET_MLFLOW_LOCAL_{_key.upper()}"] = (f"mlflow_local_{_key}", "mlflow", False)
for _key in (
    "tracking_uri",
    "experiment_name",
    "trace_catalog",
    "trace_schema",
    "trace_table_prefix",
    "tracing_sql_warehouse_id",
):
    FIELDS[f"FLEET_MLFLOW_DATABRICKS_{_key.upper()}"] = (f"mlflow_databricks_{_key}", "mlflow", False)

SECTIONS: tuple[Literal["models", "database", "mlflow", "daytona"], ...] = (
    "models",
    "database",
    "mlflow",
    "daytona",
)


class SetupConflictError(ValueError):
    """A setup action cannot run in the current state; safe to display."""


@dataclass
class Operation:
    """Bounded, replayable progress for one operation; disconnect never replays work."""

    id: str
    action: str
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=200))
    finished: bool = False
    sequence: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, message: str, *, event: str = "progress", **values: Any) -> None:
        """Append bounded progress or a terminal outcome with a monotonic sequence."""
        with self.lock:
            self.sequence += 1
            self.events.append({"seq": self.sequence, "event": event, "message": message[:2000], **values})
            if event in {"complete", "error"}:
                self.finished = True

    def read(self, after: int) -> tuple[list[dict[str, Any]], bool]:
        """Read retained events after a cursor and report terminal completion."""
        with self.lock:
            return [dict(event) for event in self.events if event["seq"] > after], self.finished


class SetupService:
    """One CLI-owned server's operator state. Filesystem authority is fixed at launch."""

    def __init__(self, project_dir: Path, backend: Literal["local", "databricks"] = "local") -> None:
        self.project_dir = project_dir.resolve()
        self.env_path = self.project_dir / ".env"
        self.backend = backend
        self.draft: dict[str, str] = {}
        self.checks: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, Operation] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fleet-setup")
        self._busy = False
        self._closed = False
        self._applying = False
        self._verified_fingerprint: str | None = None
        self._inference_fingerprint: str | None = None
        self._baseline: dict[str, str | None] = {}
        try:
            derived = json.loads(os.environ.get("FLEET_SETUP_DERIVED_ENV", "[]"))
        except ValueError:
            derived = []
        self._derived_env = (
            {name for name in derived if isinstance(name, str) and name in FIELDS}
            if isinstance(derived, list)
            else set()
        )

    def close(self) -> None:
        """Stop accepting new operations without replaying provider work."""
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def settings(self) -> FleetSettings:
        """Validate the candidate using Fleet configuration precedence and validators."""
        # Launcher-derived profile variables freeze the running server. They must not
        # masquerade as operator environment overrides in a new setup draft.
        raw = {key.upper(): value for key, value in dotenv_values(self.env_path, interpolate=False).items()}
        toml = _toml_settings(FleetSettings) if self._derived_env else {}
        values = {
            FIELDS[name][0]: raw.get(
                name, toml.get(FIELDS[name][0], FleetSettings.model_fields[FIELDS[name][0]].get_default())
            )
            for name in self._derived_env
        }
        values.update({FIELDS[name][0]: value for name, value in self.draft.items()})
        return FleetSettings(
            _env_file=self.env_path,
            mlflow_mode=self.backend,
            **values,
        )

    @staticmethod
    def fingerprint(settings: FleetSettings) -> str:
        """Identify the private effective configuration used by the last verification."""
        values = settings.model_dump(mode="json")
        # SecretStr serializes as masked text; include its actual value only in this private digest.
        values["databricks_token"] = settings.databricks_token.get_secret_value()
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()

    def status(self) -> dict[str, Any]:
        """Return redacted fields, source labels, and the last readiness results."""
        with self._lock:
            raw = {key.upper(): value for key, value in dotenv_values(self.env_path, interpolate=False).items()}
            environment = {
                key.upper(): value for key, value in os.environ.items() if key.upper() not in self._derived_env
            }
            try:
                toml = _toml_settings(FleetSettings)
                config_error = None
            except ValueError as exc:
                toml, config_error = {}, str(exc)
            fields = []
            for name, (attr, section, secret) in FIELDS.items():
                if section == "mlflow" and f"MLFLOW_{self.backend.upper()}_" not in name:
                    continue
                default = FleetSettings.model_fields[attr].get_default()
                if hasattr(default, "get_secret_value"):
                    default = default.get_secret_value()
                value, source = toml.get(attr, default), "toml" if attr in toml else "default"
                if raw.get(name) is not None:
                    value, source = raw[name], "dotenv"
                if name in environment:
                    value, source = environment[name], "environment"
                if name in self.draft:
                    value, source = self.draft[name], "draft"
                if name in {"DATABRICKS_HOST", "FLEET_LLM_BASE_URL", "FLEET_MLFLOW_DATABRICKS_TRACKING_URI"} and value:
                    try:
                        parsed = urlsplit(str(value))
                        if parsed.username or parsed.password or parsed.query or parsed.fragment:
                            value = ""
                    except ValueError:
                        value = ""
                fields.append(
                    {
                        "name": name,
                        "section": section,
                        "secret": secret,
                        "configured": bool(value),
                        "value": None if secret else str(value or ""),
                        "source": source,
                        "editable": name not in environment,
                    }
                )
            try:
                settings = self.settings()
                fingerprint = self.fingerprint(settings)
                errors = []
            except ValidationError as exc:
                fingerprint = None
                errors = ["Invalid setting: " + ".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)]
            except ValueError as exc:
                fingerprint, errors = None, [str(exc)]
            if config_error and config_error not in errors:
                errors.append(config_error)
            ready = bool(
                fingerprint
                and fingerprint == self._verified_fingerprint
                and not self.draft
                and all(self.checks.get(section, {}).get("status") in {"ready", "warning"} for section in SECTIONS)
            )
            return {
                "backend": self.backend,
                "snapshot_name": SNAPSHOT_NAME,
                "env_path": str(self.env_path),
                "fields": fields,
                "checks": self.checks.copy(),
                "errors": errors,
                "dirty": bool(self.draft),
                "busy": self._busy,
                "ready": ready,
                "restart_required": self._applying,
            }

    def stage(self, values: dict[str, str]) -> dict[str, Any]:
        """Stage supported edits while preserving process environment authority."""
        with self._lock:
            if self._busy or self._applying:
                raise SetupConflictError("Wait for the current setup operation to finish.")
            if values.keys() - FIELDS.keys():
                raise ValueError("Unsupported setup setting")
            if any(
                name.lower() in {key.lower() for key in os.environ if key.upper() not in self._derived_env}
                for name in values
            ):
                raise SetupConflictError(
                    "This value is controlled by the process environment; change it before restarting Fleet."
                )
            if any("\x00" in value for value in values.values()):
                raise ValueError("Settings cannot contain NUL characters")
            raw = {key.upper(): value for key, value in dotenv_values(self.env_path, interpolate=False).items()}
            for name, value in values.items():
                self._baseline.setdefault(name, raw.get(name))
                self.draft[name] = value.strip()
            self._verified_fingerprint = None
            self.checks.clear()
            return self.status()

    def save(self) -> dict[str, Any]:
        """Validate and atomically merge explicit edits into the project dotenv file."""
        with self._lock:
            if self._busy or self._applying:
                raise SetupConflictError("Wait for the current setup operation to finish.")
            if self.env_path.is_symlink():
                raise SetupConflictError("Edit the linked .env file externally, then recheck setup.")
            current = {key.upper(): value for key, value in dotenv_values(self.env_path, interpolate=False).items()}
            if any(current.get(name) != self._baseline[name] for name in self.draft):
                raise SetupConflictError("The .env file changed externally; reopen setup before saving.")
            if not self.draft:
                return self.status()
            self.settings()  # Run Fleet's field and cross-field validators before saving.
            descriptor, temp_name = tempfile.mkstemp(prefix=".fleet-env-", dir=self.project_dir)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(self.env_path.read_text() if self.env_path.exists() else "")
                for name, value in self.draft.items():
                    set_key(temp_name, name, value, quote_mode="always")
                os.chmod(temp_name, 0o600)
                os.replace(temp_name, self.env_path)
            finally:
                Path(temp_name).unlink(missing_ok=True)
            self.draft.clear()
            self._baseline.clear()
            self._verified_fingerprint = None
            self.checks.clear()
            return self.status()

    def discard(self) -> dict[str, Any]:
        """Discard unsaved values and reload externally edited configuration."""
        with self._lock:
            if self._busy or self._applying:
                raise SetupConflictError("Wait for the current setup operation to finish.")
            self.draft.clear()
            self._baseline.clear()
            self.checks.clear()
            self._verified_fingerprint = None
            return self.status()

    def start(self, action: str, request_id: str, run: Callable[[Operation], Any]) -> Operation:
        """Serialize one operator action and retain its bounded progress for reconnection."""
        with self._lock:
            if request_id in self.operations:
                operation = self.operations[request_id]
                if operation.action != action:
                    raise SetupConflictError("This request ID belongs to a different action.")
                return operation
            if self._busy or self._closed or self._applying:
                raise SetupConflictError("Another setup operation is running; wait or reconnect to its progress.")
            self._busy = True
            if action in {"build_snapshot", "activate_snapshot", "create_volume"}:
                self.checks.pop("daytona", None)
            operation = Operation(request_id, action)
            self.operations[request_id] = operation
            while len(self.operations) > 32:
                del self.operations[next(iter(self.operations))]

        def worker() -> None:
            terminal: dict[str, Any] | None = None
            try:
                operation.emit("Started " + action)
                result = run(operation)
                terminal = {"message": "Finished " + action, "event": "complete", "result": result}
            except SnapshotUnavailableError as exc:
                terminal = {"message": str(exc), "event": "error", "state": exc.state}
            except Exception as exc:
                # Provider errors and validation exception bodies may contain credentials.
                terminal = {
                    "message": (
                        f"{action} failed ({type(exc).__name__}). "
                        "Check configuration and provider state before retrying."
                    ),
                    "event": "error",
                }
            finally:
                with self._lock:
                    self._busy = False
                    if terminal is not None:
                        operation.emit(**terminal)

        self._pool.submit(worker)
        return operation

    def verify(self, operation: Operation, sections: tuple[str, ...] = SECTIONS) -> dict[str, Any]:
        """Probe each service without initializing schemas, sandboxes, or inference."""
        settings = self.settings()
        fingerprint = self.fingerprint(settings)
        results = self.checks.copy() if fingerprint == self._verified_fingerprint else {}
        for section in SECTIONS:
            if section not in sections:
                continue
            operation.emit("Checking " + section)
            try:
                results[section] = getattr(self, "check_" + section)(settings)
            except SnapshotUnavailableError as exc:
                results[section] = {"status": "blocked", "message": str(exc), "state": exc.state}
            except Exception as exc:
                results[section] = {
                    "status": "blocked",
                    "message": f"Check failed ({type(exc).__name__}); review this section's configuration and access.",
                }
            operation.emit(results[section]["message"], section=section, status=results[section]["status"])
        with self._lock:
            self.checks = results
            self._verified_fingerprint = fingerprint
        return self.status()

    def check_models(self, settings: FleetSettings) -> dict[str, Any]:
        """Discover serving routes while keeping paid inference an explicit action."""
        tested = self._inference_fingerprint == self.fingerprint(settings)
        inference = (
            "Root model inference test succeeded." if tested else "Inference is untested; the model test is optional."
        )
        if not settings.databricks_token.get_secret_value() or not (
            settings.databricks_host or settings.fleet_llm_base_url
        ):
            return {"status": "blocked", "message": "Configure the Gateway host/base URL and Databricks token."}
        configured = {settings.fleet_root_model, settings.fleet_sub_model}
        scopes = {model.removeprefix("openai/").rsplit(".", 1)[0] for model in configured}
        try:
            models = sorted({model for scope in sorted(scopes) for model in settings.list_models(scope)})
        except Exception:
            return {
                "status": "warning",
                "message": "Models are configured; discovery is unavailable. " + inference,
                "inference_tested": tested,
            }
        available = set(models)
        return {
            "status": "ready" if configured <= available else "blocked",
            "message": (
                "Model discovery complete. " + inference
                if configured <= available
                else "A configured serving model was not found. Select available root/sub model IDs."
            ),
            "models": models,
            "inference_tested": tested,
        }

    def check_database(self, settings: FleetSettings) -> dict[str, Any]:
        """Check existing database connections in read-only transactions."""
        import psycopg
        from psycopg import sql

        from fleet_rlm.store.db import SCHEMA_VERSION

        connection_class = psycopg.Connection
        if settings.fleet_lakebase_endpoint:
            from databricks.sdk import WorkspaceClient
            from databricks.sdk.core import Config

            from fleet_rlm.store.db import lakebase_connection_class

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
        versions = []
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
        if any(version > SCHEMA_VERSION for version in versions):
            return {
                "status": "blocked",
                "message": "The database requires a newer Fleet version; upgrade before starting.",
            }
        return {
            "status": "ready",
            "message": "Database connection verified. Fleet will apply its additive schema migrations at startup.",
            "schema_initialized": initialized,
            "schema_version": max(versions, default=0),
        }

    def check_mlflow(self, settings: FleetSettings) -> dict[str, Any]:
        """Check the selected tracking backend without provisioning experiments."""
        target = settings.mlflow_target()
        if target.mode == "local":
            return {"status": "ready", "message": "Local MLflow will be started or reused by the launcher."}
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
        return {
            "status": "ready",
            "message": (
                "Databricks MLflow is accessible. Startup will initialize the configured "
                "experiment and trace destination."
            ),
            "experiment_exists": experiment is not None,
        }

    @staticmethod
    def daytona(settings: FleetSettings) -> Any:
        """Construct a provider client only inside an authorized operation."""
        from daytona import Daytona, DaytonaConfig

        if not settings.fleet_daytona_api_key or not settings.fleet_daytona_org_id:
            raise ValueError("Configure Daytona API key and organization ID")
        return Daytona(
            DaytonaConfig(api_key=settings.fleet_daytona_api_key, organization_id=settings.fleet_daytona_org_id)
        )

    def volumes(self) -> list[dict[str, str]]:
        """List safe Volume identities from the configured organization."""
        return [
            {"id": volume.id, "name": volume.name, "state": str(getattr(volume.state, "value", volume.state))}
            for volume in self.daytona(self.settings()).volume.list()
        ]

    def check_daytona(self, settings: FleetSettings) -> dict[str, Any]:
        """Verify snapshot resource floors and the selected workspace Volume."""
        if not settings.fleet_daytona_api_key or not settings.fleet_daytona_org_id:
            return {"status": "blocked", "message": "Configure the Daytona API key and organization ID."}
        client = self.daytona(settings)
        from fleet_rlm.sandbox.snapshots import preflight

        identity = preflight(
            client.snapshot, api_key=settings.fleet_daytona_api_key, org_id=settings.fleet_daytona_org_id, refresh=True
        )
        if not settings.fleet_daytona_volume_id:
            return {"status": "blocked", "message": "Select or create a Daytona Volume.", "state": "volume_missing"}
        volumes = client.volume.list()
        volume = next((v for v in volumes if v.id == settings.fleet_daytona_volume_id), None)
        if volume is None:
            return {
                "status": "blocked",
                "message": "The configured Volume is unavailable in this organization.",
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

    def build(self, operation: Operation) -> dict[str, Any]:
        """Build explicitly and redact provider logs before streaming progress."""
        settings = self.settings()
        # Build output is intentionally redacted before entering replayable progress.
        secrets = [
            settings.fleet_daytona_api_key,
            settings.databricks_token.get_secret_value(),
            settings.fleet_database_url,
            settings.fleet_database_migration_url,
            settings.typesafe_api_key,
        ]

        def logs(chunk: str) -> None:
            for secret in filter(None, secrets):
                chunk = chunk.replace(secret, "[redacted]")
            operation.emit(chunk)

        result = build_snapshot(
            api_key=settings.fleet_daytona_api_key, org_id=settings.fleet_daytona_org_id, on_logs=logs
        )
        return result.as_dict()

    def activate(self, _operation: Operation) -> dict[str, Any]:
        """Activate once and verify the resulting snapshot identity."""
        settings = self.settings()
        result = activate_snapshot(api_key=settings.fleet_daytona_api_key, org_id=settings.fleet_daytona_org_id)
        return result.as_dict()

    def create_volume(self, name: str) -> dict[str, str]:
        """Create a new named Volume without adopting or replacing existing storage."""
        client = self.daytona(self.settings())
        if any(v.name == name for v in client.volume.list()):
            raise SetupConflictError("A Volume with this name already exists; select it instead.")
        volume = client.volume.create(name)
        return {"id": volume.id, "name": volume.name}

    def test_model(self, _operation: Operation) -> dict[str, str]:
        """Perform one explicitly requested root-model inference with no retries."""
        import dspy

        settings = self.settings()
        lm = settings._build_lm(settings.fleet_root_model).copy(num_retries=0, max_tokens=16, cache=False, callbacks=[])
        try:
            with dspy.context(callbacks=[]):
                result = lm("Reply with exactly OK")
            if not result:
                raise ValueError("Model returned no response")
        finally:
            lm.close()
        with self._lock:
            self._inference_fingerprint = self.fingerprint(settings)
            if "models" in self.checks:
                self.checks["models"] = {
                    **self.checks["models"],
                    "inference_tested": True,
                    "message": "Root model inference test succeeded. Verify to refresh serving-model discovery.",
                }
        return {"message": "A model response was received. This does not verify a complete Fleet Turn."}

    def can_apply(self) -> None:
        """Require saved and verified settings before allowing a controlled restart."""
        with self._lock:
            if self._applying:
                return  # An unobserved successful apply can be recovered without repeating work.
            status = self.status()
            if status["busy"] or not status["ready"]:
                raise SetupConflictError("Save your changes and verify the required sections before starting Fleet.")
            self._applying = True

    def cancel_apply(self) -> None:
        """Reopen setup when running work prevented a restart."""
        with self._lock:
            self._applying = False

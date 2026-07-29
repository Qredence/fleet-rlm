<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: logging_system
  name: Python stdlib logging with MLflow tracing observability
  category: logging_system
  scope:
      - '**'
  source_files:
      - src/fleet_rlm/config.py
      - src/fleet_rlm/app.py
      - src/fleet_rlm/observability/tracing.py
      - src/fleet_rlm/observability/turn_tracing.py
      - migrations/env.py
-->


The Fleet RLM backend uses Python's built-in `logging` module for all application log output, combined with optional MLflow DSPy autologging for engineering traces. There is no third-party logging framework (no loguru, structlog, or logzero); the system relies on the standard library logger hierarchy and level configuration driven by runtime settings.

**Framework and initialization**
- Each module creates a module-scoped logger via `logger = logging.getLogger(__name__)`, producing hierarchical loggers under the `fleet_rlm.*` namespace.
- The FastAPI app factory (`src/fleet_rlm/app.py`) calls `configure_logging(settings)` at startup, which sets the effective log level for the `fleet_rlm` and `dspy` root loggers based on the `Settings.log_level` field (a `Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]`).
- Handlers and sinks are not configured by the application; they are left to whatever process-level configuration exists (e.g., Alembic migrations use `logging.config.fileConfig` in `migrations/env.py`). This means stdout/stderr output depends on the default Python logging configuration unless an external handler is installed.

**Configuration model**
- Log level is controlled through the `FLEET_LOG_LEVEL` environment variable (mapped to `Settings.log_level`), applied uniformly to both `fleet_rlm` and `dspy` loggers during `configure_logging`.
- A `redacted_policy_summary()` helper exposes a safe diagnostic string that includes `log_level` without leaking secrets.

**Structured fields and context**
- Logging is unstructured text; there is no structured JSON logging, request IDs injected into log records, or custom `logging.Formatter`. Contextual information (session id, run id) is attached to MLflow spans rather than log records.
- Tests assert log levels using `pytest.caplog.at_level(..., logger="fleet_rlm.api.routes.turns")`, confirming that per-module logger names are the primary addressing mechanism.

**Tracing vs. logging separation**
- Engineering traces are handled separately via MLflow:
  - `observability/tracing.py` configures MLflow DSPy autolog against a Databricks Unity Catalog warehouse when `FLEET_MLFLOW_TRACING_ENABLED=true` and required settings are present. Setup is fail-soft: missing dependencies or auth failures produce warnings and the service continues.
  - `observability/turn_tracing.py` provides a `turn_trace()` context manager that opens a root `fleet_turn` span per Turn, attaching `session_id`, `run_id`, and an optional trace id exposed through a `ContextVar`. When disabled or unavailable, it yields a no-op handle with `trace_id=None`.
- Tracing is explicitly documented as engineering observability only and must never affect Turn outcomes.

**Conventions observed**
- Every module defines its own `logger = logging.getLogger(__name__)` and logs via `logger.debug/info/warning/error/critical`.
- Warning-level messages are used for non-fatal operational conditions (e.g., missing MLflow settings, import failures, cleanup drain expiry).
- Debug-level messages are used for internal state checks (e.g., conftest diagnostics, tracing disabled).
- No centralized formatter or sink is defined in application code; log routing is delegated to the process environment.
"""Explicitly authorized live acceptance using disposable DB/provider resources.

Usage: uv run python scripts/verify_runtime_live.py --live --model openai/SERVICE
Never prints credential values. Creates and drops a private PostgreSQL schema;
creates and deletes only sandboxes labeled with this run's random session ID.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import dspy
import psycopg
from daytona import Daytona, DaytonaConfig, DaytonaNotFoundError, ListSandboxesQuery
from dspy.primitives.facade import FacadeInvocation
from fastapi.testclient import TestClient
from psycopg import sql

from fleet_rlm.adapters import FleetRLMChatAdapter
from fleet_rlm.api.app import create_app
from fleet_rlm.config import FleetSettings
from fleet_rlm.optimize.evaluation import parse_sse
from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME

# Reported by the Turn itself, so the harness measures without instrumenting the runtime.
_METRIC_FIELDS = (
    "route",
    "answer",
    "duration_seconds",
    "lm_calls",
    "budget_breakdown",
    "sandbox_errors",
    "submit_rejections",
    "trace_id",
)
_OUTCOME_EVENTS = frozenset({"turn_complete", "error", "turn_cancelled"})


def _turn_metrics(events: list[dict], wall_clock: float) -> dict[str, object]:
    """Latency, budget accounting, and stream shape for before/after parity.

    Reads only what the Turn already reports and what the client observes, so the
    harness stays measurement-only: no runtime code is touched to gather it.
    """
    terminal = next((event for event in events if event.get("event") in _OUTCOME_EVENTS), {})
    metrics: dict[str, object] = {
        "wall_clock_s": round(wall_clock, 3),
        "event_kinds": [event.get("event") for event in events],
        "outcome": terminal.get("event"),
    }
    metrics.update({field: terminal.get(field) for field in _METRIC_FIELDS})
    return metrics


def main() -> None:
    """Run the `--live` runtime checks and save a report after attempting resource cleanup.

    Creates a private database schema and provider sandboxes. Exits unsuccessfully
    if a check, report write, or cleanup fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize disposable credential-backed calls")
    parser.add_argument("--model", required=True)
    parser.add_argument("--local-postgres", action="store_true", help="Use disposable Orb PostgreSQL on port 55432")
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Capture traces in a unique experiment on the configured local MLflow server (must be running)",
    )
    parser.add_argument("--output", type=Path, help="Report path (default: .scratch/runtime-validation/<run>.json)")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live authorization is required")

    session = "runtime-acceptance-" + uuid.uuid4().hex[:12]
    schema = session.replace("-", "_")
    run_dir = Path(".scratch/runtime-validation") / session
    output = args.output or Path(".scratch/runtime-validation") / f"{session}.json"
    local_database = "postgresql://user@127.0.0.1:55432/postgres"
    base_settings = FleetSettings()
    settings = base_settings.with_explicit_overrides(
        fleet_root_model=args.model,
        fleet_sub_model=args.model,
        fleet_reflection_model=args.model,
        fleet_database_url=local_database if args.local_postgres else base_settings.fleet_database_url,
        fleet_database_migration_url=local_database
        if args.local_postgres
        else base_settings.fleet_database_migration_url,
        fleet_database_schema=schema,
        compiled_program_dir=str(run_dir / "compiled"),
        local_skills_dir=str(run_dir / "skills"),
        mlflow_mode="local",
        mlflow_local_experiment_name=f"runtime-acceptance-{uuid.uuid4().hex[:12]}",
        typesafe_api_key="",
        execution_mode="rlm",
        max_upload_bytes=25_000_000,
    )
    required = {
        "FLEET_DAYTONA_API_KEY": settings.fleet_daytona_api_key,
        "FLEET_DAYTONA_ORG_ID": settings.fleet_daytona_org_id,
        "FLEET_DAYTONA_VOLUME_ID": settings.fleet_daytona_volume_id,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        parser.error("Missing credentials: " + ", ".join(missing))
    if not settings.fleet_database_migration_url:
        parser.error("FLEET_DATABASE_MIGRATION_URL is required for private-schema setup and cleanup")

    report: dict = {
        "session": session,
        "model": args.model,
        "snapshot": SNAPSHOT_NAME,
        "cases": {},
        "metrics": {},
    }
    start = time.monotonic()
    provider = None
    schema_created = False
    failure: Exception | None = None
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        provider = Daytona(
            DaytonaConfig(api_key=settings.fleet_daytona_api_key, organization_id=settings.fleet_daytona_org_id)
        )
        with psycopg.connect(settings.fleet_database_migration_url) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            # The migration role owns the schema, while the app connects as a different role. Without
            # USAGE and default privileges the serving role cannot see the migrated tables at all
            # (`UndefinedTable`), so grant them here, before `initialize` creates anything.
            app_role = urlparse(settings.fleet_database_url).username
            if app_role and app_role != urlparse(settings.fleet_database_migration_url).username:
                connection.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(app_role))
                )
                for object_type in ("TABLES", "SEQUENCES"):
                    connection.execute(
                        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT ALL ON {} TO {}").format(
                            sql.Identifier(schema), sql.SQL(object_type), sql.Identifier(app_role)
                        )
                    )
            schema_created = True
        app = create_app(settings=settings, configure_observability=args.trace)
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            # 1.25M records, >10 MB, far above the model context window.
            # Cycles 0..96 independently give sum=3686, negatives=10 per 97.
            count = 1_250_000
            raw = ("id,value\n" + "".join(f"{i},{i % 97 - 10}\n" for i in range(count))).encode()
            cycles, remainder = divmod(count, 97)
            expected = {
                "rows": count,
                "sum": cycles * 3686 + remainder * (remainder - 1) // 2 - remainder * 10,
                "negative": cycles * 10 + min(remainder, 10),
            }
            response = client.post(f"/sessions/{session}/attach", files={"file": ("ledger.csv", raw)})
            assert response.status_code == 200, response.text
            download = client.get(f"/sessions/{session}/workspace/files/files/ledger.csv")
            assert download.content == raw
            report["cases"]["large_roundtrip"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
            response = client.put(f"/sessions/{session}/context", json={"path": "files/ledger.csv"})
            assert response.status_code == 200, response.text
            task = (
                "Inspect the entire context ledger using streaming Python csv processing. Count data rows, "
                "sum the value column as integers, and count values < 0. Write artifacts/summary.json "
                "using workspace.write_artifact('summary.json', json.dumps(result)) with keys rows, sum, negative. "
                "Submit a short answer stating those verified numbers. "
                "Do not print or load the full dataset into a prompt."
            )
            ledger_started = time.monotonic()
            response = client.post(f"/sessions/{session}/turn", json={"task": task, "idempotency_key": "ledger-v1"})
            events = parse_sse(response.text)
            report["metrics"]["ledger_turn_rlm"] = _turn_metrics(events, time.monotonic() - ledger_started)
            report["events"] = events
            outcomes = [e for e in events if e["event"] in {"turn_complete", "error"}]
            assert len(outcomes) == 1 and outcomes[0]["event"] == "turn_complete", outcomes
            turn_id = outcomes[0]["turn_id"]
            persisted = client.get(f"/sessions/{session}/turns/{turn_id}").json()
            assert persisted["status"] == "completed"
            assert persisted["result"]["answer"] == outcomes[0]["answer"]
            app.state.session_registry._sessions[session].harness.store.finish_turn(
                session, turn_id, "failed", {"event": "error", "error": "simulated lost delivery"}
            )
            assert client.get(f"/sessions/{session}/turns/{turn_id}").json()["status"] == "completed"
            report["cases"]["durable_completion_survives_delivery_failure"] = "passed"
            actual_response = client.get(f"/sessions/{session}/workspace/files/artifacts/summary.json")
            actual = actual_response.json()
            assert actual == expected, (actual, expected)
            report["cases"]["strict_rlm_analysis"] = {
                "expected": expected,
                "actual": actual,
                "answer": outcomes[0]["answer"],
            }
            replay_started = time.monotonic()
            replay = client.post(f"/sessions/{session}/turn", json={"task": task, "idempotency_key": "ledger-v1"})
            replay_events = parse_sse(replay.text)
            report["metrics"]["ledger_replay"] = _turn_metrics(replay_events, time.monotonic() - replay_started)
            replay_outcomes = [e for e in replay_events if e["event"] in {"turn_complete", "error"}]
            assert len(replay_outcomes) == 1 and replay_outcomes[0].get("answer") == outcomes[0]["answer"]
            assert not any(e["event"] == "repl_code" for e in replay_events)
            report["cases"]["idempotent_replay"] = "passed"
            source = {"files/ledger.csv": hashlib.sha256(raw).hexdigest()}
            assert (
                client.put(
                    f"/sessions/{session}/memory/ledger",
                    json={
                        "content": "The ledger contains 1250000 records verified by Python.",
                        "sources": source,
                        "reviewed": True,
                    },
                ).status_code
                == 200
            )
            matched = client.get(f"/sessions/{session}/memory", params={"q": "reconcile ledger anomalies"}).json()
            assert [item["memory_id"] for item in matched["memories"]] == ["ledger"]
            report["cases"]["source_backed_memory"] = "passed"
            runtime = app.state.session_registry._sessions[session]
            manager = runtime.sandbox_mgr
            recursive_events: list[dict] = []
            manager.event_callback = recursive_events.append
            manager.reset_turn_budget()
            lm = settings._build_lm(args.model)
            with dspy.context(lm=lm, adapter=FleetRLMChatAdapter()):
                interpreter = manager.interpreter_factory(
                    tools={"spawn_agent": runtime.engine.primary_rlm._user_tools["spawn_agent"]}
                )()
                facade = FacadeInvocation({}, manager.interpreter_factory(), 20, lm=lm, reserve=manager.consume_budget)
                facade.install(interpreter)
                try:
                    for kind in ("Predict", "ChainOfThought", "ReActV2"):
                        options = ", tools=[], max_iters=3" if kind == "ReActV2" else ""
                        result = interpreter.execute(
                            f"import dspy\nagent = dspy.{kind}('task -> result'{options})\n"
                            "agent(task='Compute 7*19 exactly. Return only the integer in result.').result"
                        )
                        assert str(result).strip() == "133", (kind, str(result))
                        report["cases"][f"facade_{kind}"] = str(result)
                    manager.write_workspace_file("files/private.txt", b"not approved for the explicit child")
                    scoped = interpreter.execute(
                        'spawn_agent(subtask="Use Python to check if /workspace/files/private.txt exists, '
                        "and read only the first header line of /workspace/files/ledger.csv. "
                        "Write exactly one artifact using workspace.write_artifact('selected.json', json.dumps(data)) "
                        "with JSON keys "
                        "private_visible (boolean) and header (str without newline), "
                        "then SUBMIT(findings='checked').\", "
                        "input_paths=['files/ledger.csv'], allowed_tools=[], max_iters=4, max_llm_calls=5)"
                    )
                    assert isinstance(scoped, dict) and len(scoped["artifacts"]) == 1, scoped
                    promoted = scoped["artifacts"][0]
                    promoted_raw = manager.read_workspace_file(promoted["path"])
                    assert hashlib.sha256(promoted_raw).hexdigest() == promoted["sha256"]
                    assert json.loads(promoted_raw) == {"private_visible": False, "header": "id,value"}
                    report["cases"]["scoped_spawn_and_promotion"] = scoped
                    result = interpreter.execute(
                        "import dspy\n"
                        "agent = dspy.RLM('task -> result', max_iters=5)\n"
                        "agent(task=\"Build a nested dspy.RLM('task -> result', max_iters=3), ask it to calculate "
                        "13*17 with Python and SUBMIT(result=str(13*17)), "
                        'then SUBMIT(result=str(nested_result.result)).").result'
                    )
                    assert "221" in str(result), str(result)
                    report["cases"]["nested_facade"] = {"result": str(result), "events": recursive_events}
                finally:
                    interpreter.shutdown()
                    manager.end_turn()
                    manager.event_callback = None
            ids = {e["sandbox_id"] for e in recursive_events if e["event"] == "sandbox_ready"}
            assert len(ids) >= 2, "Expected distinct child and grandchild sandboxes"
            assert not manager._children, "Ephemeral children were not cleaned up"
            discovery = client.get("/models")
            assert discovery.status_code == 200, discovery.text
            models = discovery.json()["models"]
            assert args.model in models
            alternate = next(model for model in models if model != args.model)
            selection = client.put(f"/sessions/{session}/model", json={"role": "sub", "model": alternate})
            assert selection.status_code == 200, selection.text
            assert manager.sub_lm.model == alternate
            selection = client.put(f"/sessions/{session}/mode", json={"mode": "flex"})
            assert selection.status_code == 200, selection.text
            configuration = client.get(f"/sessions/{session}/config").json()
            assert configuration["models"] == {"root": args.model, "sub": alternate}
            assert configuration["context"]["workspace_path"] == "files/ledger.csv"
            report["cases"]["model_discovery_selection"] = {"count": len(models), "configuration": configuration}
        # Rebuild the app and root manager: Turns and settings come from PostgreSQL, files from the Volume.
        with TestClient(create_app(settings=settings, configure_observability=False)) as resumed:
            recovered_config = resumed.get(f"/sessions/{session}/config").json()
            assert recovered_config == configuration
            report["cases"]["model_configuration_restart"] = "passed"
            assert resumed.get(f"/sessions/{session}/workspace/files/artifacts/summary.json").json() == expected
            assert len(resumed.get(f"/sessions/{session}/history").json()["turns"]) == 1
            assert resumed.get(f"/sessions/{session}/memory").json()["memories"][0]["memory_id"] == "ledger"
            report["cases"]["restart_recovery"] = "passed"
            # Route coverage: pinned modes are deterministic, so their route is a parity check.
            # Direct is only reachable by hybrid triage with no selected context.
            arithmetic = "Use Python to compute 13*17 exactly and SUBMIT the result as the answer."
            for mode, case, task_text, expected_route in (
                ("rlm", "route_rlm", arithmetic, "rlm"),
                ("flex", "route_flex", arithmetic, "flex"),
            ):
                selection = resumed.put(f"/sessions/{session}/mode", json={"mode": mode})
                assert selection.status_code == 200, selection.text
                started = time.monotonic()
                body = resumed.post(f"/sessions/{session}/turn", json={"task": task_text, "idempotency_key": case})
                assert body.status_code == 200, body.text
                route_events = parse_sse(body.text)
                report["metrics"][case] = _turn_metrics(route_events, time.monotonic() - started)
                assert report["metrics"][case]["route"] == expected_route, report["metrics"][case]
                assert report["metrics"][case]["answer"] == "221", report["metrics"][case]
            cleared = resumed.delete(f"/sessions/{session}/context")
            assert cleared.status_code == 200, cleared.text
            selection = resumed.put(f"/sessions/{session}/mode", json={"mode": "hybrid"})
            assert selection.status_code == 200, selection.text
            started = time.monotonic()
            body = resumed.post(
                f"/sessions/{session}/turn",
                json={"task": "Reply with exactly this text and nothing else: ACK", "idempotency_key": "route_hybrid"},
            )
            assert body.status_code == 200, body.text
            hybrid_events = parse_sse(body.text)
            report["metrics"]["route_hybrid"] = _turn_metrics(hybrid_events, time.monotonic() - started)
            assert report["metrics"]["route_hybrid"]["answer"] == "ACK", report["metrics"]["route_hybrid"]
            selection = resumed.put(f"/sessions/{session}/context", json={"path": "files/ledger.csv"})
            assert selection.status_code == 200, selection.text
            manager = resumed.app.state.session_registry._sessions[session].sandbox_mgr
            manager.reset_turn_budget()
            allowance = manager.max_llm_calls
            manager.max_llm_calls = 3
            manager.consume_budget(2)
            manager.consume_budget()
            try:
                manager.consume_budget()
                raise AssertionError("Over-budget work was admitted")
            except RuntimeError:
                pass
            assert manager._llm_calls_used == 3
            manager.max_llm_calls = allowance
            report["cases"]["shared_budget_boundary"] = "passed"
            capacity = manager.max_active_children
            manager.max_active_children = 1
            child = manager.allocate_child()
            try:
                child.allocate_child()
                raise AssertionError("Descendant exceeded live sandbox capacity")
            except RuntimeError:
                pass
            assert len(manager._children) == 1
            child.write_workspace_file("artifacts/cancelled.txt", b"must not be promoted")
            cancellation_events: list[dict] = []
            manager.event_callback = cancellation_events.append
            manager.cancel_turn()
            assert not manager._children
            try:
                child.collect_child_artifacts()
                raise AssertionError("Cancelled child promoted an artifact")
            except RuntimeError:
                pass
            assert any(event["event"] == "sandbox_deleted" for event in cancellation_events)
            report["cases"]["capacity_and_cancellation"] = {
                "events": cancellation_events,
                "unauthorized_promotion": False,
            }
            manager.max_active_children = capacity
            manager.event_callback = None
        report["status"] = "passed"
    except Exception as exc:
        # Provider and database exceptions may contain request headers or credentials.
        failure = exc
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
    finally:
        deleted = []
        remaining = []
        if provider is not None:
            try:
                for sandbox in provider.list(ListSandboxesQuery(labels={"fleet_session": session})):
                    try:
                        provider.delete(sandbox, wait=True, timeout=60)
                        deleted.append(sandbox.id)
                    except DaytonaNotFoundError:
                        # It expired between the listing and the delete: already cleaned up.
                        continue
                # Provider list indexes can briefly lag a confirmed delete.
                deadline = time.monotonic() + 60
                while True:
                    remaining = [s.id for s in provider.list(ListSandboxesQuery(labels={"fleet_session": session}))]
                    if not remaining or time.monotonic() >= deadline:
                        break
                    time.sleep(2)
            except Exception as exc:
                report["sandbox_cleanup_error_type"] = type(exc).__name__
                report["status"] = "cleanup_incomplete"
        report["deleted_sandboxes"] = deleted
        report["remaining_sandboxes"] = remaining
        if remaining:
            report["status"] = "cleanup_incomplete"
        if schema_created:
            try:
                with psycopg.connect(settings.fleet_database_migration_url) as connection:
                    connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
                report["schema_cleanup"] = "dropped"
            except Exception as exc:
                report["schema_cleanup_error_type"] = type(exc).__name__
                report["status"] = "cleanup_incomplete"
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, default=str))
            print(json.dumps({"status": report.get("status"), "cases": list(report["cases"]), "evidence": str(output)}))
        except Exception as exc:
            print(json.dumps({"status": "report_write_failed", "error_type": type(exc).__name__}))
            if failure is None:
                failure = exc

    if failure is not None or report.get("status") != "passed":
        raise SystemExit("Runtime acceptance failed or cleanup was incomplete; inspect the sanitized report") from None


if __name__ == "__main__":
    main()

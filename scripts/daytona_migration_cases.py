"""Live sandbox replacement and recursive trajectory checks for Fleet staging."""

import asyncio
import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import httpx
import mlflow
from bundled_skill_cases import verify_chart
from dspy_baseline import workspace_trace_root, workspace_trace_usage
from fleet_rlm.config import Settings
from fleet_rlm.interpreter import (
    delete_sandbox_by_id,
    get_running_sandbox,
    make_daytona_client,
)

_CSV = b"value\n2\n5\n11\n"
_CHART = "artifacts/result.png"
_FIRST = (
    "@data-analysis @chart-visualization. Use exactly this workflow. In the first Python "
    "cell read /workspace/files/sales.csv into a variable, set phase2_sentinel = 'first', "
    "and print the values. In the next cell call spawn_agent once and ask the child to "
    "compute the CSV sum itself. Use your retained list to cross-check its answer, save a "
    "PNG chart at /workspace/artifacts/result.png. In SUBMIT, put the numeric sum alone "
    "in answer and put the chart path in artifact_paths."
)
_SECOND = (
    "@data-analysis. Use Python to assert that phase2_sentinel is absent from globals(). "
    "Read the staged CSV and verify its sum matches the value from the previous completed "
    "turn in prior_turns. Complete the turn directly by calling "
    "SUBMIT(answer='<sum>; fresh', artifact_paths=[]), substituting the computed sum."
)
_RECONCILE = (
    "@data-analysis @recursive-synthesis. Reconcile the two staged CSVs and classify the "
    "two decisions in decisions.txt. Use at least two Python cells. Call spawn_agent(task) "
    "to run each of two RLM children, "
    "one for each CSV. Require the North child to call spawn_agent once for an independent "
    "audit of north.csv and consume the grandchild's result. Ask each child to write its "
    "total into a distinct .txt file under /workspace/artifacts. Use llm_query_batched with "
    "two tiny prompts derived from the two lines of decisions.txt and request approval "
    "labels approved or rejected as single words. Strictly use those returned labels when "
    "composing the answer. Cross-check your child totals. In your last cell call "
    "SUBMIT with answer set to '<total>|<north-label>|<south-label>' and artifact_paths "
    "set to a list of both artifact paths."
)


def _require(condition: object, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _runs(parts: list[dict], parent: str, depth: int = 0) -> Iterator[dict]:
    for part in parts:
        if (
            part["type"] != "tool-subagent"
            or part.get("state") != "output-available"
            or part.get("preliminary")
            or not isinstance(part.get("output"), dict)
        ):
            continue
        child = part["output"]
        child_parts = child["parts"]
        if any(p["type"] == "text" and p.get("text") for p in child_parts):
            yield {
                "id": child["id"],
                "parent": parent,
                "depth": depth + 1,
                "kind": part["input"]["kind"],
                "task": part["input"]["task"],
                "parts": child_parts,
            }
            yield from _runs(child_parts, child["id"], depth + 1)


def _contains_submission(parts: list[dict], answer: str) -> bool:
    def value(field):
        if isinstance(field, dict) and field.get("__dspy__") == "wire":
            return field.get("value")
        return field

    for part in parts:
        if (
            part["type"] == "tool-python_exec"
            and part.get("state") == "output-available"
            and re.search(r"\bSUBMIT\s*\(", part.get("input", ""))
        ):
            output = part.get("output")
            if (
                isinstance(output, dict)
                and str(value(output.get("answer"))).strip() == answer.strip()
                and isinstance(value(output.get("artifact_paths")), list)
            ):
                return True
    return False


def _verify_trace(turn: dict, spans: list, arm: str) -> None:
    by_id = {span.span_id: span for span in spans}

    def ancestors(span):
        while span.parent_id is not None:
            span = by_id[span.parent_id]
            yield span

    def succeeded(span):
        return span.status.status_code.name == "OK"

    def parents_include(span, ids):
        return any(parent.span_id in ids for parent in ancestors(span))

    def matches_run(span, run):
        if arm != "v2":
            return (
                isinstance(span.inputs, dict) and span.inputs.get("task") == run["task"]
            )
        scope = by_id.get(span.parent_id)
        return (
            scope is not None
            and scope.name == "fleet.rlm.run"
            and succeeded(scope)
            and scope.attributes.get("fleet.root_run_id") == turn["run_id"]
            and scope.attributes.get("fleet.run_id") == run["id"]
            and scope.attributes.get("fleet.parent_run_id") == run["parent"]
            and scope.attributes.get("fleet.depth") == run["depth"]
        )

    if arm == "v2" and turn.get("requires_batched_query"):
        _require(
            any(
                span.name == "fleet.rlm.llm_query"
                and span.attributes.get("fleet.tool") == "llm_query_batched"
                and span.attributes.get("fleet.parent_run_id") == turn["run_id"]
                and succeeded(span)
                for span in spans
            ),
            "No successful batched query span",
        )

    runs = [
        {
            "id": turn["run_id"],
            "parent": "",
            "depth": 0,
            "kind": "root",
            "task": turn["task"],
        },
        *_runs(turn["parts"], turn["run_id"]),
    ]
    cells_by_run = {}
    matched = set()
    owners = {}
    sandbox_ids = set()
    for run in runs:
        parent_cells = cells_by_run.get(run["parent"], set())
        if run["kind"] == "llm_query":
            if arm == "v2":
                queries = {
                    span.span_id
                    for span in spans
                    if span.name == "fleet.rlm.llm_query"
                    and span.attributes.get("fleet.run_id") == run["id"]
                    and span.attributes.get("fleet.parent_run_id") == run["parent"]
                    and parents_include(span, parent_cells)
                    and succeeded(span)
                }
                _require(queries, "Missing model query span for completed child")
                parent_cells = queries
            _require(
                any(
                    span.name == "LM.__call__"
                    and succeeded(span)
                    and parents_include(span, parent_cells)
                    for span in spans
                ),
                "Missing successful model call under the query",
            )
            continue
        rlm = next(
            (
                span
                for span in spans
                if span.name == "RLM.forward"
                and succeeded(span)
                and span.span_id not in matched
                and matches_run(span, run)
                and (
                    parents_include(span, parent_cells)
                    if run["parent"]
                    else not any(
                        parent.name == "RLM.forward" for parent in ancestors(span)
                    )
                )
            ),
            None,
        )
        _require(rlm is not None, f"Missing completed RLM span for {run['id']}")
        matched.add(rlm.span_id)
        cells = [
            s
            for s in spans
            if s.name == "daytona.python.execute"
            and next((p for p in ancestors(s) if p.name == "RLM.forward"), None) is rlm
        ]
        _require(
            any(succeeded(s) and s.outputs for s in cells),
            f"No successful cell for {run['id']}",
        )
        for cell in cells:
            sandbox_ids.add(cell.attributes.get("daytona.sandbox_id"))
            if arm == "v2":
                worker = cell.attributes.get("daytona.worker_id")
                _require(worker, "Missing worker ID")
                _require(
                    owners.setdefault(worker, run["id"]) == run["id"],
                    "RLMs shared a worker",
                )
        cells_by_run[run["id"]] = {cell.span_id for cell in cells}
    _require(
        len(sandbox_ids) == 1 and None not in sandbox_ids,
        "RLMs did not share one sandbox",
    )
    turn["rlm_invocations"] = len(matched)
    turn["worker_ids"] = sorted(owners)


def _attach_traces(
    tracker: mlflow.MlflowClient, experiment_id: str, cases: list[dict], arm: str
) -> None:
    turns = [turn for case in cases for turn in case["turns"]]
    if not turns:
        return
    after = min(turn["started_ms"] for turn in turns) - 3000
    traces = tracker.search_traces(
        locations=[experiment_id],
        filter_string=f"timestamp_ms >= {after}",
        max_results=500,
    )
    for turn in turns:
        if turn.get("trace_id"):
            continue
        for trace in traces:
            spans = trace.data.spans
            root = workspace_trace_root(trace)
            if root is None or not any(
                span.name == "daytona.python.execute" for span in spans
            ):
                continue
            if trace.info.tags.get("fleet.run_id") != turn["run_id"]:
                continue
            turn["trace_id"] = trace.info.trace_id
            turn["tokens"], turn["model_calls"] = workspace_trace_usage(trace, root)
            turn["spans"] = [
                {
                    "id": span.span_id,
                    "parent_id": span.parent_id,
                    "name": span.name,
                    "status": str(span.status.status_code),
                    "attributes": {
                        key: value
                        for key, value in span.attributes.items()
                        if key.startswith(("daytona.", "fleet."))
                    },
                }
                for span in spans
            ]
            try:
                _verify_trace(turn, spans, arm)
            except (KeyError, ValueError) as error:
                turn["trace_error"] = str(error)
            break


async def run_daytona_migration(url: str, output: Path, repeats: int, arm: str) -> bool:
    """Run against a disposable deployment using that deployment's settings."""
    settings = Settings()
    if not settings.mlflow_tracking_uri:
        raise ValueError("Configure the staging MLflow tracking URI")
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    tracker = mlflow.MlflowClient()
    headers = {}
    if settings.api_bearer_token:
        headers["Authorization"] = (
            f"Bearer {settings.api_bearer_token.get_secret_value()}"
        )
    output.mkdir(parents=True, exist_ok=True)
    rows = []

    async with (
        httpx.AsyncClient(base_url=url, headers=headers, timeout=180) as http,
        make_daytona_client(settings) as daytona,
    ):

        async def call(method, path, **options):
            response = await http.request(method, path, **options)
            response.raise_for_status()
            return response

        async def run_case(repeat: int, case: str):
            created = await call(
                "POST", "/api/sessions", json={"title": f"Daytona {arm} {case}"}
            )
            session_id = created.json()["id"]
            route = f"/api/sessions/{session_id}"
            row = {
                "case": case,
                "repeat": repeat,
                "session_id": session_id,
                "fixtures": {},
                "turns": [],
            }
            rows.append(row)

            async def upload(name: str, data: bytes) -> None:
                file = await call(
                    "POST", route + "/files", files={"file": (name, data)}
                )
                read = await call("GET", route + "/files/files/" + name)
                digest = hashlib.sha256(data).hexdigest()
                _require(
                    read.content == data and file.json()["sha256"] == digest,
                    f"Input round trip failed for {name}",
                )
                row["fixtures"][name] = {"sha256": digest, "bytes": len(data)}

            async def vm_id():
                response = await call("GET", route)
                return response.json()["daytona_sandbox_id"]

            async def chat(prompt: str, answer: str, *, cells: int, depth: int):
                prompt += (
                    f" The acceptance attempt identifier is {repeat}."
                    " Keep the answer in the requested format."
                )
                started = time.monotonic()
                timestamp = time.time_ns() // 1_000_000
                response = await call(
                    "POST",
                    route + "/chat",
                    json={
                        "messages": [
                            {
                                "role": "user",
                                "parts": [{"type": "text", "text": prompt}],
                            }
                        ],
                    },
                )
                frames = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: {")
                ]
                history = (await call("GET", route + "/messages")).json()
                message = history[-1]
                parts = message["parts"]
                actual = "".join(
                    part["text"] for part in parts if part["type"] == "text"
                )
                row["turns"].append(
                    {
                        "run_id": message["id"],
                        "task": prompt,
                        "started_ms": timestamp,
                        "seconds": round(time.monotonic() - started, 3),
                        "answer": actual,
                        "parts": parts,
                        "frames": frames,
                    }
                )
                _require(
                    response.headers.get("x-vercel-ai-ui-message-stream") == "v1",
                    "SSE protocol",
                )
                _require(
                    any(frame["type"] == "finish" for frame in frames),
                    "Missing finish frame",
                )
                _require(
                    not any(frame["type"] in {"abort", "error"} for frame in frames),
                    "Failed turn",
                )
                _require(
                    actual.strip().lower() == answer, f"Unexpected answer {actual!r}"
                )
                _require(
                    sum(p["type"] == "tool-python_exec" for p in parts) >= cells,
                    "Too few cells",
                )
                _require(
                    _contains_submission(parts, actual),
                    "No matching typed root submission",
                )
                runs = list(_runs(parts, message["id"]))
                if depth:
                    _require(
                        any(
                            run["depth"] == depth
                            and run["kind"] == "subagent"
                            and any(
                                part["type"] == "tool-python_exec"
                                and part.get("state") == "output-available"
                                for part in run["parts"]
                            )
                            for run in runs
                        ),
                        f"No RLM at depth {depth}",
                    )
                return runs

            async def artifact(path: str) -> bytes:
                return (await call("GET", route + "/files/" + path)).content

            try:
                if case == "csv-replacement":
                    await upload("sales.csv", _CSV)
                    if arm == "v2":
                        _require(await vm_id() is None, "Upload provisioned a VM")
                    await chat(_FIRST, "18", cells=2, depth=1)
                    original = row["original_vm"] = await vm_id()
                    _require(original, "Missing sandbox binding")
                    chart = await artifact(_CHART)
                    _require(verify_chart(_CHART, chart), "Chart did not parse")
                    row["chart_sha256"] = hashlib.sha256(chart).hexdigest()
                    if arm == "v2":
                        await delete_sandbox_by_id(daytona, original)
                        _require(
                            await get_running_sandbox(daytona, original) is None,
                            "VM still running",
                        )
                        _require(
                            await artifact(_CHART) == chart, "Cold chart bytes changed"
                        )
                    await chat(_SECOND, "18; fresh", cells=1, depth=0)
                    current = row["second_vm"] = await vm_id()
                    _require(
                        current and (current != original) == (arm == "v2"),
                        "Wrong replacement behavior",
                    )
                    _require(
                        await artifact(_CHART) == chart,
                        "Chart bytes changed on second turn",
                    )
                else:
                    await upload("north.csv", b"value\n2\n5\n")
                    await upload("south.csv", b"value\n11\n")
                    await upload(
                        "decisions.txt",
                        (
                            b"North: authorization explicitly granted.\n"
                            b"South: authorization explicitly denied.\n"
                        ),
                    )
                    runs = await chat(
                        _RECONCILE, "18|approved|rejected", cells=2, depth=2
                    )
                    root = row["turns"][-1]
                    root["requires_batched_query"] = True
                    _require(
                        sum(
                            run["kind"] == "subagent"
                            and run["parent"] == root["run_id"]
                            for run in runs
                        )
                        >= 2,
                        "Fewer than two direct RLM children completed",
                    )
                    _require(
                        any(run["kind"] == "llm_query" for run in runs)
                        and any(
                            part["type"] == "tool-python_exec"
                            and part.get("state") == "output-available"
                            and re.search(
                                r"\bllm_query_batched\s*\(", part.get("input", "")
                            )
                            for part in root["parts"]
                        ),
                        "No batched query",
                    )
                    files = (await call("GET", route + "/files")).json()
                    published = []
                    for file in files:
                        if file["kind"] == "artifact":
                            content = await artifact(file["path"])
                            digest = hashlib.sha256(content).hexdigest()
                            _require(digest == file["sha256"], "Artifact hash mismatch")
                            published.append(
                                {
                                    "path": file["path"],
                                    "sha256": digest,
                                    "text": content.decode(),
                                }
                            )
                    _require(len(published) >= 2, "Missing child artifacts")
                    for total in ("7", "11"):
                        _require(
                            any(total == item["text"].strip() for item in published),
                            f"Missing child total {total}",
                        )
                    row["artifacts"] = published
                row["passed"] = True
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as error:
                row.update(passed=False, error=str(error))
            finally:
                try:
                    row["cleanup_http"] = (await http.delete(route)).status_code
                    _require(row["cleanup_http"] == 204, "Session deletion failed")
                except (httpx.HTTPError, ValueError) as error:
                    row.update(
                        passed=False, cleanup_error=str(error) or type(error).__name__
                    )

        deployment = (await call("GET", "/api/settings")).json()
        settings_evidence = {
            field: deployment["effective"][field]
            for field in (
                "llm_model",
                "sub_llm_model",
                "decision_model",
                "rlm_max_iters",
                "rlm_max_llm_calls",
                "rlm_max_depth",
                "rlm_max_output_chars",
            )
        }
        status = deployment["status"]
        experiment = tracker.get_experiment_by_name(status["mlflow_experiment"])
        if experiment is None:
            raise ValueError(
                "Start the staging deployment with its configured MLflow experiment"
            )
        settings_evidence["image"] = status.get("daytona_image") or status.get(
            "daytona_snapshot"
        )
        for repeat in range(1, repeats + 1):
            await asyncio.gather(
                *(
                    run_case(repeat, case)
                    for case in ("csv-replacement", "reconciliation")
                )
            )
            (output / f"daytona-{arm}.json").write_text(
                json.dumps(rows, indent=2) + "\n"
            )

    for _ in range(30):
        await asyncio.to_thread(
            _attach_traces, tracker, experiment.experiment_id, rows, arm
        )
        if all(turn.get("trace_id") for row in rows for turn in row["turns"]):
            break
        await asyncio.sleep(2)
    for row in rows:
        if not all(turn.get("trace_id") for turn in row["turns"]):
            row.update(
                passed=False, trace_error="One or more turns could not be traced"
            )
        elif any(turn.get("trace_error") for turn in row["turns"]):
            row.update(
                passed=False,
                trace_error="One or more trajectories failed trace verification",
            )
    payload = {
        "arm": arm,
        "experiment": experiment.experiment_id,
        "settings": settings_evidence,
        "rows": rows,
    }
    path = output / f"daytona-{arm}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(path, Counter((row["case"], row["passed"]) for row in rows))
    return all(row["passed"] for row in rows)

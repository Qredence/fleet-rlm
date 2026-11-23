"""Run repeated synthetic workflows through the live Fleet HTTP endpoints."""

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

import httpx
import mlflow
from dspy_baseline import CASES, answer_passes
from fleet_rlm.config import Settings
from mlflow import MlflowClient


async def evaluate(
    url: str, output: Path, repeats: int, *, large: bool, resume: Path | None
) -> None:
    settings = Settings()
    if not settings.mlflow_tracking_uri:
        raise ValueError("Configure FLEET_MLFLOW_TRACKING_URI for workflow provenance")
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment)
    tracker = MlflowClient()
    headers = {}
    if settings.api_bearer_token:
        headers["Authorization"] = (
            f"Bearer {settings.api_bearer_token.get_secret_value()}"
        )
    output.mkdir(parents=True, exist_ok=True)
    cases = {case.id: case for case in CASES}
    tasks = {
        case.id: tracker.get_trace(case.source_trace).data.spans[0].inputs["task"]
        for case in CASES
    }
    tasks["birch-invalid"] = tasks["birch-valid"].replace("C = 13.", "C = 13.5.")
    run_tag = uuid4().hex[:6]
    rows = (
        [
            row
            for row in json.loads(resume.read_text())["rows"]
            if row["passed"] is not None
        ]
        if resume
        else []
    )
    completed = {(row["id"], row["repeat"]) for row in rows}
    async with httpx.AsyncClient(
        base_url=url,
        headers=headers,
        timeout=180,
        limits=httpx.Limits(max_connections=10),
    ) as client:

        async def call(method, path, **kwargs):
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response

        async def new(label):
            response = await call(
                "POST", "/api/sessions", json={"title": f"Eval {run_tag} {label}"}
            )
            return response.json()["id"]

        async def turn(session_id, prompt, skill=None):
            started = time.monotonic()
            start_ms = time.time_ns() // 1_000_000
            payload = {
                "messages": [
                    {"role": "user", "parts": [{"type": "text", "text": prompt}]}
                ]
            }
            if skill:
                payload["pinned_skills"] = [skill]
            result = await call(
                "POST", f"/api/sessions/{session_id}/chat", json=payload
            )
            if "[DONE]" not in result.text:
                raise RuntimeError("Fleet stream did not terminate")
            frames = [
                json.loads(line[6:])
                for line in result.text.splitlines()
                if line.startswith("data: {")
            ]
            errors = [
                frame.get("errorText") for frame in frames if frame["type"] == "error"
            ]
            if errors:
                raise RuntimeError(f"Fleet stream failed: {errors!r}")
            messages = (
                await call("GET", f"/api/sessions/{session_id}/messages")
            ).json()
            message = messages[-1]
            answer = "".join(
                part["text"] for part in message["parts"] if part["type"] == "text"
            )
            if message["role"] != "assistant" or not answer:
                raise RuntimeError("Fleet did not persist a final answer")
            return {
                "session_id": session_id,
                "run_id": message["id"],
                "task": prompt,
                "answer": answer,
                "seconds": time.monotonic() - started,
                "start_ms": start_ms,
                "python": sum(
                    frame.get("toolName") == "python_exec"
                    and frame["type"] == "tool-input-start"
                    for frame in frames
                ),
                "active_skills": [
                    item["name"]
                    for frame in frames
                    if frame["type"] == "data-skills"
                    for item in frame["data"]["skills"]
                ],
            }

        def record(case, repeat, row, expected, *, passed=None):
            rows[:] = [
                item
                for item in rows
                if (item["id"], item.get("repeat")) != (case, repeat)
            ]
            rows.append(
                {
                    "id": case,
                    "repeat": repeat,
                    "expected": expected,
                    "passed": answer_passes(row["answer"], expected)
                    if passed is None
                    else passed,
                    **row,
                }
            )
            (output / "workflow-progress.json").write_text(
                json.dumps(rows, indent=2) + "\n"
            )

        async def simple(case, repeat, skill=None):
            row = await turn(await new(f"{repeat} {case}"), tasks[case], skill)
            record(case, repeat, row, cases[case].answer)
            if skill and skill not in row["active_skills"]:
                rows[-1]["passed"] = False

        async def memory(repeat):
            name = f"Vesper{run_tag}{repeat}"
            token = f"VESPER-{run_tag.upper()}-{repeat}"
            source = await new(f"{repeat} memory-source")
            await turn(
                source,
                (
                    f"Please remember this narrowly scoped synthetic preference globally across future sessions: "
                    f"When I ask for a {name} QA handoff, return exactly one line in the form "
                    f"{token} | <owner> | <status> using the requested owner and status. Never apply it to other "
                    "requests. Briefly acknowledge the preference as a memory candidate. Do not create any files "
                    "or skills and do not claim it has been approved."
                ),
            )
            for _ in range(15):
                candidates = (
                    await call("GET", "/api/memories", params={"limit": 100})
                ).json()
                match = [
                    m
                    for m in candidates
                    if token in m["content"] and m["session_id"] is None
                ]
                if len(match) == 1:
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError(
                    "Expected exactly one synthetic global memory candidate"
                )
            candidate = match[0]
            if candidate["approved"]:
                raise RuntimeError(
                    "Memory was automatically approved before the manual-approval control"
                )
            prompt = tasks["memory-pending"].replace("Tamarack", name)
            before = await turn(await new(f"{repeat} memory-pending"), prompt)
            record("memory-pending", repeat, before, "NO SAVED HANDOFF FORMAT")
            approved = (
                await call(
                    "PATCH", f"/api/memories/{candidate['id']}", json={"approved": True}
                )
            ).json()
            after = await turn(await new(f"{repeat} memory-approved"), prompt)
            expected = f"{token} | Avery | READY"
            record(
                "memory-approved",
                repeat,
                after,
                expected,
                passed=answer_passes(after["answer"], expected)
                and approved["approved"]
                and approved["content"] == candidate["content"]
                and "pending approval" not in approved["content"].casefold(),
            )

        async def history(repeat):
            source = await new(f"{repeat} history-source")
            await turn(
                source,
                (
                    "Create a one-time synthetic state test in this session. Use Python to write exactly "
                    "FENNEL-9721-GRAY followed by a newline to /workspace/artifacts/session-state-witness.txt. "
                    "Submit the file as a downloadable artifact. A separate one-time conversation marker is "
                    "WREN-1674-JADE. Leave that marker only in this turn’s conversation history, never put it "
                    "in the file, and do not request or create a Memory or Skill from either marker. Reply with "
                    "WITNESS CREATED."
                ),
            )
            file_path = "artifacts/session-state-witness.txt"
            witness = await call("GET", f"/api/sessions/{source}/files/{file_path}")
            if witness.content != b"FENNEL-9721-GRAY\n":
                raise RuntimeError(
                    "Source artifact bytes differed from the independent fixture"
                )
            row = await turn(source, tasks["history"])
            record("history", repeat, row, cases["history"].answer)
            isolation = await new(f"{repeat} isolation")
            outside = await client.get(f"/api/sessions/{isolation}/files/{file_path}")
            if outside.status_code != 404:
                raise RuntimeError("Fresh session could access the witness artifact")
            row = await turn(isolation, tasks["isolation"])
            record("isolation", repeat, row, cases["isolation"].answer)

        async def large_checks():
            specs = [
                (
                    "csv",
                    "Fleet QA · Million-row ledger",
                    "tr-9b5b524b4291104f02b6e9f789a878ec",
                    "artifacts/qa-ledger.csv",
                    54_596_009,
                    (
                        "1,000,000",
                        "90,904",
                        "8,635,839,203",
                        "948d72fcc541c6f4151a6af382e38578",
                        "e6972f698e8c9fff814a190ceb327089",
                    ),
                    "filtered-counts-by-region",
                ),
                (
                    "archive",
                    "Fleet QA · Harbor archive",
                    "tr-cd3e13142f17415786966177c0e84880",
                    "files/harbor-correspondence.txt",
                    47_995_018,
                    (
                        "AC-47",
                        "Blue Heron",
                        "21:20",
                        "Imani Sato",
                        "412",
                        "VERDANT-OPAL-726",
                        "P052777",
                        "P084777",
                        "P095901",
                        "P099786",
                    ),
                    "harbor-evidence.md",
                ),
            ]
            sessions = (
                await call("GET", "/api/sessions", params={"limit": 100})
            ).json()
            for case, title, trace, path, size, facts, artifact in specs:
                session = next(item for item in sessions if item["title"] == title)
                # Reuse the original live fixtures. The answer and artifacts are revalidated.
                files = (
                    await call("GET", f"/api/sessions/{session['id']}/files")
                ).json()
                original = next(item for item in files if item["path"] == path)
                if original["size_bytes"] != size:
                    raise RuntimeError(f"Unexpected {case} fixture length")
                contents = (
                    await call("GET", f"/api/sessions/{session['id']}/files/{path}")
                ).content
                if hashlib.sha256(contents).hexdigest() != original["sha256"]:
                    raise RuntimeError(f"{case} fixture hash changed")
                task = tracker.get_trace(trace).data.spans[0].inputs["task"]
                row = await turn(session["id"], task)
                files = (
                    await call("GET", f"/api/sessions/{session['id']}/files")
                ).json()
                matches = [
                    file
                    for file in files
                    if artifact in file["path"] and file["size_bytes"] > 0
                ]
                record(
                    case,
                    1,
                    row,
                    list(facts),
                    passed=bool(matches)
                    and all(
                        fact.casefold() in row["answer"].casefold() for fact in facts
                    ),
                )

        async def run_group(label, action):
            try:
                await action()
            except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as error:
                rows.append({"id": label, "passed": None, "error": str(error)})
                (output / "workflow-progress.json").write_text(
                    json.dumps(rows, indent=2) + "\n"
                )

        async def lane(repeat):
            if (
                not {("memory-pending", repeat), ("memory-approved", repeat)}
                <= completed
            ):
                await run_group(f"memory-{repeat}", lambda: memory(repeat))
            if not {("history", repeat), ("isolation", repeat)} <= completed:
                await run_group(f"history-{repeat}", lambda: history(repeat))
            for case in (
                "birch-valid",
                "birch-invalid",
                "quartz-valid",
                "quartz-invalid",
                "writing",
                "missing",
            ):
                if (case, repeat) in completed:
                    continue
                skill = {
                    "birch": "birch-receipt-audit",
                    "quartz": "quartz-qa-audit",
                }.get(case.split("-")[0])
                await run_group(
                    f"{case}-{repeat}",
                    lambda case=case, skill=skill: simple(case, repeat, skill),
                )

        with mlflow.start_run(run_name="fleet-live-workflow-baseline") as run:
            # Capture the original long-file sessions before the short cases fill the sidebar.
            jobs = (
                [run_group("large-checks", large_checks)]
                if large and not {("csv", 1), ("archive", 1)} <= completed
                else []
            )
            await asyncio.gather(*jobs, *(lane(i) for i in range(1, repeats + 1)))
            unresolved = [
                row for row in rows if "start_ms" in row and not row.get("trace_id")
            ]
            candidates = (
                tracker.search_traces(
                    locations=[run.info.experiment_id],
                    filter_string=f"timestamp_ms >= {min(row['start_ms'] for row in unresolved)}",
                    max_results=500,
                )
                if unresolved
                else []
            )
            used = {row["trace_id"] for row in rows if "trace_id" in row}
            for row in sorted(unresolved, key=lambda item: item["start_ms"]):
                matches = []
                for trace in candidates:
                    root = trace.data.spans[0] if trace.data.spans else None
                    if (
                        root is None
                        or not isinstance(root.inputs, dict)
                        or not isinstance(root.outputs, dict)
                        or trace.info.trace_id in used
                        or root.inputs.get("task") != row.get("task")
                        or root.outputs.get("answer") != row.get("answer")
                        or trace.info.request_time < row.get("start_ms", 0)
                    ):
                        continue
                    matches.append(trace)
                if matches:
                    trace = min(matches, key=lambda match: match.info.request_time)
                    used.add(trace.info.trace_id)
                    row["trace_id"] = trace.info.trace_id
                    row["tokens"] = (trace.info.token_usage or {}).get("total_tokens")
                    row["model_calls"] = sum(
                        span.name == "LM.__call__" for span in trace.data.spans
                    )
            path = output / "workflow.json"
            path.write_text(
                json.dumps(
                    {
                        "run_id": run.info.run_id,
                        "experiment": run.info.experiment_id,
                        "rows": rows,
                    },
                    indent=2,
                )
                + "\n"
            )
            mlflow.log_artifact(str(path))
            counts = Counter((row["id"], row["passed"]) for row in rows)
            print(f"{run.info.run_id} {path}\n{counts}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--large", action="store_true")
    parser.add_argument(
        "--resume", type=Path, help="Keep scored results and retry missing executions."
    )
    args = parser.parse_args()
    asyncio.run(
        evaluate(
            args.url,
            args.output_dir,
            args.repeats,
            large=args.large,
            resume=args.resume,
        )
    )


if __name__ == "__main__":
    main()

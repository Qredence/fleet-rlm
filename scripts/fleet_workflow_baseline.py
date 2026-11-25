"""Run repeated synthetic workflows through the live Fleet HTTP endpoints."""

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import mlflow
import yaml
from bundled_skill_cases import cases as bundled_cases
from bundled_skill_cases import verify_chart
from dspy_baseline import (
    CASES,
    answer_passes,
    workspace_trace_root,
    workspace_trace_task,
    workspace_trace_usage,
)
from fleet_rlm.config import Settings
from fleet_rlm.db import create_db, save_or_update_memory
from mlflow import MlflowClient


async def evaluate(
    url: str,
    output: Path,
    repeats: int,
    *,
    large: bool,
    resume: Path | None,
    bundled: bool = False,
    requested_cases: tuple[str, ...] = (),
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
    tasks = {}
    skill_cases = (
        bundled_cases(vision_fallback="chart-png-fallback" in requested_cases)
        if bundled
        else []
    )
    if bundled:
        known_cases = {"skill-creator", *(case.id for case in skill_cases)}
        if unknown := set(requested_cases) - known_cases:
            raise ValueError(f"Unknown bundled cases: {sorted(unknown)}")
    else:
        tasks = {
            case.id: workspace_trace_task(tracker, case.source_trace) for case in CASES
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

        async def turn(session_id, prompt, skill=None, *, pins=()):
            started = time.monotonic()
            start_ms = time.time_ns() // 1_000_000
            payload = {
                "messages": [
                    {"role": "user", "parts": [{"type": "text", "text": prompt}]}
                ]
            }
            if pins or skill:
                payload["pinned_skills"] = list(pins) if pins else [skill]
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
            image_queries = {
                frame["toolCallId"]
                for frame in frames
                if frame["type"] == "tool-input-available"
                and frame.get("toolName") == "subagent"
                and frame.get("input", {}).get("kind") == "llm_query"
                and "data:image/png;base64," in frame["input"].get("task", "")
            }
            return {
                "failed_image_query": any(
                    frame["type"] == "tool-output-error"
                    and frame.get("toolCallId") in image_queries
                    for frame in frames
                ),
                "session_id": session_id,
                "run_id": message["id"],
                "task": prompt,
                "answer": answer,
                "seconds": time.monotonic() - started,
                "start_ms": start_ms,
                "child_runs": sum(
                    frame["type"] == "tool-input-available"
                    and frame.get("toolName") == "subagent"
                    and frame.get("input", {}).get("kind") == "subagent"
                    for frame in frames
                ),
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

        @asynccontextmanager
        async def test_session(label):
            session_id = await new(label)
            try:
                yield session_id
            finally:
                await call("DELETE", f"/api/sessions/{session_id}")

        async def fetch_artifact(session_id, path):
            endpoint = f"/api/sessions/{session_id}/files"
            files = (await call("GET", endpoint)).json()
            entry = next((entry for entry in files if entry["path"] == path), None)
            if entry is None or entry["kind"] != "artifact":
                raise ValueError(f"Artifact is missing: {path}")
            content = (await call("GET", f"{endpoint}/{path}")).content
            if entry["sha256"] != hashlib.sha256(content).hexdigest():
                raise ValueError(f"Artifact checksum mismatch: {path}")
            return content, entry["sha256"]

        async def bundled_case(case, repeat):
            async with test_session(f"{repeat} {case.id}") as session_id:
                if case.filename:
                    await call(
                        "POST",
                        f"/api/sessions/{session_id}/files",
                        files={"file": (case.filename, case.contents)},
                    )
                row = await turn(
                    session_id, case.prompt, pins=() if case.automatic else case.skills
                )
                passed = set(row["active_skills"]) == set(case.skills)
                if isinstance(case.expected, dict):
                    try:
                        passed &= json.loads(row["answer"]) == case.expected
                    except ValueError:
                        passed = False
                try:
                    if case.artifact.endswith((".png", ".svg")):
                        chart, _ = await fetch_artifact(session_id, case.artifact)
                        passed &= verify_chart(case.artifact, chart)
                    if case.id == "chart-png-fallback":
                        passed &= row["failed_image_query"]
                    if case.id == "recursive-artifacts":
                        merged, _ = await fetch_artifact(session_id, case.artifact)
                        north, _ = await fetch_artifact(
                            session_id, "artifacts/north.txt"
                        )
                        south, _ = await fetch_artifact(
                            session_id, "artifacts/south.txt"
                        )
                        passed &= (
                            row["child_runs"] >= 2
                            and json.loads(merged) == case.expected
                            and north.strip() == b"12"
                            and south.strip() == b"17"
                        )
                except (ValueError, SyntaxError, OSError) as error:
                    row["verification_error"] = str(error)
                    passed = False
                record(case.id, repeat, row, case.expected, passed=passed)

        async def creator(repeat):
            name = f"receipt-audit-{run_tag}-{repeat}"
            token = f"APPROVED-{run_tag.upper()}-{repeat}"
            pending = f"PENDING-{run_tag.upper()}-{repeat}"
            global_token = f"GLOBAL-{run_tag.upper()}-{repeat}"
            path = f"artifacts/{name}/SKILL.md"
            global_id = None
            async with test_session(f"{repeat} skill-creator") as source:
                engine, db = create_db(settings)
                try:
                    await save_or_update_memory(
                        db,
                        session_id=UUID(source),
                        approved=True,
                        content=f"For receipt audits with three or more receipts, return {token}.",
                    )
                    await save_or_update_memory(
                        db,
                        session_id=UUID(source),
                        content=f"For receipt audits, also print {pending}.",
                    )
                    global_rule = await save_or_update_memory(
                        db,
                        session_id=None,
                        approved=True,
                        content=f"For receipt audits, also print {global_token}.",
                    )
                    global_id = global_rule.id
                    draft = await turn(
                        source,
                        f"Create a reusable Fleet skill named {name} from the approved Session "
                        f"receipt-audit workflow. Save it at {path} for installation.",
                        "skill-creator",
                    )
                    passed = draft["active_skills"] == ["skill-creator"]
                    try:
                        payload, digest = await fetch_artifact(source, path)
                        text = payload.decode("utf-8")
                        opening, separator, rest = text.partition("\n")
                        frontmatter, closing, body = rest.partition("\n---\n")
                        metadata = yaml.safe_load(frontmatter)
                        draft["skill_body_chars"] = len(body.strip())
                        passed &= (
                            opening.strip() == "---"
                            and bool(separator and closing)
                            and isinstance(metadata, dict)
                            and metadata.get("name") == name
                            and isinstance(metadata.get("description"), str)
                            and 0 < len(metadata["description"].strip()) <= 1024
                            and 0 < len(body.strip()) <= 450
                            and token in body
                            and pending not in text
                            and global_token not in text
                        )
                        installed = (
                            await call(
                                "POST",
                                f"/api/sessions/{source}/skills",
                                json={"path": path, "sha256": digest},
                            )
                        ).json()
                        detail = (await call("GET", f"/api/skills/{name}")).json()
                        passed &= (
                            installed["name"] == name and detail["body"] == body.strip()
                        )
                    except (
                        ValueError,
                        KeyError,
                        yaml.YAMLError,
                        httpx.HTTPError,
                    ) as error:
                        draft["verification_error"] = str(error)
                        passed = False
                    record("skill-creator", repeat, draft, token, passed=passed)
                    if passed:
                        async with test_session(f"{repeat} installed-skill") as fresh:
                            reused = await turn(
                                fresh,
                                "Audit these four receipt IDs: A, B, C, and D. Return only "
                                "the audit code from the selected skill.",
                                name,
                            )
                            record(
                                "skill-creator-reuse",
                                repeat,
                                reused,
                                token,
                                passed=reused["active_skills"] == [name]
                                and reused["answer"].strip() == token,
                            )
                finally:
                    await engine.dispose()
                    if global_id is not None:
                        await call("DELETE", f"/api/memories/{global_id}")

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
                task = workspace_trace_task(tracker, trace)
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
            if bundled:
                for case in skill_cases:
                    if (not requested_cases or case.id in requested_cases) and (
                        case.id,
                        repeat,
                    ) not in completed:
                        await run_group(
                            f"{case.id}-{repeat}",
                            lambda case=case: bundled_case(case, repeat),
                        )
                if (not requested_cases or "skill-creator" in requested_cases) and not {
                    ("skill-creator", repeat),
                    ("skill-creator-reuse", repeat),
                } <= completed:
                    await run_group(f"skill-creator-{repeat}", lambda: creator(repeat))
                return
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

        run_name = (
            "fleet-live-bundled-skills" if bundled else "fleet-live-workflow-baseline"
        )
        with mlflow.start_run(run_name=run_name) as run:
            # Capture the original long-file sessions before the short cases fill the sidebar.
            jobs = (
                [run_group("large-checks", large_checks)]
                if large and not {("csv", 1), ("archive", 1)} <= completed
                else []
            )
            await asyncio.gather(*jobs, *(lane(i) for i in range(1, repeats + 1)))
            # The server exports traces asynchronously, including after the final response.
            for attempt in range(4 if bundled else 1):
                if attempt:
                    await asyncio.sleep(2)
                unresolved = [
                    row for row in rows if "start_ms" in row and not row.get("trace_id")
                ]
                if not unresolved:
                    break
                candidates = tracker.search_traces(
                    locations=[run.info.experiment_id],
                    filter_string=f"timestamp_ms >= {min(row['start_ms'] for row in unresolved)}",
                    max_results=500,
                )
                used = {row["trace_id"] for row in rows if "trace_id" in row}
                for row in sorted(unresolved, key=lambda item: item["start_ms"]):
                    matches = []
                    for trace in candidates:
                        root = workspace_trace_root(trace)
                        if (
                            root is None
                            or not isinstance(root.inputs, dict)
                            or not isinstance(root.outputs, dict)
                            or trace.info.trace_id in used
                            or root.inputs.get("task") != row.get("task")
                            or root.outputs.get("answer") != row.get("answer")
                            or trace.info.tags.get("fleet.run_id") != row["run_id"]
                        ):
                            continue
                        matches.append((trace, root))
                    if matches:
                        trace, root = min(
                            matches, key=lambda match: match[0].info.request_time
                        )
                        used.add(trace.info.trace_id)
                        row["trace_id"] = trace.info.trace_id
                        tokens, row["model_calls"] = workspace_trace_usage(trace, root)
                        row["tokens"] = (tokens or {}).get("total_tokens")
                        if bundled:
                            row["daytona_execution"] = any(
                                span.attributes.get("daytona.sandbox_id")
                                for span in trace.data.spans
                            )
            path = output / "workflow.json"
            path.write_text(
                json.dumps(
                    {
                        "run_id": run.info.run_id,
                        "experiment": run.info.experiment_id,
                        "rows": rows,
                        **(
                            {
                                "image": settings.daytona_image,
                                "model": settings.llm_model,
                                "sub_model": settings.sub_llm_model,
                                "decision_model": settings.decision_model,
                            }
                            if bundled
                            else {}
                        ),
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
    suites = parser.add_mutually_exclusive_group()
    suites.add_argument("--large", action="store_true")
    suites.add_argument(
        "--daytona-migration",
        choices=("v0", "v2"),
        help="Exercise recursive trajectories and the selected sandbox lifecycle on staging.",
    )
    suites.add_argument(
        "--bundled-skills",
        action="store_true",
        help="Run synthetic Skill cases against a disposable Fleet deployment and its database.",
    )
    parser.add_argument(
        "--case", action="append", default=[], help="Select a bundled Skill case."
    )
    parser.add_argument(
        "--resume", type=Path, help="Keep scored results and retry missing executions."
    )
    args = parser.parse_args()
    if args.case and not args.bundled_skills:
        parser.error("--case requires --bundled-skills")
    if args.daytona_migration:
        from daytona_migration_cases import run_daytona_migration

        if args.resume:
            parser.error("--resume is supported by the existing workflow suites")
        passed = asyncio.run(
            run_daytona_migration(
                args.url, args.output_dir, args.repeats, args.daytona_migration
            )
        )
        if not passed:
            raise SystemExit(1)
        return
    asyncio.run(
        evaluate(
            args.url,
            args.output_dir,
            args.repeats,
            large=args.large,
            resume=args.resume,
            bundled=args.bundled_skills,
            requested_cases=tuple(args.case),
        )
    )


if __name__ == "__main__":
    main()

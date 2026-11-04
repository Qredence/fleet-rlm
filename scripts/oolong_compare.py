"""OOLONG-synth: one raw DeepSeek call vs the same task through Fleet's RLM lane.

Standalone and small on purpose. It loads N validation tasks, runs each one twice
through the already-running Fleet service, and prints a paired table.

  arm 1 `direct`  one `dspy.lm15` request from this process; the context is inlined
  arm 2 `flex`/`rlm` a normal Fleet Turn; the context is `files/context.txt`

Both arms use the configured root model. Gold never reaches Fleet: it is used only
here, by the official OOLONG scorer.

Usage (from the repository root, with `uv run fleet web` already listening):

    uv run python scripts/oolong_compare.py --arm rlm
    uv run python scripts/oolong_compare.py --arm flex --context-len 16384 --count 3

Answers, receipts and a summary land in `outputs/oolong-compare/<stamp>/`.
Nothing is re-executed on a rerun with the same `--stamp`.
"""

from __future__ import annotations

import argparse
import ast
import json
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import dateutil.parser
import httpx
import mlflow
from anyio.from_thread import start_blocking_portal
from dspy.lm15 import Config, Message, Request

from fleet_rlm.config import get_settings

DATASET = "oolongbench/oolong-synth"
REVISION = "81d2f8d9349f6ff329d695f9be494051e9f05cb9"
COMPARISON_PHRASES = ("more common", "less common", "same frequency")


# --------------------------------------------------------------------------
# Official OOLONG-synth scoring (abertsch72/oolong, src/eval/eval_helpers.py)
# --------------------------------------------------------------------------
def _parse_answer(answer: str) -> str:
    if ":" not in answer:
        return answer if len(answer) < 20 else answer.split()[-1]
    candidate = answer.split(":")[-1].strip().replace("*", "").replace("[", "").replace("]", "")
    if len(candidate) >= 20:
        return candidate
    for phrase in COMPARISON_PHRASES:
        if phrase in candidate.lower():
            return phrase
    return candidate


def official_score(gold: dict, output: str) -> float:
    raw, answer_type = gold["answer"], gold["answer_type"]
    expected = datetime.strptime(raw, "[datetime.date(%Y, %m, %d)]") if "datetime" in raw else ast.literal_eval(raw)[0]
    parsed = _parse_answer(output)
    if str(parsed) == str(expected):
        return 1.0
    if str(parsed) in COMPARISON_PHRASES and str(parsed) in str(expected):
        return 1.0
    if answer_type == "ANSWER_TYPE.NUMERIC":
        try:
            return float(0.75 ** abs(int(expected) - int(parsed)))
        except Exception:
            return 0.0
    if answer_type == "ANSWER_TYPE.DATE":
        try:
            return 1.0 if dateutil.parser.parse(parsed) == expected else 0.0
        except Exception:
            return 0.0
    return 0.0


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------
def load_tasks(count: int, context_len: int) -> list[dict]:
    from datasets import load_dataset

    wanted = {"ANSWER_TYPE.LABEL", "ANSWER_TYPE.COMPARISON", "ANSWER_TYPE.USER"}
    rows, windows = [], set()
    stream = load_dataset(DATASET, split="validation", revision=REVISION, streaming=True)
    for row in stream:
        if row["context_len"] != context_len or row["answer_type"] not in wanted:
            continue
        if row["context_window_id"] in windows:  # one question per window: no shared cache
            continue
        windows.add(row["context_window_id"])
        rows.append(row)
        if len(rows) == count:
            break
    if len(rows) < count:
        raise SystemExit(f"Only {len(rows)} eligible rows at context_len={context_len}")
    return rows


def task_text(row: dict) -> str:
    """The prompt both arms receive; only the delivery of the context differs."""
    return (
        "The complete unlabelled context is in files/context.txt. "
        "Read that file to answer the question below.\n\n" + row["question"]
    )


# --------------------------------------------------------------------------
# Arm "raw": one plain LM call, no Fleet
# --------------------------------------------------------------------------
def run_raw(lm, row: dict, evidence: Path) -> dict:
    context = row["context_window_text"]
    # The dataset card specifies `context + "\n" + question`: OOLONG questions say "in the above
    # data", so the context must precede them.
    prompt = f"{context}\n{row['question']}"
    request = Request(
        model=lm.model,
        messages=(Message.user(prompt),),
        config=Config(max_tokens=lm.kwargs.get("max_tokens"), temperature=lm.kwargs.get("temperature")),
    )

    async def complete():
        return await lm.acall(request)

    started = time.monotonic()
    with mlflow.start_span(name="oolong_raw", span_type="CHAIN") as span:
        span.set_inputs({"task": prompt[:2000], "inlined_chars": len(prompt)})
        with start_blocking_portal() as portal:
            response = portal.call(complete)
        span.set_outputs({"answer": response.text[:2000]})
        trace_id = span.trace_id
    mlflow.flush_trace_async_logging()
    usage = {key: value for key, value in asdict(response.usage).items() if value is not None}
    evidence.write_text(json.dumps({"prompt_chars": len(prompt), "answer": response.text}, indent=2))
    return {
        "answer": response.text or "",
        "trace_id": trace_id,
        "latency_seconds": round(time.monotonic() - started, 1),
        "finish_reason": response.finish_reason,
        "model_calls": 1,
        "usage": usage,
    }


# --------------------------------------------------------------------------
# Fleet arms: one Turn per task in the requested lane
# --------------------------------------------------------------------------
def run_fleet(client: httpx.Client, row: dict, arm: str, evidence: Path) -> dict:
    """Run one Fleet Turn in `arm` mode over the same attached context as every other lane."""
    session = f"oolong-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    client.put(f"/sessions/{session}/mode", json={"mode": arm}).raise_for_status()
    client.post(
        f"/sessions/{session}/attach",
        files={"file": ("context.txt", row["context_window_text"].encode(), "text/plain")},
    ).raise_for_status()
    try:
        response = client.post(f"/sessions/{session}/turn", json={"task": task_text(row)}, timeout=None)
        response.raise_for_status()
        evidence.write_text(response.text)
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        calls = [e for e in events if e.get("event") == "lm_end"]
        final = next(
            (e for e in reversed(events) if e.get("event") in {"turn_complete", "error", "turn_cancelled"}), {}
        )
        payload = final.get("payload", {})
        usage = {"input_tokens": 0, "output_tokens": 0}
        for call in calls:
            usage["input_tokens"] += (call.get("usage") or {}).get("prompt_tokens") or 0
            usage["output_tokens"] += (call.get("usage") or {}).get("completion_tokens") or 0
        return {
            "answer": payload.get("answer", ""),
            "route": payload.get("route"),
            "trace_id": payload.get("trace_id"),
            "latency_seconds": round(time.monotonic() - started, 1),
            "model_calls": len(calls),
            "usage": usage,
            "status": final.get("event"),
            # Direct mode inlines the workspace; keep the measurement so a clipped arm is visible.
            "direct_context_chars": payload.get("direct_context_chars"),
            "direct_context_truncated": payload.get("direct_context_truncated"),
        }
    finally:
        client.delete(f"/sessions/{session}", timeout=120)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arm",
        choices=("raw", "direct", "flex", "rlm"),
        default="rlm",
        help="Fleet lane to measure, or 'raw' for one plain LM call with no Fleet Turn",
    )
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--context-len", type=int, default=16384)
    parser.add_argument("--stamp", default=time.strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()

    settings = get_settings("local")
    base_url = f"http://{settings.host}:{settings.port}"
    out = Path("outputs/oolong-compare") / args.stamp
    out.mkdir(parents=True, exist_ok=False)
    root_lm = settings.build_lms()[0]
    rows = load_tasks(args.count, args.context_len)
    print(f"{len(rows)} tasks at context_len={args.context_len}; service {base_url}; arm={args.arm}")
    print(f"model: {root_lm.model}\n")

    results = []
    with httpx.Client(base_url=base_url, timeout=httpx.Timeout(1200, connect=10)) as client:
        for index, row in enumerate(rows, 1):
            record = {"id": row["id"], "task_group": row["task_group"], "answer_type": row["answer_type"]}
            for arm in (args.arm,):
                case_dir = out / str(row["id"])
                case_dir.mkdir(exist_ok=True)
                try:
                    outcome = (
                        run_raw(root_lm, row, case_dir / "raw.json")
                        if arm == "raw"
                        else run_fleet(client, row, arm, case_dir / f"{arm}.sse")
                    )
                except Exception as exc:
                    outcome = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
                outcome["score"] = (
                    official_score(
                        {"answer": row["answer"], "answer_type": row["answer_type"]}, outcome.get("answer", "")
                    )
                    if outcome.get("answer")
                    else 0.0
                )
                record[arm] = outcome
                print(
                    f"[{index}/{len(rows)}] {row['id']} {arm:<7} "
                    f"score={outcome['score']:.2f} route={outcome.get('route')} calls={outcome.get('model_calls')} "
                    f"{outcome.get('latency_seconds', 0):.0f}s"
                    + (f" error={outcome['error']}" if outcome.get("error") else "")
                )
            record["gold"] = row["answer"]
            results.append(record)
            (out / "results.json").write_text(json.dumps(results, indent=2, default=str))

    scores = [r[args.arm]["score"] for r in results]
    arm_rows = [r[args.arm] for r in results]
    errors = [r["id"] for r in results if "error" in r[args.arm]]
    summary = {
        "stamp": args.stamp,
        "arm": args.arm,
        "model": root_lm.model,
        "context_len": args.context_len,
        "tasks": len(results),
        "tasks_with_errors": errors,
        "mean_score": sum(scores) / len(scores),
        "exact_correct": sum(score == 1 for score in scores),
        "model_calls": sum(row.get("model_calls") or 0 for row in arm_rows),
        "seconds": round(sum(row.get("latency_seconds") or 0 for row in arm_rows), 1),
        "input_tokens": sum((row.get("usage") or {}).get("input_tokens") or 0 for row in arm_rows),
        "output_tokens": sum((row.get("usage") or {}).get("output_tokens") or 0 for row in arm_rows),
        # Only the direct lane inlines the workspace; report it rather than inferring it.
        "direct_context_chars": sum(row.get("direct_context_chars") or 0 for row in arm_rows),
        "truncated_cases": [r["id"] for r in results if r[args.arm].get("direct_context_truncated")],
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()

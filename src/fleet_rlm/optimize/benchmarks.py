"""RLM-oriented eval cases: synthetic long-context generators and public benchmark adapters.

LongCoT cases are labeled ``review_status="benchmark"`` and are held out entirely. Oolong cases are
split by context window (see ``window_status``): two of three windows are ``approved`` and may train,
and the rest stay ``benchmark``. GEPA must never train or validate on a ``benchmark`` case.
"""

from __future__ import annotations

import ast
import hashlib
import json
import random
import time
from collections import Counter
from typing import Any

import httpx

from fleet_rlm.optimize.evaluation import EvalCase

_HF = "https://datasets-server.huggingface.co"
OOLONG = "oolongbench/oolong-synth"
LONGCOT = "LongHorizonReasoning/longcot"
# LongCoT hides gold answers for `logic`; the rest ship JSON payloads.
LONGCOT_DOMAINS = ("cs", "chemistry", "chess", "math")
_ACTIONS = ("login", "logout", "view", "purchase", "refund", "failed_login")
_REGIONS = ("north", "south", "east", "west")


def _log(rng: random.Random, target_chars: int) -> tuple[str, list[dict[str, Any]]]:
    lines, events, size = [], [], 0
    while size < target_chars:
        event = {
            "user": f"u{rng.randrange(1, 400):03d}",
            "action": rng.choices(_ACTIONS, weights=(30, 25, 25, 10, 2, 8))[0],
            "region": rng.choice(_REGIONS),
            "amount": rng.randrange(5, 500),
        }
        line = (
            f"2026-03-{rng.randrange(1, 29):02d}T{rng.randrange(24):02d}:{rng.randrange(60):02d}:"
            f"{rng.randrange(60):02d}Z user={event['user']} action={event['action']} "
            f"region={event['region']} amount={event['amount']}"
        )
        lines.append(line)
        events.append(event)
        size += len(line) + 1
    return "\n".join(lines) + "\n", events


def long_context_cases(
    seed: int = 11, tiers: tuple[int, ...] = (20_000, 50_000, 100_000, 200_000), per_tier: int = 4
) -> list[EvalCase]:
    """Whole-file aggregation tasks over logs far larger than a prompt budget, by size tier."""
    rng = random.Random(seed)
    cases = []
    for tier in tiers:
        for index in range(per_tier):
            text, events = _log(rng, tier)
            kind = index % 4
            if kind == 0:
                task, values, answer_type = (
                    "How many refund actions are in files/events.log?",
                    (str(sum(e["action"] == "refund" for e in events)),),
                    "scalar",
                )
            elif kind == 1:
                spend: Counter[str] = Counter()
                for e in events:
                    if e["action"] == "purchase":
                        spend[e["user"]] += e["amount"]
                top = max(sorted(spend), key=lambda user: spend[user])
                task, values, answer_type = (
                    "Which user has the highest total purchase amount in files/events.log, and what is that total?",
                    (top, str(spend[top])),
                    "scalar",
                )
            elif kind == 2:
                fails = Counter(e["user"] for e in events if e["action"] == "failed_login")
                if not any(count >= 2 for count in fails.values()):
                    # Guarantee a non-empty gold list so an empty answer cannot pass by accident.
                    user = rng.choice(sorted({e["user"] for e in events}))
                    extra = [
                        f"2026-03-15T00:00:0{n}Z user={user} action=failed_login region=north amount=0"
                        for n in range(3)
                    ]
                    text += "\n".join(extra) + "\n"
                    fails[user] += 3
                flagged = sorted(user for user, count in fails.items() if count >= 2)
                task, values, answer_type = (
                    "List every user with at least 2 failed_login actions in files/events.log.",
                    tuple(flagged),
                    "list",
                )
            else:
                code = f"KX-{rng.randrange(1000, 9999)}"
                lines = text.splitlines()
                lines.insert(rng.randrange(len(lines)), f"ALERT code={code} severity=critical")
                text = "\n".join(lines) + "\n"
                task, values, answer_type = (
                    "What is the code in the single ALERT line of files/events.log?",
                    (code,),
                    "scalar",
                )
            case_id = f"longctx-{tier // 1000}k-{index:02d}"
            cases.append(
                EvalCase(
                    case_id=case_id,
                    task=task,
                    expected_values=values,
                    category=f"long_context_{('count', 'topk', 'list', 'needle')[kind]}",
                    source_group=case_id,
                    files={"files/events.log": text},
                    answer_type=answer_type,
                    context_chars=len(text),
                )
            )
    return cases


def _get(client: httpx.Client, path: str, **params: Any) -> list[dict[str, Any]]:
    """GET a datasets-server page, backing off on 429/5xx (anonymous access is rate limited)."""
    for attempt in range(6):
        response = client.get(f"{_HF}/{path}", params=params, timeout=120)
        if response.status_code == 429 or response.status_code >= 500:
            time.sleep(min(float(response.headers.get("retry-after", 2 * (attempt + 1))), 30))
            continue
        response.raise_for_status()
        return response.json().get("rows", [])
    response.raise_for_status()
    return []


def window_status(window_id: str) -> str:
    """Two of every three Oolong context windows train (``approved``); the rest stay a held-out benchmark.

    A stable hash of the window id keeps every question from one window on the same side, and reruns never reshuffle.
    """
    bucket = int(hashlib.sha256(str(window_id).encode()).hexdigest(), 16) % 3
    return "approved" if bucket < 2 else "benchmark"


def oolong_cases(
    client: httpx.Client,
    *,
    limit: int = 24,
    max_tokens: int = 32_768,
    page: int = 5,
    stride: int = 20,
    max_pages: int = 70,
    per_window: int = 2,
    pause: float = 0.5,
) -> list[EvalCase]:
    """Oolong-synth validation items up to ``max_tokens`` of context (~4 characters per token).

    Pages through ``/rows`` and filters locally: the ``/filter`` index is built lazily and can
    answer 500 for a long time, and the split mixes sizes up to millions of tokens. Pages are
    strided across the split and each context window contributes at most ``per_window`` questions,
    so a sample spans context sizes and tasks instead of repeating the first window.
    """
    cases: dict[str, EvalCase] = {}
    windows: Counter[int] = Counter()
    for number in range(max_pages):
        time.sleep(pause if number else 0)
        rows = _get(
            client, "rows", dataset=OOLONG, config="default", split="validation", offset=number * stride, length=page
        )
        if not rows:
            break
        for item in rows:
            row = item["row"]
            if item.get("truncated_cells") or int(row["context_len"]) > max_tokens:
                continue
            if windows[row["context_window_id"]] >= per_window:
                continue
            windows[row["context_window_id"]] += 1
            try:
                parsed = ast.literal_eval(row["answer"])
            except (ValueError, SyntaxError):
                parsed = row["answer"]
            values = tuple(str(v) for v in parsed) if isinstance(parsed, (list, tuple)) else (str(parsed),)
            case_id = f"oolong-{row['id']}"
            cases[case_id] = EvalCase(
                case_id=case_id,
                task=f"The data is in files/context.txt. {row['question']}",
                expected_values=values,
                category=f"oolong_{row['task_group']}",
                source_group=f"oolong-window-{row['context_window_id']}",
                files={"files/context.txt": row["context_window_text"]},
                review_status=window_status(row["context_window_id"]),
                answer_type="list" if len(values) > 1 else "scalar",
                benchmark="oolong-synth",
            )
            if len(cases) == limit:
                return list(cases.values())
    return list(cases.values())


def _payload_values(payload: Any) -> tuple[str, ...] | None:
    """Flat string values for scalar/list payloads; None for structured payloads we cannot match."""
    if isinstance(payload, (str, int, float)):
        return (str(payload),)
    if isinstance(payload, list) and payload and all(isinstance(v, (str, int, float)) for v in payload):
        return tuple(str(v) for v in payload)
    return None


def longcot_cases(
    client: httpx.Client, *, difficulty: str = "medium", per_domain: int = 4, domains: tuple[str, ...] = LONGCOT_DOMAINS
) -> list[EvalCase]:
    """LongCoT items whose gold answer is a scalar or flat list (structured payloads need the official verifier)."""
    cases = []
    for domain in domains:
        taken = 0
        for item in _get(client, "rows", dataset=LONGCOT, config=domain, split=difficulty, offset=0, length=100):
            row = item["row"]
            try:
                values = _payload_values(json.loads(row["answer"]))
            except (TypeError, json.JSONDecodeError):
                continue
            if values is None:
                continue
            cases.append(
                EvalCase(
                    case_id=f"longcot-{row['question_id']}",
                    task=row["prompt"],
                    expected_values=values,
                    category=f"longcot_{domain}_{difficulty}",
                    source_group=f"longcot-{row['template']}",
                    review_status="benchmark",
                    answer_type="list" if len(values) > 1 else "scalar",
                    benchmark="longcot",
                )
            )
            taken += 1
            if taken == per_domain:
                break
    return cases

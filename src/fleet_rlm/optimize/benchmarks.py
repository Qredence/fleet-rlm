"""Deterministic synthetic long-context evaluation tasks for Fleet."""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from fleet_rlm.optimize.evaluation import EvalCase

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

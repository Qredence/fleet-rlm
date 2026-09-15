"""Official Oolong scoring helpers vendored from abertsch72/oolong.

Source of truth: https://github.com/abertsch72/oolong
File: src/eval/eval_helpers.py @ 5c8113ee360957cff010d27310e844630216b21d

Fleet adapters must call these helpers with the model answer string. Do not use
historical Fleet `_synth_score` or v0.6.x Oolong scripts.
"""

from __future__ import annotations

import ast
import re
from datetime import datetime

import dateutil.parser

OOLONG_REPO_URL = "https://github.com/abertsch72/oolong"
OOLONG_EVAL_HELPERS_SOURCE = "https://github.com/abertsch72/oolong/blob/main/src/eval/eval_helpers.py"
OOLONG_EVAL_HELPERS_REVISION = "5c8113ee360957cff010d27310e844630216b21d"


def synth_attempt_answer_parse(answer: str) -> tuple[str, str]:
    """Parse a synthetic-split model answer using the official heuristic."""
    parse_confidence = "low"
    if ":" not in answer:
        if len(answer) < 20:
            return answer, parse_confidence
        return answer.split()[-1], parse_confidence
    candidate_answer = answer.split(":")[-1].strip()
    candidate_answer = candidate_answer.replace("*", "")
    candidate_answer = candidate_answer.replace("[", "")
    candidate_answer = candidate_answer.replace("]", "")
    parse_confidence = "med"
    if "User:" in answer or "Answer:" in answer or "Date:" in answer or "Label" in answer:
        parse_confidence = "high"
    if len(candidate_answer) < 20:
        parse_confidence = "vhigh"
    elif "more common" in candidate_answer:
        candidate_answer = "more common"
    elif "less common" in candidate_answer:
        candidate_answer = "less common"
    elif "same frequency" in candidate_answer:
        candidate_answer = "same frequency"
    return candidate_answer, parse_confidence


def synth_process_response(datapoint: dict[str, object], output: str, model: str) -> dict[str, object]:
    """Score one synthetic-split prediction with the official rubric."""
    score = 0
    answer_field = str(datapoint["answer"])
    gold = (
        ast.literal_eval(answer_field)[0]
        if "datetime" not in answer_field
        else datetime.strptime(answer_field, "[datetime.date(%Y, %m, %d)]")
    )

    trimmed_output, parse_confidence = synth_attempt_answer_parse(output)
    if str(trimmed_output) == str(gold):
        score = 1
    elif str(trimmed_output) in {"more common", "less common", "same frequency"}:
        if str(trimmed_output) in str(gold):
            score = 1
    elif datapoint["answer_type"] == "ANSWER_TYPE.NUMERIC":
        try:
            trimmed_output = int(trimmed_output)
            gold = int(gold)
            score = 0.75 ** (abs(gold - trimmed_output))
        except Exception:
            parse_confidence = "low"
    elif datapoint["answer_type"] == "ANSWER_TYPE.DATE":
        try:
            trimmed_output = dateutil.parser.parse(trimmed_output)
            score = trimmed_output == gold
        except Exception:
            parse_confidence = "low"

    return {
        "id": datapoint["id"],
        "context_window_id": datapoint["context_window_id"],
        "dataset": datapoint["dataset"],
        "model": model,
        "attempted_parse": str(trimmed_output),
        "parse_confidence": parse_confidence,
        "full_answer": output,
        "score": score,
        "answer": str(gold),
    }


def dnd_parse_answer(answer: str) -> int | str | list[str]:
    """Parse the real-split gold or model answer."""
    try:
        return int(answer)
    except ValueError:
        pass
    if "," in answer:
        return [item.strip() for item in answer.split(",") if item.strip()]
    return answer


def dnd_parse_response(answer: str) -> tuple[int | str | list[str], str]:
    """Extract a real-split answer from model output."""
    match = re.search(r"\\boxed\{\\text\{([^}]*)\}\}", answer) or re.search(
        r"\\boxed[\{]+([^}]*)[\}]+",
        answer,
    )
    if match:
        answer = match.group(1)
    else:
        return answer, "low"
    return dnd_parse_answer(answer), "high"


def dnd_process_response(datapoint: dict[str, object], output: str, model: str) -> dict[str, object]:
    """Score one real-split prediction with the official rubric."""
    gold = dnd_parse_answer(str(datapoint["answer"]))
    trimmed_output, parse_confidence = dnd_parse_response(output)
    score = 0.0
    if isinstance(gold, int) and isinstance(trimmed_output, int):
        score = 0.75 ** abs(gold - trimmed_output)
    elif isinstance(gold, str) and isinstance(trimmed_output, str):
        score = float(gold.strip().lower() == trimmed_output.strip().lower())
    elif isinstance(gold, list) and isinstance(trimmed_output, list):
        overlap = set(gold) & set(trimmed_output)
        score = len(overlap) / len(gold) if gold else 0.0
    return {
        "id": datapoint["id"],
        "context_window_id": datapoint["context_window_id"],
        "model": model,
        "attempted_parse": trimmed_output,
        "parse_confidence": parse_confidence,
        "full_answer": output,
        "score": score,
        "answer": gold,
    }


__all__ = [
    "OOLONG_EVAL_HELPERS_REVISION",
    "OOLONG_EVAL_HELPERS_SOURCE",
    "OOLONG_REPO_URL",
    "dnd_process_response",
    "synth_process_response",
]

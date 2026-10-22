"""OOLONG-Synth scoring, vendored from Amanda Bertsch's MIT-licensed evaluator.

Upstream: abertsch72/oolong, commit 0bb7eabe839218fee7fe8d007f41cfc2fd3ae24c
Source: src/eval/eval_helpers.py (Git blob 5c8113ee360957cff010d27310e844630216b21d).
Only the two synth functions are retained; unrelated inference/DnD imports are
omitted. Parser behavior, including its quirks, is intentionally not improved.

MIT License
Copyright (c) 2025 Amanda Bertsch

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import ast
from datetime import datetime

import dateutil.parser

UPSTREAM_COMMIT = "0bb7eabe839218fee7fe8d007f41cfc2fd3ae24c"
UPSTREAM_BLOB = "5c8113ee360957cff010d27310e844630216b21d"
FUNCTIONS = ("synth_attempt_answer_parse", "synth_process_response")


def synth_attempt_answer_parse(answer):
    parse_confidence = "low"
    if ":" not in answer:
        if len(answer) < 20:
            return answer, parse_confidence
        else:
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


def synth_process_response(datapoint, output, model):
    score = 0
    gold = (
        ast.literal_eval(datapoint["answer"])[0]
        if "datetime" not in datapoint["answer"]
        else datetime.strptime(datapoint["answer"], "[datetime.date(%Y, %m, %d)]")
    )
    trimmed_output, parse_confidence = synth_attempt_answer_parse(output)
    if str(trimmed_output) == str(gold):
        score = 1
    elif str(trimmed_output) in ["more common", "less common", "same frequency"]:
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
    this_output = {
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
    return this_output

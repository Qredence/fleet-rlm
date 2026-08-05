"""Deterministic corpus-chain benchmark fixtures and report validation."""

from __future__ import annotations

import ast
import json
import random
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

CORPUS_WORKLOAD_ID = "corpus-chain-v1"
CORPUS_SIZE = 500_000
CORPUS_WRITE_COUNT = 132
CORPUS_PAYLOAD_VALUE = 65535
CORPUS_LOOKUP_FIELD = "N333/SYNC/S777"
CORPUS_WRITE_KEY = "WRITE/S042"
CORPUS_TERMINAL_CLAIM = "S555"
CORPUS_COMPUTED_ANSWER = "S444"
CORPUS_SEEDS = (0, 1)


@dataclass(frozen=True, slots=True)
class CorpusCase:
    """One reproducible corpus challenge and its externally verifiable answer."""

    seed: int
    size: int
    path: tuple[int, ...]
    payload_indices: tuple[int, ...]
    write_indices: tuple[int, ...]

    @property
    def start_index(self) -> int:
        return self.path[0]

    @property
    def lookup_index(self) -> int:
        return self.path[3]

    @property
    def terminal_index(self) -> int:
        return self.path[-1]

    @property
    def expected_report(self) -> dict[str, object]:
        return {
            "path": list(self.path),
            "lookup_matches": [self.lookup_index],
            "payload_count": len(self.payload_indices),
            "payload_last_index": self.payload_indices[-1],
            "computed_answer": CORPUS_COMPUTED_ANSWER,
            "terminal_claim": CORPUS_TERMINAL_CLAIM,
            "terminal_discrepancy": True,
        }


@dataclass(frozen=True, slots=True)
class CorpusValidation:
    """Bounded result of validating one submitted corpus report."""

    passed: bool
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "errors": list(self.errors)}


@dataclass(frozen=True, slots=True)
class CorpusEvidenceValidation:
    """Bounded execution evidence required for one corpus report."""

    passed: bool
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"passed": self.passed, "errors": list(self.errors)}


def _choose_indices(rng: random.Random, *, size: int, count: int, excluded: set[int]) -> tuple[int, ...]:
    candidates = [index for index in range(1, size - 1) if index not in excluded]
    if len(candidates) < count:
        raise ValueError("corpus fixture does not have enough free indices")
    return tuple(sorted(rng.sample(candidates, count)))


def make_corpus_case(seed: int, *, size: int = CORPUS_SIZE) -> CorpusCase:
    """Build a deterministic corpus case without materializing all corpus lines."""
    if size < 256:
        raise ValueError("corpus fixture size must be at least 256")

    rng = random.Random(seed)
    if seed == 0 and size == CORPUS_SIZE:
        path = (40_000, 187_653, 287_653, 350_500, 499_999)
    else:
        lower = max(1, min(10_000, size // 10))
        upper = max(lower + 4, size - 200)
        path = (*tuple(sorted(rng.sample(range(lower, upper), 4))), size - 1)

    path_set = set(path)
    if seed == 0 and size == CORPUS_SIZE:
        payload_indices = (10_000, 20_000, 30_000, 450_000, size - 1)
    else:
        payload_indices = (*_choose_indices(rng, size=size, count=4, excluded=path_set), size - 1)

    payload_set = set(payload_indices)
    write_indices = tuple(
        sorted(
            {
                *range(size - (CORPUS_WRITE_COUNT - 1), size - 1),
                path[3],
                size - 1,
            }
        )
    )
    if len(write_indices) != CORPUS_WRITE_COUNT or path_set & (set(write_indices) - {path[3], size - 1}):
        write_indices = tuple(
            sorted(
                {
                    *_choose_indices(
                        rng,
                        size=size,
                        count=CORPUS_WRITE_COUNT - 2,
                        excluded=path_set | payload_set,
                    ),
                    path[3],
                    size - 1,
                }
            )
        )

    return CorpusCase(
        seed=seed,
        size=size,
        path=path,
        payload_indices=tuple(payload_indices),
        write_indices=write_indices,
    )


def corpus_line(case: CorpusCase, index: int) -> str:
    """Render one deterministic corpus line for fixture tests or a local probe."""
    if not 0 <= index < case.size:
        raise IndexError(index)

    path = case.path
    if index == path[0]:
        key = "HOP-1"
        tag = f"next needle is at entry {path[1]:,}"
    elif index == path[1]:
        key = "HOP-2"
        tag = f"next needle is at entry {path[2]:,}"
    elif index == path[2]:
        key = "HOP-3"
        tag = f"query field {CORPUS_LOOKUP_FIELD}; follow its unique match"
    elif index == path[3]:
        key = CORPUS_WRITE_KEY
        tag = f"next needle is at entry {case.terminal_index:,}"
    elif index == case.terminal_index:
        key = CORPUS_WRITE_KEY
        tag = f"TERMINAL payload={CORPUS_PAYLOAD_VALUE} the answer key is {CORPUS_TERMINAL_CLAIM}"
    elif index in case.write_indices:
        key = CORPUS_WRITE_KEY
        tag = "write record"
    else:
        key = f"FILL/{index}"
        tag = "NOOP"

    field = f" field={CORPUS_LOOKUP_FIELD}" if index == case.lookup_index else ""
    payload = (
        f" payload={CORPUS_PAYLOAD_VALUE}" if index in case.payload_indices and index != case.terminal_index else ""
    )
    return f'[E{index}] key={key}{field} tag="{tag}"{payload}'


def iter_corpus_lines(case: CorpusCase) -> Iterator[str]:
    """Stream the complete corpus without holding 500,000 lines in memory."""
    for index in range(case.size):
        yield corpus_line(case, index)


def write_corpus(case: CorpusCase, destination: Path) -> None:
    """Write a deterministic corpus fixture for local or sandbox probes."""
    with destination.open("w", encoding="utf-8") as handle:
        for line in iter_corpus_lines(case):
            handle.write(line)
            handle.write("\n")


def corpus_workload(case: CorpusCase) -> str:
    """Return the RLM prompt for one host-provided hidden corpus attachment."""
    return f"""Build and analyze a deterministic {case.size:,}-entry corpus.

The authoritative corpus is supplied as a hidden Attachment. Do not write,
reconstruct, or guess the corpus from this request. Inspect the authorized
Attachment metadata in `attachments`, then call
`read_attachment(attachment_id=...)` and use its returned body. Check the
`encoding` field; use `content` for UTF-8 or decode `content_base64` for
base64, then decompress gzip when the filename ends in `.gz`. Parse the
newline-delimited records with the Python standard library.

Follow the actual linked records in the attachment, resolve the unique lookup
field, count and locate the payload records, and verify the write-record count
and last index. Every reported value must be computed from the attachment
contents. Reuse the loaded corpus and resolved variables between actions, and
do not submit a hardcoded fixture answer.

End with exactly one typed SUBMIT using `SUBMIT(answer=json.dumps(report))`.
`report` must be a Python dict with exactly these fields: `path` (five integer
indices), `lookup_matches` (the unique matching index), `payload_count`,
`payload_last_index`, `computed_answer`, `terminal_claim`, and
`terminal_discrepancy` (a JSON boolean)."""


def _strict_value_matches(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _strict_value_matches(item, wanted) for item, wanted in zip(actual, expected, strict=True)
        )
    if isinstance(actual, Mapping) and isinstance(expected, Mapping):
        return set(actual) == set(expected) and all(
            _strict_value_matches(actual[key], expected[key]) for key in expected
        )
    return actual == expected


def validate_corpus_report(answer: str, case: CorpusCase) -> CorpusValidation:
    """Validate the structured answer against data-derived expectations."""
    candidate = answer.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        candidate = "\n".join(lines[1:-1]).strip() if len(lines) >= 3 else ""
    try:
        parsed = json.loads(candidate)
    except (TypeError, json.JSONDecodeError):
        return CorpusValidation(False, ("answer is not one JSON object",))
    if not isinstance(parsed, Mapping):
        return CorpusValidation(False, ("answer JSON must be an object",))

    expected = case.expected_report
    errors = list(
        f"{key} does not match the fixture"
        for key, value in expected.items()
        if not _strict_value_matches(parsed.get(key), value)
    )
    extra_keys = sorted(str(key) for key in parsed if key not in expected)
    if extra_keys:
        errors.append("answer contains unexpected fields: " + ", ".join(str(key) for key in extra_keys))
    return CorpusValidation(not errors, tuple(errors))


def _contains_call(code: str, function_name: str, *, attribute: str | None = None) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if attribute is None and isinstance(function, ast.Name) and function.id == function_name:
            return True
        if (
            attribute is not None
            and isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == function_name
            and function.attr == attribute
        ):
            return True
    return False


def validate_corpus_evidence(
    trajectory: Mapping[str, object],
    *,
    attachment_accessed: bool,
) -> CorpusEvidenceValidation:
    """Require bounded, observable attachment-backed execution evidence."""
    raw_codes = trajectory.get("codes")
    raw_outputs = trajectory.get("outputs")
    codes = tuple(item for item in raw_codes if isinstance(item, str)) if isinstance(raw_codes, list) else ()
    outputs = tuple(item for item in raw_outputs if isinstance(item, str)) if isinstance(raw_outputs, list) else ()
    errors: list[str] = []
    if attachment_accessed is not True:
        errors.append("the submitted report did not access the host Attachment")
    if not any(_contains_call(code, "read_attachment") for code in codes):
        errors.append("trajectory has no executable read_attachment call")
    if not any(_contains_call(code, "json", attribute="dumps") for code in codes):
        errors.append("trajectory has no executable json.dumps serialization")
    if not any(item.strip() for item in outputs):
        errors.append("trajectory has no bounded RLM output evidence")
    elif "FINAL submitted" not in outputs:
        errors.append("trajectory has no typed SUBMIT output evidence")
    return CorpusEvidenceValidation(not errors, tuple(errors))

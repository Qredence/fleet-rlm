"""P41 documentation freeze (VAL-CROSS-022).

The P41 freeze is behavior-based. This lane pins the final-state
documentation rules:

- the behavior-freeze document exists, lists one public owner per frozen
  behavior, and never freezes private Python structure;
- README, setup, testing, and codebase-map docs carry no Git-pin or
  Git-install guidance for DSPy/GEPA, no removed-frontend references, and no
  claim that Workspace Memory appends coordinate across Sandboxes;
- the cross-Sandbox Workspace Memory append limitation stays explicitly
  documented as unsupported;
- behavior goldens remain byte-identical to the sealed baseline digest
  manifest (changed golden bytes require a recorded human decision).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_FREEZE_DOC = "docs/reference/behavior-freeze.md"

# README, setup, testing, and codebase-map surfaces (plus their direct
# neighbors). CHANGELOG.md is deliberately excluded: it is a historical
# release record, not current guidance.
_P41_DOC_PATHS = (
    "README.md",
    "CONTRIBUTING.md",
    "PRODUCT.md",
    "docs/index.md",
    "docs/architecture.md",
    _FREEZE_DOC,
    "docs/reference/codebase-map.md",
    "docs/reference/source-layout.md",
    "docs/reference/cli.md",
    "docs/reference/configuration.md",
    "docs/how-to-guides/testing-strategy.md",
    "docs/how-to-guides/dspy-integration.md",
)

_GIT_PIN_PATTERNS = (
    r"git\+https?://",
    r"git@github\.com",
    r"@ git\+",
    r"(?i)\bpip\s+install\s+(?:-[^\n]*\s+)*git",
    r"(?i)\bunreleased\b[^\n]{0,40}\bdspy\b|\bdspy\b[^\n]{0,40}\bunreleased\b",
    r"(?i)(dspy|gepa)[^\n]{0,60}\bgit\s+(override|pin|commit)\b|\bgit\s+(override|pin|commit)\b[^\n]{0,60}(dspy|gepa)",
    r"\boptimize_anything\b|\bOptimizeAnythingConfig\b",
)

_FRONTEND_TOPIC = re.compile(r"(?i)front\s*-?\s*end|\breact\b|\bvite\b|\besbuild\b|web\s+ui")
_NEGATED_CLAIM = re.compile(
    r"(?i)\bremov\w*\b|\bno\b|\bnot\b|\bnever\b|\bwithout\b|\bonly\b|\blegacy\b"
    r"|separate\s+(work|effort)|\bfuture\b|\babsent\b|\bguard\w*\b"
)

_MEMORY_COORDINATION = re.compile(
    r"(?i)(memory|memories\.md)[^\n]*(coordinat\w+|synchroni[sz]\w+|linearizab\w+)"
    r"|(coordinat\w+|synchroni[sz]\w+|linearizab\w+)[^\n]*(memory|memories\.md)"
)
_MEMORY_NEGATION = re.compile(
    r"(?i)\bnot\b|\bno\b|\bnever\b|without|unsupported|uncertified|falsif"
    r"|\bgated\b|absent|process-local|\bonly\b"
)
_LIMITATION_MARKER = re.compile(
    r"(?i)append serialization is process-local"
    r"|independent sandbox mounts do NOT coordinate"
    r"|concurrent cross-(sandbox|process) append is not (coordinated|guaranteed)"
    r"|appends from multiple host processes are not coordinated"
)

_FORBIDDEN_PRIVATE_NAMES = (
    "PreparationAttempt",
    "RunExecutionDriver",
    "RunOwnership",
    "RunLifetimeReceipt",
    "OwnershipComponentReceipt",
    "PreparedResourcesReceipt",
    "WorkspaceLikeConfig",
    "workspace_like_tools",
    "workspace_like_event_views",
    "fleet_rlm.chat.preparation_attempt",
    "fleet_rlm.chat.run_execution",
    "fleet_rlm.chat.run_runtime_owner",
)

_FREEZE_TABLE_HEADER = "## Frozen behaviors and owners"
_FREEZE_TABLE_TOPICS = (
    "dspy==3.3.1",
    "native RLM",
    "Recursion",
    "Turn",
    "Runtime Event",
    "SSE",
    "pi-tui",
    "failure taxonomy",
    "Session Workspace",
    "Workspace Memory",
    "Attachment",
    "Artifact",
    "Daytona provider",
    "FastAPI",
    "Packaging",
    "CLI",
)
_FREEZE_DOC_ANCHORS = (
    "behavior-based",
    "never private Python structure",
    "Cross-Sandbox Workspace Memory append",
    "unsupported",
    "tests/freeze/",
)

_BASELINE_MANIFEST = Path("tests/fixtures/p35e-golden-baseline.json")
_TAXONOMY_GOLDEN = Path("tests/fixtures/failure-taxonomy.json")


def _doc_text(relative: str) -> str:
    return (_REPO_ROOT / relative).read_text(encoding="utf-8")


def _paragraphs(text: str) -> list[str]:
    return [block.replace("\n", " ") for block in re.split(r"\n\s*\n", text)]


def test_documentation_inventory_and_freeze_doc_contract() -> None:
    existing = {path for path in _P41_DOC_PATHS if (_REPO_ROOT / path).is_file()}
    assert existing == set(_P41_DOC_PATHS)

    freeze = _doc_text(_FREEZE_DOC)
    for anchor in _FREEZE_DOC_ANCHORS:
        assert anchor in freeze, f"behavior-freeze doc is missing anchor {anchor!r}"
    for private_name in _FORBIDDEN_PRIVATE_NAMES:
        assert private_name not in freeze, f"behavior-freeze doc freezes a private name: {private_name}"

    section_match = re.search(
        re.escape(_FREEZE_TABLE_HEADER) + r"(?P<body>.*?)\n## ",
        freeze,
        flags=re.DOTALL,
    )
    assert section_match is not None
    rows = [
        line
        for line in section_match.group("body").splitlines()
        if line.startswith("|") and "---" not in line and "Frozen behavior" not in line
    ]
    assert len(rows) >= len(_FREEZE_TABLE_TOPICS)
    body = section_match.group("body")
    for topic in _FREEZE_TABLE_TOPICS:
        assert topic.lower() in body.lower(), f"freeze table is missing behavior {topic!r}"
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert len(cells) >= 4, f"freeze row is malformed: {row!r}"
        behavior, _surface, owner, _lanes = cells[0], cells[1], cells[2], cells[3]
        assert behavior, "freeze row has an empty behavior cell"
        assert owner, f"freeze row lacks an owner: {row!r}"


def test_docs_have_no_git_pin_or_git_install_guidance() -> None:
    offenders: list[str] = []
    for relative in _P41_DOC_PATHS:
        text = _doc_text(relative)
        for pattern in _GIT_PIN_PATTERNS:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{relative}:{line}: {match.group(0)!r} ({pattern})")
    assert offenders == []


def test_docs_have_no_removed_frontend_references() -> None:
    offenders: list[str] = []
    for relative in _P41_DOC_PATHS:
        for paragraph in _paragraphs(_doc_text(relative)):
            if _FRONTEND_TOPIC.search(paragraph) and not _NEGATED_CLAIM.search(paragraph):
                offenders.append(f"{relative}: {paragraph[:160]!r}")
    assert offenders == []


def test_docs_make_no_cross_sandbox_memory_coordination_claims() -> None:
    offenders: list[str] = []
    markers: list[str] = []
    for relative in _P41_DOC_PATHS:
        text = _doc_text(relative)
        for paragraph in _paragraphs(text):
            if _MEMORY_COORDINATION.search(paragraph) and not _MEMORY_NEGATION.search(paragraph):
                offenders.append(f"{relative}: {paragraph[:160]!r}")
            if _LIMITATION_MARKER.search(paragraph):
                markers.append(relative)
    assert offenders == []
    assert markers, "no curated doc records the cross-Sandbox Memory append limitation"

    freeze = _doc_text(_FREEZE_DOC)
    assert re.search(r"Cross-Sandbox Workspace Memory append", freeze)
    assert re.search(r"(?i)cross-Sandbox Workspace Memory append[^\n]*unsupported", freeze)


def test_behavior_goldens_match_the_baseline_digest_manifest() -> None:
    manifest = json.loads((_REPO_ROOT / _BASELINE_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema"] == "fleet.behavior-golden-baseline/v1"
    pinned = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    assert pinned, "golden baseline manifest pins no files"
    drift: list[str] = []
    for relative, expected in sorted(pinned.items()):
        path = _REPO_ROOT / relative
        if not path.is_file():
            drift.append(f"{relative}: missing")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            drift.append(f"{relative}: {actual} != {expected}")
    assert drift == [], "changed golden bytes require a recorded human decision in the baseline"

    assert json.loads((_REPO_ROOT / _TAXONOMY_GOLDEN).read_text(encoding="utf-8"))["schema"] == (
        "fleet.public-failure-taxonomy/v1"
    )

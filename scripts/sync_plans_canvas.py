#!/usr/bin/env python3
"""Regenerate the plans-roadmap canvas from implementation phase dossiers."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PHASES_PATH = REPO_ROOT / "docs" / "plan-implementation" / "phases"
EXPECTED_DOSSIERS = (
    "01-sse-transport",
    "02-direct-rlm-runtime",
    "03-skills",
    "04-daytona-facade",
    "05-tools-artifacts-attachments",
    "06-observability",
    "07-typed-config",
    "08-gepa-quality",
    "08.5-persistence-db",
    "09-direct-rlm-promotion",
    "10-frontend-sse-cleanup",
)
BEGIN_MARKER = "// BEGIN GENERATED PHASES — do not edit; run: uv run python scripts/sync_plans_canvas.py"
LEGACY_BEGIN_MARKER = "// BEGIN GENERATED PHASES — do not edit; run: make plans-canvas-sync"
END_MARKER = "// END GENERATED PHASES"

PHASE_HEADING_RE = re.compile(
    r"^[ \t]*(#{1,3})\s+Phase\s+([\dA-Z.]+)\s+[—–-]\s+(.+?)\s*$",
    re.MULTILINE,
)
METADATA_RE_TEMPLATE = r"^[ \t]*-\s+\*\*{field}:\*\*\s+(.+?)\s*$"
SRC_PATH_RE = re.compile(r"`(src/fleet_rlm/[^`]+)`")
TESTS_PATH_RE = re.compile(r"`(tests/[^`]+)`")
ACCEPTANCE_RE = re.compile(r"^-\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)
BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)

DOCUMENT_STATUSES = {
    "complete",
    "partial",
    "in_progress_uncommitted",
    "planned",
    "promotion_gated",
}
CANVAS_STATUS = {
    "complete": "complete",
    "partial": "pending",
    "in_progress_uncommitted": "in_progress",
    "planned": "pending",
    "promotion_gated": "pending",
}

TRACK_BY_CODE: dict[str, str] = {
    "2A.1": "Runtime",
    "2A.2": "Runtime",
    "2B": "Runtime",
    "2C": "Runtime",
    "2D": "Runtime",
    "2D.1": "Runtime",
    "9": "Runtime",
    "3A": "Skills",
    "3B": "Skills",
    "3C": "Skills",
    "3D": "Skills",
    "3E": "Skills",
    "3F": "Skills",
    "3G": "Skills",
    "3H": "Skills",
    "4": "Daytona",
    "5": "Daytona",
    "6": "Observability",
    "7": "Config",
    "8": "Config",
    "8.5": "Persistence",
    "8.5A": "Persistence",
    "8.5B": "Persistence",
    "10": "Frontend",
}


@dataclass
class PhaseRecord:
    order: float
    code: str
    name: str
    summary: str
    status: str = "planned"
    commit: str | None = None
    track_name: str | None = None
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    acceptance: list[dict[str, object]] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    validation_cmd: str | None = None
    plans_section: str | None = None

    @property
    def phase_id(self) -> str:
        return self.code.lower().replace(".", "")

    @property
    def track(self) -> str:
        return self.track_name or TRACK_BY_CODE.get(self.code, "Runtime")

    @property
    def display_name(self) -> str:
        return f"{self.code} {self.name}"


def default_canvas_path() -> Path:
    slug = "-".join(REPO_ROOT.parts[1:]) if REPO_ROOT.is_absolute() else "-".join(REPO_ROOT.parts)
    return Path.home() / ".cursor" / "projects" / slug / "canvases" / "plans-roadmap.canvas.tsx"


def title_case_summary(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _metadata(section: str, field: str, *, required: bool = True) -> str | None:
    pattern = re.compile(METADATA_RE_TEMPLATE.format(field=re.escape(field)), re.MULTILINE | re.IGNORECASE)
    match = pattern.search(section)
    if not match:
        if required:
            raise ValueError(f"phase section is missing {field} metadata")
        return None
    return match.group(1).strip().strip("`")


def _heading_block(section: str, heading: str) -> str:
    match = re.search(
        rf"^[ \t]*###\s+{re.escape(heading)}\s*$([\s\S]*?)(?=^[ \t]*###\s+|\Z)",
        section,
        re.MULTILINE | re.IGNORECASE,
    )
    return match.group(1) if match else ""


def _parse_phase(section: str, code: str, name: str, source: Path) -> PhaseRecord:
    order_text = _metadata(section, "Order")
    status = _metadata(section, "Status")
    track = _metadata(section, "Track")
    summary = _metadata(section, "Summary")
    assert order_text and status and track and summary
    if status not in DOCUMENT_STATUSES:
        raise ValueError(f"unsupported status {status!r} in {source}")
    try:
        order = float(order_text)
    except ValueError as exc:
        raise ValueError(f"invalid phase order {order_text!r} in {source}") from exc

    phase = PhaseRecord(
        order=order,
        code=code,
        name=name,
        summary=summary,
        status=status,
        commit=_metadata(section, "Commit", required=False),
        track_name=track,
    )

    files = sorted(set(SRC_PATH_RE.findall(section)))
    phase.files = [path for path in files if not path.endswith("/")]
    phase.tests = sorted(set(TESTS_PATH_RE.findall(section)))
    acceptance_block = _heading_block(section, "Acceptance criteria")
    phase.acceptance = [
        {"text": match.group(2).strip(), "done": match.group(1).lower() == "x"}
        for match in ACCEPTANCE_RE.finditer(acceptance_block)
    ]
    phase.non_goals = [
        line.strip()[2:].strip()
        for line in _heading_block(section, "Non-goals").splitlines()
        if line.strip().startswith("- ")
    ][:8]
    bash_blocks = BASH_BLOCK_RE.findall(_heading_block(section, "Validation"))
    if bash_blocks:
        phase.validation_cmd = bash_blocks[0].strip()
    return phase


def build_phases(
    phases_path: Path = PHASES_PATH,
    *,
    required_dossiers: tuple[str, ...] = EXPECTED_DOSSIERS,
) -> tuple[list[PhaseRecord], list[str]]:
    """Load ordered phase records and deferred gaps from dossier READMEs."""
    if not phases_path.is_dir():
        raise FileNotFoundError(f"missing phase dossier directory: {phases_path}")
    missing = [name for name in required_dossiers if not (phases_path / name / "README.md").is_file()]
    if missing:
        raise ValueError(f"missing phase dossiers: {', '.join(missing)}")

    phases: list[PhaseRecord] = []
    deferred: list[str] = []
    seen_codes: dict[str, Path] = {}
    for readme in sorted(phases_path.glob("*/README.md")):
        text = readme.read_text(encoding="utf-8")
        matches = list(PHASE_HEADING_RE.finditer(text))
        for index, match in enumerate(matches):
            code, name = match.group(2), match.group(3).strip()
            if code in seen_codes:
                raise ValueError(f"duplicate phase code: {code} ({seen_codes[code]} and {readme})")
            seen_codes[code] = readme
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            phases.append(_parse_phase(text[match.start() : end], code, name, readme))

        deferred_match = re.search(
            r"^[ \t]*##\s+Deferred gaps\s*$([\s\S]*?)(?=^[ \t]*##\s+|\Z)",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if deferred_match:
            deferred.extend(
                line.strip()[2:].strip()
                for line in deferred_match.group(1).splitlines()
                if line.strip().startswith("- ")
            )

    if not phases:
        raise ValueError(f"no phase records found under {phases_path}")
    orders = [phase.order for phase in phases]
    if len(set(orders)) != len(orders):
        raise ValueError("duplicate phase order in implementation dossiers")
    phases.sort(key=lambda phase: phase.order)
    deferred = list(dict.fromkeys(deferred))[:10]
    return phases, deferred


def ts_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def render_phase_object(phase: PhaseRecord) -> str:
    payload: dict[str, object] = {
        "id": phase.phase_id,
        "name": phase.display_name,
        "track": phase.track,
        "status": CANVAS_STATUS[phase.status],
        "summary": phase.summary,
        "order": phase.order,
    }
    if phase.commit:
        payload["commit"] = phase.commit
    if phase.files:
        payload["files"] = phase.files
    if phase.tests:
        payload["tests"] = phase.tests
    if phase.acceptance:
        payload["acceptance"] = phase.acceptance
    if phase.non_goals:
        payload["nonGoals"] = phase.non_goals
    if phase.validation_cmd:
        payload["validationCmd"] = phase.validation_cmd

    def to_ts(value: object) -> str:
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=True)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                items = ", ".join(
                    "{ text: " + to_ts(item["text"]) + ", done: " + to_ts(item["done"]) + " }" for item in value
                )
                return f"[{items}]"
            inner = ", ".join(to_ts(item) for item in value)
            return f"[{inner}]"
        raise TypeError(f"Unsupported value type: {type(value)!r}")

    parts = [f"{key}: {to_ts(value)}" for key, value in payload.items()]
    return "{ " + ", ".join(parts) + " }"


def render_phases_ts(phases: list[PhaseRecord], deferred: list[str]) -> str:
    lines: list[str] = [
        BEGIN_MARKER,
        "const PHASES: readonly Phase[] = [",
    ]
    for phase in phases:
        lines.append(f"  {render_phase_object(phase)},")
    lines.append("];")

    next_phase = select_next_phase(phases)
    lines.append(f"const NEXT_PHASE_ID = {ts_string(next_phase.phase_id)};")
    lines.append(f"const DEFERRED_GAPS: readonly string[] = [{', '.join(ts_string(gap) for gap in deferred)}];")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def select_next_phase(phases: list[PhaseRecord]) -> PhaseRecord:
    """Prefer active work over earlier partial or promotion-gated records."""
    return next(
        (phase for phase in phases if phase.status == "in_progress_uncommitted"),
        next((phase for phase in phases if phase.status != "complete"), phases[-1]),
    )


def patch_canvas(canvas_path: Path, generated: str) -> bool:
    content = canvas_path.read_text(encoding="utf-8")
    begin_marker = BEGIN_MARKER if BEGIN_MARKER in content else LEGACY_BEGIN_MARKER
    start = content.find(begin_marker)
    end = content.find(END_MARKER)
    if start < 0 or end < 0:
        raise ValueError(f"{canvas_path} is missing generated markers; add {BEGIN_MARKER!r} and {END_MARKER!r}")
    end += len(END_MARKER)
    updated = content[:start] + generated.rstrip("\n") + content[end:]
    if updated == content:
        return False
    canvas_path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canvas",
        type=Path,
        default=default_canvas_path(),
        help="Path to plans-roadmap.canvas.tsx",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when the canvas would change",
    )
    args = parser.parse_args()

    if not PHASES_PATH.exists():
        print(f"ERROR: missing {PHASES_PATH}", file=sys.stderr)
        return 1
    if not args.canvas.exists():
        print(f"ERROR: missing canvas file {args.canvas}", file=sys.stderr)
        return 1

    phases, deferred = build_phases()
    generated = render_phases_ts(phases, deferred)

    if args.check:
        content = args.canvas.read_text(encoding="utf-8")
        begin_marker = BEGIN_MARKER if BEGIN_MARKER in content else LEGACY_BEGIN_MARKER
        start = content.find(begin_marker)
        end = content.find(END_MARKER)
        if start < 0 or end < 0:
            print("ERROR: canvas markers missing", file=sys.stderr)
            return 1
        end += len(END_MARKER)
        current = content[start:end]
        if current != generated.rstrip("\n"):
            print("ERROR: plans-roadmap canvas is out of sync with phase dossiers", file=sys.stderr)
            print("Run: uv run python scripts/sync_plans_canvas.py", file=sys.stderr)
            return 1
        print("✅ plans-roadmap canvas is in sync with phase dossiers")
        return 0

    changed = patch_canvas(args.canvas, generated)
    if changed:
        print(f"✅ Regenerated phase data in {args.canvas}")
    else:
        print(f"✅ plans-roadmap canvas already up to date ({args.canvas})")
    print(
        f"   phases: {len(phases)} complete: {sum(1 for p in phases if p.status == 'complete')} "
        f"next: {select_next_phase(phases).phase_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

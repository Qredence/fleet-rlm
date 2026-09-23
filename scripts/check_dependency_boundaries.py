#!/usr/bin/env python3
"""Check the dependency directions required by the P50 domain split.

This complements the existing backend-tree check with the P50 destination
dependency graph.  It runs as part of the baseline ``make check`` gate so
provider/domain edges cannot regress silently.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.import_walk import iter_imports, matches

_SOURCE_ROOT_NAME = "src"
_PACKAGE_NAME = "fleet_rlm"
_ALLOWED_STORAGE_TRANSPORT = "fleet_rlm.daytona.workspace_agent.client"
_MEMORY_CONTENT_PATTERNS = (
    re.compile(r"memory_(?:migrate|append|edit|delete)"),
    re.compile(r"\bWorkspaceMemory\b"),
    re.compile(r"\bMemoryCandidate\b"),
    re.compile(r"\bbuild_workspace_memory_store\b"),
    re.compile(r"\bMemoryPromotionIntent\b"),
    re.compile(r"\bMemoryFailureCategory\b"),
    re.compile(r"memory_promotion"),
)


@dataclass(frozen=True)
class BoundaryViolation:
    """One import or content edge that violates a P50 boundary."""

    path: str
    line: int
    rule: str
    target: str

    def render(self) -> str:
        """Render a stable, source-oriented diagnostic for CLI and CI output."""
        return f"{self.path}:{self.line}: {self.rule}: {self.target}"


def _relative_path(path: Path, root: Path) -> str:
    """Return a stable path for diagnostics, independent of checkout location."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _forbidden_imports(relative: Path) -> tuple[tuple[str, str], ...]:
    """Return ``(rule, target-prefix)`` pairs for a source-tree path."""
    scope = relative.parts[0] if relative.parts else ""
    if scope == "daytona":
        if relative.as_posix() == "daytona/turn_environment.py":
            # This provider adapter constructs Turn capabilities from Workspace
            # services; the SDK itself remains below DaytonaRuntime.
            return (("daytona must not import chat", "fleet_rlm.chat"),)
        return (
            ("daytona must not import chat", "fleet_rlm.chat"),
            ("daytona must not import workspace domain", "fleet_rlm.workspace"),
        )
    if scope == "workspace":
        return (
            ("workspace must not import chat", "fleet_rlm.chat"),
            ("workspace must not import rlm", "fleet_rlm.rlm"),
            ("workspace must not import api", "fleet_rlm.api"),
            ("workspace must not import FastAPI", "fastapi"),
            ("workspace must not import Daytona provider modules", "fleet_rlm.daytona"),
        )
    if scope == "runtime" and relative.parts[1:2] != ("daytona",):
        return (
            ("provider-neutral runtime must not import Daytona provider modules", "fleet_rlm.daytona"),
            ("provider-neutral runtime must not import workspace domain", "fleet_rlm.workspace"),
            ("provider-neutral runtime must not import chat", "fleet_rlm.chat"),
            ("provider-neutral runtime must not import api", "fleet_rlm.api"),
        )
    if scope == "sessions":
        return (
            ("sessions must not import chat", "fleet_rlm.chat"),
            ("sessions must not import turn preparation", "fleet_rlm.turn_preparation"),
            ("sessions must not import turn settlement", "fleet_rlm.turn_settlement"),
            ("sessions must not import turn coordination", "fleet_rlm.turns"),
        )
    if scope == "chat":
        return (
            ("chat must not import api", "fleet_rlm.api"),
            ("chat must not import FastAPI", "fastapi"),
        )
    if scope == "persistence":
        return (
            ("persistence must not import rlm", "fleet_rlm.rlm"),
            ("persistence must not import chat", "fleet_rlm.chat"),
            ("persistence must not import turn settlement", "fleet_rlm.turn_settlement"),
            ("persistence must not import turn preparation", "fleet_rlm.turn_preparation"),
            ("persistence must not import turn coordination", "fleet_rlm.turns"),
            ("persistence must not import api", "fleet_rlm.api"),
            ("persistence must not import FastAPI", "fastapi"),
        )
    if scope == "rlm":
        return (
            ("rlm must not import api", "fleet_rlm.api"),
            ("rlm must not import FastAPI", "fastapi"),
            ("rlm must not import chat", "fleet_rlm.chat"),
            ("rlm must not import turn preparation", "fleet_rlm.turn_preparation"),
            ("rlm must not import turn settlement", "fleet_rlm.turn_settlement"),
            ("rlm must not import turn coordination", "fleet_rlm.turns"),
        )
    if scope in {"artifacts", "attachments", "skills"}:
        return ((f"{scope} must not import Daytona provider modules", "fleet_rlm.daytona"),)
    return ()


def _is_storage_transport_exception(relative: Path, imported: str) -> bool:
    """Whether a storage import is the one permitted Daytona transport edge."""
    return relative.as_posix() == "workspace/storage.py" and matches(imported, _ALLOWED_STORAGE_TRANSPORT)


def _content_violations(path: Path, root: Path) -> Iterable[BoundaryViolation]:
    """Find Memory domain names that must leave the Daytona package."""
    relative = _relative_path(path, root)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for pattern in _MEMORY_CONTENT_PATTERNS:
            if pattern.search(line):
                yield BoundaryViolation(
                    relative,
                    line_number,
                    "daytona content must not contain Memory domain policy",
                    pattern.pattern,
                )


def check_dependency_boundaries(root: Path = ROOT) -> tuple[BoundaryViolation, ...]:
    """Return all P50 dependency and Daytona content violations under ``root``."""
    root = root.resolve()
    source_root = root / _SOURCE_ROOT_NAME
    package = source_root / _PACKAGE_NAME
    if not package.is_dir():
        return (
            BoundaryViolation(
                _relative_path(package, root),
                0,
                "source package is missing",
                package.as_posix(),
            ),
        )

    violations: list[BoundaryViolation] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        import_rules = _forbidden_imports(relative)
        if import_rules:
            try:
                imports = tuple(iter_imports(path, source_root=source_root))
            except (OSError, SyntaxError) as exc:
                violations.append(
                    BoundaryViolation(
                        _relative_path(path, root),
                        getattr(exc, "lineno", 0) or 0,
                        "unable to parse source for dependency checks",
                        type(exc).__name__,
                    )
                )
                imports = ()
            seen: set[tuple[int, str, str]] = set()
            for line_number, imported in imports:
                for rule, target in import_rules:
                    if target == "fleet_rlm.daytona" and _is_storage_transport_exception(relative, imported):
                        continue
                    if matches(imported, target):
                        key = (line_number, rule, target)
                        if key not in seen:
                            seen.add(key)
                            violations.append(
                                BoundaryViolation(
                                    _relative_path(path, root),
                                    line_number,
                                    rule,
                                    imported,
                                )
                            )
        if relative.parts[:1] == ("daytona",) and relative.as_posix() != "daytona/turn_environment.py":
            try:
                violations.extend(_content_violations(path, root))
            except OSError as exc:
                violations.append(
                    BoundaryViolation(
                        _relative_path(path, root),
                        0,
                        "unable to read Daytona source for content checks",
                        type(exc).__name__,
                    )
                )
    return tuple(sorted(violations, key=lambda item: (item.path, item.line, item.rule, item.target)))


def build_parser() -> argparse.ArgumentParser:
    """Build the dependency-boundary checker CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root containing src/fleet_rlm (default: current repository)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checker and return a process status code."""
    args = build_parser().parse_args(argv)
    violations = check_dependency_boundaries(args.root)
    if violations:
        print(f"Dependency boundary check failed: {len(violations)} violation(s)", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.render()}", file=sys.stderr)
        return 1
    print("Dependency boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

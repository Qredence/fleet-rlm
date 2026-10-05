from __future__ import annotations

# Shared AST import walker.
import ast
from collections.abc import Iterable
from pathlib import Path


def walker_module_name(path: Path, source_root: Path) -> tuple[str, ...]:
    """Return the package module parts for a source file."""
    return path.relative_to(source_root).with_suffix("").parts


def walker_resolve_from_import(
    node: ast.ImportFrom,
    *,
    path: Path,
    source_root: Path,
) -> Iterable[str]:
    """Yield absolute module names represented by an ``ImportFrom`` node.

    Relative imports are resolved against the source file's package.  Both
    the imported base and alias are yielded so ``from fleet_rlm import chat``
    is checked just like ``import fleet_rlm.chat``.
    """
    module_parts = walker_module_name(path, source_root)
    package_parts = module_parts[:-1]
    if node.level:
        anchor_length = max(0, len(package_parts) - (node.level - 1))
        base_parts = list(package_parts[:anchor_length])
        if node.module:
            base_parts.extend(node.module.split("."))
    else:
        base_parts = node.module.split(".") if node.module else []

    if not base_parts:
        return
    base = ".".join(base_parts)
    yield base
    for alias in node.names:
        if alias.name != "*":
            yield f"{base}.{alias.name}"


def walker_iter_imports(path: Path, *, source_root: Path) -> Iterable[tuple[int, str]]:
    """Yield line-numbered absolute imports, including local imports."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            for imported in walker_resolve_from_import(node, path=path, source_root=source_root):
                yield node.lineno, imported


def walker_matches(imported: str, target: str) -> bool:
    """Match a module prefix, including the intentional trailing-underscore rule."""
    if target.endswith("_"):
        return imported.startswith(target)
    return imported == target or imported.startswith(f"{target}.")


# Codebase ownership and test-layout checks.


import sys

TREE_ROOT = Path(__file__).resolve().parents[1]

if str(TREE_ROOT) not in sys.path:
    sys.path.insert(0, str(TREE_ROOT))


TREE_SOURCE_ROOT = TREE_ROOT / "src"

TREE_PACKAGE = TREE_SOURCE_ROOT / "fleet_rlm"


def tree_find_nested_ternaries(tree: ast.AST) -> list[int]:
    """Return line numbers of ``ast.IfExp`` nodes that nest another ``IfExp``.

    A simple conditional expression (``a if cond else b``) is allowed. Nesting
    another ``IfExp`` anywhere under the body or else branch (including inside
    calls) is a clarity violation. Nesting only in the condition is allowed.
    """
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.IfExp):
            continue
        for child in (node.body, node.orelse):
            if any(isinstance(sub, ast.IfExp) for sub in ast.walk(child)):
                violations.append(node.lineno)
                break
    return violations


def tree_check_codebase_tree(root: Path = TREE_ROOT) -> tuple[list[str], list[str]]:
    """Return boundary and clarity violation messages for the backend package."""
    root = root.resolve()
    source_root = root / "src"
    package = source_root / "fleet_rlm"
    boundary_violations: list[str] = []
    clarity_violations: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for line, imported in walker_iter_imports(path, source_root=source_root):
            if (imported == "daytona" or imported.startswith("daytona.")) and (
                not relative.parts or relative.parts[0] != "daytona"
            ):
                boundary_violations.append(f"{relative}:{line}: Daytona SDK import outside daytona/")
            if relative.parts[:2] == ("api", "routes") and (
                walker_matches(imported, "fleet_rlm.persistence") or walker_matches(imported, "fleet_rlm.daytona")
            ):
                boundary_violations.append(f"{relative}:{line}: route bypasses injected application modules")
        for line in tree_find_nested_ternaries(tree):
            clarity_violations.append(f"{relative}:{line}: nested conditional expression (IfExp)")
    return boundary_violations, clarity_violations


TREE_LAYOUT_EXEMPT_LANES = (
    "tests/live/",
    "tests/contracts/",
    "tests/freeze/",
    "tests/e2e/",
    "tests/unit/backend/packaging/",
)

TREE_MIN_CASES_PER_NEW_FILE = 3


def tree__test_case_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )


def tree_check_test_layout(root: Path = TREE_ROOT) -> list[str]:
    """Return layout violations for the test suite.

    Enforces two rules: the backend unit root stays organized into
    behavior-owner sub-packages, and no test file outside the exempt lanes
    holds fewer than ``_MIN_CASES_PER_NEW_FILE`` cases.
    """
    root = root.resolve()
    tests_root = root / "tests"
    if not tests_root.is_dir():
        return []

    violations: list[str] = []
    backend_root = tests_root / "unit" / "backend"
    for path in sorted(backend_root.glob("test_*.py")):
        violations.append(f"{path.relative_to(root)}: flat file in the backend unit root; move it into a sub-package")

    for path in sorted(tests_root.rglob("test_*.py")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(TREE_LAYOUT_EXEMPT_LANES):
            continue
        cases = tree__test_case_count(path)
        if cases < TREE_MIN_CASES_PER_NEW_FILE:
            violations.append(
                f"{relative}: {cases} case(s); merge into the behavior-owner file "
                f"(minimum {TREE_MIN_CASES_PER_NEW_FILE}) or justify a new lane"
            )
    return violations


def tree_main() -> int:
    boundary_violations, clarity_violations = tree_check_codebase_tree()
    layout_violations = tree_check_test_layout()
    if boundary_violations or clarity_violations or layout_violations:
        print("Backend tree check failed:", file=sys.stderr)
        if boundary_violations:
            print("Boundary:", file=sys.stderr)
            for violation in boundary_violations:
                print(f"- {violation}", file=sys.stderr)
        if clarity_violations:
            print("Clarity:", file=sys.stderr)
            for violation in clarity_violations:
                print(f"- {violation}", file=sys.stderr)
        if layout_violations:
            print("Test layout:", file=sys.stderr)
            for violation in layout_violations:
                print(f"- {violation}", file=sys.stderr)
        return 1
    print("Canonical backend tree check passed (boundaries + nested-ternary clarity + test layout)")
    return 0


# Backend dependency-direction checks.

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEPENDENCY_ROOT = Path(__file__).resolve().parents[1]

if str(DEPENDENCY_ROOT) not in sys.path:
    sys.path.insert(0, str(DEPENDENCY_ROOT))


DEPENDENCY_SOURCE_ROOT_NAME = "src"

DEPENDENCY_PACKAGE_NAME = "fleet_rlm"

ALLOWED_STORAGE_TRANSPORT = "fleet_rlm.daytona.workspace_agent.client"

MEMORY_CONTENT_PATTERNS = (
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


def deps__relative_path(path: Path, root: Path) -> str:
    """Return a stable path for diagnostics, independent of checkout location."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def deps__forbidden_imports(relative: Path) -> tuple[tuple[str, str], ...]:
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
            ("sessions must not import turn coordination", "fleet_rlm.turns"),
            ("sessions must not import persistence", "fleet_rlm.persistence"),
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
            ("persistence must not import turn coordination", "fleet_rlm.turns"),
            ("persistence must not import api", "fleet_rlm.api"),
            ("persistence must not import FastAPI", "fastapi"),
        )
    if scope == "rlm":
        return (
            ("rlm must not import api", "fleet_rlm.api"),
            ("rlm must not import FastAPI", "fastapi"),
            ("rlm must not import chat", "fleet_rlm.chat"),
            ("rlm must not import turn coordination", "fleet_rlm.turns"),
        )
    if scope == "skills":
        return (("skills must not import Daytona provider modules", "fleet_rlm.daytona"),)
    return ()


def deps__is_storage_transport_exception(relative: Path, imported: str) -> bool:
    """Whether a storage import is the one permitted Daytona transport edge."""
    if relative.as_posix() == "workspace/storage.py":
        return walker_matches(imported, ALLOWED_STORAGE_TRANSPORT)
    return relative.as_posix() == "workspace/host_io.py" and walker_matches(imported, "fleet_rlm.daytona.interpreter")


def deps__content_violations(path: Path, root: Path) -> Iterable[BoundaryViolation]:
    """Find Memory domain names that must leave the Daytona package."""
    relative = deps__relative_path(path, root)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for pattern in MEMORY_CONTENT_PATTERNS:
            if pattern.search(line):
                yield BoundaryViolation(
                    relative,
                    line_number,
                    "daytona content must not contain Memory domain policy",
                    pattern.pattern,
                )


def deps_check_dependency_boundaries(root: Path = DEPENDENCY_ROOT) -> tuple[BoundaryViolation, ...]:
    """Return all P50 dependency and Daytona content violations under ``root``."""
    root = root.resolve()
    source_root = root / DEPENDENCY_SOURCE_ROOT_NAME
    package = source_root / DEPENDENCY_PACKAGE_NAME
    if not package.is_dir():
        return (
            BoundaryViolation(
                deps__relative_path(package, root),
                0,
                "source package is missing",
                package.as_posix(),
            ),
        )

    violations: list[BoundaryViolation] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        import_rules = deps__forbidden_imports(relative)
        if import_rules:
            try:
                imports = tuple(walker_iter_imports(path, source_root=source_root))
            except (OSError, SyntaxError) as exc:
                violations.append(
                    BoundaryViolation(
                        deps__relative_path(path, root),
                        getattr(exc, "lineno", 0) or 0,
                        "unable to parse source for dependency checks",
                        type(exc).__name__,
                    )
                )
                imports = ()
            seen: set[tuple[int, str, str]] = set()
            for line_number, imported in imports:
                for rule, target in import_rules:
                    if target == "fleet_rlm.daytona" and deps__is_storage_transport_exception(relative, imported):
                        continue
                    if walker_matches(imported, target):
                        key = (line_number, rule, target)
                        if key not in seen:
                            seen.add(key)
                            violations.append(
                                BoundaryViolation(
                                    deps__relative_path(path, root),
                                    line_number,
                                    rule,
                                    imported,
                                )
                            )
        if relative.parts[:1] == ("daytona",) and relative.as_posix() != "daytona/turn_environment.py":
            try:
                violations.extend(deps__content_violations(path, root))
            except OSError as exc:
                violations.append(
                    BoundaryViolation(
                        deps__relative_path(path, root),
                        0,
                        "unable to read Daytona source for content checks",
                        type(exc).__name__,
                    )
                )
    return tuple(sorted(violations, key=lambda item: (item.path, item.line, item.rule, item.target)))


def deps_build_parser() -> argparse.ArgumentParser:
    """Build the dependency-boundary checker CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEPENDENCY_ROOT,
        help="repository root containing src/fleet_rlm (default: current repository)",
    )
    return parser


def deps_main(argv: Sequence[str] | None = None) -> int:
    """Run the checker and return a process status code."""
    args = deps_build_parser().parse_args(argv)
    violations = deps_check_dependency_boundaries(args.root)
    if violations:
        print(f"Dependency boundary check failed: {len(violations)} violation(s)", file=sys.stderr)
        for violation in violations:
            print(f"- {violation.render()}", file=sys.stderr)
        return 1
    print("Dependency boundary check passed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("tree", "dependencies", "all"))
    parser.add_argument("--root", "--repo-root", dest="root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    failed = False
    if args.command in {"tree", "all"}:
        boundary, clarity = tree_check_codebase_tree(args.root)
        layout = tree_check_test_layout(args.root)
        if boundary or clarity or layout:
            failed = True
            for error in boundary + clarity + layout:
                print(f"- {error}", file=sys.stderr)
        else:
            print("Codebase tree and test layout checks passed")
    if args.command in {"dependencies", "all"}:
        violations = deps_check_dependency_boundaries(args.root)
        if violations:
            failed = True
            print(f"Dependency boundary check failed: {len(violations)} violation(s)", file=sys.stderr)
            for violation in violations:
                print(f"- {violation.render()}", file=sys.stderr)
        else:
            print("Dependency boundary check passed")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

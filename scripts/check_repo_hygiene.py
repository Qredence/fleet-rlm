#!/usr/bin/env python3
"""Run the repository's AGENTS, documentation, and harness hygiene checks."""

from __future__ import annotations

import argparse

# AGENTS.md freshness and link validation.
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, NamedTuple


class AgentsValidationError(NamedTuple):
    file: str
    issue: str
    detail: str


@dataclass
class AgentsMdValidator:
    repo_root: Path
    errors: list[AgentsValidationError] = field(default_factory=list)

    ALLOWED_AGENT_PATHS: ClassVar[frozenset[str]] = frozenset({"AGENTS.md", "tools/fleet-tui/AGENTS.md"})
    DEVELOPMENT_SKILL: ClassVar[str] = ".agents/skills/analyzing-rlm-performance/SKILL.md"

    # Patterns for extracting references
    LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    CODE_BLOCK_PATTERN = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    MAKEFILE_TARGET_PATTERN = re.compile(r"(?:^|[;&|])[ \t]*make[ \t]+([a-zA-Z_][a-zA-Z0-9_-]*)", re.MULTILINE)
    CLI_COMMAND_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"`(uv\s+run\s+fleet[^\s`]*)"),
        re.compile(r"`(uv\s+run\s+fleet-rlm[^\s`]*)"),
        re.compile(r"`(fleet\s+\w+)"),
        re.compile(r"`(pnpm\s+run\s+\w+)"),
    ]

    # External prefixes to skip for link validation
    EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")

    # Directories to exclude from AGENTS.md validation (third-party)
    EXCLUDED_DIRS = (
        "node_modules",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        ".evo",
        ".claude",
        ".factory",
    )

    def validate_all(self) -> list[AgentsValidationError]:
        """Run all validation checks."""
        agents_files = self._find_agents_files()

        if not agents_files:
            self.errors.append(
                AgentsValidationError(
                    file="repo",
                    issue="missing_agents_md",
                    detail="No AGENTS.md files found in repository",
                )
            )
            return self.errors

        self._validate_structure(agents_files)

        for agents_file in agents_files:
            self._validate_file(agents_file)

        self._validate_cross_references(agents_files)
        self._validate_claude_import()
        self._validate_development_skill()

        return self.errors

    def _validate_claude_import(self) -> None:
        """Keep Claude on the canonical repository instructions."""
        path = self.repo_root / "CLAUDE.md"
        if not path.is_file() or path.read_text(encoding="utf-8").strip() != "@AGENTS.md":
            self.errors.append(AgentsValidationError("CLAUDE.md", "invalid_import", "expected only @AGENTS.md"))

    def _validate_development_skill(self) -> None:
        """Validate the owned skill without inspecting installed third-party skills."""
        entry = self.repo_root / self.DEVELOPMENT_SKILL
        if not entry.is_file():
            self.errors.append(
                AgentsValidationError(self.DEVELOPMENT_SKILL, "missing_skill", "development skill is missing")
            )
            return
        skill_root = entry.parent.resolve()
        pending = [entry.resolve()]
        visited: set[Path] = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            content = path.read_text(encoding="utf-8")
            self._check_links(path, content)
            for match in self.LINK_PATTERN.finditer(content):
                target = match.group(2).split("#")[0]
                if not target or target.startswith(self.EXTERNAL_PREFIXES):
                    continue
                linked = (path.parent / target).resolve()
                if linked.is_relative_to(skill_root) and linked.is_file() and linked.suffix == ".md":
                    pending.append(linked)
        for path in sorted(entry.parent.rglob("*.md")):
            if path.resolve() not in visited:
                self.errors.append(
                    AgentsValidationError(
                        str(path.relative_to(self.repo_root)), "unreachable_reference", "not linked from SKILL.md"
                    )
                )

    def _validate_structure(self, agents_files: list[Path]) -> None:
        """Require the root guide and the one intentionally specialized guide."""
        actual = {path.relative_to(self.repo_root).as_posix() for path in agents_files}
        for required in sorted(self.ALLOWED_AGENT_PATHS - actual):
            self.errors.append(
                AgentsValidationError(
                    file=required,
                    issue="missing_required_guide",
                    detail="required repository agent guide is missing",
                )
            )
        for unexpected in sorted(actual - self.ALLOWED_AGENT_PATHS):
            self.errors.append(
                AgentsValidationError(
                    file=unexpected,
                    issue="unexpected_nested_guide",
                    detail="only AGENTS.md and tools/fleet-tui/AGENTS.md are allowed",
                )
            )

    def _find_agents_files(self) -> list[Path]:
        """Find all AGENTS.md files in the repository, excluding third-party dirs."""
        results = []
        for p in self.repo_root.rglob("AGENTS.md"):
            # Exclude third-party directories
            if any(exc in p.parts for exc in self.EXCLUDED_DIRS):
                continue
            relative_path = p.relative_to(self.repo_root)
            ignored = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(relative_path)],
                cwd=self.repo_root,
                check=False,
            )
            if ignored.returncode == 0:
                continue
            results.append(p)
        return sorted(results)

    def _validate_file(self, agents_file: Path) -> None:
        """Validate a single AGENTS.md file."""
        content = agents_file.read_text(encoding="utf-8", errors="ignore")

        # Skip code blocks for some checks
        content_without_code = self._remove_code_blocks(content)

        # Check internal links
        self._check_links(agents_file, content)

        # Check referenced paths exist
        self._check_path_references(agents_file, content_without_code)

        # Check Makefile targets
        self._check_makefile_targets(agents_file, content)

        # Check CLI commands (limited subset for CI efficiency)
        self._check_cli_commands(agents_file, content_without_code)

    def _remove_code_blocks(self, content: str) -> str:
        """Remove code blocks from content for certain checks."""
        return self.CODE_BLOCK_PATTERN.sub("", content)

    def _check_links(self, agents_file: Path, content: str) -> None:
        """Check that internal links resolve to existing files."""
        rel_path = agents_file.relative_to(self.repo_root)

        for match in self.LINK_PATTERN.finditer(content):
            link_text = match.group(1)
            link_target = match.group(2)

            if not link_target or link_target.startswith(self.EXTERNAL_PREFIXES):
                continue

            # Handle absolute filesystem paths (e.g., /Users/.../file.md)
            # These are not portable - flag them as warnings
            if link_target.startswith("/") and not link_target.startswith("/Volumes/"):
                target_path = self.repo_root / link_target.lstrip("/")
            elif link_target.startswith("/"):
                # Extract the repo-relative path from absolute path
                # Find where the repo name appears in the path
                try:
                    parts = Path(link_target).parts
                    repo_name = self.repo_root.name
                    if repo_name in parts:
                        idx = parts.index(repo_name)
                        relative_parts = parts[idx + 1 :]
                        target_path = self.repo_root.joinpath(*relative_parts)
                    else:
                        # Can't resolve, skip
                        continue
                except (ValueError, IndexError):
                    continue
            else:
                target_path = agents_file.parent / link_target

            # Remove fragment if present
            target_path = Path(str(target_path).split("#")[0])

            if not target_path.exists():
                self.errors.append(
                    AgentsValidationError(
                        file=str(rel_path),
                        issue="broken_link",
                        detail=f"Link '{link_text}' -> '{link_target}' does not exist",
                    )
                )

    def _check_path_references(self, agents_file: Path, content: str) -> None:
        """Check that referenced paths (in backticks or as directories) exist."""
        rel_path = agents_file.relative_to(self.repo_root)

        # Resolve paths relative to the guide's directory when needed.
        # Path references there are relative to the package root, not repo root
        package_root = None
        if agents_file.parent != self.repo_root:
            # Find the package root - the parent of the directory containing AGENTS.md
            # that contains a pyproject.toml or is a known package
            candidate = agents_file.parent
            if (candidate / "__init__.py").exists() or (candidate / "pyproject.toml").exists():
                package_root = candidate
            elif (candidate.parent / "pyproject.toml").exists():
                # Python src layout case (e.g., src/fleet_rlm/)
                package_root = candidate
            elif (candidate / "package.json").exists():
                # Node.js package case
                package_root = candidate

        # Pattern for backtick-enclosed paths like `src/fleet_rlm/` or `core/agent/`
        path_pattern = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_\-/.]*/)`")

        for match in path_pattern.finditer(content):
            path_ref = match.group(1).rstrip("/")

            # Skip common non-path patterns and variables
            if path_ref in ("src/", "tests/", "docs/"):
                if not (self.repo_root / path_ref).exists():
                    self.errors.append(
                        AgentsValidationError(
                            file=str(rel_path),
                            issue="missing_directory",
                            detail=f"Referenced directory '{path_ref}' does not exist",
                        )
                    )
                continue

            # Check more specific paths
            if "/" in path_ref and not path_ref.startswith("http"):
                # First try repo-relative
                full_path = self.repo_root / path_ref
                if full_path.exists():
                    continue

                # If package root is set, try package-relative and common subdirs
                if package_root:
                    pkg_path = package_root / path_ref
                    if pkg_path.exists():
                        continue

                    # For Node.js/frontend projects: paths may be relative to src/ or src/components/
                    for subdir in ("src", "src/components", "src/lib", "src/features"):
                        subdir_path = package_root / subdir / path_ref
                        if subdir_path.exists():
                            break
                    else:
                        subdir_path = None
                    if subdir_path and subdir_path.exists():
                        continue

                # Try relative to the AGENTS.md parent
                rel_path_check = agents_file.parent / path_ref
                if rel_path_check.exists():
                    continue

                self.errors.append(
                    AgentsValidationError(
                        file=str(rel_path),
                        issue="missing_path",
                        detail=f"Referenced path '{path_ref}' does not exist",
                    )
                )

    def _check_makefile_targets(self, agents_file: Path, content: str) -> None:
        """Check that referenced Makefile targets exist."""
        rel_path = agents_file.relative_to(self.repo_root)
        makefile_path = self.repo_root / "Makefile"

        if not makefile_path.exists():
            self.errors.append(
                AgentsValidationError(
                    file=str(rel_path),
                    issue="missing_makefile",
                    detail="Makefile not found for target validation",
                )
            )
            return

        # Parse Makefile targets
        makefile_content = makefile_path.read_text(encoding="utf-8", errors="ignore")
        target_pattern = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_-]*):", re.MULTILINE)
        valid_targets = set(target_pattern.findall(makefile_content))

        # Inspect inline examples and fenced commands without interpreting prose or executing them.
        snippets = self.CODE_BLOCK_PATTERN.findall(content)
        snippets.extend(re.findall(r"`([^`\n]+)`", self._remove_code_blocks(content)))
        for match in self.MAKEFILE_TARGET_PATTERN.finditer("\n".join(snippets)):
            target = match.group(1)
            if target not in valid_targets:
                self.errors.append(
                    AgentsValidationError(
                        file=str(rel_path),
                        issue="invalid_makefile_target",
                        detail=f"Makefile target 'make {target}' does not exist",
                    )
                )

    def _check_cli_commands(self, agents_file: Path, content: str) -> None:
        """Check that documented CLI commands still work.

        We run a limited subset of commands that are quick and safe:
        - Help commands (--help)
        - Version checks
        """
        rel_path = agents_file.relative_to(self.repo_root)

        # Extract unique CLI commands
        commands_found: set[str] = set()
        for pattern in self.CLI_COMMAND_PATTERNS:
            for match in pattern.finditer(content):
                commands_found.add(match.group(1))

        # Only validate help commands in CI (safe, quick)
        help_commands = [cmd for cmd in commands_found if "--help" in cmd]

        for cmd in help_commands:
            try:
                proc = subprocess.run(
                    cmd.split(),
                    cwd=self.repo_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode != 0:
                    # Only warn, don't fail - CI environment may differ
                    self.errors.append(
                        AgentsValidationError(
                            file=str(rel_path),
                            issue="cli_command_failed",
                            detail=f"Command '{cmd}' returned {proc.returncode}",
                        )
                    )
            except FileNotFoundError:
                self.errors.append(
                    AgentsValidationError(
                        file=str(rel_path),
                        issue="cli_command_not_found",
                        detail=f"Command '{cmd}' not found in PATH",
                    )
                )
            except subprocess.TimeoutExpired:
                self.errors.append(
                    AgentsValidationError(
                        file=str(rel_path),
                        issue="cli_command_timeout",
                        detail=f"Command '{cmd}' timed out",
                    )
                )
            except Exception as exc:
                self.errors.append(
                    AgentsValidationError(
                        file=str(rel_path),
                        issue="cli_command_error",
                        detail=f"Command '{cmd}' failed: {exc}",
                    )
                )

    def _validate_cross_references(self, agents_files: list[Path]) -> None:
        """Validate cross-references between AGENTS.md files."""
        # Check that the root guide references the one specialized guide.
        root_agents = self.repo_root / "AGENTS.md"
        if not root_agents.exists():
            return

        content = root_agents.read_text(encoding="utf-8", errors="ignore")

        # Every discovered sub-guide must be reachable from the root map.
        expected_refs = ["tools/fleet-tui/AGENTS.md"]

        for ref in expected_refs:
            if ref not in content:
                self.errors.append(
                    AgentsValidationError(
                        file="AGENTS.md",
                        issue="missing_cross_reference",
                        detail=f"Root AGENTS.md should reference '{ref}'",
                    )
                )

        # Check sub-AGENTS.md files reference root
        for agents_file in agents_files:
            if agents_file == root_agents:
                continue

            rel_path = agents_file.relative_to(self.repo_root)
            file_content = agents_file.read_text(encoding="utf-8", errors="ignore")

            # Should reference the root AGENTS.md
            if "AGENTS.md" not in file_content or "[AGENTS.md]" not in file_content:
                self.errors.append(
                    AgentsValidationError(
                        file=str(rel_path),
                        issue="missing_root_reference",
                        detail="Sub-AGENTS.md should reference root AGENTS.md",
                    )
                )


# Active documentation quality checks.

import functools
import re

DOCS_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

DOCS_OPENAPI_PATH_PATTERN = re.compile(r"^\s{2}/", re.MULTILINE)

DOCS_EXTERNAL_PREFIXES = (
    "http://",
    "https://",
    "mailto:",
    "#",
    "discussion://",
    "collection://",
)

DOCS_LEGACY_DOC_DIRS = ("artifacts", "plans", "references", "reviews")

DOCS_ARCHIVED_DOC_PREFIXES = (Path("internal/history"), Path("internal/legacy-backend"))

DOCS_GENERATED_DOC_PREFIXES = (Path("wiki"),)

DOCS_LEGACY_EXPLANATION_MARKERS = (
    Path("explanation/README.md"),
    Path("explanation/architecture.md"),
    Path("explanation/rlm-concepts.md"),
    Path("explanation/stateful-architecture.md"),
    Path("explanation/memory-topology.md"),
    Path("explanation/memory-topology"),
)

DOCS_CLI_CONTRACT_COMMANDS = (("uv", "run", "fleet-rlm", "--help"),)

DOCS_CANONICAL_RUN_ENVIRONMENTS = frozenset({"daytona"})

DOCS_CANONICAL_ENVIRONMENT_DOCS = (
    Path("ARCHITECTURE.md"),
    Path("docs/reference/database.md"),
)

DOCS_CANONICAL_ENVIRONMENT_DECLARATION = re.compile(
    r"^Canonical Run Environment set:\s*(?P<values>[^\n]+)$",
    re.MULTILINE,
)

DOCS_INLINE_CODE_VALUE = re.compile(r"`([a-z][a-z0-9_-]*)`")


def docs_iter_docs_files(docs_root: Path) -> list[Path]:
    """Return active tracked Markdown documents, excluding generated mirrors."""
    repo_root = docs_root.parent
    result = subprocess.run(
        ("git", "ls-files", "docs"),
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return sorted(
            repo_root / rel_path
            for rel_path in result.stdout.splitlines()
            if rel_path.endswith(".md")
            and (repo_root / rel_path).is_file()
            and not any(
                (repo_root / rel_path).relative_to(docs_root).is_relative_to(prefix)
                for prefix in DOCS_GENERATED_DOC_PREFIXES
            )
        )
    return sorted(
        path
        for path in docs_root.rglob("*.md")
        if path.is_file()
        and not any(path.relative_to(docs_root).is_relative_to(prefix) for prefix in DOCS_GENERATED_DOC_PREFIXES)
    )


@functools.cache
def docs__resolve_path(parent_path: Path, target: str) -> Path:
    return (parent_path / target).resolve()


def docs__local_targets(file_path: Path, text: str) -> list[tuple[str, Path]]:
    links: list[tuple[str, Path]] = []
    for raw_target in DOCS_LINK_PATTERN.findall(text):
        if not raw_target or raw_target.startswith(DOCS_EXTERNAL_PREFIXES):
            continue
        clean = raw_target.split("#", 1)[0]
        if not clean:
            continue
        resolved = docs__resolve_path(file_path.parent, clean)
        links.append((raw_target, resolved))
    return links


def docs_check_internal_links(docs_root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    for file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        for raw_target, resolved in docs__local_targets(file_path, text):
            if not resolved.exists():
                rel_file = file_path.relative_to(docs_root.parent).as_posix()
                errors.append(f"broken link: {rel_file} -> {raw_target}")
    return errors


def docs_check_banned_link_schemes(docs_root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    banned = "file://"
    for file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        if banned in text:
            rel_file = file_path.relative_to(docs_root.parent).as_posix()
            errors.append(f"banned link scheme in {rel_file}: contains '{banned}'")
    return errors


def docs__reachable_docs(docs_root: Path, files: list[Path]) -> set[Path]:
    by_path = {p.resolve(): p for p in files}
    start = (docs_root / "index.md").resolve()
    if start not in by_path:
        return set()

    seen: set[Path] = set()
    stack: list[Path] = [start]

    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)

        text = by_path[current].read_text(encoding="utf-8", errors="ignore")
        for _, resolved in docs__local_targets(by_path[current], text):
            if resolved in by_path and resolved not in seen:
                stack.append(resolved)

    return seen


def docs_check_orphans(docs_root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    reachable = docs__reachable_docs(docs_root, files)
    if not reachable:
        return ["missing docs/index.md or unable to traverse docs graph"]

    for file_path in files:
        relative = file_path.relative_to(docs_root)
        if any(relative.is_relative_to(prefix) for prefix in DOCS_ARCHIVED_DOC_PREFIXES):
            continue
        if file_path.resolve() not in reachable:
            rel_file = file_path.relative_to(docs_root.parent).as_posix()
            errors.append(f"orphan active doc: {rel_file}")
    return errors


def docs_check_archived_paths(docs_root: Path) -> list[str]:
    errors: list[str] = []

    def tracked(path: Path) -> bool:
        try:
            result = subprocess.run(
                ("git", "ls-files", "--error-unmatch", path.relative_to(docs_root.parent).as_posix()),
                cwd=docs_root.parent,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return path.exists()
        if result.returncode == 0:
            return path.exists()
        # Temporary test roots need the filesystem fallback; a repository
        # ignores untracked and ignored local material by design.
        inside_repository = (
            subprocess.run(
                ("git", "rev-parse", "--show-toplevel"),
                cwd=docs_root.parent,
                check=False,
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )
        return path.exists() if not inside_repository else False

    for dirname in DOCS_LEGACY_DOC_DIRS:
        candidate = docs_root / dirname
        if tracked(candidate):
            errors.append(f"archived docs directory still present: {candidate}")

    for marker in DOCS_LEGACY_EXPLANATION_MARKERS:
        candidate = docs_root / marker
        if tracked(candidate):
            errors.append(f"archived explanation artifact still present: {candidate}")

    return errors


def docs_check_canonical_environment_sets(repo_root: Path) -> list[str]:
    """Keep the canonical Run Environment declaration aligned across durable docs."""
    errors: list[str] = []
    expected = sorted(DOCS_CANONICAL_RUN_ENVIRONMENTS)

    for relative_path in DOCS_CANONICAL_ENVIRONMENT_DOCS:
        file_path = repo_root / relative_path
        if not file_path.is_file():
            errors.append(f"missing canonical environment document: {relative_path.as_posix()}")
            continue

        text = file_path.read_text(encoding="utf-8", errors="ignore")
        match = DOCS_CANONICAL_ENVIRONMENT_DECLARATION.search(text)
        if match is None:
            errors.append(
                f"missing canonical Run Environment declaration in {relative_path.as_posix()}; expected {expected}"
            )
            continue

        actual = frozenset(DOCS_INLINE_CODE_VALUE.findall(match.group("values")))
        if actual != DOCS_CANONICAL_RUN_ENVIRONMENTS:
            errors.append(
                f"canonical Run Environment drift in {relative_path.as_posix()}: "
                f"expected {expected}, found {sorted(actual)}"
            )

    return errors


def docs_check_contract_sanity(repo_root: Path) -> list[str]:
    errors: list[str] = []

    openapi_path = repo_root / "openapi.yaml"
    if not openapi_path.exists():
        errors.append("missing openapi.yaml")
    else:
        text = openapi_path.read_text(encoding="utf-8", errors="ignore")
        if not DOCS_OPENAPI_PATH_PATTERN.search(text):
            errors.append("openapi.yaml has no path entries")

    for command in DOCS_CLI_CONTRACT_COMMANDS:
        try:
            proc = subprocess.run(
                command,
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            errors.append(f"failed to run {' '.join(command)}: {exc}")
            continue

        if proc.returncode != 0:
            snippet = (proc.stderr or proc.stdout).strip().splitlines()
            tail = snippet[-1] if snippet else "no output"
            errors.append(f"command failed ({proc.returncode}): {' '.join(command)} :: {tail}")

    return errors


def docs_run_checks(repo_root: Path, *, include_contract_checks: bool = True) -> list[str]:
    docs_root = repo_root / "docs"
    if not docs_root.exists():
        return ["missing docs/ directory"]

    files = docs_iter_docs_files(docs_root)
    if not files:
        return ["no markdown files found under docs/"]

    errors: list[str] = []
    errors.extend(docs_check_internal_links(docs_root, files))
    errors.extend(docs_check_banned_link_schemes(docs_root, files))
    errors.extend(docs_check_orphans(docs_root, files))
    errors.extend(docs_check_archived_paths(docs_root))
    errors.extend(docs_check_canonical_environment_sets(repo_root))

    if include_contract_checks:
        errors.extend(docs_check_contract_sanity(repo_root))

    return errors


# Repository harness and bootstrap checks.

import ast
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HARNESS_ROOT_AGENTS_LINE_BUDGET = 140

HARNESS_SAFE_SCRIPT_HELP = frozenset(
    {
        "check_repo_hygiene.py",
        "check_architecture.py",
        "contracts.py",
        "database.py",
        "validate_release.py",
        "live_daytona_verify.py",
        "run_rlm_latency.py",
        "run_routing_eval.py",
        "run_oolong_predict.py",
        "certify_mlflow.py",
    }
)

HARNESS_REQUIRED_GUIDANCE_FILES = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "tools/fleet-tui/AGENTS.md",
)

HARNESS_ALLOWED_AGENT_FILES = frozenset({"AGENTS.md", "tools/fleet-tui/AGENTS.md"})

HARNESS_DOC_INDEXES = ("docs/index.md", "docs/SUMMARY.md")

HARNESS_GENERATED_ARTIFACTS = ("openapi.yaml",)

HARNESS_GENERATED_COMMANDS = ("make api-sync", "make api-check")

HARNESS_HEAVY_IMPORTS = ("dspy", "mlflow", "posthog", "daytona")

HARNESS_CONFIG_MODULES = (
    "src/fleet_rlm/__init__.py",
    "src/fleet_rlm/config/__init__.py",
    "src/fleet_rlm/config/settings.py",
    "src/fleet_rlm/config/loader.py",
)

HARNESS_REMOVED_PATHS = (
    ".factory",
    "oolong_rlm",
    "docs/internal/legacy-backend",
    "src/frontend",
    "src/fleet_rlm/ui",
)

HARNESS_STALE_CONTROL_MARKERS = (
    "src/frontend",
    "docs/internal/legacy-backend",
    "scripts/sync_plans_canvas.py",
    "scripts/consolidate_rlm_results.py",
    "scripts/run_ty_check.zsh",
    "scripts/run_backend_fast_tests.zsh",
    "scripts/run_duplicate_check.zsh",
    "make build-ui",
    "make check-frontend",
    "fleet-rlm chat",
    "fleet-rlm daytona-smoke",
    "src/fleet_rlm/integrations/daytona",
)

HARNESS_LOCAL_COMMAND_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:scripts|\.codex)/[A-Za-z0-9_./-]+\.(?:py|sh|zsh))(?![A-Za-z0-9_.-])"
)


@dataclass(frozen=True)
class HarnessError:
    """A single harness validation failure."""

    path: str
    detail: str


@dataclass
class HarnessChecker:
    """Run repo-specific harness checks."""

    repo_root: Path
    check_script_help: bool = True
    editorial: bool = False
    errors: list[HarnessError] = field(default_factory=list)

    def run(self) -> list[HarnessError]:
        """Run checks and return collected errors.

        Default mode keeps CI/release hard gates only. Editorial checks cover
        documentation hygiene and stale-marker scans that are useful locally
        but brittle for routine ``make check-docs`` runs.
        """
        if self.editorial:
            self._check_root_agents_budget()
            self._check_docs_index_links()
            self._check_generated_artifact_controls()
            self._check_control_surface_drift()
        self._check_required_guidance_files()
        self._check_agent_guide_structure()
        self._check_codex_config()
        self._check_script_inventory()
        self._check_removed_paths()
        self._check_backend_import_boundaries()
        return self.errors

    def _check_root_agents_budget(self) -> None:
        path = self.repo_root / "AGENTS.md"
        if not path.exists():
            self._error("AGENTS.md", "missing root agent map")
            return
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > HARNESS_ROOT_AGENTS_LINE_BUDGET:
            self._error(
                "AGENTS.md",
                f"root guide has {line_count} lines; budget is {HARNESS_ROOT_AGENTS_LINE_BUDGET}",
            )

    def _check_required_guidance_files(self) -> None:
        for rel_path in HARNESS_REQUIRED_GUIDANCE_FILES:
            if not (self.repo_root / rel_path).is_file():
                self._error(rel_path, "required repository guidance file is missing")

    def _check_agent_guide_structure(self) -> None:
        """Keep the nested AGENTS.md surface intentionally closed."""
        try:
            listed = subprocess.run(
                ("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"),
                cwd=self.repo_root,
                check=False,
                capture_output=True,
            )
        except FileNotFoundError:
            listed = None
        if listed and listed.returncode == 0:
            candidates = [
                self.repo_root / raw_path
                for raw_path in listed.stdout.decode().split("\0")
                if raw_path and Path(raw_path).name == "AGENTS.md" and (self.repo_root / raw_path).is_file()
            ]
        else:
            candidates = list(self.repo_root.rglob("AGENTS.md"))
        for path in candidates:
            relative = path.relative_to(self.repo_root).as_posix()
            if relative not in HARNESS_ALLOWED_AGENT_FILES:
                self._error(relative, "unexpected nested AGENTS.md; only the TUI guide is allowed")

    def _check_docs_index_links(self) -> None:
        required_link = "../ARCHITECTURE.md"
        for rel_path in HARNESS_DOC_INDEXES:
            path = self.repo_root / rel_path
            if not path.is_file():
                self._error(rel_path, "docs index is missing")
                continue
            content = path.read_text(encoding="utf-8")
            if required_link not in content:
                self._error(rel_path, "does not link ../ARCHITECTURE.md")

    def _check_codex_config(self) -> None:
        codex_dir = self.repo_root / ".codex"
        required_toml = [
            codex_dir / "config.toml",
            codex_dir / "environments" / "environment.toml",
        ]
        required_toml.extend(sorted((codex_dir / "agents").glob("*.toml")))
        for path in required_toml:
            self._parse_toml(path)
        if (codex_dir / "hooks.json").exists():
            self._error(".codex/hooks.json", "retired Codex hook source is still present")
        for rel_path in (
            ".codex/workspace-bootstrap.zsh",
            ".codex/cloud-preflight.zsh",
        ):
            if not (self.repo_root / rel_path).is_file():
                self._error(rel_path, "required Codex bootstrap/preflight script is missing")

    def _check_generated_artifact_controls(self) -> None:
        docs = "\n".join(
            (self.repo_root / rel_path).read_text(encoding="utf-8")
            for rel_path in ("AGENTS.md", "ARCHITECTURE.md", "tools/fleet-tui/AGENTS.md")
            if (self.repo_root / rel_path).is_file()
        )
        for artifact in HARNESS_GENERATED_ARTIFACTS:
            if artifact not in docs:
                self._error("ARCHITECTURE.md", f"generated artifact not documented: {artifact}")
        for command in HARNESS_GENERATED_COMMANDS:
            if command not in docs:
                self._error("ARCHITECTURE.md", f"generated artifact command not documented: {command}")

    def _check_script_inventory(self) -> None:
        inventory_path = self.repo_root / "scripts" / "README.md"
        if not inventory_path.is_file():
            self._error("scripts/README.md", "script inventory is missing")
            return
        inventory = inventory_path.read_text(encoding="utf-8")
        for script in sorted((self.repo_root / "scripts").rglob("*")):
            if not script.is_file() or script.name == "README.md" or "__pycache__" in script.parts:
                continue
            rel_path = script.relative_to(self.repo_root).as_posix()
            if f"`{rel_path}`" not in inventory:
                self._error(rel_path, "script or data file is missing from scripts/README.md")
            if (
                self.check_script_help
                and script.suffix == ".py"
                and script.name != "__init__.py"
                and script.name in HARNESS_SAFE_SCRIPT_HELP
            ):
                self._check_script_help(script)

    def _control_surface_files(self) -> list[Path]:
        relative_files = (
            "AGENTS.md",
            "ARCHITECTURE.md",
            "tools/fleet-tui/AGENTS.md",
            "CONTRIBUTING.md",
            "Makefile",
            "pyproject.toml",
            ".pre-commit-config.yaml",
            ".circleci/config.yml",
            ".chunk/config.json",
            ".codex/config.toml",
            ".codex/environments/environment.toml",
        )
        files = [self.repo_root / rel_path for rel_path in relative_files]
        files.extend(sorted((self.repo_root / ".github" / "workflows").glob("*.yml")))
        files.extend(sorted((self.repo_root / ".codex" / "agents").glob("*.toml")))
        files.extend(sorted((self.repo_root / ".codex" / "hooks").glob("*.zsh")))
        tracked_docs = subprocess.run(
            ("git", "ls-files", "docs"),
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if tracked_docs.returncode == 0:
            files.extend(
                self.repo_root / rel_path for rel_path in tracked_docs.stdout.splitlines() if rel_path.endswith(".md")
            )
        files.append(self.repo_root / "scripts" / "README.md")
        return [path for path in files if path.is_file()]

    def _check_removed_paths(self) -> None:
        for rel_path in HARNESS_REMOVED_PATHS:
            tracked = subprocess.run(
                ("git", "ls-files", rel_path),
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
            )
            for tracked_path in tracked.stdout.splitlines():
                if (self.repo_root / tracked_path).is_file():
                    self._error(tracked_path, "removed backend/frontend artifact reintroduced")

    def _check_control_surface_drift(self) -> None:
        for path in self._control_surface_files():
            content = path.read_text(encoding="utf-8", errors="ignore")
            rel_path = path.relative_to(self.repo_root).as_posix()
            for marker in HARNESS_STALE_CONTROL_MARKERS:
                if marker in content:
                    self._error(rel_path, f"stale removed-surface reference: {marker}")
            for command_path in HARNESS_LOCAL_COMMAND_PATTERN.findall(content):
                if not (self.repo_root / command_path).is_file():
                    self._error(rel_path, f"references missing local command: {command_path}")

    def _check_script_help(self, script: Path) -> None:
        rel_path = script.relative_to(self.repo_root).as_posix()
        try:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=self.repo_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._error(rel_path, f"--help timed out after {exc.timeout} seconds")
            return
        if result.returncode != 0:
            stderr = result.stderr.strip().splitlines()
            detail = stderr[-1] if stderr else f"exited with {result.returncode}"
            self._error(rel_path, f"--help failed: {detail}")

    def _check_backend_import_boundaries(self) -> None:
        for rel_path in HARNESS_CONFIG_MODULES:
            path = self.repo_root / rel_path
            if not path.is_file():
                continue
            for module in self._extract_import_roots(path):
                if module in HARNESS_HEAVY_IMPORTS:
                    self._error(rel_path, f"config/package-root module imports heavy runtime provider: {module}")

    def _parse_toml(self, path: Path) -> None:
        rel_path = path.relative_to(self.repo_root).as_posix()
        if not path.is_file():
            self._error(rel_path, "required TOML file is missing")
            return
        try:
            tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            self._error(rel_path, f"TOML parse failed: {exc}")

    def _extract_import_roots(self, path: Path) -> set[str]:
        content = path.read_text(encoding="utf-8", errors="ignore")
        roots: set[str] = set()
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return roots
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        return roots

    def _error(self, path: str, detail: str) -> None:
        self.errors.append(HarnessError(path=path, detail=detail))


_REMOVED_COMMANDS = (
    "scripts/live_phase5_verify.py",
    "scripts/live_phase2_recursive_verify.py",
    "scripts/live_p27_snapshot_verify.py",
    "scripts/benchmarks/run_phase4_campaign.py",
    "scripts/verify_child_sandboxes_turn.py",
    "scripts/benchmarks/align_judges.py",
    "scripts/benchmarks/annotate_traces.py",
    "scripts/benchmarks/enable_monitoring.py",
    "scripts/benchmarks/manage_prompts.py",
    "scripts/benchmarks/rlm_eval_dataset.py",
    "scripts/check_agents_md_freshness.py",
    "scripts/check_docs_quality.py",
    "scripts/check_harness_engineering.py",
    "scripts/check_codebase_tree.py",
    "scripts/check_dependency_boundaries.py",
    "scripts/import_walk.py",
    "scripts/openapi_tools.py",
    "scripts/generate_stream_fixture.py",
    "scripts/generate_tui_chunk_validation.py",
    "scripts/generate_configuration_reference.py",
    "scripts/db_init.py",
    "scripts/migrate_sqlite_to_postgres.py",
    "scripts/lakebase_preflight.py",
    "scripts/benchmarks/certify_postgres.py",
    "scripts/normalize_release_artifacts.py",
    "scripts/live_recursive_batch_canary.py",
    "scripts/validate_mlflow_tracing.py",
    "scripts/benchmarks/usage_cost.py",
    "scripts/benchmarks/scorers.py",
)
_ACTIVE_DOC_EXCLUSIONS = (
    Path("internal/history"),
    Path("internal/legacy-backend"),
    Path("plans"),
)


def _tracked_paths(repo_root: Path) -> set[str] | None:
    """Return tracked repository paths, or None for standalone test fixtures."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return set(result.stdout.splitlines())


def _active_control_files(repo_root: Path) -> list[Path]:
    tracked = _tracked_paths(repo_root)
    paths = [
        repo_root / relative
        for relative in (
            "README.md",
            "AGENTS.md",
            "ARCHITECTURE.md",
            "CONTRIBUTING.md",
            "Makefile",
            "scripts/README.md",
        )
    ]
    docs_root = repo_root / "docs"
    if docs_root.is_dir():
        for path in docs_root.rglob("*.md"):
            relative = path.relative_to(docs_root)
            if any(relative.is_relative_to(prefix) for prefix in _ACTIVE_DOC_EXCLUSIONS):
                continue
            if relative.parts[:1] == ("testing",):
                continue  # Dated acceptance records preserve the commands that produced their evidence.
            paths.append(path)
    for workflow_dir in (repo_root / ".circleci", repo_root / ".github" / "workflows"):
        if workflow_dir.is_dir():
            paths.extend(
                path for path in workflow_dir.rglob("*") if path.is_file() and path.suffix in {".yml", ".yaml"}
            )
    for relative in (".codex/cloud-preflight.zsh", ".codex/workspace-bootstrap.zsh"):
        candidate = repo_root / relative
        if candidate.is_file():
            paths.append(candidate)
    skills_root = repo_root / ".agents" / "skills"
    if skills_root.is_dir():
        paths.extend(skills_root.rglob("SKILL.md"))
    return sorted(
        {
            path
            for path in paths
            if path.is_file() and (tracked is None or path.relative_to(repo_root).as_posix() in tracked)
        }
    )


def check_active_script_references(repo_root: Path) -> list[str]:
    """Require every script path in active guidance and workflows to resolve."""
    errors: list[str] = []
    for path in _active_control_files(repo_root):
        content = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(repo_root).as_posix()
        for script_path in HARNESS_LOCAL_COMMAND_PATTERN.findall(content):
            # Bundled skills can demonstrate host-owned helper scripts. Fleet's
            # retired entrypoints are still rejected below across every skill.
            if relative.startswith(".agents/skills/"):
                continue
            if not (repo_root / script_path).is_file():
                errors.append(f"{relative}: references missing script {script_path}")
    return errors


def check_retired_commands_absent(repo_root: Path) -> list[str]:
    """Keep retired commands out of active operator guidance and automation."""
    errors: list[str] = []
    for path in _active_control_files(repo_root):
        content = path.read_text(encoding="utf-8", errors="ignore")
        relative = path.relative_to(repo_root).as_posix()
        for command in _REMOVED_COMMANDS:
            if command in content:
                errors.append(f"{relative}: retired command remains active: {command}")
    return errors


def run_checks(
    repo_root: Path,
    *,
    check_script_help: bool = True,
    editorial: bool = False,
) -> list[str]:
    """Run all formerly separate CI checks once and return unified diagnostics."""
    errors: list[str] = []
    for error in AgentsMdValidator(repo_root).validate_all():
        errors.append(f"AGENTS [{error.file}] {error.issue}: {error.detail}")
    errors.extend(f"docs {error}" for error in docs_run_checks(repo_root))
    for error in HarnessChecker(
        repo_root,
        check_script_help=check_script_help,
        editorial=editorial,
    ).run():
        errors.append(f"harness [{error.path}]: {error.detail}")
    errors.extend(check_active_script_references(repo_root))
    errors.extend(check_retired_commands_absent(repo_root))
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to validate.",
    )
    parser.add_argument("--skip-script-help", action="store_true", help="Skip safe executable --help checks.")
    parser.add_argument("--editorial", action="store_true", help="Include local editorial harness checks.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = run_checks(
        args.repo_root.resolve(),
        check_script_help=not args.skip_script_help,
        editorial=args.editorial,
    )
    if errors:
        print("Repository hygiene checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: repository hygiene checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

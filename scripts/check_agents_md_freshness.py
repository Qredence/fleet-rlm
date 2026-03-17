#!/usr/bin/env python3
"""Validation script for AGENTS.md freshness.

This script validates that AGENTS.md files stay consistent with the codebase by:
1. Checking that referenced paths and files exist
2. Validating internal links between AGENTS.md files
3. Verifying Makefile targets mentioned still exist
4. Checking that CLI commands documented still work
5. Validating structure descriptions match reality

This ensures AGENTS.md documentation remains accurate as the codebase evolves.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple


class ValidationError(NamedTuple):
    file: str
    issue: str
    detail: str


@dataclass
class AgentsMdValidator:
    repo_root: Path
    errors: list[ValidationError] = field(default_factory=list)

    # Patterns for extracting references
    LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    CODE_BLOCK_PATTERN = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    MAKEFILE_TARGET_PATTERN = re.compile(r"`make\s+(\w+)`")
    CLI_COMMAND_PATTERNS = [
        re.compile(r"`(uv\s+run\s+fleet[^\s`]*)"),
        re.compile(r"`(uv\s+run\s+fleet-rlm[^\s`]*)"),
        re.compile(r"`(fleet\s+\w+)"),
        re.compile(r"`(pnpm\s+run\s+\w+)"),
    ]

    # External prefixes to skip for link validation
    EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "#")

    # Directories to exclude from AGENTS.md validation (third-party)
    EXCLUDED_DIRS = ("node_modules", ".venv", "__pycache__", "dist", "build")

    def validate_all(self) -> list[ValidationError]:
        """Run all validation checks."""
        agents_files = self._find_agents_files()

        if not agents_files:
            self.errors.append(
                ValidationError(
                    file="repo",
                    issue="missing_agents_md",
                    detail="No AGENTS.md files found in repository",
                )
            )
            return self.errors

        for agents_file in agents_files:
            self._validate_file(agents_file)

        self._validate_cross_references(agents_files)

        return self.errors

    def _find_agents_files(self) -> list[Path]:
        """Find all AGENTS.md files in the repository, excluding third-party dirs."""
        results = []
        for p in self.repo_root.rglob("AGENTS.md"):
            # Exclude third-party directories
            if any(exc in p.parts for exc in self.EXCLUDED_DIRS):
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
        self._check_makefile_targets(agents_file, content_without_code)

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
                    ValidationError(
                        file=str(rel_path),
                        issue="broken_link",
                        detail=f"Link '{link_text}' -> '{link_target}' does not exist",
                    )
                )

    def _check_path_references(self, agents_file: Path, content: str) -> None:
        """Check that referenced paths (in backticks or as directories) exist."""
        rel_path = agents_file.relative_to(self.repo_root)

        # Determine if this is a subdirectory AGENTS.md (e.g., src/fleet_rlm/AGENTS.md)
        # Path references there are relative to the package root, not repo root
        package_root = None
        if agents_file.parent != self.repo_root:
            # Find the package root - the parent of the directory containing AGENTS.md
            # that contains a pyproject.toml or is a known package
            candidate = agents_file.parent
            if (candidate / "__init__.py").exists() or (
                candidate / "pyproject.toml"
            ).exists():
                package_root = candidate
            elif (candidate.parent / "pyproject.toml").exists():
                # src/frontend case
                package_root = candidate

        # Pattern for backtick-enclosed paths like `src/fleet_rlm/` or `core/agent/`
        path_pattern = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_\-/.]*/)`")

        for match in path_pattern.finditer(content):
            path_ref = match.group(1).rstrip("/")

            # Skip common non-path patterns and variables
            if path_ref in ("src/", "tests/", "docs/"):
                if not (self.repo_root / path_ref).exists():
                    self.errors.append(
                        ValidationError(
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

                # If package root is set, try package-relative
                if package_root:
                    pkg_path = package_root / path_ref
                    if pkg_path.exists():
                        continue

                # Try relative to the AGENTS.md parent
                rel_path_check = agents_file.parent / path_ref
                if rel_path_check.exists():
                    continue

                self.errors.append(
                    ValidationError(
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
                ValidationError(
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

        # Find referenced targets in AGENTS.md
        for match in self.MAKEFILE_TARGET_PATTERN.finditer(content):
            target = match.group(1)
            if target not in valid_targets:
                self.errors.append(
                    ValidationError(
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
                        ValidationError(
                            file=str(rel_path),
                            issue="cli_command_failed",
                            detail=f"Command '{cmd}' returned {proc.returncode}",
                        )
                    )
            except FileNotFoundError:
                self.errors.append(
                    ValidationError(
                        file=str(rel_path),
                        issue="cli_command_not_found",
                        detail=f"Command '{cmd}' not found in PATH",
                    )
                )
            except subprocess.TimeoutExpired:
                self.errors.append(
                    ValidationError(
                        file=str(rel_path),
                        issue="cli_command_timeout",
                        detail=f"Command '{cmd}' timed out",
                    )
                )
            except Exception as exc:
                self.errors.append(
                    ValidationError(
                        file=str(rel_path),
                        issue="cli_command_error",
                        detail=f"Command '{cmd}' failed: {exc}",
                    )
                )

    def _validate_cross_references(self, agents_files: list[Path]) -> None:
        """Validate cross-references between AGENTS.md files."""
        # Check that root AGENTS.md references sub-AGENTS.md files correctly
        root_agents = self.repo_root / "AGENTS.md"
        if not root_agents.exists():
            return

        content = root_agents.read_text(encoding="utf-8", errors="ignore")

        # Expected cross-references from root to sub AGENTS.md files
        expected_refs = ["src/fleet_rlm/AGENTS.md", "src/frontend/AGENTS.md"]

        for ref in expected_refs:
            if ref not in content:
                self.errors.append(
                    ValidationError(
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
                    ValidationError(
                        file=str(rel_path),
                        issue="missing_root_reference",
                        detail="Sub-AGENTS.md should reference root AGENTS.md",
                    )
                )


def main() -> int:
    """Run AGENTS.md freshness validation."""
    repo_root = Path(__file__).resolve().parents[1]

    print("Validating AGENTS.md freshness...")

    validator = AgentsMdValidator(repo_root)
    errors = validator.validate_all()

    if errors:
        print("\nERROR: AGENTS.md freshness validation failed:\n")
        for error in errors:
            print(f"  [{error.file}] {error.issue}: {error.detail}")
        return 1

    print("OK: AGENTS.md freshness validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

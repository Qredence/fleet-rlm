#!/usr/bin/env python3
"""Validate the repo-local harness engineering control surface."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT_AGENTS_LINE_BUDGET = 150
REQUIRED_HARNESS_DOCS = (
    "docs/agent-harness/README.md",
    "docs/agent-harness/feedback-loop.md",
    "docs/agent-harness/architecture-invariants.md",
    "docs/agent-harness/quality-score.md",
    "docs/agent-harness/drift-control.md",
)
DOC_INDEXES = ("docs/index.md", "docs/SUMMARY.md")
GENERATED_ARTIFACTS = ("openapi.yaml",)
GENERATED_COMMANDS = ("make api-sync", "make api-check")
HEAVY_IMPORTS = ("dspy", "mlflow", "posthog", "daytona")
CONFIG_MODULES = (
    "src/fleet_rlm/__init__.py",
    "src/fleet_rlm/config.py",
)
REMOVED_PATHS = (
    ".factory",
    "oolong_rlm",
    "docs/internal/legacy-backend",
    "src/frontend",
    "src/fleet_rlm/ui",
)
STALE_CONTROL_MARKERS = (
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
LOCAL_COMMAND_PATTERN = re.compile(
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
    errors: list[HarnessError] = field(default_factory=list)

    def run(self) -> list[HarnessError]:
        """Run all checks and return collected errors."""
        self._check_root_agents_budget()
        self._check_required_docs()
        self._check_docs_index_links()
        self._check_codex_config()
        self._check_generated_artifact_controls()
        self._check_script_inventory()
        self._check_removed_paths()
        self._check_control_surface_drift()
        self._check_backend_import_boundaries()
        return self.errors

    def _check_root_agents_budget(self) -> None:
        path = self.repo_root / "AGENTS.md"
        if not path.exists():
            self._error("AGENTS.md", "missing root agent map")
            return
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > ROOT_AGENTS_LINE_BUDGET:
            self._error(
                "AGENTS.md",
                f"root guide has {line_count} lines; budget is {ROOT_AGENTS_LINE_BUDGET}",
            )

    def _check_required_docs(self) -> None:
        for rel_path in REQUIRED_HARNESS_DOCS:
            if not (self.repo_root / rel_path).is_file():
                self._error(rel_path, "required harness doc is missing")

    def _check_docs_index_links(self) -> None:
        required_link = "agent-harness/README.md"
        for rel_path in DOC_INDEXES:
            path = self.repo_root / rel_path
            if not path.is_file():
                self._error(rel_path, "docs index is missing")
                continue
            content = path.read_text(encoding="utf-8")
            if required_link not in content and "docs/agent-harness/README.md" not in content:
                self._error(rel_path, "does not link docs/agent-harness/README.md")

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
            self._error(
                ".codex/hooks.json",
                "deprecated hook source still present; use inline [hooks] in .codex/config.toml only",
            )
        for rel_path in (".codex/workspace-bootstrap.zsh",):
            if not (self.repo_root / rel_path).is_file():
                self._error(rel_path, "required Codex hook/bootstrap script is missing")

    def _check_generated_artifact_controls(self) -> None:
        docs = "\n".join(
            (self.repo_root / rel_path).read_text(encoding="utf-8")
            for rel_path in REQUIRED_HARNESS_DOCS
            if (self.repo_root / rel_path).is_file()
        )
        for artifact in GENERATED_ARTIFACTS:
            if artifact not in docs:
                self._error("docs/agent-harness", f"generated artifact not documented: {artifact}")
        for command in GENERATED_COMMANDS:
            if command not in docs:
                self._error("docs/agent-harness", f"generated artifact command not documented: {command}")

    def _check_script_inventory(self) -> None:
        inventory_path = self.repo_root / "scripts" / "README.md"
        if not inventory_path.is_file():
            self._error("scripts/README.md", "script inventory is missing")
            return
        inventory = inventory_path.read_text(encoding="utf-8")
        for script in sorted((self.repo_root / "scripts").glob("*.py")):
            rel_path = script.relative_to(self.repo_root).as_posix()
            if script.name not in inventory and rel_path not in inventory:
                self._error(rel_path, "top-level Python helper is missing from scripts/README.md")
            if self.check_script_help:
                self._check_script_help(script)

    def _control_surface_files(self) -> list[Path]:
        relative_files = (
            "AGENTS.md",
            "CONTRIBUTING.md",
            "Makefile",
            "PRODUCT.md",
            "pyproject.toml",
            ".pre-commit-config.yaml",
            ".circleci/config.yml",
            ".chunk/config.json",
            ".fastapicloudignore",
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
        for rel_path in REMOVED_PATHS:
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
            for marker in STALE_CONTROL_MARKERS:
                if marker in content:
                    self._error(rel_path, f"stale removed-surface reference: {marker}")
            for command_path in LOCAL_COMMAND_PATTERN.findall(content):
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
        for rel_path in CONFIG_MODULES:
            path = self.repo_root / rel_path
            if not path.is_file():
                continue
            for module in self._extract_import_roots(path):
                if module in HEAVY_IMPORTS:
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to validate.",
    )
    parser.add_argument(
        "--skip-script-help",
        action="store_true",
        help="Skip executing top-level scripts with --help.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    checker = HarnessChecker(
        repo_root=args.repo_root.resolve(),
        check_script_help=not args.skip_script_help,
    )
    errors = checker.run()
    if errors:
        print("Harness engineering checks failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error.path}: {error.detail}", file=sys.stderr)
        return 1
    print("OK: harness engineering checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

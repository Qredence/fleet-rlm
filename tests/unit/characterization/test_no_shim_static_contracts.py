"""Static no-shim characterization checks for legacy contract surfaces.

These tests intentionally separate legacy/product-contract terms from broad
framework words such as ``fallback`` or ``alias``.  The allowlists below are a
baseline inventory of pre-refactor hotspots, not approval to spread the terms
elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class StaticGuard:
    """A precise source scan guard with an explicit current allowlist."""

    name: str
    pattern: re.Pattern[str]
    roots: tuple[str, ...]
    allowed_paths: frozenset[str]
    rationale: str


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _source_files(roots: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".mjs", ".zsh", ".yaml", ".yml"}:
                continue
            if _is_fixture_or_generated(rel):
                continue
            files.append(path)
    return sorted(files)


def _is_fixture_or_generated(rel_path: str) -> bool:
    fixture_parts = {
        "__tests__",
        "tests",
        "scaffold",
        "generated",
        "dist",
        "node_modules",
    }
    parts = set(rel_path.split("/"))
    if parts & fixture_parts:
        return True
    return rel_path.endswith((".test.ts", ".test.tsx", ".spec.ts", ".gen.ts"))


def _matches(guard: StaticGuard) -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for path in _source_files(guard.roots):
        rel = path.relative_to(REPO_ROOT).as_posix()
        lines: list[str] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            if guard.pattern.search(line):
                lines.append(f"{line_number}: {line.strip()}")
        if lines:
            offenders[rel] = lines
    return offenders


NO_SHIM_GUARDS = (
    StaticGuard(
        name="stale route/client contracts",
        pattern=re.compile(r"(/api/v1/(?:chat|ws/chat)\b|rlmCoreEndpoints\.)"),
        roots=("src/fleet_rlm", "src/frontend/src"),
        allowed_paths=frozenset(
            {
                # Pre-refactor URL normalizer for explicit user-provided WS env vars.
                "src/frontend/src/lib/rlm-api/config.ts",
            }
        ),
        rationale="Deleted HTTP chat helpers and websocket chat routes must not gain new callers.",
    ),
    StaticGuard(
        name="obsolete websocket event translators",
        pattern=re.compile(
            r"\b("
            r"assistant_token|reasoning_step|trajectory_step|plan_update|rlm_executing|"
            r"hitl_request|hitl_resolved|command_ack|command_reject"
            r")\b"
        ),
        roots=("src/frontend/src/lib", "src/frontend/src/features/workspace"),
        allowed_paths=frozenset(
            {
                # Current production adapters still render historical frame names; cleanup should remove these files.
                "src/frontend/src/features/workspace/conversation/agent-chat-adapter.ts",
                "src/frontend/src/features/workspace/inspection/graph-node-detail-parsers.ts",
                "src/frontend/src/features/workspace/inspection/parsers/artifact-payload-schemas.ts",
                "src/frontend/src/features/workspace/inspection/parsers/artifact-payload-summaries.ts",
                "src/frontend/src/lib/rlm-api/ws-client.ts",
                "src/frontend/src/lib/rlm-api/ws-frame-parser.ts",
                "src/frontend/src/lib/rlm-api/ws-types.ts",
                "src/frontend/src/lib/workspace/backend-artifact-event-adapter.ts",
                "src/frontend/src/lib/workspace/backend-chat-event-adapter.ts",
                "src/frontend/src/lib/workspace/workspace-types.ts",
            }
        ),
        rationale="Legacy websocket event kinds must stay confined to the known pre-refactor adapter inventory.",
    ),
    StaticGuard(
        name="legacy runtime mode aliases",
        pattern=re.compile(r"\b(recursive_rlm|host_loop_rlm)\b"),
        roots=("src/fleet_rlm", "src/frontend/src"),
        allowed_paths=frozenset(
            {
                # Current UI label normalizer; follow-up refactors should delete this alias mapping.
                "src/frontend/src/lib/workspace/daytona-mode.ts",
            }
        ),
        rationale="Runtime mode aliases cannot spread beyond the existing label normalizer.",
    ),
    StaticGuard(
        name="compatibility run-summary backfills",
        pattern=re.compile(r"\b(run_result|runResult|finalArtifact)\b"),
        roots=("src/frontend/src/lib", "src/frontend/src/features/workspace"),
        allowed_paths=frozenset(
            {
                # Current narrow workbench/chat hydration backfill surfaces.
                "src/frontend/src/features/workspace/workbench/run-workbench.tsx",
                "src/frontend/src/lib/rlm-api/ws-frame-parser.ts",
                "src/frontend/src/lib/workspace/backend-chat-event-adapter.ts",
                "src/frontend/src/lib/workspace/run-workbench-hydration.ts",
                "src/frontend/src/lib/workspace/workspace-types.ts",
            }
        ),
        rationale="Deleted run-result/finalArtifact shapes must not gain new production adapters.",
    ),
)


def test_no_shim_static_guards_allow_only_documented_legacy_contract_hotspots() -> None:
    """No-shim denylist terms remain confined to explicit pre-refactor hotspots."""
    failures: list[str] = []

    for guard in NO_SHIM_GUARDS:
        observed = _matches(guard)
        unexpected_paths = sorted(set(observed) - guard.allowed_paths)
        if unexpected_paths:
            details = "\n".join(f"  {path}:\n    " + "\n    ".join(observed[path][:5]) for path in unexpected_paths)
            failures.append(f"{guard.name}: {guard.rationale}\n{details}")

    assert not failures, "\n\n".join(failures)


def test_no_shim_allowlist_paths_are_existing_production_files() -> None:
    """Every no-shim allowlist entry names a real production source file, not a fixture."""
    missing_or_fixture = [
        path
        for guard in NO_SHIM_GUARDS
        for path in guard.allowed_paths
        if not (REPO_ROOT / path).is_file() or _is_fixture_or_generated(path)
    ]

    assert missing_or_fixture == []


def test_generated_contract_drift_is_guarded_by_backend_frontend_and_hook_checks() -> None:
    """Generated API, client, route, and UI artifacts have owning drift-check commands."""
    makefile = _read("Makefile")
    frontend_package = _read("src/frontend/package.json")
    frontend_api_check = _read("src/frontend/scripts/check-api-sync.mjs")
    generated_hook = _read(".codex/hooks/generated-artifact-check.zsh")
    harness_checker = _read("scripts/check_harness_engineering.py")

    assert "uv run python scripts/openapi_tools.py validate" in makefile
    assert "pnpm run api:check" in makefile
    assert '"api:check": "node scripts/check-api-sync.mjs"' in frontend_package
    assert "pnpm run api:sync" in frontend_api_check

    for artifact in (
        "openapi.yaml",
        "src/frontend/openapi/fleet-rlm.openapi.yaml",
        "src/frontend/src/lib/rlm-api/generated/openapi.ts",
        "src/frontend/src/routeTree.gen.ts",
        "src/frontend/dist",
        "src/fleet_rlm/ui/dist",
    ):
        assert artifact in generated_hook
        assert artifact in harness_checker


def test_no_stale_chat_route_in_committed_openapi_contract() -> None:
    """The committed OpenAPI contract does not expose deleted chat endpoints."""
    root_openapi = _read("openapi.yaml")
    frontend_openapi = _read("src/frontend/openapi/fleet-rlm.openapi.yaml")

    for contract in (root_openapi, frontend_openapi):
        assert "/api/v1/chat" not in contract
        assert "/api/v1/ws/chat" not in contract

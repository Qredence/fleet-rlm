# Skill script runtime preparation evidence

Audit date: 2026-07-09
Scope: Phase 3F.3 — whether turn-prep `ActiveSkills` wiring or scaffold materialization is **required** for `run_skill_script` correctness.

## Verdict

| Item | Required for correctness? | Notes |
|------|---------------------------|-------|
| `runtime._active_skills` turn-prep wiring | **No** | Optional optimization only. |
| Scaffold/package skill materialization into Daytona | **Only for scaffold `scripts/` execution** | Not blocking volume directory skills. |
| Next planned phase | **3G — skill writes and approval workflow** | Do not implement 3F.4 unless product needs scaffold script execution. |

## How `run_skill_script` works without `_active_skills`

`SkillExecutionDeps.from_runtime()` may read `_active_skills` when present, but validation and execution do not depend on it:

1. **`selected_skill_ids`** — resolved at call time from `runtime._selected_skill_ids` (wired in 3F.2).
2. **Script inventory** — `_resolve_script_inventory()` falls back to `inventory_skill_resources(skill_dir)` or `load_skill_bundle()` when `resources` is absent.
3. **Sandbox root** — `resolve_skill_sandbox_root()` derives `{volume_mount}/skills/{scope}/{name}` for directory-style volume skills when `sandbox_paths` is absent.

Volume directory skills with `scripts/` on the Daytona mount execute correctly without publishing turn-prep `ActiveSkills` onto `AgentRuntime`.

## `_active_skills` wiring (not implemented)

Publishing turn-prep `ActiveSkills` onto `AgentRuntime` would only skip redundant bundle/inventory reloads during a turn. The audit found **no correctness gap** for the supported path (selected, trusted, directory-style volume skills). **Do not wire unless a future correctness bug requires it.**

## Scaffold materialization (deferred, not 3G-blocking)

`seed_system_skills()` copies flat `SKILL.md` into `skills/system/{name}.md` only. Package/scaffold skills with `scripts/` are not materialized into the sandbox. Executing those scripts inside Daytona would require explicit materialization — **out of scope** until product requires scaffold script execution. This does not block Phase 3G.

## Tests (3F.3 shipped)

- `tests/unit/skills/test_script_execution.py` — script validation, Daytona execution, public error sanitization
- `tests/unit/skills/test_execution_deps.py` — optional `_active_skills` read path
- `tests/unit/tools/test_skill_tools.py` — read-only skill tools only

# Skill Authoring Guide

## Canonical Bundled Skills vs `.fleet/skills/`

- The 8 bundled skills (`delegation`, `document-extraction`, `file-editing`,
  `long-context-search`, `repository-inspection`, `skill-creator`,
  `tabular-analysis`, `web-research`) are canonical to the installed Fleet version.
- Bundled skill names are reserved: runtime `save_skill(...)` rejects them, and a
  `.fleet/skills/` folder with a bundled name is ignored with a warning, so the bundled
  copy stays active. Choose a distinct kebab-case name for every user skill.
- `/skills push` uploads `.fleet/skills/` to the Session Volume; `/skills pull`
  exports only user-authored skills back to `.fleet/skills/`.
- `.agents/skills/` is contributor documentation and is never synchronized.

## Routing Descriptions

- `description` (1–1024 chars, single line) is the sole text shown in Stage-1
  `<available_skills>` XML and evaluated by `SkillRelevance`.
- State the exact task triggers, input formats, and expected outputs so the
  selector distinguishes the new skill from existing bundled skills.

## Progressive Disclosure

1. **Stage 1 (`list_skills()`)**: returns `name` and `description` only.
2. **Stage 2 (`use_skill(name)` / preloaded `<preloaded_skill>`)**: keep
   `SKILL.md` focused on workflow steps and under 20,000 characters.
3. **Stage 3 (`read_skill_reference`, `load_skill_module`, `run_skill_script`)**:
   place detailed lookup tables, formulas, and schemas in `references/` and
   reusable code in `scripts/`.

## Dual-Mode Script Convention

- Place one primary module at `scripts/<module_name>.py` exposing `run(...)`
  (plus focused helper functions returning JSON-serializable dicts/lists) so
  `mod = load_skill_module("<name>")` makes `mod(...)` directly callable in the REPL.
- Include `main()` + `if __name__ == "__main__": raise SystemExit(main())`
  using `sys.stdout.write` so `run_skill_script("<name>", "scripts/<file>.py", args)`
  also works as a bounded CLI entrypoint.
- Keep scripts stdlib-first or import optional snapshot packages dynamically
  inside functions; never hardcode credentials in Skill files.

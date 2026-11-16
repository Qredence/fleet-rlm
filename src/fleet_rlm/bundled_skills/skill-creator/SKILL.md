---
name: skill-creator
description: Create or improve a Fleet user Skill for a repeatable sandbox workflow. Use when authoring reusable guidance or troubleshooting .fleet/skills/ synchronization.
---

# Create Fleet Skills

Fleet Skills are Markdown instructions and optional `scripts/` / `references/`
synchronized into `/workspace/skills/`. Bundled skills are canonical: `save_skill(...)`
rejects their names, and a `.fleet/skills/` folder with a bundled name is ignored with a
warning. Always pick a new distinct kebab-case name for user Skills.

## Create or update

1. Load the authoring helper to scaffold and validate a new Skill spec:

   ```python
   sc = load_skill_module("skill-creator")
   spec = sc.scaffold_skill(
       "invoice-audit",
       "Audit PDF/CSV vendor invoices against PO totals and write artifacts/audit.json.",
       workflow_steps=[
           "Inspect files/ inputs and profile line items.",
           "Compare totals using Decimal arithmetic and verify discrepancies.",
           "Write artifacts/audit.json, print totals, and SUBMIT from variables.",
       ],
       include_script=True,
       include_reference=True,
   )
   ```

   Or validate custom text/scripts with `sc.validate_skill_spec(name, description, body, scripts=..., references=...)`.

2. Replace scaffold placeholders with task-specific instructions and executable
   logic, and run the helper on a known input. Syntax validation alone does not
   establish that a generated Skill works. Persist the completed Skill to the
   Session Volume with `save_skill(...)`:

   ```python
   print(
       save_skill(
           spec["name"],
           spec["description"],
           spec["body"],
           scripts=spec["scripts"] or None,
           references=spec["references"] or None,
       )
   )
   ```

3. Move the Skill to the host repository when needed: `save_skill(...)` persists it to
   the Session Volume, `/skills pull` exports Volume-authored Skills into
   `.fleet/skills/` (bundled Skills are never exported), and `/skills push` uploads
   `.fleet/skills/` back to the Session. `/skills` lists the catalog. `.agents/skills/`
   stays excluded.

4. Read `read_skill_reference("skill-creator", "references/skill-authoring-guide.md")`
   for routing descriptions, progressive disclosure bounds, and dual-mode script rules.

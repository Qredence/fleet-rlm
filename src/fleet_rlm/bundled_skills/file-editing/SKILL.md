---
name: file-editing
description: Make exact, unique-match edits to existing workspace text files. Use when a localized change needs a diff preview and ambiguous replacements must fail.
---

# Safe file editing

Use this Skill whenever modifying existing source files, configs, Markdown docs, or scripts in `/workspace` so changes remain minimal, verifiable, and safe against ambiguous matches.

## Quick start (REPL)

Load the module once and invoke it directly or via its named helpers:

```python
edit = load_skill_module("file-editing")

# Preview first with dry_run=True
preview = edit("artifacts/draft.py", "TIMEOUT = 10", "TIMEOUT = 30", dry_run=True)
print(preview["diff"])

# Apply once verified
result = edit("artifacts/draft.py", "TIMEOUT = 10", "TIMEOUT = 30")
print(result["start_line"], result["end_line"])
```

## Available helpers

- `edit.edit_unique(path, old_str, new_str, *, dry_run=False)` — requires `old_str` to match **exactly once** (raises `ValueError` with occurrence count on 0 or >1 matches), writes the result (see Write semantics), and returns `{path, start_line, end_line, diff, dry_run}`.
- `edit.apply_edits(path, edits, *, dry_run=False)` — applies a list of `(old_str, new_str)` tuples or `{"old_str": ..., "new_str": ...}` dicts sequentially in memory before writing the result (see Write semantics).
- `edit.preview_diff(path, new_content, *, context_lines=3)` — generates a unified diff string without writing.

## Write semantics

- The helpers write a sibling temporary file, copy the target's permission bits onto it, and replace the target with `os.replace`. This requires a filesystem that supports copying permission bits; permission-copy errors propagate before replacement. Some mounted Daytona workspaces reject `chmod`, so do not promise arbitrary Unix modes there.
- If that rename fails with `EXDEV` (cross-device), `ENOSYS`, or `EOPNOTSUPP` (filesystems such as some FUSE-mounted workspaces that cannot rename), the helper **writes the new content directly into the target file in place**. This fallback is **not atomic**: an interruption during that write can leave the file partly written. For important files, keep a copy or rely on version control, and check the result with a re-read.
- Dry runs never write, so they are always safe to use first.

CLI usage via `run_skill_script`:

```python
run_skill_script(
    "file-editing",
    "scripts/edit_tools.py",
    ["--path", "artifacts/config.yaml", "--old-str", "retries: 1", "--new-str", "retries: 3", "--dry-run"],
)
```

Read `references/safe-editing-patterns.md` for multi-hunk staging and verification practices.

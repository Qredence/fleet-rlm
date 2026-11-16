# Safe file-editing patterns

## 1. Read exact context before editing

Before constructing `old_str`:
- Read the exact surrounding lines (including indentation and blank lines) from the target file.
- Include enough surrounding lines in `old_str` that it matches **once and only once**; `edit_unique` rejects both 0 matches and ambiguous multi-matches with an explicit occurrence count.

## 2. Batch related changes with `apply_edits`

When multiple non-adjacent blocks in one file must change together:

```python
edit = load_skill_module("file-editing")
res = edit.apply_edits(
    "artifacts/module.py",
    [
        ("DEFAULT_LIMIT = 10", "DEFAULT_LIMIT = 25"),
        {"old_str": "return items[:10]", "new_str": "return items[:DEFAULT_LIMIT]"},
    ],
    dry_run=True,
)
print(res["diff"])
```

All edits are validated sequentially in memory first: if edit #2 fails to match uniquely, disk state remains completely untouched.

## 3. Verify after writing

1. Print `res["diff"]` or run syntax/lint checks (`py_compile`, `ruff`, or tests) on the modified file before `SUBMIT`.
2. Use `preview_diff(path, new_content)` whenever constructing an entire updated text buffer in Python so you can inspect the diff before overwriting.

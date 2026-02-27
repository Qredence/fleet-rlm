#!/usr/bin/env python3
"""Release hygiene checks for secret/config safety."""

from __future__ import annotations

import re
import subprocess
import sys


ENV_EXAMPLE_SUFFIXES = (".env.example",)
ENV_ALLOWED_EXACT = {".env.example", "src/frontend/.env.example"}
FORBIDDEN_TRACKED_ENV_PATTERN = re.compile(r"(^|/)\.env(\..+)?$")
FORBIDDEN_TRACKED_TMP_PATTERNS = (
    re.compile(r"\.tmp$", re.IGNORECASE),
    re.compile(r"(^|/)\.tmp($|[/_-])"),
    re.compile(r"(^|/)tmp($|/)"),
)
FORBIDDEN_TRACKED_MJS_PATTERN = re.compile(r"\.mjs$")
ALLOWED_TRACKED_MJS_EXACT = {"src/frontend/postcss.config.mjs"}


def git_ls_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out = proc.stdout.decode("utf-8", errors="replace")
    return [path for path in out.split("\0") if path]


def is_allowed_env_example(path: str) -> bool:
    if path in ENV_ALLOWED_EXACT:
        return True
    return path.endswith(ENV_EXAMPLE_SUFFIXES)


def is_forbidden_tmp_path(path: str) -> bool:
    return any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_TMP_PATTERNS)


def main() -> int:
    tracked = git_ls_files()
    forbidden_env = [
        path
        for path in tracked
        if FORBIDDEN_TRACKED_ENV_PATTERN.search(path)
        and not is_allowed_env_example(path)
    ]
    forbidden_tmp = [path for path in tracked if is_forbidden_tmp_path(path)]
    forbidden_mjs = [
        path
        for path in tracked
        if FORBIDDEN_TRACKED_MJS_PATTERN.search(path)
        and path not in ALLOWED_TRACKED_MJS_EXACT
    ]

    has_errors = False
    if forbidden_env:
        has_errors = True
        print("ERROR: Forbidden tracked env file(s) found:")
        for path in sorted(forbidden_env):
            print(f"  - {path}")
        print("\nOnly .env.example files are allowed in git.")

    if forbidden_tmp:
        has_errors = True
        if forbidden_env:
            print()
        print("ERROR: Forbidden tracked temp file(s) found:")
        for path in sorted(forbidden_tmp):
            print(f"  - {path}")
        print("\nTemporary artifacts (*.tmp, .tmp*, tmp/) must not be tracked.")

    if forbidden_mjs:
        has_errors = True
        if forbidden_env or forbidden_tmp:
            print()
        print("ERROR: Forbidden tracked .mjs file(s) found:")
        for path in sorted(forbidden_mjs):
            print(f"  - {path}")
        print("\nOnly allowlisted .mjs files may be tracked.")
        print("Allowlisted paths:")
        for path in sorted(ALLOWED_TRACKED_MJS_EXACT):
            print(f"  - {path}")

    if has_errors:
        return 1

    print("OK: Release hygiene checks passed (.env/.tmp/.mjs policies).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

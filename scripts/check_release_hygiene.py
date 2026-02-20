#!/usr/bin/env python3
"""Release hygiene checks for secret/config safety."""

from __future__ import annotations

import re
import subprocess
import sys


ENV_EXAMPLE_SUFFIXES = (".env.example",)
ENV_ALLOWED_EXACT = {".env.example", "src/frontend/.env.example"}
FORBIDDEN_TRACKED_ENV_PATTERN = re.compile(r"(^|/)\.env(\..+)?$")


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


def main() -> int:
    tracked = git_ls_files()
    forbidden = [
        path
        for path in tracked
        if FORBIDDEN_TRACKED_ENV_PATTERN.search(path)
        and not is_allowed_env_example(path)
    ]

    if forbidden:
        print("ERROR: Forbidden tracked env file(s) found:")
        for path in sorted(forbidden):
            print(f"  - {path}")
        print("\nOnly .env.example files are allowed in git.")
        return 1

    print("OK: No forbidden tracked .env files detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Check the working tree against its merge base or an explicitly selected baseline."""

import ast
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

SRC = Path("backend/src/fleet_rlm")
OWNERS = {
    "fastapi": "api.py",
    "daytona": "interpreter.py",
    "boto3": "storage.py",
    "botocore": "storage.py",
}
MOVED_API_FUNCTIONS = {
    "_prepare_turn": Path("http/chat.py"),
    "_execute_turn": Path("http/chat.py"),
    "update_optimization_provider": Path("http/providers.py"),
    "_resolve_role_credentials": Path("http/optimizations.py"),
    "create_optimization_job": Path("http/optimizations.py"),
    "promote_optimization_job": Path("http/optimizations.py"),
}


def scores(root: Path) -> dict[tuple[Path, str], int]:
    result = subprocess.run(
        [
            "ruff",
            "check",
            "--isolated",
            "--target-version",
            "py313",
            "--select",
            "C901",
            "--ignore-noqa",
            "--output-format",
            "json",
            "--exit-zero",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    scores_by_name = {}
    for item in json.loads(result.stdout):
        path = Path(item["filename"])
        row = item["location"]["row"]
        name = item["message"].split("`")[1]
        nested = [
            node
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.lineno <= row <= node.end_lineno
        ]
        qualified = ".".join(
            node.name for node in sorted(nested, key=lambda node: node.lineno)
        )
        if qualified.split(".")[-1] != name:
            raise ValueError(f"Cannot locate {name} at {path}:{row}")
        scores_by_name[path.resolve().relative_to(root.resolve()), qualified] = int(
            item["message"].split("(")[-1].split()[0]
        )
    return scores_by_name


def check_complexity(base: str):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        names = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", base, str(SRC)], text=True
        )
        for name in names.splitlines():
            path = Path(name)
            if path.suffix == ".py":
                dest = root / path.relative_to(SRC)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(
                    subprocess.check_output(["git", "show", f"{base}:{name}"])
                )
        previous = scores(root)
        for name, path in MOVED_API_FUNCTIONS.items():
            original_score = previous.get((Path("api.py"), name))
            if original_score is not None and not (root / path).exists():
                previous[path, name] = original_score
    for key, current in scores(SRC).items():
        limit = previous.get(key, 10)
        if current > limit:
            yield f"{key[0]} {key[1]} complexity {current} exceeds {limit}"


def check_files():
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            for module in modules:
                library = module.split(".")[0]
                owner = OWNERS.get(library)
                if owner is None or path == SRC / owner:
                    continue
                if library == "fastapi":
                    if path.is_relative_to(SRC / "http"):
                        continue
                    owner = "api.py or http/"
                yield f"{path}:{node.lineno} {module} belongs in {owner}"
    markdown = subprocess.check_output(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "README.md",
            "AGENTS.md",
            "GLOSSARY.md",
            "frontend/README.md",
            "docs/*.md",
        ]
    )
    for name in markdown.split(b"\0")[:-1]:
        path = Path(os.fsdecode(name))
        if not path.is_file():
            continue
        for target in re.findall(r"\]\(([^()\s]+)\)", path.read_text()):
            url = urlsplit(target)
            if (
                not url.scheme
                and not url.netloc
                and url.path
                and not (path.parent / unquote(url.path)).exists()
            ):
                yield f"{path}: missing {target}"
    for path in Path("scripts").glob("*.py"):
        compile(path.read_bytes(), str(path), "exec")


def complexity_base() -> str:
    if base := os.environ.get("FLEET_HYGIENE_BASE"):
        return base
    result = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/main"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "HEAD"


if __name__ == "__main__":
    problems = [*check_files(), *check_complexity(complexity_base())]
    if problems:
        raise SystemExit("\n".join(problems))

#!/usr/bin/env bash
set -euo pipefail

test "$#" -eq 1
smoke_dir=$(mktemp -d)
trap 'rm -rf "$smoke_dir"' EXIT
uv venv --python 3.13 "$smoke_dir"
smoke_python="$smoke_dir/bin/python"
uv pip install --python "$smoke_python" --index-url https://pypi.org/simple "$1"
uv pip check --python "$smoke_python"
cd "$smoke_dir"
"$smoke_python" -I - <<'PY'
import os
from importlib.metadata import version

from fleet_rlm import optimize
from fleet_rlm.api import app
from fleet_rlm.config import Settings
from fleet_rlm.opt_runner import resolve_eval_datasets_dir
from fleet_rlm.optimize import load_memory_dataset, load_rlm_dataset, load_skills_dataset
from fleet_rlm.skills import SkillRegistry

if release_version := os.environ.get("FLEET_RELEASE_VERSION"):
    assert version("fleet-rlm") == release_version
settings = Settings.model_construct()
registry = SkillRegistry.load(settings.skills_dir, fallback_to_bundled=True)
expected_skills = {
    "chart-visualization", "data-analysis", "document-extraction", "recursive-synthesis",
    "scientific-computing", "skill-creator", "spreadsheet-audit", "web-research",
}
assert expected_skills <= {skill.name for skill in registry.skills}
datasets = resolve_eval_datasets_dir(settings)
assert load_skills_dataset(datasets / "skills.jsonl", registry)
assert load_memory_dataset(datasets / "memory.jsonl")
examples = load_rlm_dataset(datasets / "rlm.jsonl", settings=settings, registry=registry)
specs, _, _ = optimize._build_case_spec_tables(examples)
fixture = next(spec for spec in specs.values() if spec.fixture_id)
files, web = optimize._resolve_case_fixtures(fixture)
assert files or web
assert "/healthz" in app.openapi()["paths"]
PY

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from scripts import check_docs_quality


def _write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    docs = repo / "docs"
    _write(repo / "openapi.yaml", "paths:\n  /health:\n    get: {}\n")
    _write(
        docs / "index.md",
        "# Docs\n\n- [Reference](reference/index.md)\n",
    )
    _write(
        docs / "reference/index.md",
        "# Ref\n\n- [API](http-api.md)\n",
    )
    _write(docs / "reference/http-api.md", "# API\n")
    return repo


def test_run_checks_ok_without_contract_validation(tmp_path: pathlib.Path) -> None:
    repo = _base_repo(tmp_path)
    errors = check_docs_quality.run_checks(repo, include_contract_checks=False)
    assert errors == []


def test_detects_broken_internal_link(tmp_path: pathlib.Path) -> None:
    repo = _base_repo(tmp_path)
    docs = repo / "docs"
    _write(docs / "reference/index.md", "# Ref\n\n- [Missing](missing.md)\n")

    errors = check_docs_quality.run_checks(repo, include_contract_checks=False)
    assert any("broken link" in err for err in errors)


def test_detects_orphan_active_doc(tmp_path: pathlib.Path) -> None:
    repo = _base_repo(tmp_path)
    docs = repo / "docs"
    _write(docs / "tutorials/extra.md", "# Extra\n")

    errors = check_docs_quality.run_checks(repo, include_contract_checks=False)
    assert any("orphan active doc" in err for err in errors)


def test_detects_banned_file_scheme(tmp_path: pathlib.Path) -> None:
    repo = _base_repo(tmp_path)
    docs = repo / "docs"
    _write(docs / "reference/http-api.md", "# API\nfile://tmp/legacy\n")

    errors = check_docs_quality.run_checks(repo, include_contract_checks=False)
    assert any("banned link scheme" in err for err in errors)


def test_detects_legacy_docs_directory(tmp_path: pathlib.Path) -> None:
    repo = _base_repo(tmp_path)
    docs = repo / "docs"
    (docs / "artifacts").mkdir(parents=True, exist_ok=True)

    errors = check_docs_quality.run_checks(repo, include_contract_checks=False)
    assert any("legacy docs directory still present" in err for err in errors)


def test_detects_legacy_explanation_marker(tmp_path: pathlib.Path) -> None:
    repo = _base_repo(tmp_path)
    docs = repo / "docs"
    _write(docs / "explanation/README.md", "# Legacy\n")

    errors = check_docs_quality.run_checks(repo, include_contract_checks=False)
    assert any("legacy explanation artifact still present" in err for err in errors)

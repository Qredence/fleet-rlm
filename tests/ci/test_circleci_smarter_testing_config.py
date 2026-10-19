from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_smarter_testing_discovers_pytest_files_and_emits_junit_and_lcov() -> None:
    suite = yaml.safe_load((ROOT / ".circleci/test-suites.yml").read_text())

    assert suite["name"] == "ci tests"
    assert "find tests -type f -name 'test_*.py'" in suite["discover"]
    assert "<< test.atoms >>" in suite["run"]
    assert "<< outputs.junit >>" in suite["run"]
    assert "<< outputs.circleci-list >>" in suite["file-mapper"]
    assert "<< outputs.lcov >>" in suite["analysis"]
    assert suite["outputs"]["junit"] == "test-results/junit.xml"


def test_smarter_testing_uses_paid_features_with_safe_full_suite_fallbacks() -> None:
    suite = yaml.safe_load((ROOT / ".circleci/test-suites.yml").read_text())

    assert suite["options"]["test-impact-analysis"] is True
    assert suite["options"]["dynamic-test-splitting"] is True
    assert suite["options"]["max-auto-rerun"] == 1
    assert {
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "tests/**",
        ".circleci/**",
    }.issubset(suite["options"]["full-test-run-paths"])


def test_quality_aggregates_both_full_matrix_and_smarter_testing_jobs() -> None:
    config = yaml.safe_load((ROOT / ".circleci/config.yml").read_text())
    jobs = config["workflows"]["required-ci"]["jobs"]
    quality = next(job["quality-gate"] for job in jobs if isinstance(job, dict) and "quality-gate" in job)

    assert quality["name"] == "quality"
    assert set(quality["requires"]) >= {
        "static-checks",
        "test-python-3.11",
        "test-python-312",
        "test-python-3.13",
        "package-check",
        "security",
    }
    assert config["experimental"]["dynamic-parallelism"]["test-python-312"] == {
        "key": "ci-tests-parallelism-key",
        "min": 1,
        "max": 2,
    }

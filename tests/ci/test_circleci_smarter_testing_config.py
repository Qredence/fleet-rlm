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


def test_pypi_release_requires_webhook_preflight_and_canary_validation() -> None:
    config = yaml.safe_load((ROOT / ".circleci/config.yml").read_text())
    assert config["parameters"]["skip_testpypi"] == {"type": "boolean", "default": False}
    workflow_jobs = config["workflows"]["required-ci"]["jobs"]
    by_name = {
        name: options
        for item in workflow_jobs
        for name, options in (item.items() if isinstance(item, dict) else [(item, {})])
    }

    assert "validate-pypi-webhook-context" in by_name["release-preflight"]["requires"]
    assert "quality" in by_name["release-preflight"]["requires"]
    assert by_name["pypi-canary"]["context"] == "fleet-pypi-validation-webhook"
    assert by_name["pypi-canary"]["requires"] == ["publish-pypi"]
    assert set(by_name["github-release"]["requires"]) == {"release-validation", "pypi-canary"}

    webhook_guard = config["jobs"]["validate-pypi-webhook-context"]
    assert webhook_guard["docker"][0]["image"] == "cimg/base:current"
    guard_command = webhook_guard["steps"][0]["run"]["command"]
    assert '"${CIRCLECI_VALIDATION_WEBHOOK_TOKEN:-}"' in guard_command

    validation = config["jobs"]["release-validation"]
    assert validation["type"] == "release"
    assert validation["plan_name"] == "fleet-rlm-pypi"
    assert validation["validation"]["auto_rollback_on_failure"] is False
    assert validation["validation"]["webhooks"][0]["name"] == "pypi_canary"
    assert validation["validation"]["webhooks"][0]["provider"] == "custom"

    publish_command = config["jobs"]["publish-pypi"]["steps"][1]["run"]["command"]
    assert "circleci run release plan fleet-rlm-pypi" in publish_command
    assert "--environment-name=pypi" in publish_command
    assert "--component-name=fleet-rlm" in publish_command
    assert '--target-version="$FLEET_RELEASE_VERSION"' in publish_command

    testpypi_publish = config["jobs"]["publish-testpypi"]
    assert testpypi_publish["environment"]["FLEET_SKIP_TESTPYPI"] == "<< pipeline.parameters.skip_testpypi >>"
    assert '"$FLEET_SKIP_TESTPYPI" = "true"' in testpypi_publish["steps"][1]["run"]["command"]

    testpypi_command = config["jobs"]["smoke-test-testpypi"]["steps"][4]["run"]["command"]
    assert '"$FLEET_SKIP_TESTPYPI" = "true"' in testpypi_command
    assert "https://test.pypi.org/simple" in testpypi_command
    assert "PIP_CONFIG_FILE=/dev/null" in testpypi_command
    assert "PIP_EXTRA_INDEX_URL=" in testpypi_command
    assert "--no-cache-dir" in testpypi_command

    canary_command = "\n".join(
        step["run"]["command"]
        for step in config["jobs"]["pypi-canary"]["steps"]
        if isinstance(step, dict) and "run" in step
    )
    assert "pypi-canary" in canary_command

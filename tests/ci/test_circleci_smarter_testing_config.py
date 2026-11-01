"""Behavioral and graph contracts for offline CI and package publication."""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_config():
    return yaml.safe_load((ROOT / ".circleci/config.yml").read_text())


def workflow_jobs():
    result = {}
    for item in load_config()["workflows"]["required-ci"]["jobs"]:
        if isinstance(item, str):
            result[item] = {}
        else:
            job, options = next(iter(item.items()))
            result[options.get("name", job)] = options
    return result


def test_smarter_testing_uses_file_atoms_and_per_atom_lcov_analysis():
    suite = yaml.safe_load((ROOT / ".circleci/test-suites.yml").read_text())
    atoms = subprocess.check_output(["bash", "-ec", suite["discover"]], cwd=ROOT, text=True).splitlines()
    assert atoms == sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py"))
    assert "file-mapper" not in suite
    assert "<< outputs.lcov >>" in suite["analysis"]
    assert "coverage run" in suite["analysis"]
    assert "coverage lcov --fail-under=0" in suite["analysis"]
    assert "junit_family=xunit1" in suite["run"]
    assert suite["options"]["impact-key"] == "fleet-ci-files-lcov-v1"
    assert suite["options"]["test-impact-analysis"] is True
    assert suite["options"]["dynamic-test-splitting"] is True
    assert suite["options"]["max-auto-rerun"] == 1
    assert {
        "pyproject.toml",
        "uv.lock",
        "Makefile",
        "tests/**",
        ".circleci/**",
        "config/**",
        "scripts/**",
        "openapi.yaml",
        "src/fleet_rlm/bundled_skills/**",
        "src/fleet_rlm/sandbox/snapshot-requirements.txt",
    } <= set(suite["options"]["full-test-run-paths"])
    for command in (suite["run"], suite["analysis"]):
        # Empty selection must not accidentally turn into pytest's full-suite discovery.
        subprocess.run(["bash", "-euc", command.replace("<< test.atoms >>", "")], cwd=ROOT, check=True)


def test_quality_and_release_gates_remain_required():
    config = load_config()
    jobs = workflow_jobs()
    assert set(jobs["quality"]["requires"]) == {
        "static-checks",
        "test-python-3.11",
        "coverage-report",
        "test-python-312",
        "test-python-3.13",
        "package-check",
        "security",
        "sonarqube",
    }
    assert config["jobs"]["sonarqube"]["steps"][0] == {"checkout": {"method": "full"}}
    assert config["experimental"]["dynamic-parallelism"]["test-python-312"] == {
        "key": "ci-tests-parallelism-key",
        "min": 1,
        "max": 2,
    }
    assert set(jobs["release-preflight"]["requires"]) == {
        "quality",
        "validate-release-request",
        "validate-pypi-webhook-context",
    }
    assert set(jobs["github-release"]["requires"]) == {"release-validation", "pypi-canary"}
    assert set(jobs["github-release-recovery"]["requires"]) == {"release-validation-recovery", "pypi-canary-recovery"}
    assert jobs["github-release-recovery"]["name"] == "github-release-recovery"
    assert "github-release-recovery" not in config["jobs"]
    assert config["parameters"]["skip_testpypi"]["default"] is False


def test_shared_pypi_validation_and_cancellation_contracts():
    config = load_config()
    validation = config["jobs"]["release-validation"]["validation"]
    assert validation == config["jobs"]["release-validation-recovery"]["validation"]
    assert validation["enabled"] is True
    assert validation["evaluation_time"] == "20m"
    assert validation["auto_rollback_on_failure"] is False
    check = validation["webhooks"][0]
    assert check["name"] == "pypi_canary"
    assert check["provider"] == "custom"
    assert check["data_points"]["project_id"] == "request.project_id"
    assert check["max_failures"] == 1
    jobs = workflow_jobs()
    assert jobs["cancel-pypi-publication"]["requires"] == [{"publish-pypi": ["canceled"]}]
    assert jobs["cancel-pypi-canary"]["requires"] == [{"pypi-canary": ["canceled"]}]
    assert jobs["cancel-pypi-canary-recovery"]["requires"] == [{"pypi-canary-recovery": ["canceled"]}]
    assert "--status=CANCELED" in "\n".join(
        step.get("run", {}).get("command", "")
        for step in config["jobs"]["cancel-pypi-deployment"]["steps"]
        if isinstance(step, dict)
    )
    steps = config["jobs"]["pypi-canary"]["steps"]
    run = next(step["run"] for step in steps if isinstance(step, dict) and "run" in step)
    assert "timeout --signal=TERM --kill-after=30s 15m" in run["command"]
    assert "canary-with-receipt" in run["command"]
    assert any("store_artifacts" in step for step in run["teardown"])
    assert jobs["fail-pypi-canary"]["requires"] == [{"pypi-canary": ["failed"]}]
    assert steps[-1]["persist_to_workspace"]["paths"] == ["canary-results/receipt.json"]
    assert jobs["github-release"]["context"] == "fleet-github-release"
    assert jobs["pypi-canary"]["context"] == "fleet-pypi-validation-webhook"
    assert jobs["publish-pypi"]["context"] == "fleet-pypi-upload-token"


def test_validation_acceptance_fixture_is_isolated_and_matches_production_policy():
    production = load_config()["jobs"]["release-validation"]["validation"]
    fixture = yaml.safe_load((ROOT / ".circleci/validation-acceptance.yml").read_text())
    validation = fixture["jobs"]["acceptance-validation"]["validation"]
    for key in ("enabled", "evaluation_time", "auto_rollback_on_failure", "webhooks"):
        assert validation[key] == production[key]
    assert fixture["jobs"]["acceptance-validation"]["plan_name"] == "fleet-rlm-validation-acceptance"
    assert "pypi-validation-acceptance" in "\n".join(str(step) for step in fixture["jobs"]["acceptance-plan"]["steps"])
    assert not any("publish-pypi" in name or "github-release" in name for name in fixture["jobs"])
    workflows = fixture["workflows"]
    assert "downstream-sentinel" in str(workflows["failure-acceptance"])
    cancel_job = next(
        item["acceptance-cancel-marker"]
        for item in workflows["cancellation-acceptance"]["jobs"]
        if isinstance(item, dict) and "acceptance-cancel-marker" in item
    )
    assert cancel_job["requires"] == [{"acceptance-cancellable": ["canceled"]}]


def test_release_request_treats_shell_syntax_as_literal(tmp_path):
    job = load_config()["jobs"]["validate-release-request"]
    command = job["steps"][0]["run"]["command"]
    for parameter in ("run_release", "run_python314", "run_pypi_recovery"):
        command = command.replace(
            f"<< pipeline.parameters.{parameter} >>", "true" if parameter == "run_release" else "false"
        )
    marker = tmp_path / "injected"
    env = {
        **os.environ,
        "CIRCLE_BRANCH": "main",
        "FLEET_RELEASE_VERSION": f"$(touch {marker})",
        "FLEET_RELEASE_SHA": "a" * 40,
    }
    subprocess.run(["bash", "-euc", command], env=env, check=True)
    assert not marker.exists()
    assert "<< pipeline.parameters.release_version >>" not in command
    assert "<< pipeline.parameters.release_sha >>" not in command


def test_coverage_xml_survives_test_or_threshold_failure(tmp_path):
    config = load_config()
    steps = config["jobs"]["coverage-report"]["steps"]
    reporter = next(step["run"] for step in steps if isinstance(step, dict) and "run" in step)
    binary = tmp_path / "uv"
    binary.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        '  *" combine "*) touch coverage/.coverage; printf combine >> "$ORDER" ;;\n'
        '  *" xml "*) touch coverage/coverage.xml; printf xml >> "$ORDER" ;;\n'
        '  *" report "*) printf report >> "$ORDER"; exit 2 ;;\n'
        "  *) exit 3 ;;\n"
        "esac\n"
    )
    binary.chmod(0o755)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_sample.py").write_text("def test_sample(): pass\n")
    for node in (0, 1):
        shard = tmp_path / f"coverage-shards/node-{node}"
        shard.mkdir(parents=True)
        (shard / "test-status").write_text("0\n")
        (shard / "test-files.txt").write_text("tests/test_sample.py\n")
        (shard / ".coverage.fake").write_text("parallel data\n")
    order = tmp_path / "coverage-command-order"
    result = subprocess.run(
        ["bash", "-ec", reporter["command"]],
        cwd=tmp_path,
        # CircleCI's shell startup file prepends the real uv installation to PATH.
        env={
            **os.environ,
            "BASH_ENV": "",
            "ORDER": str(order),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
        check=False,
    )
    assert result.returncode == 2
    assert (tmp_path / "coverage/coverage.xml").is_file()
    coverage_upload = {"store_artifacts": {"path": "coverage", "destination": "coverage"}}
    assert coverage_upload in reporter["teardown"]
    assert order.read_text() == "combinexmlreport"
    assert steps.index(next(step for step in steps if "persist_to_workspace" in step)) > steps.index(
        next(step for step in steps if isinstance(step, dict) and "run" in step)
    )
    assert workflow_jobs()["coverage-report"]["requires"] == [{"test-python-3.11": "terminal"}]


def test_python_selection_survives_circleci_step_shells(tmp_path):
    config = load_config()
    steps = config["commands"]["setup-python"]["steps"]
    commands = [step["run"] for step in steps if isinstance(step, dict) and "run" in step]
    install = next(step["command"] for step in commands if step["name"] == "Install locked project dependencies")
    verify = next(step["command"] for step in commands if step["name"] == "Verify selected Python interpreter")
    binary = tmp_path / "uv"
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$UV_PYTHON" >> "$RECORD"\n')
    binary.chmod(0o755)
    for version in ("3.11", "3.12", "3.13"):
        record = tmp_path / f"record-{version}"
        env = {
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "BASH_ENV": str(tmp_path / f"env-{version}"),
            "RECORD": str(record),
        }
        env.pop("UV_PYTHON", None)
        for command in (install, verify):
            subprocess.run(
                ["bash", "-eo", "pipefail", "-c", command.replace("<< parameters.python-version >>", version)],
                env=env,
                check=True,
            )
        assert record.read_text().splitlines() == [version, version]
    chunk = yaml.safe_load((ROOT / ".circleci/cci-agent-setup.yml").read_text())
    assert "export UV_PYTHON=3.12" in chunk["jobs"]["cci-agent-setup"]["steps"][3]["run"]["command"]


def test_discovery_does_not_install_project_dependencies():
    steps = load_config()["jobs"]["list-smarter-tests"]["steps"]
    assert steps[0] == "checkout"
    assert len(steps) == 2
    assert steps[1]["run"]["command"] == 'circleci testsuite list-tests "ci tests" --key=ci-tests-parallelism-key'

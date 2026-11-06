"""Behavioral and graph contracts for offline CI and package publication."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_config():
    return yaml.safe_load((ROOT / ".circleci/config.yml").read_text())


def workflow_jobs():
    result = {}
    for item in load_config()["workflows"]["required-ci"]["jobs"] + load_config()["job-groups"]["publication"]["jobs"]:
        if isinstance(item, str):
            result[item] = {}
        else:
            job, options = next(iter(item.items()))
            result[options.get("name", job)] = options
    return result


def test_smarter_testing_uses_file_atoms_and_per_atom_lcov_analysis():
    suite = yaml.safe_load((ROOT / ".circleci/test-suites.yml").read_text())
    atoms = subprocess.check_output(["bash", "-ec", suite["discover"]], cwd=ROOT, text=True).splitlines()
    # Some environments print a startup banner to stdout (e.g., Node version).
    # Filter to the expected test file paths to keep this test robust.
    atoms = [line for line in atoms if line.startswith("tests/")]
    assert atoms == sorted(str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py"))
    assert "file-mapper" not in suite
    assert "<< outputs.lcov >>" in suite["analysis"]
    assert "coverage run" in suite["analysis"]
    assert "coverage lcov --fail-under=0" in suite["analysis"]
    assert "junit_family=xunit1" in suite["run"]
    assert "<< outputs.junit-directory >>/junit.xml" in suite["run"]
    assert suite["outputs"]["junit-directory"] == "test-results/junit"
    assert "junit" not in suite["outputs"]
    for job_name in ("test-python", "test-python-312"):
        assert {"store_test_results": {"path": "test-results/junit"}} in load_config()["jobs"][job_name]["steps"]
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
    assert {item if isinstance(item, str) else next(iter(item)) for item in jobs["quality"]["requires"]} == {
        "release-metadata",
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
        "validate-release-request",
        "validate-pypi-webhook-context",
    }
    assert set(jobs["github-release"]["requires"]) == {"release-validation", "pypi-canary"}
    assert set(jobs["github-release-recovery"]["requires"]) == {"release-validation-recovery", "pypi-canary-recovery"}
    assert jobs["github-release-recovery"]["name"] == "github-release-recovery"
    assert "github-release-recovery" not in config["jobs"]
    assert config["parameters"]["skip_testpypi"]["default"] is True


def test_pypi_upload_does_not_require_system_python_pip(tmp_path):
    config = load_config()
    steps = config["jobs"]["publish-pypi"]["steps"]
    assert "install-uv" in steps
    command = next(step["run"]["command"] for step in steps if isinstance(step, dict) and "run" in step)
    dist = tmp_path / "dist"
    dist.mkdir()
    archive = dist / "fleet_rlm-0.10.0.tar.gz"
    archive.write_bytes(b"offline upload fixture")
    (dist / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "python": "#!/bin/sh\nexit 91\n",
        "uv": """#!/bin/sh
set -eu
echo "uv $*" >> "$UPLOAD_LOG"
if [ "$1" = venv ]; then
    mkdir -p "$4/bin"
    cat > "$4/bin/python" <<'SH'
#!/bin/sh
echo "isolated-python $*" >> "$UPLOAD_LOG"
SH
    chmod +x "$4/bin/python"
fi
""",
        "circleci": '#!/bin/sh\necho "circleci $*" >> "$UPLOAD_LOG"\n',
    }
    for name, script in scripts.items():
        executable = bin_dir / name
        executable.write_text(script)
        executable.chmod(0o755)
    log = tmp_path / "upload.log"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "UPLOAD_LOG": str(log)}
    env.pop("BASH_ENV", None)
    env.update(PYPI_API_TOKEN="offline-fixture", FLEET_RELEASE_VERSION="0.10.0")
    subprocess.run(["bash", "-euc", command], cwd=tmp_path, env=env, check=True)
    calls = log.read_text()
    assert "isolated-python -m scripts.release_checks plan-deployment" in calls
    assert "isolated-python -m twine upload --non-interactive" in calls
    assert calls.index("-m twine upload") < calls.index("circleci run release update")


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
    assert {"store_artifacts": {"path": "canary-results", "destination": "pypi-canary"}} in steps
    assert jobs["fail-pypi-canary"]["requires"] == [{"pypi-canary": ["failed"]}]
    assert steps[-1]["persist_to_workspace"]["paths"] == ["canary-results/receipt.json"]
    names = [step["run"]["name"] for step in steps if "run" in step]
    assert names.index("Enforce canary preparation result") < names.index(
        "Require matching canary evidence before promotion"
    )
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


@pytest.mark.parametrize("failed_command", ["none", "combine", "xml", "report"])
def test_coverage_xml_survives_test_or_threshold_failure(tmp_path, failed_command):
    config = load_config()
    steps = config["jobs"]["coverage-report"]["steps"]
    reporter = next(step["run"] for step in steps if isinstance(step, dict) and "run" in step)
    binary = tmp_path / "uv"
    binary.write_text(
        "#!/bin/sh\n"
        f'case " $* " in *" {failed_command} "*) exit 2 ;; esac\n'
        'case " $* " in\n'
        '  *" combine "*) touch coverage/.coverage; printf combine >> "$ORDER" ;;\n'
        '  *" xml "*) touch coverage/coverage.xml; printf xml >> "$ORDER" ;;\n'
        '  *" report "*) printf report >> "$ORDER" ;;\n'
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
    assert result.returncode == 0
    enforcement = steps[-1]["run"]["command"]
    env = {**os.environ, "BASH_ENV": ""}
    final = subprocess.run(["bash", "-ec", enforcement], cwd=tmp_path, env=env, check=False)
    assert final.returncode == (0 if failed_command == "none" else 2)
    assert (tmp_path / "coverage/coverage.xml").is_file() == (failed_command in ("none", "report"))
    coverage_upload = {"store_artifacts": {"path": "coverage", "destination": "coverage"}}
    assert coverage_upload in steps
    assert (order.read_text() if order.exists() else "") == {
        "none": "combinexmlreport",
        "combine": "",
        "xml": "combine",
        "report": "combinexml",
    }[failed_command]
    assert steps.index(coverage_upload) < len(steps) - 1
    (tmp_path / "coverage/.status").unlink()
    assert subprocess.run(["bash", "-ec", enforcement], cwd=tmp_path, env=env, check=False).returncode != 0
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


@pytest.mark.parametrize(
    ("job", "name", "status_path"),
    [
        ("test-python-312", "Preserve the Python 3.12 test result", "test-results/test-status"),
        ("pypi-canary", "Enforce canary preparation result", "canary-results/.status"),
    ],
)
@pytest.mark.parametrize("status", [None, 0, 1, 124])
def test_deferred_status_fails_closed(tmp_path, job, name, status_path, status):
    steps = load_config()["jobs"][job]["steps"]
    enforcement = next(step for step in steps if isinstance(step, dict) and step.get("run", {}).get("name") == name)
    upload = next(step for step in steps if "store_artifacts" in step)
    assert steps.index(upload) < steps.index(enforcement)
    if job == "pypi-canary":
        verify = next(
            step
            for step in steps
            if isinstance(step, dict)
            and step.get("run", {}).get("name") == "Require matching canary evidence before promotion"
        )
        persist = next(step for step in steps if "persist_to_workspace" in step)
        assert steps.index(enforcement) < steps.index(verify) < steps.index(persist)
    path = tmp_path / status_path
    if status is not None:
        path.parent.mkdir(parents=True)
        path.write_text(f"{status}\n")
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", enforcement["run"]["command"]],
        cwd=tmp_path,
        env={**os.environ, "BASH_ENV": ""},
        check=False,
    )
    assert result.returncode == (1 if status is None else status)


def test_publication_is_serialized_with_recovery_and_github_health_gates():
    config = load_config()
    invocations = config["workflows"]["required-ci"]["jobs"]
    publication = next(item["publication"] for item in invocations if isinstance(item, dict) and "publication" in item)
    assert publication["requires"] == ["quality"]
    assert publication["serial-group"] == "<< pipeline.project.slug >>/pypi-publication"
    jobs = workflow_jobs()
    assert jobs["publish-pypi"]["requires"] == ["github-deployment"]
    assert jobs["github-deployment"]["requires"] == ["smoke-test-testpypi"]
    assert "github-deployment-recovery" in jobs["plan-pypi-recovery"]["requires"]
    for phase in ("publish-pypi", "pypi-canary", "release-validation", "github-release"):
        assert jobs[f"report-{phase}-failure"]["requires"] == [{phase: ["failed", "canceled"]}]
    command = config["jobs"]["github-release"]["steps"][-1]["run"]["command"]
    assert command.index("scripts.github_release") < command.index("deployment-status")
    assert "--state success" in command


def test_dynamic_setup_is_staged_and_preserves_explicit_release_parameters():
    setup = yaml.safe_load((ROOT / ".circleci/setup.yml").read_text())
    assert setup["setup"] is True
    assert {k: v["type"] for k, v in setup["parameters"].items()} == {
        k: v["type"] for k, v in load_config()["parameters"].items()
    }
    assert setup["parameters"]["activate_auto_release"]["default"] is False
    assert setup["parameters"]["run_draft"]["default"] is True
    job = setup["jobs"]["resolve-release"]
    assert job["steps"][0] == {"checkout": {"method": "full"}}
    commands = "\n".join(step["run"]["command"] for step in job["steps"] if "run" in step)
    assert '"$CIRCLE_SHA1"' in commands
    assert "CIRCLE_CONTINUATION_KEY" in commands
    assert 'Path(".circleci/config.yml").read_text()' in commands


@pytest.mark.parametrize(
    ("event", "pull_request", "expected"),
    [
        ("push", "https://github.com/Qredence/fleet-rlm/pull/679", "pull_request"),
        ("push", "", "push"),
        ("api", "", "api"),
        ("scheduled_pipeline", "", "scheduled_pipeline"),
    ],
)
def test_dynamic_setup_preserves_pipeline_event_except_pull_requests(tmp_path, event, pull_request, expected):
    setup = yaml.safe_load((ROOT / ".circleci/setup.yml").read_text())
    job = setup["jobs"]["resolve-release"]
    assert job["environment"]["FLEET_EVENT"] == "<< pipeline.event.name >>"
    selection = job["steps"][1]["run"]["command"]
    assert "${CIRCLE_PULL_REQUEST:-}" in selection
    command = job["steps"][2]["run"]["command"].replace(r"\<<", "<<")
    for name, script in {
        "python": "#!/bin/sh\ncat >/dev/null\n",
        "python3.12": '#!/bin/sh\nprintf "%s\\n" "$FLEET_EVENT" > "$EVENT_RECORD"\n',
    }.items():
        binary = tmp_path / name
        binary.write_text(script)
        binary.chmod(0o755)
    record = tmp_path / "event"
    env = {
        **os.environ,
        "BASH_ENV": "",
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "EVENT_RECORD": str(record),
        "FLEET_EVENT": event,
        "FLEET_AUTO_RELEASE": "true",
        "FLEET_BRANCH": "main",
        "CIRCLE_SHA1": "a" * 40,
    }
    if pull_request:
        env["CIRCLE_PULL_REQUEST"] = pull_request
    else:
        env.pop("CIRCLE_PULL_REQUEST", None)
    subprocess.run(["bash", "-euc", command], cwd=tmp_path, env=env, check=True)
    assert record.read_text().strip() == expected

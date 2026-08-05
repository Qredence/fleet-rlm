from __future__ import annotations

import json
import subprocess

import pytest

from scripts import run_benchmarks
from scripts.performance_benchmarks import concurrency_stress, daytona_baseline


@pytest.mark.asyncio
async def test_baseline_io_and_local_concurrency_are_executable(tmp_path) -> None:
    io_results = await daytona_baseline.benchmark_io_latency(
        tmp_path / "io",
        file_sizes_kb=(1,),
        iterations=1,
    )
    assert io_results[1].write_times_ms
    assert io_results[1].read_times_ms

    stress = await concurrency_stress.simulate_concurrent_operations(
        num_sandboxes=2,
        operations_per_sandbox=2,
        test_dir=str(tmp_path / "stress"),
    )
    assert stress.completed_operations == 4
    assert stress.failed_operations == 0


def test_baseline_profiler_and_report_use_supported_apis(tmp_path) -> None:
    profile = daytona_baseline.profile_filesystem_operations(tmp_path, iterations=2)
    assert profile["top_hotspots"]
    assert profile["total_calls"] > 0

    output_path = tmp_path / "baseline.json"
    daytona_baseline.generate_baseline_report(
        {1: daytona_baseline.IOBenchmarkResult(1, [1.0], [2.0])},
        profile,
        output_path,
        {"status": "offline"},
        {"status": "offline"},
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["provisioning"]["status"] == "offline"
    assert report["concurrency_stress"]["status"] == "offline"


def test_stress_report_is_json_serializable(tmp_path) -> None:
    result = concurrency_stress.ConcurrencyTestResult(
        num_sandboxes=1,
        duration_seconds=0.1,
        completed_operations=1,
        throughput_ops_per_sec=10.0,
        contention_events=[{"count": 0}],
    )
    output_path = tmp_path / "stress.json"
    concurrency_stress.generate_stress_test_report({"one": result}, str(output_path))
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["results"]["one"]["completed_operations"] == 1


def test_benchmark_runner_returns_subprocess_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(7, ["uv", "run"])

    monkeypatch.setattr(run_benchmarks.subprocess, "run", fail)

    assert run_benchmarks.main() == 7
    assert "exit code 7" in capsys.readouterr().err

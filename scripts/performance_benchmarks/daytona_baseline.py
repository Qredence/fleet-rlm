"""Phase 1 Performance Baseline Measurement for Daytona Volumes.

This module provides benchmarking tools to establish performance baselines
before implementing optimizations in phases 2-5 of the Daytona efficiency
optimization plan.

Usage:
    uv run python scripts/performance_benchmarks/daytona_baseline.py

Metrics captured:
    - Volume I/O latency across different file sizes
    - Sandbox provisioning time
    - Concurrent sandbox resource usage
    - Filesystem operation hotspots
"""

import asyncio
import cProfile
import json
import pstats
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Any
from uuid import UUID

import aiofiles


@dataclass
class IOBenchmarkResult:
    """Results from I/O benchmark tests."""

    file_size_kb: int
    write_times_ms: list[float] = field(default_factory=list)
    read_times_ms: list[float] = field(default_factory=list)

    @property
    def write_latency_ms(self) -> float:
        """Mean write latency in milliseconds."""
        return mean(self.write_times_ms) if self.write_times_ms else 0.0

    @property
    def read_latency_ms(self) -> float:
        """Mean read latency in milliseconds."""
        return mean(self.read_times_ms) if self.read_times_ms else 0.0

    @property
    def write_stddev_ms(self) -> float:
        """Standard deviation of write latency (variability indicator)."""
        return stdev(self.write_times_ms) if len(self.write_times_ms) > 1 else 0.0

    @property
    def read_stddev_ms(self) -> float:
        """Standard deviation of read latency (variability indicator)."""
        return stdev(self.read_times_ms) if len(self.read_times_ms) > 1 else 0.0


async def benchmark_io_latency(
    test_dir: Path,
    file_sizes_kb: tuple[int, ...] = (1, 10, 100, 1000),
    iterations: int = 5,
) -> dict[int, IOBenchmarkResult]:
    """Benchmark read/write latency for various file sizes.

    Args:
        test_dir: Directory to use for test files
        file_sizes_kb: Tuple of file sizes in KB to test
        iterations: Number of repeat measurements per size

    Returns:
        Dictionary mapping file size (KB) to benchmark results
    """
    test_dir.mkdir(parents=True, exist_ok=True)
    results: dict[int, IOBenchmarkResult] = {}

    for size_kb in file_sizes_kb:
        print(f"\nBenchmarking {size_kb}KB files ({iterations} iterations)...")

        result = IOBenchmarkResult(file_size_kb=size_kb)

        # Create test data
        test_data = b"x" * (size_kb * 1024)

        for i in range(iterations):
            # Write benchmark
            write_file = test_dir / f"test_write_{size_kb}kb_{i}.tmp"
            start_time = time.perf_counter()
            async with aiofiles.open(write_file, "wb") as f:
                await f.write(test_data)
            write_time = (time.perf_counter() - start_time) * 1000  # ms
            result.write_times_ms.append(write_time)

            # Delete file for clean read test
            await asyncio.to_thread(write_file.unlink, missing_ok=True)

            # Read benchmark
            read_file = test_dir / f"test_read_{size_kb}kb_{i}.tmp"
            async with aiofiles.open(read_file, "wb") as f:
                await f.write(test_data)

            start_time = time.perf_counter()
            async with aiofiles.open(read_file, "rb") as f:
                _ = await f.read()
            read_time = (time.perf_counter() - start_time) * 1000  # ms
            result.read_times_ms.append(read_time)

            # Cleanup
            await asyncio.to_thread(read_file.unlink, missing_ok=True)

        results[size_kb] = result
        print(f"  Write: {result.write_latency_ms:.2f}ms ± {result.write_stddev_ms:.2f}ms")
        print(f"  Read:  {result.read_latency_ms:.2f}ms ± {result.read_stddev_ms:.2f}ms")

    return results


async def profile_sandbox_provisioning(_provisioning_tester: Any) -> dict[str, Any]:
    """Profile sandbox provisioning time breakdown.

    This should be integrated with actual Daytona sandbox creation
    to measure:
    - Volume mount preparation time
    - Sandbox runtime initialization time
    - First-access I/O latency
    - Total time to ready state

    For now, returns placeholder structure for integration later.
    """
    # TODO: Integrate with SandboxProvisioner.create() timing
    return {
        "total_provisioning_time_ms": None,
        "volume_mount_time_ms": None,
        "sandbox_init_time_ms": None,
        "ready_time_ms": None,
        "notes": "Requires integration with provisioning.py instrumentation",
    }


def profile_filesystem_operations(target_dir: Path, *, iterations: int = 1000) -> dict[str, Any]:
    """Profile filesystem operation patterns using Python profiler.

    Uses cProfile to identify hotspots in volume_paths.py and
    workspace_fs.py operations.

    Returns:
        Dictionary with profiling summary and hotspot analysis
    """
    del target_dir
    profiler = cProfile.Profile()
    profiler.enable()

    try:
        from fleet_rlm.files.volume_paths import VolumePaths

        # Profile path resolution under load
        paths = VolumePaths.from_mount()
        session_id = UUID("00000000-0000-0000-0000-000000000001")
        run_id = UUID("00000000-0000-0000-0000-000000000002")
        test_operations = [
            ("artifacts_root", paths.artifacts_root),
            ("attachments_root", paths.attachments_root),
            ("session_dir", lambda: paths.session_dir(session_id)),
            ("run_dir", lambda: paths.run_dir(session_id, run_id)),
            ("workspace_subpath", lambda: paths.session_workspace_dir(session_id)),
        ]

        for _name, op in test_operations:
            for _ in range(iterations):
                op()

    finally:
        profiler.disable()

    # Analyze results
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")

    # Extract top callers
    hotspots = []
    for func_name, call_stats in stats.stats.items():
        _primitive_calls, calls, total_time, cumulative_time, _callers = call_stats
        if calls > 0:
            avg_time = total_time / calls
            hotspots.append(
                {
                    "function": str(func_name),
                    "total_seconds": total_time,
                    "cumulative_seconds": cumulative_time,
                    "calls": calls,
                    "avg_per_call_ms": avg_time * 1000,
                }
            )

    hotspots.sort(key=lambda x: x["cumulative_seconds"], reverse=True)

    return {
        "top_hotspots": hotspots[:10],
        "total_calls": sum(h["calls"] for h in hotspots),
        "total_runtime_seconds": sum(h["total_seconds"] for h in hotspots),
    }


async def concurrency_stress_test(num_sandboxes: int = 10) -> dict[str, Any]:
    """Stress test concurrent sandbox operations.

    Spawns multiple simulated concurrent operations on shared volume
    to measure:
    - Resource contention under load
    - Memory overhead scaling
    - I/O queue saturation

    Returns resource usage metrics and throughput data.
    """
    # TODO: Integrate with actual Daytona sandbox creation
    # This is a placeholder for live backend stress testing

    return {
        "num_concurrent_sandboxes": num_sandboxes,
        "memory_overhead_per_sandbox_mb": None,
        "throughput_ops_per_second": None,
        "contention_events": None,
        "notes": "Requires integration with tests/live/backend/ infrastructure",
    }


def generate_baseline_report(
    io_results: dict[int, IOBenchmarkResult],
    fs_profile: dict[str, Any],
    output_path: Path,
    provisioning_profile: dict[str, Any] | None = None,
    stress_results: dict[str, Any] | None = None,
) -> None:
    """Generate comprehensive baseline report in JSON format."""

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "metric_system": "milliseconds, bytes, count",
        "io_benchmarks": {},
        "filesystem_profiling": fs_profile,
        "provisioning": provisioning_profile or {},
        "concurrency_stress": stress_results or {},
        "recommendations": [],
    }

    # Process I/O benchmarks
    for size_kb, result in io_results.items():
        report["io_benchmarks"][f"{size_kb}kb"] = {
            "write_latency_ms": round(result.write_latency_ms, 3),
            "write_stddev_ms": round(result.write_stddev_ms, 3),
            "read_latency_ms": round(result.read_latency_ms, 3),
            "read_stddev_ms": round(result.read_stddev_ms, 3),
            "samples": len(result.write_times_ms),
        }

    # Add optimization recommendations based on findings
    if io_results:
        max_write = max(r.write_latency_ms for r in io_results.values())
        max_read = max(r.read_latency_ms for r in io_results.values())

        if max_write > 100 or max_read > 100:
            report["recommendations"].append("HIGH I/O LATENCY: Consider caching layer for frequent operations")
        if any(r.read_stddev_ms > r.read_latency_ms * 0.3 for r in io_results.values()):
            report["recommendations"].append(
                "HIGH READ VARIABILITY: Investigate I/O queue depth and mount configuration"
            )

    # Write report
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"\nBaseline report saved to: {output_path}")


def main() -> int:
    """Run all baseline measurements and generate report."""

    print("=" * 60)
    print("DAYTONA VOLUME PERFORMANCE BASELINE MEASUREMENTS")
    print("=" * 60)

    # Setup test directory
    test_dir = Path(".scratch") / f"benchmark-{time.strftime('%Y%m%d_%H%M%S')}"
    test_dir.mkdir(parents=True, exist_ok=True)

    # Run I/O benchmarks
    print("\n[1/4] Running I/O latency benchmarks...")
    io_results = asyncio.run(benchmark_io_latency(test_dir))

    # Profile filesystem operations
    print("\n[2/4] Profiling filesystem operation patterns...")
    fs_profile = profile_filesystem_operations(Path("src"))

    # Profile sandbox provisioning (requires live Daytona env)
    print("\n[3/4] Profiling sandbox provisioning...")
    provisioning_profile = asyncio.run(profile_sandbox_provisioning(None))

    # Concurrency stress test (requires live environment)
    print("\n[4/4] Running concurrency stress test...")
    stress_results = asyncio.run(concurrency_stress_test(10))

    # Generate report
    output_path = test_dir / "baseline_report.json"
    generate_baseline_report(io_results, fs_profile, output_path, provisioning_profile, stress_results)

    print(f"\n{'=' * 60}")
    print("BASELINE MEASUREMENTS COMPLETE")
    print(f"{'=' * 60}")
    print(f"Test directory: {test_dir}")
    print(f"Full report: {output_path}")

    # Print summary
    print("\nSUMMARY:")
    for size_kb, result in io_results.items():
        print(f"  {size_kb}KB: W={result.write_latency_ms:.2f}ms R={result.read_latency_ms:.2f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

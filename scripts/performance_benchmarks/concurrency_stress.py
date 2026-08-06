"""Concurrent sandbox stress testing for Daytona efficiency optimization.

This module provides stress testing infrastructure for concurrent sandbox
operations, measuring resource contention, throughput, and scalability limits.

Usage:
    uv run python scripts/performance_benchmarks/concurrency_stress.py

Metrics captured:
    - Memory overhead per sandbox at scale
    - I/O queue saturation under concurrent load
    - Throughput degradation as sandbox count increases
    - Resource contention events and recovery
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

import aiofiles


@dataclass
class ConcurrencyTestResult:
    """Results from concurrent sandbox stress test."""

    num_sandboxes: int
    duration_seconds: float
    effective_concurrency: int = 0
    completed_operations: int = 0
    failed_operations: int = 0
    avg_memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    throughput_ops_per_sec: float = 0.0
    contention_events: list[dict[str, int]] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Calculate operation success rate."""
        total = self.completed_operations + self.failed_operations
        return (self.completed_operations / total * 100) if total > 0 else 0.0


def current_rss_mb() -> float | None:
    """Sample the current process resident set size in MB.

    Reads /proc/self/statm on Linux; returns None when live RSS sampling is
    unavailable (for example on macOS).
    """
    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            pages = int(handle.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)


def lifetime_peak_rss_mb() -> float:
    """Process lifetime high-water RSS in MB with platform-correct units.

    ru_maxrss is reported in bytes on macOS and in KiB on Linux. Returns 0.0
    on platforms without the resource module (for example Windows).
    """
    try:
        import resource
    except ImportError:
        return 0.0
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return max_rss / (1024 * 1024)
    return max_rss / 1024


async def simulate_concurrent_operations(
    num_sandboxes: int,
    operations_per_sandbox: int = 10,
    test_dir: str | None = None,
    max_concurrency: int | None = None,
) -> ConcurrencyTestResult:
    """Simulate concurrent file operations across multiple sandboxes.

    This creates a workload pattern similar to recursive DSPy calls where
    multiple child runtimes perform I/O on shared workspace volume paths.

    Args:
        num_sandboxes: Number of concurrent "sandbox" simulation tasks
        operations_per_sandbox: File I/O operations per sandbox
        test_dir: Directory to use for test files
        max_concurrency: Cap on simultaneously active workers; defaults to
            num_sandboxes so each requested scale runs at full concurrency

    Returns:
        Aggregated results from all simulated sandboxes
    """

    root = Path(test_dir) if test_dir is not None else Path(".scratch") / f"benchmark-concurrency-{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)

    effective_concurrency = num_sandboxes if max_concurrency is None else min(max_concurrency, num_sandboxes)

    start_time = time.perf_counter()

    completed = 0
    failed = 0
    contention_events = 0
    memory_samples_mb: list[float] = []

    async def sandbox_worker(sandbox_id: int):
        """Single sandbox simulation performing I/O operations."""
        nonlocal completed, failed, contention_events

        worker_start = time.perf_counter()

        for i in range(operations_per_sandbox):
            op_start = time.perf_counter()

            try:
                # Create test file path with sandbox-specific naming
                sandbox_dir = root / f"sandbox_{sandbox_id}"
                sandbox_dir.mkdir(parents=True, exist_ok=True)
                test_file_path = sandbox_dir / f"op_{i}.tmp"

                # Perform write operation
                async with aiofiles.open(test_file_path, "wb") as f:
                    await f.write(b"x" * 1024)  # 1KB writes

                # Perform read operation
                async with aiofiles.open(test_file_path, "rb") as f:
                    _ = await f.read()

                # Cleanup
                await asyncio.to_thread(test_file_path.unlink, missing_ok=True)
            except Exception as e:
                failed += 1
                print(f"Sandbox {sandbox_id} operation {i} failed: {e}", file=sys.stderr)
                continue

            op_duration = time.perf_counter() - op_start

            # Simulate contention detection (high latency spike)
            if op_duration > 0.5:  # 500ms threshold
                contention_events += 1

            completed += 1

            sample = current_rss_mb()
            if sample is not None:
                memory_samples_mb.append(sample)

        return time.perf_counter() - worker_start

    # Run concurrent workers
    semaphore = asyncio.Semaphore(effective_concurrency)

    async def limited_worker(sandbox_id: int):
        """Wrapper to limit concurrency via semaphore."""
        async with semaphore:
            return await sandbox_worker(sandbox_id)

    tasks = [limited_worker(sid) for sid in range(num_sandboxes)]
    await asyncio.gather(*tasks)

    duration = time.perf_counter() - start_time

    # Derive workload memory from live RSS samples; fall back to the process
    # lifetime high-water mark when live sampling is unavailable.
    if memory_samples_mb:
        avg_mem = mean(memory_samples_mb)
        peak_mem = max(memory_samples_mb)
    else:
        avg_mem = peak_mem = lifetime_peak_rss_mb()

    return ConcurrencyTestResult(
        num_sandboxes=num_sandboxes,
        duration_seconds=round(duration, 3),
        effective_concurrency=effective_concurrency,
        completed_operations=completed,
        failed_operations=failed,
        avg_memory_mb=round(avg_mem, 2),
        peak_memory_mb=round(peak_mem, 2),
        throughput_ops_per_sec=round(completed / duration, 2) if duration else 0.0,
        contention_events=[{"count": contention_events}],
    )


def analyze_contention_patterns(results: dict[str, ConcurrencyTestResult]) -> str:
    """Analyze contention patterns across different sandbox counts."""

    analysis = []

    for label, result in sorted(results.items()):
        num_sbs = result.num_sandboxes
        success_rate = result.success_rate
        contention_count = sum(event.get("count", 0) for event in result.contention_events)
        throughput = result.throughput_ops_per_sec

        issues = []
        if success_rate < 95:
            issues.append("LOW_SUCCESS_RATE")
        if throughput < 10:
            issues.append("LOW_THROUGHPUT")
        if contention_count > num_sbs * 5:
            issues.append("HIGH_CONTENTION")

        status = "OK" if not issues else ", ".join(issues)

        analysis.append(
            f"{label}: {status} | "
            f"Success={success_rate:.1f}% | "
            f"Throughput={throughput:.1f} ops/s | "
            f"Contention={contention_count}"
        )

    return "\n".join(analysis)


def generate_stress_test_report(
    test_results: dict[str, ConcurrencyTestResult],
    output_path: str,
) -> None:
    """Generate comprehensive stress test report."""

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "test_type": "concurrency_stress",
        "results": {},
        "summary": {},
        "recommendations": [],
    }

    for label, result in test_results.items():
        report["results"][label] = {
            "num_sandboxes": result.num_sandboxes,
            "effective_concurrency": result.effective_concurrency,
            "duration_seconds": result.duration_seconds,
            "completed_operations": result.completed_operations,
            "failed_operations": result.failed_operations,
            "success_rate_percent": round(result.success_rate, 2),
            "avg_memory_mb": result.avg_memory_mb,
            "peak_memory_mb": result.peak_memory_mb,
            "throughput_ops_per_sec": result.throughput_ops_per_sec,
            "contention_events": sum(event.get("count", 0) for event in result.contention_events),
        }

    # Add recommendations based on findings
    max_sbs_supported = None
    for _label, result in sorted(test_results.items(), key=lambda x: x[1].num_sandboxes):
        contention_count = sum(event.get("count", 0) for event in result.contention_events)
        if result.success_rate >= 95 and contention_count <= result.num_sandboxes * 5:
            max_sbs_supported = result.num_sandboxes
        else:
            break

    if max_sbs_supported:
        report["summary"]["max_recommended_concurrent_sandboxes"] = max_sbs_supported
        report["recommendations"].append(f"System stable up to {max_sbs_supported} concurrent sandboxes")
    else:
        report["recommendations"].append("Consider reducing expected concurrency or optimizing I/O patterns")

    if any(r.peak_memory_mb > 200 * r.num_sandboxes for r in test_results.values()):
        report["recommendations"].append(
            "HIGH_MEMORY_OVERHEAD: Per-sandbox memory exceeds 200MB; consider "
            "optimizing sandbox lifecycle or implementing sandbox reuse pool"
        )

    # Write report
    report_path = Path(output_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"\nStress test report saved to: {output_path}")


async def main() -> int:
    """Run concurrency stress tests at multiple scales."""

    print("=" * 60)
    print("DAYTONA CONCURRENT SANDBOX STRESS TESTS")
    print("=" * 60)

    # Test scaling configurations
    test_scales = {
        "low_load_5_sbs": 5,
        "medium_load_10_sbs": 10,
        "high_load_20_sbs": 20,
        "extreme_load_50_sbs": 50,
    }

    results: dict[str, ConcurrencyTestResult] = {}

    for name, num_sbs in test_scales.items():
        print(f"\nTesting {name} ({num_sbs} concurrent sandboxes)...")

        result = await simulate_concurrent_operations(
            num_sandboxes=num_sbs,
            operations_per_sandbox=10,
        )

        results[name] = result

        print(f"  Effective Concurrency: {result.effective_concurrency}")
        print(f"  Completed: {result.completed_operations} ops")
        print(f"  Success Rate: {result.success_rate:.1f}%")
        print(f"  Throughput: {result.throughput_ops_per_sec:.1f} ops/s")
        print(f"  Peak Memory: {result.peak_memory_mb:.1f} MB")
        print(f"  Contentions: {result.contention_events[0]['count'] if result.contention_events else 0}")

    # Generate report
    output_path = ".scratch/stress-test-report.json"
    generate_stress_test_report(results, output_path)

    # Print summary analysis
    print("\n" + "=" * 60)
    print("CONTENTION PATTERN ANALYSIS")
    print("=" * 60)
    print(analyze_contention_patterns(results))

    print(f"\n{'=' * 60}")
    print("STRESS TEST COMPLETE")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

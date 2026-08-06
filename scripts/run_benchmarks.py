"""Benchmark runner for Daytona efficiency optimization phases."""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Execute the baseline performance measurement suite."""

    print("=" * 60)
    print("DAYTONA VOLUME EFFICIENCY OPTIMIZATION - PHASE 1")
    print("=" * 60)

    # Run the baseline measurement script
    cmd = ["uv", "run", "scripts/performance_benchmarks/daytona_baseline.py"]

    print(f"\nRunning: {' '.join(cmd)}")

    try:
        subprocess.run(
            cmd,
            check=True,
            cwd=Path(__file__).resolve().parents[1],
        )
    except OSError as exc:
        print(f"Could not start benchmark runner: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Benchmark baseline failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode if exc.returncode > 0 else 1

    print("\n" + "=" * 60)
    print("BASELINE MEASUREMENTS COMPLETED")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

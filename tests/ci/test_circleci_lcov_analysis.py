"""Verify the LCOV format emitted by coverage.py for per-atom analysis."""

import subprocess
import sys


def test_coverage_lcov_emits_relative_source_records_for_one_test_atom(tmp_path):
    source = tmp_path / "src" / "sample.py"
    source.parent.mkdir()
    source.write_text("def answer():\n    return 42\n")
    test_file = tmp_path / "tests" / "test_sample.py"
    test_file.parent.mkdir()
    test_file.write_text("from sample import answer\n\ndef test_answer():\n    assert answer() == 42\n")

    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\npythonpath = ['src']\ntestpaths = ['tests']\n")
    (tmp_path / ".coveragerc").write_text("[run]\nrelative_files = true\nsource = src\n")
    data_file = tmp_path / ".coverage"
    report = tmp_path / "coverage.lcov"
    measured = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--data-file={data_file}",
            "--source=src",
            "-m",
            "pytest",
            "-c",
            str(tmp_path / "pyproject.toml"),
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert measured.returncode == 0, measured.stdout + measured.stderr

    exported = subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "lcov",
            f"--data-file={data_file}",
            "-o",
            str(report),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert exported.returncode == 0, exported.stdout + exported.stderr
    records = report.read_text().split("end_of_record")
    assert len(records) == 2
    assert "SF:src/sample.py" in records[0]
    assert "DA:1,1" in records[0]

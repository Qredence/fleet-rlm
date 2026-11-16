"""Direct unit and CLI regression tests for all eight bundled Skill scripts."""

from __future__ import annotations

import email.message
import errno
import gzip
import importlib.util
import io
import ipaddress
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tracemalloc
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from functools import cache
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.skills import BUNDLED_SKILLS_DIR, bundled_skill_names


@cache
def _load_bundled_script(skill_name: str, script_name: str) -> ModuleType:
    """Load a bundled Skill script directly from disk without emitting bytecode into src/."""
    path = BUNDLED_SKILLS_DIR / skill_name / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(f"_test_bundled_{skill_name.replace('-', '_')}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
    return module


def _make_tar_gz(path: Path, entries: list[tuple[str, bytes | None, str]]) -> bytes:
    """Build a tar.gz archive in memory; kind is 'dir', 'file', or 'symlink'."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name=name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                payload = data or b""
                info.type = tarfile.REGTYPE
                info.size = len(payload)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(payload))
    raw = buf.getvalue()
    if path is not None:
        path.write_bytes(raw)
    return raw


class _FakeUrlopenResponse(io.BytesIO):
    """Canned HTTP response that urllib's own HTTP processors accept (code, msg, info, geturl)."""

    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://codeload.github.com/octocat/hello/tar.gz/abc",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(body)
        self.url = url
        self.code = status
        self.status = status
        self.msg = "OK"
        self.headers = email.message.Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def geturl(self) -> str:
        return self.url

    def info(self) -> email.message.Message:
        return self.headers


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _serve_urllib(
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[str], _FakeUrlopenResponse],
) -> list[str]:
    """Serve urllib requests from respond(url) without sockets while urllib's redirect logic stays real.

    Returns the live list of every requested URL, in order.
    """
    requested: list[str] = []

    def fake_open(_handler: Any, req: urllib.request.Request) -> _FakeUrlopenResponse:
        requested.append(req.full_url)
        return respond(req.full_url)

    # An empty proxy map keeps ProxyHandler from registering system proxies that would bypass the fake.
    monkeypatch.setattr(urllib.request, "getproxies", dict)
    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", fake_open)
    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", fake_open)
    mod = _load_bundled_script("web-research", "web_tools.py")
    monkeypatch.setattr(mod._PinnedHTTPHandler, "http_open", fake_open)
    monkeypatch.setattr(mod._PinnedHTTPSHandler, "https_open", fake_open)
    return requested


def _patch_dns(monkeypatch: pytest.MonkeyPatch, answers: dict[str, str] | None = None) -> None:
    """Resolve hosts offline: IP literals map to themselves, names to a public address unless overridden."""
    overrides = answers or {}

    def fake_getaddrinfo(host: str, port: Any, *_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        try:
            address = str(ipaddress.ip_address(host))
        except ValueError:
            address = overrides.get(host, "93.184.216.34")
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ---------------------------------------------------------------------------
# 1. repository-inspection/scripts/fetch_repo.py
# ---------------------------------------------------------------------------


def test_fetch_repo_url_and_ref_validation() -> None:
    mod = _load_bundled_script("repository-inspection", "fetch_repo.py")

    assert mod.validate_github_repo_url("https://github.com/qredence/fleet-rlm.git/") == {
        "owner": "qredence",
        "repo": "fleet-rlm",
        "url": "https://github.com/qredence/fleet-rlm",
    }
    for bad_url in (
        "",
        "http://github.com/a/b",
        "https://user:pass@github.com/a/b",
        "https://github.com:443/a/b",
        "https://gitlab.com/a/b",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b#frag",
        "https://github.com/a",
        "https://github.com/a/b/c",
        "https://github.com/../b",
    ):
        with pytest.raises(ValueError):
            mod.validate_github_repo_url(bad_url)

    for bad_ref in ("", "-n", "--upload-pack=evil", "refs/heads/main", "a..b", "HEAD@{1}", "bad ref", "v*"):
        with pytest.raises(ValueError):
            mod._validate_ref(bad_ref)

    for bad_path in ("", "-p", "/abs/path", "../escape", "a\\b"):
        with pytest.raises(ValueError):
            mod._validate_rel_repo_path(bad_path)
    assert mod._validate_rel_repo_path("src/app.py") == "src/app.py"


def test_fetch_repo_resolve_commit_sha_and_archive_security(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bundled_script("repository-inspection", "fetch_repo.py")
    sha = "A" * 40
    assert mod.resolve_commit_sha("https://github.com/octocat/hello", ref=sha) == sha.lower()

    monkeypatch.setattr(
        mod,
        "_run_git",
        lambda _cmd, **_kw: SimpleNamespace(returncode=0, stdout=f"{'b' * 40}\trefs/heads/main\n", stderr=""),
    )
    assert mod.resolve_commit_sha("https://github.com/octocat/hello", ref="main") == "b" * 40

    monkeypatch.setattr(mod, "_run_git", lambda *_a, **_kw: SimpleNamespace(returncode=128, stdout="", stderr="fatal"))
    with pytest.raises(RuntimeError, match="Could not resolve"):
        mod.resolve_commit_sha("https://github.com/octocat/hello", ref="main")

    monkeypatch.setattr(mod, "_run_git", lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(RuntimeError, match="Expected exactly 1"):
        mod.resolve_commit_sha("https://github.com/octocat/hello", ref="main")

    monkeypatch.setattr(
        mod, "_run_git", lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="bad\tline\n", stderr="")
    )
    with pytest.raises(RuntimeError, match="Unexpected ls-remote"):
        mod.resolve_commit_sha("https://github.com/octocat/hello", ref="main")

    good_tar = _make_tar_gz(
        tmp_path / "good.tar.gz",
        [
            ("octocat-hello-1234", None, "dir"),
            ("octocat-hello-1234/README.md", b"# Hello\n", "file"),
            ("octocat-hello-1234/src", None, "dir"),
            ("octocat-hello-1234/src/main.py", b"x = 1\n", "file"),
        ],
    )
    dest_root = tmp_path / "archives"
    _serve_urllib(
        monkeypatch,
        lambda _url: _FakeUrlopenResponse(good_tar, headers={"Content-Length": str(len(good_tar))}),
    )
    fetched = mod.fetch_archive("octocat", "hello", "b" * 40, dest_root=dest_root)
    assert Path(fetched["repo_dir"]).is_dir()
    inv = mod.inventory_repo(fetched["repo_dir"], max_files=1)
    assert inv["total_files"] == 2 and inv["truncated"] is True and "README.md" in inv["key_files"]
    assert mod.cleanup_repo(fetched["repo_dir"])["removed"] is True
    assert Path(fetched["temp_dir"]).is_dir()
    assert mod.cleanup_repo(fetched["repo_dir"])["removed"] is False
    assert mod.cleanup_repo(fetched["temp_dir"])["removed"] is True
    assert mod.cleanup_repo(fetched["temp_dir"])["removed"] is False

    # A final URL on a disallowed host is refused even without a redirect hop (second guard).
    _serve_urllib(
        monkeypatch,
        lambda _url: _FakeUrlopenResponse(good_tar, url="https://evil.example/archive.tar.gz"),
    )
    with pytest.raises(ValueError, match="disallowed host"):
        mod.fetch_archive("octocat", "hello", "b" * 40, dest_root=dest_root)

    # Reject oversized Content-Length and streamed size.
    _serve_urllib(
        monkeypatch,
        lambda _url: _FakeUrlopenResponse(good_tar, headers={"Content-Length": "999999"}),
    )
    with pytest.raises(ValueError, match="Declared archive size"):
        mod.fetch_archive("octocat", "hello", "b" * 40, dest_root=dest_root, max_archive_bytes=100)

    _serve_urllib(monkeypatch, lambda _url: _FakeUrlopenResponse(good_tar))
    with pytest.raises(ValueError, match="Streamed archive size"):
        mod.fetch_archive("octocat", "hello", "b" * 40, dest_root=dest_root, max_archive_bytes=10)

    # Reject traversal, symlinks, multiple top-level dirs, entry/byte limits.
    bad_archives = [
        ([("octocat-hello/../../etc/passwd", b"x", "file")], {}, "escapes root"),
        ([("octocat-hello/link", None, "symlink")], {}, "Disallowed archive member type"),
        ([("a/f.txt", b"1", "file"), ("b/g.txt", b"2", "file")], {}, "single top-level directory"),
        ([("a", None, "dir"), ("a/1.txt", b"1", "file"), ("a/2.txt", b"2", "file")], {"max_entries": 2}, "entry count"),
        ([("a", None, "dir"), ("a/big.txt", b"x" * 50, "file")], {"max_unpacked_bytes": 10}, "Unpacked archive size"),
    ]
    for entries, kwargs, pattern in bad_archives:
        bad_bytes = _make_tar_gz(tmp_path / "bad.tar.gz", entries)
        _serve_urllib(monkeypatch, lambda _url, b=bad_bytes: _FakeUrlopenResponse(b))
        with pytest.raises(ValueError, match=pattern):
            mod.fetch_archive("octocat", "hello", "b" * 40, dest_root=dest_root, **kwargs)

    with pytest.raises(ValueError, match="protected directory"):
        mod.cleanup_repo("/tmp")


def test_fetch_repo_validates_every_archive_redirect_hop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bundled_script("repository-inspection", "fetch_repo.py")
    sha = "c" * 40
    archive_url = f"https://codeload.github.com/octocat/hello/tar.gz/{sha}"
    good_tar = _make_tar_gz(
        tmp_path / "hop.tar.gz",
        [("octocat-hello", None, "dir"), ("octocat-hello/README.md", b"# Hello\n", "file")],
    )
    redirects = {archive_url: "https://evil.example/archive.tar.gz"}

    def respond(url: str) -> _FakeUrlopenResponse:
        if url in redirects:
            return _FakeUrlopenResponse(b"", url=url, status=302, headers={"Location": redirects[url]})
        return _FakeUrlopenResponse(good_tar, url=url)

    requested = _serve_urllib(monkeypatch, respond)
    dest_root = tmp_path / "hops"
    with pytest.raises(ValueError, match="disallowed host"):
        mod.fetch_archive("octocat", "hello", sha, dest_root=dest_root)
    assert requested == [archive_url]  # the off-allowlist hop is never requested
    assert list(dest_root.iterdir()) == []  # the failed fetch removed its temporary directory

    # An allowlisted hop (same codeload host, different path) is followed to completion.
    moved = f"https://codeload.github.com/octocat/hello/legacy.tar.gz/{sha}"
    redirects[archive_url] = moved
    fetched = mod.fetch_archive("octocat", "hello", sha, dest_root=dest_root)
    assert requested[-1] == moved and fetched["entry_count"] == 2
    assert mod.cleanup_repo(fetched["temp_dir"])["removed"] is True


def test_fetch_repo_local_git_clone_log_diff_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_bundled_script("repository-inspection", "fetch_repo.py")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-b", "main")
    _git(upstream, "config", "user.name", "Fleet Tester")
    _git(upstream, "config", "user.email", "tester@example.com")
    _git(upstream, "config", "uploadpack.allowAnySHA1InWant", "true")

    (upstream / "README.md").write_text("# Demo\n", encoding="utf-8")
    (upstream / "src").mkdir()
    (upstream / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(upstream, "add", ".")
    _git(upstream, "commit", "-m", "initial commit")
    commit1 = _git(upstream, "rev-parse", "HEAD")

    (upstream / "src" / "app.py").write_text("VALUE = 2\nEXTRA = 'long diff line'\n", encoding="utf-8")
    _git(upstream, "add", "src/app.py")
    _git(upstream, "commit", "-m", "update value")
    commit2 = _git(upstream, "rev-parse", "HEAD")

    real_run = subprocess.run

    def local_git_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        rewritten = [str(upstream) if arg == "https://github.com/octocat/demo.git" else arg for arg in cmd]
        return real_run(rewritten, **kwargs)

    monkeypatch.setattr(mod.subprocess, "run", local_git_run)

    clones_dir = tmp_path / "clones"
    cloned = mod.clone_shallow("https://github.com/octocat/demo", ref="main", depth=10, dest_root=clones_dir)
    assert cloned["mode"] == "git" and cloned["sha"] == commit2

    log_all = mod.git_log(cloned["repo_dir"], max_count=10)
    assert [row["subject"] for row in log_all] == ["update value", "initial commit"]
    assert mod.git_log(cloned["repo_dir"], path="README.md")[0]["sha"] == commit1

    diff_res = mod.git_diff(cloned["repo_dir"], commit1, "HEAD", path="src/app.py", max_chars=25)
    assert diff_res["truncated"] is True
    assert "app.py" in diff_res["stat"]

    # Shallow fetch by 40-hex commit SHA also works against the local repo.
    sha_clone = mod.clone_shallow("https://github.com/octocat/demo", ref=commit1, depth=1, dest_root=clones_dir)
    assert sha_clone["sha"] == commit1
    assert mod.cleanup_repo(sha_clone["temp_dir"])["removed"] is True

    # CLI git-log, git-diff, and --mode git --no-keep.
    assert mod.main(["git-log", cloned["repo_dir"], "--max-count", "1"]) == 0
    log_json = json.loads(capsys.readouterr().out)
    assert len(log_json) == 1 and log_json[0]["sha"] == commit2

    assert mod.main(["git-diff", cloned["repo_dir"], commit1, "HEAD", "--path", "src/app.py"]) == 0
    diff_json = json.loads(capsys.readouterr().out)
    assert "VALUE = 2" in diff_json["diff"]

    assert mod.cleanup_repo(cloned["temp_dir"])["removed"] is True

    cli_clones = tmp_path / "cli-clones"
    assert (
        mod.main(
            [
                "https://github.com/octocat/demo",
                "--mode",
                "git",
                "--ref",
                "main",
                "--dest-root",
                str(cli_clones),
                "--no-keep",
            ]
        )
        == 0
    )
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["cleaned_up"] is True and cli_out["inventory"]["total_files"] == 2

    # Archive mode via run() and CLI error exit code.
    tar_bytes = _make_tar_gz(
        tmp_path / "cli.tar.gz",
        [("octocat-demo", None, "dir"), ("octocat-demo/README.md", b"# Demo\n", "file")],
    )
    _serve_urllib(monkeypatch, lambda _url: _FakeUrlopenResponse(tar_bytes))
    res = mod.run("https://github.com/octocat/demo", ref=commit2, keep=False, mode="archive")
    assert res["cleaned_up"] is True and res["inventory"]["total_files"] == 1

    with pytest.raises(ValueError, match="Unsupported acquisition mode"):
        mod.run("https://github.com/octocat/demo", mode="svn")

    assert mod.main(["http://github.com/bad/url"]) == 1
    assert "Error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 2. tabular-analysis/scripts/tabular_tools.py
# ---------------------------------------------------------------------------


def test_tabular_tools_formats_math_verification_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")

    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "region,status,revenue,refund\n"
        "north,ok,$100.10,10.00\n"
        "north,ok,200.20,20.00\n"
        "south,ok,50.00,5.00\n"
        "south,void,999.00,0\n",
        encoding="utf-8",
    )
    tsv_path = tmp_path / "sales.tsv"
    tsv_path.write_text("region\trevenue\neast\t0.1\neast\t0.2\n", encoding="utf-8")
    jsonl_path = tmp_path / "sales.jsonl"
    jsonl_path.write_text('{"region": "west", "revenue": 12.5}\n\n{"region": "west", "revenue": 7.5}\n')
    json_path = tmp_path / "sales.json"
    json_path.write_text('[{"region": "west", "revenue": 20}]', encoding="utf-8")

    prof = mod.profile_table(csv_path, sample_rows=2)
    assert prof["format"] == "csv" and prof["row_count"] == 4 and len(prof["sample"]) == 2
    assert mod.profile_table(tsv_path)["format"] == "tsv"
    assert mod.profile_table(jsonl_path)["format"] == "jsonl"
    assert mod.profile_table(json_path)["format"] == "json"

    # Mock openpyxl for .xlsx coverage.
    class FakeSheet:
        title = "Orders"

        def iter_rows(self, values_only: bool = True):
            assert values_only is True
            yield ("region", "revenue")
            yield ("north", 125)
            yield (None, None)

    class FakeWorkbook:
        def __init__(self) -> None:
            self.sheetnames = ["Orders"]
            self.active = FakeSheet()

        def close(self) -> None:
            return None

    xlsx_path = tmp_path / "sales.xlsx"
    xlsx_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        mod.importlib, "import_module", lambda _name: SimpleNamespace(load_workbook=lambda *_a, **_kw: FakeWorkbook())
    )
    xlsx_prof = mod.profile_table(xlsx_path)
    assert xlsx_prof["format"] == "xlsx" and xlsx_prof["sheet_names"] == ["Orders"] and xlsx_prof["row_count"] == 1

    # Decimal vs float aggregation + filters.
    agg_tsv = mod.aggregate_groups(tsv_path, "region", "revenue", use_decimal=True)
    assert agg_tsv["totals"]["revenue"] == 0.3
    agg_filtered = mod.aggregate_groups(
        csv_path,
        ["region"],
        ["revenue", "refund"],
        filters={"status": ["ok"]},
    )
    assert agg_filtered["row_count"] == 3
    assert agg_filtered["totals"] == {"revenue": 350.3, "refund": 35.0}
    agg_float = mod.aggregate_groups(
        [{"region": "a", "revenue": 1}, {"region": "b", "revenue": 2}],
        "region",
        "revenue",
        use_decimal=False,
        filters=lambda r: r["region"] == "a",
    )
    assert agg_float["row_count"] == 1

    # Weighted rate_of_totals with ROUND_HALF_UP.
    rate = mod.rate_of_totals([{"n": "1"}], "n", [{"d": "8"}], "d", scale="percent", places=0)
    assert rate["formatted"] == "13%" and rate["rate"] == 13.0
    ratio = mod.rate_of_totals([{"n": 1}], "n", [{"d": 4}], "d", scale="ratio", places=2)
    assert ratio["formatted"] == "0.25"

    with pytest.raises(ZeroDivisionError):
        mod.rate_of_totals([{"n": 1}], "n", [{"d": 0}], "d")
    for bad_val in (True, None, "", "abc"):
        with pytest.raises(ValueError):
            mod._to_decimal(bad_val, "col")

    # Report verification on JSON and Markdown prose.
    json_report = tmp_path / "report.json"
    json_report.write_text(json.dumps({"total_revenue": 350.3, "refund_rate": "9.99%", "note": "OK"}), encoding="utf-8")
    ok_json = mod.verify_report_numbers(
        json_report, {"total_revenue": 350.3, "refund_rate": 9.99}, required_phrases=["OK"]
    )
    assert ok_json["ok"] is True

    bad_json = mod.verify_report_numbers(
        json_report, {"total_revenue": 400.0, "note": 1, "missing_val": 77}, required_phrases=["MISSING"]
    )
    assert bad_json["ok"] is False
    assert len(bad_json["mismatched_keys"]) == 2
    assert bad_json["missing_numbers"][0]["label"] == "missing_val"
    assert bad_json["missing_phrases"] == ["MISSING"]

    md_report = tmp_path / "report.md"
    md_report.write_text("Total: $1,234.50 and margin 12.50%.\n", encoding="utf-8")
    assert mod.verify_report_numbers(md_report, [1234.5, 12.5])["ok"] is True

    # run() and CLI main().
    run_res = mod.run(csv_path, group_by="region", metrics="revenue")
    assert run_res["aggregation"]["row_count"] == 4
    assert mod.main([str(csv_path), "--group-by", "region", "--metrics", "revenue", "--sample-rows", "1"]) == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["profile"]["row_count"] == 4
    assert mod.main([str(csv_path), "--group-by", "region"]) == 1
    assert "Error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 3. long-context-search/scripts/context_search.py
# ---------------------------------------------------------------------------


def test_context_search_lines_chunks_over_1mb_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_bundled_script("long-context-search", "context_search.py")

    missing = tmp_path / "missing.log"
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    binary = tmp_path / "bin.dat"
    binary.write_bytes(b"abc\x00def")
    normal = tmp_path / "app.log"
    normal.write_text(
        "\n".join(f"line {i}: {'payment failed' if i in {3, 8, 12} else 'ok'}" for i in range(1, 16)) + "\n",
        encoding="utf-8",
    )
    huge_line = tmp_path / "huge.log"
    huge_payload = ("a" * 520_000) + "NEEDLE_1" + ("b" * 520_000) + "NEEDLE_2" + ("c" * 20_000)
    huge_line.write_text(huge_payload, encoding="utf-8")

    probes = mod.probe_files([missing, empty, binary, normal, huge_line])
    assert [p["recommended_mode"] for p in probes] == ["missing", "lines", "binary", "lines", "chunks"]
    assert probes[4]["has_long_lines"] is True and probes[4]["is_minified"] is True

    # Lines mode on file and raw text.
    line_res = mod.search_windows(normal, r"payment\s+failed", context_lines=1, max_hits=2)
    assert line_res["mode"] == "lines" and line_res["hit_count"] == 2 and line_res["truncated"] is True
    assert line_res["hits"][0]["line_number"] == 3 and "2: line 2: ok" in line_res["hits"][0]["excerpt"]

    raw_line_res = mod.search_windows("alpha\nbeta match\ngamma\n", "beta", context_lines=0, ignore_case=False)
    assert raw_line_res["mode"] == "lines" and raw_line_res["hit_count"] == 1

    # >1 MB single-line file & raw text automatically switch to chunk mode.
    chunk_file_res = mod.search_windows(huge_line, r"NEEDLE_\d+", chunk_size=4000, chunk_overlap=300, max_hits=10)
    assert chunk_file_res["mode"] == "chunks"
    assert [h["match"] for h in chunk_file_res["hits"]] == ["NEEDLE_1", "NEEDLE_2"]

    chunk_text_res = mod.search_windows(huge_payload, r"NEEDLE_\d+", chunk_size=4000, chunk_overlap=300, max_hits=1)
    assert (
        chunk_text_res["mode"] == "chunks" and chunk_text_res["hit_count"] == 1 and chunk_text_res["truncated"] is True
    )

    # read_line_range + >1 MB line guard.
    ranged = mod.read_line_range(normal, 2, 4)
    assert ranged["start_line"] == 2 and ranged["end_line"] == 4 and len(ranged["lines"]) == 3
    with pytest.raises(ValueError, match="search_windows chunk mode"):
        mod.read_line_range(huge_line, 1, 1)
    with pytest.raises(ValueError, match="Binary".lower()):
        mod.search_windows(binary, "abc")
    with pytest.raises(ValueError, match="chunk_overlap"):
        mod.search_windows(normal, "ok", chunk_size=100, chunk_overlap=100)

    # build_jev_batches with >100 items, line hits, chunk hits, and string items.
    mixed_windows = [
        *line_res["hits"],
        *chunk_file_res["hits"],
        *[f"plain item {i}" for i in range(101)],
    ]
    batches = mod.build_jev_batches(mixed_windows, "Find failures", max_chars_per_item=500)
    assert len(batches) == 2 and len(batches[0]["items"]) == 100 and len(batches[1]["items"]) == 5
    assert "[lines " in batches[0]["items"][0] and "[chunk " in batches[0]["items"][2]
    assert mod.build_jev_batches(line_res, "Find failures")[0]["metadata"][0]["line_number"] == 3

    # run() and CLI main().
    assert mod.run(normal, "payment")["hit_count"] == 3
    assert mod.main([str(normal), "--probe"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["recommended_mode"] == "lines"
    assert mod.main([str(normal), "payment", "--max-hits", "2"]) == 0
    assert json.loads(capsys.readouterr().out)["hit_count"] == 2
    assert mod.main([str(normal), "--start-line", "2", "--end-line", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["end_line"] == 3
    assert mod.main([str(normal)]) == 0
    capsys.readouterr()
    assert mod.main([str(normal), "--start-line", "2"]) == 1
    assert "Error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 4. delegation/scripts/delegation_tools.py
# ---------------------------------------------------------------------------


def test_delegation_tools_mechanisms_specs_outputs_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_bundled_script("delegation", "delegation_tools.py")

    assert mod.choose_mechanism(item_count=4, needs_isolated_files_or_code_exec=True)["mechanism"] == "spawn_agents"
    assert mod.choose_mechanism(item_count=1, needs_isolated_files_or_code_exec=True)["mechanism"] == "spawn_agent"
    assert mod.choose_mechanism(item_count=5, needs_choice_or_ranking=True)["mechanism"] == "jev_decide_batched"
    assert mod.choose_mechanism(item_count=3, needs_freeform_text=True)["mechanism"] == "llm_query_batched"
    assert mod.choose_mechanism(item_count=1, needs_freeform_text=True)["mechanism"] == "dspy.Predict"
    assert mod.choose_mechanism(item_count=1)["mechanism"] == "local_python"
    with pytest.raises(ValueError):
        mod.choose_mechanism(item_count=0)

    ws = tmp_path / "ws"
    (ws / "files").mkdir(parents=True)
    (ws / "artifacts" / "child-1").mkdir(parents=True)
    (ws / "files" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (ws / "artifacts" / "child-1" / "out.json").write_text('{"sum": 3}', encoding="utf-8")
    (ws / "artifacts" / "child-1" / "bad.json").write_text("{not-json", encoding="utf-8")

    spec = mod.build_child_specs(
        [
            {"prompt": "Analyze data", "input_paths": ["/workspace/files/data.csv"], "tag": "t1"},
            "Summarize findings",
        ],
        workspace_root=ws,
    )
    assert spec["task_count"] == 2
    assert spec["input_paths"] == [["files/data.csv"], []]
    assert spec["specs"][0]["tag"] == "t1"

    with pytest.raises(ValueError, match="beneath"):
        mod.build_child_specs([{"prompt": "Bad", "input_paths": ["memory/secret.txt"]}], workspace_root=ws)
    with pytest.raises(FileNotFoundError):
        mod.build_child_specs([{"prompt": "Missing", "input_paths": ["files/nope.csv"]}], workspace_root=ws)
    with pytest.raises(ValueError, match="tasks must be a sequence"):
        mod.build_child_specs(["t"] * 9, workspace_root=ws)

    collected = mod.collect_child_outputs(
        [
            {
                "findings": "ok",
                "artifacts": ["artifacts/child-1/out.json", {"path": "artifacts/child-1/bad.json"}],
            },
            {"error": "child timeout", "artifacts": []},
        ],
        artifacts_root=ws / "artifacts",
    )
    assert collected["succeeded_count"] == 1 and collected["failed_count"] == 1
    assert collected["all_succeeded"] is False
    assert collected["parsed_json"] == {"artifacts/child-1/out.json": {"sum": 3}}
    assert "json_error" in collected["items"][0]["artifacts"][1]
    assert collected["errors"] == [{"index": 1, "error": "child timeout"}]

    run_res = mod.run([{"prompt": "Do work", "input_paths": ["files/data.csv"]}], workspace_root=ws)
    assert run_res["recommendation"]["mechanism"] == "spawn_agent"

    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps([{"subtask": "Check CSV", "input_paths": ["files/data.csv"]}]), encoding="utf-8")
    assert mod.main(["--tasks-json", str(tasks_file), "--workspace-root", str(ws)]) == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["task_count"] == 1
    assert mod.main(["--workspace-root", str(ws)]) == 1
    assert "Error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 5. skill-creator/scripts/skill_tools.py
# ---------------------------------------------------------------------------


def test_skill_tools_reserved_names_follow_the_sandbox_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    path = BUNDLED_SKILLS_DIR / "skill-creator" / "scripts" / "skill_tools.py"
    monkeypatch.setenv("FLEET_BUNDLED_SKILLS", "shipped-one,shipped-two")
    spec = importlib.util.spec_from_file_location("_test_skill_tools_injected", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
    assert {"shipped-one", "shipped-two"} == module.BUNDLED_SKILL_NAMES
    with pytest.raises(ValueError, match="Cannot overwrite bundled skill"):
        module.validate_skill_spec("shipped-one", "Attempt", "Body")


def test_skill_tools_validation_scaffolding_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_bundled_script("skill-creator", "skill_tools.py")
    assert bundled_skill_names() == mod.BUNDLED_SKILL_NAMES

    for bundled in mod.BUNDLED_SKILL_NAMES:
        with pytest.raises(ValueError, match="Cannot overwrite bundled skill"):
            mod.validate_skill_spec(bundled, "Attempt", "Body")

    spec = mod.validate_skill_spec(
        "invoice-audit",
        "Audit vendor invoices.",
        "Check totals in Python.",
        scripts={"audit.py": "def run(x):\n    return x\n", "helper.sh": "echo ok\n"},
        references={"rules.md": "# Rules\n"},
    )
    assert spec["valid"] is True and spec["script_names"] == ["audit.py", "helper.sh"]

    long_spec = mod.validate_skill_spec("long-skill", "Long skill", "x" * 20_100)
    assert long_spec["warnings"]

    with pytest.raises(ValueError, match="Syntax error"):
        mod.validate_skill_spec("bad-py", "Desc", "Body", scripts={"broken.py": "def f(:\n"})
    with pytest.raises(ValueError, match=r"\.py or \.sh"):
        mod.validate_skill_spec("bad-ext", "Desc", "Body", scripts={"notes.txt": "hi"})
    with pytest.raises(ValueError, match="Invalid scripts filename"):
        mod.validate_skill_spec("bad-path", "Desc", "Body", scripts={"../evil.py": "x = 1"})

    scaffolded = mod.scaffold_skill(
        "vendor-check",
        "Check vendor records.",
        workflow_steps=["Load CSV", "Compute totals"],
        include_script=True,
        include_reference=True,
    )
    assert "vendor_check.py" in scaffolded["scripts"]
    assert "guide.md" in scaffolded["references"]

    assert mod.run("custom-tool", "Custom desc")["validation"]["valid"] is True
    assert mod.run("custom-tool", "Custom desc", body="Use carefully.")["valid"] is True
    with_custom_scripts = mod.run("custom-tool", "Custom desc", scripts={"tool.py": "def run(): return 1\n"})
    assert with_custom_scripts["validation"]["script_names"] == ["tool.py"]

    body_file = tmp_path / "body.md"
    body_file.write_text("Follow steps 1-3.", encoding="utf-8")
    assert mod.main(["--name", "cli-skill", "--description", "CLI skill", "--include-reference"]) == 0
    assert "cli_skill.py" in json.loads(capsys.readouterr().out)["scripts"]
    assert mod.main(["--name", "cli-skill", "--description", "CLI skill", "--body-file", str(body_file)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert mod.main(["--name", "tabular-analysis", "--description", "Nope"]) == 1
    assert "Error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 6. web-research/scripts/web_tools.py
# ---------------------------------------------------------------------------


def test_web_tools_search_fetch_and_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("YOU_API_KEY", raising=False)

    # Exercise stdlib urllib.request + HTMLParser paths first (bs4 unavailable).
    monkeypatch.setattr(mod, "_try_import", lambda _name: None)

    ddg_html = (
        b'<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">Alpha</a>'
        b'<a class="result__snippet">First snippet</a>'
    )
    wiki_json = json.dumps(
        {"query": {"search": [{"title": "DSPy", "snippet": "<span>Typed</span> framework"}]}}
    ).encode()
    arxiv_xml = (
        b'<feed xmlns="http://www.w3.org/2005/Atom">'
        b"<entry><title>Paper One</title><id>https://arxiv.org/abs/1234</id>"
        b"<summary>Summary text</summary></entry></feed>"
    )
    github_json = json.dumps(
        {
            "items": [
                {
                    "full_name": "qredence/fleet-rlm",
                    "html_url": "https://github.com/qredence/fleet-rlm",
                    "description": "Fleet runtime",
                    "stargazers_count": 42,
                    "language": "Python",
                }
            ]
        }
    ).encode()
    pypi_html = (
        b'<a class="package-snippet" href="/project/dspy/">'
        b'<span class="package-snippet__name">dspy</span>'
        b'<span class="package-snippet__version">3.5.0</span>'
        b'<span class="package-snippet__description">DSPy library</span></a>'
    )
    page_html = (
        b"<html><head><title>Docs &amp; Guide</title><style>.x{}</style></head>"
        b"<body><nav>Menu</nav><h1>Overview</h1><p>Useful content.</p>"
        b'<script>alert(1)</script><a href="/sub">Subpage</a><footer>Foot</footer></body></html>'
    )

    responses: dict[str, tuple[int, str, str, bytes]] = {}

    def respond(url: str) -> _FakeUrlopenResponse:
        for prefix, resp in responses.items():
            if prefix in url:
                status, final_url, ct, body = resp
                return _FakeUrlopenResponse(body, url=final_url, status=status, headers={"Content-Type": ct})
        raise AssertionError(f"Unexpected URL: {url}")

    _serve_urllib(monkeypatch, respond)
    _patch_dns(monkeypatch)

    responses["html.duckduckgo.com"] = (200, "https://html.duckduckgo.com/html/", "text/html", ddg_html)
    ddg_res = mod.search_web("fleet rlm", provider="auto")
    assert ddg_res["provider"] == "ddg" and ddg_res["results"][0]["url"] == "https://example.com/a"

    # Challenge page on ddg falls back to wikipedia in auto mode.
    responses["html.duckduckgo.com"] = (200, "https://html.duckduckgo.com/html/", "text/html", b"anomaly-modal")
    responses["lite.duckduckgo.com"] = (202, "https://lite.duckduckgo.com/lite/", "text/html", b"")
    responses["en.wikipedia.org"] = (200, "https://en.wikipedia.org/w/api.php", "application/json", wiki_json)
    fallback_res = mod.search_web("dspy")
    assert fallback_res["provider"] == "wikipedia" and fallback_res["results"][0]["snippet"] == "Typed framework"

    responses["export.arxiv.org"] = (200, "https://export.arxiv.org/api/query", "application/atom+xml", arxiv_xml)
    assert mod.search_web("rlm", provider="arxiv")["results"][0]["title"] == "Paper One"

    responses["api.github.com"] = (200, "https://api.github.com/search/repositories", "application/json", github_json)
    assert mod.search_web("fleet-rlm", provider="github")["results"][0]["title"] == "qredence/fleet-rlm"

    responses["pypi.org/search"] = (200, "https://pypi.org/search/", "text/html", pypi_html)
    assert mod.search_web("dspy", provider="pypi")["results"][0]["title"] == "dspy 3.5.0"

    responses["pypi.org/search"] = (404, "https://pypi.org/search/", "text/html", b"")
    responses["pypi.org/pypi/"] = (
        200,
        "https://pypi.org/pypi/dspy/json",
        "application/json",
        json.dumps({"info": {"name": "dspy", "version": "3.5.0", "summary": "Typed AI"}}).encode(),
    )
    assert mod.search_web("dspy", provider="pypi")["results"][0]["snippet"] == "Typed AI"

    # Keyed search is host-mediated: the sandbox never reads search keys and retired providers are refused.
    script = (BUNDLED_SKILLS_DIR / "web-research" / "scripts" / "web_tools.py").read_text(encoding="utf-8")
    assert "SERPER_API_KEY" not in script and "YOU_API_KEY" not in script and "api.ydc-index.io" not in script
    for retired in ("serper", "eco"):
        with pytest.raises(ValueError, match="Unsupported provider"):
            mod.search_web("query", provider=retired)

    # fetch_page and fetch_json.
    responses["example.com/docs"] = (200, "https://example.com/docs", "text/html; charset=utf-8", page_html)
    fetched = mod.fetch_page("https://example.com/docs", extract_links=True, max_chars=50)
    assert fetched["title"] == "Docs & Guide"
    assert "Useful content." in fetched["content"]
    assert (
        "alert(1)" not in fetched["content"] and "Menu" not in fetched["content"] and "Foot" not in fetched["content"]
    )
    assert fetched["links"] == [{"text": "Subpage", "url": "https://example.com/sub"}]

    responses["example.com/data.json"] = (200, "https://example.com/data.json", "application/json", b'{"ok": true}')
    assert mod.fetch_json("https://example.com/data.json", params={"a": "1"}) == {"ok": True}
    assert mod.fetch_page("https://example.com/data.json")["content"].startswith("{")

    responses["example.com/oversize"] = (200, "https://example.com/oversize", "application/json", b"x" * 200)
    with pytest.raises(ValueError, match="max_bytes"):
        mod.fetch_json("https://example.com/oversize", max_bytes=50)

    responses["example.com/404"] = (404, "https://example.com/404", "text/plain", b"missing")
    with pytest.raises(RuntimeError, match="HTTP 404"):
        mod.fetch_json("https://example.com/404")

    # run() and CLI main().
    assert mod.run("https://example.com/docs")["title"] == "Docs & Guide"
    assert mod.run("dspy")["provider"] == "wikipedia"

    assert mod.main(["search", "dspy", "--provider", "wikipedia", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["provider"] == "wikipedia"
    assert mod.main(["fetch", "https://example.com/docs", "--extract-links", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Docs & Guide"
    assert mod.main(["fetch", "https://example.com/docs"]) == 0
    assert "# Docs & Guide" in capsys.readouterr().out

    for bad in ("", "ftp://example.com"):
        with pytest.raises(ValueError):
            mod._validate_http_url(bad)


def test_web_tools_gzip_bodies_expand_only_up_to_max_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    bomb = gzip.compress(b"\x00" * (32 * 1024 * 1024))
    assert len(bomb) < 64 * 1024  # small on the wire, 32 MiB once expanded

    _serve_urllib(monkeypatch, lambda url: _FakeUrlopenResponse(bomb, url=url, headers={"Content-Encoding": "gzip"}))
    tracemalloc.start()
    try:
        raw = mod._http_request("https://example.com/bomb.gz", max_bytes=64 * 1024)[3]
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert raw == b"\x00" * (64 * 1024)
    assert peak < 4 * 1024 * 1024  # the expanded payload is never materialized

    # Malformed gzip is returned as received rather than raising.
    _serve_urllib(
        monkeypatch,
        lambda url: _FakeUrlopenResponse(b"not gzip at all", url=url, headers={"Content-Encoding": "gzip"}),
    )
    assert mod._http_request("https://example.com/plain", max_bytes=4096)[3] == b"not gzip at all"


def test_web_tools_fetch_page_refuses_non_public_destinations_and_redirect_hops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    _patch_dns(monkeypatch, {"internal.example": "10.1.2.3"})
    redirects = {
        "https://example.com/start": "http://169.254.169.254/latest/meta-data/",
        "https://example.com/ftp-hop": "ftp://example.com/archive.txt",
        "https://example.com/moved": "https://example.com/final",
    }

    def respond(url: str) -> _FakeUrlopenResponse:
        if url in redirects:
            return _FakeUrlopenResponse(b"", url=url, status=302, headers={"Location": redirects[url]})
        return _FakeUrlopenResponse(b"<p>Final page.</p>", url=url, headers={"Content-Type": "text/html"})

    requested = _serve_urllib(monkeypatch, respond)

    # The initial URL is checked before any request: loopback literals, IPv6 loopback, and RFC 1918 names.
    for blocked in ("http://127.0.0.1:8080/admin", "http://[::1]/", "https://internal.example/admin"):
        with pytest.raises(ValueError, match="non-public"):
            mod.fetch_page(blocked)
    assert requested == []

    # Each redirect hop is validated before urllib follows it.
    with pytest.raises(ValueError, match="non-public"):
        mod.fetch_page("https://example.com/start")
    with pytest.raises(ValueError, match="expected http/https"):
        mod.fetch_page("https://example.com/ftp-hop")
    assert requested == ["https://example.com/start", "https://example.com/ftp-hop"]
    assert not any("169.254" in url for url in requested)

    # A public redirect is still followed.
    assert mod.fetch_page("https://example.com/moved")["url"] == "https://example.com/final"
    assert requested[-1] == "https://example.com/final"

    # fetch_json applies the same public-destination guard before any request.
    before = len(requested)
    for blocked in ("http://127.0.0.1:8080/status.json", "https://internal.example/data.json"):
        with pytest.raises(ValueError, match="non-public"):
            mod.fetch_json(blocked)
    assert len(requested) == before


def test_web_tools_auto_prefers_the_host_web_search_and_falls_back_to_keyless_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    monkeypatch.setattr(mod, "_try_import", lambda _name: None)
    _patch_dns(monkeypatch)
    ddg_html = (
        b'<a class="result__a" href="https://example.com/keyless">Keyless</a><a class="result__snippet">Fallback</a>'
    )
    requested = _serve_urllib(
        monkeypatch,
        lambda url: _FakeUrlopenResponse(ddg_html, url=url, headers={"Content-Type": "text/html"}),
    )
    calls: list[tuple[str, int]] = []

    def host_web_search(query: str, max_results: int) -> list[dict[str, str]]:
        calls.append((query, max_results))
        return [{"title": "Host", "url": "https://host.example/a", "snippet": "From host", "provider": "serper"}]

    monkeypatch.setitem(sys.modules, "__fleet_repl__", SimpleNamespace(web_search=host_web_search))
    hosted = mod.search_web("dspy", num_results=3)
    assert hosted["provider"] == "serper" and hosted["results"][0]["url"] == "https://host.example/a"
    assert calls == [("dspy", 3)] and requested == []

    def unavailable(_query: str, _max_results: int) -> list[dict[str, str]]:
        raise RuntimeError("web_search not configured")

    monkeypatch.setitem(sys.modules, "__fleet_repl__", SimpleNamespace(web_search=unavailable))
    fallback = mod.search_web("dspy")
    assert fallback["provider"] == "ddg" and fallback["results"][0]["url"] == "https://example.com/keyless"
    assert requested and requested[0].startswith("https://html.duckduckgo.com/")

    monkeypatch.delitem(sys.modules, "__fleet_repl__")
    assert mod.search_web("dspy")["provider"] == "ddg"


# ---------------------------------------------------------------------------
# 7. document-extraction/scripts/doc_tools.py
# ---------------------------------------------------------------------------


def _xlsx_relationships(*targets: str) -> str:
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{idx}" Target="{target}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            for idx, target in enumerate(targets, 1)
        )
        + "</Relationships>"
    )


def _build_minimal_ooxml_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    docx_path = tmp_path / "sample.docx"
    with zipfile.ZipFile(docx_path, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Hello Docx</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell1</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "</w:body></w:document>",
        )

    pptx_path = tmp_path / "sample.pptx"
    with zipfile.ZipFile(pptx_path, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Slide Title</a:t></a:r></a:p></p:txBody></p:sp>"
            "</p:spTree></p:cSld></p:sld>",
        )

    xlsx_path = tmp_path / "sample.xlsx"
    with zipfile.ZipFile(xlsx_path, "w") as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Region</t></si></sst>',
        )
        zf.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Summary" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr("xl/_rels/workbook.xml.rels", _xlsx_relationships("worksheets/sheet1.xml"))
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row><c t="s"><v>0</v></c><c><v>42</v></c></row></sheetData></worksheet>',
        )
    return docx_path, pptx_path, xlsx_path


def test_doc_tools_images_text_ooxml_pdf_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_bundled_script("document-extraction", "doc_tools.py")

    # Stdlib header image inspection for PNG, GIF, WebP, JPEG without Pillow.
    monkeypatch.setattr(mod, "_try_import", lambda _name: None)

    png_path = tmp_path / "img.png"
    png_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 320, 240))
    gif_path = tmp_path / "img.gif"
    gif_path.write_bytes(b"GIF89a" + struct.pack("<HH", 64, 32))
    webp_path = tmp_path / "img.webp"
    webp_path.write_bytes(b"RIFF\x00\x00\x00\x00WEBP" + b"VP8 " + b"\x00" * 10 + struct.pack("<HH", 120, 90))
    jpg_path = tmp_path / "img.jpg"
    jpg_path.write_bytes(b"\xff\xd8\xff\xc0\x00\x0b\x08" + struct.pack(">HH", 480, 640))

    assert mod.inspect_image(png_path)["width"] == 320
    assert mod.inspect_image(gif_path)["height"] == 32
    assert mod.inspect_image(webp_path)["image_format"] == "WEBP"
    jpg_meta = mod.inspect_document(jpg_path)
    assert jpg_meta["image_format"] == "JPEG" and jpg_meta["width"] == 640 and jpg_meta["height"] == 480

    with pytest.raises(RuntimeError, match="requires Pillow"):
        mod.inspect_image(png_path, resize_max_dim=100)

    # Markdown, HTML, and OOXML zip-xml paths.
    md_path = tmp_path / "notes.md"
    md_path.write_text("# Heading\nBody text.\n", encoding="utf-8")
    html_path = tmp_path / "page.html"
    html_path.write_text("<html><title>Page</title><body><script>x</script><p>Hello</p></body></html>")
    assert mod.inspect_document(md_path)["headings"] == ["# Heading"]
    assert mod.run(html_path)["content"] == "Hello"
    assert mod.inspect_document(html_path)["title"] == "Page"

    docx_path, pptx_path, xlsx_path = _build_minimal_ooxml_files(tmp_path)
    assert mod.inspect_document(docx_path)["paragraph_count"] == 2
    assert mod.inspect_document(pptx_path)["slide_count"] == 1
    assert mod.inspect_document(xlsx_path)["sheet_names"] == ["Summary"]
    docx_extracted = mod.extract_office(docx_path)
    assert docx_extracted["paragraphs"] == ["Hello Docx"]
    assert docx_extracted["tables"] == [[["Cell1"]]]
    assert mod.extract_office(pptx_path)["slides"][0]["texts"] == ["Slide Title"]
    assert mod.extract_office(xlsx_path)["sheets"][0]["rows"] == [["Region", "42"]]

    # PDF header inspection & error without pdfplumber/pypdf.
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Page >> endobj\n")
    assert mod.inspect_document(pdf_path)["page_count"] == 1
    with pytest.raises(RuntimeError, match="requires pdfplumber or pypdf"):
        mod.extract_pdf(pdf_path)

    # Mocked pdfplumber, pypdf, and PIL.Image.
    class FakePdfPlumberDoc:
        def __init__(self) -> None:
            self.pages = [
                SimpleNamespace(extract_text=lambda: "Page one text", extract_tables=lambda: [[["A", "B"]]]),
                SimpleNamespace(extract_text=lambda: "Page two text", extract_tables=lambda: []),
            ]
            self.metadata = {"Title": "Report"}

        def __enter__(self) -> FakePdfPlumberDoc:
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    fake_pdfplumber = SimpleNamespace(open=lambda _p: FakePdfPlumberDoc())
    fake_pypdf = SimpleNamespace(
        PdfReader=lambda _p: SimpleNamespace(
            pages=[SimpleNamespace(extract_text=lambda: "Pypdf page 1")],
            is_encrypted=False,
            metadata={"/Author": "Alice"},
        )
    )

    class FakePilImg:
        format = "PNG"
        size = (800, 600)
        mode = "RGB"
        width = 400
        height = 300

        def copy(self) -> FakePilImg:
            return self

        def thumbnail(self, _dim: tuple[int, int]) -> None:
            return None

        def save(self, dest: Path) -> None:
            dest.write_bytes(b"resized")

        def __enter__(self) -> FakePilImg:
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    fake_pil = SimpleNamespace(open=lambda _p: FakePilImg())

    def mock_imports(name: str) -> Any | None:
        return {"pdfplumber": fake_pdfplumber, "pypdf": fake_pypdf, "PIL.Image": fake_pil}.get(name)

    monkeypatch.setattr(mod, "_try_import", mock_imports)
    pdf_meta = mod.inspect_document(pdf_path)
    assert pdf_meta["inspector"] == "pypdf" and pdf_meta["page_count"] == 1
    extracted = mod.extract_pdf(pdf_path, pages="1-2")
    assert extracted["extractor"] == "pdfplumber" and extracted["pages"][0]["tables"] == [[["A", "B"]]]
    resized = mod.inspect_image(png_path, resize_max_dim=400, output_path=tmp_path / "thumb.png")
    assert resized["inspector"] == "pillow" and (tmp_path / "thumb.png").read_bytes() == b"resized"

    # CLI main() for inspect and extract.
    assert mod.main(["inspect", str(xlsx_path)]) == 0
    assert json.loads(capsys.readouterr().out)["format"] == "xlsx"
    assert mod.main(["extract", str(pdf_path), "--pages", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["extracted_pages"] == [1]
    assert mod.main(["extract", str(docx_path)]) == 0
    assert json.loads(capsys.readouterr().out)["format"] == "docx"
    assert mod.main(["extract", str(png_path), "--resize-max-dim", "200"]) == 0
    assert json.loads(capsys.readouterr().out)["resized_width"] == 400
    assert mod.main(["extract", str(md_path)]) == 0
    assert json.loads(capsys.readouterr().out)["format"] == "markdown"


def test_doc_tools_zip_members_are_size_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bundled_script("document-extraction", "doc_tools.py")
    monkeypatch.setattr(mod, "_try_import", lambda _name: None)

    payload = b"<w:document/>" * 20
    member_zip = tmp_path / "member.zip"
    with zipfile.ZipFile(member_zip, "w") as zf:
        zf.writestr("word/document.xml", payload)
    with zipfile.ZipFile(member_zip) as zf:
        assert mod._read_member(zf, "word/document.xml", len(payload)) == payload
        with pytest.raises(ValueError, match="limit"):
            mod._read_member(zf, "word/document.xml", len(payload) - 1)

    # Every OOXML part read by the zip-XML fallbacks goes through the same cap.
    monkeypatch.setattr(mod, "_MAX_ZIP_MEMBER_BYTES", 16)
    docx_path, pptx_path, xlsx_path = _build_minimal_ooxml_files(tmp_path)
    for office_path in (docx_path, pptx_path, xlsx_path):
        with pytest.raises(ValueError, match="limit"):
            mod.extract_office(office_path)
    with pytest.raises(ValueError, match="limit"):
        mod.inspect_document(docx_path)


# ---------------------------------------------------------------------------
# 8. file-editing/scripts/edit_tools.py
# ---------------------------------------------------------------------------


def test_edit_tools_unique_batch_diff_errors_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_bundled_script("file-editing", "edit_tools.py")
    target = tmp_path / "config.py"
    target.write_bytes(b"TIMEOUT = 10\r\nRETRIES = 1\r\nRETRIES_BACKUP = 1\r\n")

    assert "RETRIES = 3" in mod.preview_diff(target, "TIMEOUT = 10\r\nRETRIES = 3\r\n")
    dry = mod.edit_unique(target, "TIMEOUT = 10", "TIMEOUT = 30", dry_run=True)
    assert dry["dry_run"] is True and dry["start_line"] == 1
    assert "TIMEOUT = 10" in target.read_text(encoding="utf-8")

    applied = mod.run(target, "TIMEOUT = 10", "TIMEOUT = 30")
    assert applied["dry_run"] is False and applied["start_line"] == 1
    assert target.read_bytes().startswith(b"TIMEOUT = 30\r\n")

    # Error cases: 0 occurrences, >1 occurrences, empty old_str, symlink, missing file.
    with pytest.raises(ValueError, match="occurred 0 times"):
        mod.edit_unique(target, "MISSING", "X")
    with pytest.raises(ValueError, match="occurred 2 times"):
        mod.edit_unique(target, "RETRIES", "RETRY")
    with pytest.raises(ValueError, match="non-empty"):
        mod.edit_unique(target, "", "X")
    with pytest.raises(FileNotFoundError):
        mod.edit_unique(tmp_path / "nope.py", "a", "b")

    link = tmp_path / "link.py"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        mod.edit_unique(link, "TIMEOUT", "X")

    # apply_edits validates all steps in memory before writing.
    with pytest.raises(ValueError, match="Edit 1 failed"):
        mod.apply_edits(target, [("TIMEOUT = 30", "TIMEOUT = 60"), ("NOT_FOUND", "X")])
    assert "TIMEOUT = 30" in target.read_text(encoding="utf-8")

    batch = mod.apply_edits(
        target,
        [("TIMEOUT = 30", "TIMEOUT = 45"), {"old_str": "RETRIES = 1", "new_str": "RETRIES = 5"}],
    )
    assert batch["edits_applied"] == 2
    assert "TIMEOUT = 45" in target.read_text(encoding="utf-8") and "RETRIES = 5" in target.read_text(encoding="utf-8")

    # Fallback when os.replace raises ENOSYS (FUSE-style rename limit).
    def fail_replace(_src: Any, _dst: Any) -> None:
        raise OSError(errno.ENOSYS, "not supported")

    monkeypatch.setattr(os, "replace", fail_replace)
    mod.edit_unique(target, "TIMEOUT = 45", "TIMEOUT = 50")
    assert "TIMEOUT = 50" in target.read_text(encoding="utf-8")
    monkeypatch.undo()

    # CLI main().
    assert mod.main(["--path", str(target), "--old-str", "TIMEOUT = 50", "--new-str", "TIMEOUT = 99", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert mod.main(["--path", str(target), "--old-str", "TIMEOUT = 50", "--new-str", "TIMEOUT = 99"]) == 0
    assert "TIMEOUT = 99" in target.read_text(encoding="utf-8")


def test_edit_tools_preserves_file_mode_on_atomic_and_fallback_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load_bundled_script("file-editing", "edit_tools.py")
    target = tmp_path / "run.sh"
    target.write_text("echo one\n", encoding="utf-8")
    os.chmod(target, 0o700)

    # mkstemp creates 0600 files; the replace must carry the original mode across.
    mod.edit_unique(target, "one", "two")
    assert target.read_text(encoding="utf-8") == "echo two\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700

    # The in-place fallback writes the same inode, so the mode survives it too.
    def refuse_rename(_src: Any, _dst: Any) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "replace", refuse_rename)
    mod.apply_edits(target, [("two", "three")])
    assert target.read_text(encoding="utf-8") == "echo three\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".run.sh.")]


@pytest.mark.parametrize("mode", ["archive", "git"])
def test_repository_cleanup_preserves_repo_cache_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    mod = _load_bundled_script("repository-inspection", "fetch_repo.py")
    destination = tmp_path / "repo-cache"
    destination.mkdir()
    unrelated = destination / "notes.txt"
    unrelated.write_text("keep")
    sibling = destination / "sibling"
    sibling.mkdir()
    (sibling / "README.md").write_text("keep checkout")
    sha = "b" * 40
    if mode == "archive":
        payload = _make_tar_gz(
            tmp_path / "repo.tar.gz",
            [("checkout", None, "dir"), ("checkout/README.md", b"hello", "file")],
        )
        _serve_urllib(monkeypatch, lambda _url: _FakeUrlopenResponse(payload))
        monkeypatch.setattr(mod, "resolve_commit_sha", lambda *_a, **_kw: sha)
        acquire = mod.fetch_archive
        monkeypatch.setattr(mod, "fetch_archive", lambda *a, **kw: acquire(*a, **kw, dest_root=destination))
    else:
        upstream = tmp_path / "upstream"
        upstream.mkdir()
        _git(upstream, "init", "-b", "main")
        _git(upstream, "config", "user.name", "Fleet Tester")
        _git(upstream, "config", "user.email", "tester@example.com")
        (upstream / "README.md").write_text("hello")
        _git(upstream, "add", "README.md")
        _git(upstream, "commit", "-m", "initial")
        real_run = subprocess.run

        def local_git(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            rewritten = [str(upstream) if arg == "https://github.com/octocat/demo.git" else arg for arg in cmd]
            # Prevent background Git maintenance racing with immediate cleanup.
            return real_run([rewritten[0], "-c", "maintenance.auto=false", "-c", "gc.auto=0", *rewritten[1:]], **kwargs)

        monkeypatch.setattr(mod.subprocess, "run", local_git)
        acquire = mod.clone_shallow
        monkeypatch.setattr(mod, "clone_shallow", lambda *a, **kw: acquire(*a, **kw, dest_root=destination))

    fetched = mod.run("https://github.com/octocat/demo", mode=mode)
    assert mod.cleanup_repo(fetched["temp_dir"])["removed"] is True
    assert mod.cleanup_repo(fetched["temp_dir"])["removed"] is False
    assert mod.run("https://github.com/octocat/demo", mode=mode, keep=False)["cleaned_up"] is True

    def fail_inventory(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("inventory failed")

    monkeypatch.setattr(mod, "inventory_repo", fail_inventory)
    with pytest.raises(RuntimeError, match="inventory failed"):
        mod.run("https://github.com/octocat/demo", mode=mode)
    assert set(destination.iterdir()) == {unrelated, sibling}
    assert unrelated.read_text() == "keep"
    assert (sibling / "README.md").read_text() == "keep checkout"


def test_json_report_requires_expected_keys(tmp_path: Path) -> None:
    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")
    report = tmp_path / "report.json"
    report.write_text('{"unrelated": 42, "correct": 7, "incorrect": 3}')
    result = mod.verify_report_numbers(report, {"total_revenue": 42, "correct": 7, "incorrect": 4})
    assert result["ok"] is False
    assert result["missing_numbers"] == [{"label": "total_revenue", "expected": 42}]
    assert result["mismatched_keys"] == [{"key": "incorrect", "expected": 4, "actual": 3}]
    assert mod.verify_report_numbers(report, [42, 7])["ok"] is True
    report.write_text("Revenue: 42")
    assert mod.verify_report_numbers(report, {"total_revenue": 42})["ok"] is True


@pytest.mark.parametrize("coordinate", [None, "A0", "1A", "a1", "XFE1", "AAAA1", "A1048577"])
def test_xlsx_xml_sparse_columns_and_coordinate_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, coordinate: str | None
) -> None:
    mod = _load_bundled_script("document-extraction", "doc_tools.py")
    monkeypatch.setattr(mod, "_try_import", lambda _name: None)
    path = tmp_path / "sparse.xlsx"
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    bad_cell = f'<c r="{coordinate}"><v>1</v></c>' if coordinate else ""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sparse" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_relationships("worksheets/sheet1.xml"))
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>shared</t></si></sst>')
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{ns}"><sheetData>'
            '<row><c r="A1" t="s"><v>0</v></c><c r="C1"><v>42</v></c></row>'
            '<row><c r="C2" t="inlineStr"><is><t>inline</t></is></c>'
            '<c r="AA2"><v>99</v></c></row>'
            "<row><c><v>1</v></c><c><v>2</v></c></row>"
            f"<row>{bad_cell}</row></sheetData></worksheet>",
        )
    if coordinate:
        with pytest.raises(ValueError, match="XLSX cell coordinate"):
            mod.extract_office(path)
    else:
        rows = mod.extract_office(path)["sheets"][0]["rows"]
        assert rows[0] == ["shared", None, "42"]
        assert rows[1] == [None, None, "inline", *([None] * 23), "99"]
        assert rows[2] == ["1", "2"]
        limited = mod.extract_office(path, max_rows_per_sheet=1)["sheets"][0]
        assert limited["rows"] == [rows[0]] and limited["truncated"] is True


@pytest.mark.parametrize("absolute_target", [False, True])
def test_xlsx_fallback_follows_workbook_relationships(tmp_path: Path, absolute_target: bool) -> None:
    mod = _load_bundled_script("document-extraction", "doc_tools.py")
    path = tmp_path / "reordered.xlsx"
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    prefix = "/xl/" if absolute_target else ""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="First" r:id="rId1"/><sheet name="Second" r:id="rId2"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            _xlsx_relationships(f"{prefix}worksheets/sheet2.xml", f"{prefix}worksheets/sheet10.xml"),
        )
        for number, value in [(2, "FIRST DATA"), (10, "SECOND DATA")]:
            archive.writestr(
                f"xl/worksheets/sheet{number}.xml",
                f'<worksheet xmlns="{ns}"><sheetData><row><c t="inlineStr"><is><t>{value}</t></is></c></row>'
                "</sheetData></worksheet>",
            )
    result = mod._extract_xlsx_zip(path, 500)
    assert [(sheet["name"], sheet["rows"]) for sheet in result["sheets"]] == [
        ("First", [["FIRST DATA"]]),
        ("Second", [["SECOND DATA"]]),
    ]


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("Turn budget exhausted"),
        RuntimeError("serper search request failed"),
        TimeoutError("host tool timed out"),
        ValueError("invalid query"),
    ],
)
def test_auto_search_propagates_host_failures(monkeypatch: pytest.MonkeyPatch, failure: Exception) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")

    def host_failure(_query: str, _max_results: int) -> list[dict[str, str]]:
        raise failure

    def unexpected_fallback(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("host failure must not trigger a keyless request")

    monkeypatch.setitem(sys.modules, "__fleet_repl__", SimpleNamespace(web_search=host_failure))
    monkeypatch.setattr(mod, "_search_ddg", unexpected_fallback)
    with pytest.raises(type(failure), match=str(failure)):
        mod.search_web("dspy")


def test_auto_search_empty_host_results_use_keyless_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    monkeypatch.setitem(sys.modules, "__fleet_repl__", SimpleNamespace(web_search=lambda *_args: []))
    monkeypatch.setattr(
        mod, "_search_ddg", lambda *_args: [{"title": "Docs", "url": "https://example.com", "snippet": "x"}]
    )
    assert mod.search_web("dspy")["provider"] == "ddg"


@pytest.mark.parametrize("literal,value", [("1e6", 1000000), ("-2.5E+4", -25000), ("2.5e-4", 0.00025)])
def test_report_scientific_tokens(tmp_path: Path, literal: str, value: float) -> None:
    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")
    report = tmp_path / "report.json"
    report.write_text('{"total": ' + literal + "}")
    assert mod.verify_report_numbers(report, [value])["ok"]
    assert not mod.verify_report_numbers(report, [1])["ok"]


@pytest.mark.parametrize("literal", ["1e", "1e+", "1e-", "1e+6oops", "1e309"])
def test_report_no_partial_numeric_tokens(tmp_path: Path, literal: str) -> None:
    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")
    report = tmp_path / "report.txt"
    report.write_text(literal)
    assert not mod.verify_report_numbers(report, [1])["ok"]


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", '"NaN"', '"Infinity"', "1e309"])
def test_report_nonfinite_actual(tmp_path: Path, literal: str) -> None:
    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")
    report = tmp_path / "report.json"
    report.write_text('{"total": ' + literal + "}")
    result = mod.verify_report_numbers(report, {"total": 123})
    assert not result["ok"]
    assert result["mismatched_keys"][0]["key"] == "total"


@pytest.mark.parametrize("literal", [float("nan"), float("inf"), float("-inf"), "NaN", "1e309"])
def test_report_nonfinite_expectation(tmp_path: Path, literal: Any) -> None:
    from decimal import Decimal

    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")
    report = tmp_path / "report.json"
    report.write_text('{"total": 123}')
    for value in (literal, Decimal(str(literal))):
        for expected in ([value], {"total": value}):
            with pytest.raises(ValueError, match="finite"):
                mod.verify_report_numbers(report, expected)


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("length", [99, 100, 101])
def test_fetch_page_byte_completeness(monkeypatch: pytest.MonkeyPatch, compressed: bool, length: int) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    body = b"x" * length
    headers = {"Content-Type": "text/plain"}
    if compressed:
        body = gzip.compress(body)
        headers["Content-Encoding"] = "gzip"
    _serve_urllib(monkeypatch, lambda url: _FakeUrlopenResponse(body, url=url, headers=headers))
    result = mod.fetch_page("https://example.com/page", max_bytes=100, max_chars=200)
    assert result["content"] == "x" * min(length, 100)
    assert result["total_chars"] == min(length, 100)
    assert result["truncated"] is (length > 100)
    if length > 100:
        with pytest.raises(ValueError, match="max_bytes"):
            mod.fetch_json("https://example.com/page", max_bytes=100)


def test_fetch_page_wire_and_error_clipping(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    for body, headers, status in (
        (b"abcdefghijklmnopqrstuvwxyz", {"Content-Type": "text/plain"}, 200),
        (gzip.compress(bytes(range(256))), {"Content-Encoding": "gzip"}, 200),
        (b"abcdefghijklmnopqrstuvwxyz", {"Content-Type": "text/plain"}, 404),
    ):
        _serve_urllib(
            monkeypatch,
            lambda url, body=body, headers=headers, status=status: _FakeUrlopenResponse(
                body, url=url, headers=headers, status=status
            ),
        )
        result = mod.fetch_page("https://example.com/page", max_bytes=10, max_chars=100)
        assert result["truncated"]
        assert result["status_code"] == status


@pytest.mark.parametrize("default_root", [False, True])
def test_image_relative_output_uses_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, default_root: bool
) -> None:
    mod = _load_bundled_script("document-extraction", "doc_tools.py")
    workspace = tmp_path / "workspace"
    (workspace / "files").mkdir(parents=True)
    image = workspace / "files" / "scan.png"
    image.write_bytes(b"input")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    if default_root:
        monkeypatch.delenv("FLEET_WORKSPACE", raising=False)
        monkeypatch.setattr(mod, "Path", lambda value: workspace if value == "/workspace" else Path(value))
    else:
        monkeypatch.setenv("FLEET_WORKSPACE", str(workspace))

    class Image:
        format = "PNG"
        size = (400, 300)
        mode = "RGB"
        width = 400
        height = 300

        def __enter__(self) -> Image:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def copy(self) -> Image:
            return self

        def thumbnail(self, size: tuple[int, int]) -> None:
            pass

        def save(self, dest: Path) -> None:
            dest.write_bytes(b"resized")

    monkeypatch.setattr(mod, "_try_import", lambda _name: SimpleNamespace(open=lambda _path: Image()))
    result = mod.inspect_image("files/scan.png", resize_max_dim=400, output_path="artifacts/thumb.png")
    assert result["output_path"] == str(workspace / "artifacts/thumb.png")
    assert (workspace / "artifacts/thumb.png").read_bytes() == b"resized"
    assert not (cwd / "artifacts").exists()
    result = mod.inspect_image(image, resize_max_dim=400)
    assert result["output_path"] == str(image.with_name("scan_resized.png"))


def test_report_formatted_numbers_and_sentence_punctuation(tmp_path: Path) -> None:
    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")
    report = tmp_path / "report.txt"
    report.write_text("Revenue was $1,234.50. Rate was 12.5%. Total was 42.")
    assert mod.verify_report_numbers(report, [1234.5, 12.5, 42])["ok"]


@pytest.mark.parametrize("rename_supported", [True, False], ids=["rename", "volume-without-rename"])
@pytest.mark.parametrize("save_fails", [False, True], ids=["replace-protected-file", "preserve-on-save-failure"])
def test_should_replace_image_without_truncating_existing_artifact(tmp_path, monkeypatch, save_fails, rename_supported):
    mod = _load_bundled_script("document-extraction", "doc_tools.py")
    if not rename_supported:

        def unsupported(_source, _destination):
            raise OSError(errno.ENOSYS, "Function not implemented")

        monkeypatch.setattr(mod.os, "replace", unsupported)
    source, destination = tmp_path / "source.png", tmp_path / "output.png"
    source.write_bytes(b"source")
    destination.write_bytes(b"previous")

    class Image:
        format, mode, size, width, height = "PNG", "RGB", (320, 240), 160, 120

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def copy(self):
            return self

        def thumbnail(self, _dimensions):
            pass

        def save(self, path):
            if Path(path) == destination:
                raise PermissionError("Cannot truncate existing artifact")
            Path(path).write_bytes(b"partial" if save_fails else b"resized")
            if save_fails:
                raise ValueError("Image encoding failed")

    monkeypatch.setattr(mod, "_try_import", lambda _name: SimpleNamespace(open=lambda _path: Image()))
    if save_fails:
        with pytest.raises(ValueError, match="encoding"):
            mod.inspect_image(source, resize_max_dim=160, output_path=destination)
        assert destination.read_bytes() == b"previous"
    else:
        result = mod.inspect_image(source, resize_max_dim=160, output_path=destination)
        assert result["output_path"] == str(destination)
        assert destination.read_bytes() == b"resized"
    assert set(tmp_path.iterdir()) == {source, destination}


def test_page_range_rejects_before_expansion() -> None:
    mod = _load_bundled_script("document-extraction", "doc_tools.py")
    with pytest.raises(ValueError, match="out of bounds"):
        mod._parse_pages("1-1000000000", 1)
    assert mod._parse_pages("1-3,2", 3) == [1, 2, 3]


def test_xlsx_fallback_ignores_markitdown(tmp_path, monkeypatch) -> None:
    mod = _load_bundled_script("document-extraction", "doc_tools.py")
    path = tmp_path / "table.xlsx"
    path.touch()
    monkeypatch.setattr(mod, "_try_import", lambda _name: None)
    monkeypatch.setattr(mod, "_extract_with_markitdown", lambda *_args: pytest.fail("unbounded fallback"))
    monkeypatch.setattr(mod, "_extract_xlsx_zip", lambda target, limit: {"path": target, "limit": limit})
    assert mod.extract_office(path, max_rows_per_sheet=2) == {"path": path, "limit": 2}


@pytest.mark.parametrize("mode", ["lines", "chunks"])
def test_jev_crop_retains_late_match(tmp_path, mode) -> None:
    mod = _load_bundled_script("long-context-search", "context_search.py")
    path = tmp_path / "late.txt"
    path.write_text("x" * 3500 + "NEEDLE" + "x" * 494)
    if mode == "chunks":
        path.write_text(path.read_text() + "x" * 20000)
    hits = mod.search_windows(path, "NEEDLE")
    assert hits["mode"] == mode
    batch = mod.build_jev_batches(hits, "Evidence?")[0]
    assert "NEEDLE" in batch["items"][0]
    assert len(batch["items"][0]) <= 3000
    meta = batch["metadata"][0]
    assert meta["excerpt_start"] > 0
    if mode == "chunks":
        assert meta["start_offset"] == meta["excerpt_start"]
        assert meta["end_offset"] == meta["excerpt_end"]
        assert f"offsets {meta['start_offset']}-{meta['end_offset']}" in batch["items"][0]


@pytest.mark.parametrize("size_limit", [False, True])
def test_archive_limits_stop_member_iteration(tmp_path, monkeypatch, size_limit) -> None:
    mod = _load_bundled_script("repository-inspection", "fetch_repo.py")
    path = tmp_path / "many.tar.gz"
    _make_tar_gz(path, [(f"repo/{i}", b"abc", "file") for i in range(10)])
    original = tarfile.TarFile.next
    calls = []

    def next_member(self):
        calls.append(1)
        assert len(calls) <= 4, "archive scanned beyond rejection"
        return original(self)

    monkeypatch.setattr(tarfile.TarFile, "next", next_member)
    with pytest.raises(ValueError, match="exceeds limit"):
        mod._inspect_and_extract_tar(path, tmp_path / "out", max_entries=2, max_unpacked_bytes=2 if size_limit else 100)


def test_git_output_is_retained_as_bounded_prefix() -> None:
    mod = _load_bundled_script("repository-inspection", "fetch_repo.py")
    text, count = mod._run_git_bounded([sys.executable, "-c", "print('x'*1000000, end='')"], timeout=10, max_chars=37)
    assert text == "x" * 37
    assert count == 1000000


@pytest.fixture
def dated_xlsx(tmp_path):
    import datetime

    openpyxl = pytest.importorskip("openpyxl")

    path = tmp_path / "dated.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["date", "value"])
    workbook.active.append([datetime.datetime(2026, 1, 2), 7])
    workbook.active["A2"].number_format = "yyyy-mm-dd"
    workbook.save(path)
    workbook.close()
    return path


def test_tabular_cli_serializes_xlsx_dates(dated_xlsx, capsys) -> None:
    mod = _load_bundled_script("tabular-analysis", "tabular_tools.py")
    assert mod.main([str(dated_xlsx)]) == 0
    assert "2026-01-02T00:00:00" in capsys.readouterr().out
    with pytest.raises(TypeError, match="not JSON serializable"):
        json.dumps(object(), default=mod._json_default)


@pytest.mark.parametrize(
    "destination,kept",
    [("https://example.com/next", True), ("https://other.example/", False), ("http://example.com/", False)],
)
def test_redirect_credentials_are_origin_scoped(destination, kept) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    req = urllib.request.Request("https://example.com/start", headers={"Authorization": "synthetic", "Cookie": "a=b"})
    redirected = mod._GuardedRedirectHandler(public_only=False).redirect_request(
        req, None, 302, "Found", {}, destination
    )
    assert bool(redirected.get_header("Authorization")) is kept
    assert bool(redirected.get_header("Cookie")) is kept


def test_pinned_connection_uses_validated_address(monkeypatch) -> None:
    mod = _load_bundled_script("web-research", "web_tools.py")
    calls = []

    def resolve(*_args, **_kwargs):
        calls.append("resolve")
        assert calls.count("resolve") == 1
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    class FakeSocket:
        def __init__(self, *_args):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, address):
            calls.append(address)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "socket", FakeSocket)
    connection = mod._pinned_connection(mod.http.client.HTTPSConnection, "https://example.com/")(
        "example.com", timeout=2
    )
    assert connection.host == "example.com"
    connection._create_connection(("example.com", 443), 2)
    assert calls == ["resolve", ("93.184.216.34", 443)]


@pytest.mark.parametrize(
    "href",
    [
        "https://duckduckgo.com.evil.example/l/?uddg=https%3A%2F%2Fexample.org",
        "https://evilduckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org",
        "https://evil.example/l/?uddg=https%3A%2F%2Fexample.org",
        "https://duckduckgo.com@evil.example/l/?uddg=https%3A%2F%2Fexample.org",
    ],
)
def test_ddg_unwrap_refuses_unrelated_origins(href):
    mod = _load_bundled_script("web-research", "web_tools.py")
    assert mod._unwrap_ddg_url(href) == href


@pytest.mark.parametrize("prefix", ["/l/", "https://duckduckgo.com/l/", "https://html.duckduckgo.com/l/"])
def test_ddg_unwrap_accepts_provider_and_relative_links(prefix):
    mod = _load_bundled_script("web-research", "web_tools.py")
    assert mod._unwrap_ddg_url(prefix + "?uddg=https%3A%2F%2Fexample.org") == "https://example.org"

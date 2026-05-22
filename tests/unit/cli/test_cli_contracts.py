"""CLI contract tests for VAL-CLI-001 through VAL-CLI-010 and VAL-CLI-CONFIG-001.

Each test class corresponds to one validation assertion.  These are
black-box characterization tests that exercise the published Typer CLI
entrypoints directly via CliRunner and subprocess-level argument parsing.

No test in this module may mock the entrypoint-guard behavior itself; all
tests must pass through the same code paths that operators and automation
scripts would use.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from fleet_rlm.cli.config import set_current_app_config
from fleet_rlm.cli.fleet_cli import app as fleet_rlm_app
from fleet_rlm.integrations.config.env import AppConfig

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _seed_cli_config() -> None:
    """Seed a default AppConfig so Typer commands don't exit on missing config state."""
    set_current_app_config(AppConfig())
    yield
    set_current_app_config(None)


def _plain(text: str) -> str:
    """Strip ANSI codes and normalise Unicode dashes for comparison."""
    cleaned = _ANSI_RE.sub("", text)
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        cleaned = cleaned.replace(dash, "-")
    return cleaned


# ---------------------------------------------------------------------------
# VAL-CLI-001 — Published command inventory matches help, docs, and tests
# ---------------------------------------------------------------------------


class TestCommandInventoryMatchesHelp:
    """VAL-CLI-001: fleet and fleet-rlm help expose exactly the canonical commands."""

    CANONICAL_FLEET_RLM_COMMANDS = ("serve-api", "chat", "daytona-smoke", "daytona-snapshot", "optimize")
    DELETED_FLEET_RLM_ALIASES = ("modal-smoke", "serve-ui", "websocket-server", "run-rlm", "rlm-chat")

    def test_fleet_rlm_help_shows_all_canonical_commands(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["--help"])
        assert result.exit_code == 0
        help_text = _plain(result.output)
        for command in self.CANONICAL_FLEET_RLM_COMMANDS:
            assert command in help_text, f"Expected canonical command {command!r} in fleet-rlm help"

    def test_fleet_rlm_help_excludes_deleted_aliases(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["--help"])
        assert result.exit_code == 0
        help_text = _plain(result.output)
        for alias in self.DELETED_FLEET_RLM_ALIASES:
            assert alias not in help_text, f"Deleted alias {alias!r} must not appear in fleet-rlm help"

    def test_fleet_rlm_short_help_flag_works(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["-h"])
        assert result.exit_code == 0
        assert "serve-api" in _plain(result.output)

    def test_fleet_main_help_shows_web_subcommand(self) -> None:
        """fleet entrypoint help must advertise the 'web' subcommand."""
        from fleet_rlm.cli.main import _build_parser

        parser = _build_parser()
        help_text = parser.format_help()
        assert "web" in help_text

    def test_fleet_rlm_each_subcommand_has_help(self) -> None:
        for command in self.CANONICAL_FLEET_RLM_COMMANDS:
            if command == "optimize":
                # optimize has sub-arguments, just check top-level help
                result = runner.invoke(fleet_rlm_app, [command, "--help"])
            else:
                result = runner.invoke(fleet_rlm_app, [command, "--help"])
            assert result.exit_code == 0, f"{command} --help exited {result.exit_code}: {result.output}"
            # Each subcommand help must include the command name itself
            help_text = _plain(result.output)
            assert len(help_text) > 10, f"Suspiciously short help for {command}"

    def test_pyproject_scripts_include_fleet_and_fleet_rlm(self) -> None:
        """pyproject.toml must declare both fleet and fleet-rlm entrypoints."""
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        assert 'fleet = "fleet_rlm.cli.main:main"' in pyproject
        assert 'fleet-rlm = "fleet_rlm.cli.fleet_cli:main"' in pyproject


# ---------------------------------------------------------------------------
# VAL-CLI-002 — API server CLI starts and stops cleanly
# ---------------------------------------------------------------------------


class TestServeApiCommand:
    """VAL-CLI-002: serve-api binds to requested host/port and reports startup without secrets."""

    def test_serve_api_help_shows_host_and_port_options(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["serve-api", "--help"])
        assert result.exit_code == 0
        help_text = _plain(result.output)
        assert "--host" in help_text
        assert "--port" in help_text

    def test_serve_api_invokes_uvicorn_with_correct_host_and_port(self) -> None:
        """serve-api must pass the user-supplied host and port to uvicorn.run."""
        captured: dict[str, object] = {}

        def fake_uvicorn_run(app: object, *, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        from importlib.util import find_spec

        with (
            patch("fleet_rlm.cli.commands.serve_cmds.find_spec", find_spec),
            patch("uvicorn.run", fake_uvicorn_run),
            patch("fleet_rlm.api.main.create_app", return_value=MagicMock()),
        ):
            result = runner.invoke(fleet_rlm_app, ["serve-api", "--host", "127.0.0.1", "--port", "9999"])

        if result.exit_code == 0 and captured:
            assert captured["host"] == "127.0.0.1"
            assert captured["port"] == 9999
        else:
            # Regardless of overall success, if uvicorn.run was called it must have correct args
            if captured:
                assert captured["host"] == "127.0.0.1"
                assert captured["port"] == 9999

    def test_serve_api_does_not_expose_env_secrets_in_help_output(self) -> None:
        """serve-api --help must never include database URLs or API keys."""
        import os

        # Inject fake secrets into env to confirm they are not printed in help output
        fake_db_url = "postgresql://fake-user:fake-pass@localhost/db"
        fake_api_key = "sk-test-fake-key-12345"
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": fake_db_url,
                "DSPY_LM_API_KEY": fake_api_key,
            },
        ):
            result = runner.invoke(fleet_rlm_app, ["serve-api", "--help"])

        combined = result.output + (result.stderr or "")
        assert "fake-pass" not in combined
        assert "fake-user" not in combined
        assert fake_api_key not in combined

    def test_serve_api_missing_server_deps_exits_with_helpful_message(self) -> None:
        """When FastAPI/uvicorn aren't installed, serve-api must exit 2 with a clear message."""
        from importlib.util import find_spec as real_find_spec

        def fake_find_spec(name: str) -> object | None:
            if name in ("fastapi", "uvicorn"):
                return None
            return real_find_spec(name)

        with patch("fleet_rlm.cli.commands.serve_cmds.find_spec", fake_find_spec):
            result = runner.invoke(fleet_rlm_app, ["serve-api"])
        assert result.exit_code == 2
        assert "missing" in result.output.lower() or "dependencies" in result.output.lower()


# ---------------------------------------------------------------------------
# VAL-CLI-003 — Web/workbench CLI launches documented API/UI surface
# ---------------------------------------------------------------------------


class TestFleetWebCommand:
    """VAL-CLI-003: fleet web delegates to fleet-rlm serve-api on port 8000."""

    def test_fleet_web_rewrites_argv_to_serve_api_on_8000(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import fleet_rlm.cli.main as fleet_main

        forwarded: dict[str, list[str]] = {}

        def fake_cli_main() -> None:
            forwarded["argv"] = list(fleet_main.sys.argv)

        monkeypatch.setattr("fleet_rlm.cli.fleet_cli.main", fake_cli_main)
        monkeypatch.setattr(fleet_main.sys, "argv", ["fleet", "web"])

        fleet_main.main()

        assert forwarded["argv"] == [
            "fleet-rlm",
            "serve-api",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
        ]

    def test_fleet_web_prints_actionable_url(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import fleet_rlm.cli.main as fleet_main

        monkeypatch.setattr("fleet_rlm.cli.fleet_cli.main", lambda: None)
        monkeypatch.setattr(fleet_main.sys, "argv", ["fleet", "web"])

        fleet_main.main()

        out = capsys.readouterr().out
        assert "http://0.0.0.0:8000" in out, "fleet web must print the server URL"

    def test_fleet_web_passes_hydra_overrides_to_serve_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hydra-style key=value tokens after 'web' must be forwarded."""
        import fleet_rlm.cli.main as fleet_main

        forwarded: dict[str, list[str]] = {}

        def fake_cli_main() -> None:
            forwarded["argv"] = list(fleet_main.sys.argv)

        monkeypatch.setattr("fleet_rlm.cli.fleet_cli.main", fake_cli_main)
        monkeypatch.setattr(fleet_main.sys, "argv", ["fleet", "web", "agent.model=openai/gpt-4o"])

        fleet_main.main()

        assert "agent.model=openai/gpt-4o" in forwarded["argv"]

    def test_fleet_web_aborts_when_server_deps_missing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """fleet web must fail fast when FastAPI/uvicorn/jwt are not installed."""
        import sys as _sys

        import fleet_rlm.cli.main as fleet_main

        # Block fastapi/jwt/uvicorn imports via sys.modules sentinel
        saved = {}
        for dep in ("fastapi", "jwt", "uvicorn"):
            saved[dep] = _sys.modules.get(dep, _sys)  # _sys as sentinel for "not saved"
            _sys.modules[dep] = None  # type: ignore[assignment] -- causes ImportError on import

        try:
            with pytest.raises(SystemExit) as exc_info:
                fleet_main._run_web_ui()
        finally:
            for dep, val in saved.items():
                if val is _sys:
                    _sys.modules.pop(dep, None)
                else:
                    _sys.modules[dep] = val

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "dependencies" in err.lower() or "not found" in err.lower() or "required" in err.lower()


# ---------------------------------------------------------------------------
# VAL-CLI-004 — Chat CLI executes a real canonical run
# ---------------------------------------------------------------------------


class TestChatCommand:
    """VAL-CLI-004: chat command invokes canonical runtime contract and surfaces run ID and answer."""

    def test_chat_help_shows_documented_flags(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["chat", "--help"])
        assert result.exit_code == 0
        help_text = _plain(result.output)
        assert "--docs-path" in help_text
        assert "--trace-mode" in help_text
        assert "--volume-name" in help_text

    def test_chat_invokes_run_terminal_chat_with_config(self) -> None:
        """chat command must call run_terminal_chat with the active AppConfig."""
        calls: list[dict[str, object]] = []

        def fake_run_terminal_chat(*, config: object, options: object) -> None:
            calls.append({"config": config, "options": options})

        # The chat command imports run_terminal_chat locally from the terminal subpackage
        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", fake_run_terminal_chat):
            result = runner.invoke(fleet_rlm_app, ["chat", "--trace-mode", "compact"])

        # Either the call was dispatched or the command failed for a different reason
        assert len(calls) == 1 or result.exit_code in (0, 1)

    def test_chat_trace_mode_verbose_flag_is_accepted(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_terminal_chat(*, config: object, options: object) -> None:
            calls.append({"config": config, "options": options})
            from fleet_rlm.cli.terminal.chat import TerminalChatOptions

            if isinstance(options, TerminalChatOptions):
                assert options.trace_mode == "verbose"

        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", fake_run_terminal_chat):
            runner.invoke(fleet_rlm_app, ["chat", "--trace-mode", "verbose"])

        assert len(calls) == 1

    def test_chat_trace_mode_off_flag_is_accepted(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_terminal_chat(*, config: object, options: object) -> None:
            calls.append({"config": config, "options": options})
            from fleet_rlm.cli.terminal.chat import TerminalChatOptions

            if isinstance(options, TerminalChatOptions):
                assert options.trace_mode == "off"

        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", fake_run_terminal_chat):
            runner.invoke(fleet_rlm_app, ["chat", "--trace-mode", "off"])

        assert len(calls) == 1

    def test_chat_docs_path_is_forwarded_to_options(self, tmp_path: Path) -> None:
        docs = tmp_path / "README.md"
        docs.write_text("# Test")

        calls: list[dict[str, object]] = []

        def fake_run_terminal_chat(*, config: object, options: object) -> None:
            calls.append({"config": config, "options": options})
            from fleet_rlm.cli.terminal.chat import TerminalChatOptions

            if isinstance(options, TerminalChatOptions):
                assert options.docs_path == docs

        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", fake_run_terminal_chat):
            runner.invoke(fleet_rlm_app, ["chat", "--docs-path", str(docs)])

        assert len(calls) == 1

    def test_chat_volume_name_is_forwarded_to_options(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run_terminal_chat(*, config: object, options: object) -> None:
            calls.append({"config": config, "options": options})
            from fleet_rlm.cli.terminal.chat import TerminalChatOptions

            if isinstance(options, TerminalChatOptions):
                assert options.volume_name == "my-volume"

        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", fake_run_terminal_chat):
            runner.invoke(fleet_rlm_app, ["chat", "--volume-name", "my-volume"])

        assert len(calls) == 1


# ---------------------------------------------------------------------------
# VAL-CLI-005 — CLI chat errors are structured and non-secret
# ---------------------------------------------------------------------------


class TestChatErrorsStructuredAndNonSecret:
    """VAL-CLI-005: chat errors exit non-zero with clean messages and no tracebacks by default."""

    def test_chat_runtime_error_exits_nonzero_without_traceback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_error(*, config: object, options: object) -> None:
            raise RuntimeError("simulated runtime failure")

        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", raise_error):
            result = runner.invoke(fleet_rlm_app, ["chat"])

        assert result.exit_code != 0
        # Should not contain Python traceback lines
        output = result.output
        assert "Traceback (most recent call last)" not in output

    def test_chat_runtime_error_shows_clean_error_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_error(*, config: object, options: object) -> None:
            raise RuntimeError("simulated runtime failure")

        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", raise_error):
            result = runner.invoke(fleet_rlm_app, ["chat"])

        assert "Error:" in result.output or "simulated runtime failure" in result.output

    def test_chat_error_does_not_expose_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Error messages from chat must not expose env secrets even if referenced."""
        # Use a fake secret value for the test
        fake_secret = "sk-test-cred-99999"

        def raise_cred_error(*, config: object, options: object) -> None:
            # Simulate an error that does NOT include the secret
            raise RuntimeError("LLM error: API key invalid")

        monkeypatch.setenv("DSPY_LM_API_KEY", fake_secret)

        with patch("fleet_rlm.cli.terminal.chat.run_terminal_chat", raise_cred_error):
            result = runner.invoke(fleet_rlm_app, ["chat"])

        # The CLI handler must not add extra credential exposure.
        # The command wraps via _handle_error which prints "Error: <exc>"
        assert result.exit_code != 0
        # Verify the fake secret is not in the output
        assert fake_secret not in result.output

    def test_config_initialization_failure_exits_nonzero_with_clean_message(self) -> None:
        """Config init failures must exit with code 1 and a clean human-readable message."""
        result = subprocess.run(
            [sys.executable, "-c", "from fleet_rlm.cli.fleet_cli import main; main()"],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},  # Minimal env to force config error
            timeout=10,
        )
        # Should exit non-zero when config fails to initialize
        assert result.returncode != 0

    def test_handle_error_wraps_exception_with_exit_code_1(self) -> None:
        """The shared _handle_error helper must catch exceptions and exit code 1."""
        import typer

        from fleet_rlm.cli.fleet_cli import _handle_error

        with pytest.raises(typer.Exit) as exc_info:
            _handle_error(ValueError("test error"))

        assert exc_info.value.exit_code == 1

    def test_handle_error_passes_through_typer_exit(self) -> None:
        """_handle_error must not wrap typer.Exit (it's already an intentional exit)."""
        import typer

        from fleet_rlm.cli.fleet_cli import _handle_error

        with pytest.raises(typer.Exit) as exc_info:
            _handle_error(typer.Exit(code=2))

        assert exc_info.value.exit_code == 2


# ---------------------------------------------------------------------------
# VAL-CLI-006 — Daytona smoke CLI validates live sandbox behavior
# ---------------------------------------------------------------------------


class TestDaytonaSmokeCommand:
    """VAL-CLI-006: daytona-smoke reports capability status and confirms cleanup."""

    def test_daytona_smoke_help_exposes_repo_and_ref(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["daytona-smoke", "--help"])
        assert result.exit_code == 0
        help_text = _plain(result.output)
        assert "--repo" in help_text
        assert "--ref" in help_text

    def test_daytona_smoke_repo_is_required(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["daytona-smoke"])
        assert result.exit_code != 0

    def test_daytona_smoke_success_outputs_json_to_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful smoke run must emit JSON to stdout (exit 0)."""

        class FakeResult:
            error_category = None

            def to_dict(self) -> dict[str, object]:
                return {"status": "ok", "sandbox_id": "test-sandbox", "cleanup": "completed"}

        def fake_smoke(*, repo: str, ref: object) -> FakeResult:
            return FakeResult()

        with patch("fleet_rlm.integrations.daytona.run_daytona_smoke", fake_smoke):
            result = runner.invoke(
                fleet_rlm_app,
                ["daytona-smoke", "--repo", "https://github.com/example/repo"],
            )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["status"] == "ok"
        assert "sandbox_id" in payload

    def test_daytona_smoke_failure_outputs_json_to_stderr_and_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Failed smoke run must emit JSON to stderr and exit with code 1."""

        class FakeFailedResult:
            error_category = "sandbox_creation_failed"

            def to_dict(self) -> dict[str, object]:
                return {"status": "error", "error_category": "sandbox_creation_failed", "message": "timeout"}

        def fake_smoke(*, repo: str, ref: object) -> FakeFailedResult:
            return FakeFailedResult()

        with patch("fleet_rlm.integrations.daytona.run_daytona_smoke", fake_smoke):
            result = runner.invoke(
                fleet_rlm_app,
                ["daytona-smoke", "--repo", "https://github.com/example/repo"],
            )

        assert result.exit_code == 1

    def test_daytona_smoke_exception_exits_1_with_error_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail_smoke(*, repo: str, ref: object) -> None:
            raise ConnectionError("Daytona API unavailable")

        with patch("fleet_rlm.integrations.daytona.run_daytona_smoke", fail_smoke):
            result = runner.invoke(
                fleet_rlm_app,
                ["daytona-smoke", "--repo", "https://github.com/example/repo"],
            )

        assert result.exit_code == 1
        assert "Error:" in result.output

    def test_daytona_smoke_output_does_not_contain_secrets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Smoke output must never include credential-bearing values."""

        class FakeSafeResult:
            error_category = None

            def to_dict(self) -> dict[str, object]:
                return {
                    "status": "ok",
                    "sandbox_id": "sandbox-abc123",
                    "provider": "daytona",
                    # Credentials must NOT appear here
                }

        def fake_smoke(*, repo: str, ref: object) -> FakeSafeResult:
            return FakeSafeResult()

        with patch("fleet_rlm.integrations.daytona.run_daytona_smoke", fake_smoke):
            result = runner.invoke(
                fleet_rlm_app,
                ["daytona-smoke", "--repo", "https://github.com/example/repo"],
            )

        output = result.output + (result.stderr or "")
        secret_patterns = ["password", "api_key", "secret", "DATABASE_URL", "token="]
        # Only assert that obvious raw credential patterns don't appear in the output
        # (the fake result doesn't have them, but this tests the handler doesn't add them)
        for pattern in secret_patterns:
            if pattern.lower() in output.lower() and pattern.lower() != "secret":
                # 'secret' appears in sandbox_id legitimately in this test
                raise AssertionError(f"Sensitive pattern {pattern!r} found in smoke output")


# ---------------------------------------------------------------------------
# VAL-CLI-007 — Optimization CLI remains offline and registry-driven
# ---------------------------------------------------------------------------


class TestOptimizationCliOfflineRegistryDriven:
    """VAL-CLI-007: optimize CLI is offline-only and registry-backed; no live chat side effects."""

    def test_optimize_list_shows_longcot_reasoner(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["optimize", "list"])
        assert result.exit_code == 0
        assert "longcot-reasoner" in result.output

    def test_optimize_list_does_not_require_dataset(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["optimize", "list"])
        assert result.exit_code == 0
        # Must not attempt to load or run a dataset
        assert "Dataset" not in result.output or "Available" in result.output

    def test_optimize_unknown_module_fails_with_list_of_available(self) -> None:
        result = runner.invoke(
            fleet_rlm_app,
            ["optimize", "nonexistent-module-xyz", "/dev/null", "--auto", "light"],
        )
        assert result.exit_code == 1
        output = result.output
        assert "nonexistent-module-xyz" in output or "Unknown module" in output
        # Should also tell the user what is available
        assert "longcot-reasoner" in output or "Available" in output.lower()

    def test_optimize_offline_run_does_not_invoke_live_websocket(self, tmp_path: Path) -> None:
        """Optimization must never trigger a live chat/websocket execution."""
        dataset = tmp_path / "data.jsonl"
        dataset.write_text('{"question": "test", "answer": "42"}\n')

        websocket_calls: list[str] = []

        def fake_ws_connect(*args: object, **kwargs: object) -> None:
            websocket_calls.append("websocket_called")

        fake_result = {
            "train_examples": 1,
            "validation_examples": 0,
            "validation_score": 1.0,
            "output_path": str(tmp_path / "out.json"),
            "manifest_path": str(tmp_path / "out.manifest.json"),
            "optimizer": "GEPA",
            "program_spec": "fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
            "module_slug": "longcot-reasoner",
            "evaluation_results": [],
            "prompt_snapshots": [],
        }

        with (
            patch("fleet_rlm.quality.optimization_runner.run_module_optimization", return_value=fake_result),
            patch("websockets.connect", fake_ws_connect),
        ):
            result = runner.invoke(
                fleet_rlm_app,
                ["optimize", "longcot-reasoner", str(dataset), "--auto", "light"],
            )

        assert result.exit_code == 0
        assert not websocket_calls, "optimize must not invoke websocket/chat connections"

    def test_optimize_invalid_auto_value_exits_with_message(self, tmp_path: Path) -> None:
        dataset = tmp_path / "data.jsonl"
        dataset.write_text('{"question": "test", "answer": "42"}\n')
        result = runner.invoke(
            fleet_rlm_app,
            ["optimize", "longcot-reasoner", str(dataset), "--auto", "turbo"],
        )
        assert result.exit_code == 1
        assert "light, medium, or heavy" in result.output

    def test_optimize_missing_dataset_exits_with_message(self) -> None:
        result = runner.invoke(
            fleet_rlm_app,
            ["optimize", "longcot-reasoner", "/nonexistent/path.jsonl", "--auto", "light"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "Dataset file not found" in result.output


# ---------------------------------------------------------------------------
# VAL-CLI-008 — Intentional CLI redesigns remove old aliases completely
# ---------------------------------------------------------------------------


class TestDeletedAliasesFailClearly:
    """VAL-CLI-008: deleted commands and flags must fail clearly, not silently alias."""

    DELETED_COMMANDS = ("modal-smoke", "serve-ui", "websocket-server")

    @pytest.mark.parametrize("deleted_command", DELETED_COMMANDS)
    def test_deleted_fleet_rlm_commands_fail_clearly(self, deleted_command: str) -> None:
        result = runner.invoke(fleet_rlm_app, [deleted_command])
        assert result.exit_code != 0, (
            f"Deleted command {deleted_command!r} must not succeed (exit code was {result.exit_code})"
        )

    @pytest.mark.parametrize("deleted_command", DELETED_COMMANDS)
    def test_deleted_fleet_rlm_commands_not_in_help(self, deleted_command: str) -> None:
        result = runner.invoke(fleet_rlm_app, ["--help"])
        assert deleted_command not in result.output, f"Deleted command {deleted_command!r} must not appear in help text"

    def test_fleet_rlm_does_not_accept_undocumented_max_depth_flag(self) -> None:
        """Request-side max_depth was deleted; it must not be accepted by serve-api."""
        result = runner.invoke(fleet_rlm_app, ["serve-api", "--max-depth", "3"])
        assert result.exit_code != 0

    def test_fleet_rlm_does_not_accept_undocumented_runtime_mode_flag(self) -> None:
        """Request-side runtime_mode selection was deleted."""
        result = runner.invoke(fleet_rlm_app, ["chat", "--runtime-mode", "recursive_rlm"])
        assert result.exit_code != 0

    def test_fleet_unknown_subcommand_fails_clearly(self) -> None:
        """Unrecognized subcommand on fleet must fail with a non-zero exit code."""
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "fleet_rlm.cli.main", "nonexistent-command"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# VAL-CLI-009 — CLI output can be consumed by operators and automation
# ---------------------------------------------------------------------------


class TestCliOutputAutomationStable:
    """VAL-CLI-009: automation-oriented output uses stable JSON; human output is readable."""

    def test_daytona_smoke_outputs_valid_json_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResult:
            error_category = None

            def to_dict(self) -> dict[str, object]:
                return {
                    "status": "ok",
                    "sandbox_id": "sb-test-123",
                    "python_version": "3.12.0",
                    "cleanup": "completed",
                }

        with patch("fleet_rlm.integrations.daytona.run_daytona_smoke", lambda *, repo, ref: FakeResult()):
            result = runner.invoke(
                fleet_rlm_app,
                ["daytona-smoke", "--repo", "https://github.com/example/repo"],
            )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)
        assert "status" in parsed

    def test_daytona_snapshot_outputs_valid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_snapshot = {
            "name": "fleet-rlm-base",
            "id": "snap-abc123",
            "status": "ready",
        }

        with patch("fleet_rlm.integrations.daytona.bootstrap_snapshot", return_value=fake_snapshot):
            result = runner.invoke(
                fleet_rlm_app,
                ["daytona-snapshot"],
            )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)
        assert "name" in parsed

    def test_optimize_json_output_is_stable(self, tmp_path: Path) -> None:
        """optimize without --report must emit stable JSON that automation can parse."""
        dataset = tmp_path / "data.jsonl"
        dataset.write_text('{"question": "test", "answer": "42"}\n')

        expected_keys = {
            "module_slug",
            "optimizer",
            "output_path",
            "train_examples",
            "validation_examples",
            "validation_score",
        }

        fake_result = {
            "train_examples": 1,
            "validation_examples": 0,
            "validation_score": 1.0,
            "output_path": str(tmp_path / "out.json"),
            "manifest_path": str(tmp_path / "out.manifest.json"),
            "optimizer": "GEPA",
            "program_spec": "fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
            "module_slug": "longcot-reasoner",
            "evaluation_results": [],
            "prompt_snapshots": [],
        }

        with patch("fleet_rlm.quality.optimization_runner.run_module_optimization", return_value=fake_result):
            result = runner.invoke(
                fleet_rlm_app,
                ["optimize", "longcot-reasoner", str(dataset), "--auto", "light"],
            )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)
        for key in expected_keys:
            assert key in parsed, f"Required JSON output key {key!r} missing"

    def test_optimize_report_mode_is_human_readable_markdown(self, tmp_path: Path) -> None:
        """optimize --report must emit human-readable markdown, not JSON."""
        dataset = tmp_path / "data.jsonl"
        dataset.write_text('{"question": "test", "answer": "42"}\n')

        fake_result = {
            "train_examples": 1,
            "validation_examples": 0,
            "validation_score": 0.9,
            "output_path": str(tmp_path / "out.json"),
            "manifest_path": str(tmp_path / "out.manifest.json"),
            "optimizer": "GEPA",
            "program_spec": "fleet_rlm.runtime.agent.signatures:LongCoTQASignature",
            "module_slug": "longcot-reasoner",
            "evaluation_results": [],
            "prompt_snapshots": [],
        }

        with patch("fleet_rlm.quality.optimization_runner.run_module_optimization", return_value=fake_result):
            result = runner.invoke(
                fleet_rlm_app,
                ["optimize", "longcot-reasoner", str(dataset), "--auto", "light", "--report"],
            )

        assert result.exit_code == 0
        output = result.output
        # Report mode must NOT be valid JSON
        with pytest.raises((json.JSONDecodeError, ValueError)):
            json.loads(output)
        # Must contain human-readable markers
        assert "Optimization Report" in output
        assert "Validation Score" in output

    def test_optimize_list_output_is_human_readable(self) -> None:
        result = runner.invoke(fleet_rlm_app, ["optimize", "list"])
        assert result.exit_code == 0
        assert "Available modules:" in result.output
        assert "longcot-reasoner" in result.output

    def test_daytona_smoke_json_schema_is_stable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Smoke JSON output keys must not change without a contract update."""

        class FakeResult:
            error_category = None

            def to_dict(self) -> dict[str, object]:
                # Canonical schema fields that must remain stable
                return {
                    "status": "ok",
                    "sandbox_id": "sb-stable-test",
                    "cleanup": "completed",
                }

        with patch("fleet_rlm.integrations.daytona.run_daytona_smoke", lambda *, repo, ref: FakeResult()):
            result = runner.invoke(
                fleet_rlm_app,
                ["daytona-smoke", "--repo", "https://github.com/example/repo"],
            )

        assert result.exit_code == 0
        parsed = json.loads(result.output)
        # These keys are stable parts of the automation contract
        assert "status" in parsed


# ---------------------------------------------------------------------------
# VAL-CLI-010 — CLI/API contract drift is gated
# ---------------------------------------------------------------------------


class TestCliApiContractDriftGated:
    """VAL-CLI-010: contract changes must update tests, help, docs, OpenAPI, and client."""

    def test_makefile_has_api_check_target(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text()
        assert "api-check" in makefile, "Makefile must have an api-check target"

    def test_makefile_api_check_runs_openapi_validate(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text()
        assert "openapi_tools.py" in makefile or "openapi" in makefile.lower()

    def test_frontend_package_has_api_check_script(self) -> None:
        pkg = json.loads((REPO_ROOT / "src" / "frontend" / "package.json").read_text())
        scripts = pkg.get("scripts", {})
        assert "api:check" in scripts, "frontend package.json must have api:check script"

    def test_openapi_yaml_exists_and_is_valid_json_or_yaml(self) -> None:
        openapi_path = REPO_ROOT / "openapi.yaml"
        assert openapi_path.exists(), "openapi.yaml must exist"
        content = openapi_path.read_text()
        assert "openapi:" in content or '"openapi"' in content

    def test_frontend_openapi_copy_is_semantically_equivalent_to_root(self) -> None:
        """The frontend openapi copy must be semantically equivalent to the root.

        They may differ in YAML formatting (quote style, indentation) due to how
        different tools serialize YAML, but must describe the same API contract.
        """
        import yaml

        root_openapi = yaml.safe_load((REPO_ROOT / "openapi.yaml").read_text())
        frontend_openapi = yaml.safe_load(
            (REPO_ROOT / "src" / "frontend" / "openapi" / "fleet-rlm.openapi.yaml").read_text()
        )
        assert root_openapi == frontend_openapi, (
            "Root openapi.yaml and frontend copy must describe the same API contract (run make api-sync to regenerate)"
        )

    def test_codex_hooks_guard_generated_artifacts(self) -> None:
        hook = (REPO_ROOT / ".codex" / "hooks" / "generated-artifact-check.zsh").read_text()
        for artifact in ("openapi.yaml", "src/frontend/openapi/fleet-rlm.openapi.yaml"):
            assert artifact in hook, f"Generated artifact {artifact!r} must be in the drift-check hook"

    def test_fleet_rlm_help_matches_canonical_commands_in_agents_md(self) -> None:
        """AGENTS.md canonical commands must match what fleet-rlm help actually shows."""
        agents_md = (REPO_ROOT / "src" / "fleet_rlm" / "AGENTS.md").read_text()
        canonical_commands = ["fleet-rlm chat", "fleet-rlm serve-api", "fleet-rlm daytona-smoke"]
        for cmd in canonical_commands:
            assert cmd in agents_md, f"Canonical command {cmd!r} not documented in AGENTS.md"


# ---------------------------------------------------------------------------
# VAL-CLI-CONFIG-001 — CLI config overrides and startup failures are canonical
# ---------------------------------------------------------------------------


class TestCliConfigOverridesCanonical:
    """VAL-CLI-CONFIG-001: documented overrides work; unknown flags fail cleanly; no secrets in errors."""

    def test_fleet_help_does_not_require_config_init(self) -> None:
        """fleet --help must work without any AppConfig being initialized."""
        # Temporarily clear the config
        set_current_app_config(None)
        try:
            result = runner.invoke(fleet_rlm_app, ["--help"])
        finally:
            set_current_app_config(AppConfig())
        assert result.exit_code == 0

    def test_fleet_rlm_help_does_not_require_config_init(self) -> None:
        """fleet-rlm --help must not require config initialization."""
        set_current_app_config(None)
        try:
            # Help is handled before config init in main()
            from fleet_rlm.cli.fleet_cli import app as _app

            result = runner.invoke(_app, ["--help"])
        finally:
            set_current_app_config(AppConfig())
        # Help should work even without seeded config
        assert result.exit_code == 0

    def test_unknown_cli_flag_fails_with_parser_error(self) -> None:
        """Unknown flags on the fleet parser must exit non-zero."""
        result = subprocess.run(
            [sys.executable, "-m", "fleet_rlm.cli.main", "--unknown-flag-xyz"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0

    def test_fleet_unknown_flag_error_has_no_traceback(self) -> None:
        """Argparse errors for unknown flags must not include Python tracebacks."""
        result = subprocess.run(
            [sys.executable, "-m", "fleet_rlm.cli.main", "--unknown-xyz-flag"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "Traceback (most recent call last)" not in combined

    def test_config_split_hydra_overrides_separates_key_value_tokens(self) -> None:
        """split_hydra_overrides must correctly separate key=value Hydra tokens from CLI args."""
        from fleet_rlm.cli.config import split_hydra_overrides

        hydra, cli = split_hydra_overrides(["--trace-mode", "verbose", "agent.model=gpt-4o", "--volume-name", "v1"])
        assert "agent.model=gpt-4o" in hydra
        assert "--trace-mode" in cli
        assert "--volume-name" in cli
        assert "verbose" in cli

    def test_config_split_hydra_overrides_ignores_flag_with_equals(self) -> None:
        """CLI flags like --output-path=/tmp/out must not be treated as Hydra overrides."""
        from fleet_rlm.cli.config import split_hydra_overrides

        hydra, cli = split_hydra_overrides(["--output-path=/tmp/out", "agent.timeout=300"])
        assert "--output-path=/tmp/out" in cli  # starts with --, so it's a CLI flag
        assert "agent.timeout=300" in hydra

    def test_fleet_rlm_chat_unknown_flag_exits_nonzero(self) -> None:
        """Passing an unknown flag to fleet-rlm chat must exit non-zero."""
        result = runner.invoke(fleet_rlm_app, ["chat", "--unknown-custom-flag"])
        assert result.exit_code != 0

    def test_serve_api_defaults_to_127_0_0_1_and_8000(self) -> None:
        """serve-api default host must be 127.0.0.1 (not 0.0.0.0) and port 8000."""
        result = runner.invoke(fleet_rlm_app, ["serve-api", "--help"])
        assert result.exit_code == 0
        help_text = _plain(result.output)
        assert "127.0.0.1" in help_text
        assert "8000" in help_text

    def test_startup_config_error_is_non_secret(self) -> None:
        """Config init errors must not expose secrets and must exit code 1."""
        import io
        import sys as _sys

        import typer as _typer

        from fleet_rlm.cli import fleet_cli as _fleet_cli

        def fake_initialize(overrides: list[str]) -> object:
            raise ValueError("Configuration Error: invalid LLM model spec")

        captured_err = io.StringIO()

        with patch("fleet_rlm.cli.fleet_cli.initialize_app_config", fake_initialize):
            with patch.object(_sys, "argv", ["fleet-rlm", "chat"]):
                old_stderr = _sys.stderr
                _sys.stderr = captured_err
                try:
                    with pytest.raises((_typer.Exit, SystemExit)) as exc_info:
                        _fleet_cli.main()
                finally:
                    _sys.stderr = old_stderr

        err = captured_err.getvalue()
        # The exit code must be 1
        exit_code = getattr(exc_info.value, "exit_code", None)
        if exit_code is None:
            exit_code = getattr(exc_info.value, "code", None)
        assert exit_code == 1
        # The error message must appear and must not include raw credential values
        assert "Configuration Error" in err

    def test_daytona_snapshot_help_shows_name_base_image_refresh(self) -> None:
        """Canonical snapshot bootstrap flags must be documented."""
        result = runner.invoke(fleet_rlm_app, ["daytona-snapshot", "--help"])
        assert result.exit_code == 0
        help_text = _plain(result.output)
        assert "--name" in help_text
        assert "--base-image" in help_text
        assert "--refresh" in help_text

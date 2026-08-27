"""P43.2 opt-in proof for committed Session History over a real Daytona interpreter.

The test deliberately has no LLM dependency.  A deterministic action double drives
``dspy.RLM.acall`` while its generated code runs through the production
``DaytonaCodeInterpreter`` broker in an ephemeral, unmounted Sandbox.  The
history witness is intentionally written only inside that Sandbox; public
interpreter observations and captured diagnostics may contain its count and
digest, but never a history body.

Run only with explicit provider authority::

    FLEET_LIVE=1 uv run pytest tests/live/backend/test_p43_2_daytona_history_live.py -q

P43 evidence found that the adapter rejects a raw ``dspy.History`` Pydantic
object before code execution, so this proof exercises the single permitted
P43.7 fallback: Fleet's narrow ``CommittedSessionHistory``
``SandboxSerializable`` wrapper carrying the same complete canonical records.
Its ``finally`` path still strictly closes the broker, deletes the owned
Sandbox, confirms provider absence, and closes the Daytona client.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any

import dspy
import pytest
from dotenv import load_dotenv
from dspy.primitives.repl_types import REPLHistory

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.rlm._dspy_compat import CERTIFIED_DSPY_VERSION
from fleet_rlm.rlm.events import RLMCode, RLMOutput
from fleet_rlm.sessions.history_transport import CommittedSessionHistory

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(300)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_VALUES = frozenset({"1", "true", "yes"})
_WITNESS_PATH = "/tmp/fleet-p43-2-history-witness.json"


class _P43HistorySignature(dspy.Signature):
    """Minimal named-input boundary for the native History transport proof."""

    request: str = dspy.InputField()
    history: dspy.History = dspy.InputField()
    history_sha256: str = dspy.InputField()
    history_witness_path: str = dspy.InputField()
    history_message_count: int = dspy.InputField()
    answer: str = dspy.OutputField()


class _HistoryInspectionAction:
    """Deterministically inspect history without embedding its bodies in public code."""

    def __init__(self) -> None:
        self.repl_histories: list[REPLHistory] = []

    async def acall(self, **kwargs: Any) -> dspy.Prediction:
        repl_history = kwargs["repl_history"]
        assert type(repl_history) is REPLHistory
        self.repl_histories.append(repl_history)
        assert kwargs["iteration"] == "1/1"
        return dspy.Prediction(
            reasoning="Inspect the History privately in the Daytona Sandbox and submit only a digest.",
            code="\n".join(
                (
                    "import hashlib",
                    "import json",
                    "from pathlib import Path",
                    "assert type(history).__name__ == '_FleetCommittedHistory'",
                    (
                        "serialized = json.dumps(history.messages, ensure_ascii=False, "
                        "sort_keys=True, separators=(',', ':'))"
                    ),
                    "assert len(history.messages) == history_message_count",
                    "actual_sha256 = hashlib.sha256(serialized.encode('utf-8')).hexdigest()",
                    "assert actual_sha256 == history_sha256",
                    "Path(history_witness_path).write_text(serialized, encoding='utf-8')",
                    "SUBMIT(answer=f'history-records={history_message_count} sha256={actual_sha256}')",
                )
            ),
        )


def _canonical_history_messages() -> list[dict[str, str]]:
    """Return many canonical request/answer records with Unicode and multiline bodies."""
    messages = [
        {
            "request": "P43.2 private Unicode request Ω漢字🧪\nsecond request line",
            "answer": "P43.2 private Unicode answer café — résumé ✓",
        },
        {
            "request": "P43.2 private multiline request\nline two\nline three",
            "answer": "P43.2 private multiline answer\nparagraph two",
        },
        {
            "request": "P43.2 private canonical boundary request 000",
            "answer": "P43.2 private canonical boundary answer 000",
        },
    ]
    messages.extend(
        {
            "request": f"P43.2 private canonical request {ordinal:03d}\nbody {ordinal:03d}",
            "answer": f"P43.2 private canonical answer {ordinal:03d}\nreply {ordinal:03d}",
        }
        for ordinal in range(1, 33)
    )
    return messages


def _canonical_history_sha256(messages: list[dict[str, str]]) -> str:
    """Hash the exact UTF-8 JSON representation written by Sandbox code."""
    encoded = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _history_body_markers(messages: list[dict[str, str]]) -> tuple[str, ...]:
    """Return full private bodies that must never occur in public observations."""
    return tuple(value for message in messages for value in message.values())


def _require_live_platform() -> tuple[Any, Any, Any]:
    """Resolve the configured Fleet Daytona client only after explicit opt-in."""
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in _LIVE_VALUES:
        pytest.skip("Set FLEET_LIVE=1 for the P43.2 Daytona History proof")

    load_dotenv(_REPO_ROOT / ".env", override=False)
    from fleet_rlm.config import load_runtime_settings
    from fleet_rlm.daytona.platform import LiveDaytonaPlatform, build_daytona_client
    from fleet_rlm.daytona.provisioning import DaytonaSandboxSpec

    settings = load_runtime_settings()
    if settings.daytona_api_key is None:
        pytest.fail("P43.2 Daytona History proof requires configured Daytona credentials")
    spec = DaytonaSandboxSpec.from_settings(settings)
    client = build_daytona_client(settings)
    return client, LiveDaytonaPlatform(client, spec), spec


async def _close_daytona_client(client: Any) -> None:
    """Close the SDK client across its supported sync and async close shapes."""
    result = client.close()
    if inspect.isawaitable(result):
        await result


async def _cleanup_live_history_proof(
    *,
    client: Any | None,
    platform: Any | None,
    sandbox_id: str | None,
    interpreter: DaytonaCodeInterpreter | None,
) -> tuple[str, ...]:
    """Strictly release the broker, Sandbox, and SDK client, then confirm absence."""
    failures: list[str] = []
    if interpreter is not None:
        try:
            await asyncio.to_thread(interpreter.shutdown, strict_broker_cleanup=True)
        except BaseException:
            failures.append("interpreter")

    if platform is not None and sandbox_id:
        try:
            from fleet_rlm.daytona.lifecycle import confirm_absence

            target = await platform.get(sandbox_id)
            if target is not None:
                await platform.delete(target)
            absence = await confirm_absence(
                probe=platform.get,
                sandbox_id=sandbox_id,
                timeout_s=180.0,
                poll_interval_s=2.0,
            )
            if not absence.absent:
                failures.append("sandbox_absence")
        except BaseException:
            failures.append("sandbox")

    if client is not None:
        try:
            await _close_daytona_client(client)
        except BaseException:
            failures.append("client")
    return tuple(failures)


@pytest.mark.asyncio
async def test_p43_2_live_daytona_history_transport_is_private_and_inspectable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Prove complete ``dspy.History`` transport in a real, disposable Daytona Sandbox."""
    assert CERTIFIED_DSPY_VERSION == dspy.__version__ == "3.3.1"
    client: Any | None = None
    platform: Any | None = None
    sandbox: Any | None = None
    sandbox_id: str | None = None
    interpreter: DaytonaCodeInterpreter | None = None
    primary_error: BaseException | None = None

    messages = _canonical_history_messages()
    assert len(messages) == 35
    assert any("Ω漢字🧪" in body for body in _history_body_markers(messages))
    assert any("\n" in body for body in _history_body_markers(messages))
    history = CommittedSessionHistory(messages)
    history_sha256 = _canonical_history_sha256(messages)
    action = _HistoryInspectionAction()
    caplog.set_level(logging.INFO)

    try:
        client, platform, _spec = _require_live_platform()
        sandbox = await platform.create(
            with_volume=False,
            ephemeral=True,
            labels={"fleet.p43": "2", "purpose": "dspy-history-transport"},
        )
        sandbox_id = str(getattr(sandbox, "id", "")) or None
        assert sandbox_id is not None
        interpreter = DaytonaCodeInterpreter(
            backend=sandbox_backend(sandbox, loop=asyncio.get_running_loop(), timeout_s=120),
        )
        assert type(interpreter) is DaytonaCodeInterpreter
        rlm = dspy.RLM(
            _P43HistorySignature,
            max_iters=1,
            max_llm_calls=1,
            max_output_chars=97,
        )
        rlm.generate_action = action
        observations: list[object] = []
        interpreter.bind_observer(observations.append)

        # RLM executes synchronously inside its async method.  Keep that work on
        # a worker loop so the main loop can service the live sync-Sandbox bridge.
        prediction = await asyncio.to_thread(
            lambda: asyncio.run(
                rlm.acall(
                    interpreter,
                    request="P43.2 history transport inspection",
                    history=history,
                    history_sha256=history_sha256,
                    history_witness_path=_WITNESS_PATH,
                    history_message_count=len(messages),
                )
            )
        )

        assert prediction.answer == f"history-records={len(messages)} sha256={history_sha256}"
        assert action.repl_histories and action.repl_histories[0].entries == []
        assert any(isinstance(event, RLMCode) for event in observations)
        assert any(isinstance(event, RLMOutput) for event in observations)

        # This direct download is the private Sandbox inspection surface.  The
        # value is never forwarded through the observer, answer, or diagnostics.
        witness = (await sandbox.fs.download_file(_WITNESS_PATH)).decode("utf-8")
        assert json.loads(witness) == messages
        assert hashlib.sha256(witness.encode("utf-8")).hexdigest() == history_sha256

        public_observations = "\n".join(str(event) for event in observations)
        diagnostics = "\n".join(record.getMessage() for record in caplog.records)
        for body in _history_body_markers(messages):
            assert body not in public_observations
            assert body not in diagnostics
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_failures = await asyncio.shield(
            _cleanup_live_history_proof(
                client=client,
                platform=platform,
                sandbox_id=sandbox_id,
                interpreter=interpreter,
            )
        )
        if cleanup_failures:
            cleanup_error = AssertionError("P43.2 live cleanup failed: " + ", ".join(cleanup_failures))
            if primary_error is None:
                raise cleanup_error
            primary_error.add_note(str(cleanup_error))

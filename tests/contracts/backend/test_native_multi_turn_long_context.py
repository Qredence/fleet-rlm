"""Native RLM contract for a multi-turn URL source workflow."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.files.url_tool import UrlFetchResult, UrlToolHost, WorkspaceUrlSourceStore
from fleet_rlm.files.workspace_models import WorkspaceEntry, WorkspaceListResult, WorkspaceTextPage
from fleet_rlm.rlm.events import observe_tool
from fleet_rlm.rlm.program import RLMFactory, RLMModelBundle, RLMOptions
from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
from fleet_rlm.sessions.models import HistoryMessage, SessionHistory


class _FakeFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, url: str, *, max_bytes: int) -> UrlFetchResult:
        self.calls += 1
        assert url == "https://example.com/report"
        assert max_bytes >= 1
        return UrlFetchResult(
            url,
            "text/plain; charset=utf-8",
            "header\n" + ("x" * 80) + "\nneedle: 42\n" + ("y" * 80) + "\nsecond: 7\ntrailer",
        )


class _Workspace:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def stat(self, path: str) -> WorkspaceEntry | None:
        """Return metadata for a stored workspace file.

        Parameters:
            path (str): The workspace path to inspect.

        Returns:
            WorkspaceEntry | None: The file metadata, or `None` when no value is stored at the path.
        """
        value = self.values.get(path)
        return None if value is None else WorkspaceEntry(path, "file", len(value.encode()), None)

    def list_entries(self, path: str, *, limit: int = 100, after: str | None = None) -> WorkspaceListResult:
        """
        List files stored under a workspace path.

        Parameters:
            path (str): Directory-like path whose entries are listed.
            limit (int): Maximum number of entries to return.
            after (str | None): Cursor parameter, currently ignored.

        Returns:
            WorkspaceListResult: Matching file entries, truncation status, and no continuation cursor.
        """
        del after
        prefix = path.rstrip("/") + "/"
        entries = tuple(
            WorkspaceEntry(item, "file", len(value.encode()), None)
            for item, value in self.values.items()
            if item.startswith(prefix)
        )
        return WorkspaceListResult(entries[:limit], truncated=len(entries) > limit, next_cursor=None)

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage:
        """
        Read a bounded page of text from a workspace path.

        Parameters:
            path (str): Path of the stored text.
            cursor (str | None): Character offset from which to read.
            max_chars (int): Maximum number of characters to return.
            max_bytes (int): Maximum allowed encoded size of the stored text.

        Returns:
            WorkspaceTextPage: The requested text, continuation cursor, total byte
                size, and completion status.

        Raises:
            ValueError: If the stored text exceeds the byte limit.
        """
        value = self.values[path]
        if len(value.encode()) > max_bytes:
            raise ValueError("workspace read exceeded bound")
        offset = int(cursor or "0")
        content = value[offset : offset + max_chars]
        next_offset = offset + len(content)
        return WorkspaceTextPage(
            content,
            None if next_offset >= len(value) else str(next_offset),
            len(value.encode()),
            next_offset >= len(value),
        )

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry:
        """
        Write text content to a workspace path.

        Parameters:
                path (str): The destination path.
                content (str): The text to store.
                overwrite (bool): Whether to replace existing content at the path.

        Returns:
                WorkspaceEntry: Metadata for the stored file.

        Raises:
                FileExistsError: If the path already exists and overwriting is disabled.
        """
        if path in self.values and not overwrite:
            raise FileExistsError(path)
        self.values[path] = content
        return WorkspaceEntry(path, "file", len(content.encode()), None)


class _SemanticLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> list[dict[str, str]]:
        """Generate a deterministic semantic response for a prompt and record the prompt."""
        self.prompts.append(prompt)
        return [{"text": f"semantic:{len(prompt)}"}]


class _OneAction(dspy.Predict):
    def __init__(self, codes: list[str]) -> None:
        """Initialize the action generator with REPL code and history tracking."""
        super().__init__("variables_info, repl_history, iteration -> reasoning, code")
        self.codes = codes
        self.history_lengths: list[int] = []
        self._index = 0

    async def aforward(self, **kwargs: Any) -> dspy.Prediction:
        """
        Generate the next configured REPL action and record the current history length.

        Parameters:
                repl_history (list): The current REPL history used to record its length.

        Returns:
                dspy.Prediction: A prediction containing the configured REPL code and reasoning.
        """
        history = kwargs["repl_history"]
        self.history_lengths.append(len(history))
        code = self.codes[min(self._index, len(self.codes) - 1)]
        self._index += 1
        return dspy.Prediction(reasoning="Use the source through the native REPL.", code=code)


def _rlm(
    *, tools: tuple[dspy.Tool, ...], action: list[str], root_lm: Any, sub_lm: Any
) -> tuple[dspy.RLM, DaytonaCodeInterpreter]:
    """
    Create an RLM configured with the supplied tools, language models, and deterministic action sequence.

    Parameters:
        tools (tuple[dspy.Tool, ...]): Tools available to the RLM.
        action (list[str]): REPL actions generated sequentially during execution.
        root_lm (Any): Language model used for root-level reasoning.
        sub_lm (Any): Language model used for subqueries.

    Returns:
        tuple[dspy.RLM, DaytonaCodeInterpreter]: The configured RLM and its caller-owned interpreter.
    """
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    rlm = RLMFactory().create(
        models=RLMModelBundle(root_lm=root_lm, sub_lm=sub_lm),
        options=RLMOptions(max_iters=len(action), max_llm_calls=4),
        tools=tools,
        signature="request -> answer: str",
    )
    rlm.generate_action = _OneAction(action)
    return rlm, interpreter


@pytest.mark.asyncio
async def test_native_rlm_keeps_source_in_repl_and_reuses_session_cache_across_turns() -> None:
    """
    Verify multi-turn URL retrieval, caching, session-history access, REPL isolation, and bounded semantic queries.
    """
    session_id = uuid4()
    history_detail = "Earlier turn established cobalt-orchid."
    fetcher = _FakeFetcher()
    host = UrlToolHost(
        session_id=session_id,
        store=WorkspaceUrlSourceStore(_Workspace()),
        max_bytes=1_024,
        fetcher=fetcher,
    )
    observed: list[object] = []
    source_tool = observe_tool(host.as_tools()[0], observed.append, host.event_views()["fetch_url"])
    (history_source_tool,) = SessionHistoryToolHost(
        SessionHistory((HistoryMessage("assistant", history_detail),))
    ).as_tools()
    history_tool = observe_tool(
        history_source_tool,
        observed.append,
        SessionHistoryToolHost(SessionHistory(())).event_views()["read_session_history"],
    )
    semantic_lm = _SemanticLM()
    root_lm = dspy.utils.DummyLM(
        [{"reasoning": "submit answer", "code": "SUBMIT(answer='ok')"}],
        adapter=dspy.JSONAdapter(),
    )

    first, first_interpreter = _rlm(
        tools=(source_tool,),
        action=[
            "source = fetch_url(url='https://example.com/report')\n"
            "assert source['ok'] is True\n"
            "content = source['content']\n"
            "positions = [content.index('needle: 42'), content.index('second: 7')]\n"
            "excerpts = [content[max(0, position - 20):position + 20] for position in positions]\n"
            "batch = llm_query_batched(['Question: classify\\nEvidence: ' + excerpt for excerpt in excerpts])",
            "assert 'needle: 42' in content\n"
            "assert len(batch) == 2\n"
            "SUBMIT(answer=f'{content.count(\"needle: 42\")}|{len(batch)}')",
        ],
        root_lm=root_lm,
        sub_lm=semantic_lm,
    )
    first_prediction = await first.acall(first_interpreter, request="Analyze the URL")

    second, second_interpreter = _rlm(
        tools=(source_tool, history_tool),
        action=[
            "source = fetch_url(url='https://example.com/report')\n"
            "assert source['cache_hit'] is True\n"
            "assert 'content' not in globals()\n"
            "prior = read_session_history(offset=0, limit=1)\n"
            "assert prior['messages'][0]['content'] == 'Earlier turn established cobalt-orchid.'\n"
            "SUBMIT(answer=source['content'].split('\\n')[2] + '|' + prior['messages'][0]['content'])",
        ],
        root_lm=root_lm,
        sub_lm=semantic_lm,
    )
    second_prediction = await second.acall(second_interpreter, request="Follow up on the URL")

    first_interpreter.shutdown()
    second_interpreter.shutdown()

    assert first_prediction.answer == "1|2"
    assert second_prediction.answer == "needle: 42|Earlier turn established cobalt-orchid."
    assert first.generate_action.history_lengths == [0, 1]
    assert second.generate_action.history_lengths == [0]
    assert fetcher.calls == 1
    assert [
        item.output["cache_hit"] for item in observed if hasattr(item, "output") and "cache_hit" in item.output
    ] == [False, True]
    assert len(semantic_lm.prompts) == 2
    assert all(len(prompt) < 100 for prompt in semantic_lm.prompts)
    assert "header" not in str(observed)
    assert history_detail not in str(observed)

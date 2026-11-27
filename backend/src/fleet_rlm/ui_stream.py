"""Map an invocation's domain events to AI SDK v1 chunks and message snapshots."""

from collections.abc import Awaitable, Callable, Iterator
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal

from . import agent


class UiPartAccumulator:
    """Emit AI SDK v1 chunks and retain the matching JSON-serializable message parts."""

    def __init__(self, message_id: str):
        self.message_id = message_id
        self._parts: list[dict] = []
        self._tool_parts: dict[str, dict] = {}
        self.python_calls: dict[int, str] = {}
        self._next_id = 0

    def snapshot(self) -> list[dict]:
        return deepcopy(self._parts)

    def message(self) -> dict:
        return {"id": self.message_id, "role": "assistant", "parts": self.snapshot()}

    def new_id(self) -> str:
        self._next_id += 1
        return f"{self.message_id}:{self._next_id}"

    def start(self) -> dict:
        return {"type": "start", "messageId": self.message_id}

    def data(
        self,
        name: Literal["skills", "file-tree", "web-call", "turn-details", "python-submission"],
        value: dict,
        *,
        key: str | None = None,
    ) -> dict:
        part = {
            "type": f"data-{name}",
            "id": f"{self.message_id}:{key or name}",
            "data": deepcopy(value),
        }
        for index, existing in enumerate(self._parts):
            if existing.get("id") == part["id"]:
                self._parts[index] = part
                break
        else:
            self._parts.append(part)
        return deepcopy(part)

    def _text(self, kind: Literal["reasoning", "text"], text: str) -> Iterator[dict]:
        part_id = self.new_id()
        part = {"type": kind, "text": "", "state": "streaming"}
        if kind == "reasoning":
            part["id"] = part_id
        self._parts.append(part)
        yield {"type": f"{kind}-start", "id": part_id}
        part["text"] = text
        yield {"type": f"{kind}-delta", "id": part_id, "delta": text}
        part["state"] = "done"
        yield {"type": f"{kind}-end", "id": part_id}

    def reasoning(self, text: str) -> Iterator[dict]:
        return self._text("reasoning", text)

    def finish(self, answer: str) -> Iterator[dict]:
        yield from self._text("text", answer)
        yield {"type": "finish", "finishReason": "stop"}

    def _tool(self, call_id: str, name: str, state: str) -> dict:
        if call_id in self._tool_parts:
            raise ValueError(f"Duplicate UI tool call {call_id!r}")
        part = {"type": f"tool-{name}", "toolCallId": call_id, "state": state}
        self._tool_parts[call_id] = part
        self._parts.append(part)
        return part

    def python(self, call_id: str, code: str) -> Iterator[dict]:
        code = code.strip()
        if code.startswith("```") and code.endswith("```") and "\n" in code:
            code = code.split("\n", 1)[1][:-3].rstrip()
        part = self._tool(call_id, "python_exec", "input-streaming")
        part["inputEncoding"] = "text"
        yield {
            "type": "tool-input-start",
            "toolCallId": call_id,
            "toolName": "python_exec",
            "inputEncoding": "text",
        }
        part.update(input=code, rawInput=code)
        yield {"type": "tool-input-delta", "toolCallId": call_id, "inputTextDelta": code}

    def subagent(self, call_id: str, tool_input: dict) -> dict:
        part = self._tool(call_id, "subagent", "input-available")
        part["input"] = deepcopy(tool_input)
        return {
            "type": "tool-input-available",
            "toolCallId": call_id,
            "toolName": "subagent",
            "input": deepcopy(tool_input),
        }

    def tool_output(self, call_id: str, output: Any, *, preliminary: bool | None = None) -> dict:
        part = self._tool_parts[call_id]
        for key in ("inputEncoding", "rawInput", "errorText"):
            part.pop(key, None)
        part.update(state="output-available", output=deepcopy(output))
        frame = {"type": "tool-output-available", "toolCallId": call_id, "output": output}
        if preliminary is not None:
            part["preliminary"] = frame["preliminary"] = preliminary
        else:
            part.pop("preliminary", None)
        return deepcopy(frame)

    def tool_error(self, call_id: str, error: str) -> dict:
        part = self._tool_parts[call_id]
        for key in ("inputEncoding", "output", "preliminary"):
            part.pop(key, None)
        part.update(state="output-error", errorText=error)
        return {"type": "tool-output-error", "toolCallId": call_id, "errorText": error}


def error_frame(error: str) -> dict:
    return {"type": "error", "errorText": error}


@dataclass(frozen=True)
class _ChildStream:
    parent_id: str
    call_id: str
    parts: UiPartAccumulator


class UiStream:
    """Build live nested snapshots in memory for one HTTP turn."""

    def __init__(self, root_id: str, emit: Callable[[dict], Awaitable[None]]):
        self._root = UiPartAccumulator(root_id)
        self._children: dict[str, _ChildStream] = {}
        self._emit = emit
        self._web_calls: dict[str, dict] = {}

    def _parts(self, run_id: str) -> UiPartAccumulator:
        if run_id == self._root.message_id:
            return self._root
        return self._children[run_id].parts

    def reserve_call(self, run_id: str) -> str:
        return self._parts(run_id).new_id()

    def add_child(self, child_id: str, parent_id: str, call_id: str) -> None:
        self._children[child_id] = _ChildStream(parent_id, call_id, UiPartAccumulator(child_id))

    def discard_child(self, child_id: str) -> None:
        del self._children[child_id]

    def snapshot(self, run_id: str) -> list[dict]:
        return self._parts(run_id).snapshot()

    def pending_child_snapshots(self) -> dict[str, list[dict]]:
        return {run_id: child.parts.snapshot() for run_id, child in self._children.items()}

    async def _forward(self, run_id: str, frame: dict) -> None:
        while run_id != self._root.message_id:
            child = self._children[run_id]
            run_id = child.parent_id
            frame = self._parts(run_id).tool_output(
                child.call_id, child.parts.message(), preliminary=True
            )
        await self._emit(frame)

    async def skills(self, run_id: str, scores: list[tuple[str, float]]) -> None:
        skills = [{"name": name, "score": score} for name, score in scores]
        await self._forward(run_id, self._parts(run_id).data("skills", {"skills": skills}))

    async def files(self, run_id: str, files: list[dict]) -> None:
        await self._forward(run_id, self._parts(run_id).data("file-tree", {"files": files}))

    async def web_call(self, event: agent.WebCallEvent) -> None:
        row = asdict(event)
        if event.outcome == "running":
            self._web_calls[event.id] = row
        else:
            self._web_calls.pop(event.id, None)
        await self._emit(self._root.data("web-call", row, key=event.id))

    def interrupt_web_calls(self) -> list[dict]:
        frames = [
            self._root.data("web-call", {**call, "outcome": "interrupted"}, key=call_id)
            for call_id, call in self._web_calls.items()
        ]
        self._web_calls.clear()
        return frames

    def turn_details(self, details: dict) -> dict:
        return self._root.data("turn-details", details)

    @staticmethod
    def _python_result(
        parts: UiPartAccumulator,
        event: agent.PythonExecCompletedEvent,
    ) -> Iterator[dict]:
        call_id = parts.python_calls.pop(event.execution_id)
        # Record the terminal result before publishing the submission marker.
        output = parts.tool_output(call_id, event.output)
        if event.submission:
            yield parts.data("python-submission", {"tool_call_id": call_id}, key=call_id)
        yield output

    def _frames(self, parts: UiPartAccumulator, event: agent.RunEvent) -> Iterator[dict]:
        match event:
            case agent.RunStartedEvent():
                yield parts.start()
            case agent.ReasoningEvent(text):
                yield from parts.reasoning(text)
            case agent.PythonExecStartedEvent(execution_id, code):
                call_id = parts.new_id()
                parts.python_calls[execution_id] = call_id
                yield from parts.python(call_id, code)
            case agent.PythonExecProgressEvent(execution_id, stdout):
                yield parts.tool_output(parts.python_calls[execution_id], stdout, preliminary=True)
            case agent.PythonExecCompletedEvent():
                yield from self._python_result(parts, event)
            case agent.PythonExecFailedEvent(execution_id, error):
                yield parts.tool_error(parts.python_calls.pop(execution_id), error)
            case agent.SubagentStartedEvent(child):
                yield parts.subagent(
                    self._children[child.id].call_id, {"task": child.prompt, "kind": child.kind}
                )
            case agent.SubagentCompletedEvent(child_id):
                child = self._children.pop(child_id)
                yield parts.tool_output(child.call_id, child.parts.message(), preliminary=False)
            case agent.SubagentFailedEvent(child_id, error):
                yield parts.tool_error(self._children.pop(child_id).call_id, error)
            case agent.RunResultEvent(answer):
                yield from parts.finish(answer)

    async def publish(self, run_id: str, event: agent.RunEvent) -> None:
        if isinstance(event, agent.WebCallEvent):
            await self.web_call(event)
            return
        for frame in self._frames(self._parts(run_id), event):
            await self._forward(run_id, frame)

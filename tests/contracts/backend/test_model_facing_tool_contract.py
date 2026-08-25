"""P42.4: model-facing Tool contract stays aligned with DSPy and Fleet source."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import dspy

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "model-facing-tool-contract.json"

_REQUIRED_ENTRY_FIELDS = frozenset(
    {
        "name",
        "description",
        "argument_schema",
        "result_schema",
        "availability_policy",
        "authorization_policy",
        "event_projection_policy",
    }
)

_EXPECTED_CATEGORIES = {
    "semantic": {"llm_query", "llm_query_batched"},
    "recursive": {"rlm_query", "rlm_query_batched"},
    "session_history": {"read_session_history"},
    "workspace": {
        "list_workspace_files",
        "stat_workspace_file",
        "read_workspace_text",
        "write_workspace_text",
        "append_workspace_text",
        "delete_workspace_path",
        "edit_workspace_text",
    },
    "project": {
        "list_project_files",
        "stat_project_file",
        "read_project_text",
        "write_project_text",
        "delete_project_path",
        "edit_project_text",
    },
    "workspace_memory": {
        "read_workspace_memory",
        "remember",
        "update_workspace_memory",
        "list_memories",
        "search_memories",
        "edit_memory",
        "forget",
    },
    "skill": {"load_skill", "read_skill_resource"},
    "url": {"fetch_url"},
    "attachment_artifact": {"read_attachment", "create_artifact", "publish_workspace_artifact"},
    "conditional_capability": {"propose_memory", "read_curated_input"},
}


def _fixture() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def _argument_schema(properties: dict[str, Any]) -> dict[str, Any]:
    """Mirror the required-property rule in DSPy's Tool function-call formatter."""
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(name for name, schema in properties.items() if "default" not in schema),
        "additionalProperties": False,
    }


def _source_tools() -> dict[str, dspy.Tool]:
    """Construct Tool metadata only; no host capability is invoked."""
    from fleet_rlm.files.memory_candidate_tools import MemoryCandidateToolHost
    from fleet_rlm.files.memory_candidates import MemoryCandidateCollector
    from fleet_rlm.files.memory_models import WorkspaceMemoryStore
    from fleet_rlm.files.memory_tools import WorkspaceMemoryToolHost
    from fleet_rlm.files.project_tools import ProjectToolHost
    from fleet_rlm.files.tools import FileToolHost
    from fleet_rlm.files.url_tool import UrlSourceStore, UrlToolHost
    from fleet_rlm.files.volume_storage import VolumeBlobFs
    from fleet_rlm.files.workspace_models import SessionWorkspaceFS
    from fleet_rlm.files.workspace_tools import WorkspaceToolHost
    from fleet_rlm.optimization.curated_input import CuratedEvaluationStore
    from fleet_rlm.optimization.types import OptimizationRecord
    from fleet_rlm.rlm.model_bundle import RLMModelBundle
    from fleet_rlm.rlm.recursive_calls import RecursiveRLMExecutor, RecursiveRLMOptions
    from fleet_rlm.sessions.history_tools import SessionHistoryToolHost
    from fleet_rlm.sessions.models import SessionHistory
    from fleet_rlm.skills.catalog import SkillCatalog
    from fleet_rlm.skills.tools import SkillToolHost

    class _Record:
        def optimizer_example(self) -> dict[str, object]:
            return {
                "record_id": "fixture-record",
                "query": "fixture query",
                "output_contract": {},
                "expectations": [],
                "execution_requirements": {},
            }

    identifier = uuid4()
    recursive = RecursiveRLMExecutor(
        models=cast(RLMModelBundle, object()),
        options=RecursiveRLMOptions(),
        child_runtime_factory=None,
        deadline=0,
    )
    curated = CuratedEvaluationStore(candidate="fixture candidate", record=cast(OptimizationRecord, _Record()))
    tools = (
        *SessionHistoryToolHost(cast(SessionHistory, object())).as_tools(),
        *WorkspaceToolHost(cast(SessionWorkspaceFS, object()), max_file_bytes=1).as_tools(),
        *ProjectToolHost(cast(SessionWorkspaceFS, object()), max_file_bytes=1).as_tools(),
        *WorkspaceMemoryToolHost(cast(WorkspaceMemoryStore, object())).as_tools(),
        *MemoryCandidateToolHost(cast(MemoryCandidateCollector, object())).as_tools(),
        *FileToolHost(
            attachments=(),
            staged_attachments=(),
            volume_fs=cast(VolumeBlobFs, object()),
            user_id=identifier,
            workspace_id=identifier,
            session_id=identifier,
            run_id=identifier,
        ).as_tools(),
        *UrlToolHost(session_id=identifier, store=cast(UrlSourceStore, object()), max_bytes=1).as_tools(),
        *SkillToolHost(cast(SkillCatalog, object())).as_tools(),
        recursive.tool,
        recursive.batched_tool,
        dspy.Tool(
            curated.broker_tool(handle=curated.handle),
            name="read_curated_input",
            desc="Read bounded canonical curated evaluation input with the supplied capability handle.",
        ),
    )
    result = {str(tool.name): tool for tool in tools}
    assert len(result) == len(tools), "fixture source inventory must not contain duplicate Tool names"
    return result


def test_model_facing_tool_contract_fixture_matches_dspy_and_host_tool_metadata() -> None:
    fixture = _fixture()
    assert fixture["schema_version"] == 1
    assert fixture["dspy_version"] == dspy.__version__ == "3.3.1"

    entries = cast(list[dict[str, Any]], fixture["entries"])
    by_name = {entry["name"]: entry for entry in entries}
    assert len(by_name) == len(entries), "Tool names must be unique"
    assert all(entry.keys() >= _REQUIRED_ENTRY_FIELDS for entry in entries)
    assert {entry["category"] for entry in entries} == set(_EXPECTED_CATEGORIES)
    for category, names in _EXPECTED_CATEGORIES.items():
        assert {entry["name"] for entry in entries if entry["category"] == category} == names

    source_tools = _source_tools()
    host_entry_names = set(by_name) - {"llm_query", "llm_query_batched"}
    assert set(source_tools) == host_entry_names
    for name, tool in source_tools.items():
        entry = by_name[name]
        assert entry["description"] == tool.desc
        assert entry["argument_schema"] == _argument_schema(cast(dict[str, Any], tool.args))

    from dspy.predict.rlm import ACTION_INSTRUCTIONS_TEMPLATE

    native = dspy.RLM("prompt -> answer")
    native_tools = native._make_llm_tools()
    assert set(native_tools) == {"llm_query", "llm_query_batched"}
    for name, function in native_tools.items():
        entry = by_name[name]
        assert entry["description"] in ACTION_INSTRUCTIONS_TEMPLATE
        native_tool = dspy.Tool(function)
        assert entry["argument_schema"] == _argument_schema(cast(dict[str, Any], native_tool.args))


def test_model_facing_tool_contract_fixture_has_complete_policies_and_current_composition_guards() -> None:
    fixture = _fixture()
    definitions = cast(dict[str, Any], fixture["$defs"])
    entries = cast(list[dict[str, Any]], fixture["entries"])
    for entry in entries:
        result_schema = cast(dict[str, Any], entry["result_schema"])
        reference = result_schema.get("$ref")
        assert isinstance(reference, str) and reference.startswith("#/$defs/")
        assert reference.removeprefix("#/$defs/") in definitions
        assert isinstance(entry["availability_policy"], str) and entry["availability_policy"]
        assert isinstance(entry["authorization_policy"], str) and entry["authorization_policy"]
        projection = cast(dict[str, Any], entry["event_projection_policy"])
        assert projection["mode"] in {"bounded_allowlist", "none"}
        assert isinstance(projection["input"], list)
        assert isinstance(projection["output"], list)
        assert isinstance(projection["omits"], list)
        assert isinstance(projection["source"], str) and projection["source"]

    from fleet_rlm.chat import capability_preparation
    from fleet_rlm.composition import testing
    from fleet_rlm.daytona import run_environment
    from fleet_rlm.rlm import recursive_calls, runner

    prepared_source = inspect.getsource(capability_preparation.prepare_host_capabilities)
    testing_source = inspect.getsource(testing.TestingCapabilityPreparer.prepare)
    live_source = inspect.getsource(run_environment._LiveCapabilityPreparer.prepare)
    recursive_source = inspect.getsource(recursive_calls.RecursiveRLMExecutor._run_native_child)
    runner_source = inspect.getsource(runner.RLMRunner._start_worker)

    assert "SessionHistoryToolHost" in prepared_source
    assert "create_artifact", "publish_workspace_artifact" in testing_source
    assert "if self.settings.rlm_autonomous_memory_categories:" in live_source
    assert "tools=[child_executor.tool]" in recursive_source
    assert "is_authorized=lambda: not context.identity.authority.revoked" in runner_source

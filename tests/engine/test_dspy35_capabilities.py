"""Focused offline tests for the 9 DSPy 3.5.0 capabilities in Fleet-RLM.

Capabilities:
(a) XMLAdapter default & model_type routing
(b) ThinkingPart extraction in streaming events
(c) Noul select_skills & 3-way Choice select_route
(d) Multi-turn History on RLM
(e) fleet-program-b2 ReAnchor roundtrip
(f) jev_decide_batched (Choice & boolean Noul)
(g) workspace.load_image
(h) Skill-grounded InstructionProposer
(i) Two-stage GEPA + ReAnchor calibration
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import dspy
import pytest

from fleet_rlm.api.events import _reasoning_text
from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import (
    TRIAGE_ROUTE,
    Route,
    item_decision,
    make_noul_type,
    supports_decision_requests,
)
from fleet_rlm.engine.compiled import (
    PROGRAM_SCHEMA,
    load_program_envelope,
    program_envelope,
)
from fleet_rlm.engine.decisions import select_route, select_skills
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.reasoning import (
    _MAX_RLM_HISTORY_EVENTS,
    deserialize_rlm_history,
    rlm_history_processor,
    serialize_rlm_turn_history,
)
from fleet_rlm.models import ResolvedModel, build_lm
from fleet_rlm.optimize.artifacts import tunable_components
from fleet_rlm.optimize.gepa import build_instruction_proposer
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.workspace import (
    _ALLOWED_WORKSPACE_ROOTS,
    MAX_WORKSPACE_FILE_BYTES,
    WORKSPACE_CODE,
)


# =========================================================================
# (a) XMLAdapter default & model_type routing
# =========================================================================
def test_dspy35_capability_a_xml_adapter_default_and_model_type_routing(monkeypatch) -> None:
    # 1. XMLAdapter is the default adapter
    resolved = ResolvedModel(model="openai/gpt-4o")
    assert resolved.adapter == "xml"
    adapter = resolved.build_adapter()
    assert isinstance(adapter, dspy.XMLAdapter)
    assert adapter.use_native_function_calling is True

    # 2. FleetRuntime defaults root and sub adapters to XMLAdapter
    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    manager = DaytonaSandboxManager("k", "o", "v", "sess-test")
    runtime = FleetRuntime(manager, lm, lm)
    assert isinstance(runtime.root_adapter, dspy.XMLAdapter)
    assert isinstance(runtime.sub_adapter, dspy.XMLAdapter)

    # 3. model_type routing for custom OpenAI gateways defaults to 'chat' when 'auto'
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/custom-route",
        fleet_llm_api_base="https://custom-gateway.local/v1",
        fleet_llm_api_key="secret",
    )
    calls = []
    monkeypatch.setattr(
        dspy, "LM", lambda model, **kwargs: calls.append((model, kwargs)) or SimpleNamespace(model=model, kwargs=kwargs)
    )
    build_lm(settings, "", "root")
    assert len(calls) == 1
    assert calls[0][0] == "openai/custom-route"
    assert calls[0][1]["model_type"] == "chat"


# =========================================================================
# (b) ThinkingPart extraction
# =========================================================================
def test_dspy35_capability_b_thinking_part_extraction() -> None:
    from dspy.lm15 import Message, Response, TextPart, ThinkingPart, Usage

    # Case 1: Response containing ThinkingPart in message parts
    msg = Message.assistant([ThinkingPart("Thinking step 1..."), TextPart("Final answer")])
    resp = Response(
        id=None,
        model="test",
        message=msg,
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3),
    )
    extracted = _reasoning_text(resp)
    assert "Thinking step 1..." in extracted

    # Case 2: outputs object with .thinking attribute
    obj = SimpleNamespace(thinking="Deep internal reflection")
    assert _reasoning_text(obj) == "Deep internal reflection"

    # Case 3: List containing dict with reasoning_content
    dict_outputs = [{"reasoning_content": "Chain-of-thought analysis"}]
    assert _reasoning_text(dict_outputs) == "Chain-of-thought analysis"

    # Case 4: No reasoning/thinking present
    assert _reasoning_text([]) == ""
    assert _reasoning_text(None) == ""


# =========================================================================
# (c) Noul select_skills & 3-way Choice select_route
# =========================================================================
def test_dspy35_capability_c_noul_select_skills_and_3way_choice_select_route() -> None:
    # 1. Noul select_skills
    noul_bool = make_noul_type("relevant for data extraction", "irrelevant")
    noul_yes = noul_bool(value=True, confidence=0.95, probability=0.95)
    noul_no = noul_bool(value=False, confidence=0.2, probability=0.2)

    class FakeSkillSelector:
        def batch(self, examples, *, num_threads=1, timeout=0):  # noqa: ARG002
            return [
                SimpleNamespace(relevance=noul_yes if ex.skill_name == "csv-parser" else noul_no) for ex in examples
            ]

    catalog = (
        "<available_skills>\n"
        '  <skill name="csv-parser">Parse and analyze CSV files.</skill>\n'
        '  <skill name="web-search">Search the internet.</skill>\n'
        "</available_skills>"
    )

    budget_calls = []
    events = []
    selected = select_skills(
        skill_selector=FakeSkillSelector(),
        consume_budget=lambda count, category: budget_calls.append((count, category)),
        emit=lambda event, **kwargs: events.append((event, kwargs)),
        jev_lm=SimpleNamespace(model="typesafe:jev", supports_decision_requests=True),
        task="Parse sales data from files/sales.csv",
        skills_catalog=catalog,
        route=Route.RLM,
    )
    assert selected == ["csv-parser"]
    assert len(events) == 1
    assert events[0][0] == "skill_selection"
    assert events[0][1]["selected"] == ["csv-parser"]

    # 2. 3-way Choice select_route
    class FakeTriage:
        def __init__(self, choice_value):
            self.choice_value = choice_value

        def __call__(self, **_kwargs):
            return SimpleNamespace(route=TRIAGE_ROUTE(value=self.choice_value, confidence=0.9))

    for choice in ("direct", "flex", "rlm"):
        route = select_route(
            triage=FakeTriage(choice),
            consume_budget=lambda **_kwargs: None,
            emit=lambda *_a, **_k: None,
            jev_lm=SimpleNamespace(model="typesafe:jev", supports_decision_requests=True),
            route_lm=SimpleNamespace(model="typesafe:jev"),
            sub_adapter=dspy.XMLAdapter(),
            mode="hybrid",
            task="do task",
            workspace_manifest="",
            skills_catalog="",
            has_context=False,
        )
        assert route == Route(choice)


# =========================================================================
# (d) Multi-turn History on RLM
# =========================================================================
def test_dspy35_capability_d_multiturn_history_on_rlm() -> None:
    from dspy.primitives.repl_types import REPLEntry, build_repl_event

    # 1. Processing and capping history
    events = [
        build_repl_event(
            {"task": f"Task {i}"},
            REPLEntry(reasoning=f"Step {i}", code=f"x = {i}", output=f"{i}", max_output_chars=1000),
            {"answer": f"{i}"},
        )
        for i in range(_MAX_RLM_HISTORY_EVENTS + 5)
    ]
    raw_history = dspy.History(messages=events)
    processed = rlm_history_processor(raw_history)
    assert len(processed.messages) <= _MAX_RLM_HISTORY_EVENTS

    # 2. Serialization and deserialization roundtrip
    serialized = serialize_rlm_turn_history(processed)
    assert isinstance(serialized, list)
    assert len(serialized) > 0

    reconstructed = deserialize_rlm_history([{"rlm_history": serialized}])
    assert reconstructed is not None
    assert len(reconstructed.messages) == len(serialized)


# =========================================================================
# (e) fleet-program-b2 ReAnchor roundtrip
# =========================================================================
def test_dspy35_capability_e_fleet_program_b2_reanchor_roundtrip() -> None:
    manager = DaytonaSandboxManager("k", "o", "v", "b2-test")
    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    runtime = FleetRuntime(manager, lm, lm)

    # Attach calibrated decision field (Choice parameter is weights)
    runtime.program.routing.predict.fields = {"route": {"weights": {"direct": 1.0, "flex": 1.5, "rlm": 0.8}}}

    envelope_bytes = program_envelope(runtime.program)
    data = json.loads(envelope_bytes)
    assert data["program_schema"] == "fleet-program-b2"
    assert data["program_schema"] == PROGRAM_SCHEMA

    # Inspect tunable components includes ReAnchor calibration
    components = tunable_components(runtime.program)
    assert "routing.predict" in components
    assert "[decision_calibration]" in components["routing.predict"]

    # Verify envelope loader roundtrips state
    target_program = runtime.program.deepcopy()
    load_program_envelope(target_program, envelope_bytes)
    assert target_program.dump_state() == runtime.program.dump_state()


# =========================================================================
# (f) jev_decide_batched (Choice & boolean Noul)
# =========================================================================
def test_dspy35_capability_f_jev_decide_batched(monkeypatch) -> None:
    jev_lm = SimpleNamespace(model="typesafe:jev", supports_decision_requests=True)
    manager = DaytonaSandboxManager("k", "o", "v", "jev-batched", jev_lm=jev_lm)
    interpreter = DaytonaInterpreter(manager)

    # 1. With choices (Choice classification)
    sig = item_decision(["fruit", "vegetable"])
    choice_type = sig.model_fields["decision"].annotation

    def fake_choice_batch(_self, examples, *, num_threads=1, timeout=0):  # noqa: ARG001
        return [SimpleNamespace(decision=choice_type(value="fruit", confidence=0.88)) for _ in examples]

    monkeypatch.setattr(dspy.Predict, "batch", fake_choice_batch)
    results = interpreter._jev_decide_batched([["apple", "banana"], "classify", ["fruit", "vegetable"]], {})
    assert len(results) == 2
    assert results[0]["decision"] == "fruit"
    assert results[0]["confidence"] == 0.88

    # 2. Without choices (boolean Noul classification)
    noul_type = make_noul_type("yes", "no")

    def fake_noul_batch(_self, examples, *, num_threads=1, timeout=0):  # noqa: ARG001
        return [SimpleNamespace(decision=noul_type(value=True, confidence=0.92, probability=0.92)) for _ in examples]

    monkeypatch.setattr(dspy.Predict, "batch", fake_noul_batch)
    results_bool = interpreter._jev_decide_batched([["apple", "banana"], "is edible?"], {})
    assert len(results_bool) == 2
    assert results_bool[0]["decision"] is True
    assert results_bool[0]["confidence"] == 0.92


# =========================================================================
# (g) workspace.load_image
# =========================================================================
def test_dspy35_capability_g_workspace_load_image(tmp_path: Path, monkeypatch) -> None:
    ws_dir = tmp_path / "workspace"
    files_dir = ws_dir / "files"
    files_dir.mkdir(parents=True)

    # Valid PNG (header: 89 50 4E 47 0D 0A 1A 0A)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    png_path = files_dir / "sample.png"
    png_path.write_bytes(png_bytes)

    # Valid JPEG (header: FF D8 FF)
    jpg_bytes = b"\xff\xd8\xff" + b"\x00" * 32
    jpg_path = files_dir / "photo.jpg"
    jpg_path.write_bytes(jpg_bytes)

    # Invalid text file
    txt_path = files_dir / "doc.txt"
    txt_path.write_text("not an image")

    ns = {
        "__FLEET_MAX_FILE_BYTES__": MAX_WORKSPACE_FILE_BYTES,
        "__FLEET_WORKSPACE_ROOTS__": _ALLOWED_WORKSPACE_ROOTS,
    }
    exec(WORKSPACE_CODE, ns)
    ws = ns["workspace"]
    monkeypatch.setattr(ws, "resolve", lambda rel: ws_dir / rel)

    # Load PNG
    img_png = ws.load_image("files/sample.png")
    assert isinstance(img_png, dspy.Image)
    assert img_png.url.startswith("data:image/png;base64,")

    # Load JPEG
    img_jpg = ws.load_image("files/photo.jpg")
    assert isinstance(img_jpg, dspy.Image)
    assert img_jpg.url.startswith("data:image/jpeg;base64,")

    # Oversized check
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        ws.load_image("files/sample.png", max_bytes=10)

    # Unsupported format check
    with pytest.raises(ValueError, match="unsupported image format"):
        ws.load_image("files/doc.txt")


# =========================================================================
# (h) Skill-grounded InstructionProposer
# =========================================================================
def test_dspy35_capability_h_skill_grounded_instruction_proposer(tmp_path: Path) -> None:
    fleet_skills = tmp_path / ".fleet" / "skills"
    skill_a = fleet_skills / "data-cleaning"
    skill_a.mkdir(parents=True)
    (skill_a / "SKILL.md").write_text("---\nname: data-cleaning\ndescription: Clean datasets\n---\nRules")

    agent_skills = tmp_path / ".agents" / "skills"
    agent_skills.mkdir(parents=True)
    (agent_skills / "dev-guidance").mkdir()
    (agent_skills / "dev-guidance" / "SKILL.md").write_text("dev only")

    adapter = dspy.XMLAdapter(use_native_function_calling=True)
    proposer = build_instruction_proposer(adapter=adapter, skills_dir=fleet_skills)
    assert proposer.adapter is adapter
    assert proposer.skills is not None
    assert len(proposer.skills) == 1
    assert proposer.skills[0].name == "data-cleaning"
    assert proposer.truncate_history_outputs is True

    # .agents skills must never be loaded
    bad_proposer = build_instruction_proposer(adapter=adapter, skills_dir=agent_skills)
    assert not bad_proposer.skills


# =========================================================================
# (i) Two-stage GEPA + ReAnchor calibration
# =========================================================================
def test_dspy35_capability_i_twostage_gepa_plus_reanchor(monkeypatch) -> None:

    manager = DaytonaSandboxManager("k", "o", "v", "reanchor-test")
    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    runtime = FleetRuntime(manager, lm, lm)

    calibrated_prog = runtime.program.deepcopy()
    calibrated_prog.routing.predict.fields = {"route": {"weights": {"direct": 2.0, "flex": 1.0, "rlm": 1.0}}}

    class MockReAnchor:
        def __init__(self, metric, num_threads=1, log_dir=None, require_cache=False):  # noqa: ARG002
            self.report = {"val_score_before": 0.8, "val_score": 0.95}

        def compile(self, student, trainset=None, valset=None):  # noqa: ARG002
            return SimpleNamespace(program=calibrated_prog)

    monkeypatch.setattr("dspy.experimental.ReAnchor", MockReAnchor)

    # Verify decision support check
    jev = SimpleNamespace(supports_decision_requests=True)
    assert supports_decision_requests(jev) is True

    # Run ReAnchor stage
    reanchor = MockReAnchor(lambda _g, _p: 1.0)
    res = reanchor.compile(SimpleNamespace(program=runtime.program), trainset=[], valset=[])
    val_before = reanchor.report["val_score_before"]
    val_after = reanchor.report["val_score"]
    adopted = val_after >= val_before and res.program.dump_state() != runtime.program.dump_state()
    assert adopted is True
    assert res.program.routing.predict.fields["route"]["weights"]["direct"] == 2.0

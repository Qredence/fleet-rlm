"""Selection comparison contracts; no live credentials or providers."""

from pathlib import Path

import dspy
import pytest
from dspy.experimental import Noul

from fleet_rlm.optimization.skill_selection import (
    SelectionCase,
    SkillRecommender,
    calibration_examples,
    calibration_metric,
    load_cases,
    make_predictor,
    run_selection_case,
    summarize,
)
from fleet_rlm.skills.catalog import build_bundled_skill_catalog, stable_skill_id
from fleet_rlm.skills.tools import SkillToolHost


def prediction(catalog, probabilities):
    return dspy.Prediction(
        **{
            "skill_" + card.name.replace("-", "_"): Noul(
                value=probabilities.get(card.name, 0) >= 0.5,
                confidence=abs(probabilities.get(card.name, 0) - 0.5) * 2,
                probability=probabilities.get(card.name, 0),
            )
            for card in catalog.cards()
        }
    )


def case(**overrides):
    defaults = dict(
        id="fixture",
        family="fixture",
        split="heldout",
        request="Analyze the supplied CSV.",
        required=("data-analysis",),
        allowed=("data-analysis", "web-research"),
    )
    return SelectionCase(**{**defaults, **overrides})


def test_dataset_has_disjoint_reviewed_partitions_and_calibration_inputs():
    catalog = build_bundled_skill_catalog()
    cases = load_cases(Path("scripts/benchmarks/skill_selection_cases.json"), catalog)
    assert len(cases) == 36
    train = {row.family for row in cases if row.split == "calibration"}
    heldout = {row.family for row in cases if row.split == "heldout"}
    assert len(train) == len(heldout) == 18 and not train & heldout
    examples = calibration_examples(cases, catalog)
    assert len(examples) == 16  # Explicit selection and full-capacity cases bypass prediction.
    assert all(set(example.inputs()) == {"task", "candidates", "available_tools"} for example in examples)
    assert all(len(example.labels()) == len(catalog.cards()) for example in examples)


def test_dataset_rejects_family_leakage(tmp_path):
    import json

    raw = json.loads(Path("scripts/benchmarks/skill_selection_cases.json").read_text())
    raw[-1]["family"] = raw[0]["family"]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="family"):
        load_cases(path, build_bundled_skill_catalog())


def test_recommender_filters_forbidden_and_loaded_cards_and_caps_capacity():
    catalog = build_bundled_skill_catalog()
    host = SkillToolHost(catalog)
    for name in ("dspy-rlm", "long-context", "workspace-files"):
        host.mark_preloaded(catalog.require(stable_skill_id(name)))
    calls = []

    def predict(**inputs):
        calls.append(inputs)
        return prediction(catalog, {"data-analysis": 0.8, "report-builder": 0.8, "web-research": 1.0})

    recommender = SkillRecommender(
        catalog, host, predict, allowed=["data-analysis", "report-builder", "workspace-files"], available_tools=[]
    )
    result = recommender.recommend_skills("Analyze data and report it.")
    assert result["ok"]
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["id"] == str(stable_skill_id("data-analysis"))
    assert {card["name"] for card in calls[0]["candidates"]} == {"data-analysis", "report-builder"}
    assert len(host.loaded_skill_ids) == 3  # Advice does not activate anything.


def test_recommender_allows_none_and_rejects_unusable_evidence_safely():
    catalog = build_bundled_skill_catalog()
    host = SkillToolHost(catalog)
    recommender = SkillRecommender(
        catalog, host, lambda **_: prediction(catalog, {}), allowed=["web-research"], available_tools=[]
    )
    assert recommender.recommend_skills("No research needed.") == {"ok": True, "recommendations": []}
    recommender.predictor = lambda **_: dspy.Prediction()
    assert recommender.recommend_skills("Research.") == {"ok": False, "error": "recommendations_unavailable"}
    assert recommender.recommend_skills(" ")["error"] == "invalid_task"


def test_full_capacity_never_invokes_provider():
    catalog = build_bundled_skill_catalog()
    host = SkillToolHost(catalog)
    for card in catalog.cards()[:4]:
        host.mark_preloaded(catalog.require(card.id))

    def unexpected(**_):
        raise AssertionError("provider should not run")

    helper = SkillRecommender(
        catalog, host, unexpected, allowed=[card.name for card in catalog.cards()], available_tools=[]
    )
    assert helper.recommend_skills("Find relevant Skills.") == {"ok": True, "recommendations": []}
    assert helper.calls == helper.failures == 0


def test_predictor_has_independent_top_level_decisions_and_weighted_metric():
    catalog = build_bundled_skill_catalog()
    lm = dspy.utils.DummyLM([])
    predictor = make_predictor(catalog, lm)
    assert len(predictor.signature.output_fields) == len(catalog.cards())
    assert all(field.annotation is Noul for field in predictor.signature.output_fields.values())
    sample = dspy.Example(a=True, b=False).with_inputs()
    assert calibration_metric(sample, dspy.Prediction(a=False, b=False)) == pytest.approx(1 / 3)
    assert calibration_metric(sample, dspy.Prediction(a=True, b=True)) == pytest.approx(2 / 3)


def test_native_loop_can_request_advice_then_explicitly_load_skill():
    catalog = build_bundled_skill_catalog()
    root = dspy.utils.DummyLM(
        [
            {
                "reasoning": "Ask for advice and activate the relevant Skill.",
                "code": "r = recommend_skills(task=request)\n"
                "for item in r['recommendations']:\n"
                "    load_skill(skill_id=item['id'], expected_version=item['expected_version'])\n"
                "SUBMIT(answer='Selected data analysis.')",
            },
        ],
        adapter=dspy.JSONAdapter(),
    )
    row = run_selection_case(
        case(),
        catalog,
        root_lm=root,
        predictor=lambda **_: prediction(catalog, {"data-analysis": 0.9}),
        interpreter_factory=dspy.LocalInterpreter,
    )
    assert row["success"] and row["missing_required"] == []
    assert row["loaded"] == ["data-analysis"]
    assert row["recommendation_calls"] == row["root_calls"] == 1


def test_failed_advice_leaves_root_free_to_use_existing_loader():
    catalog = build_bundled_skill_catalog()
    card = catalog.require(stable_skill_id("data-analysis")).card
    root = dspy.utils.DummyLM(
        [
            {
                "reasoning": "Continue after unavailable advice.",
                "code": "r = recommend_skills(task=request)\n"
                f"load_skill(skill_id='{card.id}', expected_version='{card.version}')\n"
                "SUBMIT(answer='Loaded directly.')",
            },
        ],
        adapter=dspy.JSONAdapter(),
    )
    row = run_selection_case(
        case(),
        catalog,
        root_lm=root,
        predictor=lambda **_: dspy.Prediction(),
        interpreter_factory=dspy.LocalInterpreter,
    )
    assert row["success"] and not row["missing_required"]
    assert row["recommendation_failures"] == 1


def test_explicit_selection_bypasses_both_root_and_predictor():
    catalog = build_bundled_skill_catalog()
    row = run_selection_case(
        case(loaded=("data-analysis",), allowed=("data-analysis",), explicit=True),
        catalog,
        root_lm=None,
        predictor=object(),
    )
    assert row["explicit_bypass"] and row["root_calls"] == row["recommendation_calls"] == 0
    assert not row["missing_required"]
    baseline = {**row, "arm": "root"}
    report = summarize([baseline, row], [case()])
    assert report["root"]["required_skill_recall"] == 1
    assert "Keep production" in report["recommendation"]


def test_typesafe_sdk_translation_and_reanchor_calibration_without_network(monkeypatch, tmp_path):
    from types import SimpleNamespace

    sdk = pytest.importorskip("typesafe_sdk")
    from dspy.clients.cache import Cache
    from dspy.experimental import ReAnchor, TypeSafe

    monkeypatch.setattr(
        dspy, "cache", Cache(enable_memory_cache=True, enable_disk_cache=False, disk_cache_dir=str(tmp_path))
    )
    requests = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "synthetic-test-key"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def system_one(self, *, state, questions):
            requests.append((state, questions))
            needs_data = "CSV" in state["inputs"]["task"]
            return SimpleNamespace(
                model="synthetic-jev",
                usage=SimpleNamespace(input_tokens=12, output_tokens=6),
                answers={
                    name: SimpleNamespace(noul=0.3 if name == "skill_data_analysis" and needs_data else 0.1)
                    for name in questions
                },
            )

    monkeypatch.setattr(sdk, "TypeSafeClient", Client)
    catalog = build_bundled_skill_catalog()
    lm = TypeSafe("synthetic-jev", api_key="synthetic-test-key", cache=True)
    student = make_predictor(catalog, lm)
    examples = calibration_examples(
        [case(split="calibration", request=f"CSV {i}") for i in range(4)]
        + [case(split="calibration", request=f"No data {i}", required=()) for i in range(4)],
        catalog,
    )
    compiled = ReAnchor(calibration_metric, num_threads=1).compile(student, trainset=examples)
    assert student.fields == {}  # The original predictor remains unchanged.
    assert compiled.fields["skill_data_analysis"]["threshold"] <= 0.3
    assert len(requests) == 8  # Calibration reuses cached evidence.
    assert "synthetic-test-key" not in repr(requests)
    compiled.get_lm().cache = False
    host = SkillToolHost(catalog)
    helper = SkillRecommender(catalog, host, compiled, allowed=["data-analysis"], available_tools=[])
    result = helper.recommend_skills("Held-out CSV")
    assert result["ok"] and len(result["recommendations"]) == 1
    assert result["recommendations"][0]["probability"] == 0.3
    assert len(requests) == 9


def test_loop_error_is_reported_even_when_selection_recovers():
    catalog = build_bundled_skill_catalog()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "Synthetic interpreter error.", "code": "undefined_name"},
            {"reasoning": "Report without more calls.", "code": "SUBMIT(answer='No Skill loaded.')"},
        ],
        adapter=dspy.JSONAdapter(),
    )
    row = run_selection_case(case(), catalog, root_lm=root, interpreter_factory=dspy.LocalInterpreter)
    assert row["success"] and row["interpreter_errors"] == 1
    assert row["root_calls"] == row["iterations"] == 2


def test_operator_plan_never_reads_keys_or_constructs_models(monkeypatch, capsys):
    import importlib.util
    import json
    import sys

    spec = importlib.util.spec_from_file_location("skill_selection_runner", "scripts/evaluate_skill_selection.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def unexpected(*_args, **_kwargs):
        pytest.fail("credential-free planning attempted model setup or key access")

    monkeypatch.setattr(module, "dotenv_values", unexpected)
    monkeypatch.setattr(module, "build_model_bundle", unexpected)
    monkeypatch.setattr(module, "load_runtime_settings", unexpected)
    monkeypatch.setattr(sys, "argv", ["evaluate_skill_selection.py", "--plan"])
    module.main()
    planned = json.loads(capsys.readouterr().out)
    assert planned["heldout_cases"] == planned["calibration_cases"] == 18
    assert planned["jev_model"] == "jev-latest"

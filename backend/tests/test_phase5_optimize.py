"""Offline verification for Phase 5 optimization CLI (GEPA, ReAnchor, Neon guard)."""

import json
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import dspy
import pytest

from fleet_rlm import agent, db, optimize, skills
from fleet_rlm.config import Settings

_BRANCH_URL = (
    "postgresql+psycopg://user:password@ep-branch-123-pooler.region.aws.neon.tech/fleet"
    "?sslmode=require"
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for name, desc in (
        ("data-analysis", "Analyze tabular CSV data"),
        ("charts", "Render matplotlib visualizations"),
    ):
        skill_dir = skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\nUse {name}.\n",
            encoding="utf-8",
        )
    return Settings(
        _env_file=None,
        database_url=(
            "postgresql+psycopg://user:password@ep-prod-pooler.region.aws.neon.tech/fleet"
            "?sslmode=require"
        ),
        database_direct_url=(
            "postgresql+psycopg://user:password@ep-prod.region.aws.neon.tech/fleet?sslmode=require"
        ),
        daytona_api_key="offline",
        daytona_volume_id="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        skills_dir=skills_dir,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_guard_database_url_rejects_configured_urls_unless_allowed(settings: Settings) -> None:
    with pytest.raises(optimize.DatabaseSafetyError):
        optimize.guard_database_url(settings.database_url, settings)
    with pytest.raises(optimize.DatabaseSafetyError):
        optimize.guard_database_url(settings.database_direct_url, settings)
    with pytest.raises(optimize.DatabaseSafetyError):
        optimize.guard_database_url(
            "postgresql://other:pw@ep-prod-pooler.region.aws.neon.tech/fleet?application_name=x&sslmode=require",
            settings,
        )

    assert optimize.guard_database_url(_BRANCH_URL, settings) == _BRANCH_URL
    assert optimize.guard_database_url(
        settings.database_url, settings, allow_direct=True
    ) == optimize._normalize_database_url(settings.database_url)


def test_cli_refuses_primary_database_url_with_exit_code_2(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    dataset = _write_jsonl(
        tmp_path / "rlm.jsonl",
        [{"task": "t1", "answer": "a1"}, {"task": "t2", "answer": "a2"}],
    )
    called = False

    def unexpected(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Optimizer must not run when DB guard rejects URL")

    monkeypatch.setattr(optimize, "optimize_rlm", unexpected)
    code = optimize.main(
        [
            "--target",
            "rlm",
            "--database-url",
            settings.database_url,
            "--dataset",
            str(dataset),
        ],
        settings=settings,
    )
    assert code == 2
    assert not called
    assert "Refusing to run optimization" in capsys.readouterr().err


def test_metrics_cover_exact_partial_and_decision_cases() -> None:
    gold_rlm = dspy.Example(answer="42 rows total")
    exact = optimize.rlm_metric(gold_rlm, dspy.Prediction(answer="  42   ROWS total "))
    partial = optimize.rlm_metric(gold_rlm, dspy.Prediction(answer="42 rows"))
    empty = optimize.rlm_metric(gold_rlm, dspy.Prediction(answer=""))
    assert exact.score == 1.0
    assert 0.5 < partial.score < 1.0
    assert empty.score == 0.0

    gold_skill = dspy.Example(needs_skill=True, expected_skill="data-analysis")
    assert (
        optimize.skill_metric(
            gold_skill,
            SimpleNamespace(
                needs_skill=SimpleNamespace(value=True),
                primary_skill=SimpleNamespace(value="data-analysis"),
            ),
        )
        == 1.0
    )
    assert (
        optimize.skill_metric(
            dspy.Example(needs_skill=False, expected_skill=None),
            SimpleNamespace(
                needs_skill=SimpleNamespace(value=False),
                primary_skill=SimpleNamespace(value="charts"),
            ),
        )
        == 1.0
    )
    assert (
        optimize.skill_metric(
            gold_skill,
            SimpleNamespace(
                needs_skill=SimpleNamespace(value=True),
                primary_skill=SimpleNamespace(value="charts"),
            ),
        )
        == 0.0
    )

    gold_mem = dspy.Example(worth_saving=True, action="insert", scope="global")
    assert (
        optimize.memory_metric(
            gold_mem,
            SimpleNamespace(
                worth_saving=SimpleNamespace(value=True),
                action=SimpleNamespace(value="insert"),
                scope=SimpleNamespace(value="global"),
            ),
        )
        == 1.0
    )
    assert (
        optimize.memory_metric(
            gold_mem,
            SimpleNamespace(
                worth_saving=SimpleNamespace(value=True),
                action=SimpleNamespace(value="update"),
                scope=SimpleNamespace(value="session"),
            ),
        )
        == 0.5
    )
    assert (
        optimize.memory_metric(
            dspy.Example(worth_saving=False, action="skip", scope="session"),
            SimpleNamespace(
                worth_saving=SimpleNamespace(value=False),
                action=SimpleNamespace(value="insert"),
                scope=SimpleNamespace(value="session"),
            ),
        )
        == 1.0
    )


def test_optimize_rlm_holdout_gate_force_flex_and_mlflow(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _write_jsonl(
        tmp_path / "rlm.jsonl",
        [{"task": f"Task {index}", "answer": f"Ans {index}"} for index in range(10)],
    )

    class FakeGEPA:
        instances: list["FakeGEPA"] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.compile_calls: list[dict] = []
            FakeGEPA.instances.append(self)

        def compile(self, student, *, trainset, valset):
            self.compile_calls.append({"student": student, "trainset": trainset, "valset": valset})
            compiled = student.deepcopy()
            for predictor in compiled.predictors():
                predictor.lm = dspy.LM("openai/gpt-4o-mini", api_key="secret")
            return compiled

    scores = iter([60.0, 55.0, 60.0, 80.0, 60.0, 50.0, 40.0, 75.0])

    class FakeEvaluate:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __call__(self, program):
            return SimpleNamespace(score=next(scores))

    autolog_calls: list[dict] = []
    logged_artifacts: list[str] = []
    monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
    monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
    monkeypatch.setattr(optimize.agent, "_model", lambda _s, name: dspy.LM(name, api_key="offline"))
    monkeypatch.setattr(optimize.mlflow, "set_tracking_uri", lambda _uri: None)
    monkeypatch.setattr(optimize.mlflow, "set_experiment", lambda _exp: None)
    monkeypatch.setattr(
        optimize.mlflow,
        "start_run",
        lambda **_kwargs: nullcontext(),
    )
    monkeypatch.setattr(
        optimize.mlflow.dspy,
        "autolog",
        lambda **kwargs: autolog_calls.append(kwargs),
    )
    monkeypatch.setattr(optimize.mlflow, "log_params", lambda _params: None)
    monkeypatch.setattr(optimize.mlflow, "log_metrics", lambda _metrics: None)
    monkeypatch.setattr(
        optimize.mlflow,
        "log_artifact",
        lambda path: logged_artifacts.append(path),
    )

    # 1. Regression without --force does not save
    rejected_path = tmp_path / "rejected.json"
    rejected = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=rejected_path,
    )
    assert rejected.saved is False
    assert (rejected.train_size, rejected.holdout_size) == (7, 3)
    assert (rejected.baseline_score, rejected.optimized_score) == (60.0, 55.0)
    assert not rejected_path.exists()

    # 2. Improvement saves artifact and logs to MLflow when configured
    mlflow_settings = settings.model_copy(update={"mlflow_tracking_uri": "http://127.0.0.1:5001"})
    improved_path = tmp_path / "artifacts" / "rlm_program.json"
    improved = optimize.optimize_rlm(
        mlflow_settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=improved_path,
    )
    assert improved.saved is True
    assert improved_path.exists()
    reloaded = agent.build_program(
        settings.model_copy(update={"optimized_program_path": improved_path}), []
    )
    assert all(predictor.lm is None for predictor in reloaded.predictors())
    assert autolog_calls == [
        {"log_compiles": True, "log_evals": True, "log_traces_from_compile": True},
        {"disable": True},
    ]
    assert logged_artifacts == [str(improved_path)]

    # 3. Regression with --force saves anyway
    forced_path = tmp_path / "forced.json"
    forced = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=forced_path,
        force=True,
    )
    assert forced.saved is True
    assert forced_path.exists()

    # 4. Flex program compiles dspy.Flex
    flex_path = tmp_path / "flex.json"
    flex_result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=flex_path,
        program_kind="flex",
    )
    assert flex_result.saved is True
    assert isinstance(FakeGEPA.instances[-1].compile_calls[0]["student"], dspy.Flex)


@pytest.mark.asyncio
async def test_load_rlm_examples_from_db_reads_completed_roots(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = [
        SimpleNamespace(
            prompt="Summarize sales.csv",
            answer="Total is 42",
            ui_parts=[
                {
                    "type": "data-file-tree",
                    "id": "r1:file-tree",
                    "data": {"files": [{"path": "files/sales.csv", "kind": "input"}]},
                }
            ],
        ),
        SimpleNamespace(prompt="Quick math", answer="4", ui_parts=[]),
    ]
    disposed = False

    class FakeSession:
        async def scalars(self, _query):
            return SimpleNamespace(all=lambda: runs)

    @asynccontextmanager
    async def fake_sessionmaker():
        yield FakeSession()

    class FakeEngine:
        async def dispose(self):
            nonlocal disposed
            disposed = True

    monkeypatch.setattr(db, "create_db", lambda _s: (FakeEngine(), fake_sessionmaker))
    examples = await optimize.load_rlm_examples_from_db(_BRANCH_URL, settings, limit=10)
    assert disposed is True
    assert len(examples) == 2
    assert examples[0].task == "Summarize sales.csv"
    assert examples[0].answer == "Total is 42"
    assert examples[0].files_manifest == [{"path": "files/sales.csv", "kind": "input"}]
    assert examples[1].files_manifest == []


def test_optimize_skills_reanchor_and_cli_reminder(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    dataset = _write_jsonl(
        tmp_path / "skills.jsonl",
        [
            {"task": "Summarize CSV columns", "expected_skill": "@data-analysis"},
            {"task": "Plot revenue chart", "expected_skill": "charts"},
            {"task": "Say hello", "expected_skill": None},
            {"task": "Group CSV rows", "expected_skill": "data-analysis"},
        ],
    )
    captured: dict = {}

    class FakeReAnchor:
        def __init__(self, metric, **kwargs):
            captured["metric"] = metric
            captured["kwargs"] = kwargs
            self.report = {
                "train_score_before": 0.5,
                "train_score": 0.9,
                "val_score_before": 0.5,
                "val_score": 1.0,
                "fitted": {"needs_skill": {"threshold": 0.65}},
            }

        def compile(self, student, *, trainset, valset=None):
            captured["student"] = student
            captured["trainset"] = trainset
            captured["valset"] = valset
            compiled = student.deepcopy()
            compiled.fields = {
                "needs_skill": {"threshold": 0.65},
                "primary_skill": {"weights": {"data-analysis": 1.5}},
            }
            return compiled

    monkeypatch.setattr(optimize, "ReAnchor", FakeReAnchor)
    monkeypatch.setattr(optimize.dspy, "LM", MagicMock(return_value="dummy-jev"))
    output_path = tmp_path / "skill_selector.json"
    exit_code = optimize.main(
        [
            "--target",
            "skills",
            "--database-url",
            _BRANCH_URL,
            "--dataset",
            str(dataset),
            "--output",
            str(output_path),
        ],
        settings=settings,
    )
    assert exit_code == 0
    assert captured["metric"] is optimize.skill_metric
    assert isinstance(captured["student"], dspy.Predict)
    assert {"needs_skill", "primary_skill"} <= set(captured["student"].signature.output_fields)
    assert len(captured["trainset"]) == 3 and len(captured["valset"]) == 1
    assert output_path.is_file()

    reloaded = skills.build_skill_selector(
        skills.SkillRegistry.load(settings.skills_dir).skills, output_path
    )
    assert reloaded.fields["needs_skill"]["threshold"] == 0.65
    assert reloaded.fields["primary_skill"]["weights"]["data-analysis"] == 1.5

    stdout = capsys.readouterr().out
    assert "FLEET_DECISION_MODEL" in stdout
    assert "typesafe:jev-latest" in stdout


def test_optimize_memory_reanchor_and_cli_reminder(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    dataset = _write_jsonl(
        tmp_path / "memory.jsonl",
        [
            {
                "turn_exchange": {"task": "Use UTC timestamps", "answer": "Noted."},
                "should_save": True,
                "action": "insert",
                "scope": "global",
            },
            {
                "task": "Update batch size preference",
                "answer": "Set to 64.",
                "worth_saving": True,
                "action": "update",
                "scope": "session",
            },
            {
                "task": "What is 2+2?",
                "answer": "4",
                "should_save": False,
                "action": "skip",
                "scope": "session",
            },
        ],
    )
    captured: dict = {}

    class FakeReAnchor:
        def __init__(self, metric, **kwargs):
            captured["metric"] = metric
            self.report = {
                "train_score_before": 0.6,
                "train_score": 0.95,
                "val_score_before": 0.5,
                "val_score": 1.0,
                "fitted": {"worth_saving": {"threshold": 0.82}},
            }

        def compile(self, student, *, trainset, valset=None):
            captured["student"] = student
            captured["trainset"] = trainset
            captured["valset"] = valset
            compiled = student.deepcopy()
            compiled.fields = {"worth_saving": {"threshold": 0.82}}
            return compiled

    monkeypatch.setattr(optimize, "ReAnchor", FakeReAnchor)
    monkeypatch.setattr(optimize.dspy, "LM", MagicMock(return_value="dummy-jev"))
    output_path = tmp_path / "memory_gate.json"
    exit_code = optimize.main(
        [
            "--target",
            "memory",
            "--database-url",
            _BRANCH_URL,
            "--dataset",
            str(dataset),
            "--output",
            str(output_path),
        ],
        settings=settings,
    )
    assert exit_code == 0
    assert captured["metric"] is optimize.memory_metric
    assert isinstance(captured["student"], dspy.Predict)
    assert captured["student"].signature is agent.MemoryGate
    assert output_path.is_file()

    reloaded = dspy.Predict(agent.MemoryGate)
    reloaded.load(output_path)
    assert reloaded.fields["worth_saving"]["threshold"] == 0.82

    stdout = capsys.readouterr().out
    assert "FLEET_DECISION_MODEL" in stdout
    assert "typesafe:jev-latest" in stdout

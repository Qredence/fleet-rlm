"""Offline verification for Phase 5 optimization CLI (GEPA, ReAnchor, Neon guard)."""

import asyncio
import json
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import dspy
import pytest
from dspy.clients.base_lm import BaseLM
from dspy.experimental import ReAnchor
from pydantic import SecretStr

from fleet_rlm import agent, db, interpreter, optimize, skills
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
        daytona_image="offline",
        llm_model="openai/gpt-4o-mini",
        sub_llm_model="openai/gpt-4o-mini",
        llm_api_key="offline",
        mlflow_tracking_uri=None,
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
    # 1-skill selector predictions omit primary_skill entirely.
    assert (
        optimize.skill_metric(
            gold_skill,
            dspy.Prediction(needs_skill=SimpleNamespace(value=True)),
        )
        == 1.0
    )
    assert (
        optimize.skill_metric(
            gold_skill,
            dspy.Prediction(needs_skill=SimpleNamespace(value=False)),
        )
        == 0.0
    )
    assert (
        optimize.skill_metric(
            dspy.Example(needs_skill=False, expected_skill=None),
            dspy.Prediction(needs_skill=SimpleNamespace(value=False)),
        )
        == 1.0
    )
    assert (
        optimize.skill_metric(
            dspy.Example(needs_skill=False, expected_skill=None),
            dspy.Prediction(needs_skill=SimpleNamespace(value=True)),
        )
        == 0.0
    )
    assert (
        optimize.skill_metric(
            gold_skill,
            SimpleNamespace(needs_skill=SimpleNamespace(value=True), primary_skill=None),
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


def test_split_train_val_holdout_properties_and_validation() -> None:
    examples = [
        dspy.Example(task=f"Task {index}", answer=f"Ans {index}").with_inputs("task")
        for index in range(10)
    ]

    train_a, val_a, holdout_a = optimize.split_train_val_holdout(examples, seed=42)
    train_b, val_b, holdout_b = optimize.split_train_val_holdout(examples, seed=42)
    assert (len(train_a), len(val_a), len(holdout_a)) == (6, 2, 2)
    assert [ex.task for ex in train_a] == [ex.task for ex in train_b]
    assert [ex.task for ex in val_a] == [ex.task for ex in val_b]
    assert [ex.task for ex in holdout_a] == [ex.task for ex in holdout_b]

    train_tasks = {ex.task for ex in train_a}
    val_tasks = {ex.task for ex in val_a}
    holdout_tasks = {ex.task for ex in holdout_a}
    assert train_tasks.isdisjoint(val_tasks)
    assert train_tasks.isdisjoint(holdout_tasks)
    assert val_tasks.isdisjoint(holdout_tasks)
    assert train_tasks | val_tasks | holdout_tasks == {ex.task for ex in examples}

    # Minimum dataset size of 3 always yields three non-empty disjoint partitions.
    for ratios in ((0.6, 0.2), (0.9, 0.05), (0.05, 0.9)):
        tr, va, ho = optimize.split_train_val_holdout(
            examples[:3], train_ratio=ratios[0], val_ratio=ratios[1], seed=0
        )
        assert (len(tr), len(va), len(ho)) == (1, 1, 1)
        assert len({tr[0].task, va[0].task, ho[0].task}) == 3

    for bad_len in (0, 1, 2):
        with pytest.raises(ValueError, match="At least 3 examples"):
            optimize.split_train_val_holdout(examples[:bad_len])

    for bad_train, bad_val in (
        (0.0, 0.2),
        (-0.1, 0.2),
        (0.6, 0.0),
        (0.6, -0.1),
        (0.6, 0.4),
        (0.7, 0.3),
        (0.8, 0.3),
        (1.0, 0.1),
        (float("nan"), 0.2),
        (0.6, float("inf")),
    ):
        with pytest.raises(ValueError):
            optimize.split_train_val_holdout(examples, train_ratio=bad_train, val_ratio=bad_val)


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
    eval_devsets: list[list[dspy.Example]] = []

    class FakeEvaluate:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            eval_devsets.append(list(kwargs["devset"]))

        def __call__(self, program):
            return SimpleNamespace(score=next(scores))

    def forbid_daytona(*_args, **_kwargs):
        raise AssertionError("Daytona must not be initialized for local_interpreter or flex runs")

    autolog_calls: list[dict] = []
    logged_params: list[dict] = []
    logged_artifacts: list[str] = []
    monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
    monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
    monkeypatch.setattr(optimize.agent, "_model", lambda _s, name: dspy.LM(name, api_key="offline"))
    monkeypatch.setattr(optimize.interpreter, "make_daytona_client", forbid_daytona)
    monkeypatch.setattr(optimize.interpreter, "ephemeral_sandbox", forbid_daytona)
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
    monkeypatch.setattr(
        optimize.mlflow,
        "log_params",
        lambda params: logged_params.append(dict(params)),
    )
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
        local_interpreter=True,
    )
    assert rejected.saved is False
    assert (rejected.train_size, rejected.val_size, rejected.holdout_size) == (6, 2, 2)
    assert (rejected.baseline_score, rejected.optimized_score) == (60.0, 55.0)
    assert not rejected_path.exists()

    first_compile = FakeGEPA.instances[0].compile_calls[0]
    assert {ex.task for ex in first_compile["valset"]}.isdisjoint(
        {ex.task for ex in eval_devsets[0]}
    )

    # 2. Improvement saves artifact and logs to MLflow when configured
    mlflow_settings = settings.model_copy(update={"mlflow_tracking_uri": "http://127.0.0.1:5001"})
    improved_path = tmp_path / "artifacts" / "rlm_program.json"
    improved = optimize.optimize_rlm(
        mlflow_settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=improved_path,
        local_interpreter=True,
    )
    assert improved.saved is True
    assert (improved.train_size, improved.val_size, improved.holdout_size) == (6, 2, 2)
    assert improved_path.exists()
    reloaded = agent.build_program(
        settings.model_copy(update={"optimized_program_path": improved_path}), []
    )
    assert all(predictor.lm is None for predictor in reloaded.predictors())
    assert autolog_calls == [
        {"log_compiles": True, "log_evals": True, "log_traces_from_compile": True},
        {"disable": True},
    ]
    assert logged_params == [
        {
            "target": "rlm",
            "program_kind": "rlm",
            "auto": "light",
            "train_size": 6,
            "val_size": 2,
            "holdout_size": 2,
            "local_interpreter": True,
            "force": False,
        }
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
        local_interpreter=True,
    )
    assert forced.saved is True
    assert forced_path.exists()

    # 4. Flex program compiles dspy.Flex and skips Daytona even without local_interpreter
    flex_path = tmp_path / "flex.json"
    flex_result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=flex_path,
        program_kind="flex",
    )
    assert flex_result.saved is True
    assert (flex_result.train_size, flex_result.val_size, flex_result.holdout_size) == (6, 2, 2)
    assert isinstance(FakeGEPA.instances[-1].compile_calls[0]["student"], dspy.Flex)


def test_optimize_rlm_zero_holdout_leakage_and_ratio_overrides(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _write_jsonl(
        tmp_path / "rlm.jsonl",
        [{"task": f"Task {index}", "answer": f"Ans {index}"} for index in range(10)],
    )
    all_tasks = {f"Task {index}" for index in range(10)}
    recorded: list[dict[str, set[str]]] = []

    class FakeGEPA:
        def __init__(self, **_kwargs):
            pass

        def compile(self, student, *, trainset, valset):
            recorded[-1]["train"] = {ex.task for ex in trainset}
            recorded[-1]["val"] = {ex.task for ex in valset}
            return student.deepcopy()

    class FakeEvaluate:
        def __init__(self, **kwargs):
            recorded.append({"holdout": {ex.task for ex in kwargs["devset"]}})
            self.calls = 0

        def __call__(self, _program):
            self.calls += 1
            return SimpleNamespace(score=50.0 if self.calls == 1 else 70.0)

    monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
    monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
    monkeypatch.setattr(optimize.agent, "_model", lambda _s, name: dspy.LM(name, api_key="offline"))

    # Default 0.6 / 0.2 / 0.2 split
    default_result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=tmp_path / "default.json",
        local_interpreter=True,
    )
    assert (default_result.train_size, default_result.val_size, default_result.holdout_size) == (
        6,
        2,
        2,
    )

    # Caller overriding only train_ratio=0.7 splits remaining evenly into non-empty val & holdout
    override_result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=tmp_path / "override.json",
        train_ratio=0.7,
        local_interpreter=True,
    )
    assert override_result.train_size == 7
    assert override_result.val_size >= 1
    assert override_result.holdout_size >= 1
    assert (
        override_result.train_size + override_result.val_size + override_result.holdout_size == 10
    )

    # Caller overriding both train_ratio and val_ratio
    custom_result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=tmp_path / "custom.json",
        train_ratio=0.5,
        val_ratio=0.3,
        local_interpreter=True,
    )
    assert (custom_result.train_size, custom_result.val_size, custom_result.holdout_size) == (
        5,
        3,
        2,
    )

    assert len(recorded) == 3
    for split in recorded:
        assert split["train"] and split["val"] and split["holdout"]
        assert split["train"].isdisjoint(split["val"])
        assert split["train"].isdisjoint(split["holdout"])
        assert split["val"].isdisjoint(split["holdout"])
        assert split["train"] | split["val"] | split["holdout"] == all_tasks


def test_optimize_rlm_ephemeral_daytona_sandbox_and_cleanup_on_errors(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    dataset = _write_jsonl(
        tmp_path / "rlm.jsonl",
        [{"task": f"Task {index}", "answer": f"Ans {index}"} for index in range(6)],
    )

    events: list[dict] = []

    def make_fake_daytona(_settings: Settings):
        state: dict = {
            "client_entered": False,
            "client_exited": False,
            "sandbox_created": False,
            "sandbox_deleted": False,
            "loop": None,
            "factory": None,
        }
        events.append(state)

        class FakeDaytonaClient:
            async def __aenter__(self):
                state["client_entered"] = True
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                state["client_exited"] = True

        return FakeDaytonaClient()

    @asynccontextmanager
    async def fake_ephemeral_sandbox(_daytona, _settings: Settings):
        state = events[-1]
        state["loop"] = asyncio.get_running_loop()
        state["sandbox_created"] = True
        try:
            yield SimpleNamespace(id="sb-ephemeral")
        finally:
            state["sandbox_deleted"] = True

    def fake_make_interpreter_factory(
        sandbox, _settings: Settings, loop: asyncio.AbstractEventLoop
    ):
        state = events[-1]
        assert sandbox.id == "sb-ephemeral"
        assert loop is state["loop"]

        class FakeFactory:
            execution_instructions = interpreter.DaytonaCodeInterpreter.execution_instructions

            def __call__(self):
                return SimpleNamespace(sandbox=sandbox, loop=loop)

        factory = FakeFactory()
        state["factory"] = factory
        return factory

    observed_factories: list[object] = []
    observed_instructions: list[str] = []
    fail_mode: dict[str, str | None] = {"where": None}

    async def ping_loop() -> str:
        return "pong"

    class FakeEvaluate:
        def __init__(self, **_kwargs):
            self.calls = 0

        def __call__(self, program):
            self.calls += 1
            state = events[-1]
            observed_factories.append(dspy.settings.interpreter_factory)
            assert dspy.settings.interpreter_factory is state["factory"]
            assert state["sandbox_created"] is True
            assert state["sandbox_deleted"] is False
            assert (
                asyncio.run_coroutine_threadsafe(ping_loop(), state["loop"]).result(timeout=5)
                == "pong"
            )
            observed_instructions.append(program.generate_action.signature.instructions)
            if fail_mode["where"] == "evaluate":
                raise RuntimeError("evaluate exploded")
            return SimpleNamespace(score=40.0 if self.calls == 1 else 80.0)

    class FakeGEPA:
        def __init__(self, **_kwargs):
            pass

        def compile(self, student, *, trainset, valset):
            state = events[-1]
            observed_factories.append(dspy.settings.interpreter_factory)
            assert dspy.settings.interpreter_factory is state["factory"]
            assert state["sandbox_created"] is True
            assert state["sandbox_deleted"] is False
            if fail_mode["where"] == "compile":
                raise RuntimeError("compile exploded")
            return student.deepcopy()

    monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
    monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
    monkeypatch.setattr(optimize.agent, "_model", lambda _s, name: dspy.LM(name, api_key="offline"))
    monkeypatch.setattr(optimize.interpreter, "make_daytona_client", make_fake_daytona)
    monkeypatch.setattr(optimize.interpreter, "ephemeral_sandbox", fake_ephemeral_sandbox)
    monkeypatch.setattr(
        optimize.interpreter, "make_interpreter_factory", fake_make_interpreter_factory
    )

    # 1. Success path: sandbox created, factory bound during eval & compile, torn down cleanly
    output_path = tmp_path / "ephemeral_ok.json"
    result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=output_path,
    )
    assert result.saved is True
    assert len(observed_factories) == 3
    assert all(factory is events[0]["factory"] for factory in observed_factories)
    assert any(
        interpreter.DaytonaCodeInterpreter.execution_instructions in instr
        for instr in observed_instructions
    )
    assert events[0]["client_entered"] is True
    assert events[0]["client_exited"] is True
    assert events[0]["sandbox_created"] is True
    assert events[0]["sandbox_deleted"] is True
    assert events[0]["loop"].is_closed() is True
    assert dspy.settings.interpreter_factory is None

    # 2. Exception during Evaluate still deletes ephemeral sandbox and closes background loop
    fail_mode["where"] = "evaluate"
    with pytest.raises(RuntimeError, match="evaluate exploded"):
        optimize.optimize_rlm(
            settings,
            database_url=_BRANCH_URL,
            dataset=dataset,
            output_path=tmp_path / "ephemeral_eval_fail.json",
        )
    assert events[1]["client_entered"] is True
    assert events[1]["client_exited"] is True
    assert events[1]["sandbox_created"] is True
    assert events[1]["sandbox_deleted"] is True
    assert events[1]["loop"].is_closed() is True
    assert dspy.settings.interpreter_factory is None

    # 3. Exception during GEPA.compile still deletes ephemeral sandbox and closes background loop
    fail_mode["where"] = "compile"
    with pytest.raises(RuntimeError, match="compile exploded"):
        optimize.optimize_rlm(
            settings,
            database_url=_BRANCH_URL,
            dataset=dataset,
            output_path=tmp_path / "ephemeral_compile_fail.json",
        )
    assert events[2]["client_entered"] is True
    assert events[2]["client_exited"] is True
    assert events[2]["sandbox_created"] is True
    assert events[2]["sandbox_deleted"] is True
    assert events[2]["loop"].is_closed() is True
    assert dspy.settings.interpreter_factory is None

    # 4. CLI default (--target rlm --program rlm without --local-interpreter) uses ephemeral Daytona
    fail_mode["where"] = None
    cli_output = tmp_path / "ephemeral_cli.json"
    exit_code = optimize.main(
        [
            "--target",
            "rlm",
            "--program",
            "rlm",
            "--database-url",
            _BRANCH_URL,
            "--dataset",
            str(dataset),
            "--output",
            str(cli_output),
        ],
        settings=settings,
    )
    assert exit_code == 0
    assert cli_output.is_file()
    assert events[3]["sandbox_created"] is True
    assert events[3]["sandbox_deleted"] is True
    assert events[3]["client_exited"] is True
    assert events[3]["loop"].is_closed() is True
    assert "[rlm] train=4 val=1 holdout=1" in capsys.readouterr().out


def test_cli_local_interpreter_skips_daytona_and_reports_val_size(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    dataset = _write_jsonl(
        tmp_path / "rlm.jsonl",
        [{"task": f"Task {index}", "answer": f"Ans {index}"} for index in range(10)],
    )
    seen_factories: list[object] = []

    class FakeEvaluate:
        def __init__(self, **_kwargs):
            self.calls = 0

        def __call__(self, _program):
            self.calls += 1
            seen_factories.append(dspy.settings.interpreter_factory)
            return SimpleNamespace(score=45.0 if self.calls == 1 else 75.0)

    class FakeGEPA:
        def __init__(self, **_kwargs):
            pass

        def compile(self, student, *, trainset, valset):
            seen_factories.append(dspy.settings.interpreter_factory)
            return student.deepcopy()

    def forbid_daytona(*_args, **_kwargs):
        raise AssertionError("--local-interpreter must skip Daytona client and sandbox creation")

    monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
    monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
    monkeypatch.setattr(optimize.agent, "_model", lambda _s, name: dspy.LM(name, api_key="offline"))
    monkeypatch.setattr(optimize.interpreter, "make_daytona_client", forbid_daytona)
    monkeypatch.setattr(optimize.interpreter, "ephemeral_sandbox", forbid_daytona)

    output_path = tmp_path / "cli_local.json"
    exit_code = optimize.main(
        [
            "--target",
            "rlm",
            "--database-url",
            _BRANCH_URL,
            "--dataset",
            str(dataset),
            "--output",
            str(output_path),
            "--local-interpreter",
        ],
        settings=settings,
    )
    assert exit_code == 0
    assert output_path.is_file()
    assert seen_factories == [None, None, None]
    stdout = capsys.readouterr().out
    assert "[rlm] train=6 val=2 holdout=2" in stdout


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


def test_optimize_single_skill_real_reanchor(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    single_skills_dir = tmp_path / "single-skills"
    skill_dir = single_skills_dir / "data-analysis"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: data-analysis\ndescription: Analyze tabular CSV data\n---\nInspect CSV.\n",
        encoding="utf-8",
    )
    single_settings = settings.model_copy(update={"skills_dir": single_skills_dir})
    registry = skills.SkillRegistry.load(single_skills_dir)
    assert len(registry.skills) == 1

    probabilities = {
        "csv-1": 0.92,
        "csv-2": 0.88,
        "csv-3": 0.84,
        "csv-4": 0.80,
        "csv-5": 0.78,
        "csv-6": 0.86,
        "chat-1": 0.64,
        "chat-2": 0.62,
        "chat-3": 0.60,
        "chat-4": 0.58,
        "chat-5": 0.56,
        "chat-6": 0.61,
    }

    class FakeDecisionLM(BaseLM):
        supports_decision_requests = True

        def __init__(self) -> None:
            super().__init__(model="typesafe:jev-fake", cache=True)

        def __call__(self, request, **kwargs):
            task = request.messages[0].parts[0].value["inputs"]["task"]
            assert set(request.config.extensions["questions"]) == {"needs_skill"}
            return SimpleNamespace(
                provider_data={
                    "typesafe": {"answers": {"needs_skill": {"noul": probabilities[task]}}}
                }
            )

    fake_lm = FakeDecisionLM()

    # 1. Direct real ReAnchor.compile on a 1-skill selector must not crash in _fit_weights.
    selector = skills.build_skill_selector(registry.skills)
    assert set(selector.signature.output_fields) == {"needs_skill"}
    trainset = [
        dspy.Example(
            task=f"csv-{index}", needs_skill=True, expected_skill="data-analysis"
        ).with_inputs("task")
        for index in range(1, 6)
    ] + [
        dspy.Example(task=f"chat-{index}", needs_skill=False, expected_skill=None).with_inputs(
            "task"
        )
        for index in range(1, 6)
    ]
    valset = [
        dspy.Example(task="csv-6", needs_skill=True, expected_skill="data-analysis").with_inputs(
            "task"
        ),
        dspy.Example(task="chat-6", needs_skill=False, expected_skill=None).with_inputs("task"),
    ]
    with dspy.context(lm=fake_lm):
        reanchor = ReAnchor(optimize.skill_metric, require_cache=False)
        compiled = reanchor.compile(selector, trainset=trainset, valset=valset)
    threshold = compiled.fields["needs_skill"]["threshold"]
    assert 0.64 < threshold < 0.78
    assert reanchor.report["train_score_before"] == 0.5
    assert reanchor.report["train_score"] == 1.0
    assert reanchor.report["val_score_before"] == 0.5
    assert reanchor.report["val_score"] == 1.0

    # 2. Full optimize_skills -> save -> reload -> select_skills round-trip with real ReAnchor.
    dataset = _write_jsonl(
        tmp_path / "single-skills.jsonl",
        [
            {"task": task, "expected_skill": "data-analysis" if task.startswith("csv") else None}
            for task in probabilities
        ],
    )
    output_path = tmp_path / "single_skill_selector.json"
    monkeypatch.setattr(optimize.dspy, "LM", lambda _model: fake_lm)
    result = optimize.optimize_skills(
        single_settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=output_path,
        seed=0,
        require_cache=False,
    )
    assert result.saved is True
    assert result.optimized_score == 1.0
    assert output_path.is_file()
    saved_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved_payload["lm"] is None
    assert "primary_skill" not in saved_payload.get("fields", {})

    reloaded = skills.build_skill_selector(registry.skills, output_path)
    calibrated_threshold = reloaded.fields["needs_skill"]["threshold"]
    assert 0.64 < calibrated_threshold < 0.78

    monkeypatch.setattr(skills.dspy, "LM", lambda _model: fake_lm)
    runtime_settings = single_settings.model_copy(update={"skill_selector_path": output_path})
    positive = skills.select_skills(runtime_settings, registry, "csv-6")
    assert [skill.name for skill in positive.active_skills] == ["data-analysis"]
    assert positive.scores == {"data-analysis": 0.86}

    negative = skills.select_skills(runtime_settings, registry, "chat-6")
    assert negative.active_skills == ()
    assert negative.scores == {"data-analysis": 0.61}


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


def test_optimize_production_skill_catalog_with_reanchor(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prod_skills_dir = Path(__file__).resolve().parents[1] / "skills"
    registry = skills.SkillRegistry.load(prod_skills_dir)
    skill_names = tuple(skill.name for skill in registry.skills)
    assert skill_names == (
        "chart-visualization",
        "data-analysis",
        "document-extraction",
        "recursive-synthesis",
        "scientific-computing",
        "skill-creator",
        "spreadsheet-audit",
        "web-research",
    )

    rows = []
    choice_table: dict[str, tuple[float, dict[str, float]]] = {}
    for index, skill_name in enumerate(skill_names):
        for variant in (1, 2):
            task = f"task-{skill_name}-{variant}"
            rows.append({"task": task, "expected_skill": skill_name})
            probs = {
                candidate: (0.72 if candidate == skill_name else 0.28 / (len(skill_names) - 1))
                for candidate in skill_names
            }
            choice_table[task] = (0.88 - 0.01 * index, probs)
    for variant in range(1, 7):
        task = f"smalltalk-{variant}"
        rows.append({"task": task, "expected_skill": None})
        uniform = {candidate: 1.0 / len(skill_names) for candidate in skill_names}
        choice_table[task] = (0.56 + 0.01 * variant, uniform)

    class FakeSixSkillDecisionLM(BaseLM):
        supports_decision_requests = True

        def __init__(self) -> None:
            super().__init__(model="typesafe:jev-six-skill", cache=True)

        def __call__(self, request, **kwargs):
            task = request.messages[0].parts[0].value["inputs"]["task"]
            assert set(request.config.extensions["questions"]) == {"needs_skill", "primary_skill"}
            noul_prob, choice_probs = choice_table[task]
            return SimpleNamespace(
                provider_data={
                    "typesafe": {
                        "answers": {
                            "needs_skill": {"noul": noul_prob},
                            "primary_skill": {"probabilities": choice_probs, "confidence": 0.8},
                        }
                    }
                }
            )

    fake_lm = FakeSixSkillDecisionLM()
    dataset = _write_jsonl(tmp_path / "six-skills.jsonl", rows)
    output_path = tmp_path / "six_skill_selector.json"
    prod_settings = settings.model_copy(
        update={"skills_dir": prod_skills_dir, "tavily_api_key": SecretStr("offline")}
    )

    monkeypatch.setattr(optimize.dspy, "LM", lambda _model: fake_lm)
    result = optimize.optimize_skills(
        prod_settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=output_path,
        seed=0,
        require_cache=False,
    )
    assert result.saved is True
    assert result.optimized_score == 1.0
    assert output_path.is_file()

    reloaded = skills.build_skill_selector(registry.skills, output_path)
    assert set(reloaded.signature.output_fields) == {"needs_skill", "primary_skill"}
    assert 0.62 < reloaded.fields["needs_skill"]["threshold"] < 0.83

    monkeypatch.setattr(skills.dspy, "LM", lambda _model: fake_lm)
    runtime_settings = prod_settings.model_copy(update={"skill_selector_path": output_path})
    for skill_name in skill_names:
        selected = skills.select_skills(runtime_settings, registry, f"task-{skill_name}-1")
        assert [s.name for s in selected.active_skills] == [skill_name]
    skipped = skills.select_skills(runtime_settings, registry, "smalltalk-1")
    assert skipped.active_skills == ()

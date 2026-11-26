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
        == 0.25
    )
    assert (
        optimize.skill_metric(
            gold_skill,
            SimpleNamespace(
                needs_skill=SimpleNamespace(value=False),
                primary_skill=SimpleNamespace(value="data-analysis"),
            ),
        )
        == 0.25
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
        == 0.4
    )
    assert (
        optimize.memory_metric(
            gold_mem,
            SimpleNamespace(
                worth_saving=SimpleNamespace(value=True),
                action=SimpleNamespace(value="insert"),
                scope=SimpleNamespace(value="session"),
            ),
        )
        == 0.7
    )
    assert (
        optimize.memory_metric(
            gold_mem,
            SimpleNamespace(
                worth_saving=SimpleNamespace(value=True),
                action=SimpleNamespace(value="skip"),
                scope=SimpleNamespace(value="global"),
            ),
        )
        == 0.0
    )
    assert (
        optimize.memory_metric(
            dspy.Example(worth_saving=False, action="skip", scope="session"),
            SimpleNamespace(
                worth_saving=SimpleNamespace(value=True),
                action=SimpleNamespace(value="insert"),
                scope=SimpleNamespace(value="session"),
            ),
        )
        == 0.0
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
            if isinstance(compiled, dspy.Flex):
                compiled._module_src = f"{compiled.module_src}\n# optimized"
            for predictor in compiled.predictors():
                predictor.lm = dspy.LM("openai/gpt-4o-mini", api_key="secret")
                sig = getattr(predictor, "signature", None)
                if sig is not None:
                    predictor.signature = sig.with_instructions(
                        f"{sig.instructions or ''}\nOptimized instruction variant."
                    )
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
    assert len(logged_params) == 1
    assert logged_params[0]["target"] == "rlm"
    assert logged_params[0]["program_kind"] == "rlm"
    assert logged_params[0]["auto"] == "budget"
    assert logged_params[0]["max_metric_calls"] == optimize.DEFAULT_MAX_METRIC_CALLS
    assert logged_params[0]["holdout_repeats"] == optimize.DEFAULT_HOLDOUT_REPEATS
    assert logged_params[0]["disable_cache"] is True
    assert logged_params[0]["train_size"] == 6
    assert logged_params[0]["val_size"] == 2
    assert logged_params[0]["holdout_size"] == 2
    assert logged_params[0]["local_interpreter"] is True
    assert logged_params[0]["force"] is False
    assert str(improved_path) in logged_artifacts
    assert any(Path(path).name == "report.json" for path in logged_artifacts)

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
            compiled = student.deepcopy()
            sig = compiled.generate_action.signature
            assert sig is not None
            compiled.generate_action.signature = sig.with_instructions(
                f"{sig.instructions or ''}\nRefined."
            )
            return compiled

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
            "sandboxes_created": 0,
            "sandboxes_deleted": 0,
            "loop": None,
            "factories": [],
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
        state["sandboxes_created"] += 1
        sandbox_id = f"sb-ephemeral-{state['sandboxes_created']}"
        try:
            yield SimpleNamespace(id=sandbox_id)
        finally:
            state["sandboxes_deleted"] += 1

    def fake_make_interpreter_factory(
        sandbox, _settings: Settings, loop: asyncio.AbstractEventLoop
    ):
        state = events[-1]
        assert sandbox.id.startswith("sb-ephemeral-")
        assert loop is state["loop"]

        class FakeFactory:
            execution_instructions = interpreter.DaytonaCodeInterpreter.execution_instructions

            def __call__(self, **_kwargs):
                return SimpleNamespace(sandbox=sandbox, loop=loop, shutdown=lambda: None)

        factory = FakeFactory()
        state["factories"].append(factory)
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
            assert dspy.settings.interpreter_factory is not None
            assert (
                getattr(dspy.settings.interpreter_factory, "execution_instructions", None)
                == interpreter.DaytonaCodeInterpreter.execution_instructions
            )
            assert state["sandboxes_created"] >= 1
            assert state["sandboxes_deleted"] == 0
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
            assert dspy.settings.interpreter_factory is not None
            assert state["sandboxes_created"] >= 1
            assert state["sandboxes_deleted"] == 0
            if fail_mode["where"] == "compile":
                raise RuntimeError("compile exploded")
            compiled = student.deepcopy()
            sig = compiled.generate_action.signature
            assert sig is not None
            compiled.generate_action.signature = sig.with_instructions(
                f"{sig.instructions or ''}\nOptimized for Daytona."
            )
            return compiled

    monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
    monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
    monkeypatch.setattr(optimize.agent, "_model", lambda _s, name: dspy.LM(name, api_key="offline"))
    monkeypatch.setattr(optimize.interpreter, "make_daytona_client", make_fake_daytona)
    monkeypatch.setattr(optimize.interpreter, "ephemeral_sandbox", fake_ephemeral_sandbox)
    monkeypatch.setattr(
        optimize.interpreter, "make_interpreter_factory", fake_make_interpreter_factory
    )

    # 1. Success path: default pool creates 2 sandboxes, binds factory, and tears all down
    output_path = tmp_path / "ephemeral_ok.json"
    result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=output_path,
        holdout_repeats=1,
    )
    assert result.saved is True
    assert len(observed_factories) == 3
    assert any(
        interpreter.DaytonaCodeInterpreter.execution_instructions in instr
        for instr in observed_instructions
    )
    assert events[0]["client_entered"] is True
    assert events[0]["client_exited"] is True
    assert events[0]["sandboxes_created"] == 2
    assert events[0]["sandboxes_deleted"] == 2
    assert events[0]["loop"].is_closed() is True
    assert dspy.settings.interpreter_factory is None

    # 2. Exception during Evaluate still deletes all ephemeral sandboxes and closes background loop
    fail_mode["where"] = "evaluate"
    with pytest.raises(RuntimeError, match="evaluate exploded"):
        optimize.optimize_rlm(
            settings,
            database_url=_BRANCH_URL,
            dataset=dataset,
            output_path=tmp_path / "ephemeral_eval_fail.json",
            holdout_repeats=1,
        )
    assert events[1]["client_entered"] is True
    assert events[1]["client_exited"] is True
    assert events[1]["sandboxes_created"] == 2
    assert events[1]["sandboxes_deleted"] == 2
    assert events[1]["loop"].is_closed() is True
    assert dspy.settings.interpreter_factory is None

    # 3. Exception during GEPA.compile still deletes all sandboxes and closes the loop
    fail_mode["where"] = "compile"
    with pytest.raises(RuntimeError, match="compile exploded"):
        optimize.optimize_rlm(
            settings,
            database_url=_BRANCH_URL,
            dataset=dataset,
            output_path=tmp_path / "ephemeral_compile_fail.json",
            holdout_repeats=1,
        )
    assert events[2]["client_entered"] is True
    assert events[2]["client_exited"] is True
    assert events[2]["sandboxes_created"] == 2
    assert events[2]["sandboxes_deleted"] == 2
    assert events[2]["loop"].is_closed() is True
    assert dspy.settings.interpreter_factory is None

    # 4. CLI default (--target rlm --program rlm) uses ephemeral Daytona pool
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
            "--num-threads",
            "1",
            "--holdout-repeats",
            "1",
        ],
        settings=settings,
    )
    assert exit_code == 0
    assert cli_output.is_file()
    assert events[3]["sandboxes_created"] == 1
    assert events[3]["sandboxes_deleted"] == 1
    assert observed_factories[-1] is events[3]["factories"][0]
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
            compiled = student.deepcopy()
            sig = compiled.generate_action.signature
            assert sig is not None
            compiled.generate_action.signature = sig.with_instructions(
                f"{sig.instructions or ''}\nLocal CLI variant."
            )
            return compiled

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


def test_lineage_disjoint_and_explicit_splits(tmp_path: Path, settings: Settings) -> None:
    # 1. Explicit splits preserve frozen disjoint partitions.
    explicit_2way = [
        dspy.Example(task=f"t{i}", lineage=f"lin-{i // 2}", split="train" if i < 4 else "holdout")
        for i in range(6)
    ]
    train2, holdout2 = optimize.split_examples(explicit_2way, seed=99)
    assert [ex.task for ex in train2] == ["t0", "t1", "t2", "t3"]
    assert [ex.task for ex in holdout2] == ["t4", "t5"]

    explicit_3way = [
        dspy.Example(
            task=f"r{i}",
            answer=f"a{i}",
            lineage=f"fam-{i}",
            split=("train" if i < 3 else "val" if i < 5 else "holdout"),
        )
        for i in range(7)
    ]
    tr3, va3, ho3 = optimize.split_train_val_holdout(explicit_3way, seed=123)
    assert [ex.task for ex in tr3] == ["r0", "r1", "r2"]
    assert [ex.task for ex in va3] == ["r3", "r4"]
    assert [ex.task for ex in ho3] == ["r5", "r6"]

    # Partial explicit splits are rejected.
    with pytest.raises(ValueError, match="Either all examples or no examples"):
        optimize.split_examples(
            [dspy.Example(task="a", split="train"), dspy.Example(task="b", split=None)]
        )

    # Lineage leaking across explicit splits is rejected.
    with pytest.raises(ValueError, match="leaks across multiple dataset splits"):
        optimize.split_examples(
            [
                dspy.Example(task="a", lineage="shared", split="train"),
                dspy.Example(task="b", lineage="shared", split="holdout"),
            ]
        )

    # 2. Auto lineage-grouped 2-way and 3-way splits guarantee zero lineage overlap.
    lineage_examples = [
        dspy.Example(task=f"ex-{fam}-{idx}", answer="ok", lineage=f"fam-{fam}")
        for fam in range(6)
        for idx in range(3)
    ]
    tr_lin, ho_lin = optimize.split_examples(lineage_examples, train_ratio=0.7, seed=7)
    assert {ex.lineage for ex in tr_lin}.isdisjoint({ex.lineage for ex in ho_lin})
    assert len(tr_lin) + len(ho_lin) == len(lineage_examples)

    tr_l3, va_l3, ho_l3 = optimize.split_train_val_holdout(
        lineage_examples, train_ratio=0.5, val_ratio=0.25, seed=7
    )
    sets3 = [{ex.lineage for ex in part} for part in (tr_l3, va_l3, ho_l3)]
    assert sets3[0].isdisjoint(sets3[1])
    assert sets3[0].isdisjoint(sets3[2])
    assert sets3[1].isdisjoint(sets3[2])
    assert len(tr_l3) + len(va_l3) + len(ho_l3) == len(lineage_examples)

    # Fewer distinct lineages than required splits raises ValueError.
    with pytest.raises(ValueError, match="At least 3 distinct lineages"):
        optimize.split_train_val_holdout(
            [
                dspy.Example(task="a", answer="1", lineage="only-a"),
                dspy.Example(task="b", answer="2", lineage="only-a"),
                dspy.Example(task="c", answer="3", lineage="only-b"),
            ]
        )


def test_skills_dataset_multiturn_secondary_and_unavailable_guard(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prod_skills_dir = Path(__file__).resolve().parents[1] / "skills"
    no_tavily_settings = settings.model_copy(
        update={"skills_dir": prod_skills_dir, "tavily_api_key": None}
    )
    with_tavily_settings = settings.model_copy(
        update={"skills_dir": prod_skills_dir, "tavily_api_key": SecretStr("offline-tavily")}
    )

    dataset = _write_jsonl(
        tmp_path / "skills_web.jsonl",
        [
            {
                "task": "who won in Georgia?",
                "prior_turns": [
                    {
                        "user": "Look up the 2024 presidential election results.",
                        "assistant": "Found national totals.",
                    }
                ],
                "expected_skill": "web-research",
                "needs_skill": True,
                "secondary_skills": ["data-analysis"],
                "lineage": "followup-election",
                "split": "train",
            },
            {
                "task": "hello there",
                "expected_skill": None,
                "needs_skill": False,
                "lineage": "greeting",
                "split": "holdout",
            },
        ],
    )

    # Missing TAVILY_API_KEY rejects dataset requiring web-research with actionable message.
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        optimize.optimize_skills(
            no_tavily_settings,
            database_url=_BRANCH_URL,
            dataset=dataset,
            output_path=tmp_path / "unused.json",
            require_cache=False,
        )

    registry = skills.SkillRegistry.load(prod_skills_dir, fallback_to_bundled=True).with_installed(
        prod_skills_dir
    )
    loaded = optimize.load_skills_dataset(dataset, registry, settings=with_tavily_settings)
    expected_wire, _ = skills.format_selector_task(
        "who won in Georgia?",
        [
            {
                "user": "Look up the 2024 presidential election results.",
                "assistant": "Found national totals.",
            }
        ],
    )
    assert loaded[0].task == expected_wire
    assert loaded[0].is_multi_turn is True
    assert loaded[0].secondary_skills == ["data-analysis"]
    assert loaded[0].lineage == "followup-election"
    assert loaded[0].split == "train"
    assert loaded[1].task == "hello there"
    assert loaded[1].is_multi_turn is False

    # Installed skills under .installed/ are included by optimize_skills.
    installed_dir = settings.skills_dir / ".installed"
    installed_dir.mkdir(parents=True, exist_ok=True)
    (installed_dir / "custom-audit.md").write_text(
        "---\nname: custom-audit\ndescription: Custom installed audit rules\n---\nAudit cleanly.\n",
        encoding="utf-8",
    )
    installed_dataset = _write_jsonl(
        tmp_path / "installed_skills.jsonl",
        [
            {"task": "Run custom audit", "expected_skill": "custom-audit", "split": "train"},
            {"task": "Say hi", "expected_skill": None, "split": "holdout"},
        ],
    )

    seen_candidates: list[set[str]] = []

    class FakeReAnchor:
        def __init__(self, _metric, **_kwargs):
            self.report = {
                "train_score_before": 0.5,
                "train_score": 1.0,
                "val_score_before": 0.5,
                "val_score": 1.0,
                "fitted": [{"field": "needs_skill", "parameter": "threshold", "value": 0.7}],
            }

        def compile(self, student, *, trainset, valset=None):
            seen_candidates.append(set(student.signature.output_fields))
            return student.deepcopy()

    monkeypatch.setattr(optimize, "ReAnchor", FakeReAnchor)
    monkeypatch.setattr(optimize.dspy, "LM", MagicMock(return_value="dummy-jev"))
    result = optimize.optimize_skills(
        settings,
        database_url=_BRANCH_URL,
        dataset=installed_dataset,
        output_path=tmp_path / "installed_out.json",
    )
    assert result.saved is True
    assert seen_candidates == [{"needs_skill", "primary_skill"}]


def test_memory_dataset_normalizes_existing_memories_matching_runtime(tmp_path: Path) -> None:
    dataset = _write_jsonl(
        tmp_path / "memory_norm.jsonl",
        [
            {
                "task": "Switch default currency to EUR.",
                "answer": "Updated default currency to EUR.",
                "existing_memories": [
                    {
                        "id": "m1",
                        "content": "Approved global memory: Default currency is USD.",
                        "scope": "global",
                        "approved": True,
                    },
                    {
                        "id": "m2",
                        "content": "Pending session memory: Draft batch size 32.",
                        "scope": "session",
                        "approved": False,
                    },
                    {
                        "id": "m3",
                        "content": "Explicit status memory.",
                        "scope": "session",
                        "status": "pending",
                    },
                    {
                        "id": "m4",
                        "content": "Fourth entry should be truncated like runtime [:3].",
                        "scope": "global",
                        "approved": True,
                    },
                ],
                "worth_saving": True,
                "action": "update",
                "scope": "global",
                "lineage": "currency-pref",
                "split": "train",
            }
        ],
    )
    rows = optimize.load_memory_dataset(dataset)
    assert len(rows) == 1
    assert rows[0].lineage == "currency-pref"
    assert rows[0].split == "train"
    assert rows[0].existing_memories == [
        {
            "id": "m1",
            "content": "Default currency is USD.",
            "scope": "global",
            "status": "approved",
        },
        {"id": "m2", "content": "Draft batch size 32.", "scope": "session", "status": "pending"},
        {"id": "m3", "content": "Explicit status memory.", "scope": "session", "status": "pending"},
    ]


def test_reanchor_strict_save_gate_diagnostics_and_jev_metadata(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _write_jsonl(
        tmp_path / "memory_gate.jsonl",
        [
            {
                "task": "Remember timezone UTC",
                "answer": "Noted.",
                "worth_saving": True,
                "action": "insert",
                "scope": "global",
                "lineage": "tz",
                "split": "train",
            },
            {
                "task": "Remember batch 64",
                "answer": "Noted.",
                "worth_saving": True,
                "action": "insert",
                "scope": "session",
                "lineage": "batch",
                "split": "train",
            },
            {
                "task": "What is 1+1?",
                "answer": "2",
                "worth_saving": False,
                "action": "skip",
                "scope": "session",
                "lineage": "math",
                "split": "train",
            },
            {
                "task": "Remember ISO dates",
                "answer": "Noted.",
                "worth_saving": True,
                "action": "insert",
                "scope": "global",
                "lineage": "iso",
                "split": "holdout",
            },
            {
                "task": "Hello there!",
                "answer": "Hi!",
                "worth_saving": False,
                "action": "skip",
                "scope": "session",
                "lineage": "greet",
                "split": "holdout",
            },
        ],
    )

    def run_case(
        *,
        report_payload: dict,
        out_name: str,
        force: bool = False,
        lm_obj: object = "dummy-jev",
        log_dir: Path | None = None,
    ) -> optimize.OptimizationResult:
        class FakeReAnchor:
            def __init__(self, _metric, **_kwargs):
                self.report = dict(report_payload)

            def compile(self, student, *, trainset, valset=None):
                compiled = student.deepcopy()
                compiled.fields = {"worth_saving": {"threshold": 0.75}}
                return compiled

        monkeypatch.setattr(optimize, "ReAnchor", FakeReAnchor)
        monkeypatch.setattr(optimize.dspy, "LM", lambda _model: lm_obj)
        return optimize.optimize_memory(
            settings,
            database_url=_BRANCH_URL,
            dataset=dataset,
            output_path=tmp_path / out_name,
            force=force,
            log_dir=log_dir,
        )

    # 1. All fitted rows skipped (ReAnchor list format) -> discarded even if score rose.
    skipped_res = run_case(
        report_payload={
            "train_score_before": 0.6,
            "train_score": 0.8,
            "val_score_before": 0.5,
            "val_score": 0.9,
            "fitted": [
                {"field": "worth_saving", "parameter": "threshold", "skipped": "no improvement"}
            ],
        },
        out_name="skipped.json",
    )
    assert skipped_res.saved is False
    assert skipped_res.report["fitted_changed"] is False
    assert not (tmp_path / "skipped.json").exists()

    # 2. Holdout score tie or regression -> discarded.
    tie_res = run_case(
        report_payload={
            "val_score_before": 0.8,
            "val_score": 0.8,
            "fitted": [{"field": "worth_saving", "parameter": "threshold", "value": 0.75}],
        },
        out_name="tie.json",
    )
    assert tie_res.saved is False
    assert not (tmp_path / "tie.json").exists()

    # 3. Holdout FP rate regression -> discarded unless force=True.
    fp_reg_res = run_case(
        report_payload={
            "val_score_before": 0.6,
            "val_score": 0.85,
            "fp_rate_before": 0.0,
            "fp_rate_after": 0.2,
            "fitted": [{"field": "worth_saving", "parameter": "threshold", "value": 0.75}],
        },
        out_name="fp_reg.json",
    )
    assert fp_reg_res.saved is False
    assert not (tmp_path / "fp_reg.json").exists()

    forced_res = run_case(
        report_payload={
            "val_score_before": 0.6,
            "val_score": 0.85,
            "fp_rate_before": 0.0,
            "fp_rate_after": 0.2,
            "fitted": [{"field": "worth_saving", "parameter": "threshold", "value": 0.75}],
        },
        out_name="fp_forced.json",
        force=True,
    )
    assert forced_res.saved is True
    assert (tmp_path / "fp_forced.json").is_file()

    # 4. BaseLM diagnostics capture auto_approve_fp_count (>=0.90) & Jev metadata safely.
    class FakeMemoryDecisionLM(BaseLM):
        supports_decision_requests = True

        def __init__(self) -> None:
            super().__init__(model="typesafe:jev-latest", cache=True)
            self.history = [{"response": {"model": "jev-2026-10-22"}}]

        def __call__(self, request, **kwargs):
            task = request.messages[0].parts[0].value["inputs"]["task"]
            is_greet = "Hello" in task
            return SimpleNamespace(
                provider_data={
                    "typesafe": {
                        "answers": {
                            "worth_saving": {"noul": 0.94 if is_greet else 0.96},
                            "action": {
                                "probabilities": {"insert": 0.85, "update": 0.10, "skip": 0.05},
                                "confidence": 0.85,
                            },
                            "scope": {
                                "probabilities": {"global": 0.80, "session": 0.20},
                                "confidence": 0.80,
                            },
                        }
                    }
                }
            )

    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key")
    monkeypatch.setattr(
        optimize.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": "jev-latest", "release_date": "2026-10-22"}]},
        ),
    )
    fake_mem_lm = FakeMemoryDecisionLM()
    log_dir = tmp_path / "mem-logs"

    class FakeCompiledReAnchor:
        def __init__(self, _metric, **_kwargs):
            self.report = {
                "val_score_before": 0.5,
                "val_score": 1.0,
                "fitted": [{"field": "worth_saving", "parameter": "threshold", "value": 0.95}],
            }

        def compile(self, student, *, trainset, valset=None):
            compiled = student.deepcopy()
            compiled.fields = {"worth_saving": {"threshold": 0.95}}
            return compiled

    monkeypatch.setattr(optimize, "ReAnchor", FakeCompiledReAnchor)
    monkeypatch.setattr(optimize.dspy, "LM", lambda _model: fake_mem_lm)
    diag_res = optimize.optimize_memory(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=tmp_path / "mem_ok.json",
        log_dir=log_dir,
    )
    assert diag_res.saved is True
    assert diag_res.report["fp_rate_before"] == 1.0
    assert diag_res.report["fp_rate_after"] == 0.0
    assert diag_res.report["auto_approve_fp_count_before"] == 1.0
    assert diag_res.report["auto_approve_fp_count_after"] == 0.0
    assert diag_res.report["decision_model_metadata"]["resolved_model"] == "jev-2026-10-22"
    assert diag_res.report["decision_model_metadata"]["release_date"] == "2026-10-22"
    assert (log_dir / "report.json").is_file()

    # Metadata network failure is non-fatal.
    def fail_http(*_args, **_kwargs):
        raise RuntimeError("network offline")

    monkeypatch.setattr(optimize.httpx, "get", fail_http)
    meta = optimize._fetch_jev_metadata("typesafe:jev-latest", fake_mem_lm)
    assert meta["configured_model"] == "typesafe:jev-latest"
    assert meta["resolved_model"] == "jev-2026-10-22"


def test_rlm_metric_all_verifiers_and_execution_discipline() -> None:
    import io

    from PIL import Image

    # 1. Exact verifier
    ex_exact = dspy.Example(
        answer="MISSING: report.csv",
        verifier={"type": "exact", "expected": "MISSING: report.csv"},
        blocking=True,
    )
    assert (
        optimize.rlm_metric(ex_exact, dspy.Prediction(answer="  MISSING: report.csv  ")).score
        == 1.0
    )
    assert optimize.rlm_metric(ex_exact, dspy.Prediction(answer="missing: report.csv")).score == 0.0

    # 2. JSON verifier (abs_tol, strict_json, bool vs int safety)
    ex_json = dspy.Example(
        answer='{"t": 16.4, "significant": true}',
        verifier={
            "type": "json",
            "expected": {"t": 16.4, "significant": True},
            "abs_tol": 0.05,
            "strict_json": False,
        },
    )
    assert (
        optimize.rlm_metric(
            ex_json, dspy.Prediction(answer='```json\n{"t": 16.42, "significant": true}\n```')
        ).score
        == 1.0
    )
    # bool vs int must NOT match
    assert (
        optimize.rlm_metric(ex_json, dspy.Prediction(answer='{"t": 16.4, "significant": 1}')).score
        == 0.0
    )
    ex_strict = dspy.Example(
        answer='{"ok": true}',
        verifier={"type": "json", "expected": {"ok": True}, "strict_json": True},
    )
    assert (
        optimize.rlm_metric(ex_strict, dspy.Prediction(answer='```json\n{"ok": true}\n```')).score
        == 0.0
    )

    # 3. Fact checklist verifier
    ex_facts = dspy.Example(
        answer="",
        verifier={
            "type": "fact_checklist",
            "required_patterns": [
                r"search (?:is|remains) slow",
                [r"next update at 14:00 UTC", r"update will follow at 14:00 UTC"],
            ],
            "forbidden_patterns": [r"database outage"],
            "max_sentences": 2,
            "max_words": 20,
        },
    )
    assert (
        optimize.rlm_metric(
            ex_facts, dspy.Prediction(answer="Search is slow. Next update at 14:00 UTC.")
        ).score
        == 1.0
    )
    assert (
        optimize.rlm_metric(
            ex_facts,
            dspy.Prediction(
                answer="Search is slow due to database outage. Next update at 14:00 UTC."
            ),
        ).score
        == 0.0
    )
    assert (
        optimize.rlm_metric(
            ex_facts, dspy.Prediction(answer="Search is slow. One. Two. Next update at 14:00 UTC.")
        ).score
        == 0.0
    )

    # 4. Artifact verifier (SVG labels, PNG nonblank, JSON, text, SKILL.md)
    valid_svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>EU 120</text><text>Region</text></svg>'
    )
    blank_png_buf = io.BytesIO()
    Image.new("RGB", (120, 120), color=(255, 255, 255)).save(blank_png_buf, format="PNG")
    good_png_buf = io.BytesIO()
    img = Image.new("RGB", (120, 120), color=(255, 255, 255))
    img.putpixel((10, 10), (0, 0, 0))
    img.save(good_png_buf, format="PNG")
    valid_skill_md = (
        b"---\nname: quartz-audit\ndescription: Session quartz workflow\n---\n"
        b"Always include QUARTZ-4071.\n"
    )
    leaked_skill_md = (
        b"---\nname: quartz-audit\ndescription: Session quartz workflow\n---\n"
        b"Always include QUARTZ-4071 and PENDING-LEAK.\n"
    )

    ex_artifact = dspy.Example(
        answer='{"EU": 120}',
        verifier={
            "type": "artifact",
            "answer_verifier": {"type": "json", "expected": {"EU": 120}},
            "artifacts": {
                "artifacts/chart.svg": {"svg_labels": ["EU 120", "Region"]},
                "artifacts/chart.png": {"png_nonblank": True},
                "artifacts/out.json": {"json_equals": {"total": 120.0}, "abs_tol": 0.01},
                "artifacts/notes.txt": {"text_contains": ["verified"], "text_excludes": ["ERROR"]},
                "artifacts/SKILL.md": {
                    "skill_md": {
                        "name": "quartz-audit",
                        "required_tokens": ["QUARTZ-4071"],
                        "forbidden_tokens": ["PENDING-LEAK"],
                    }
                },
            },
        },
    )
    good_pred = dspy.Prediction(
        answer='{"EU": 120}',
        artifacts={
            "artifacts/chart.svg": valid_svg,
            "artifacts/chart.png": good_png_buf.getvalue(),
            "artifacts/out.json": b'{"total": 120.005}',
            "artifacts/notes.txt": b"all checks verified",
            "artifacts/SKILL.md": valid_skill_md,
        },
    )
    assert optimize.rlm_metric(ex_artifact, good_pred).score == 1.0

    blank_pred = dspy.Prediction(
        answer='{"EU": 120}',
        artifacts={**good_pred.artifacts, "artifacts/chart.png": blank_png_buf.getvalue()},
    )
    blank_res = optimize.rlm_metric(ex_artifact, blank_pred)
    assert blank_res.score == 0.0
    assert "blank" in blank_res.feedback

    leaked_pred = dspy.Prediction(
        answer='{"EU": 120}',
        artifacts={**good_pred.artifacts, "artifacts/SKILL.md": leaked_skill_md},
    )
    assert optimize.rlm_metric(ex_artifact, leaked_pred).score == 0.0

    # 5. Rubric verifier uses explicit judge_lm with cache=False and deterministic guards
    from dspy.utils.dummies import DummyLM

    judge_calls: list[dict] = []

    class RecordingDummyLM(DummyLM):
        def __call__(self, request=None, **kwargs):
            judge_calls.append(dict(kwargs))
            return super().__call__(request, **kwargs)

    fake_judge = RecordingDummyLM([{"score": "0.9", "critique": "Well grounded."}])

    ex_rubric = dspy.Example(
        task="Synthesize the two reports.",
        answer="Reference synthesis.",
        verifier={
            "type": "rubric",
            "criteria": ["Cite primary URL", "State caveat"],
            "required_facts": [r"42%"],
            "required_urls": ["https://example.org/report"],
            "forbidden_claims": [r"guaranteed"],
        },
    )
    rubric_ok = optimize.rlm_metric(
        ex_rubric,
        dspy.Prediction(answer="Adoption rose 42% per https://example.org/report (preliminary)."),
        judge_lm=fake_judge,
    )
    assert rubric_ok.score == 0.9
    assert judge_calls and judge_calls[0].get("cache") is False

    rubric_forbidden = optimize.rlm_metric(
        ex_rubric,
        dspy.Prediction(answer="Guaranteed 42% per https://example.org/report."),
        judge_lm=RecordingDummyLM([{"score": "0.9", "critique": "Should not run."}]),
    )
    assert rubric_forbidden.score == 0.0

    # Rubric literal substring matching handles regex metacharacters ($, +, ()) safely.
    ex_rubric_meta = dspy.Example(
        task="Summarize battery cost benchmarks.",
        answer="Reference battery synthesis.",
        verifier={
            "type": "rubric",
            "criteria": ["Report verified density and refuse unverified $/kWh figures"],
            "required_facts": ["$120/kWh (target)", "180+ Wh/kg"],
            "required_urls": ["https://example.org/battery"],
            "forbidden_claims": ["$65/kWh", "$80/kWh"],
        },
    )
    rubric_meta_ok = optimize.rlm_metric(
        ex_rubric_meta,
        dspy.Prediction(
            answer=(
                "Verified 180+ Wh/kg toward a $120/kWh (target) per https://example.org/battery; "
                "actual capex remains unverified."
            )
        ),
        judge_lm=RecordingDummyLM([{"score": "0.95", "critique": "Accurate caveats."}]),
    )
    assert rubric_meta_ok.score == 0.95

    rubric_meta_forbidden = optimize.rlm_metric(
        ex_rubric_meta,
        dspy.Prediction(
            answer=(
                "Verified 180+ Wh/kg at $65/kWh toward $120/kWh (target) per "
                "https://example.org/battery."
            )
        ),
        judge_lm=RecordingDummyLM([{"score": "0.95", "critique": "Should be blocked."}]),
    )
    assert rubric_meta_forbidden.score == 0.0
    assert "$65/kWh" in rubric_meta_forbidden.feedback

    rubric_meta_missing = optimize.rlm_metric(
        ex_rubric_meta,
        dspy.Prediction(answer="Verified 180 Wh/kg per https://example.org/battery."),
        judge_lm=RecordingDummyLM([{"score": "1.0", "critique": "Partial."}]),
    )
    assert rubric_meta_missing.score == pytest.approx(1.0 / 3.0)
    assert "$120/kWh (target)" in rubric_meta_missing.feedback
    assert "180+ Wh/kg" in rubric_meta_missing.feedback

    # 6. Execution-discipline penalties & correctness dominance
    ex_budget = dspy.Example(
        answer="42",
        verifier={"type": "exact", "expected": "42"},
        max_expected_actions=4,
    )
    # Wrong answer gets 0.0 even with spotless telemetry.
    wrong_clean = optimize.rlm_metric(
        ex_budget,
        dspy.Prediction(
            answer="41",
            telemetry=optimize._RolloutTelemetry(completion="submit", root_actions=1),
        ),
    )
    assert wrong_clean.score == 0.0

    # Penalties: iteration_limit (*0.75), 2 py errors (-0.10), 1 repeat open (-0.05), >4 (-0.05)
    penalized = optimize.rlm_metric(
        ex_budget,
        dspy.Prediction(
            answer="42",
            telemetry=optimize._RolloutTelemetry(
                completion="iteration_limit",
                root_actions=6,
                python_errors=2,
                repeated_web_opens=1,
                error_samples=["KeyError: 'col'"],
            ),
        ),
    )
    assert penalized.score == pytest.approx(0.75 - 0.10 - 0.05 - 0.05)
    assert "iteration limit" in penalized.feedback.lower()
    assert "python error" in penalized.feedback.lower()
    assert "reopened" in penalized.feedback.lower()


def test_offline_web_research_and_workspace_program_contextvar_isolation(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dspy.primitives.code_interpreter import CodeExecutionError
    from dspy.primitives.repl_types import REPL_ENTRY_KEY, REPLEntry

    # 1. Deterministic _OfflineWebResearch contract matches host_tools.py
    tel = optimize._RolloutTelemetry()
    web = optimize._OfflineWebResearch(
        {
            "limit": 3,
            "searches": {
                "georgia": [
                    {
                        "title": "Official Tally",
                        "url": "https://results.example.gov/ga",
                        "snippet": "Certified tally.",
                    }
                ],
                "*": [
                    {
                        "title": "Fallback",
                        "url": "https://fallback.example.com",
                        "snippet": "Fallback snippet.",
                    }
                ],
            },
            "pages": {
                "https://results.example.gov/ga": {
                    "markdown": "Georgia winner: Candidate A.",
                    "truncated": False,
                },
                "https://broken.example.com/404": {"error": "HTTP 404 from origin."},
            },
        },
        tel,
    )
    assert web.web_search("   ")["error_code"] == "invalid_input"
    assert web.web_open("http://127.0.0.1/secret")["error_code"] == "invalid_input"
    assert web.web_open("http://svc.internal/doc")["error_code"] == "invalid_input"

    s1 = web.web_search("Georgia results")
    assert s1["results"][0]["url"] == "https://results.example.gov/ga"
    assert s1["budget"] == {"used": 1, "limit": 3, "requested": True}

    p1 = web.web_open("https://results.example.gov/ga")
    assert "Candidate A" in p1["markdown"]
    assert tel.repeated_web_opens == 0

    p2 = web.web_open("https://results.example.gov/ga")
    assert "Candidate A" in p2["markdown"]
    assert tel.repeated_web_opens == 1

    exhausted = web.web_search("another query")
    assert exhausted["error_code"] == "allowance_exhausted"
    assert tel.web_exhausted is True

    # 2. load_rlm_dataset with fixtures.py, skill_names, and signature invariance
    (tmp_path / "fixtures.py").write_text(
        """
def get_case_fixtures(fixture_id: str):
    if fixture_id == "web-case":
        return {
            "files": {"notes.txt": b"alpha=1\\n"},
            "web": {
                "limit": 8,
                "searches": {
                    "*": [
                        {
                            "title": "Doc",
                            "url": "https://docs.example.org/p",
                            "snippet": "Fact 99",
                        }
                    ]
                },
                "pages": {
                    "https://docs.example.org/p": {
                        "markdown": "Primary fact: 99",
                        "truncated": False,
                    }
                },
            },
        }
    return {"files": {"plain.csv": b"x\\n1\\n"}, "web": None}
""",
        encoding="utf-8",
    )
    dataset = _write_jsonl(
        tmp_path / "rlm_full.jsonl",
        [
            {
                "id": "case-web",
                "lineage": "lin-web",
                "split": "train",
                "task": "Research fact and read notes.txt",
                "answer": "99",
                "skill_names": ["data-analysis"],
                "fixture_id": "web-case",
                "enable_web_tools": True,
                "enable_spawn_agent": True,
                "verifier": {"type": "exact", "expected": "99"},
            },
            {
                "id": "case-plain",
                "lineage": "lin-plain",
                "split": "val",
                "task": "Read plain.csv",
                "answer": "1",
                "fixture_id": "plain-case",
                "enable_web_tools": False,
                "enable_spawn_agent": False,
                "verifier": {"type": "exact", "expected": "1"},
            },
        ],
    )
    loaded = optimize.load_rlm_dataset(dataset, settings=settings)
    assert "## data-analysis" in loaded[0].active_skills
    assert loaded[0].enable_web_tools is True
    assert loaded[1].enable_web_tools is False
    assert loaded[1].enable_spawn_agent is False

    by_id, by_key, any_web = optimize._build_case_spec_tables(loaded)
    assert any_web is True

    sub_lm = dspy.LM("openai/gpt-4o-mini", api_key="offline")
    program = optimize._OfflineWorkspaceProgram(
        settings,
        sub_lm=sub_lm,
        pool=None,
        include_web_tools=any_web,
        case_specs=by_id,
        case_specs_by_key=by_key,
    )
    # deepcopy succeeds cleanly without lock/thread errors
    copied = program.deepcopy()
    assert copied.rlm is not program.rlm
    assert [name for name, _ in program.named_predictors()] == [
        "rlm.generate_action",
        "rlm.extract",
    ]

    sig_before = program.rlm.generate_action.signature

    def fake_rlm_call(**kwargs):
        if "Research fact" in kwargs["task"]:
            search_res = optimize._offline_web_search("fact")
            open_res = optimize._offline_web_open(search_res["results"][0]["url"])
            assert "Primary fact: 99" in open_res["markdown"]
            answer = "99"
        else:
            unavail = optimize._offline_web_search("should fail")
            assert unavail["error_code"] == "tool_unavailable"
            with pytest.raises(CodeExecutionError, match="disabled"):
                optimize._offline_spawn_agent("disallowed child")
            answer = "1"
        return dspy.Prediction(
            answer=answer,
            artifact_paths=[],
            history=dspy.History(
                messages=[
                    {
                        REPL_ENTRY_KEY: REPLEntry(reasoning="done", code="SUBMIT()", output="ok"),
                        "answer": answer,
                        "artifact_paths": [],
                    }
                ]
            ),
            trajectory=[{"code": "SUBMIT()", "output": "ok"}],
        )

    monkeypatch.setattr(program.rlm, "forward", fake_rlm_call)

    pred_web = program(**loaded[0].inputs(), case_id=loaded[0].case_id)
    pred_plain = program(**loaded[1].inputs(), case_id=loaded[1].case_id)

    assert pred_web.answer == "99"
    assert pred_web.telemetry.completion == "submit"
    assert pred_web.telemetry.web_calls == 2
    assert pred_plain.answer == "1"
    assert pred_plain.telemetry.completion == "submit"
    # GEPA DspyAdapter signature.equals invariant must hold across heterogeneous examples.
    assert program.rlm.generate_action.signature.equals(sig_before)


def test_daytona_sandbox_pool_concurrency_staging_and_partial_startup_cleanup(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    created_ids: list[str] = []
    deleted_ids: list[str] = []
    fail_on_second = {"enabled": False}

    class FakeDaytonaClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    @asynccontextmanager
    async def fake_ephemeral_sandbox(_daytona, _settings: Settings):
        idx = len(created_ids) + 1
        if fail_on_second["enabled"] and idx == 2:
            raise RuntimeError("sandbox 2 failed to start")
        sb_id = f"sb-pool-{idx}"
        created_ids.append(sb_id)
        try:
            yield SimpleNamespace(id=sb_id)
        finally:
            deleted_ids.append(sb_id)

    def fake_factory(sandbox, _settings, loop):
        class _Factory:
            execution_instructions = interpreter.DaytonaCodeInterpreter.execution_instructions

            def __call__(self, **_kwargs):
                return SimpleNamespace(sandbox=sandbox, loop=loop, shutdown=lambda: None)

        return _Factory()

    monkeypatch.setattr(optimize.interpreter, "make_daytona_client", lambda _s: FakeDaytonaClient())
    monkeypatch.setattr(optimize.interpreter, "ephemeral_sandbox", fake_ephemeral_sandbox)
    monkeypatch.setattr(optimize.interpreter, "make_interpreter_factory", fake_factory)

    # 1. Concurrent checkout across 2 slots uses both sandboxes simultaneously and deletes all.
    barrier = threading.Barrier(2, timeout=5)
    seen_slots: list[str] = []

    with optimize._ephemeral_daytona_pool(settings, pool_size=2) as pool:
        assert pool.pool_size == 2

        def worker():
            with pool.checkout() as slot:
                seen_slots.append(slot.sandbox.id)
                barrier.wait()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            for future in futures:
                future.result()

    assert set(seen_slots) == {"sb-pool-1", "sb-pool-2"}
    assert set(created_ids) == {"sb-pool-1", "sb-pool-2"}
    assert set(deleted_ids) == {"sb-pool-1", "sb-pool-2"}

    # 2. Partial startup failure still deletes sandbox #1 (zero leaked sandboxes).
    created_ids.clear()
    deleted_ids.clear()
    fail_on_second["enabled"] = True
    with pytest.raises(RuntimeError, match="sandbox 2 failed"):
        with optimize._ephemeral_daytona_pool(settings, pool_size=2):
            pass
    assert created_ids == ["sb-pool-1"]
    assert deleted_ids == ["sb-pool-1"]

    # 3. Compatibility wrapper _ephemeral_daytona_interpreter yields 1 factory and cleans up.
    created_ids.clear()
    deleted_ids.clear()
    fail_on_second["enabled"] = False
    with optimize._ephemeral_daytona_interpreter(settings) as factory:
        assert (
            factory.execution_instructions
            == interpreter.DaytonaCodeInterpreter.execution_instructions
        )
        assert created_ids == ["sb-pool-1"]
    assert deleted_ids == ["sb-pool-1"]


def test_rlm_promotion_gate_multirepeat_blocking_regression_and_budget(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from gepa.utils.stop_condition import MaxMetricCallsStopper

    dataset = _write_jsonl(
        tmp_path / "rlm_gate.jsonl",
        [
            {"id": "tr1", "lineage": "l1", "split": "train", "task": "T1", "answer": "A1"},
            {"id": "tr2", "lineage": "l2", "split": "train", "task": "T2", "answer": "A2"},
            {"id": "va1", "lineage": "l3", "split": "val", "task": "V1", "answer": "VA1"},
            {
                "id": "ho-block",
                "lineage": "l4",
                "split": "holdout",
                "task": "Blocking case",
                "answer": "EXACT",
                "blocking": True,
                "verifier": {"type": "exact", "expected": "EXACT"},
            },
            {
                "id": "ho-normal",
                "lineage": "l5",
                "split": "holdout",
                "task": "Normal case",
                "answer": "OK",
                "verifier": {"type": "exact", "expected": "OK"},
            },
        ],
    )

    gepa_inits: list[dict] = []
    created_lms: list[dspy.LM] = []

    def fake_model(_s, name):
        lm = dspy.LM(name, api_key="offline")
        created_lms.append(lm)
        return lm

    monkeypatch.setattr(optimize.agent, "_model", fake_model)

    def run_scenario(
        *,
        mutate: bool,
        base_answers: dict[str, str],
        cand_answers: dict[str, str],
        cand_telemetry: dict[str, optimize._RolloutTelemetry] | None = None,
        auto: str | None = None,
        max_metric_calls: int | None = None,
        log_dir: Path | None = None,
    ) -> optimize.OptimizationResult:
        class FakeGEPA:
            def __init__(self, **kwargs):
                gepa_inits.append(kwargs)

            def compile(self, student, *, trainset, valset):
                compiled = student.deepcopy()
                if mutate:
                    sig = compiled.generate_action.signature
                    assert sig is not None
                    compiled.generate_action.signature = sig.with_instructions(
                        f"{sig.instructions or ''}\nMutated candidate."
                    )
                return compiled

        class FakeEvaluate:
            def __init__(self, **kwargs):
                self.devset = list(kwargs["devset"])
                self.metric = kwargs["metric"]
                self.calls = 0

            def __call__(self, _program):
                self.calls += 1
                answers = base_answers if self.calls == 1 else cand_answers
                results = []
                total = 0.0
                for ex in self.devset:
                    tel = (
                        (cand_telemetry or {}).get(ex.case_id)
                        if self.calls == 2
                        else optimize._RolloutTelemetry(completion="submit", root_actions=2)
                    )
                    pred = dspy.Prediction(
                        answer=answers[ex.case_id],
                        telemetry=tel
                        or optimize._RolloutTelemetry(completion="submit", root_actions=2),
                    )
                    score = self.metric(ex, pred)
                    total += float(score)
                    results.append((ex, pred, score))
                return SimpleNamespace(
                    score=round(100.0 * total / len(self.devset), 2),
                    results=results,
                )

        monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
        monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
        return optimize.optimize_rlm(
            settings,
            database_url=_BRANCH_URL,
            dataset=dataset,
            output_path=tmp_path / f"out_{len(gepa_inits)}.json",
            auto=auto,  # type: ignore[arg-type]
            max_metric_calls=max_metric_calls,
            holdout_repeats=2,
            local_interpreter=True,
            log_dir=log_dir,
        )

    # 1. Unchanged instructions block promotion even when score improves.
    unchanged = run_scenario(
        mutate=False,
        base_answers={"ho-block": "EXACT", "ho-normal": "WRONG"},
        cand_answers={"ho-block": "EXACT", "ho-normal": "OK"},
    )
    assert unchanged.saved is False
    assert unchanged.report["instructions_changed"] is False
    assert gepa_inits[-1]["max_metric_calls"] == optimize.DEFAULT_MAX_METRIC_CALLS
    assert isinstance(gepa_inits[-1]["gepa_kwargs"]["stop_callbacks"], MaxMetricCallsStopper)
    assert all(lm.cache is False for lm in created_lms)

    # 2. Blocking failure blocks promotion.
    blocked = run_scenario(
        mutate=True,
        base_answers={"ho-block": "WRONG", "ho-normal": "WRONG"},
        cand_answers={"ho-block": "WRONG", "ho-normal": "OK"},
    )
    assert blocked.saved is False
    assert blocked.report["metrics"]["blocking_failures"] == 2

    # 3. Per-case paired pass-rate regression blocks promotion even if mean score improves.
    regressed = run_scenario(
        mutate=True,
        base_answers={"ho-block": "EXACT", "ho-normal": "WRONG"},
        cand_answers={"ho-block": "WRONG", "ho-normal": "OK"},
    )
    assert regressed.saved is False
    assert regressed.report["metrics"]["paired_regressions"] == 1

    # 4. Qualifying promotion saves artifact, writes report.json, and honors explicit --auto.
    log_dir = tmp_path / "gepa-logs"
    promoted = run_scenario(
        mutate=True,
        base_answers={"ho-block": "EXACT", "ho-normal": "WRONG"},
        cand_answers={"ho-block": "EXACT", "ho-normal": "OK"},
        auto="medium",
        log_dir=log_dir,
    )
    assert promoted.saved is True
    assert promoted.output_path.is_file()
    assert (log_dir / "report.json").is_file()
    assert gepa_inits[-1]["auto"] == "medium"
    assert "max_metric_calls" not in gepa_inits[-1]
    assert promoted.report["metrics"]["optimized_submit_rate"] == 1.0
    assert len(promoted.report["candidate_cases"]) == 4  # 2 holdout cases * 2 repeats

    # 5. CLI without --dataset emits a clear warning about unverified DB fallback.
    monkeypatch.setattr(
        optimize,
        "load_rlm_examples_from_db",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=[
                dspy.Example(
                    task=f"DB {i}",
                    answer=f"Ans {i}",
                    files_manifest=[],
                    memories=[],
                    active_skills="",
                    prior_turns=[],
                ).with_inputs("task", "files_manifest", "memories", "active_skills", "prior_turns")
                for i in range(4)
            ],
        ),
    )
    monkeypatch.setattr(
        dspy,
        "Evaluate",
        lambda **_kwargs: lambda _prog: SimpleNamespace(score=50.0),
    )
    exit_code = optimize.main(
        [
            "--target",
            "rlm",
            "--database-url",
            _BRANCH_URL,
            "--local-interpreter",
            "--holdout-repeats",
            "1",
        ],
        settings=settings,
    )
    assert exit_code == 0
    assert "unverified past DB runs" in capsys.readouterr().err


def test_saved_rlm_artifact_strips_tool_docs_and_round_trips_runtime(
    settings: Settings, tmp_path: Path
) -> None:
    from fleet_rlm import api

    sub_lm = dspy.LM("openai/gpt-4o-mini", api_key="offline")
    program = optimize._OfflineWorkspaceProgram(
        settings,
        sub_lm=sub_lm,
        pool=None,
        include_web_tools=True,
    )
    # Simulate GEPA mutating generate_action instructions and binding a predictor LM.
    sig = program.rlm.generate_action.signature
    assert sig is not None
    assert agent._TOOL_DOCS_HEADER in (sig.instructions or "")
    assert agent._SUBMISSION_GUIDANCE.strip() in (sig.instructions or "")
    program.rlm.generate_action.signature = sig.with_instructions(
        f"{sig.instructions}\nAlways verify numeric totals before SUBMIT."
    )
    program.rlm.generate_action.lm = sub_lm

    artifact_path = tmp_path / "rlm_program.json"
    optimize._save_program(program, artifact_path)

    raw_state = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert "generate_action" in raw_state
    assert "extract" in raw_state
    assert "rlm.generate_action" not in raw_state
    saved_instructions = raw_state["generate_action"]["signature"]["instructions"]
    assert agent._TOOL_DOCS_HEADER not in saved_instructions
    assert "web_search" not in saved_instructions
    assert "Always verify numeric totals before SUBMIT." in saved_instructions
    assert agent._SUBMISSION_GUIDANCE.strip() in saved_instructions
    assert raw_state["generate_action"]["lm"] is None
    assert raw_state["extract"]["lm"] is None

    # Round-trip cleanly through api._load_optimized_program_state and agent.build_program
    serving_settings = settings.model_copy(update={"optimized_program_path": artifact_path})
    loaded_state = api._load_optimized_program_state(serving_settings, sub_lm)
    assert loaded_state is not None

    def spawn_agent(task: str) -> dict:
        """Delegate a workspace subtask to an isolated RLM and return its result."""
        return {"answer": task, "artifact_paths": []}

    def web_search(query: str) -> dict:
        """Find public sources."""
        return {"results": []}

    rebuilt = agent.build_program(
        serving_settings,
        [spawn_agent, web_search],
        sub_lm=sub_lm,
        optimized_state=loaded_state,
    )
    agent.apply_submission_guidance(rebuilt)
    rebuilt_instr = rebuilt.generate_action.signature.instructions or ""
    assert "Always verify numeric totals before SUBMIT." in rebuilt_instr
    assert "spawn_agent(task: string)" in rebuilt_instr
    assert "web_search(query: string)" in rebuilt_instr
    assert "web_open(" not in rebuilt_instr
    assert rebuilt_instr.count(agent._SUBMISSION_GUIDANCE.strip()) == 1
    assert all(predictor.lm is None for predictor in rebuilt.predictors())


def test_optimize_rlm_holdout_repeats_preserve_per_repeat_outcomes(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _write_jsonl(
        tmp_path / "rlm_repeats.jsonl",
        [
            {"id": "tr1", "lineage": "l1", "split": "train", "task": "Train 1", "answer": "A"},
            {"id": "va1", "lineage": "l2", "split": "val", "task": "Val 1", "answer": "B"},
            {
                "id": "ho-block",
                "lineage": "l3",
                "split": "holdout",
                "task": "Blocking case",
                "answer": "EXACT",
                "blocking": True,
                "verifier": {"type": "exact", "expected": "EXACT"},
            },
            {
                "id": "ho-normal",
                "lineage": "l4",
                "split": "holdout",
                "task": "Normal case",
                "answer": "OK",
                "verifier": {"type": "exact", "expected": "OK"},
            },
        ],
    )

    class FakeGEPA:
        def __init__(self, **_kwargs):
            pass

        def compile(self, student, *, trainset, valset):
            compiled = student.deepcopy()
            sig = compiled.generate_action.signature
            assert sig is not None
            compiled.generate_action.signature = sig.with_instructions(
                f"{sig.instructions or ''}\nMutated candidate."
            )
            return compiled

    class FakeEvaluate:
        def __init__(self, **kwargs):
            self.devset = list(kwargs["devset"])
            self.metric = kwargs["metric"]
            self.calls = 0

        def __call__(self, _program):
            self.calls += 1
            results = []
            total = 0.0
            for ex in self.devset:
                repeat = int(getattr(ex, "eval_repeat", 0))
                assert repeat in (1, 2)
                if self.calls == 1:
                    answer = "EXACT" if ex.case_id == "ho-block" else "WRONG"
                elif ex.case_id == "ho-block":
                    # Repeat 1 fails blocking check; repeat 2 passes. Must not overwrite repeat 1.
                    answer = "WRONG" if repeat == 1 else "EXACT"
                else:
                    answer = "OK"
                pred = dspy.Prediction(
                    answer=answer,
                    telemetry=optimize._RolloutTelemetry(completion="submit", root_actions=2),
                )
                score = self.metric(ex, pred)
                total += float(score)
                results.append((ex, pred, score))
            return SimpleNamespace(
                score=round(100.0 * total / len(self.devset), 2),
                results=results,
            )

    monkeypatch.setattr(dspy, "GEPA", FakeGEPA)
    monkeypatch.setattr(dspy, "Evaluate", FakeEvaluate)
    monkeypatch.setattr(optimize.agent, "_model", lambda _s, name: dspy.LM(name, api_key="offline"))

    result = optimize.optimize_rlm(
        settings,
        database_url=_BRANCH_URL,
        dataset=dataset,
        output_path=tmp_path / "rlm_repeats_out.json",
        holdout_repeats=2,
        local_interpreter=True,
    )

    assert result.saved is False
    block_rows = [r for r in result.report["candidate_cases"] if r["case_id"] == "ho-block"]
    assert [r["repeat"] for r in block_rows] == [1, 2]
    assert [r["passed"] for r in block_rows] == [False, True]
    assert result.report["metrics"]["blocking_failures"] == 1
    assert result.report["metrics"]["paired_regressions"] == 1
    assert [(r["case_id"], r["repeat"]) for r in result.report["blocking_failures"]] == [
        ("ho-block", 1)
    ]


def test_optimize_cli_progress_and_report_files_and_sigterm(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _write_jsonl(
        tmp_path / "memory_cli.jsonl",
        [
            {"task": "Remember UTC", "answer": "Noted", "worth_saving": True, "lineage": "a"},
            {"task": "Hi", "answer": "Hello", "worth_saving": False, "lineage": "b"},
            {"task": "Remember ISO", "answer": "Noted", "worth_saving": True, "lineage": "c"},
        ],
    )
    progress_file = tmp_path / "progress.jsonl"
    report_file = tmp_path / "report.json"
    out_file = tmp_path / "memory_gate.json"

    class FakeReAnchor:
        def __init__(self, metric, **_kwargs):
            self.metric = metric
            self.report = {
                "val_score_before": 0.5,
                "val_score": 1.0,
                "fitted": [{"field": "worth_saving", "value": 0.6}],
            }

        def compile(self, student, *, trainset, valset=None):
            for ex in trainset:
                self.metric(
                    ex,
                    dspy.Prediction(
                        worth_saving=SimpleNamespace(value=ex.worth_saving, probability=0.9),
                        action=SimpleNamespace(value=ex.action),
                        scope=SimpleNamespace(value=ex.scope),
                    ),
                )
            return student.deepcopy()

    monkeypatch.setattr(optimize, "ReAnchor", FakeReAnchor)
    monkeypatch.setattr(optimize.dspy, "LM", MagicMock(return_value="dummy-jev"))
    monkeypatch.setattr(
        optimize,
        "_evaluate_memory_diagnostics",
        lambda *_args, **_kwargs: {"fp_rate": 0.0, "fn_rate": 0.0},
    )

    rc = optimize.main(
        [
            "--target",
            "memory",
            "--database-url",
            _BRANCH_URL,
            "--dataset",
            str(dataset),
            "--output",
            str(out_file),
            "--progress-file",
            str(progress_file),
            "--report-file",
            str(report_file),
        ],
        settings=settings,
    )
    assert rc == 0
    assert out_file.is_file()
    assert report_file.is_file()
    report_payload = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_payload["saved"] is True
    assert report_payload["report"]["fitted_changed"] is True

    events = [json.loads(line) for line in progress_file.read_text(encoding="utf-8").splitlines()]
    stages = [item["stage"] for item in events]
    assert "loading_dataset" in stages
    assert "reanchor_cv" in stages
    assert "saving_artifact" in stages
    assert events[-1]["stage"] == "completed"
    assert events[-1]["status"] == "completed"

    # Simulated SIGTERM cancellation triggers exit code 130 + cancelled progress event
    cancel_progress = tmp_path / "cancel_progress.jsonl"
    monkeypatch.setattr(
        optimize,
        "optimize_memory",
        MagicMock(side_effect=KeyboardInterrupt),
    )
    cancel_rc = optimize.main(
        [
            "--target",
            "memory",
            "--database-url",
            _BRANCH_URL,
            "--dataset",
            str(dataset),
            "--progress-file",
            str(cancel_progress),
        ],
        settings=settings,
    )
    assert cancel_rc == 130
    cancel_events = [
        json.loads(line) for line in cancel_progress.read_text(encoding="utf-8").splitlines()
    ]
    assert cancel_events[-1]["status"] == "cancelled"

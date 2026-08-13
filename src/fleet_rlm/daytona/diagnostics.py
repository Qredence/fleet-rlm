"""Opt-in, disposable Daytona environment diagnostics.

The doctor never creates Fleet domain rows or Sandbox bindings. External work is
behind an injectable dependency seam so unit tests remain credential-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import uuid4

from fleet_rlm.config import Settings
from fleet_rlm.daytona.errors import DaytonaAdapterError, classify_provider_error
from fleet_rlm.daytona.platform import (
    LiveDaytonaPlatform,
    LiveDaytonaVolumeClient,
    build_daytona_client,
)
from fleet_rlm.daytona.provisioning import (
    ExpectedWorkspaceMount,
    sandbox_spec_from_settings,
    snapshot_dependency_import_names,
    verify_sandbox_spec,
    verify_sandbox_workspace_mount,
    volume_config_from_settings,
    workspace_volume_subpath,
)
from fleet_rlm.persistence.database import ensure_database_compatible

DoctorStepName = Literal["settings", "database", "provider", "rlm", "sandbox", "interpreter", "cleanup"]
DoctorFailureCategory = Literal[
    "settings",
    "database",
    "auth",
    "quota",
    "network_timeout",
    "provider_5xx",
    "request_validation",
    "mount_mismatch",
    "rlm_provider",
    "interpreter",
    "cleanup",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class DaytonaDoctorStep:
    """One safe, user-displayable diagnostic outcome."""

    name: DoctorStepName
    ok: bool
    message: str
    category: DoctorFailureCategory | None = None


@dataclass(frozen=True, slots=True)
class DaytonaDoctorResult:
    """Complete doctor outcome in execution order."""

    ok: bool
    steps: tuple[DaytonaDoctorStep, ...]
    failure_category: DoctorFailureCategory | None = None


class DaytonaDoctorDependencies(Protocol):
    """External operations used by :func:`run_daytona_doctor`."""

    async def check_database(self, settings: Settings) -> None: ...

    async def resolve_volume(self, settings: Settings) -> str: ...

    async def check_rlm_readiness(self, settings: Settings) -> None: ...

    async def create_sandbox(
        self,
        *,
        expected_mount: ExpectedWorkspaceMount,
        labels: dict[str, str],
        ephemeral: bool,
    ) -> Any: ...

    async def verify_mount(self, sandbox: Any, expected_mount: ExpectedWorkspaceMount) -> None: ...

    async def execute(self, sandbox: Any) -> str: ...

    async def delete_sandbox(self, sandbox: Any) -> None: ...

    async def close(self) -> None: ...


class _ProductionDaytonaDoctorDependencies:
    """Production operations composed from Fleet's canonical adapters."""

    def __init__(self, settings: Settings) -> None:
        self._client = build_daytona_client(settings)
        self._sandbox_spec = sandbox_spec_from_settings(settings)
        self._platform = LiveDaytonaPlatform(self._client, self._sandbox_spec)
        self._volume_client = LiveDaytonaVolumeClient(self._client)
        self._volume_config = volume_config_from_settings(settings)

    async def check_database(self, settings: Settings) -> None:
        database_url = (settings.database_url or "").strip()
        await ensure_database_compatible(database_url)

    async def resolve_volume(self, settings: Settings) -> str:
        del settings
        volume = await self._volume_client.get(self._volume_config.name, create=False)
        volume_id = getattr(volume, "id", None)
        if volume_id is None:
            raise DaytonaAdapterError(
                message="configured Daytona Volume did not expose an id",
                cause_type="VolumeVisibilityError",
            )
        return str(volume_id)

    async def create_sandbox(
        self,
        *,
        expected_mount: ExpectedWorkspaceMount,
        labels: dict[str, str],
        ephemeral: bool,
    ) -> Any:
        return await self._platform.create(
            volume_id=expected_mount.volume_id,
            mount_path=expected_mount.mount_path,
            volume_subpath=expected_mount.volume_subpath,
            labels=labels,
            with_volume=True,
            ephemeral=ephemeral,
        )

    async def verify_mount(self, sandbox: Any, expected_mount: ExpectedWorkspaceMount) -> None:
        refresh = getattr(sandbox, "refresh_data", None)
        if callable(refresh):
            await refresh()
        mounts = getattr(sandbox, "volumes", None)
        if mounts is None:
            mounts = getattr(sandbox, "mounts", None)
        if not mounts:
            raise DaytonaAdapterError(
                message="sandbox did not expose scoped volume mount metadata",
                cause_type="WorkspaceMountMismatch",
            )
        verify_sandbox_workspace_mount(sandbox, expected_mount)
        verify_sandbox_spec(sandbox, self._sandbox_spec)

    async def execute(self, sandbox: Any) -> str:
        """
        Run the Daytona interpreter diagnostic in a temporary sandbox context.

        Parameters:
            sandbox (Any): Sandbox whose code interpreter runs the diagnostic.

        Returns:
            str: Diagnostic process output.

        Raises:
            RuntimeError: If the interpreter reports an execution error.
        """
        context = await sandbox.code_interpreter.create_context()
        run_error: BaseException | None = None
        try:
            dependencies = snapshot_dependency_import_names()
            result = await sandbox.code_interpreter.run_code(
                "import importlib, importlib.metadata, os, shutil, sys\n"
                "assert sys.version_info[:3] == (3, 13, 13)\n"
                "assert os.geteuid() != 0\n"
                "assert os.getcwd() == '/home/daytona'\n"
                "assert shutil.which('git'), 'git toolchain missing from snapshot'\n"
                f"dependencies = {dependencies!r}\n"
                "for package, module, expected in dependencies:\n"
                "    importlib.import_module(module)\n"
                "    assert importlib.metadata.version(package) == expected\n"
                "print('fleet-doctor-ok')",
                context=context,
            )
            stdout = getattr(result, "stdout", result)
            error = getattr(result, "error", None)
            if error:
                raise RuntimeError("interpreter returned an error")
            return str(stdout or "")
        except BaseException as exc:
            run_error = exc
            raise
        finally:
            try:
                await sandbox.code_interpreter.delete_context(context)
            except Exception:
                if run_error is None:
                    raise

    async def delete_sandbox(self, sandbox: Any) -> None:
        if getattr(sandbox, "id", None) is None:
            raise DaytonaAdapterError(
                message="disposable sandbox did not expose an id",
                cause_type="SandboxIdentityError",
            )
        await self._platform.delete(sandbox)

    async def close(self) -> None:
        await self._client.close()

    async def check_rlm_readiness(self, settings: Settings) -> None:
        from fleet_rlm.rlm.provider_probe import probe_configured_root_lm

        await probe_configured_root_lm(
            settings,
            interpreter_factory=_provider_probe_interpreter,
            child_runtime_factory=_ProviderProbeChildRuntimeFactory(),
        )


def _provider_probe_interpreter() -> Any:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend

    return DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())


def _provider_probe_child_runtime(call_index: int) -> Any:
    from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
    from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease

    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    return ChildRuntimeLease(
        interpreter=interpreter,
        sandbox_id=f"provider-probe-{call_index}",
        volume_id="in-process",
        volume_subpath=f"recursive/provider-probe/run/{call_index}",
        _close=interpreter.shutdown,
    )


class _ProviderProbeChildRuntimeFactory:
    """In-process probe factory with no late-acquisition ownership."""

    def __call__(self, call_index: int) -> Any:
        return _provider_probe_child_runtime(call_index)

    def wait_owned(self) -> None:
        return None

    def raise_if_cleanup_failed(self) -> None:
        return None


_SUCCESS_MESSAGES: dict[DoctorStepName, str] = {
    "settings": "Fleet Daytona settings are valid.",
    "database": "Database connection and Alembic revision are compatible.",
    "provider": "Daytona authentication and Volume access succeeded.",
    "rlm": "Configured Root LM satisfies the pinned DSPy RLM action contract.",
    "sandbox": "Disposable scoped Sandbox mount is valid.",
    "interpreter": "Daytona interpreter executed the diagnostic.",
    "cleanup": "Disposable Daytona Sandbox was deleted.",
}

_FAILURE_MESSAGES: dict[DoctorFailureCategory, str] = {
    "settings": "Required Fleet Daytona settings are missing or invalid.",
    "database": "Database connection or Alembic revision validation failed.",
    "auth": "Daytona authentication was rejected.",
    "quota": "Daytona capacity or quota prevented the diagnostic.",
    "network_timeout": "Daytona could not be reached before the request timed out.",
    "provider_5xx": "Daytona returned a provider service error.",
    "request_validation": "Daytona rejected the diagnostic request.",
    "mount_mismatch": "The disposable Sandbox Volume mount did not match the requested scope.",
    "rlm_provider": "The configured Root LM is not compatible with the pinned DSPy RLM action protocol.",
    "interpreter": "The Daytona interpreter diagnostic failed.",
    "cleanup": "Disposable Daytona Sandbox cleanup failed.",
    "unknown": "The Daytona diagnostic failed safely.",
}


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _failure_category(exc: BaseException, step: DoctorStepName) -> DoctorFailureCategory:
    step_categories: dict[DoctorStepName, DoctorFailureCategory] = {
        "settings": "settings",
        "database": "database",
        "cleanup": "cleanup",
    }
    if step in step_categories:
        return step_categories[step]
    if step == "rlm":
        return "rlm_provider"
    provider_kind = classify_provider_error(exc)
    if provider_kind == "mount_mismatch":
        return "mount_mismatch"
    if provider_kind == "auth":
        return "auth"
    if provider_kind == "quota":
        return "quota"
    if provider_kind in {"network", "timeout"}:
        return "network_timeout"
    if provider_kind == "provider_5xx":
        return "provider_5xx"
    if provider_kind == "request_validation":
        return "request_validation"
    if step == "interpreter":
        return "interpreter"
    status = _status_code(exc)
    if status in {405, 406, 415}:
        return "request_validation"
    error_name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
        marker in error_name for marker in ("timeout", "network", "connection", "connect")
    ):
        return "network_timeout"
    return "unknown"


def _failed_step(name: DoctorStepName, exc: BaseException) -> DaytonaDoctorStep:
    category = _failure_category(exc, name)
    return DaytonaDoctorStep(name, False, _FAILURE_MESSAGES[category], category)


def _settings_failure(settings: Settings) -> Exception | None:
    key = settings.daytona_api_key
    raw_key = key.get_secret_value().strip() if key is not None else ""
    if not raw_key or not (settings.database_url or "").strip():
        return ValueError("required settings are missing")
    try:
        volume_config_from_settings(settings)
        sandbox_spec_from_settings(settings)
    except (TypeError, ValueError) as exc:
        return exc
    return None


async def run_daytona_doctor(
    settings: Settings,
    *,
    dependencies: DaytonaDoctorDependencies | None = None,
) -> DaytonaDoctorResult:
    """Run the opt-in Daytona doctor and always clean up acquired resources."""
    settings_error = _settings_failure(settings)
    if settings_error is not None:
        failed = _failed_step("settings", settings_error)
        return DaytonaDoctorResult(ok=False, steps=(failed,), failure_category=failed.category)
    if dependencies is None:
        try:
            dependencies = _ProductionDaytonaDoctorDependencies(settings)
        except Exception as exc:
            failed = _failed_step("provider", exc)
            return DaytonaDoctorResult(
                ok=False,
                steps=(
                    DaytonaDoctorStep("settings", True, _SUCCESS_MESSAGES["settings"]),
                    failed,
                ),
                failure_category=failed.category,
            )

    steps: list[DaytonaDoctorStep] = [DaytonaDoctorStep("settings", True, _SUCCESS_MESSAGES["settings"])]
    volume = volume_config_from_settings(settings)
    workspace_id = uuid4()
    doctor_id = uuid4().hex
    sandbox: Any | None = None
    primary_failure: DaytonaDoctorStep | None = None
    current_step: DoctorStepName = "database"
    try:
        await dependencies.check_database(settings)
        steps.append(DaytonaDoctorStep("database", True, _SUCCESS_MESSAGES["database"]))
        current_step = "provider"
        volume_id = await dependencies.resolve_volume(settings)
        steps.append(DaytonaDoctorStep("provider", True, _SUCCESS_MESSAGES["provider"]))
        check_rlm_readiness = getattr(dependencies, "check_rlm_readiness", None)
        if callable(check_rlm_readiness):
            current_step = "rlm"
            await check_rlm_readiness(settings)
            steps.append(DaytonaDoctorStep("rlm", True, _SUCCESS_MESSAGES["rlm"]))
        expected = ExpectedWorkspaceMount(
            volume_id=volume_id,
            volume_subpath=workspace_volume_subpath(workspace_id),
            mount_path=volume.mount_path,
            workspace_id=workspace_id,
        )
        labels = {
            "fleet_package": "fleet_rlm",
            "purpose": "fleet-daytona-doctor",
            "doctor_id": doctor_id,
            "workspace_id": str(workspace_id),
        }
        current_step = "sandbox"
        sandbox = await dependencies.create_sandbox(
            expected_mount=expected,
            labels=labels,
            ephemeral=True,
        )
        await dependencies.verify_mount(sandbox, expected)
        steps.append(DaytonaDoctorStep("sandbox", True, _SUCCESS_MESSAGES["sandbox"]))
        current_step = "interpreter"
        output = await dependencies.execute(sandbox)
        if output.strip() != "fleet-doctor-ok":
            raise RuntimeError("interpreter diagnostic output mismatch")
        steps.append(DaytonaDoctorStep("interpreter", True, _SUCCESS_MESSAGES["interpreter"]))
    except Exception as exc:
        primary_failure = _failed_step(current_step, exc)
        steps.append(primary_failure)
    finally:
        if sandbox is not None:
            try:
                await dependencies.delete_sandbox(sandbox)
            except Exception as exc:
                cleanup_failure = _failed_step("cleanup", exc)
                steps.append(cleanup_failure)
                if primary_failure is None:
                    primary_failure = cleanup_failure
            else:
                steps.append(DaytonaDoctorStep("cleanup", True, _SUCCESS_MESSAGES["cleanup"]))
        try:
            await dependencies.close()
        except Exception as exc:
            cleanup_failure = _failed_step("cleanup", exc)
            steps.append(cleanup_failure)
            if primary_failure is None:
                primary_failure = cleanup_failure

    return DaytonaDoctorResult(
        ok=primary_failure is None,
        steps=tuple(steps),
        failure_category=primary_failure.category if primary_failure is not None else None,
    )


__all__ = [
    "DaytonaDoctorDependencies",
    "DaytonaDoctorResult",
    "DaytonaDoctorStep",
    "run_daytona_doctor",
]

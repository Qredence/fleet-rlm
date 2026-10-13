from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import daytona._async.snapshot as snapshot_module
import pytest
from daytona._async.snapshot import AsyncSnapshotService

from fleet_rlm.daytona.diagnostics import environment_manifest, snapshot_dependency_import_names
from fleet_rlm.daytona.specs import DaytonaSandboxSpec
from scripts import daytona_snapshot


def _snapshot(
    spec: DaytonaSandboxSpec,
    *,
    state: str = "active",
    dockerfile: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=spec.snapshot,
        state=state,
        cpu=spec.cpu,
        mem=spec.memory_gib,
        disk=spec.disk_gib,
        build_info=SimpleNamespace(
            dockerfile_content=dockerfile or daytona_snapshot.build_snapshot_image(spec).dockerfile()
        ),
    )


def test_help_needs_no_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        daytona_snapshot.main(["--help"])
    assert "immutable Fleet Daytona Snapshot" in capsys.readouterr().out


# Captured from the existing declarations before the dependency/refactoring
# cleanup. Do not derive these expectations from the builder under test.
_HISTORICAL_IMAGE_SHA256 = {
    "fleet-rlm-python313-v1": "8cacb9d79745aaefa689219b43149f6e54c157852bd33363a3d66f41215f52f3",
    "fleet-rlm-python313-v2": "2e647b8ccdeb45ac45113c10e4f0ce79b791b44e8297fe7e4db2956053b7adc2",
    "fleet-rlm-python313-v3": "3e755bfe41bfc0c566cc69d9c71b5d0b229dbe30e99a8ebf5abc4a1fb0c498ed",
    "fleet-rlm-python313-v4": "267115072e5a6811fad3546a7fcd09a6fe85efe9fc3e70744bb77aadcab428c4",
    "fleet-rlm-python313-v5": "c1365aa178b975ec4e054c7e6e5e5e6a20765290d77a7cb1e97883ad04ca3054",
    "fleet-rlm-python313-v6": "6ebaadc7f15165ddad7965d58e678c5cf967371041602cbc9ada1a8b8c54e3d7",
    "fleet-rlm-python313-v7": "17d267c25bacc0ad59361f7270c27fce357cb9b59c4255e5cf56025028f87daf",
    "fleet-rlm-python313-v8": "2dff5d6df92211729666c928f9e972a82e23c602e3f4a4cea0439f45fe30c6e9",
    "fleet-rlm-python313-v9": "e162f9cc2afd819679509408a51620bdf22a0c41fb586c398c453a1abe6f735c",
    "fleet-rlm-python313-v10": "1cccfc4f801bf75d19ed848e7e4ab05e1a3c222e32f414e727258e9e5c2ff062",
    "fleet-rlm-python313-v11": "7c83f700eb731bb97b3a050e1b905b036b8195fc6fa1f367184ef2deb9c9e8ae",
    "fleet-rlm-python313-v12": "36f4e84fb2311fe705fd3f51ef498855154f53117c1ef814eeae75fca626a341",
    "fleet-rlm-python313-child-v1": "87cf0f33675c699fa7e6d694036c73dac0a5378691d2e176715a6d5dc3af71ff",
    "fleet-rlm-python313-child-v2": "14267d2dc9ee7f441378334fe0d47484d20d289df29fe70466552dca96f75c8e",
    "fleet-rlm-python313-child-v3": "f6f5c458cbb90a803336693cf2392346b9bf212a0d3edd9ecea6744ca2ad1197",
    "fleet-rlm-python313-child-v4": "51abdabc300ccdc492581cf4a93afc84ae50d3e46755481418a0b4ed6bee514e",
    "fleet-rlm-python313-child-v5": "493ce9140fa6ab4440e7ab126e242171d5a75b9c404d6e9a6ab0c3c86814c69d",
}


@pytest.mark.parametrize("name,expected", _HISTORICAL_IMAGE_SHA256.items())
def test_historical_snapshot_declarations_keep_their_fixed_identity(name: str, expected: str) -> None:
    profile = "semantic-child" if "-child-" in name else "session"
    spec = daytona_snapshot._spec(name, profile)
    dockerfile = daytona_snapshot.build_snapshot_image(spec).dockerfile()
    assert hashlib.sha256(dockerfile.encode()).hexdigest() == expected
    if name == "fleet-rlm-python313-v5":
        # This legacy image predates the baked manifest.
        assert environment_manifest(spec).digest == "5e4f582cb7f83218dff5d61a32fdd5485db2e89645452734e5e63c1294d323aa"


def test_new_snapshot_manifest_reads_active_requirements_without_changing_historical_pins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleet_rlm.daytona import diagnostics

    (tmp_path / "snapshot-requirements.txt").write_text("numpy==9.9.9\n")
    (tmp_path / "browser-requirements.txt").write_text("playwright==9.9.9\n")
    monkeypatch.setattr(diagnostics, "files", lambda _package: tmp_path)
    active = diagnostics.environment_manifest(DaytonaSandboxSpec("fleet-rlm-python313-v13"))
    assert active.dependencies == ("numpy==9.9.9", "playwright==9.9.9")
    historical = diagnostics.environment_manifest(DaytonaSandboxSpec("fleet-rlm-python313-v11"))
    assert historical.dependencies == (
        "mpmath==1.4.1",
        "numpy==2.5.1",
        "pandas==3.0.5",
        "beautifulsoup4==4.15.0",
        "playwright==1.63.0",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["session", "semantic-child"])
@pytest.mark.parametrize("succeeds", [True, False])
async def test_runtime_probe_checks_profile_dependencies_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, profile: str, succeeds: bool
) -> None:
    spec = daytona_snapshot._spec("fleet-probe-v1", profile)
    operations: list[str] = []
    context = object()

    async def create_context():
        return context

    async def run_code(code, *, context):
        assert context is not None
        compile(code, "snapshot-probe", "exec")
        assert "dspy" not in code
        assert f"dependencies = {snapshot_dependency_import_names(spec.profile, spec=spec)!r}" in code
        assert "importlib.import_module(module)" in code
        assert "importlib.metadata.version(distribution) == version" in code
        if profile == "session":
            assert "chromium.launch(headless=True, chromium_sandbox=True" in code
            assert "page.set_content(" in code
            assert "browser_context.close()" in code
            assert "browser.close()" in code
            assert "Fleet delayed evidence" in code
            assert "Fleet second page evidence" in code
            assert "Fleet frame evidence" in code
            assert "for run_index in range(2)" in code
            assert "groups=groups" in code
            assert "owner['_groups_alive'](groups)" in code
        else:
            assert "playwright" not in code
        return SimpleNamespace(stdout="fleet-snapshot-runtime-ok" if succeeds else "", error=None)

    async def delete_context(received):
        assert received is context
        operations.append("delete-context")

    sandbox = SimpleNamespace(
        id="probe",
        code_interpreter=SimpleNamespace(
            create_context=create_context, run_code=run_code, delete_context=delete_context
        ),
    )

    async def create(**_kwargs):
        return sandbox

    async def delete(received):
        assert received is sandbox
        operations.append("delete-sandbox")

    async def get(sandbox_id):
        assert sandbox_id == "probe"
        operations.append("confirm-absence")
        return None

    platform = SimpleNamespace(create=create, delete=delete, get=get)
    monkeypatch.setattr(daytona_snapshot, "LiveDaytonaPlatform", lambda *_args: platform)
    if succeeds:
        await daytona_snapshot.verify_runtime(object(), spec)
    else:
        with pytest.raises(RuntimeError, match="snapshot runtime probe failed"):
            await daytona_snapshot.verify_runtime(object(), spec)
    assert operations == ["delete-context", "delete-sandbox", "confirm-absence"]


@pytest.mark.asyncio
async def test_check_requires_active_matching_snapshot() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")

    async def active(_name: str) -> object:
        return _snapshot(spec)

    client = SimpleNamespace(snapshot=SimpleNamespace(get=active))
    await daytona_snapshot.check_snapshot(client, spec)

    async def building(_name: str) -> object:
        return _snapshot(spec, state="building")

    with pytest.raises(RuntimeError, match="not active"):
        await daytona_snapshot.check_snapshot(
            SimpleNamespace(snapshot=SimpleNamespace(get=building)),
            spec,
        )


@pytest.mark.asyncio
async def test_check_rejects_snapshot_built_from_a_different_image() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    snapshot = _snapshot(spec, dockerfile="FROM python:3.13.13-slim-bookworm\n")

    async def get(_name: str) -> object:
        return snapshot

    with pytest.raises(RuntimeError, match="image metadata"):
        await daytona_snapshot.check_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get)), spec)


@pytest.mark.asyncio
async def test_check_rejects_snapshot_with_dependency_contract_drift() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    dockerfile = daytona_snapshot.build_snapshot_image(spec).dockerfile()
    snapshot = _snapshot(
        spec,
        dockerfile=dockerfile.replace(
            "FLEET_SNAPSHOT_DEPENDENCIES_SHA256=", "FLEET_SNAPSHOT_DEPENDENCIES_SHA256=wrong"
        ),
    )

    async def get(_name: str) -> object:
        return snapshot

    with pytest.raises(RuntimeError, match="image metadata"):
        await daytona_snapshot.check_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get)), spec)


@pytest.mark.asyncio
async def test_check_rejects_snapshot_with_resource_contract_drift() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    snapshot = _snapshot(spec)
    snapshot.cpu = 1

    async def get(_name: str) -> object:
        return snapshot

    with pytest.raises(RuntimeError, match="resources"):
        await daytona_snapshot.check_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get)), spec)


@pytest.mark.asyncio
async def test_create_is_idempotent_without_overwriting_existing_snapshot() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    create = pytest.fail

    async def get(_name: str) -> object:
        return _snapshot(spec)

    client = SimpleNamespace(
        snapshot=SimpleNamespace(get=get, create=create),
    )
    await daytona_snapshot.create_snapshot(client, spec)


@pytest.mark.asyncio
async def test_snapshot_sdk_skips_build_context_upload_for_fleet_image() -> None:
    """Published Fleet images preserve their original empty build context."""
    spec = DaytonaSandboxSpec("fleet-rlm-python313-v12")

    class ObjectStorageAPI:
        async def get_push_access(self) -> object:
            pytest.fail("empty Fleet image must not request object-storage credentials")

    assert (
        await AsyncSnapshotService.process_image_context(
            ObjectStorageAPI(), daytona_snapshot.build_snapshot_image(spec)
        )
        == []
    )


@pytest.mark.asyncio
async def test_snapshot_sdk_uploads_local_build_context_once_without_fleet_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise Daytona's uploader path separately from sandbox filesystem I/O."""
    image = daytona_snapshot.build_snapshot_image(DaytonaSandboxSpec("fleet-rlm-python313-v13"))
    calls: list[tuple[str, str, str]] = []

    class PushAccess:
        storage_url = "https://object-storage.invalid"
        access_key = "access"
        secret = "secret"
        session_token = "session"
        bucket = "bucket"
        region = "region"
        organization_id = "organization"

    class ObjectStorageAPI:
        async def get_push_access(self) -> PushAccess:
            return PushAccess()

    class Storage:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def upload(self, source_path: str, organization_id: str, archive_path: str) -> str:
            calls.append((source_path, organization_id, archive_path))
            return "uploaded-context-hash"

    monkeypatch.setattr(snapshot_module, "AsyncObjectStorage", Storage)
    hashes = await AsyncSnapshotService.process_image_context(ObjectStorageAPI(), image)

    assert hashes == ["uploaded-context-hash"] * 4
    assert len(calls) == 4
    assert all(organization == "organization" for _, organization, _ in calls)
    content = {archive: Path(source).read_text() for source, _, archive in calls}
    assert set(content) == {
        "snapshot-requirements.txt",
        "browser-requirements.txt",
        "browser_session.py",
        "runtime-manifest.json",
    }
    assert "mpmath" not in content["snapshot-requirements.txt"]
    assert content["browser-requirements.txt"] == "playwright==1.63.0\n"
    assert "numpy==2.5.1" in content["snapshot-requirements.txt"]
    assert "def start(" in content["browser_session.py"]
    assert '"snapshot":"fleet-rlm-python313-v13"' in content["runtime-manifest.json"]


@pytest.mark.asyncio
async def test_create_builds_with_expected_resources() -> None:
    spec = DaytonaSandboxSpec("fleet-test-v1")
    captured: dict[str, object] = {}

    async def get(_name: str) -> object:
        error = FileNotFoundError("missing")
        error.status_code = 404  # type: ignore[attr-defined]
        raise error

    async def create(params, *, on_logs):
        captured["params"] = params
        on_logs("internal build log")
        return _snapshot(spec)

    await daytona_snapshot.create_snapshot(SimpleNamespace(snapshot=SimpleNamespace(get=get, create=create)), spec)
    params = captured["params"]
    assert params.name == spec.snapshot
    assert (params.resources.cpu, params.resources.memory, params.resources.disk) == (4, 8, 8)

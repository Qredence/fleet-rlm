"""Skill validation, workspace synchronization, and selected child inputs."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.skills import BUNDLED_SKILLS_DIR, SANDBOX_SKILL_HELPERS_CODE, SkillManager, parse_and_validate_skill_md

# Skill validation


def test_skill_frontmatter_uses_yaml_and_preserves_colons_in_description() -> None:
    raw = """---
name: ledger-audit
description: "Audits ledgers: use when checking totals."
---

Check the total with Python.
"""

    metadata, body = parse_and_validate_skill_md("ledger-audit", raw)

    assert metadata == {
        "name": "ledger-audit",
        "description": "Audits ledgers: use when checking totals.",
    }
    assert body == "Check the total with Python."


def test_skill_frontmatter_rejects_folder_name_mismatch() -> None:
    raw = "---\nname: another-skill\ndescription: example\n---\n\nInstructions\n"

    with pytest.raises(ValueError, match="must match folder name"):
        parse_and_validate_skill_md("ledger-audit", raw)


def test_local_skill_helpers_support_progressive_disclosure_and_reject_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    helpers = {}
    source = SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})")
    exec(source, helpers)

    helpers["save_skill"](
        "ledger-audit",
        "Audit ledger totals when a reconciliation is requested.",
        "Read the source ledger and verify each total with Python.",
        scripts={"audit.py": "print('audit complete')\n"},
        references={"policy.md": "Amounts are stored in EUR.\n"},
    )
    assert helpers["list_skills"]() == [
        {"name": "ledger-audit", "description": "Audit ledger totals when a reconciliation is requested."}
    ]
    active_skill = helpers["use_skill"]("ledger-audit")
    assert "audit.py" in active_skill
    assert helpers["read_skill_reference"]("ledger-audit", "references/policy.md") == "Amounts are stored in EUR.\n"
    assert helpers["run_skill_script"]("ledger-audit", "scripts/audit.py") == "audit complete\n"

    outside = tmp_path / "outside.md"
    outside.write_text("not a Skill reference", encoding="utf-8")
    (root / "ledger-audit" / "references" / "escape.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        helpers["read_skill_reference"]("ledger-audit", "references/escape.md")


def test_local_skill_sync_prevalidates_all_skills_before_writing(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    valid = skills_root / "a-valid-skill"
    invalid = skills_root / "z-invalid-skill"
    valid.mkdir(parents=True)
    invalid.mkdir()
    (valid / "SKILL.md").write_text("---\nname: a-valid-skill\ndescription: valid\n---\n\nInstructions.\n")
    (invalid / "SKILL.md").write_text("missing frontmatter\n")

    class RecordingSandbox:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes]] = []

        def write_workspace_file(self, relative_path: str, content: bytes) -> None:
            self.writes.append((relative_path, content))

    sandbox = RecordingSandbox()
    manager = SkillManager(sandbox, skills_root, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="frontmatter"):
        manager.sync_local_skills_to_volume()
    assert sandbox.writes == []


def test_local_catalog_matches_the_synced_skill_metadata(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "ledger-audit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: ledger-audit\ndescription: "Audits ledger totals: on request."\n---\n\nCheck totals.\n',
        encoding="utf-8",
    )
    manager = SkillManager(object(), root, None)  # type: ignore[arg-type]

    assert manager.build_local_catalog_xml() == (
        "<available_skills>\n"
        '  <skill name="ledger-audit">Audits ledger totals: on request.</skill>\n'
        "</available_skills>"
    )


def test_local_skill_files_stage_bundled_and_local_resources_with_workspace_paths(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    local = tmp_path / "local"
    (bundled / "shared-skill").mkdir(parents=True)
    (bundled / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: Bundled copy.\n---\n\nBundled.\n", encoding="utf-8"
    )
    (local / "shared-skill" / "references").mkdir(parents=True)
    skill_md = b"---\nname: shared-skill\ndescription: Local override.\n---\n\nLocal.\n"
    ref_md = b"Reference policy.\n"
    (local / "shared-skill" / "SKILL.md").write_bytes(skill_md)
    (local / "shared-skill" / "references" / "policy.md").write_bytes(ref_md)

    manager = SkillManager(object(), local, bundled)  # type: ignore[arg-type]
    assert manager.local_skill_files() == {
        "skills/shared-skill/SKILL.md": skill_md,
        "skills/shared-skill/references/policy.md": ref_md,
    }


def test_stage_local_skills_returns_catalog_and_files_in_one_scan(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local"
    (local / "audit-skill").mkdir(parents=True)
    skill_md = b"---\nname: audit-skill\ndescription: Single-pass stage.\n---\n\nCheck.\n"
    (local / "audit-skill" / "SKILL.md").write_bytes(skill_md)
    manager = SkillManager(object(), local, None)  # type: ignore[arg-type]
    scans = 0
    original = SkillManager._stage_local_skills

    def counted(self, **kwargs):
        nonlocal scans
        scans += 1
        return original(self, **kwargs)

    monkeypatch.setattr(SkillManager, "_stage_local_skills", counted)
    catalog, staged_files = manager.stage_local_skills()

    assert scans == 1
    assert 'name="audit-skill"' in catalog
    assert staged_files == {"skills/audit-skill/SKILL.md": skill_md}


def test_bundled_skill_discovery_and_activation_do_not_install_packages(tmp_path: Path, monkeypatch) -> None:
    bundled_skills = (
        "delegation",
        "long-context-search",
        "repository-inspection",
        "skill-creator",
        "tabular-analysis",
    )
    root = tmp_path / "skills"
    shutil.copytree(BUNDLED_SKILLS_DIR, root)
    helpers: dict = {}
    exec(SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})"), helpers)

    def unexpected_process(*_args, **_kwargs):
        pytest.fail("Discovering or activating a Skill must not launch a process")

    monkeypatch.setattr(helpers["subprocess"], "run", unexpected_process)
    catalog = {item["name"] for item in helpers["list_skills"]()}
    assert catalog == set(bundled_skills)
    for name in bundled_skills:
        activated = helpers["use_skill"](name)
        assert f"# Skill: {name}" in activated


# Skill synchronization


def _skill(name: str, instructions: str = "Instructions.") -> bytes:
    return f"---\nname: {name}\ndescription: test skill\n---\n\n{instructions}\n".encode()


def test_required_capture_preserves_complete_bytes_overrides_and_does_not_reread(tmp_path):
    local, bundled = tmp_path / "local", tmp_path / "bundled"
    for root in (local, bundled):
        (root / "ledger-audit").mkdir(parents=True)
        (root / "ledger-audit" / "SKILL.md").write_bytes(_skill("ledger-audit", str(root)))
    raw = _skill("ledger-audit", "Count café € exactly.\r\n").replace(b"\n", b"\r\n")
    raw += b"x" * (20_000 - len(raw.decode("utf-8")))
    (local / "ledger-audit" / "SKILL.md").write_bytes(raw)
    (local / "ledger-audit" / "script.py").write_bytes(b"print(42)")
    volume = _VolumeDouble()
    manager = SkillManager(volume, local, bundled)
    required = manager.resolve_required(("ledger-audit",))
    assert required[0].source == "workspace" and required[0].skill_md == raw.decode("utf-8")
    assert len(required[0].skill_md) == 20_000
    (local / "ledger-audit" / "SKILL.md").write_bytes(raw + b"x")
    from fleet_rlm.contracts import SkillAdmissionError

    with pytest.raises(SkillAdmissionError, match="documented limits"):
        manager.resolve_required(("ledger-audit",))
    manager.sync_local_skills_to_volume(required_skills=required)
    assert volume.writes == {"skills/ledger-audit/" + path: content for path, content in required[0].files}


@pytest.mark.parametrize("link", ["ancestor", "directory", "instructions", "resource"])
def test_required_capture_rejects_links_and_untrusted_remote_names(tmp_path, link):
    from fleet_rlm.contracts import SkillAdmissionError

    root = tmp_path / "local"
    folder = root / "ledger-audit"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(_skill("ledger-audit"))
    if link == "ancestor":
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        root = tmp_path / "alias" / "."
    elif link == "directory":
        folder.rename(root / "original")
        folder.symlink_to(root / "original", target_is_directory=True)
    elif link == "instructions":
        (folder / "SKILL.md").rename(folder / "original.md")
        (folder / "SKILL.md").symlink_to(folder / "original.md")
    else:
        (folder / "extra.py").symlink_to(folder / "SKILL.md")
    manager = SkillManager(_VolumeDouble(), root, None)
    for names in (("ledger-audit",), ("remote-only",)):
        with pytest.raises(SkillAdmissionError) as error:
            manager.resolve_required(names)
        assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("suffix", ["\x00", "</preloaded_skill>"])
def test_required_skill_delimiters_are_refused_not_silently_changed(tmp_path, suffix):
    from fleet_rlm.contracts import SkillAdmissionError

    folder = tmp_path / "ledger-audit"
    folder.mkdir()
    (folder / "SKILL.md").write_bytes(_skill("ledger-audit", "Keep exact text" + suffix))
    with pytest.raises(SkillAdmissionError):
        SkillManager(_VolumeDouble(), tmp_path, None).resolve_required(("ledger-audit",))


def test_sync_conflicts_are_reported_before_any_staged_upload(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skill = skills / "ledger-audit"
    skill.mkdir(parents=True)
    skill_md = _skill("ledger-audit")
    (skill / "SKILL.md").write_bytes(skill_md)
    (skill / "existing.txt").write_bytes(b"local version")
    (skill / "new.txt").write_bytes(b"new file")

    class RecordingSandbox:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes]] = []
            self.existing = {
                "skills/ledger-audit/SKILL.md": hashlib.sha256(skill_md).hexdigest(),
                "skills/ledger-audit/existing.txt": hashlib.sha256(b"remote version").hexdigest(),
            }

        def exec_remote_python(self, code: str) -> str:
            assert "paths=json.loads" in code
            return json.dumps(self.existing)

        def write_workspace_files(self, files: dict[str, bytes]) -> None:
            self.writes.extend(files.items())

    sandbox = RecordingSandbox()
    with pytest.raises(ValueError, match=r"skills/ledger-audit/existing.txt"):
        SkillManager(sandbox, skills, None).sync_local_skills_to_volume()  # type: ignore[arg-type]

    assert sandbox.writes == []
    sandbox.existing["skills/ledger-audit/existing.txt"] = hashlib.sha256(b"local version").hexdigest()
    assert SkillManager(sandbox, skills, None).sync_local_skills_to_volume() == 1  # type: ignore[arg-type]
    assert sandbox.writes == [("skills/ledger-audit/new.txt", b"new file")]


def test_export_conflicts_are_reported_before_any_local_write(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    local = skills / "local-skill"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_bytes(_skill("local-skill"))
    (local / "existing.txt").write_bytes(b"local version")
    remote_files = [
        ("local-skill/SKILL.md", _skill("local-skill")),
        ("local-skill/existing.txt", b"remote version"),
        ("new-skill/SKILL.md", _skill("new-skill")),
    ]
    manifest = json.dumps([{"rel": path, "b64": base64.b64encode(content).decode()} for path, content in remote_files])

    class RecordingSandbox:
        def exec_remote_python(self, code: str) -> str:
            del code
            return manifest

    with pytest.raises(ValueError, match=r"local-skill/existing.txt"):
        SkillManager(RecordingSandbox(), skills).export_volume_skills_to_local()  # type: ignore[arg-type]

    assert not (skills / "new-skill").exists()
    assert (local / "existing.txt").read_bytes() == b"local version"
    (local / "existing.txt").unlink()
    assert SkillManager(RecordingSandbox(), skills).export_volume_skills_to_local() == ["local-skill", "new-skill"]  # type: ignore[arg-type]
    assert (local / "SKILL.md").read_bytes() == _skill("local-skill")
    assert (skills / "new-skill" / "SKILL.md").read_bytes() == _skill("new-skill")


class _VolumeDouble:
    def __init__(self, existing: dict[str, str] | None = None) -> None:
        self.existing = existing or {}
        self.writes: dict[str, bytes] = {}

    def exec_remote_python(self, _code: str) -> str:
        return json.dumps(self.existing)

    def write_workspace_files(self, files: dict[str, bytes]) -> None:
        self.batches = getattr(self, "batches", 0) + 1
        self.writes.update(files)


def test_bundled_skills_ship_valid_and_sync_with_local_overrides(tmp_path: Path) -> None:
    from fleet_rlm.skills import BUNDLED_SKILLS_DIR, parse_and_validate_skill_md

    bundled = sorted(path.parent.name for path in BUNDLED_SKILLS_DIR.glob("*/SKILL.md"))
    assert bundled == [
        "delegation",
        "long-context-search",
        "repository-inspection",
        "skill-creator",
        "tabular-analysis",
    ]
    for name in bundled:
        parse_and_validate_skill_md(name, (BUNDLED_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8"))

    local = tmp_path / "skills"
    (local / "delegation").mkdir(parents=True)
    override = _skill("delegation", "Local delegation rules.")
    (local / "delegation" / "SKILL.md").write_bytes(override)
    sandbox = _VolumeDouble()

    assert SkillManager(sandbox, local).sync_local_skills_to_volume() == 5  # type: ignore[arg-type]
    assert sorted(sandbox.writes) == [f"skills/{name}/SKILL.md" for name in bundled]
    assert sandbox.writes["skills/delegation/SKILL.md"] == override


def test_a_changed_bundled_skill_replaces_the_session_copy_but_user_edits_still_conflict(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    (bundled / "tabular-analysis").mkdir(parents=True)
    new_version = _skill("tabular-analysis", "Version 2.")
    (bundled / "tabular-analysis" / "SKILL.md").write_bytes(new_version)
    local = tmp_path / "local"
    (local / "mine").mkdir(parents=True)
    (local / "mine" / "SKILL.md").write_bytes(_skill("mine"))
    stale = hashlib.sha256(b"old").hexdigest()

    sandbox = _VolumeDouble({"skills/tabular-analysis/SKILL.md": stale})
    assert SkillManager(sandbox, local, bundled).sync_local_skills_to_volume() == 2  # type: ignore[arg-type]
    assert sandbox.writes["skills/tabular-analysis/SKILL.md"] == new_version

    sandbox = _VolumeDouble({"skills/mine/SKILL.md": stale})
    with pytest.raises(ValueError, match=r"skills/mine/SKILL\.md"):
        SkillManager(sandbox, local, bundled).sync_local_skills_to_volume()  # type: ignore[arg-type]
    assert sandbox.writes == {}


def test_skill_sync_uploads_every_new_file_in_one_batch() -> None:
    from fleet_rlm.skills import BUNDLED_SKILLS_DIR, SkillManager

    volume = _VolumeDouble()
    SkillManager(volume, None, BUNDLED_SKILLS_DIR).sync_local_skills_to_volume()  # type: ignore[arg-type]

    assert volume.batches == 1
    assert len(volume.writes) >= 3


# Selected child inputs


@pytest.fixture
def _batches_use_single_file_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests fake per-file transfers; route the batched child copy through those fakes."""
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_files",
        lambda self, paths, **_limits: {path: self.read_workspace_file(path) for path in paths},
    )
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_files",
        lambda self, files: [self.write_workspace_file(path, data) for path, data in files.items()] and None,
    )


def _manager() -> DaytonaSandboxManager:
    return DaytonaSandboxManager("key", "org", "volume", "session-a")


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_allocate_child_copies_only_selected_parent_files(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _manager()
    available = ["files/one.txt", "files/two.txt", "skills/review/SKILL.md", "artifacts/result.txt"]
    contents = {path: path.encode() for path in available}
    writes: list[tuple[str, bytes]] = []

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: contents[path])
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_file",
        lambda _self, path, data: writes.append((path, data)),
    )
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/two.txt", "skills/review/SKILL.md"])

    assert child.parent is parent
    assert writes == [
        ("files/two.txt", contents["files/two.txt"]),
        ("skills/review/SKILL.md", contents["skills/review/SKILL.md"]),
    ]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
@pytest.mark.parametrize(
    ("input_paths", "message"),
    [
        (["memory/notes.txt"], "beneath files/, skills/ or artifacts/"),
        (["files/missing.txt"], "unavailable"),
        (["files/one.txt"] * 101, "limit"),
    ],
)
def test_allocate_child_rejects_unapproved_inputs_before_provider_allocation(
    monkeypatch: pytest.MonkeyPatch, input_paths: list[str], message: str
) -> None:
    parent = _manager()
    allocations: list[DaytonaSandboxManager] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: ["files/one.txt"])
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda self: allocations.append(self))

    with pytest.raises(ValueError, match=message):
        parent.allocate_child(input_paths=input_paths)

    assert allocations == []
    assert parent._children == {}
    assert parent._allocations == 0


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_selected_artifacts_are_copied_and_unchanged_copies_are_not_promoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _manager()
    parent_files = {"files/sales.csv": b"csv", "artifacts/report.json": b'{"north": 1}'}
    child_files: dict[str, bytes] = {}
    promoted: list[str] = []

    def list_files(self: DaytonaSandboxManager) -> list[str]:
        return list(child_files if self.parent is not None else parent_files)

    def read(self: DaytonaSandboxManager, path: str, **_limits: Any) -> bytes:
        return (child_files if self.parent is not None else parent_files)[path]

    def write(self: DaytonaSandboxManager, path: str, data: bytes) -> None:
        if self.parent is not None:
            child_files[path] = data
        else:
            promoted.append(path)

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", list_files)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", read)
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", write)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_files",
        lambda self, files: [write(self, p, b) for p, b in files.items()],
    )
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "[]")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/sales.csv", "artifacts/report.json"])
    assert child_files == parent_files
    child_files["artifacts/counterfactual.json"] = b'{"central": 2}'

    results = child.collect_child_artifacts()

    assert [row["path"] for row in results] == [f"artifacts/{child.agent_id}/counterfactual.json"]
    assert promoted == [f"artifacts/{child.agent_id}/counterfactual.json"]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_explicit_children_always_get_skills_without_receipting_them_as_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _manager()
    available = ["files/sales.csv", "files/other.csv", "skills/tabular-analysis/SKILL.md"]
    writes: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: path.encode())
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", lambda _self, path, _data: writes.append(path))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/sales.csv"])

    assert writes == ["files/sales.csv", "skills/tabular-analysis/SKILL.md"]
    assert list(parent._child_receipts[child.agent_id]["inputs"]) == ["files/sales.csv"]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_allocate_child_default_still_copies_all_files_and_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _manager()
    available = ["files/one.txt", "skills/review/SKILL.md", "artifacts/result.txt"]
    writes: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: path.encode())
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", lambda _self, path, _data: writes.append(path))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    parent.allocate_child()

    assert writes == ["files/one.txt", "skills/review/SKILL.md"]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_factory_snapshots_inputs_and_omits_keyword_for_default_double(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.sandbox.manager as interpreter

    root = _manager()
    active = _manager()
    received: list[Any] = []

    def allocate_selected(_self: DaytonaSandboxManager, *, input_paths: list[str]) -> DaytonaSandboxManager:
        received.append(input_paths)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_selected)
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
        ),
    )
    captured = ["files/first.txt"]
    factory = root.interpreter_factory(input_paths=captured)
    captured.append("files/later.txt")
    token = interpreter._ACTIVE_MANAGER.set(active)
    try:
        result = factory()
    finally:
        interpreter._ACTIVE_MANAGER.reset(token)

    assert received == [["files/first.txt"]]
    assert result[0] is root
    assert result[2] is True
    assert result[3] == ("files/first.txt",)


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_factory_default_calls_allocate_child_without_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.sandbox.manager as interpreter

    root = _manager()
    active = _manager()
    calls: list[bool] = []

    def allocate_default(_self: DaytonaSandboxManager) -> DaytonaSandboxManager:
        calls.append(True)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_default)
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
        ),
    )
    factory = root.interpreter_factory()
    token = interpreter._ACTIVE_MANAGER.set(active)
    try:
        factory()
    finally:
        interpreter._ACTIVE_MANAGER.reset(token)

    assert calls == [True]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_factory_without_active_root_keeps_existing_manager(monkeypatch: pytest.MonkeyPatch) -> None:

    root = _manager()
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
        ),
    )

    result = root.interpreter_factory(input_paths=["files/input.txt"])()

    assert result[0] is root
    assert result[2] is False

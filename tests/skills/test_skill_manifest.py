"""Closed bundled Skill manifest parser contracts (expand phase)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.skills.catalog import (
    build_bundled_skill_catalog,
    load_bundled_skill_manifests,
    stable_skill_id,
)
from fleet_rlm.skills.manifest import parse_bundled_skill_manifest, parse_skill_manifest


def _document(
    *,
    name: str = "example-skill",
    description: str = "Use an example workflow.",
    compatibility: str = "Requires a Python interpreter.",
    version: str = "1.0.0",
    affordances: str = "  - workspace.files\n",
    allowed_tools: str = "read_attachment\n",
    resources: str = "resources: []\n",
    extra: str = "",
) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"compatibility: {compatibility}\n"
        "metadata:\n"
        f'  version: "{version}"\n'
        "  affordances:\n"
        f"{affordances}"
        f"allowed-tools: {allowed_tools}"
        f"{extra}"
        f"{resources}"
        "---\n\n"
        "# Example\n\nUse deterministic evidence.\n"
    )


@pytest.mark.parametrize(
    "document, message",
    (
        (_document(name=""), "name"),
        (_document(name="Bad Name"), "name"),
        (_document(description=""), "description"),
        (_document(version="bad version"), "version"),
        (_document(affordances="  - Bad.Affordance\n"), "affordances"),
        (_document(allowed_tools="\n  tool: read_attachment\n"), "allowed-tools"),
        (_document(extra="future-field: true\n"), "unknown Skill manifest fields"),
        (
            _document(
                resources=(
                    "resources:\n"
                    "  - path: references/one.md\n"
                    "    media_type: text/markdown\n"
                    "  - path: references/one.md\n"
                    "    media_type: text/markdown\n"
                )
            ),
            "duplicate",
        ),
        (
            _document(resources=("resources:\n  - path: ../outside.md\n    media_type: text/markdown\n")),
            "path",
        ),
        (_document(resources="resources: {}\n"), "resources must be a list"),
    ),
)
def test_manifest_parser_rejects_malformed_fields_and_resources(
    document: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_skill_manifest(document)


def test_bundle_parser_rejects_symlink_resource_bodies(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (root / "guide.md").symlink_to(outside)
    (root / "SKILL.md").write_text(
        _document(resources="resources:\n  - path: guide.md\n    media_type: text/markdown\n"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsafe resource path"):
        parse_bundled_skill_manifest(root)


def test_catalog_uses_one_manifest_authority_for_metadata_and_resources() -> None:
    catalog = build_bundled_skill_catalog()
    manifests = {manifest.name: manifest for manifest in load_bundled_skill_manifests()}

    for card in catalog.cards():
        manifest = manifests[card.name]
        assert card.id == stable_skill_id(card.name)
        assert card.description == manifest.description
        assert card.version == manifest.version
        assert card.affordances == manifest.affordances
        assert card.resources_available == bool(manifest.resources)
        definition = catalog.require(card.id)
        assert tuple(definition.resources) == tuple(resource.path for resource in manifest.resources)
        assert not definition.instructions.startswith("---")
        assert "allowed-tools:" not in definition.instructions


def test_custom_signature_binding_stays_code_owned_and_outside_the_manifest() -> None:
    from fleet_rlm.skills.signatures import DataAnalysisSignature

    manifest = parse_bundled_skill_manifest(Path("src/fleet_rlm/skills/bundled/data-analysis"))
    assert not hasattr(manifest, "signature")
    manifest_head = (
        (Path("src/fleet_rlm/skills/bundled/data-analysis") / "SKILL.md")
        .read_text(encoding="utf-8")
        .split("\n---\n", 1)[0]
    )
    assert "signature" not in manifest_head
    catalog = build_bundled_skill_catalog()
    assert catalog.require(stable_skill_id("data-analysis")).signature is DataAnalysisSignature


def test_bundle_parser_ignores_installer_bytecode_artifacts(tmp_path: Path) -> None:
    """pip-installed bundles gain __pycache__ pyc siblings that are not bundle content."""
    bundle = tmp_path / "bytecode-skill"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text(
        _document(resources="resources:\n  - path: scripts/tool.py\n    media_type: text/x-python\n"),
        encoding="utf-8",
    )
    scripts = bundle / "scripts"
    scripts.mkdir()
    (scripts / "tool.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    pycache = scripts / "__pycache__"
    pycache.mkdir()
    (pycache / "tool.cpython-312.pyc").write_bytes(b"\x00\x00\x00\x00\x00\x00\x00\x00")
    (pycache / "other.cpython-312.pyc").write_bytes(b"\x00\x00\x00\x00\x00\x00\x00\x00")

    manifest = parse_bundled_skill_manifest(bundle)
    assert [resource.path for resource in manifest.resources] == ["scripts/tool.py"]

    (bundle / "notes.md").write_text("undeclared", encoding="utf-8")
    with pytest.raises(ValueError, match="undeclared resource bodies"):
        parse_bundled_skill_manifest(bundle)

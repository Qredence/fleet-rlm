"""Tests for LLM profile store maintenance repairs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fleet_rlm.integrations.llm_profiles.crypto import encrypt_api_key
from fleet_rlm.integrations.llm_profiles.maintenance import repair_json_document


def test_repair_json_document_dedupes_imported_profiles_and_normalizes_google_models() -> None:
    document = {
        "profiles": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "name": "Imported from .env",
                "provider_type": "openai_compatible",
                "api_base": "https://litellm-proxy.example.com/v1",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "name": "Imported from .env",
                "provider_type": "openai_compatible",
                "api_base": "https://litellm-proxy.example.com/v1",
                "updated_at": "2026-06-11T03:00:00+00:00",
            },
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "name": "Gemini",
                "provider_type": "google",
                "api_base": "https://generativelanguage.googleapis.com/v1beta/openai/",
            },
        ],
        "role_bindings": [
            {
                "role": "planner",
                "profile_id": "11111111-1111-1111-1111-111111111111",
                "model_id": "gemini/gemini-3.1-pro-preview",
            },
            {
                "role": "delegate",
                "profile_id": "33333333-3333-3333-3333-333333333333",
                "model_id": "models/gemini-3.5-flash",
            },
            {
                "role": "delegate_small",
                "profile_id": "33333333-3333-3333-3333-333333333333",
                "model_id": "models/gemini-3.1-flash-lite",
            },
        ],
    }

    report = repair_json_document(document)

    assert report.deduped_profiles >= 1
    assert report.normalized_bindings >= 2
    assert report.planner_reassigned is True
    assert len(document["profiles"]) == 1

    bindings = {item["role"]: item for item in document["role_bindings"]}
    assert bindings["planner"]["profile_id"] == "33333333-3333-3333-3333-333333333333"
    assert bindings["planner"]["model_id"] == "gemini-3.1-pro-preview"
    assert bindings["delegate"]["model_id"] == "gemini-3.5-flash"
    assert bindings["delegate_small"]["model_id"] == "gemini-3.1-flash-lite"


@pytest.mark.asyncio
async def test_repair_local_llm_profiles_writes_document(tmp_path: Path) -> None:
    from fleet_rlm.integrations.llm_profiles.maintenance import repair_local_llm_profiles

    profiles_path = tmp_path / "llm-profiles.json"
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    profiles_path.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "id": "965d0fcb-cf23-47ee-a2ec-89821d78edb0",
                        "name": "Gemini",
                        "provider_type": "google",
                        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai/",
                        "api_key_ciphertext": encrypt_api_key("AIzaSyTestKeyForMaintenanceRepair"),
                        "metadata_json": {},
                        "created_at": "2026-06-11T03:31:00+00:00",
                        "updated_at": "2026-06-11T03:31:00+00:00",
                    }
                ],
                "role_bindings": [
                    {
                        "role": "delegate",
                        "profile_id": "965d0fcb-cf23-47ee-a2ec-89821d78edb0",
                        "model_id": "models/gemini-3.5-flash",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = await repair_local_llm_profiles(profiles_path=profiles_path, env_path=env_path)

    assert report.normalized_bindings == 1
    assert "DSPY_DELEGATE_LM_MODEL" in report.env_keys_updated
    saved = profiles_path.read_text(encoding="utf-8")
    assert "models/gemini-3.5-flash" not in saved
    assert "gemini-3.5-flash" in saved

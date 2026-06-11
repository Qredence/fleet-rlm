"""Unit tests for profile label resolution from bundles."""

from __future__ import annotations

from uuid import uuid4

from fleet_rlm.integrations.llm_profiles.resolver import profile_labels_from_bundle
from fleet_rlm.integrations.llm_profiles.types import (
    LlmProfileBundle,
    LlmProviderProfileRecord,
    LlmRoleBindingRecord,
)


def test_profile_labels_from_bundle_maps_role_to_profile_name() -> None:
    profile_id = uuid4()
    bundle = LlmProfileBundle(
        profiles=[
            LlmProviderProfileRecord(
                id=profile_id,
                name="LiteLLM Proxy",
                provider_type="openai_compatible",
                api_base="https://proxy.example/v1",
                api_key_ciphertext="cipher",
            )
        ],
        role_bindings=[
            LlmRoleBindingRecord(role="planner", profile_id=profile_id, model_id="gpt-4o"),
            LlmRoleBindingRecord(role="delegate", profile_id=profile_id, model_id="gpt-4o-mini"),
            LlmRoleBindingRecord(role="delegate_small", profile_id=None, model_id=""),
        ],
    )

    labels = profile_labels_from_bundle(bundle)

    assert labels["planner"] == (str(profile_id), "LiteLLM Proxy")
    assert labels["delegate"] == (str(profile_id), "LiteLLM Proxy")
    assert labels["delegate_small"] == (None, None)

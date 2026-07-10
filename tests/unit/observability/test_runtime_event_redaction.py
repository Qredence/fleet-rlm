from __future__ import annotations

from fleet_rlm.observability.redaction import REDACTED_VALUE, SAFE_RUNTIME_ERROR, redact_value, sanitize_runtime_event
from fleet_rlm.runtime.events import RuntimeEvent, RuntimeEventContext, RuntimeEventKind, RuntimeToolInfo


def test_sanitize_runtime_event_redacts_credentials_paths_and_error_details() -> None:
    event = RuntimeEvent(
        kind=RuntimeEventKind.ERROR,
        text="Provider failed at /home/daytona/memory/private.txt with Bearer top-secret-token",
        payload={
            "api_key": "sk-live-secret",
            "error": "provider said token=top-secret-token at /private/tmp/trace.log",
            "nested": {"password": "do-not-return", "safe": "kept"},
        },
        tool=RuntimeToolInfo(
            tool_name="repl_execute",
            tool_args={"path": "/Volumes/SSD-T7/private.py", "value": "safe"},
        ),
        context=RuntimeEventContext(workspace_path="/home/daytona/memory", repo_url="https://example.test/repo"),
    )

    safe = sanitize_runtime_event(event)

    rendered = safe.model_dump_json()
    assert "sk-live-secret" not in rendered
    assert "top-secret-token" not in rendered
    assert "/home/daytona/memory" not in rendered
    assert "/Volumes/SSD-T7" not in rendered
    assert safe.payload["api_key"] == REDACTED_VALUE
    assert safe.payload["error"] == "Runtime operation failed."
    assert safe.payload["nested"]["password"] == REDACTED_VALUE
    assert safe.payload["nested"]["safe"] == "kept"
    assert safe.tool is not None
    assert safe.tool.tool_args["path"] == REDACTED_VALUE
    assert safe.context is not None
    assert safe.context.workspace_path == REDACTED_VALUE


def test_sanitize_runtime_event_replaces_untrusted_error_text_and_camel_case_secrets() -> None:
    raw_error = "provider accessToken=top-secret failed at /etc/fleet-rlm/provider.conf"
    event = RuntimeEvent(
        kind=RuntimeEventKind.ERROR,
        text=raw_error,
        payload={
            "clientSecret": "client-secret-value",
            "nested": {"accessToken": "nested-token"},
        },
        tool=RuntimeToolInfo(
            tool_name="repl_execute",
            tool_output="stderr: /etc/fleet-rlm/provider.conf accessToken=tool-token",
        ),
    )

    safe = sanitize_runtime_event(event)

    rendered = safe.model_dump_json()
    assert safe.text == SAFE_RUNTIME_ERROR
    assert safe.payload["clientSecret"] == REDACTED_VALUE
    assert safe.payload["nested"]["accessToken"] == REDACTED_VALUE
    assert "top-secret" not in rendered
    assert "client-secret-value" not in rendered
    assert "nested-token" not in rendered
    assert "tool-token" not in rendered
    assert "/etc/fleet-rlm/provider.conf" not in rendered


def test_redact_value_redacts_compound_secret_keys_without_known_value_prefixes() -> None:
    safe = redact_value(
        {
            "api_key": "AIzaSyArbitraryProviderCredential",
            "private_key": "-----BEGIN PRIVATE KEY-----not-public-----END PRIVATE KEY-----",
        }
    )

    assert safe == {
        "api_key": REDACTED_VALUE,
        "private_key": REDACTED_VALUE,
    }


def test_redact_value_preserves_structured_error_flags_codes_and_categories() -> None:
    safe = redact_value(
        {
            "adapter_parse_error": True,
            "error_code": "invalid_adapter_payload",
            "runtime_failure_category": "adapter_parse_error",
        }
    )

    assert safe == {
        "adapter_parse_error": True,
        "error_code": "invalid_adapter_payload",
        "runtime_failure_category": "adapter_parse_error",
    }

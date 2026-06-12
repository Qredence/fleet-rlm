from __future__ import annotations

import importlib

import pytest
from pydantic import ValidationError

ROUNDTRIP_CASES = [
    ("fleet_rlm.api.schemas.base", "HealthResponse", {}),
    (
        "fleet_rlm.api.schemas.base",
        "ReadyResponse",
        {
            "ready": True,
            "planner": "ready",
            "database": "ready",
            "database_required": True,
            "sandbox_provider": "daytona",
        },
    ),
    (
        "fleet_rlm.api.schemas.base",
        "ApiErrorResponse",
        {"code": "bad_request", "message": "Bad request", "detail": {"field": "value"}},
    ),
    (
        "fleet_rlm.api.schemas.base",
        "AuthMeResponse",
        {
            "tenant_claim": "tenant-a",
            "user_claim": "user-a",
            "email": "alice@example.com",
            "name": "Alice",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
        },
    ),
    (
        "fleet_rlm.api.schemas.base",
        "ServiceInfoResponse",
        {
            "app_env": "local",
            "auth_mode": "dev",
            "auth_required": False,
            "sandbox_provider": "daytona",
            "database_enabled": False,
            "serve_ui": True,
            "expose_docs": True,
            "agent_model": "openai/gpt-4o",
            "rlm_max_depth": 2,
            "rlm_max_iterations": 4,
        },
    ),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeSettingsField",
        {
            "key": "APP_ENV",
            "label": "App Env",
            "description": "Environment label",
            "value": "local",
        },
    ),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeSettingsCategory",
        {
            "id": "general",
            "label": "General",
            "description": "General settings",
            "fields": [
                {
                    "key": "APP_ENV",
                    "label": "App Env",
                    "description": "Environment label",
                    "value": "local",
                }
            ],
        },
    ),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeSettingsSnapshot",
        {
            "env_path": "/workspace/.env",
            "categories": [
                {
                    "id": "general",
                    "label": "General",
                    "description": "General settings",
                    "fields": [
                        {
                            "key": "APP_ENV",
                            "label": "App Env",
                            "description": "Environment label",
                            "value": "local",
                        }
                    ],
                }
            ],
        },
    ),
    ("fleet_rlm.api.schemas.runtime", "RuntimeSettingsUpdateRequest", {"updates": {"APP_ENV": "local"}}),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeSettingsUpdateResponse",
        {"updated": ["APP_ENV"], "env_path": "/workspace/.env"},
    ),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeConnectivityTestResponse",
        {
            "kind": "lm",
            "ok": True,
            "preflight_ok": True,
            "checked_at": "2024-01-01T00:00:00Z",
            "checks": {"planner": True},
            "guidance": ["Looks good"],
            "latency_ms": 12,
            "output_preview": "pong",
        },
    ),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeTestCache",
        {
            "lm": {"kind": "lm", "ok": True, "preflight_ok": True, "checked_at": "2024-01-01T00:00:00Z"},
            "daytona": {
                "kind": "daytona",
                "ok": True,
                "preflight_ok": True,
                "checked_at": "2024-01-01T00:00:00Z",
            },
        },
    ),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeActiveModels",
        {
            "planner": "openai/gpt-4o",
            "delegate": "anthropic/claude-3-5-sonnet",
            "delegate_small": "openai/gpt-4o-mini",
        },
    ),
    (
        "fleet_rlm.api.schemas.runtime",
        "RuntimeStatusResponse",
        {
            "app_env": "local",
            "write_enabled": True,
            "ready": True,
            "active_models": {
                "planner": "openai/gpt-4o",
                "delegate": "anthropic/claude-3-5-sonnet",
                "delegate_small": "openai/gpt-4o-mini",
            },
            "sandbox_provider": "daytona",
            "llm": {"ready": True},
            "mlflow": {"enabled": False},
            "daytona": {"configured": True},
            "tests": {
                "lm": {"kind": "lm", "ok": True, "preflight_ok": True, "checked_at": "2024-01-01T00:00:00Z"},
                "daytona": {
                    "kind": "daytona",
                    "ok": True,
                    "preflight_ok": True,
                    "checked_at": "2024-01-01T00:00:00Z",
                },
            },
            "guidance": ["All services ready"],
        },
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionStateSummary",
        {"key": "owner:abc:__default__", "workspace_id": "workspace-a", "user_id": "user-a"},
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionStateResponse",
        {"sessions": [{"key": "owner:abc:__default__", "workspace_id": "workspace-a", "user_id": "user-a"}]},
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionListItem",
        {
            "id": "session-1",
            "title": "Chat",
            "status": "active",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        },
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionListResponse",
        {
            "items": [
                {
                    "id": "session-1",
                    "title": "Chat",
                    "status": "active",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                }
            ],
            "total": 1,
            "offset": 0,
            "limit": 20,
            "has_more": False,
        },
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionDetailResponse",
        {
            "id": "session-1",
            "title": "Chat",
            "status": "active",
            "turn_count": 2,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        },
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "TurnItem",
        {
            "id": "turn-1",
            "turn_index": 0,
            "user_message": "Hello",
            "assistant_message": "Hi",
            "created_at": "2024-01-01T00:00:00Z",
        },
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "TurnListResponse",
        {
            "items": [
                {
                    "id": "turn-1",
                    "turn_index": 0,
                    "user_message": "Hello",
                    "assistant_message": "Hi",
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ],
            "total": 1,
            "offset": 0,
            "limit": 50,
            "has_more": False,
        },
    ),
    ("fleet_rlm.api.schemas.sessions", "SessionDeleteResponse", {}),
    ("fleet_rlm.api.schemas.sessions", "SessionRestoreResponse", {}),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionPatchRequest",
        {"title": "Renamed", "metadata_json": {"topic": "rlm"}},
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionStatsResponse",
        {
            "total_tokens_in": 10,
            "total_tokens_out": 20,
            "total_latency_ms": 30,
            "model_breakdown": {"gpt-4o": 1},
        },
    ),
    ("fleet_rlm.api.schemas.sessions", "SessionExportRequest", {"module_slug": "longcot-reasoner"}),
    ("fleet_rlm.api.schemas.sessions", "SessionTraceExportRequest", {"format": "both"}),
    (
        "fleet_rlm.api.schemas.sessions",
        "SessionTraceExportResponse",
        {
            "session_id": "session-1",
            "trace_count": 1,
            "jsonl_path": "artifacts/traces/session-1.jsonl",
            "distilled_bundle_path": "artifacts/traces/session-1.distilled.jsonl",
        },
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "TranscriptTurnInput",
        {"user_message": "Hello", "assistant_message": "Hi"},
    ),
    (
        "fleet_rlm.api.schemas.sessions",
        "TranscriptDatasetRequest",
        {
            "module_slug": "longcot-reasoner",
            "title": "Transcript",
            "turns": [{"user_message": "Hello", "assistant_message": "Hi"}],
        },
    ),
    (
        "fleet_rlm.api.schemas.feedback",
        "TraceFeedbackRequest",
        {"trace_id": "trace-1", "is_correct": True, "comment": "Looks good"},
    ),
    (
        "fleet_rlm.api.schemas.feedback",
        "TraceFeedbackResponse",
        {"trace_id": "trace-1", "client_request_id": "request-1"},
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "GEPAOptimizationRequest",
        {
            "dataset_id": "dataset-1",
            "program_spec": "package.module:program",
            "reflection_profile_id": "profile-1",
            "reflection_model_id": "model-1",
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "GEPAOptimizationResponse",
        {
            "program_spec": "package.module:program",
            "train_examples": 10,
            "validation_examples": 2,
            "validation_score": 0.95,
            "output_path": "optimized/program.py",
            "reflection_profile_id": "profile-1",
            "reflection_model_id": "model-1",
            "distilled_trace_bundle_path": "traces/distilled.jsonl",
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "GEPAModuleInfo",
        {
            "slug": "longcot-reasoner",
            "label": "Long CoT",
            "program_spec": "package.module:program",
            "required_dataset_keys": ["question", "answer"],
            "input_keys": ["question"],
            "output_keys": ["reasoning", "answer"],
            "runtime_module_name": "longcot",
            "signature_class_name": "LongCoTQASignature",
            "optimization_target_kind": "runtime-signature",
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "GEPAStatusResponse",
        {"available": True, "mlflow_enabled": True, "gepa_installed": True},
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "OptimizationRunResponse",
        {
            "id": "run-1",
            "status": "running",
            "program_spec": "package.module:program",
            "optimizer": "GEPA",
            "started_at": "2024-01-01T00:00:00Z",
            "reflection_profile_id": "profile-1",
            "reflection_model_id": "model-1",
            "distilled_trace_bundle_path": "traces/distilled.jsonl",
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "OptimizationRunDetailResponse",
        {
            "run": {
                "id": "run-1",
                "status": "completed",
                "program_spec": "skill:optimization",
                "optimizer": "gepa",
                "started_at": "2026-06-11T10:00:00Z",
            },
            "manifest_available": True,
            "manifest": {"optimizer": "GEPA"},
            "review_bundle": {"feedback_summary": "ok"},
            "artifact_refs": [
                {"label": "Manifest", "path": "optimized.manifest.json", "kind": "manifest", "exists": True}
            ],
            "score_summary": {"train_examples": 1, "validation_examples": 0},
            "prompt_diffs": [
                {
                    "predictor_name": "skill",
                    "before_prompt": "before",
                    "after_prompt": "after",
                    "changed": True,
                }
            ],
            "trace_evidence": [
                {
                    "kind": "trace_evidence",
                    "trace_id": "tr-1",
                    "failure_categories": ["bad_tool_use"],
                    "prompt_change_recommendations": ["Clarify tool use."],
                }
            ],
            "candidate_decisions": [
                {"candidate_id": "selected", "status": "selected", "summary": "Selected prompt"}
            ],
            "insights": {
                "selected_outcome": "changed",
                "summary": "GEPA selected a prompt change.",
                "next_step": "Review draft.",
            },
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "OptimizationPromotionDraftResponse",
        {
            "draft_id": "promotion-draft-run-1",
            "run_id": "run-1",
            "target": "skill:optimization",
            "summary": "Draft only",
            "draft_path": "promotion-drafts/run-1.json",
            "created_at": "2026-06-11T10:00:00Z",
        },
    ),
    ("fleet_rlm.api.schemas.optimization", "OptimizationRunCreatedResponse", {"run_id": "run-1"}),
    (
        "fleet_rlm.api.schemas.optimization",
        "DatasetResponse",
        {
            "id": "dataset-1",
            "name": "Training Set",
            "row_count": 4,
            "format": "jsonl",
            "created_at": "2024-01-01T00:00:00Z",
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "DatasetListResponse",
        {
            "items": [
                {
                    "id": "dataset-1",
                    "name": "Training Set",
                    "row_count": 4,
                    "format": "jsonl",
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ],
            "total": 1,
            "offset": 0,
            "limit": 50,
            "has_more": False,
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "DatasetDetailResponse",
        {
            "id": "dataset-1",
            "name": "Training Set",
            "row_count": 4,
            "format": "jsonl",
            "created_at": "2024-01-01T00:00:00Z",
            "sample_rows": [{"question": "Q", "answer": "A"}],
            "uri": "datasets/train.jsonl",
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "EvaluationResultItem",
        {"id": "result-1", "example_index": 0, "input_data": '{"question": "Q"}', "score": 0.9},
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "EvaluationResultsResponse",
        {
            "items": [{"id": "result-1", "example_index": 0, "input_data": '{"question": "Q"}', "score": 0.9}],
            "total": 1,
            "offset": 0,
            "limit": 50,
            "has_more": False,
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "PromptSnapshotItem",
        {"predictor_name": "planner", "prompt_type": "before", "prompt_text": "Think step by step"},
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "RunComparisonItem",
        {
            "run_id": "run-1",
            "program_spec": "package.module:program",
            "prompt_snapshots": [
                {"predictor_name": "planner", "prompt_type": "before", "prompt_text": "Think step by step"}
            ],
        },
    ),
    (
        "fleet_rlm.api.schemas.optimization",
        "RunComparisonResponse",
        {
            "runs": [
                {
                    "run_id": "run-1",
                    "program_spec": "package.module:program",
                    "prompt_snapshots": [
                        {
                            "predictor_name": "planner",
                            "prompt_type": "before",
                            "prompt_text": "Think step by step",
                        }
                    ],
                }
            ]
        },
    ),
    (
        "fleet_rlm.api.schemas.sandbox",
        "SandboxListItem",
        {"id": "sandbox-1", "name": "Sandbox", "state": "started"},
    ),
    (
        "fleet_rlm.api.schemas.sandbox",
        "SandboxDetailResponse",
        {"id": "sandbox-1", "name": "Sandbox", "state": "started"},
    ),
    (
        "fleet_rlm.api.schemas.sandbox",
        "SandboxListResponse",
        {"items": [{"id": "sandbox-1", "name": "Sandbox", "state": "started"}], "total": 1},
    ),
    ("fleet_rlm.api.schemas.sandbox", "SandboxArchiveResponse", {}),
    (
        "fleet_rlm.api.schemas.sandbox",
        "RunStepItem",
        {
            "id": "step-1",
            "step_index": 0,
            "step_type": "tool_call",
            "created_at": "2024-01-01T00:00:00Z",
        },
    ),
    (
        "fleet_rlm.api.schemas.sandbox",
        "RunStepListResponse",
        {
            "items": [
                {
                    "id": "step-1",
                    "step_index": 0,
                    "step_type": "tool_call",
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ],
            "total": 1,
            "offset": 0,
            "limit": 50,
            "has_more": False,
        },
    ),
    (
        "fleet_rlm.api.schemas.volumes",
        "VolumeTreeNode",
        {
            "id": "root",
            "name": "root",
            "path": "/",
            "type": "volume",
            "children": [{"id": "file-1", "name": "README.md", "path": "/README.md", "type": "file"}],
        },
    ),
    (
        "fleet_rlm.api.schemas.volumes",
        "VolumeTreeResponse",
        {
            "provider": "daytona",
            "volume_name": "rlm-volume",
            "root_path": "/",
            "nodes": [
                {
                    "id": "root",
                    "name": "root",
                    "path": "/",
                    "type": "volume",
                    "children": [{"id": "file-1", "name": "README.md", "path": "/README.md", "type": "file"}],
                }
            ],
            "max_depth": 3,
            "max_entries": 100,
            "entries_returned": 2,
        },
    ),
    (
        "fleet_rlm.api.schemas.volumes",
        "VolumeFileContentResponse",
        {"provider": "daytona", "path": "/README.md", "mime": "text/plain", "size": 3, "content": "abc"},
    ),
    ("fleet_rlm.api.schemas.volumes", "VolumeListItem", {"id": "volume-1", "name": "volume-1"}),
    (
        "fleet_rlm.api.schemas.volumes",
        "VolumeListResponse",
        {"provider": "daytona", "volumes": [{"id": "volume-1", "name": "volume-1"}]},
    ),
    (
        "fleet_rlm.api.schemas.websocket",
        "WSMessage",
        {
            "type": "message",
            "content": "Inspect this repository",
            "trace": True,
            "trace_mode": "compact",
            "execution_mode": "auto",
            "repo_url": "https://github.com/qredence/fleet-rlm",
            "repo_ref": "main",
            "context_paths": ["src/fleet_rlm/api"],
            "batch_concurrency": 2,
            "session_id": "session-1",
        },
    ),
    (
        "fleet_rlm.api.schemas.websocket",
        "WSCommandMessage",
        {"command": "reset", "args": {"force": True}, "session_id": "session-1"},
    ),
    (
        "fleet_rlm.api.schemas.websocket",
        "WSCommandResult",
        {"command": "reset", "result": {"ok": True}},
    ),
]


INVALID_WS_MESSAGES = [
    ({"content": "hello"}, "explicit canonical type"),
    ({"type": "message", "content": "   "}, "require non-empty content"),
    ({"type": "message", "content": "hello", "workspace_id": "ws-1"}, "identity is derived from auth"),
    ({"type": "message", "content": "hello", "repo_ref": "main"}, "repo_ref requires repo_url"),
    ({"type": "message", "content": "hello", "max_depth": 3}, "no longer accept max_depth"),
]


@pytest.mark.parametrize(("module_path", "model_name", "payload"), ROUNDTRIP_CASES)
def test_schema_models_roundtrip(module_path, model_name, payload):
    module = importlib.import_module(module_path)
    model = getattr(module, model_name)

    validated = model.model_validate(payload)
    roundtripped = model.model_validate_json(validated.model_dump_json())

    assert roundtripped.model_dump(mode="json") == validated.model_dump(mode="json")


def test_ws_message_accepts_canonical_message_payload():
    websocket_module = importlib.import_module("fleet_rlm.api.schemas.websocket")

    message = websocket_module.WSMessage.model_validate(
        {
            "type": "message",
            "content": "Inspect this repository",
            "repo_url": "https://github.com/qredence/fleet-rlm",
            "repo_ref": "main",
            "trace_mode": "verbose",
            "context_paths": ["src/fleet_rlm/api"],
            "batch_concurrency": 2,
            "session_id": "session-1",
        }
    )

    assert message.type == "message"
    assert message.repo_ref == "main"
    assert message.context_paths == ["src/fleet_rlm/api"]
    assert message.batch_concurrency == 2


@pytest.mark.parametrize(("payload", "message_fragment"), INVALID_WS_MESSAGES)
def test_ws_message_rejects_invalid_payloads(payload, message_fragment):
    websocket_module = importlib.import_module("fleet_rlm.api.schemas.websocket")

    with pytest.raises(ValidationError, match=message_fragment):
        websocket_module.WSMessage.model_validate(payload)


def test_gepa_optimization_request_rejects_miprov2() -> None:
    optimization_module = importlib.import_module("fleet_rlm.api.schemas.optimization")

    with pytest.raises(ValidationError):
        optimization_module.GEPAOptimizationRequest.model_validate(
            {
                "dataset_id": "dataset-1",
                "program_spec": "package.module:program",
                "optimizer": "miprov2",
            }
        )


def test_gepa_optimization_request_accepts_skill_target() -> None:
    optimization_module = importlib.import_module("fleet_rlm.api.schemas.optimization")

    request = optimization_module.GEPAOptimizationRequest.model_validate(
        {
            "dataset_id": "dataset-1",
            "skill_name": "optimization",
            "trace_bundle_paths": ["traces/bundle.jsonl"],
        }
    )

    assert request.optimizer == "gepa"
    assert request.skill_name == "optimization"
    assert request.trace_bundle_paths == ["traces/bundle.jsonl"]

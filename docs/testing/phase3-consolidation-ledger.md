# Phase 3 test consolidation ledger

## Scope and baseline

Test organization only: preserve runtime behavior, live entry points, receipt schemas,
public APIs, durable formats, and the 75% coverage floor. No compatibility shim is needed.
The baseline below was captured before any test move on 2026-09-10.
Collection: 3027 cases in 299 collected files; 299 source test files.

## Pre-move disposition

Each source row assigns every collected case in that file to one primary owner.
Moves retain every scenario and parametrization unless explicitly reconciled below.
Distinct transport, persistence, concurrency, and live boundaries remain separate.

| Source (relative to repository root) | Cases | Primary behavior owner | Destination / disposition |
| --- | ---: | --- | --- |
| `tests/contracts/backend/test_ai_sdk_ui_stream.py` | 8 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_ai_sdk_ui_turn_contract.py` | 4 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_artifacts_api.py` | 2 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_coordinator_runner_failures.py` | 6 | Run lifecycle / settlement | Retain |
| `tests/contracts/backend/test_daytona_import_boundary.py` | 5 | packaging / configuration | Retain |
| `tests/contracts/backend/test_dspy_rlm_constructor.py` | 7 | RLM program / budget / output | Retain |
| `tests/contracts/backend/test_fastapi_sse_response.py` | 11 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_files_api.py` | 2 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_health_api.py` | 6 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_host_tool_submit_binding.py` | 5 | RLM program / budget / output | Retain |
| `tests/contracts/backend/test_local_byok_scope.py` | 2 | packaging / configuration | Retain |
| `tests/contracts/backend/test_mlflow_feedback_api.py` | 4 | observability privacy / lifecycle | Retain |
| `tests/contracts/backend/test_mlflow_lifespan.py` | 3 | observability privacy / lifecycle | Retain |
| `tests/contracts/backend/test_model_facing_tool_contract.py` | 2 | RLM program / budget / output | Retain |
| `tests/contracts/backend/test_native_dspy_fastapi_vertical_slice.py` | 1 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_native_multi_turn_long_context.py` | 1 | Run lifecycle / settlement | Retain |
| `tests/contracts/backend/test_native_rlm_tracer.py` | 7 | observability privacy / lifecycle | Retain |
| `tests/contracts/backend/test_result_snapshot_commit.py` | 5 | Run lifecycle / settlement | Retain |
| `tests/contracts/backend/test_rlm_history_retrieval.py` | 2 | RLM program / budget / output | Retain |
| `tests/contracts/backend/test_run_cancellation_api.py` | 2 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_runtime_profiles.py` | 2 | packaging / configuration | Retain |
| `tests/contracts/backend/test_sessions_api.py` | 4 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_settings_api.py` | 3 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_skill_turn_contract.py` | 7 | Workspace files / memory | Retain |
| `tests/contracts/backend/test_skills_api.py` | 3 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_sse_projection.py` | 4 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_stream_contract_parity.py` | 3 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_stream_fixture.py` | 2 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_turn_claim_adapter_parity.py` | 8 | persistence / migrations | Retain |
| `tests/contracts/backend/test_turn_preparation_diagnostics.py` | 7 | observability privacy / lifecycle | Retain |
| `tests/contracts/backend/test_turn_skill_selections_api.py` | 7 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_typed_task_contract.py` | 3 | RLM program / budget / output | Retain |
| `tests/contracts/backend/test_ui_message_part_schema.py` | 20 | packaging / configuration | Retain |
| `tests/contracts/backend/test_workspace_files_api.py` | 10 | API / SSE public contracts | Retain |
| `tests/contracts/backend/test_workspace_turn_flow.py` | 10 | Workspace files / memory | Retain |
| `tests/e2e/test_cli_smoke.py` | 2 | packaging / configuration | Retain |
| `tests/freeze/test_failure_taxonomy_golden.py` | 2 | RLM program / budget / output | Retain |
| `tests/freeze/test_public_stream_gate.py` | 7 | API / SSE public contracts | Retain |
| `tests/freeze/test_reasoning_trajectory_equality.py` | 2 | RLM program / budget / output | Retain |
| `tests/live/backend/test_attachment_artifact_durability.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_daytona_cancel_during_execution.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_daytona_containment.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_daytona_deadline_cleanup.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_daytona_deletion_lifecycle.py` | 2 | live provider certification | Retain |
| `tests/live/backend/test_daytona_recursive_batch.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_fleet_rlm_daytona_mvp.py` | 2 | live provider certification | Retain |
| `tests/live/backend/test_lakebase_engine_resilience.py` | 4 | live provider certification | Retain |
| `tests/live/backend/test_memory_candidate_live.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_memory_semantics_live.py` | 6 | live provider certification | Retain |
| `tests/live/backend/test_phase1_daytona_stream.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_phase2_daytona_recursive.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_postgres_contention.py` | 6 | live provider certification | Retain |
| `tests/live/backend/test_postgres_query_plans.py` | 5 | live provider certification | Retain |
| `tests/live/backend/test_safe_gepa_daytona_policy.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_url_cache_durability.py` | 1 | live provider certification | Retain |
| `tests/live/backend/test_workspace_memory_shared_volume.py` | 2 | live provider certification | Retain |
| `tests/unit/backend/chat/test_capability_preparation_drain.py` | 1 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/chat/test_memory_candidate_promotion.py` | 9 | Workspace files / memory | Retain |
| `tests/unit/backend/chat/test_session_context.py` | 1 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/chat/test_turn_coordinator_execution.py` | 8 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/chat/test_turn_coordinator_stream.py` | 6 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/chat/test_turn_runtime.py` | 3 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/daytona/test_broker.py` | 12 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_child_deletion_absence.py` | 8 | Daytona resource lifecycle / containment | Move to `tests/unit/backend/daytona/test_deletion_lifecycle.py` |
| `tests/unit/backend/daytona/test_child_lease_cleanup_ownership.py` | 17 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_deletion_lifecycle.py` | 20 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_interpreter_callback_shadow.py` | 13 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_interpreter_observation.py` | 14 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_interpreter_output_cap.py` | 8 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_interpreter_tracing.py` | 8 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/daytona/test_memory_candidate_promotion_flow.py` | 2 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_memory_candidate_wiring.py` | 2 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_missing_import_observations.py` | 8 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_native_sdk_contract.py` | 3 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_optimization_evaluator.py` | 10 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_recursion_volume_preservation.py` | 3 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_recursive_child_late.py` | 1 | Daytona resource lifecycle / containment | Move to `tests/unit/backend/daytona/test_child_lease_cleanup_ownership.py` |
| `tests/unit/backend/daytona/test_recursive_child_runtime.py` | 21 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_run_environment_cleanup.py` | 1 | Daytona resource lifecycle / containment | Move to `tests/unit/backend/daytona/test_daytona_session_lifecycle.py` |
| `tests/unit/backend/daytona/test_run_environment_history.py` | 5 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_run_environment_root_lease.py` | 9 | Daytona resource lifecycle / containment | Move to `tests/unit/backend/daytona/test_daytona_session_lifecycle.py` |
| `tests/unit/backend/daytona/test_runtime.py` | 15 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_sandbox_lease.py` | 14 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_sandbox_spec.py` | 7 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_sdk_resource_errors.py` | 11 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_stale_binding_recovery.py` | 5 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_sync_bridge_dispatcher.py` | 5 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_sync_bridge_loop.py` | 10 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_tool_executor_kwargs.py` | 3 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_volume_layout_parallel.py` | 5 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_warm_pool.py` | 26 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/daytona/test_workspace_agent_delete_patch.py` | 24 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_agent_install.py` | 11 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_agent_protocol.py` | 7 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_agent_runtime.py` | 4 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_agent_stat.py` | 7 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_agent_timeout.py` | 6 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_fs_cache.py` | 9 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_gateway.py` | 6 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_gateway_concurrency.py` | 6 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_memory.py` | 67 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_memory_concurrency.py` | 2 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_memory_diagnostics.py` | 14 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/daytona/test_workspace_sdk_parity.py` | 2 | Workspace files / memory | Retain |
| `tests/unit/backend/daytona/test_workspace_volume_call_counts.py` | 3 | Workspace files / memory | Retain |
| `tests/unit/backend/packaging/test_artifact_matrix.py` | 10 | packaging / configuration | Retain |
| `tests/unit/backend/packaging/test_dependency_provenance.py` | 12 | packaging / configuration | Retain |
| `tests/unit/backend/packaging/test_exact_version_runtime_guard.py` | 43 | packaging / configuration | Retain |
| `tests/unit/backend/packaging/test_install_matrix.py` | 16 | packaging / configuration | Retain |
| `tests/unit/backend/packaging/test_official_optimizer_surface.py` | 3 | packaging / configuration | Retain |
| `tests/unit/backend/rlm/test_adapter_budget_integration.py` | 40 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_dspy_compat_interpreter_contract.py` | 6 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_dspy_compat_seam.py` | 7 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_error_taxonomy_repair.py` | 12 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_events_execution_trace.py` | 7 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_events_observation.py` | 1 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_events_tool_observer.py` | 13 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_events_trajectory_projection.py` | 12 | Workspace files / memory | Retain |
| `tests/unit/backend/rlm/test_fleet_json_adapter.py` | 29 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_memory_candidate_transport.py` | 4 | Workspace files / memory | Retain |
| `tests/unit/backend/rlm/test_native_dspy_contract.py` | 55 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_portable_adapter_salvage.py` | 9 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_program_factory.py` | 20 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_program_guidance.py` | 3 | RLM program / budget / output | Move to `tests/unit/backend/rlm/test_program_instructions.py` |
| `tests/unit/backend/rlm/test_program_inputs.py` | 21 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_program_instructions.py` | 6 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_program_lm_construction.py` | 15 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_program_options.py` | 3 | RLM program / budget / output | Move to `tests/unit/backend/rlm/test_program_factory.py` |
| `tests/unit/backend/rlm/test_program_signature_history.py` | 11 | RLM program / budget / output | Move to `tests/unit/backend/rlm/test_program_inputs.py` |
| `tests/unit/backend/rlm/test_provider_probe.py` | 3 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_recursion_budgets_fallback.py` | 28 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_recursion_claim_loss_fencing.py` | 3 | recursive behavior | Move to `tests/unit/backend/rlm/test_recursion_fencing.py` |
| `tests/unit/backend/rlm/test_recursion_content_safety.py` | 2 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_recursion_deadline_fence.py` | 5 | recursive behavior | Move to `tests/unit/backend/rlm/test_recursion_fencing.py` |
| `tests/unit/backend/rlm/test_recursion_flow.py` | 6 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_recursion_fresh_children.py` | 2 | recursive behavior | Move to `tests/unit/backend/rlm/test_recursion_isolation.py` |
| `tests/unit/backend/rlm/test_recursion_lease_cleanup.py` | 15 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_recursion_metrics.py` | 10 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_recursion_namespace_and_typed_parity.py` | 5 | recursive behavior | Move to `tests/unit/backend/rlm/test_recursion_isolation.py` |
| `tests/unit/backend/rlm/test_recursion_policy_surface.py` | 6 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_recursion_role_usage_parity.py` | 2 | recursive behavior | Move to `tests/unit/backend/rlm/test_recursion_isolation.py` |
| `tests/unit/backend/rlm/test_recursion_session_snapshot.py` | 9 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_recursion_tools.py` | 31 | recursive behavior | Retain |
| `tests/unit/backend/rlm/test_result_outcome.py` | 2 | RLM program / budget / output | Move to `tests/unit/backend/rlm/test_typed_results.py` |
| `tests/unit/backend/rlm/test_result_sanitize.py` | 13 | RLM program / budget / output | Move to `tests/unit/backend/rlm/test_typed_results.py` |
| `tests/unit/backend/rlm/test_runtime_cancellation.py` | 2 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_runtime_execution.py` | 8 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_runtime_outcomes.py` | 7 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_runtime_tool_guards.py` | 18 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_runtime_worker_lane.py` | 1 | RLM program / budget / output | Move to `tests/unit/backend/rlm/test_runtime_execution.py` |
| `tests/unit/backend/rlm/test_session_runtime_reuse.py` | 4 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_submit_validation.py` | 14 | RLM program / budget / output | Move to `tests/unit/backend/rlm/test_typed_results.py` |
| `tests/unit/backend/rlm/test_tool_catalog.py` | 9 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_turn_budget.py` | 15 | RLM program / budget / output | Retain |
| `tests/unit/backend/rlm/test_turn_history_integration.py` | 4 | RLM program / budget / output | Retain |
| `tests/unit/backend/runtime/test_owned_effect.py` | 5 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/sessions/test_history.py` | 14 | RLM program / budget / output | Retain |
| `tests/unit/backend/sessions/test_history_tool.py` | 7 | Workspace files / memory | Retain |
| `tests/unit/backend/sessions/test_history_transport.py` | 5 | Workspace files / memory | Retain |
| `tests/unit/backend/test_adapter_benchmark_v2.py` | 2 | live provider certification | Retain |
| `tests/unit/backend/test_artifact_reader_and_promotion.py` | 18 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_artifacts.py` | 5 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_assistant_parts.py` | 21 | API / SSE public contracts | Retain |
| `tests/unit/backend/test_attachment_artifact_durability.py` | 6 | Workspace files / memory | Retain |
| `tests/unit/backend/test_attachment_lifecycle.py` | 5 | Workspace files / memory | Retain |
| `tests/unit/backend/test_bind_safety.py` | 14 | packaging / configuration | Retain |
| `tests/unit/backend/test_binding_lineage_migration.py` | 5 | persistence / migrations | Retain |
| `tests/unit/backend/test_claim_constraint_classification.py` | 10 | persistence / migrations | Retain |
| `tests/unit/backend/test_cli.py` | 11 | packaging / configuration | Retain |
| `tests/unit/backend/test_cli_supervisor.py` | 28 | packaging / configuration | Retain |
| `tests/unit/backend/test_committed_turn.py` | 14 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_committed_turn_events.py` | 3 | API / SSE public contracts | Retain |
| `tests/unit/backend/test_config.py` | 56 | packaging / configuration | Retain |
| `tests/unit/backend/test_config_policy.py` | 16 | packaging / configuration | Retain |
| `tests/unit/backend/test_config_schema.py` | 15 | packaging / configuration | Retain |
| `tests/unit/backend/test_contention_scenarios.py` | 6 | persistence / migrations | Retain |
| `tests/unit/backend/test_database_compatibility.py` | 6 | persistence / migrations | Retain |
| `tests/unit/backend/test_database_preflight.py` | 19 | persistence / migrations | Retain |
| `tests/unit/backend/test_daytona_adapter.py` | 11 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_daytona_admission.py` | 5 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_daytona_diagnostics.py` | 31 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_daytona_lifecycle_benchmark.py` | 5 | live provider certification | Retain |
| `tests/unit/backend/test_daytona_platform.py` | 1 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_daytona_result_snapshot.py` | 5 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_engine_pool_policy.py` | 4 | persistence / migrations | Retain |
| `tests/unit/backend/test_ensure_database_compatible.py` | 3 | persistence / migrations | Retain |
| `tests/unit/backend/test_execution_context.py` | 1 | RLM program / budget / output | Retain |
| `tests/unit/backend/test_files_upload.py` | 4 | Workspace files / memory | Retain |
| `tests/unit/backend/test_host_tool_call_counts.py` | 4 | RLM program / budget / output | Retain |
| `tests/unit/backend/test_host_tool_submit_broker.py` | 37 | RLM program / budget / output | Retain |
| `tests/unit/backend/test_import_safety.py` | 5 | packaging / configuration | Retain |
| `tests/unit/backend/test_in_memory_turn_state.py` | 8 | persistence / migrations | Retain |
| `tests/unit/backend/test_live_composition.py` | 33 | live provider certification | Retain |
| `tests/unit/backend/test_live_turn_preparation.py` | 5 | live provider certification | Retain |
| `tests/unit/backend/test_managed_database_policy.py` | 4 | persistence / migrations | Retain |
| `tests/unit/backend/test_memory_promotion_intents.py` | 8 | persistence / migrations | Retain |
| `tests/unit/backend/test_memory_promotion_outbox.py` | 11 | persistence / migrations | Retain |
| `tests/unit/backend/test_mlflow_export_outage.py` | 6 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_mlflow_export_privacy.py` | 8 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_mlflow_feedback.py` | 6 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_mlflow_runtime.py` | 16 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_mlflow_tracing_config.py` | 32 | packaging / configuration | Retain |
| `tests/unit/backend/test_open_turn_command.py` | 5 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_orphan_cleanup.py` | 8 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_persistence_observations.py` | 11 | persistence / migrations | Retain |
| `tests/unit/backend/test_persistence_query_contracts.py` | 7 | persistence / migrations | Retain |
| `tests/unit/backend/test_phase1_daytona_stream_cleanup.py` | 1 | Daytona resource lifecycle / containment | Move to `tests/unit/backend/daytona/test_live_cleanup.py`; retain scenario and add failure reporting cases |
| `tests/unit/backend/test_posthog_client.py` | 11 | packaging / configuration | Retain |
| `tests/unit/backend/test_prewarm_session_manager.py` | 9 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_query_plan_scenarios.py` | 5 | persistence / migrations | Retain |
| `tests/unit/backend/test_runtime_benchmark_v2.py` | 11 | live provider certification | Retain |
| `tests/unit/backend/test_runtime_events.py` | 4 | API / SSE public contracts | Retain |
| `tests/unit/backend/test_sandbox_binding_repository.py` | 2 | persistence / migrations | Retain |
| `tests/unit/backend/test_sandbox_lifecycle.py` | 55 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_session_domain.py` | 9 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_session_list_repo.py` | 3 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_session_manager.py` | 51 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_session_prewarm.py` | 4 | Daytona resource lifecycle / containment | Retain |
| `tests/unit/backend/test_session_schema.py` | 6 | packaging / configuration | Retain |
| `tests/unit/backend/test_skill_catalog.py` | 8 | Workspace files / memory | Retain |
| `tests/unit/backend/test_skill_manifest.py` | 20 | Workspace files / memory | Retain |
| `tests/unit/backend/test_skill_resolver.py` | 8 | Workspace files / memory | Retain |
| `tests/unit/backend/test_skill_signatures.py` | 11 | Workspace files / memory | Retain |
| `tests/unit/backend/test_skill_signatures_history.py` | 8 | Workspace files / memory | Retain |
| `tests/unit/backend/test_skill_tools.py` | 14 | Workspace files / memory | Retain |
| `tests/unit/backend/test_sql_turn_state.py` | 17 | persistence / migrations | Retain |
| `tests/unit/backend/test_sse_trace_id.py` | 2 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_turn_claim_heartbeat.py` | 11 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_claim_policy.py` | 27 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_cleanup.py` | 4 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_coordinator_cancellation.py` | 5 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_coordinator_commit.py` | 4 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_coordinator_concurrency.py` | 1 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_coordinator_failures.py` | 9 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_coordinator_replay.py` | 1 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_detail_policy.py` | 5 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_lifecycle.py` | 17 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_lifecycle_cancellation.py` | 7 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_lifecycle_settlement.py` | 6 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_lineage_migration.py` | 3 | persistence / migrations | Retain |
| `tests/unit/backend/test_turn_preparation.py` | 10 | Run lifecycle / settlement | Retain |
| `tests/unit/backend/test_turn_preparation_database_failures.py` | 3 | persistence / migrations | Retain |
| `tests/unit/backend/test_turn_preparation_tracing.py` | 3 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_turn_trace_phase_link.py` | 7 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_turn_tracing.py` | 44 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_ui_stream.py` | 16 | API / SSE public contracts | Retain |
| `tests/unit/backend/test_validate_mlflow_tracing.py` | 6 | observability privacy / lifecycle | Retain |
| `tests/unit/backend/test_volume_paths.py` | 10 | Workspace files / memory | Retain |
| `tests/unit/backend/test_warm_pool_ownership_repository.py` | 2 | persistence / migrations | Retain |
| `tests/unit/backend/test_workspace_volume_gateway.py` | 4 | Workspace files / memory | Retain |
| `tests/unit/backend/test_workspace_volume_isolation.py` | 11 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_memory_candidates.py` | 18 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_memory_models.py` | 14 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_memory_tools.py` | 29 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_project_paths.py` | 30 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_project_tools.py` | 24 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_url_tool.py` | 18 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_workspace_fs.py` | 52 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_workspace_paths.py` | 14 | Workspace files / memory | Retain |
| `tests/unit/backend/workspace/test_workspace_tools.py` | 19 | Workspace files / memory | Retain |
| `tests/unit/optimization/test_curated_input.py` | 10 | live provider certification | Retain |
| `tests/unit/optimization/test_dataset.py` | 11 | live provider certification | Retain |
| `tests/unit/optimization/test_evidence.py` | 13 | live provider certification | Retain |
| `tests/unit/optimization/test_gepa_runner.py` | 8 | live provider certification | Retain |
| `tests/unit/optimization/test_mlflow_observability.py` | 2 | live provider certification | Retain |
| `tests/unit/optimization/test_routing_eval.py` | 11 | live provider certification | Retain |
| `tests/unit/scripts/test_align_judges.py` | 5 | live provider certification | Retain |
| `tests/unit/scripts/test_annotate_traces.py` | 10 | packaging / configuration | Retain |
| `tests/unit/scripts/test_attach_phase3_receipt.py` | 5 | packaging / configuration | Retain |
| `tests/unit/scripts/test_campaign.py` | 10 | live provider certification | Retain |
| `tests/unit/scripts/test_certify_daytona_sdk.py` | 3 | live provider certification | Retain |
| `tests/unit/scripts/test_certify_mlflow.py` | 5 | live provider certification | Retain |
| `tests/unit/scripts/test_certify_postgres.py` | 11 | live provider certification | Retain |
| `tests/unit/scripts/test_check_agents_md_freshness.py` | 3 | packaging / configuration | Retain |
| `tests/unit/scripts/test_check_codebase_tree.py` | 5 | packaging / configuration | Retain |
| `tests/unit/scripts/test_check_dependency_boundaries.py` | 6 | packaging / configuration | Retain |
| `tests/unit/scripts/test_check_docs_quality.py` | 3 | packaging / configuration | Retain |
| `tests/unit/scripts/test_check_harness_engineering.py` | 3 | packaging / configuration | Retain |
| `tests/unit/scripts/test_circleci_trigger_release.py` | 2 | packaging / configuration | Retain |
| `tests/unit/scripts/test_corpus_chain.py` | 11 | live provider certification | Retain |
| `tests/unit/scripts/test_daytona_snapshot.py` | 13 | packaging / configuration | Retain |
| `tests/unit/scripts/test_daytona_warm_pool.py` | 3 | packaging / configuration | Retain |
| `tests/unit/scripts/test_enable_monitoring.py` | 6 | packaging / configuration | Retain |
| `tests/unit/scripts/test_inventory_db_heads.py` | 5 | packaging / configuration | Retain |
| `tests/unit/scripts/test_judges.py` | 6 | live provider certification | Retain |
| `tests/unit/scripts/test_lakebase_preflight.py` | 3 | packaging / configuration | Retain |
| `tests/unit/scripts/test_live_daytona_verify.py` | 21 | live provider certification | Retain |
| `tests/unit/scripts/test_live_p27_snapshot_verify.py` | 2 | live provider certification | Retain |
| `tests/unit/scripts/test_live_phase1_stream_verify.py` | 11 | live provider certification | Retain |
| `tests/unit/scripts/test_live_phase2_recursive_verify.py` | 10 | live provider certification | Retain |
| `tests/unit/scripts/test_manage_prompts.py` | 10 | packaging / configuration | Retain |
| `tests/unit/scripts/test_migrate_sqlite_to_postgres.py` | 11 | packaging / configuration | Retain |
| `tests/unit/scripts/test_phase6_cases.py` | 1 | live provider certification | Retain |
| `tests/unit/scripts/test_record_mlflow_campaign.py` | 9 | live provider certification | Retain |
| `tests/unit/scripts/test_rlm_eval_dataset.py` | 14 | live provider certification | Retain |
| `tests/unit/scripts/test_run_rlm_latency.py` | 29 | live provider certification | Retain |
| `tests/unit/scripts/test_run_routing_eval.py` | 5 | live provider certification | Retain |
| `tests/unit/scripts/test_scorers.py` | 18 | live provider certification | Retain |
| `tests/unit/test_litellm_invariant.py` | 173 | packaging / configuration | Retain |

## Shared setup disposition

- Remove duplicate recursion DummyLM constructors and reuse the existing fresh-child executor for equivalent namespace scenarios.
- Extract scripted adapter LM/signature, persistence intent seeding, and Session-manager setup from collected test modules.
- Extract PostgreSQL/local contention and query workloads into ordinary scenario helpers with explicit wrappers in each lane.
- Extract shared strict live cleanup from the Phase 1 canary; preserve directly invoked test names and evidence variables.
- Keep lifecycle cancellation/settlement orchestration separate; share only repeated claimed-run and outcome construction.
- No behavior deletion is preauthorized by an issue name, a private access, or the presence of a registry. Active broker/native feasibility tests remain.

## Hard-invariant owners

| Invariant | Owning suites (under tests/) |
| --- | --- |
| Claim exclusion, idempotency and adapter parity | unit/backend/test_turn_claim_policy.py; contracts/backend/test_turn_claim_adapter_parity.py; unit/backend/test_contention_scenarios.py |
| Cancellation, timeout, claim loss and no late mutation | unit/backend/test_turn_lifecycle_cancellation.py; unit/backend/test_turn_coordinator_cancellation.py; unit/backend/rlm/test_recursion_fencing.py; unit/backend/runtime/test_owned_effect.py |
| Settlement, snapshots and atomic artifact publication | unit/backend/test_turn_lifecycle.py; unit/backend/test_turn_lifecycle_settlement.py; contracts/backend/test_result_snapshot_commit.py |
| Resource ownership, absence confirmation and cleanup | unit/backend/daytona/test_deletion_lifecycle.py; unit/backend/daytona/test_child_lease_cleanup_ownership.py; live/backend/test_daytona_containment.py |
| Durable Session/Volume continuity | unit/backend/test_attachment_artifact_durability.py; unit/backend/daytona/test_daytona_session_lifecycle.py; live/backend/test_attachment_artifact_durability.py |
| Typed results and budget boundaries | unit/backend/rlm/test_typed_results.py; unit/backend/rlm/test_turn_budget.py; unit/backend/rlm/test_adapter_budget_integration.py |
| LM template isolation and DSPy history ownership | unit/backend/rlm/test_program_factory.py; unit/backend/rlm/test_native_dspy_contract.py; unit/backend/rlm/test_recursion_isolation.py |
| Public stream contracts and privacy | contracts/backend/test_stream_contract_parity.py; freeze/test_public_stream_gate.py; unit/backend/test_mlflow_export_privacy.py |

## Validation results

Baseline `make check` passed, including package-wide branch coverage at 79.1%
and 543 TUI cases. RLM/Daytona consolidation and shared-helper focused suites
passed serially. Before/after collection preserves every unchanged scenario's
markers and fixture names.

Explicit scenario reconciliation:

- `test_invalid_options_fail_before_construction` and the three cases from
  `test_program_options.py` are replaced by four unique `(field, value)` cases
  in `test_program_factory.py::test_rlm_options_reject_nonpositive_values`.
  Retain zero for each option, negative `max_llm_calls`, the typed configuration
  error, and field-specific diagnostics; remove repeated zero-value executions.
- Delete `test_compatibility_implementation_has_one_versioned_home`: it pinned
  absence of the removed `_dspy_compat.py` file and an implementation module name.
  The existing private-DSPy-import confinement tests and native adapter/constructor
  contracts continue to own the supported compatibility boundary.
- Add two live-cleanup failure cases and one subprocess opt-out regression to
  the existing cleanup/verifier behavior owners. The latter proves all 37 live
  pytest cases skip with live opt-in disabled.

After consolidation there are 287 source test files (12 fewer) and 3,029
collected cases (3,027 baseline, minus one internal-location case, plus three
cleanup/opt-out cases). All other node IDs reconcile through the file-move map;
the four option cases account for the explicit parametrization change above.
No surviving scenario lost a marker or fixture. The post-change deterministic,
API, TUI, typing, lint, boundary, and documentation checks passed; package-wide
coverage remains 79.1%. The documentation gate's initial one-line AGENTS budget
failure was corrected without weakening the limit.

No production source, policy, dependency, generated contract, or coverage
threshold changed. The final input-helper extraction also passed its focused
42-case lane and Ruff checks after the full gate.

### Affected live certification — 2026-09-10

Candidate: `10a347fbb86e989af616d4abfb2c81db655edb62`, tested with a clean tree.
Receipt paths below are relative to `.fleet-evidence/receipts/adr006/`; they are
local operator evidence, not checked-in artifacts or managed-service certification.

| Lane | Result | Receipt |
| --- | --- | --- |
| P2.7 Session and semantic-child probes, Session stream, recursive RLM | PASS, including disposable cleanup | `p3-p27-snapshots-20260910-r1.json` |
| Recursive two-child batch | PASS | `p3-batch-20260910-r1.json` |
| Cancellation during execution | PASS | `p3-cancel-20260910-r1.json` |
| Deadline cleanup | PASS | `p3-deadline-20260910-r1.json` |
| Memory candidate promotion and next-Turn retrieval | PASS | `p3-memory-candidate-20260910-r1.json` |
| MVP | FAIL, both cases | `p3-mvp-20260910-r1.json` |
| Memory semantics | Five passed, one failed | Per-case receipts and `p3-affected-live-summary-20260910-r1.json` |
| PostgreSQL contention and query plans | Six scenarios and five query plans PASS | `p3-postgres-20260910-r2.json` |

The PostgreSQL run used an owned, disposable local PostgreSQL 17.10 database,
not the configured shared database. Migration to head `01a087800002` passed.
The initial SQL-ASCII database attempt failed and its `r1` receipt is retained;
the UTF-8 database produced the passing `r2` receipt. The owned server was stopped.
An additional `alembic check` reported proposed constraint removal on
`fleet_sandbox_bindings`; this schema/autogenerate discrepancy remains unresolved.
This candidate did not change production models or migrations.

Remaining live failures are preserved without weakening assertions:

- Direct pi-digit MVP completed with the correct digit but emitted four code
  chunks, exceeding the contracted maximum of three.
- Complete MVP did not observe the required `append_workspace_text` tool call.
- Failed-run memory-discard scenario reached an error finish but failed its
  requirement that an error contain `timed out`; later candidate-discard
  assertions were therefore not exercised successfully.

The MVP test/helper function bodies are unchanged by extraction (AST comparison
against the pre-consolidation revision). This does not establish that baseline
live runs would have failed: no baseline provider comparison was run. The
current failures block full Phase 3 certification. Provider/workflow diagnosis
and any production fix are separate from test-suite consolidation. No retries
were used to replace these failing receipts, and no model, policy, snapshot, or
runtime promotion was performed. Unaffected MLflow/campaign lanes were not rerun.

### Bounded candidate qualification closeout — 2026-09-12

The deterministic contract lanes remained green. With explicit operator
authorization, bounded live qualification was attempted against clean detached
candidates; the verifier ran the durability lane first and then the complete
FastAPI/DSPy/Daytona MVP lane. No prompt overlay or assertion relaxation was
introduced.

| Root/Sub candidate | Clean candidate SHA | Profile / snapshots | Receipt | Result |
| --- | --- | --- | --- | --- |
| `databricks-deepseek-v4-pro-0813` / same | `e27d67100e6cd899442b45ac35cb7f523c163087` | `daytona-recursive` / `fleet-rlm-python313-v7`, `fleet-rlm-python313-child-v2` | `.scratch/live-receipts/mvp-complete-deepseek-v4-pro-0813.json` | Durability PASS; complete MVP `proof_failed` |

The final receipt records the explicit pair, candidate SHA, profile, snapshots,
900-second lane limit, 60-second subprocess grace, and two-lane verifier scope.
Earlier operator-selected `databricks-deepseek-v4-1-flash` and
`databricks-deepseek-v4-flash-0731` attempts, plus the initial Sonnet attempt,
are retained under `.scratch/live-receipts/` as bounded failures and do not
change the result. Local MLflow 3.16.0 (`127.0.0.1:5001`) and the FastAPI
OpenAPI service (`127.0.0.1:8000`) were started and health-checked separately;
their availability does not turn the failed MVP lane into a pass. Phase 3
therefore remains uncertified. The qualification did not itself promote a
default model; any later policy promotion must be recorded separately.

### Explicit Databricks profile exception rerun — 2026-09-12

The single authorized requalification rerun used Databricks profile
`218237876678801` with Root/Sub `databricks-deepseek-v4-1-flash` on clean
candidate `b12574fee3d5e7409f3725d3a3ab5f9fbbb332c0`. The endpoint was
`READY`/`NOT_UPDATING`; local MLflow 3.16.0 and FastAPI OpenAPI health checks
also passed before execution.

Receipt: `.scratch/live-receipts/mvp-complete-deepseek-v4-1-flash-profile-218237876678801.json`.
It records schema v2, the exact model pair, `daytona-recursive`, session
snapshot `fleet-rlm-python313-v7`, child snapshot `fleet-rlm-python313-child-v2`,
the 900-second lane limit, 60-second subprocess grace, and two-lane scope.
The durability lane passed, but the complete FastAPI/DSPy/Daytona MVP lane
returned `proof_failed`. This was the final authorized attempt; no retry,
assertion relaxation, prompt overlay, or model/default promotion occurred.
Phase 3 remains uncertified and Phase 5 remains out of scope.

### Default model policy promotion — 2026-09-12

Following explicit operator authorization, the committed Root and Sub policy was
set to `databricks-deepseek-v4-1-flash` through the existing Databricks Unity AI
Gateway Chat Completions transport. This changes the configured default pair only;
it does not alter the strict MVP contract or convert either failed qualification
receipt into Phase 3 certification.

### DeepSeek Chat diagnostic execution — 2026-09-12

An additionally authorized diagnostic execution on clean candidate
`706c53f906cd70ba78fc081ce75dc74f3eb5667c` confirmed that the failure is not
the Databricks Chat Completions transport. Root generated the required
three-cell shape, but replaced the specified `ROOT`/`ALPHA`/`BETA`/`GAMMA`
semantic prompts with its own arithmetic prompts. The semantic verifier then
rejected the results before the workspace append could execute, and the first
Turn ended with the public failure that a required workspace update was not
completed. Receipt:
`.scratch/live-receipts/deepseek-v4-1-chat-diagnostic-20260912.json`.
The default policy remains promoted, while Phase 3 remains uncertified because
the strict scenario was not satisfied.

### GLM 5.3 Flash qualification — 2026-09-12

The selected alternative was preflighted as `READY`/`NOT_UPDATING`; its Unity AI
Gateway Chat Completions request returned exactly `ROOT`. The one authorized
two-lane qualification then used Root/Sub `databricks-glm-5-3-flash` on clean
candidate `7015b80aeecde87ee135150559c59b7018f9f422`. The durability lane
passed, but the complete FastAPI/DSPy/Daytona MVP lane returned `proof_failed`.
Receipt: `.scratch/live-receipts/mvp-complete-glm-5-3-flash-chat-profile-218237876678801.json`.
No retry or default-policy change followed; Phase 3 remains uncertified.

### Exact-code MVP gate retired — 2026-09-12

Operator decision: retire `test_complete_daytona_mvp_through_fastapi` and its
two-lane verifier path as a Phase 3 certification requirement. The test remains
historical diagnostic coverage, but no longer blocks Phase 3 because it measures
literal model-program obedience rather than runtime safety or durable product
behavior. Failed receipts are retained unchanged. Phase 3 is complete under the
remaining deterministic contracts and live durability/replacement/cleanup
evidence; future semantic model evaluation requires a separate product-quality
charter.

# Code Health Complexity Heatmap

Wave 1 priority: ReAct + Server WS. Target complexity threshold for refactor assessment: `<= 15`.

## Thresholds

- LOC bands: `L1 >= 700`, `L2 500-699`, `L3 350-499`
- Complexity bands: `H1 >= 25`, `H2 16-24`, `H3 10-15`
- Priority formula: `0.55*complexity_rank + 0.30*runtime_criticality + 0.15*recent_churn`

## LOC Hotspots

| band | loc | file                                                                        |
| ---- | --- | --------------------------------------------------------------------------- |
| L1   | 823 | `src/fleet_rlm/db/models.py`                                                |
| L1   | 795 | `src/fleet_rlm/runtime/interpreter.py`                                         |
| L1   | 707 | `src/fleet_rlm/react/agent.py`                                              |
| L2   | 591 | `src/fleet_rlm/terminal/commands.py`                                        |
| L2   | 565 | `src/fleet_rlm/react/streaming.py`                                          |
| L2   | 560 | `src/fleet_rlm/terminal/chat.py`                                            |
| L2   | 526 | `src/fleet_rlm/runners.py`                                                  |
| L2   | 514 | `src/fleet_rlm/db/repository.py`                                            |
| L3   | 474 | `src/fleet_rlm/react/tools/sandbox.py`                                      |
| L3   | 446 | `src/fleet_rlm/terminal/ui.py`                                              |
| L3   | 414 | `src/fleet_rlm/server/routers/runtime.py`                                   |
| L3   | 408 | `src/fleet_rlm/runtime/config.py`                                              |
| L3   | 408 | `src/fleet_rlm/utils/scaffold.py`                                           |
| L3   | 382 | `src/fleet_rlm/react/tools/delegate.py`                                     |
| L3   | 381 | `src/fleet_rlm/server/routers/ws/api.py`                                    |
| L3   | 371 | `src/fleet_rlm/react/tools/document.py`                                     |
| L3   | 363 | `src/fleet_rlm/_scaffold/skills/rlm-long-context/scripts/semantic_chunk.py` |
| L3   | 350 | `src/fleet_rlm/react/rlm_runtime_modules.py`                                |

## Complexity Hotspots (Ranked)

| method                                                                                            | cc  | rank | heat_band | runtime_criticality | recent_churn | priority |
| ------------------------------------------------------------------------------------------------- | --- | ---- | --------- | ------------------- | ------------ | -------- |
| `src/fleet_rlm/server/execution/step_builder.py::ExecutionStepBuilder.from_stream_event`          | 30  | D    | H1        | 1.0                 | 1            | 0.8560   |
| `src/fleet_rlm/server/routers/ws/commands.py::_handle_command`                                    | 28  | D    | H1        | 1.0                 | 1            | 0.8560   |
| `src/fleet_rlm/react/tools/filesystem.py::_list_files_impl`                                       | 27  | D    | H1        | 1.0                 | 1            | 0.8560   |
| `src/fleet_rlm/runtime/interpreter.py::ModalInterpreter.execute`                                     | 44  | F    | H1        | 0.7                 | 11           | 0.8260   |
| `src/fleet_rlm/react/streaming.py::aiter_chat_turn_stream`                                        | 16  | C    | H2        | 1.0                 | 14           | 0.7690   |
| `src/fleet_rlm/react/streaming_citations.py::_normalize_trajectory`                               | 20  | C    | H2        | 1.0                 | 3            | 0.7030   |
| `src/fleet_rlm/react/delegate_sub_agent.py::spawn_delegate_sub_agent`                             | 19  | C    | H2        | 1.0                 | 3            | 0.7030   |
| `src/fleet_rlm/react/streaming_citations.py::_extract_final_attachments`                          | 18  | C    | H2        | 1.0                 | 3            | 0.7030   |
| `src/fleet_rlm/react/streaming_citations.py::_extract_final_citations`                            | 17  | C    | H2        | 1.0                 | 3            | 0.7030   |
| `src/fleet_rlm/react/streaming_citations.py::_normalize_citation_entry`                           | 17  | C    | H2        | 1.0                 | 3            | 0.7030   |
| `src/fleet_rlm/server/routers/ws/api.py::chat_streaming`                                          | 24  | D    | H2        | 1.0                 | 2            | 0.6970   |
| `src/fleet_rlm/react/document_sources.py::fetch_url_document_content`                             | 19  | C    | H2        | 1.0                 | 2            | 0.6970   |
| `src/fleet_rlm/terminal/commands.py::handle_alias_command`                                        | 30  | D    | H1        | 0.4                 | 2            | 0.6820   |
| `src/fleet_rlm/server/config.py::ServerRuntimeConfig`                                             | 24  | D    | H2        | 0.7                 | 14           | 0.6790   |
| `src/fleet_rlm/terminal/chat.py::_TerminalChatSession._run_chat_turn`                             | 29  | D    | H1        | 0.4                 | 1            | 0.6760   |
| `src/fleet_rlm/runtime/driver.py::sandbox_driver`                                                    | 16  | C    | H2        | 0.7                 | 7            | 0.6370   |
| `src/fleet_rlm/server/routers/sessions.py::list_session_state`                                    | 17  | C    | H2        | 0.7                 | 6            | 0.6310   |
| `src/fleet_rlm/react/agent.py::RLMReActChatAgent._count_tool_errors`                              | 11  | C    | H3        | 1.0                 | 17           | 0.6220   |
| `src/fleet_rlm/react/agent.py::RLMReActChatAgent.chat_turn_stream`                                | 11  | C    | H3        | 1.0                 | 17           | 0.6220   |
| `src/fleet_rlm/react/streaming.py::_process_stream_value`                                         | 13  | C    | H3        | 1.0                 | 14           | 0.6040   |
| `src/fleet_rlm/react/streaming.py::iter_chat_turn_stream`                                         | 12  | C    | H3        | 1.0                 | 14           | 0.6040   |
| `src/fleet_rlm/react/validation.py::trajectory_has_tool_errors`                                   | 15  | C    | H3        | 1.0                 | 2            | 0.5320   |
| `src/fleet_rlm/react/validation.py::validate_assistant_response`                                  | 13  | C    | H3        | 1.0                 | 2            | 0.5320   |
| `src/fleet_rlm/react/document_sources.py::_assert_public_http_url`                                | 11  | C    | H3        | 1.0                 | 2            | 0.5320   |
| `src/fleet_rlm/server/routers/ws/lifecycle.py::ExecutionLifecycleManager._persist_worker`         | 15  | C    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/server/execution/sanitizer.py::sanitize_event_payload`                             | 13  | C    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/server/routers/ws/lifecycle.py::ExecutionLifecycleManager.complete_run`            | 12  | C    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/server/routers/ws/message_loop.py::switch_session_if_needed`                       | 12  | C    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/server/routers/ws/session.py::persist_session_state`                               | 12  | C    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/react/tools/document.py::_read_document_content`                                   | 11  | C    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/react/document_cache.py::DocumentCacheMixin.restore_document_cache_state`          | 10  | B    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/react/tools/__init__.py::build_trajectory_payload`                                 | 10  | B    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/server/routers/ws/lifecycle.py::_classify_stream_failure`                          | 10  | B    | H3        | 1.0                 | 1            | 0.5260   |
| `src/fleet_rlm/_scaffold/skills/rlm-long-context/scripts/semantic_chunk.py::chunk_json`           | 17  | C    | H2        | 0.4                 | 3            | 0.5230   |
| `src/fleet_rlm/analytics/posthog_callback.py::PostHogLLMCallback._extract_token_usage`            | 16  | C    | H2        | 0.4                 | 3            | 0.5230   |
| `src/fleet_rlm/terminal/commands.py::handle_slash_command`                                        | 20  | C    | H2        | 0.4                 | 2            | 0.5170   |
| `src/fleet_rlm/terminal/ui.py::_prompt_choice`                                                    | 18  | C    | H2        | 0.4                 | 2            | 0.5170   |
| `src/fleet_rlm/terminal/ui.py::_iter_mention_paths`                                               | 16  | C    | H2        | 0.4                 | 2            | 0.5170   |
| `src/fleet_rlm/server/config.py::ServerRuntimeConfig.validate_startup_or_raise`                   | 11  | C    | H3        | 0.7                 | 14           | 0.5140   |
| `src/fleet_rlm/models/streaming.py::TurnState`                                                    | 22  | D    | H2        | 0.4                 | 1            | 0.5110   |
| `src/fleet_rlm/models/streaming.py::TurnState.apply`                                              | 21  | D    | H2        | 0.4                 | 1            | 0.5110   |
| `src/fleet_rlm/terminal/chat.py::_TerminalChatSession._print_status`                              | 21  | D    | H2        | 0.4                 | 1            | 0.5110   |
| `src/fleet_rlm/runtime/config.py::get_delegate_lm_from_env`                                          | 12  | C    | H3        | 0.7                 | 12           | 0.5020   |
| `src/fleet_rlm/runtime/config.py::_load_dotenv`                                                      | 11  | C    | H3        | 0.7                 | 12           | 0.5020   |
| `src/fleet_rlm/server/routers/runtime.py::patch_runtime_settings`                                 | 13  | C    | H3        | 0.7                 | 4            | 0.4540   |
| `src/fleet_rlm/server/auth/dev.py::DevAuthProvider._authenticate`                                 | 14  | C    | H3        | 0.7                 | 3            | 0.4480   |
| `src/fleet_rlm/fleet_cli.py::main`                                                                | 12  | C    | H3        | 0.4                 | 5            | 0.3700   |
| `src/fleet_rlm/analytics/posthog_callback.py::PostHogLLMCallback._extract_output_choices`         | 15  | C    | H3        | 0.4                 | 3            | 0.3580   |
| `src/fleet_rlm/_scaffold/skills/rlm-long-context/scripts/orchestrate.py::orchestrate`             | 13  | C    | H3        | 0.4                 | 3            | 0.3580   |
| `src/fleet_rlm/analytics/posthog_callback.py::PostHogLLMCallback.on_lm_end`                       | 10  | B    | H3        | 0.4                 | 3            | 0.3580   |
| `src/fleet_rlm/terminal/commands.py::print_command_palette`                                       | 13  | C    | H3        | 0.4                 | 2            | 0.3520   |
| `src/fleet_rlm/terminal/ui.py::_FleetCompleter.get_completions`                                   | 10  | B    | H3        | 0.4                 | 2            | 0.3520   |
| `src/fleet_rlm/terminal/chat.py::_TerminalChatSession.run`                                        | 13  | C    | H3        | 0.4                 | 1            | 0.3460   |
| `src/fleet_rlm/utils/modal.py::load_modal_config`                                                 | 12  | C    | H3        | 0.4                 | 1            | 0.3460   |
| `src/fleet_rlm/_scaffold/skills/rlm-long-context/scripts/cache_manager.py::main`                  | 10  | B    | H3        | 0.4                 | 1            | 0.3460   |
| `src/fleet_rlm/_scaffold/skills/rlm-long-context/scripts/codebase_concat.py::should_include_file` | 10  | B    | H3        | 0.4                 | 1            | 0.3460   |
| `src/fleet_rlm/utils/scaffold.py::list_teams`                                                     | 10  | B    | H3        | 0.4                 | 1            | 0.3460   |

## Wave 1 Method Backlog (`refactor-method-complexity-reduce`)

| order | method                                                                                   | baseline_cc | target_cc | critical tests                                                                                                                                                                                                                                        |
| ----- | ---------------------------------------------------------------------------------------- | ----------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | `src/fleet_rlm/server/execution/step_builder.py::ExecutionStepBuilder.from_stream_event` | 30          | 15        | `tests/unit/test_events.py`<br>`tests/unit/test_execution_events.py`                                                                                                                                                                                  |
| 2     | `src/fleet_rlm/server/routers/ws/commands.py::_handle_command`                           | 28          | 15        | `tests/ui/ws/test_commands.py`<br>`tests/ui/ws/test_chat_stream.py`<br>`tests/ui/ws/test_execution_stream.py`<br>`tests/ui/ws/test_validation_and_errors.py`<br>`tests/ui/server/test_api_contract_routes.py`<br>`tests/unit/test_ws_chat_helpers.py` |
| 3     | `src/fleet_rlm/server/routers/ws/api.py::chat_streaming`                                 | 24          | 15        | `tests/ui/ws/test_chat_stream.py`<br>`tests/ui/ws/test_execution_stream.py`<br>`tests/ui/ws/test_validation_and_errors.py`<br>`tests/ui/server/test_api_contract_routes.py`<br>`tests/unit/test_ws_chat_helpers.py`                                   |
| 4     | `src/fleet_rlm/react/streaming_citations.py::_normalize_trajectory`                      | 20          | 15        | `tests/unit/test_react_streaming.py`<br>`tests/unit/test_react_tools.py`                                                                                                                                                                              |
| 5     | `src/fleet_rlm/react/streaming_citations.py::_extract_final_attachments`                 | 18          | 15        | `tests/unit/test_react_streaming.py`                                                                                                                                                                                                                  |
| 6     | `src/fleet_rlm/react/streaming_citations.py::_normalize_citation_entry`                  | 17          | 15        | `tests/unit/test_react_streaming.py`                                                                                                                                                                                                                  |
| 7     | `src/fleet_rlm/react/streaming_citations.py::_extract_final_citations`                   | 17          | 15        | `tests/unit/test_react_streaming.py`                                                                                                                                                                                                                  |
| 8     | `src/fleet_rlm/react/delegate_sub_agent.py::spawn_delegate_sub_agent`                    | 19          | 15        | `tests/unit/test_rlm_state.py`<br>`tests/unit/test_react_tools.py`<br>`tests/unit/test_tools_sandbox.py`                                                                                                                                              |
| 9     | `src/fleet_rlm/react/streaming.py::aiter_chat_turn_stream`                               | 16          | 15        | `tests/unit/test_react_streaming.py`<br>`tests/unit/test_react_tools.py`<br>`tests/unit/test_tools_sandbox.py`                                                                                                                                        |

## Method Assessment Template

For each Wave 1 method, record:

1. Baseline: CC, LOC, dependent tests, side-effect boundaries.
2. Extraction opportunities: guard validation, dispatch, payload normalization, error/fallback.
3. Proposed helper methods: name, responsibility, I/O contract, state dependencies.
4. No-regression contract: invariants and preserved outputs/errors.
5. Validation: targeted tests + `--junitxml` with explicit `failures="0"` and `errors="0"`, then rerun Radon for threshold check.
6. Skill-aligned gate (`refactor-method-complexity-reduce`): explicitly verify pytest textual summary reports `failed=0` before marking method safe.

## No-Regression Scenarios

1. WS chat success path keeps envelope compatibility.
2. WS command errors preserve response schema/status behavior.
3. Step builder parent-child linkage and deterministic IDs remain stable.
4. ReAct streaming preserves event ordering and final citation/attachment payload semantics.
5. Delegate sub-agent depth, fallback, and truncation controls remain unchanged.

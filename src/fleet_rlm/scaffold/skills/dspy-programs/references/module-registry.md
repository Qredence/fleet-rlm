# Module Registry — Registered Runtime Modules

Modules registered in `src/fleet_rlm/quality/module_registry.py`. Each entry
maps a module name to its signature, purpose, and selection criteria.

## Registered Modules

| Module Name | Signature | Purpose | When Selected |
| --- | --- | --- | --- |
| `grounded_answer` | `GroundedAnswerWithCitations` | Produce evidence-backed answers with inline citations and confidence scores | Query includes `evidence_chunks`; response requires verifiable sourcing |
| `memory_tree` | `VolumeFileTreeSignature` | Traverse and represent the durable volume file tree | Agent needs to inspect or report on volume contents |
| `memory_action_intent` | `MemoryActionIntentSignature` | Classify user intent into memory actions (read/write/delete) with risk assessment | User request involves modifying persistent memory files |
| `memory_structure_migration_plan` | `MemoryStructureMigrationPlanSignature` | Plan structural reorganization of the memory volume layout | Memory layout needs consolidation or schema migration |
| `clarification_questions` | `ClarificationQuestionSignature` | Generate targeted questions to resolve ambiguity before execution | Request is ambiguous and execution would be wasteful without clarification |
| `triage_incident_logs` | `IncidentTriageFromLogs` | Triage production incidents by severity, root cause, and recommended actions | Logs provided with service context; incident response workflow |

## Module Details

### `grounded_answer`

```python
# Signature: GroundedAnswerWithCitations
# Fields: query, evidence_chunks, response_style -> answer, citations, confidence, coverage_notes
```

- **Selection trigger**: Evidence chunks are present in the request payload.
- **Evaluation metric**: Citation accuracy (cited sources exist in evidence) +
  answer groundedness (claims traceable to provided evidence).
- **Default params**: `max_iterations=15`, `max_llm_calls=20`.

### `memory_tree`

```python
# Signature: VolumeFileTreeSignature
# Fields: root_path, max_depth, include_hidden -> nodes, total_files, total_dirs, truncated
```

- **Selection trigger**: Agent tool call requests file-tree inspection of the
  durable volume.
- **Evaluation metric**: Structural accuracy (tree matches actual filesystem).
- **Default params**: `max_depth=5`, `include_hidden=False`.

### `memory_action_intent`

```python
# Signature: MemoryActionIntentSignature
# Fields: user_request, current_tree, policy_constraints -> action_type, target_paths, content_plan, risk_level, requires_confirmation, rationale
```

- **Selection trigger**: User request references persistent memory operations
  (save, update, delete, organize).
- **Evaluation metric**: Intent classification accuracy + risk-level calibration.
- **Default params**: `max_llm_calls=10`.

### `memory_structure_migration_plan`

```python
# Signature: MemoryStructureMigrationPlanSignature
# Fields: current_structure, target_constraints, migration_policy -> plan_steps, file_moves, content_transforms, validation_checks, rollback_plan
```

- **Selection trigger**: Explicit request to reorganize memory layout or
  detected structural debt in current volume.
- **Evaluation metric**: Plan completeness (all files accounted for) +
  reversibility (rollback plan valid).
- **Default params**: `max_iterations=10`, `max_llm_calls=15`.

### `clarification_questions`

```python
# Signature: ClarificationQuestionSignature
# Fields: user_request, available_context, ambiguity_signals -> questions, priority_order, blocking_level
```

- **Selection trigger**: Ambiguity score exceeds threshold; multiple valid
  interpretations detected.
- **Evaluation metric**: Question relevance + disambiguation power (would the
  answer meaningfully change execution).
- **Default params**: `max_llm_calls=5`.

### `triage_incident_logs`

```python
# Signature: IncidentTriageFromLogs
# Fields: logs, service_context, query -> severity, probable_root_causes, impacted_components, recommended_actions, time_range
```

- **Selection trigger**: Logs provided with service context; query involves
  incident investigation or root-cause analysis.
- **Evaluation metric**: Severity calibration + root-cause accuracy (validated
  against post-mortem data when available).
- **Default params**: `max_iterations=20`, `max_llm_calls=25`.

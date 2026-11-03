# Phase 00: memory contract hygiene

Status: deferred, 2026-11-03. Not implemented by the scope-reduction cleanup. Prerequisite for
[Phase 03](memory-03-schema-provenance.md) and
[Phase 04](memory-04-draft-learning.md). See the [program index](memory-program.md).

## Purpose

The memory API returns an untyped list and accepts input the route then rejects.
Phase 03 replaces the untyped list, and Phase 04 reuses the memory kind
vocabulary. Fixing the contract first avoids shipping a second representation of
the same payload and avoids writing new models against a contract that is about
to change.

This phase changes exactly three request/response contracts and records one
convention decision. It is deliberately small so its `openapi.yaml` diff can be
read in full.

## Scope

### 1. Typed memory list

`Memories.memories` is `list[dict[str, Any]]`
(`src/fleet_rlm/api/schemas.py`). Replace it with `list[MemoryRead]` and keep the
`memories` envelope and existing field names.

```python
MemoryKind = Literal["observation", "preference", "hypothesis", "procedure"]


class MemoryRead(BaseModel):
    """One stored memory with its workspace sources and review state."""

    memory_id: str
    content: str
    kind: MemoryKind
    sources: dict[str, str]
    reviewed: bool
    updated_at: AwareDatetime
```

`MemoryRead` is defined here because it is also the response element Phase 03
extends with `source_turn_ids`. Phase 00 does not add that field.

Source: `Store.memories` selects `memory_id, content, kind, sources, reviewed,
updated_at` and converts `updated_at` to an ISO string before returning. The
route passes that through `Memories(memories=...)`.

### 2. Enum kind

`MemoryRequest.kind` is `str` with
`pattern="^(observation|preference|hypothesis|procedure)$"`. Replace it with the
shared `MemoryKind` literal.

Verified difference in the generated schema:

| Form | Generated schema |
| --- | --- |
| `str` + `pattern` | `{"type": "string", "pattern": "^(observation\|...)$"}` |
| `Literal[...]` | `{"type": "string", "enum": ["observation", ...]}` |

The literal is the published vocabulary. `SessionRuntime.save_memory` already
rejects any other value, so this moves an existing rule into the declared
contract rather than adding one.

### 3. Nonblank content

`MemoryRequest.content` is `str = Field(min_length=1, max_length=10000)`. A
whitespace-only value such as `"   \n\t "` validates against that schema today
and is rejected later by `SessionRuntime.save_memory`, which returns 422 with
`"Invalid memory"` through the route's `translate` context.

Use native constraints, not a custom validator:

```python
MemoryContent = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)]
```

Verified: this rejects the whitespace-only value. Stripping before storage also
means a saved memory has no leading or trailing whitespace, so search and
duplicate comparison are not skewed by padding.

### 4. Timestamp convention

Two conventions exist for the same column type:

| Model | Field | Type |
| --- | --- | --- |
| `SessionSummary` | `updated_at` | `datetime` |
| `TurnStatus` | `updated_at` | `str` |

Decision: warehouse and response timestamps are timezone-aware. New and changed
models use `AwareDatetime`. `TurnStatus.updated_at` moves to `AwareDatetime` here
so the memory work does not introduce a third spelling.

Consequences, all acceptable:

- `Store.turn_status` returns `updated_at.isoformat()`. PostgreSQL
  `TIMESTAMPTZ` yields an aware value, so production serializes as `+00:00`
  before and after; the model now validates it instead of passing it through.
- Serialization changes from pass-through to `...Z`-normalized output. The TUI
  renders this field as display text only (`f"Updated : {data.get('updated_at')}"`),
  so no client logic depends on the exact offset spelling.
- The API test fake returns the naive literal `"2026-10-20T00:00:00"`. It must
  move to an aware value in the same change. That is test-fixture drift, not a
  production behavior change.

`SessionSummary.updated_at` stays `datetime`. Migrating it to `AwareDatetime` is
a separate one-line change with its own fixtures; bundling it here would grow
the contract diff without serving the memory work. Record the decision, apply it
to the fields this program touches, and leave the rest.

## Out of scope

Named explicitly so the review does not expand:

- `Mode.mode`, `ModelSelection.role`, `ModelSelection.model`, `TurnStatus.status`,
  `SessionConfig.mode`, and `SessionConfig.models` remain untyped `str`/`dict`.
  They are real contract debt and unrelated to memory.
- `History.turns` and `TurnStatus.result` remain `dict[str, Any]`. A Turn record
  has evolved across versions and is intentionally open; typing it is a separate
  effort.
- No new route, no new table, no migration, no Store change.
- No change to `save_memory` validation, which already enforces these rules.

## Verification

- A saved whitespace-only memory is rejected with the same 422 the route returns
  today. An untyped kind is rejected by request validation as 422.
- The list response validates through `MemoryRead`; a row with an
  out-of-vocabulary `kind` fails loudly rather than reaching the client. The
  writer prevents such a row; manual SQL can create one, and failing is the
  intended behavior.
- Existing `MemoryRequest` callers that omit `kind`, `sources`, or `reviewed`
  are unaffected; defaults are unchanged.
- `make api-sync` then `make api-check`. The `openapi.yaml` diff should contain
  only the new `MemoryRead` schema and the `Memories` element reference, the
  `enum` replacing `pattern` for `kind`, and `format: date-time` on
  `TurnStatus.updated_at`. Anything else in that diff is an unintended contract
  change.
- Content has no schema diff: `strip_whitespace` is a runtime constraint, so
  `minLength` and `maxLength` stay as they are. This is the one case where the
  published contract does not describe the full rule, and it is the correct
  trade: a schema cannot express "nonblank after trimming". State it in the API
  reference rather than adding a regex that would reject legitimate embedded
  whitespace.
- Focused API tests, then `make check`.

## Risks

| Risk | Mitigation |
| --- | --- |
| A client sends content with meaningful padding. | Stripping changes stored bytes. Memory content is prose; the spec's own content rule already rejects blank input, so padding carries no meaning. State the change in the API reference. |
| A legacy row holds a `kind` outside the vocabulary. | It now fails response validation instead of passing through. Detect with one query before release: `SELECT DISTINCT kind FROM fleet_lite_memories`. |
| `TurnStatus.updated_at` fixtures are naive in several tests. | Update fixtures in the same change; the failure is immediate and local. |
| Someone extends this phase into a general schema cleanup. | The out-of-scope list is the review boundary. |

# PRD C — FastAPI contracts

**Status:** proposed. **Priority:** high. **Baseline:** [`61bea5095dbbc992db2aeb8049a05e079b0bda10`](https://github.com/Qredence/fleet-rlm/commit/61bea5095dbbc992db2aeb8049a05e079b0bda10), FastAPI 0.162.0 / Pydantic 2.13.5. [Index](README.md).

**Revision:** 2 - review amendments applied directly, 29 October 2026. This is a source-based document update, not a new repository audit or implementation. Evidence labels and baseline limitations are defined in [README](README.md#evidence-and-baseline).

## 1. Problem, users and evidence

Client authors should learn stable payload shapes and valid requests from OpenAPI rather than reverse-engineering handlers. Operators need sanitized, documented errors, and the terminal client needs the same completion contract as persisted/replayed Turns.

**current-source (input PRD):** request models already reject unknown fields and perform runtime validation. However, timeout constraints use a validator whose range is not fully published, workspace write XOR is enforced in a handler, experiment responses return generic dictionaries, and several history/context/result fields remain generic. [Schemas](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/api/schemas.py), [experiment routes](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/api/routes/experiments.py), [workspace routes](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/api/routes/workspace.py).

Turn streaming already uses native `EventSourceResponse` and `ServerSentEvent`, but the application JSON envelope is assembled dynamically by `Envelope.wrap`. [Events](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/api/events.py#L449-L478), [turn route](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/src/fleet_rlm/api/routes/turns.py#L74-L123). The generated [OpenAPI file](https://github.com/Qredence/fleet-rlm/blob/61bea5095dbbc992db2aeb8049a05e079b0bda10/openapi.yaml) describes SSE framing but does not precisely describe Fleet JSON inside `data`. The earlier review counted 37 paths, 45 operations and 45 components; treat these as baseline audit counts, not an acceptance quota.

## 2. Native-framework-first decision and rationale

Keep FastAPI's application factory, lifespan, dependencies, routers, response models, error handlers and SSE classes. Define stable response bodies in existing Pydantic models; explicitly validate Fleet JSON before passing it to native `ServerSentEvent`. A `response_model` annotation must not be assumed to validate arbitrary JSON that is already wrapped in an SSE object.

The default documentation solution is deliberately small: a valid `text/event-stream` response description under the existing declared OpenAPI version, concrete framed examples, and documented named payload models exercised by contract tests. Reuse the pinned generator's supported schema registration only where it works with the actual consumer. Do not require `x-fleet-item-schema`, a second schema generator, a general normalization pass, or an OpenAPI version change merely to describe this stream.

An additional machine-readable SSE extension is permitted only when a named consuming client demonstrably needs it, a consumer test verifies its interpretation, and the implementation remains in the existing schema/generator owners. Without that evidence, examples plus explicit payload validation are the completed scope, not a temporary failure to implement a custom protocol. [Native SSE documentation](https://fastapi.tiangolo.com/tutorial/server-sent-events/) remains the source reference from the supplied PRD; verify version-sensitive behavior at the implementation checkout.

## 3. Scope and non-goals

Scope: stable public request/response fields, experiment/setup operations identified by the supplied review, current terminal/live/replay contracts, bounded progress extensions, timeout/workspace constraints, sanitized error responses and deterministic, version-valid OpenAPI output. Inventory actual routes before editing; historical operation counts are not acceptance quotas.

Non-goals: endpoint redesign, authentication changes, an SDK generator, a schema package or domain-DTO hierarchy, HTTP imports in the engine/Store, custom SSE transport, a mandatory vendor extension, an OpenAPI upgrade for its own sake, or future D/F/G fields before those features exist. The API must continue not importing `fleet_rlm.optimize` merely to construct response types.

## 4. Affected code tree

```text
src/fleet_rlm/api/
  schemas.py
  events.py
  routes/{experiments,setup,turns,sessions,workspace}.py
src/fleet_rlm/{contracts,optimization_runs}.py  # shared stable vocabulary only
src/fleet_rlm/tui/{stream,experiments}.py      # only required compatibility consumers
scripts/openapi_contract.py                  # existing generation/checks; edit only for proven need
openapi.yaml
tests/api/{test_routes,test_turns,test_events,test_experiments,test_setup}.py
tests/lite/test_dspy_contracts.py
docs/reference/http-api.md
```

## 5. Requirements and implementation tasks

1. **C1 - Freeze and inventory contracts.** Capture representative successful/error HTTP bodies, live and replayed terminal frames, dynamic progress frames and historical rows. For each affected route record stable fields, null/absence semantics, actual status codes, sanitization and existing client use. Reuse neutral option/run vocabulary; B owns the canonical completion inventory rather than C inventing a second one.
2. **C2 - Type ordinary responses.** Define named run/list/detail/acknowledgment and manager-status models in `api/schemas.py`. Reuse stable neutral types when appropriate. Preserve supported fields during response filtering and document intentionally open bounded data. Historical readers must not acquire blanket `extra='forbid'` behavior that rejects old records; keep the normalization boundary identified by B.
3. **C3 - Express request constraints once.** Preserve timeout intent: absent/null selects configured default; integer `0` disables only Fleet's deadline; strict integers 10-3600 select a deadline. Reject bool, strings, floats, negatives and 1-9 according to the existing strict input policy. Prefer native constrained types; add a pre-validator only if a pinned behavior test proves the native declaration insufficient. Move workspace content XOR to request validation and describe the same rule in the request schema without duplicating unrelated validation layers. Empty strings may represent empty files; preserve absent-versus-empty semantics. Document a deliberate 400-to-422 change if moving validation changes the status.
4. **C4 - Validate SSE JSON without changing transport.** Use the existing envelope and B's canonical terminal data. Validate the complete wire object before framing it with native SSE. Preserve existing event names, IDs, sequence, timestamps, identity fields and supported flattened fields. Known terminal/done payloads must be explicit. A bounded catch-all for genuine dynamic progress must exclude reserved terminal event names so it cannot accept a malformed completion.
5. **C5 - Document only what consumers can use.** Start with the generated stream response and concrete validated frames in `docs/reference/http-api.md`. Use named payload models and existing supported component registration when useful; do not create synthetic components or patch an entire schema tree solely for aesthetics. Inspect emitted OpenAPI against its declared version. The input PRD reports a possible `itemSchema`/document-version mismatch: reproduce it first, then correct only the offending declaration through an existing owner. Do not mandate a vendor extension or relabel the document as another OpenAPI version. Any optional extension needs an identified consumer, a passing consumer test and a separate bounded rationale.
6. **C6 - Errors and consumer compatibility.** Document actual sanitized validation/not-found/conflict/unavailable responses before streaming begins. Once a 200 stream is open, errors remain events under the existing contract. Regenerate the checked-in document with current tooling and exercise existing client parsing. Later D/F/G fields enter through their own additive contract updates; do not preimplement future lifecycle states here.

Proposed timeout shape, subject to tests on the pinned Pydantic version:

```python
from typing import Annotated
from pydantic import Field

DisabledDeadline = Annotated[int, Field(strict=True, ge=0, le=0)]
BoundedDeadline = Annotated[int, Field(strict=True, ge=10, le=3600)]
DeadlineValue = DisabledDeadline | BoundedDeadline | None
```

This is a declaration sketch, not a newly verified implementation. Test bool, float, string and union behavior before deciding any custom validator is needed. Keep Python type strictness and JSON Schema numeric interpretation as separate assertions; do not claim every validator distinguishes JSON numeric spellings in the same way. Avoid an unrestricted `Literal[0]` branch unless tests establish the intended strictness.

Proposed SSE boundary, adapting the real envelope/model names:

```python
wire = Envelope.wrap(existing_event)  # existing identity/flattening/sequence policy
validated = FleetEnvelope.model_validate(wire)  # proposed model in api/schemas.py
yield ServerSentEvent(
    data=validated.model_dump(mode="json"),
    event=wire["event"],
    id=str(wire["seq"]),
)
```

The input is validated once at the API boundary, not converted into a new internal agent result hierarchy. Do not double-encode JSON. Contract tests must also ensure model serialization preserves supported optional/extra fields. Documentation should show an actual fixture rendered as `id:`, `event:` and `data:` lines with its terminating blank line, not represent the complete SSE stream as one JSON response.

The source PRD links the [OpenAPI 3.1 Media Type Object](https://spec.openapis.org/oas/v3.1.1.html#media-type-object) and [OpenAPI 3.2 Media Type Object](https://spec.openapis.org/oas/v3.2.1.html#media-type-object). These are reference material for implementation verification, not a directive to migrate versions or invent a parallel schema dialect.

## 6. Before / after flow

Before: request body -> Pydantic plus handler-only constraints -> service dictionaries -> generic response schema. Event dictionary -> dynamic envelope -> native SSE, with the application JSON underspecified.

After: request body -> native Pydantic boundary constraints -> the same service -> named response model. Canonical/dynamic event -> validated Fleet payload/envelope -> unchanged native SSE framing. Existing OpenAPI tooling and validated examples explain the public contract; historical readers use one explicit compatibility boundary. No extra documentation protocol is required.

## 7. Acceptance tests and measurable outcomes

| ID | Required scenario | Passing condition |
|---|---|---|
| C-T1 | Enumerate selected ordinary operations | Stable responses have named models and runtime validation; supported fields survive filtering; no generic-object placeholder for inventoried experiment/setup responses |
| C-T2 | Timeout boundary matrix | Absent/null/0/10/3600 accepted; bool/float/string/-1/1/9/3601 handled according to declared strict policy; Python and JSON validation checks reported separately |
| C-T3 | Workspace content cases | Exactly one supported content field accepted, including empty content; neither/both rejected; any status change documented |
| C-T4 | Actual terminal/done/dynamic SSE frames | JSON validates before framing; malformed known terminal cannot take generic progress branch; `id == str(seq)` and existing order preserved |
| C-T5 | Historical and current rows | Legacy reads remain compatible, current writes are valid, no supported field disappears |
| C-T6 | Failures before/after stream starts | Sanitized documented HTTP errors before start; existing failure-event semantics after start; no raw traceback/secret exposure |
| C-T7 | Generated OpenAPI and examples | Document validates against its declared version; references resolve; examples match real validated frames; generation is deterministic and API does not import optimizer |
| C-T8 | Existing consumers and any optional extension | Supported clients parse unchanged frames; no extension added without a named consumer and an interpretation test |

Run focused API/import tests, `make api-sync`, `make api-check`, `make check-docs` and the repository's required `make check`. Report coverage of the inventoried operations, accepted/rejected boundary cases, old/new client fixtures and schema-reference validity. Do not measure success by number of generated components or number of custom OpenAPI transformations.

## 8. Migration and rollback

B's canonical field inventory is the prerequisite for strict terminal schemas. Ordinary HTTP models can be developed independently but must preserve frozen wire fixtures. Deploy historical reader compatibility before strict consumers. Do not add speculative F/G states merely to reserve names; integrate them when those features ship.

No SQL migration is expected for C. Preserve historical JSON and native program state. Roll back runtime models/validation and generated OpenAPI together; keep readers for any additive fields already written. If response filtering accidentally removes a supported field, correct it before release rather than redefining old clients as invalid. A documented status change is a compatibility decision, not an incidental side effect.

## 9. Risks, dependencies and implementation gates

Strict models can reveal undocumented history variants; the frozen fixtures and B inventory are the baseline. Native SSE framing and validation of the JSON inside it are different responsibilities. The generator and consumers may not interpret every JSON Schema/SSE feature identically, so verify the smallest supported contract rather than introducing speculative normalization.

B supplies terminal semantics. D supplies stable progress ownership; F later supplies cleanup/publication facts; G optionally adds no-change outcome. C does not require those future implementations to type the current API. New custom schema machinery requires an observed native limitation and a concrete consumer need, documented in the acceptance report.

## 10. Codex-ready checklist

### Implementation evidence (30 October 2026)

Implementation branch: `codex/fastapi-contracts`, starting at `5fd2af6ba`.
Validation below preceded the implementation commit; the PR identifies the final commit.
FastAPI 0.162.0 and Pydantic 2.13.5 remain unchanged. The only added dependency
is the development-only OpenAPI specification validator and its dependencies.

Named experiment/list/detail/acknowledgment and setup manager-status responses
replace generic response declarations. Native strict deadline constraints replace
the custom timeout validator; workspace XOR moves from the handler into its
request model, retaining the existing sanitized 422 response. Unknown historical
result fields, explicit nulls, and omitted fields remain readable without a SQL
migration or backfill.

SSE validation lives in `api/stream_contracts.py` to keep transport-specific models
out of the engine and Store. Native framing and existing flattened fields remain.
Malformed reserved events cannot enter the dynamic activity branch. Rejected
envelopes produce a generic exception without embedding submitted payload data;
stream delivery can terminate, and committed results remain recoverable by the
existing idempotency path. This does not add a recovery service or a second outcome.

The pinned generator emits OpenAPI 3.2 `itemSchema` under the application's 3.1.0
declaration. A bounded application schema hook corrects only SSE media declarations,
including setup errors that occur before streaming and return JSON. There is no
vendor extension or general normalization pass. The exported and served documents
share this hook; the existing generation/check command validates the specification.

C-T1–C-T8 coverage includes named response filtering, Python/JSON Schema timeout
matrices (JSON Schema accepts integer-valued `10.0`; strict Python rejects floats),
workspace XOR/empty files, current and historical terminal data, malformed terminals,
native framing/TUI consumption, sanitized errors, and schema validity/determinism.
Validation uses offline fakes and local fixtures; no live provider, Daytona, or
production database run was performed. Roll back response models, SSE validation,
and generated documentation together, retaining historical readers and all stored
results. No new persistent state or lifecycle fields are introduced.

Final offline validation: `make check` passed (1,756 tests passed, one skipped;
83% total coverage), including lint, formatting, types, documentation links,
OpenAPI validation/freshness, and source/wheel builds. `make api-sync` and
`git diff --check` also passed. The unchanged runtime dependency pins and scoped
handler-validator deletion above distinguish native reuse from added API models;
no runtime ownership code was relocated.

Subsequent authorized browser smoke test: `uv run fleet web` initially reused an
older API; verification instead used a fresh `fleet serve --port 8002` process
and `fleet web --url http://127.0.0.1:8002 --port 8081`. Messages were entered
through the browser terminal using agent-browser, not submitted by a test HTTP
client. Session `sess_web_d6aa9fcb9b7d` completed both Turns:

- Direct reply: Turn `b90ca1abc22c4014ae0fa94303ae564a`, trace
  `tr-3760052a08a21833be2a0038c728b547`; exact requested response displayed.
- Python calculation: Turn `70a3ff31c3314fe7846feeba5d929cd2`, trace
  `tr-828a2fde7d6fc0b77b05c1e699199822`; Flex/child execution returned `338350`.
  One generated SUBMIT call omitted a required field; the model recovered, and
  the persisted record correctly reports one sandbox error.

Both durable status reads report `completed` / `turn_complete`, with
`cleanup: not_required`. Browser page errors were empty; the web access log had
a missing favicon (404). This verifies these live paths, not every endpoint or
failure mode. No optimizer experiment or benchmark was started by this check.

- [x] Freeze existing responses/frames and inventory operations, historical variants and actual errors.
- [x] Reuse native Pydantic constraints and existing type owners; preserve the engine/Store boundary.
- [x] Validate SSE JSON explicitly and retain native framing and supported flattened fields.
- [x] Document real framed examples; keep the existing OpenAPI version and generator by default.
- [x] Add no vendor extension or generator normalization without demonstrated need and consumer tests.
- [x] Pass C-T1-C-T8 and repository gates, including historical/import-boundary tests.
- [x] Record intentional wire/status changes, added models, deleted handler validation and rollback compatibility following the [common acceptance report](README.md#acceptance-report) above.

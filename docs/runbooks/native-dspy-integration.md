# Native DSPy integration implementation report

PRD A is implemented with a temporary, fixture-backed native-first parser.
Baseline: `61bea5095dbbc992db2aeb8049a05e079b0bda10`; DSPy remains pinned to
3.4.1 (GEPA 0.1.4, FastAPI 0.162.0, Pydantic 2.13.5 unchanged). The implementation
is recorded on branch `fix/native-dspy-integration`. Unrelated untracked `.codex/`
and the supplied PRD package remain outside the implementation commit.
No configuration, registry, signature, database, or native program
state schema was added.

## Native policy and bounded compatibility

`FleetRLMChatAdapter` now inherits native ChatAdapter construction, sync/async
execution and JSON recovery without overrides. Fleet's corrective request,
retry configuration, signature rewriting, disabled JSON fallback, and unconditional
thinking-delimiter stripping are removed. Native parsing always runs first.
Valid native responses return unchanged, including literal `</think>` in fields
and code fences containing inline native markers.

The recorded gateway corpus demonstrates two required exceptions for exactly
string `reasoning`/`code` outputs: prose followed by one nonempty `python` fence,
and leaked reasoning ending in `</think>` immediately joined to a native action
header. The latter is repaired by inserting a newline without deleting text. Only after native rejection does the parser accept those
formats. Raw fences with multiple/extra closings, empty code, or trailing prose
are rejected; prior tolerance for language aliases, duplicate closings and
angle-wrapped completed markers is intentionally removed. Missing fields and
other malformed output use native JSON recovery. Provider errors propagate.

The existing engine Flex/RLM and explicit-child contexts select this subclass;
direct execution retains its ambient adapter policy. Tests assert context
selection/restoration and inheritance of native execution, in addition to parsing.
Existing live helpers still import the same class and inherit the new policy.

Owner: runtime integration maintainers. Remove the raw-fence exception when
recordings from the configured gateway consistently emit native fields for
`reasoning`/`code`; remove the inline-leak exception when recordings consistently
separate reasoning from content or place native headers on their own lines.
For either removal, the replacement recorded corpus for the versioned gateway
must cover all affected routes with 100% native parsing; explicitly retire the
obsolete format, replay all retained fixtures, and pass a route canary. No additional retry is allowed in the
meantime. The adapter module remains fingerprinted by `optimization_code_digest`;
new approvals must reevaluate the changed identity. The adapter-only diff is
independently reviewable with
`git diff 61bea5095dbbc992db2aeb8049a05e079b0bda10 -- src/fleet_rlm/adapters.py`,
excluding context/batch changes. Historical evidence remains
readable but does not measure this parser.

## Frozen corpus and acceptance

[`gateway_adapter_replies.json`](../../tests/fixtures/gateway_adapter_replies.json)
freezes eleven parser attempts from five browser-initiated traces of the configured
`openai/system.ai.deepseek-v4-1-flash-1017` gateway. Each case has a stable ID,
route, trace/span IDs, capture date, signature output fields, exact completion,
and explicit expected fields or invalid status. Prompts, headers, credentials,
configuration and shared-memory inputs are excluded; Sonar secrets scanning
passes. Provenance-backed fixtures cover direct output, RLM actions and Flex's
native child actions. This is coverage of the configured gateway, not a claim
about every provider or typed backend.

Acceptance was declared before replay: preserve all valid native fields/content,
retain required recorded formats without weakening expected output fields, and
observe native recovery through the actual Fleet budget wrapper. Expected native
fields are protocol-derived, independently of legacy output; the leaked-header
fixture expects the actual reasoning field rather than legacy protocol markers.

The corpus contains six valid native cases, three required compatibility cases,
and two invalid attempts. Its SHA-256 is
`45b0e4c23a197cbe47f5a6bb94666d947743ea781488715a3ddb1afe1078e41a`.
The production parser accepts all nine required
replies in one observable LM call, on sync and async paths. Plain native parsing
accepts six and recovers the three compatibility cases through a scripted JSON
response in two calls. Both policies reject invalid attempts after two calls.
The legacy parser preserved only one of six valid native replies exactly and
accepted the two compatibility replies while corrupting the leaked-header
reasoning. These parse-only comparisons measure no latency or provider cost.

Seven additional synthetic valid fixtures cover ordinary fields, literal thinking
delimiters, fenced code, integers/lists and reasoning prefixes. Native and
production adapters preserve all seven in both sync/async paths. Malformed
fences, missing fields, exhausted budgets, deadline/cancellation and provider
errors have separate regressions. Native fallback cannot bypass Fleet's manager
reservation; a blocked second reservation permits only the first observable
request. The production tracing test checks per-call and aggregate token receipts
across a native fallback. Logical units, observed LM calls and transport retries
remain distinct; transport-level retries and costs are unknown.

The adapter shrank from 104 to 49 lines (55 net lines removed); its four
construction/retry/signature overrides were deleted rather than relocated.
Compatibility stays in the same module and ownership scope.

## Context and prepaid batching

Injected semantic batching remains native. Only the outer fallback retains a
bounded executor, one aggregate precharge, and input-ordered futures with a
separate full Context copy for every worker. Simultaneous Turn tests check
sentinels, active managers, callbacks and usage trackers, exception propagation,
and native delegation without invoking the fallback executor.

Skill and item batches retain native `Predict.batch`, thread bounds and TypeSafe
usage bridging. Both pass `timeout=0`. Inspection of pinned DSPy established the
120-second default straggler policy; a deterministic slow-batch regression with
a positive resubmission control exercises the native scheduler. Fleet deadlines
remain independent, and optional Skill failures retain the catalog.

## Acceptance mapping

| PRD test | Evidence |
| --- | --- |
| A-T1 | Seven valid synthetic fixtures, native and production adapters, sync/async |
| A-T2 | Recorded malformed attempts, provider failure and native JSON recovery tests |
| A-T3 | Simultaneous Turn context/manager/callback/usage isolation regression |
| A-T4 | Both prepaid call-site spies plus native slow-batch test and positive control |
| A-T5 | Metered fallback limits, worker errors, optional Skill catalog/budget tests |
| A-T6 | Injected batch delegates once without constructing the fallback executor |
| A-T7 | Frozen gateway corpus: six native, three compatibility and two invalid attempts |
| A-T8 | Actual Fleet manager reservations, facade copies and per-request usage tracing |

## Validation and rollout

`make check` passed after migration: 1,613 tests passed, one skipped, aggregate
coverage 83%, adapter statement/branch coverage 100%. Ruff lint/format, ty,
documentation links, OpenAPI contract and source/wheel builds passed. A focused
follow-up also checks one aggregate item-decision precharge through the public
batch helper. Sonar secrets scans and `git diff --check` pass. Post-migration
canary results are recorded below. Ordinary tests use recorded/scripted replies and need no
live services. No migration is necessary. Roll back adapter behavior independently
from context/batch changes; do not restore valid-field corruption. Keep the
configured-gateway recordings and require compatible evidence for future approvals.

The following live evidence predates the adapter migration and supplied its
recorded corpus; it is not a post-migration test report.

## Authorized web verification, 29 October 2026

At the user's explicit request, launched `uv run fleet web` and sent all four
tasks through its browser terminal. The default API on port 8000 predated the
patch, so verification used a fresh `uv run fleet serve --host 127.0.0.1
--port 8002 --mlflow local` process and `uv run fleet web --url
http://127.0.0.1:8002 --session dspy-verify-20261029`. The original API was left
running. Each task used real configured providers, Daytona, the configured
Store, and local MLflow. No completed task was blindly rerun.

| Check | Result | Logical budget units | Recorded Turn seconds |
| --- | --- | --- | --- |
| Hybrid selecting direct | Exact `FLEET_WEB_DIRECT_OK`; persisted | 2 (1 route, 1 generation) | 22.842 |
| Strict RLM arithmetic and literal marker | Computed 385; literal marker requirement failed: observed LM output already contained `</>` | 7 (5 Skill decisions, 2 generation) | 13.768 |
| Strict Flex batch queries and item decisions | Ordered `ALPHA`, `BETA`; apple → fruit, carrot → vegetable; persisted | 13 (5 Skill decisions, 2 item decisions, 6 generation/query) | 33.086 |
| Strict RLM runtime string integrity | Constructed marker from character codes, asserted bytes, returned exact `FLEET_WEB_LITERAL_OK </think>`; persisted | 7 (5 Skill decisions, 2 generation) | 14.523 |

All four Turns completed, with zero sandbox errors and MLflow `OK` status.
After reloading the browser, `/history` recovered the committed answers,
including the exact runtime-constructed marker. Browser error collection was empty.
Operational completion does not make the first literal-marker assertion pass.
The malformed marker is present in the first RLM's `LM.__call__` output before
adapter parsing; this observation cannot distinguish model behavior from gateway
processing. The follow-up deliberately constructs the bytes in Daytona instead
of asking the model to spell the marker again. Successful runtime-string
delivery does not resolve the existing adapter's offline parsing failures.

The Skill and item batches ran against TypeSafe `jev-1.14.0`; the item results
retained backend, raw choice, confidence, and probability evidence. Flex used its
native nested RLM and one child, so its semantic batching exercised the injected
native path. Direct outer-Flex fallback ContextVar propagation remains proven by
offline regressions, not this canary. Observable generative `LM.__call__` span
counts were respectively 1, 2, 6, and 2. These exclude TypeSafe decision requests
and are not transport-level request counts. No cost or comparative performance
claim is inferred from the recorded durations.

Session: `dspy-verify-20261029`. Durable identifiers:

| Check | Turn ID | MLflow trace ID |
| --- | --- | --- |
| Direct | `b598ccc129744098a44e6f4e67a85124` | `tr-3b93962b0658dfc872fa22477ae21085` |
| RLM arithmetic | `c59364671ce747869b9178c221f6fda4` | `tr-33370765356dc75ceeedf8a0f293287d` |
| Flex batching | `acecc7c4de74410ea4eeb144a4216d4a` | `tr-afa68337820131f038286d9cadf6ff54` |
| RLM runtime string | `adbc3ef942fb4de596713007b7954774` | `tr-8d24ad71cc525a569e770e4d87ca4acb` |

These narrow checks do not constitute the representative, sanitized, frozen
gateway corpus required to qualify adapter removal. No adapter selection or
runtime implementation changed during this verification.


## Post-migration web canary

Fresh API: `127.0.0.1:8003`; web: `127.0.0.1:8081`; Session
`dspy-native-20261029`. The direct check returned exact `NATIVE_DIRECT_OK`.
The first RLM attempt failed in adapter parsing before any task Python ran:
Turn `f874d05ecdfa4a69b13b7626c34c63c6`, trace
`tr-543f5f79fcb36616420a0b9c3987e592`. Its persisted cleanup is `stopped`.
That trace supplied fixture `rlm-inline-code-11`, extending the recorded
inline-boundary rule from the reasoning header to either action header.
Native JSON recovery was attempted once and failed; no Fleet corrective request
was made. The failed request was recovered and inspected before retrying.

After the fix, a restarted API ran an explicit child with bounded resources.
It computed `6*7` in Daytona and returned `CHILD_NATIVE_OK 42`. The root verified
the runtime marker bytes and persisted
`NATIVE_RLM_CHILD_OK marker='</think>' findings=CHILD_NATIVE_OK 42`.
The Turn consumed 10 logical units (five Skill decisions and five generation
calls), with five observable `LM.__call__` spans, no JSON fallback, zero sandbox
errors, and an `OK` trace. Its 137.958 seconds include child provisioning and
execution and establish no comparative performance claim.

| Check | Turn ID | Trace ID |
| --- | --- | --- |
| Direct | `d28bbb07222a40b1a5644d6643382b23` | `tr-91d7209470011deb1b6085752514c4a8` |
| RLM and explicit child after fix | `bc4cf15bd9b246a2b7527ab641be0d93` | `tr-6673f3b35681d76e24f92417d35d2eaa` |
| Flex after fix | `a09bab519fc74ac6a8c07fc13aba39fd` | `tr-b16f3d713a12db783c75da94f52d4c30` |

The final Flex canary returned exact
`NATIVE_FLEX_OK replies=['ALPHA', 'BETA'] decisions=['fruit', 'vegetable']`.
It consumed 11 logical units (five Skill decisions, two item decisions and four
generation/query calls), with four observable LM call spans, no JSON fallback,
zero sandbox errors and a 113.893-second persisted duration including native
child provisioning. As before, injected native semantic batching was exercised;
the outer fallback context guarantee remains an offline regression result.
Browser errors were empty. The verification web interface remains available
on port 8081, with the original server on port 8000 untouched.
All three successful post-migration traces reached MLflow `OK`. PRD A's
implementation checklist is complete under its fixture-backed compatibility
option; adapter deletion is a future removal gate rather than a pending task
in this migration.

Adapter deletion remains intentionally unqualified: the recorded gateway still
requires the documented parsing exceptions. This is the PRD's permitted bounded
compatibility outcome, with an owner and removal conditions, rather than an
unfinished native execution/recovery migration. Broader provider compatibility,
production quality, transport-level retries, cost, and measured gains remain
outside this canary's evidence.

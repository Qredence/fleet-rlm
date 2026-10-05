# Retained benchmark material

This record preserves the non-executable case and scoring contracts from the
benchmark runners retired on 2026-10-05. The preserved JSON files remain data
fixtures; no benchmark command, runner, or provider workflow is retained here.

## Latency quality cases

The retired quality dataset had five records. The text below preserves the
queries and complete expectation fields used for evaluation. Each expected
answer was judged with correctness and evidence-coverage scorers; the first
record also supplied the deterministic reference heuristic.

### Case A: contract renewal

Query:

> Analyze the following evidence and decide whether the customer can prevent renewal of OF-7781 effective 2025-04-01. Resolve conflicts by authority and effective date. Explain the controlling deadline, receipt versus sending date, conflicting sources, and residual uncertainty.
>
> A1 Master Agreement: written non-renewal notice must be received at least 30 calendar days before renewal. The notice period begins when the other party receives notice.
>
> A2 Amendment 2: contracts executed after 2024-03-01 require 45 days' notice.
>
> A3 OF-7781 was executed 2024-01-15 and does not incorporate Amendment 2.
>
> A4 OF-7781 renews 2025-04-01 unless 30 calendar days' written notice is received.
>
> A5 Account manager email says 45 days are required; it is advice, not an amendment.
>
> A6 Legal memo says Amendment 2 does not govern OF-7781.
>
> A7 CRM note says no written notice was found.
>
> A8 Mailbox metadata records the customer's written notice sent 2025-02-27 and received 2025-02-28.
>
> A9 Internal policy summary says 45 days but identifies its source system as unknown.
>
> A10 Internal policy summaries are informational and non-binding.
>
> Use Python for date arithmetic. Use selected independent sub-LM comparisons only if useful. End with exactly one typed SUBMIT.

Expectations:

```text
expected_response: Yes. The 30-day receipt deadline is 2025-03-02; receipt on 2025-02-28 was timely. A1, A3, A4, A6, A8, and A10 control or corroborate; A2 does not apply and A5, A7, and A9 are overridden or non-binding.
required_evidence: [A1, A3, A4, A6, A8, A10]
required_uncertainty: Delivery validity remains conditional on the mailbox evidence being authentic and contractually valid.
forbidden_claims: [45-day rule controls, sending date alone controls]
```

### Case B: notice deadline

Query: `B1 requires receipt 20 days before 2026-01-31. B2 shows sending on 2026-01-10. B3 shows receipt on 2026-01-12. Determine timeliness and cite the controlling event.`

Expectations:

```text
expected_response: Not timely: the receipt deadline was 2026-01-11 and receipt on 2026-01-12 was one day late.
required_evidence: [B1, B3]
required_uncertainty: None beyond the stated dates.
forbidden_claims: [sending date controls, notice was timely]
```

### Case C: approval threshold

Query: `C1 signed policy requires manager approval above $50,000. C2 draft FAQ says approval is optional. C3 board resolution makes C1 binding. C4 request is $72,000. Decide whether approval is required and resolve the conflict.`

Expectations:

```text
expected_response: Approval is required because the binding C1/C3 chain controls and $72,000 exceeds $50,000; draft C2 is non-binding.
required_evidence: [C1, C3, C4]
required_uncertainty: Conditional on C1 and C3 remaining in force.
forbidden_claims: [C2 controls, approval is optional]
```

### Case D: approved total

Query: `D1 contains approved amounts 120, 80, and 45. D2 contains draft amounts 900 and 700 that must be excluded. D3 adds an approved credit of -15. Compute the approved net total and identify excluded evidence.`

Expectations:

```text
expected_response: The approved net total is 230: 120 + 80 + 45 - 15. D2's draft values are excluded.
required_evidence: [D1, D2, D3]
required_uncertainty: None beyond the classification supplied.
forbidden_claims: [1830, include D2]
```

### Case E: access authorization

Query: `E1 says access is allowed only after security approval. E2 records approval requested but no decision. E3 is an unverified chat saying approval probably happened. Decide whether access is currently authorized.`

Expectations:

```text
expected_response: The record is insufficient to establish authorization. E1 requires approval, E2 has no decision, and E3 is unverified.
required_evidence: [E1, E2, E3]
required_uncertainty: Authorization is conditional on obtaining verified approval evidence.
forbidden_claims: [access is authorized, E3 proves approval]
```

The frozen reference heuristic for the renewal case required an affirmative
answer; deadline 2025-03-02; receipt date 2025-02-28; a timely conclusion;
explicitly rejecting applicability of A2; no forbidden claim; citations to
A1/A3/A4/A6/A8/A10; and discussion of A2/A5/A7/A9. It separately recorded
whether uncertainty about authenticity and contractual delivery validity was
explicit and whether the task ended with typed `SUBMIT`.

## Judge and scorer policy

The canonical judge names were `correctness` and `evidence_coverage`. Both used
the configured MLflow judge model with `temperature=0`, `reasoning_effort=low`,
and `max_tokens=1024`, returning booleans. Their descriptions and prompt
templates were:

```text
correctness description: Check whether the response reaches the expected conclusion and preserves the expected material facts without contradiction.
correctness instructions: Compare {{ outputs }} with {{ expectations }}. Set result true iff the response matches expected_response without a material contradiction. Accept equivalent wording and use no outside knowledge.

evidence_coverage description: Check whether the response materially uses every required evidence item, preserves the required uncertainty, and avoids the forbidden claims for the evaluation case.
evidence_coverage instructions: Check {{ outputs }} against {{ expectations }}. Set result true iff every required_evidence item materially supports the conclusion, required_uncertainty is preserved, and no forbidden_claims are asserted. Accept equivalent wording and use no outside knowledge.
```

The supplemental scorer names were `response_present`, `tool_evidence_used`,
`guidelines`, and `retrieval_groundedness`. `response_present` meant a
non-empty answer. `tool_evidence_used` passed only when a trace contained TOOL
span output and that output included every required evidence identifier;
missing trace or required evidence failed closed. The default guideline was:
“The response must be concise, stay within the requested scope, and avoid
unsupported claims.” The other two names delegated to MLflow's built-in
Guidelines and RetrievalGroundedness scorers.

## Corpus-chain case and expected report

The corpus workload represented 500,000 newline-delimited records. It used
seeds 0 and 1, with 132 write records, payload value `65535`, lookup field
`N333/SYNC/S777`, write key `WRITE/S042`, terminal claim `S555`, and computed
answer `S444`. The challenge follows five linked indices, resolves the unique
lookup match, counts payload records, and checks the write-record count and
last index against the actual attachment. Reports contained exactly `path`
(five integer indices), `lookup_matches` (one index), `payload_count`,
`payload_last_index`, `computed_answer`, `terminal_claim`, and boolean
`terminal_discrepancy`.

For the frozen 500,000-entry seed-0 case, the expected report was:

```json
{"path":[40000,187653,287653,350500,499999],"lookup_matches":[350500],"payload_count":5,"payload_last_index":499999,"computed_answer":"S444","terminal_claim":"S555","terminal_discrepancy":true}
```

The terminal record intentionally claims `S555` while the linked computation
resolves to `S444`; the report must preserve that discrepancy. The second seed
was generated deterministically from Python's `random.Random(1)` using four
sorted path indices and four sorted payload indices, each ending at index
499999. Its expected report was:

```json
{"path":[80445,308426,430617,454299,499999],"lookup_matches":[454299],"payload_count":5,"payload_last_index":499999,"computed_answer":"S444","terminal_claim":"S555","terminal_discrepancy":true}
```

Non-terminal write indices were chosen from the final 130 positions
where possible, with the lookup and terminal indices always included.

Execution evidence also required host Attachment access, an executable
`read_attachment` call, `json.dumps` serialization, non-empty RLM output, and
typed `FINAL submitted` output evidence.

## Phase 6 case set

[`phase6_evaluation_cases.json`](../../../../scripts/benchmarks/phase6_evaluation_cases.json)
preserves the six frozen input/rubric records and their SHA-256 hashes. Each
rubric shared the policy that unobserved evidence, coverage, usage, staging,
cleanup, or failure status remains unknown and is never treated as success.

| Family | Expected-output contract |
| --- | --- |
| `sparse_retrieval` | Identify the approval requirement, distinguish request from decision, cite the decisive source, and state uncertainty because verified approval is absent. |
| `exhaustive_semantic_aggregation` | Include approved 120, 80, 45, and -15; exclude drafts 900 and 700; compute 230; cite all included/excluded rows. |
| `cross_document_reconciliation` | Apply agreement authority and effective dates; use receipt, not sending, for the 30-day deadline; reach a supported timeliness result and cite sources with residual uncertainty. |
| `repository_investigation` | Name source-backed path and symbol, explain actual enforcement, cite the exercising test, and avoid claims unsupported by repository evidence. |
| `decomposable_reasoning` | Calculate team capacities 9 and 7; treat work items 5, 4, and 6 as indivisible; provide a feasible allocation with arithmetic and citations. |
| `multi_turn_continuation` | Preserve valid prior facts, apply the dated correction only within scope, explain what changed and stayed the same, cite old/new evidence, and disclose missing context. |

## Oolong scoring contract

The retired adapter vendored the official helper behavior from
[`abertsch72/oolong`](https://github.com/abertsch72/oolong),
`src/eval/eval_helpers.py` at pinned revision
`5c8113ee360957cff010d27310e844630216b21d`:
[`source at pinned revision`](https://github.com/abertsch72/oolong/blob/5c8113ee360957cff010d27310e844630216b21d/src/eval/eval_helpers.py).
The `synth` Hugging Face dataset revision was
`f0d59eaf0febf130664cfceb710436c8e3216b2b`; `real` was
`6bc9ef04866fcf005c9749b70649be69dd37fffb`. `synth` used the default dataset
configuration, while `real` used config `dnd`.

For the synthetic split, the parser takes the text after the last colon,
trims it, and removes `*`, `[` and `]`; without a colon, strings shorter than
20 characters are kept and longer strings reduce to their last word. It
recognizes “more common”, “less common”, and “same frequency”. Parse confidence
starts at `low`; a colon parse is `med`, becomes `high` when the original
answer contains `User:`, `Answer:`, `Date:`, or `Label`, and becomes `vhigh`
when the extracted candidate is shorter than 20 characters (this last rule
overrides `high`). The three recognized categorical phrases are normalized to
their canonical lowercase phrase when present. Gold values are parsed from the
first item of `ast.literal_eval(answer)` unless the serialized answer contains
`datetime`, in which case the expected `[datetime.date(Y, M, D)]` form is
parsed with `datetime.strptime`. Exact parsed string equality scores 1; a
recognized categorical answer also scores 1 when it occurs in the string form
of gold. Numeric answers score `0.75 ** absolute_error`; date answers score 1
only for an equal parsed date. A failed numeric/date parse resets confidence
to `low` and leaves score 0. Parse confidence is recorded separately and does
not otherwise change the score.

For the real DND split, the parser extracts `\boxed{...}` or
`\boxed{\text{...}}`; missing wrapper has low parse confidence. Integers score
`0.75 ** absolute_error`, strings compare case-insensitively after trimming,
and list answers score `len(set(gold) & set(prediction)) / len(gold)` (or 0
when gold is empty). Gold and extracted answers parse as integers first,
comma-separated trimmed strings second, and otherwise remain strings. A
successful box extraction has `high` confidence; failure to find a box has
`low` confidence. The prompt format requires the final answer inside
`\boxed{}`. The retained offline row is
[`fixture_validation_row.json`](../../../../scripts/benchmarks/oolong/fixture_validation_row.json).

The curated routing cases remain in
`src/fleet_rlm/optimization/routing.py`; the runner and its execution path were
retired while the source policy cases remain available to the application.

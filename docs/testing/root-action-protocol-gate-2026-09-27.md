# Root action protocol gate — 2026-09-27

The requested register-program oracle is `A=7, B=7, C=8, D=12, E=2`, six
true conditions, and D as the unique largest register. The exact trace input,
including its trailing text, and the clean input are retained in
`tests/fixtures/rlm/`.

## Bounded live campaign

The selected Databricks CLI profile was `localcode`. The retired-endpoint gate
admitted one Turn and stopped on failure. After the user approved the owned
Unity Catalog service, a fresh gate admitted three serial full Turns within
20 minutes, with at most one active Daytona Sandbox. Across both gates, each
admission held a $1 accounting reservation against a combined $5 limit.
Provider-reported cost was unavailable, so all four reservations are treated
as fully charged for accounting ($4 total); this is not a provider-enforced spending cap
or proof of actual spend. No further Turn was admitted.

| Profile and input | MLflow trace | Outcome | Root calls and tokens | Parse repairs | Settlement and cleanup |
| --- | --- | --- | --- | --- | --- |
| `daytona-native-databricks`, exact input, retired endpoint ID | `tr-bbf8f42c60279b4f433fd3cb07ef5504` | Failed before an action; gateway returned 403. A direct follow-up identified the legacy ID as no longer available. | 1 failed call; usage unavailable | Not reached | Settlement span OK; Turn cleanup error; exact Sandbox deleted and absence verified. |
| `daytona-native-databricks`, exact input, owned model service | `tr-50ded26585a6ea61cc9f9024dcbde4f2` | Oracle answer, typed `SUBMIT`, trace OK. | 3 calls; 22,081 input and 11,672 output tokens | 0 | Both spans OK; persisted Session Sandbox deleted and absence verified. |
| `daytona-native-databricks`, clean input, owned model service | `tr-8a6b48e0dd19cb6b671d2e520ab13292` | Oracle answer, typed `SUBMIT`, trace OK. | 4 calls; 29,755 input and 14,381 output tokens | 0 | Both spans OK; persisted Session Sandbox deleted and absence verified. |
| `daytona-recursive-databricks`, exact input, owned model service | `tr-350f5e31c06f6c01d8b0456b2331780f` | Oracle answer, typed `SUBMIT`, trace OK. No child RLM was invoked. | 4 calls; 33,883 input and 8,156 output tokens | 0 | Both spans OK; persisted Session Sandbox deleted and absence verified. |

The three model-service traces each record `response_format` in root-call
kwargs. The native request test confirms DSPy sends `json_schema` for this
exact service. A direct `localcode` provider probe of the service accepted
`json_schema` and returned the requested object. The service routes 100% to
`system.ai.databricks-deepseek-v4-1-flash` through the existing
`/ai-gateway/mlflow/v1` base.

The clean-case and recursive-case answers used different wording but matched
every oracle value and the true-condition count. The recursive profile was
exercised as a full Turn; this workload did not call a child RLM. The three
successful service Turns consumed 85,719 input and 34,209 output tokens in
total. No format or quota failure appeared in those Turns.

## Decision

Alibaba remains the committed local default. The provider did not report
actual cost in Fleet usage or MLflow traces, so the cost gate cannot be
confirmed. The retired-ID failure is also part of this campaign record.
The managed and opt-in local Databricks profiles now name the owned Unity
Catalog model service; selecting those profiles remains explicit.

The Alibaba fallback accepted `json_object` and returned a parseable expected
object in a direct provider probe. It rejected `json_schema`. Fleet requests
only `json_object` for the exact Alibaba model on its DashScope route and
retains strict action parsing and bounded re-asks.

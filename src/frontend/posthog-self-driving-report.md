# PostHog Self-driving Setup Report

**Project:** Qredence (ID 15008)  
**Date:** 2026-07-09  
**Inbox:** https://eu.posthog.com/project/15008/inbox

## Summary

PostHog Self-driving has been configured for fleet-rlm. Session Replay, Error Tracking, Support (Conversations), GitHub Issues, and Linear are now wired as signal sources; the scout troop is running with four active scouts including a custom scout for AI agent run pipeline health. Findings will start appearing in the Self-driving inbox at https://eu.posthog.com/project/15008/inbox within ~30 minutes.

---

## AI Data Processing

**Status:** Approved. Organization-level AI data processing approval was confirmed before this run started.

---

## GitHub

**Status:** Connected during this run.

| Field | Value |
|---|---|
| Integration ID | 69928 |
| Display name | Qredence |
| Connected by | zachary@qredence.ai |
| Connected at | 2026-07-09T07:05:50Z |

---

## Products Enabled

The `products-enable` tool was not available in this MCP deployment. Server-side enables for all three products need to be applied manually from project settings (see Follow-ups). The `posthog.init` in `src/main.tsx` is clean — no `disable_session_recording` or `capture_exceptions: false` override — so server enables will take effect automatically once set.

| Product | Status | Notes |
|---|---|---|
| Session Replay | **Manual action required** | posthog.init is clean — no client-side override |
| Error Tracking | **Manual action required** | `captureException` is already instrumented in `src/lib/telemetry/client.ts` |
| Support (Conversations) | **Manual action required** | Tickets arrive only after an inbound channel (email / inbox / Slack) is also connected |

---

## Signal Sources

All sources were newly created (no prior configuration existed).

| source_product | source_type | Action | Config ID |
|---|---|---|---|
| `error_tracking` | `issue_created` | **Enabled** | `019f45b3-7c27-70ab-84fd-6385fa97cef1` |
| `error_tracking` | `issue_reopened` | **Enabled** | `019f45b3-7faa-74dc-accb-ff7a2b4ad3c0` |
| `error_tracking` | `issue_spiking` | **Enabled** | `019f45b3-83ba-7a40-99c7-fc2c904f8c83` |
| `session_replay` | `session_analysis_cluster` | **Enabled** (sample_rate: 0.1) | `019f45b3-87c6-78b4-a157-cd02bda1ea7c` |
| `conversations` | `ticket` | **Enabled** | `019f45b3-8a36-7a9a-a50f-fcde636e3fb5` |
| `signals_scout` | `cross_source_issue` | **Already on by default** | — |
| `llm_analytics` | `evaluation_report` | **Skipped** — internal-only, not a v1 responder |  |
| `logs` | — | **Skipped** — logs product not in use | — |

---

## Connected Tools

| Tool | Status | Source ID | Notes |
|---|---|---|---|
| GitHub Issues | **Connected by this setup** | `019f45c3-dcb0-0000-5235-1cc1b4d54e8a` | Syncing `issues` table (incremental on `updated_at`). First sync started. More tables can be enabled in the UI. Responder config: `019f45c3-fa2f-704f-b473-77ca0fdbdf39` |
| Linear | **Connected by this setup** | `019f45b8-7174-0000-1180-eaca0f5434dd` | Existing OAuth integration (id 58313, "QredenceAI") reused. Syncing `issues` table (incremental on `updatedAt`). First sync started. More tables can be enabled in the UI. Responder config: `019f45c3-fc04-7041-b11f-1088542f5df1` |
| Zendesk | **Not used** — not selected | — | — |
| pganalyze | **Not used** — not selected | — | — |

---

## Scout Troop

**4 active scouts** (general + 2 specialists + 1 custom). First scans fire within ~30 minutes.

### Enabled

| Scout | Reason |
|---|---|
| `signals-scout-general` | Always on — cross-product correlations and uncovered surfaces |
| `signals-scout-ai-observability` | Fleet-rlm is an AI agent platform; LLM traces, cost, latency, and eval-performance regressions are the top watchable surface |
| `signals-scout-surveys` | 2 active surveys confirmed: "LLM feedback" (rating) and "Waitlist Signup" |
| `signals-scout-rlm-run-health` _(custom)_ | See Custom Scouts section |

### Disabled

| Scout | Reason |
|---|---|
| `signals-scout-error-tracking` | Covered by the native `error_tracking` signal source (step 4) |
| `signals-scout-session-replay` | Covered by the native `session_replay` signal source (step 4) |
| `signals-scout-product-analytics` | No saved funnels/retention flows confirmed; not in top-2 used surfaces |
| `signals-scout-feature-flags` | Feature flags not confirmed as actively used |
| `signals-scout-experiments` | No active A/B experiments found |
| `signals-scout-web-analytics` | Web traffic not in top-2 surfaces (AI runs dominate) |
| `signals-scout-web-vitals` | No `$web_vitals` evidence |
| `signals-scout-revenue-analytics` | No payment SDK in the project |
| `signals-scout-customer-analytics` | Tenant churn monitoring not selected; enable if B2B account health becomes a priority |
| `signals-scout-data-warehouse` | Neon DB source is failing; enable this scout to monitor warehouse sync health |
| `signals-scout-data-pipelines` | No CDP destinations or hog flows configured |
| `signals-scout-logs` | PostHog logs product not in use |
| `signals-scout-apm` | No OpenTelemetry/APM instrumentation |
| `signals-scout-csp-violations` | No CSP reporting configured |
| `signals-scout-replay-vision` | Replay Vision scanners not configured |
| `signals-scout-mcp-tool-calls` | Not in top-2 surfaces |
| `signals-scout-anomaly-detection` | Not in top-2 surfaces |
| `signals-scout-health-checks` | Not in top-2 surfaces |
| `signals-scout-observability-gaps` | Not in top-2 surfaces |
| `signals-scout-insight-alerts` | Not in top-2 surfaces |
| `signals-scout-inbox-validation` | Fresh setup — no resolved reports to validate yet |
| `signals-scout-skills-store` | Not in top-2 surfaces |

To re-enable any disabled scout: go to the scout's config in PostHog (https://eu.posthog.com/project/15008/inbox) and flip `enabled` to true. To silence a noisy scout without losing its data, set `emit: false` on its config to switch it to dry-run mode.

---

## Custom Scouts

### Created: `signals-scout-rlm-run-health`

**What it watches:** The AI agent run pipeline — whether runs produce trajectories and whether failure rates are rising.

**Why no built-in scout covers this:** `signals-scout-ai-observability` watches LLM-trace metrics (token costs, latency, per-model errors in `$ai_*` events). It does not watch the higher-level outcome question: did the agent complete its task and produce a trajectory? The known silent-failure mode (`has_trajectory=false` with a STATUS_OK return) falls entirely outside the `ai-observability` scout's scope. `signals-scout-general` sweeps cross-product surfaces but does not own this specific job pipeline.

**Discriminator:** Run completion/success rate drops ≥10 pp week-over-week affecting multiple users, OR no-trajectory rate trending up over 3+ consecutive days, OR a new failure mode appearing in the last 7 days that was absent in the prior window.

**Noise escape hatch:** Set `emit: false` on this scout's config in PostHog to run it in dry-run mode (it still executes and logs, but files nothing to the inbox).

### Surfaces considered and ruled out

| Surface | Filter that rejected it |
|---|---|
| LLM provider connectivity health | Overlap — substantially covered by `signals-scout-ai-observability` (error rates on LLM traces) and the `error_tracking` native source |
| Optimization run pipeline | Quality bar — couldn't name 2+ concrete explore patterns without confirmed event names |
| Neon DB warehouse sync health | Duplicates built-in — `signals-scout-data-warehouse` (disabled) already covers this surface; recommended to re-enable it instead |
| Tenant engagement / churn risk | Duplicates built-in — `signals-scout-customer-analytics` (disabled) covers this; recommended to re-enable if B2B account health is a priority |

---

## Follow-ups

- [ ] **Enable Session Replay** in project settings: https://eu.posthog.com/project/15008/settings/environment-replay
- [ ] **Enable Error Tracking** in project settings: https://eu.posthog.com/project/15008/settings/environment-error-tracking
- [ ] **Enable Support (Conversations)** in project settings — then connect an inbound channel (email / inbox / Slack) so tickets reach the inbox
- [ ] **Fix the Neon DB warehouse connection** — the existing Postgres source (id `019c7b03-6e82-0000-4492-34324fe7abee`) has been failing with network-unreachable errors since June 2026. Verify the host IP and Neon project firewall rules.
- [ ] **Enable `signals-scout-data-warehouse`** once the Neon connection is restored — it will monitor the warehouse sync health automatically
- [ ] **Add more GitHub Issues tables** via the UI if you want to sync pull requests or comments in addition to issues: https://eu.posthog.com/project/15008/data-management/sources
- [ ] **Add more Linear tables** via the UI if you want to sync projects or teams in addition to issues
- [ ] **Enable `signals-scout-customer-analytics`** if B2B tenant churn monitoring becomes a priority (the warehouse has `tenants` and `tenant_subscriptions` tables)
- [ ] **Enable `signals-scout-feature-flags`** if feature flags become actively used in the product

---

## What Happens Next

The scout coordinator picks up the fresh configs within ~30 minutes. Scouts run on their default daily cadence and cluster findings into reports in the Self-driving inbox. Immediately-actionable reports can seed coding tasks directly. The `signals-scout-rlm-run-health` scout will close out quietly on its first run if no run-related events are found yet — it will leave a scratchpad note and start watching as instrumentation lands.

Inbox: **https://eu.posthog.com/project/15008/inbox**

import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vite-plus/test";

import { RunDetailsSheet } from "@/features/optimization/run-details-sheet";
import { RunHistory } from "@/features/optimization/run-history";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("Optimization run history", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders completed, running, and failed rows", () => {
    const html = renderToStaticMarkup(
      <RunHistory
        isLoading={false}
        error={null}
        refetch={() => undefined}
        runs={[
          {
            id: "run-completed",
            status: "completed",
            module_slug: "longcot-reasoner",
            program_spec: "fleet.module:Program",
            optimizer: "GEPA",
            auto: "medium",
            train_ratio: 0.8,
            validation_score: 0.91,
            output_path: "artifacts/optimized.json",
            manifest_path: "artifacts/optimized.manifest.json",
            reflection_profile_id: "profile-1",
            reflection_model_id: "openai/gpt-4.1",
            distilled_trace_bundle_path: "artifacts/traces/mlflow-traces.distilled.jsonl",
            phase: "completed",
            started_at: "2026-06-11T10:00:00Z",
            completed_at: "2026-06-11T10:02:00Z",
          },
          {
            id: "run-running",
            status: "running",
            module_slug: null,
            program_spec: "skill:optimization",
            optimizer: "GEPA",
            auto: "light",
            train_ratio: 0.8,
            validation_score: null,
            output_path: null,
            manifest_path: null,
            phase: "compiling",
            started_at: "2026-06-11T10:03:00Z",
            completed_at: null,
          },
          {
            id: "run-failed",
            status: "failed",
            module_slug: null,
            program_spec: "skill:custom",
            optimizer: "GEPA",
            auto: "heavy",
            train_ratio: 0.7,
            validation_score: null,
            output_path: null,
            manifest_path: null,
            error: "Dataset missing required fields",
            phase: "loading",
            started_at: "2026-06-11T10:04:00Z",
            completed_at: "2026-06-11T10:05:00Z",
          },
        ]}
      />,
    );

    expect(html).toContain("completed");
    expect(html).toContain("running");
    expect(html).toContain("failed");
    expect(html).toContain("longcot-reasoner");
    expect(html).toContain("skill:optimization");
    expect(html).toContain("Dataset missing required fields");
    expect(html).toContain("openai/gpt-4.1");
    expect(html).toContain("mlflow-traces.distilled.jsonl");
    expect(html).toContain("Details");
  });

  it("renders run details with unchanged prompt, distilled traces, artifacts, and draft state", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <RunDetailsSheet
          runId="277"
          open
          onOpenChange={() => undefined}
          isLoading={false}
          error={null}
          onCreateDraft={() => undefined}
          isDraftPending={false}
          draft={{
            ok: true,
            draft_id: "promotion-draft-277",
            run_id: "277",
            target: "skill:optimization",
            status: "draft",
            summary: "Draft promotion only.",
            optimized_artifact_path: "artifacts/optimization.optimized.md",
            manifest_path: "artifacts/optimization.optimized.manifest.json",
            draft_path: "promotion-drafts/promotion-draft-277.json",
            created_at: "2026-06-11T10:00:00Z",
          }}
          detail={{
            run: {
              id: "277",
              status: "completed",
              module_slug: "skill-optimization",
              program_spec: "skill:optimization",
              optimizer: "gepa",
              auto: "light",
              train_ratio: 0.8,
              reflection_model_id: "openai/gemini-3.5-flash",
              distilled_trace_bundle_path: "traces/mlflow-traces.distilled.jsonl",
              train_examples: 1,
              validation_examples: 0,
              validation_score: null,
              output_path: "artifacts/optimization.optimized.md",
              manifest_path: "artifacts/optimization.optimized.manifest.json",
              phase: "completed",
              started_at: "2026-06-11T10:00:00Z",
              completed_at: "2026-06-11T10:02:00Z",
            },
            manifest_available: true,
            manifest: { optimizer: "GEPA" },
            typed_review_bundle: {
              version: 1,
              holdout: {
                promotion_ready: false,
                external_validation_available: false,
                baseline_score: null,
                optimized_score: null,
                score_delta: null,
              },
              insights: null,
            },
            review_bundle: {
              feedback_summary: "No validation examples",
              holdout: {
                external_validation_available: false,
                gepa_internal_valset: "trainset_fallback",
                promotion_ready: false,
              },
              gepa_evidence: {
                available: true,
                path: "artifacts/optimization.optimized.gepa-evidence.json",
                log_dir: "artifacts/optimization.optimized.gepa",
              },
            },
            artifact_refs: [
              {
                label: "Manifest",
                path: "artifacts/optimization.optimized.manifest.json",
                kind: "manifest",
                exists: true,
              },
              {
                label: "GEPA candidate evidence",
                path: "artifacts/optimization.optimized.gepa-evidence.json",
                kind: "gepa_evidence",
                exists: true,
              },
            ],
            score_summary: {
              baseline_score: null,
              optimized_score: null,
              score_delta: null,
              train_examples: 1,
              validation_examples: 0,
              train_ratio: 0.8,
              split_strategy: "single-example",
            },
            prompt_diffs: [
              {
                predictor_name: "skill",
                before_prompt: "same prompt",
                after_prompt: "same prompt",
                changed: false,
              },
            ],
            trace_evidence: [
              {
                kind: "trace_evidence",
                trace_id: "tr-1",
                session_id: "default:anonymous:session",
                client_request_id: "chat-1",
                span_count: 94,
                failure_categories: ["bad_tool_use", "loop_inefficiency"],
                prompt_change_recommendations: ["Clarify when to stop."],
              },
            ],
            candidate_decisions: [
              {
                candidate_id: "selected",
                status: "selected",
                summary: "GEPA kept the original prompt as the best selected artifact.",
                rationale: "No semantic change was selected.",
                score: 0.9,
                score_delta: 0.2,
                artifact_path: "artifacts/optimization.optimized.gepa-evidence.json",
                missing_candidate_artifact: false,
              },
              {
                candidate_id: "rejected",
                status: "unavailable",
                summary: "Rejected proposal artifacts were not persisted.",
                missing_candidate_artifact: true,
              },
            ],
            insights: {
              selected_outcome: "unchanged",
              summary: "GEPA kept the original prompt as the selected artifact.",
              trace_driven_recommendations: ["Clarify when to stop."],
              next_step: "Add more validation examples.",
            },
            optimized_artifact_text: "same prompt",
            optimized_artifact_truncated: false,
          }}
        />,
      );
    });

    const html = document.body.textContent ?? "";

    expect(html).toContain("GEPA kept the original prompt");
    expect(html).toContain("Self-improving RLM objective");
    expect(html).toContain("proposer RLM");
    expect(html).toContain("executor RLM prompt artifact");
    expect(html).toContain("Holdout validation required");
    expect(html).toContain("Candidate evidence persisted");
    expect(html).toContain("No semantic prompt change selected");
    expect(html).toContain("Clarify when to stop.");
    expect(html).toContain("artifacts/optimization.optimized.gepa-evidence.json");
    expect(html).toContain("artifacts/optimization.optimized.manifest.json");
    expect(html).toContain("promotion-draft-277");

    act(() => {
      root.unmount();
    });
  });
});

import { describe, expect, it } from "vite-plus/test";

import {
  appendTraceBundlePathValue,
  buildOptimizationRequest,
  DEFAULT_OPTIMIZATION_FORM,
  parseTraceBundlePaths,
} from "@/features/optimization/optimization-model";

describe("optimization GEPA request model", () => {
  it("builds a module optimization payload with an uploaded dataset", () => {
    expect(
      buildOptimizationRequest({
        form: {
          ...DEFAULT_OPTIMIZATION_FORM,
          datasetSource: "upload",
          moduleSlug: "longcot-reasoner",
          auto: "medium",
          trainRatio: "0.75",
          maxMetricCalls: "8",
          traceBundlePaths: "trace-a.jsonl\ntrace-b.jsonl",
        },
        datasetId: "dataset-1",
      }),
    ).toEqual({
      optimizer: "gepa",
      auto: "medium",
      train_ratio: 0.75,
      max_metric_calls: 8,
      trace_bundle_paths: ["trace-a.jsonl", "trace-b.jsonl"],
      dataset_id: "dataset-1",
      module_slug: "longcot-reasoner",
    });
  });

  it("builds a module optimization payload with an existing dataset and reflection model", () => {
    expect(
      buildOptimizationRequest({
        form: {
          ...DEFAULT_OPTIMIZATION_FORM,
          moduleSlug: "longcot-reasoner",
          datasetId: "dataset-existing",
          reflectionProfileId: "profile-1",
          reflectionModelId: "openai/gpt-4.1",
        },
      }),
    ).toMatchObject({
      optimizer: "gepa",
      dataset_id: "dataset-existing",
      module_slug: "longcot-reasoner",
      reflection_profile_id: "profile-1",
      reflection_model_id: "openai/gpt-4.1",
    });
  });

  it("builds a bundled skill optimization payload", () => {
    expect(
      buildOptimizationRequest({
        form: {
          ...DEFAULT_OPTIMIZATION_FORM,
          targetMode: "skill",
          skillTargetMode: "name",
          skillName: "optimization",
          datasetSource: "path",
          datasetPath: "artifacts/skill-cases.jsonl",
          outputPath: "artifacts/optimized/optimization.md",
        },
      }),
    ).toMatchObject({
      optimizer: "gepa",
      dataset_path: "artifacts/skill-cases.jsonl",
      skill_name: "optimization",
      output_path: "artifacts/optimized/optimization.md",
    });
  });

  it("builds a skill path optimization payload", () => {
    expect(
      buildOptimizationRequest({
        form: {
          ...DEFAULT_OPTIMIZATION_FORM,
          targetMode: "skill",
          skillTargetMode: "path",
          skillPath: "skills/custom/SKILL.md",
          datasetSource: "path",
          datasetPath: "artifacts/skill-cases.jsonl",
        },
      }),
    ).toMatchObject({
      optimizer: "gepa",
      dataset_path: "artifacts/skill-cases.jsonl",
      skill_path: "skills/custom/SKILL.md",
    });
  });

  it("rejects incomplete reflection model selection", () => {
    expect(() =>
      buildOptimizationRequest({
        form: {
          ...DEFAULT_OPTIMIZATION_FORM,
          moduleSlug: "longcot-reasoner",
          datasetId: "dataset-1",
          reflectionProfileId: "profile-1",
        },
      }),
    ).toThrow("Select both a reflection profile and model");
  });

  it("rejects an invalid max metric call budget", () => {
    expect(() =>
      buildOptimizationRequest({
        form: {
          ...DEFAULT_OPTIMIZATION_FORM,
          moduleSlug: "longcot-reasoner",
          datasetId: "dataset-1",
          maxMetricCalls: "0",
        },
      }),
    ).toThrow("Max metric calls must be a positive integer");
  });

  it("rejects missing targets and missing dataset input", () => {
    expect(() =>
      buildOptimizationRequest({
        form: {
          ...DEFAULT_OPTIMIZATION_FORM,
          moduleSlug: "",
          datasetSource: "path",
          datasetPath: "data.jsonl",
        },
      }),
    ).toThrow("Select a registered module");

    expect(() =>
      buildOptimizationRequest({
        form: { ...DEFAULT_OPTIMIZATION_FORM, moduleSlug: "longcot-reasoner", datasetSource: "upload" },
      }),
    ).toThrow("Choose a JSON/JSONL dataset file");
  });

  it("parses newline and comma separated trace bundles", () => {
    expect(parseTraceBundlePaths("a.jsonl, b.jsonl\n\nc.jsonl")).toEqual([
      "a.jsonl",
      "b.jsonl",
      "c.jsonl",
    ]);
  });

  it("appends exported distilled trace bundle paths for GEPA handoff", () => {
    expect(appendTraceBundlePathValue("", "artifacts/traces/distilled.jsonl")).toBe(
      "artifacts/traces/distilled.jsonl",
    );
    expect(appendTraceBundlePathValue("existing.jsonl", "artifacts/traces/distilled.jsonl")).toBe(
      "existing.jsonl\nartifacts/traces/distilled.jsonl",
    );
  });
});

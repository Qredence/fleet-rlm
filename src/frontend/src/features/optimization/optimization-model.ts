import type { DatasetResponse, GEPAOptimizationRequest } from "@/lib/rlm-api";

export type OptimizationTargetMode = "module" | "skill";
export type SkillTargetMode = "name" | "path";
export type OptimizationAutoLevel = "light" | "medium" | "heavy";
export type DatasetSourceMode = "existing" | "upload" | "path";

export interface OptimizationRunFormState {
  targetMode: OptimizationTargetMode;
  moduleSlug: string;
  skillTargetMode: SkillTargetMode;
  skillName: string;
  skillPath: string;
  datasetSource: DatasetSourceMode;
  datasetId: string;
  datasetPath: string;
  auto: OptimizationAutoLevel;
  trainRatio: string;
  maxMetricCalls: string;
  outputPath: string;
  traceBundlePaths: string;
  reflectionProfileId: string;
  reflectionModelId: string;
}

export interface BuildOptimizationRequestInput {
  form: OptimizationRunFormState;
  datasetId?: string | null;
  hasDatasetFile?: boolean;
}

export const DEFAULT_OPTIMIZATION_FORM: OptimizationRunFormState = {
  targetMode: "module",
  moduleSlug: "",
  skillTargetMode: "name",
  skillName: "optimization",
  skillPath: "",
  datasetSource: "existing",
  datasetId: "",
  datasetPath: "",
  auto: "light",
  trainRatio: "0.8",
  maxMetricCalls: "",
  outputPath: "",
  traceBundlePaths: "",
  reflectionProfileId: "",
  reflectionModelId: "",
};

export function isRunnableDataset(dataset: DatasetResponse): boolean {
  return (dataset?.row_count ?? 0) > 0;
}

export function parseTraceBundlePaths(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export function appendTraceBundlePathValue(currentValue: string, path: string): string {
  const trimmedPath = path.trim();
  if (!trimmedPath) return currentValue;
  const existing = currentValue.trim();
  return existing ? `${existing}\n${trimmedPath}` : trimmedPath;
}

export function buildOptimizationRequest({
  form,
  datasetId,
  hasDatasetFile = false,
}: BuildOptimizationRequestInput): GEPAOptimizationRequest {
  const datasetPath = form.datasetPath.trim();
  const selectedDatasetId = (datasetId ?? form.datasetId).trim();
  if (form.datasetSource === "existing" && !selectedDatasetId) {
    throw new Error("Select an existing dataset.");
  }
  if (form.datasetSource === "upload" && !hasDatasetFile && !datasetId) {
    throw new Error("Choose a JSON/JSONL dataset file to upload.");
  }
  if (form.datasetSource === "path" && !datasetPath) {
    throw new Error("Enter a server dataset path.");
  }

  const trainRatio = Number.parseFloat(form.trainRatio);
  if (!Number.isFinite(trainRatio) || trainRatio <= 0 || trainRatio >= 1) {
    throw new Error("Train ratio must be greater than 0 and less than 1.");
  }

  const request: GEPAOptimizationRequest = {
    optimizer: "gepa",
    auto: form.auto,
    train_ratio: trainRatio,
    trace_bundle_paths: parseTraceBundlePaths(form.traceBundlePaths),
  };

  const maxMetricCallsValue = form.maxMetricCalls.trim();
  if (maxMetricCallsValue) {
    const maxMetricCalls = Number.parseInt(maxMetricCallsValue, 10);
    if (!Number.isInteger(maxMetricCalls) || maxMetricCalls < 1) {
      throw new Error("Max metric calls must be a positive integer.");
    }
    request.max_metric_calls = maxMetricCalls;
  }

  switch (form.datasetSource) {
    case "existing":
      request.dataset_id = selectedDatasetId;
      break;
    case "upload":
      request.dataset_id = selectedDatasetId;
      break;
    case "path":
      request.dataset_path = datasetPath;
      break;
    default: {
      const _exhaustive: never = form.datasetSource;
      throw new Error(`Unhandled dataset source mode: ${_exhaustive}`);
    }
  }

  const outputPath = form.outputPath.trim();
  if (outputPath) {
    request.output_path = outputPath;
  }

  const reflectionProfileId = form.reflectionProfileId.trim();
  const reflectionModelId = form.reflectionModelId.trim();
  if (reflectionProfileId || reflectionModelId) {
    if (!reflectionProfileId || !reflectionModelId) {
      throw new Error("Select both a reflection profile and model.");
    }
    request.reflection_profile_id = reflectionProfileId;
    request.reflection_model_id = reflectionModelId;
  }

  if (form.targetMode === "module") {
    const moduleSlug = form.moduleSlug.trim();
    if (!moduleSlug) {
      throw new Error("Select a registered module to optimize.");
    }
    request.module_slug = moduleSlug;
    return request;
  }

  if (form.skillTargetMode === "name") {
    const skillName = form.skillName.trim();
    if (!skillName) {
      throw new Error("Enter a bundled skill name.");
    }
    request.skill_name = skillName;
    return request;
  }

  const skillPath = form.skillPath.trim();
  if (!skillPath) {
    throw new Error("Enter a SKILL.md path.");
  }
  request.skill_path = skillPath;
  return request;
}

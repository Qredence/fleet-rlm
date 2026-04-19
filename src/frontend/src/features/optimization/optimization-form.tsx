import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  datasetEndpoints,
  optimizationEndpoints,
  optimizationKeys,
  type DatasetResponse,
  type GEPAModuleInfo,
  type GEPAOptimizationRequest,
} from "@/lib/rlm-api/optimization";

const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
const SETTINGS_SECTION_CLASSNAME = "max-w-content gap-4";
const CUSTOM_MODULE_VALUE = "__custom__";
const CUSTOM_RATIO_VALUE = "__custom__";
const CUSTOM_DATASET_VALUE = "__custom_dataset__";
const EMPTY_MODULES: GEPAModuleInfo[] = [];

const TRAIN_RATIO_PRESETS = [
  { value: "0.7", label: "70% train / 30% val" },
  { value: "0.75", label: "75% train / 25% val" },
  { value: "0.8", label: "80% train / 20% val" },
  { value: "0.85", label: "85% train / 15% val" },
  { value: "0.9", label: "90% train / 10% val" },
];

export interface OptimizationRunDraft {
  moduleSlug?: string | null;
  datasetId?: string | null;
  datasetName?: string | null;
  datasetPath?: string | null;
  programSpec?: string | null;
  auto?: "light" | "medium" | "heavy";
  trainRatio?: number | null;
  outputPath?: string | null;
}

function resolveTrainRatioState(trainRatio: number | null | undefined): {
  preset: string;
  custom: string;
} {
  const normalized = trainRatio ?? 0.8;
  const matched = TRAIN_RATIO_PRESETS.find(
    (preset) => Number.parseFloat(preset.value) === normalized,
  );
  if (matched) {
    return { preset: matched.value, custom: "" };
  }
  return { preset: CUSTOM_RATIO_VALUE, custom: String(normalized) };
}

function mergeDatasetsWithDraft(
  datasets: DatasetResponse[],
  draft: OptimizationRunDraft | null,
): DatasetResponse[] {
  if (!draft?.datasetId || datasets.some((dataset) => dataset.id === draft.datasetId)) {
    return datasets;
  }
  return [
    {
      id: draft.datasetId,
      name: draft.datasetName ?? `Dataset #${draft.datasetId}`,
      row_count: 0,
      format: "jsonl",
      module_slug: draft.moduleSlug ?? null,
      created_at: "",
    },
    ...datasets,
  ];
}

interface OptimizationFormProps {
  onRunCreated?: () => void;
  initialDraft?: OptimizationRunDraft | null;
  draftVersion?: number;
}

export function OptimizationForm({
  onRunCreated,
  initialDraft = null,
  draftVersion,
}: OptimizationFormProps) {
  const [selectedModule, setSelectedModule] = useState<string>(CUSTOM_MODULE_VALUE);
  const [selectedDatasetValue, setSelectedDatasetValue] = useState<string>(CUSTOM_DATASET_VALUE);
  const [customProgramSpec, setCustomProgramSpec] = useState("");
  const [datasetPath, setDatasetPath] = useState("");
  const [outputPath, setOutputPath] = useState("");
  const [auto, setAuto] = useState<"light" | "medium" | "heavy">("light");
  const [trainRatioPreset, setTrainRatioPreset] = useState("0.8");
  const [customTrainRatio, setCustomTrainRatio] = useState("");
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const lastHydratedDraftVersionRef = useRef<number | null>(null);
  const lastResolvedDraftVersionRef = useRef<number | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const statusQuery = useQuery({
    queryKey: optimizationKeys.status(),
    queryFn: ({ signal }) => optimizationEndpoints.status(signal),
    staleTime: 30_000,
  });

  const modulesQuery = useQuery({
    queryKey: optimizationKeys.modules(),
    queryFn: ({ signal }) => optimizationEndpoints.modules(signal),
    staleTime: 60_000,
  });
  const datasetsQuery = useQuery({
    queryKey: [...optimizationKeys.datasets(), "list", { limit: 100 }],
    queryFn: ({ signal }) => datasetEndpoints.list({ limit: 100 }, signal),
    staleTime: 60_000,
  });

  const modules: GEPAModuleInfo[] = modulesQuery.data ?? EMPTY_MODULES;
  const availableDatasets = useMemo(
    () => mergeDatasetsWithDraft(datasetsQuery.data?.items ?? [], initialDraft),
    [datasetsQuery.data, initialDraft],
  );
  const activeModuleInfo =
    selectedModule !== CUSTOM_MODULE_VALUE
      ? (modules.find((m) => m.slug === selectedModule) ?? null)
      : null;
  const selectedDatasetId =
    selectedDatasetValue !== CUSTOM_DATASET_VALUE ? selectedDatasetValue : null;
  const selectedDataset =
    selectedDatasetId != null
      ? (availableDatasets.find((dataset) => dataset.id === selectedDatasetId) ?? null)
      : null;
  const usingCustomDatasetPath = selectedDatasetValue === CUSTOM_DATASET_VALUE;

  const moduleDisplayLabel = activeModuleInfo ? activeModuleInfo.label : "Custom";
  const datasetDisplayLabel = selectedDataset
    ? selectedDataset.name
    : usingCustomDatasetPath
      ? "Custom path…"
      : "Select dataset…";

  const isCustomRatio = trainRatioPreset === CUSTOM_RATIO_VALUE;
  const ratioDisplayLabel = isCustomRatio
    ? "Custom…"
    : (TRAIN_RATIO_PRESETS.find((p) => p.value === trainRatioPreset)?.label ?? trainRatioPreset);
  const trainRatioStr = isCustomRatio ? customTrainRatio : trainRatioPreset;
  const ratio = Number.parseFloat(trainRatioStr);
  const validRatio = !Number.isNaN(ratio) && ratio > 0 && ratio < 1;

  const resolvedProgramSpec = activeModuleInfo
    ? activeModuleInfo.program_spec
    : customProgramSpec.trim();

  const status = statusQuery.data;
  const available = status?.available ?? false;
  const hasDatasetTarget = selectedDatasetId != null || datasetPath.trim() !== "";
  const canRun = available && hasDatasetTarget && resolvedProgramSpec !== "" && validRatio;

  useEffect(() => {
    if (draftVersion == null || initialDraft == null) {
      return;
    }

    const matchedModule = initialDraft.moduleSlug
      ? ((modulesQuery.data ?? EMPTY_MODULES).find(
          (moduleInfo) => moduleInfo.slug === initialDraft.moduleSlug,
        ) ?? null)
      : null;
    const ratioState = resolveTrainRatioState(initialDraft.trainRatio);
    const draftProgramSpec = (initialDraft.programSpec ?? "").trim();
    const moduleResolutionPending =
      Boolean(initialDraft.moduleSlug) &&
      matchedModule == null &&
      (modulesQuery.data == null || modulesQuery.isError);

    if (lastHydratedDraftVersionRef.current !== draftVersion) {
      setSelectedDatasetValue(
        initialDraft.datasetId != null ? String(initialDraft.datasetId) : CUSTOM_DATASET_VALUE,
      );
      setDatasetPath(initialDraft.datasetPath ?? "");
      setOutputPath(initialDraft.outputPath ?? "");
      setAuto(initialDraft.auto ?? "light");
      setTrainRatioPreset(ratioState.preset);
      setCustomTrainRatio(ratioState.custom);
      setTouched({});
      abortRef.current?.abort();
      setSelectedModule(matchedModule ? matchedModule.slug : CUSTOM_MODULE_VALUE);
      setCustomProgramSpec(matchedModule ? "" : draftProgramSpec);
      lastHydratedDraftVersionRef.current = draftVersion;
      if (!moduleResolutionPending) {
        lastResolvedDraftVersionRef.current = draftVersion;
      }
      return;
    }

    if (lastResolvedDraftVersionRef.current === draftVersion || moduleResolutionPending) {
      return;
    }

    setSelectedModule(matchedModule ? matchedModule.slug : CUSTOM_MODULE_VALUE);
    setCustomProgramSpec(matchedModule ? "" : draftProgramSpec);
    lastResolvedDraftVersionRef.current = draftVersion;
  }, [draftVersion, initialDraft, modulesQuery.data, modulesQuery.isError]);

  const runOptimization = useMutation({
    mutationFn: (input: GEPAOptimizationRequest) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      return optimizationEndpoints.createRun(input, controller.signal);
    },
    onSuccess: (result) => {
      if (!mountedRef.current) return;
      toast.success("Optimization run started", {
        description: `Run #${result.run_id} is now in progress. Switching to Run History to track progress.`,
      });
      queryClient.invalidateQueries({ queryKey: optimizationKeys.runs() });
      onRunCreated?.();
    },
    onError: (error) => {
      if (!mountedRef.current) return;
      toast.error("Failed to start optimization run", {
        description: error instanceof Error ? error.message : "Unexpected error",
      });
    },
  });

  const handleRun = () => {
    setTouched({ datasetPath: true, programSpec: true, trainRatio: true });
    if (!canRun) return;
    runOptimization.mutate({
      dataset_id: selectedDatasetId,
      dataset_path: selectedDatasetId == null ? datasetPath.trim() : null,
      program_spec: resolvedProgramSpec,
      output_path: outputPath.trim() || null,
      auto,
      train_ratio: ratio,
      module_slug: activeModuleInfo ? activeModuleInfo.slug : null,
    });
  };

  const datasetInvalid = touched.datasetPath && usingCustomDatasetPath && datasetPath.trim() === "";
  const specInvalid = touched.programSpec && !activeModuleInfo && customProgramSpec.trim() === "";
  const ratioInvalid = touched.trainRatio && isCustomRatio && !validRatio;

  return (
    <div className="flex flex-col gap-10">
      <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
        <div className="flex flex-col gap-1">
          <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
            GEPA Prompt Optimization
          </FieldLegend>
          <FieldDescription>
            Evolve prompts using text feedback with GEPA (Generative Evolution of Prompts with
            Assessment). Requires MLflow and a prepared optimization dataset.
          </FieldDescription>
        </div>

        {/* ── Section 1: Status & Module ── */}
        <FieldGroup className="gap-0">
          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Status</FieldTitle>
              <FieldDescription>GEPA optimization prerequisites and availability.</FieldDescription>
            </FieldContent>
            <div className="flex flex-col items-end gap-2 self-start">
              {statusQuery.isLoading ? (
                <Badge variant="secondary">Checking…</Badge>
              ) : available ? (
                <Badge variant="secondary" className="bg-success/15 text-success">
                  Available
                </Badge>
              ) : (
                <Badge variant="destructive">Unavailable</Badge>
              )}
              {status && !status.gepa_installed ? (
                <span className="text-xs text-muted-foreground">GEPA module not installed</span>
              ) : null}
              {status && !status.mlflow_enabled ? (
                <span className="text-xs text-muted-foreground">MLflow unavailable</span>
              ) : null}
            </div>
          </Field>

          {status?.guidance && status.guidance.length > 0 ? (
            <Field className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>Setup guidance</FieldTitle>
                <FieldDescription>
                  {status.guidance.map((msg, i) => (
                    <span key={i} className="block">
                      {msg}
                    </span>
                  ))}
                </FieldDescription>
              </FieldContent>
            </Field>
          ) : null}

          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Module</FieldTitle>
              <FieldDescription>
                Choose a registered worker module or select Custom for a raw program spec.
              </FieldDescription>
            </FieldContent>
            <Select
              value={selectedModule}
              onValueChange={(value) => {
                if (value) setSelectedModule(value);
              }}
            >
              <SelectTrigger className="w-full" aria-label="Module selection">
                <SelectValue placeholder="Select module…">{moduleDisplayLabel}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {modules.map((mod) => (
                  <SelectItem key={mod.slug} value={mod.slug}>
                    {mod.label}
                  </SelectItem>
                ))}
                <SelectSeparator />
                <SelectItem value={CUSTOM_MODULE_VALUE}>Custom</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          {activeModuleInfo && activeModuleInfo.required_dataset_keys.length > 0 ? (
            <Field className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>Required dataset keys</FieldTitle>
                <FieldDescription>
                  Dataset rows must contain the following keys for this module.
                </FieldDescription>
              </FieldContent>
              <div className="flex flex-wrap gap-1.5">
                {activeModuleInfo.required_dataset_keys.map((key) => (
                  <Badge key={key} variant="secondary" className="font-mono text-xs">
                    {key}
                  </Badge>
                ))}
              </div>
            </Field>
          ) : null}
        </FieldGroup>

        <Separator />

        {/* ── Section 2: Dataset & Spec ── */}
        <FieldGroup className="gap-0">
          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Dataset</FieldTitle>
              <FieldDescription>
                Use an uploaded/exported dataset or provide a custom path under the optimization
                data directory.
              </FieldDescription>
            </FieldContent>
            <div className="flex flex-col gap-2">
              <Select
                value={selectedDatasetValue}
                onValueChange={(value) => {
                  if (value) setSelectedDatasetValue(value);
                }}
              >
                <SelectTrigger className="w-full" aria-label="Dataset selection">
                  <SelectValue placeholder="Select dataset…">{datasetDisplayLabel}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {availableDatasets.map((dataset) => (
                    <SelectItem key={dataset.id} value={String(dataset.id)}>
                      {dataset.name}
                    </SelectItem>
                  ))}
                  <SelectSeparator />
                  <SelectItem value={CUSTOM_DATASET_VALUE}>Custom path…</SelectItem>
                </SelectContent>
              </Select>
              {selectedDataset ? (
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <span>{selectedDataset.row_count.toLocaleString()} rows</span>
                  <span aria-hidden="true">·</span>
                  <span>{selectedDataset.format.toUpperCase()}</span>
                  {selectedDataset.module_slug ? (
                    <>
                      <span aria-hidden="true">·</span>
                      <Badge variant="secondary" className="font-mono text-xs">
                        {selectedDataset.module_slug}
                      </Badge>
                    </>
                  ) : null}
                </div>
              ) : null}
            </div>
            {usingCustomDatasetPath ? (
              <Input
                type="text"
                value={datasetPath}
                autoComplete="off"
                placeholder="traces.json"
                aria-label="Dataset path"
                aria-invalid={datasetInvalid || undefined}
                onChange={(event) => setDatasetPath(event.target.value)}
                onBlur={() => setTouched((t) => ({ ...t, datasetPath: true }))}
                className="w-full"
              />
            ) : null}
            {datasetInvalid ? <FieldError>Dataset path is required.</FieldError> : null}
          </Field>

          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Program spec</FieldTitle>
              <FieldDescription>
                {activeModuleInfo
                  ? "Resolved from the selected module."
                  : "DSPy program specification to optimize in module:attr form."}
              </FieldDescription>
            </FieldContent>
            {activeModuleInfo ? (
              <code className="block rounded-md border border-border-subtle bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
                {activeModuleInfo.program_spec}
              </code>
            ) : (
              <>
                <Input
                  type="text"
                  value={customProgramSpec}
                  autoComplete="off"
                  placeholder="package.module:build_program"
                  aria-label="Program spec"
                  aria-invalid={specInvalid || undefined}
                  onChange={(event) => setCustomProgramSpec(event.target.value)}
                  onBlur={() => setTouched((t) => ({ ...t, programSpec: true }))}
                  className="w-full"
                />
                {specInvalid ? (
                  <FieldError>Program spec is required in Custom mode.</FieldError>
                ) : null}
              </>
            )}
          </Field>

          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Output path</FieldTitle>
              <FieldDescription>
                Optional path to save the optimized program. Leave empty to skip saving.
              </FieldDescription>
            </FieldContent>
            <Input
              type="text"
              value={outputPath}
              autoComplete="off"
              placeholder="optimized.json"
              aria-label="Output path"
              onChange={(event) => setOutputPath(event.target.value)}
              className="w-full"
            />
          </Field>
        </FieldGroup>

        <Separator />

        {/* ── Section 3: Optimization Parameters ── */}
        <FieldGroup className="gap-0">
          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Optimization intensity</FieldTitle>
              <FieldDescription>
                Controls how aggressively GEPA evolves prompts. Light is fastest, heavy is most
                thorough.
              </FieldDescription>
            </FieldContent>
            <ToggleGroup
              type="single"
              variant="card"
              value={auto}
              aria-label="Optimization intensity"
              className="mt-1 flex w-full flex-wrap gap-3"
              onValueChange={(value) => {
                if (value) setAuto(value as "light" | "medium" | "heavy");
              }}
            >
              <ToggleGroupItem value="light" aria-label="Light" className="flex-1">
                Light
              </ToggleGroupItem>
              <ToggleGroupItem value="medium" aria-label="Medium" className="flex-1">
                Medium
              </ToggleGroupItem>
              <ToggleGroupItem value="heavy" aria-label="Heavy" className="flex-1">
                Heavy
              </ToggleGroupItem>
            </ToggleGroup>
          </Field>

          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Train ratio</FieldTitle>
              <FieldDescription>
                Fraction of examples used for training. The remainder is used for validation.
              </FieldDescription>
            </FieldContent>
            <div className="flex flex-col gap-2">
              <Select
                value={trainRatioPreset}
                onValueChange={(value) => {
                  if (value) setTrainRatioPreset(value);
                }}
              >
                <SelectTrigger className="w-full" aria-label="Train ratio">
                  <SelectValue placeholder="Select ratio…">{ratioDisplayLabel}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {TRAIN_RATIO_PRESETS.map((preset) => (
                    <SelectItem key={preset.value} value={preset.value}>
                      {preset.label}
                    </SelectItem>
                  ))}
                  <SelectSeparator />
                  <SelectItem value={CUSTOM_RATIO_VALUE}>Custom…</SelectItem>
                </SelectContent>
              </Select>
              {isCustomRatio ? (
                <>
                  <Input
                    type="text"
                    inputMode="decimal"
                    value={customTrainRatio}
                    autoComplete="off"
                    placeholder="0.8"
                    aria-label="Custom train ratio"
                    aria-invalid={ratioInvalid || undefined}
                    onChange={(event) => setCustomTrainRatio(event.target.value)}
                    onBlur={() => setTouched((t) => ({ ...t, trainRatio: true }))}
                    className="max-w-32"
                  />
                  {ratioInvalid ? (
                    <FieldError>Enter a number between 0 and 1 (exclusive).</FieldError>
                  ) : null}
                </>
              ) : null}
            </div>
          </Field>
        </FieldGroup>

        <Separator />

        {/* ── Section 4: Run Action ── */}
        <FieldGroup className="gap-0">
          <Field className="gap-5 py-5">
            <FieldContent>
              <FieldTitle>Run optimization</FieldTitle>
              <FieldDescription>
                Start a GEPA optimization run with the configured parameters. This may take several
                minutes depending on dataset size and intensity. Progress is tracked in the Run
                History tab.
              </FieldDescription>
            </FieldContent>
            <Button
              variant="secondary"
              className="self-start rounded-lg"
              onClick={handleRun}
              disabled={!canRun || runOptimization.isPending}
            >
              {runOptimization.isPending ? "Starting…" : "Run GEPA"}
            </Button>
          </Field>
        </FieldGroup>
      </FieldSet>
    </div>
  );
}

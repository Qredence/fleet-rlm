import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";
import { Database, FileJson, Upload, Settings2, DatabaseZap, Sparkles } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectPositioner,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
import { Separator } from "@/components/ui/separator";
import type { DatasetResponse, GEPAModuleInfo, SessionTraceExportResponse } from "@/lib/rlm-api";
import type { LlmProviderProfileResponse, LlmModelCatalogEntry } from "@/lib/rlm-api/llm-profiles";
import { useLlmProfileModels, useLlmProfiles } from "@/features/settings/use-llm-profiles";
import {
  useOptimizationDatasets,
  useOptimizationModules,
  useOptimizationMutations,
} from "../use-optimization";
import { errorMessage } from "../optimization-format";

import {
  isRunnableDataset,
  appendTraceBundlePathValue,
  buildOptimizationRequest,
  DEFAULT_OPTIMIZATION_FORM,
  type DatasetSourceMode,
  type OptimizationRunFormState,
  type OptimizationTargetMode,
  type SkillTargetMode,
} from "../optimization-model";

const DEFAULT_SELECT_VALUE = "__default__";
const EMPTY_MODULES: GEPAModuleInfo[] = [];
const EMPTY_DATASETS: DatasetResponse[] = [];
const EMPTY_PROFILES: LlmProviderProfileResponse[] = [];
const EMPTY_MODELS: LlmModelCatalogEntry[] = [];

function CompactField({
  label,
  description,
  children,
  icon,
}: {
  label: string;
  description?: string;
  children: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <Field className="gap-1.5 transition-all duration-200">
      <FieldLabel className="flex items-center gap-1.5 font-medium text-foreground">
        {icon}
        {label}
      </FieldLabel>
      {children}
      {description ? <FieldDescription>{description}</FieldDescription> : null}
    </Field>
  );
}

function datasetLabel(dataset: DatasetResponse): string {
  const suffix = dataset.module_slug ? ` · ${dataset.module_slug}` : "";
  return `${dataset.name} · ${dataset.row_count} rows · ${dataset.format}${suffix}`;
}

function moduleDatasetDescription(module: GEPAModuleInfo | undefined): string {
  if (!module) return "Optimize an executor RLM skill prompt with offline traces.";
  const inputs = module.input_keys?.length ? `Inputs: ${module.input_keys.join(", ")}` : "";
  const outputs = module.output_keys?.length ? `Outputs: ${module.output_keys.join(", ")}` : "";
  const pieces = [inputs, outputs].filter(Boolean);
  return pieces.length
    ? pieces.join(" · ")
    : `Required keys: ${module.required_dataset_keys?.join(", ") ?? ""}`;
}

export type OptimizationFormProps = {
  onSuccess?: () => void;
};

export function OptimizationForm({ onSuccess }: OptimizationFormProps) {
  // Query state hooks directly localized
  const modulesQuery = useOptimizationModules();
  const [form, setForm] = useState<OptimizationRunFormState>(DEFAULT_OPTIMIZATION_FORM);
  const datasetsQuery = useOptimizationDatasets(
    form.targetMode === "module" ? form.moduleSlug : null,
    { enabled: form.targetMode !== "module" || Boolean(form.moduleSlug) },
  );
  const profilesQuery = useLlmProfiles();
  const modelsQuery = useLlmProfileModels(form.reflectionProfileId || null);

  const { uploadDataset, createRun, exportSessionTraces } = useOptimizationMutations();

  // Local UI states
  const [datasetFile, setDatasetFile] = useState<File | null>(null);
  const [traceSessionId, setTraceSessionId] = useState("");
  const [traceExport, setTraceExport] = useState<SessionTraceExportResponse | null>(null);

  const modules = modulesQuery.data ?? EMPTY_MODULES;
  const datasets = datasetsQuery.data?.items ?? EMPTY_DATASETS;
  const runnableDatasets = useMemo(() => datasets.filter(isRunnableDataset), [datasets]);
  const profiles = profilesQuery.data ?? EMPTY_PROFILES;
  const modelOptions = useMemo(
    () => modelsQuery.data?.models ?? EMPTY_MODELS,
    [modelsQuery.data?.models],
  );

  // Derive parameters inside form
  const selectedModule = useMemo(
    () => modules.find((module) => module.slug === form.moduleSlug),
    [form.moduleSlug, modules],
  );
  const selectedDataset = useMemo(
    () => datasets.find((dataset) => dataset.id === form.datasetId),
    [datasets, form.datasetId],
  );

  const isSubmitting = uploadDataset.isPending || createRun.isPending;

  // Sync / Initialize defaults
  useEffect(() => {
    if (form.moduleSlug || modules.length === 0) return;
    const firstModule = modules[0];
    if (!firstModule) return;
    setForm((current) => ({ ...current, moduleSlug: firstModule.slug }));
  }, [form.moduleSlug, modules]);

  useEffect(() => {
    if (form.datasetSource !== "existing" || form.datasetId || runnableDatasets.length === 0)
      return;
    const firstDataset = runnableDatasets[0];
    if (!firstDataset) return;
    setForm((current) => ({ ...current, datasetId: firstDataset.id }));
  }, [form.datasetId, form.datasetSource, runnableDatasets]);

  useEffect(() => {
    if (!form.reflectionProfileId || form.reflectionModelId || modelOptions.length === 0) return;
    const firstModel = modelOptions[0];
    if (!firstModel) return;
    setForm((current) => ({ ...current, reflectionModelId: firstModel.id }));
  }, [form.reflectionModelId, form.reflectionProfileId, modelOptions]);

  // Clear dataset selection when target mode or module changes to prevent cross-module dataset leakage
  useEffect(() => {
    setForm((current) => ({
      ...current,
      datasetId: "",
    }));
  }, [form.moduleSlug, form.targetMode]);

  const updateForm = <K extends keyof OptimizationRunFormState>(
    key: K,
    value: OptimizationRunFormState[K],
  ) => setForm((current) => ({ ...current, [key]: value }));

  const onReflectionProfileChange = (profileId: string) => {
    setForm((current) => ({
      ...current,
      reflectionProfileId: profileId,
      reflectionModelId: "",
    }));
  };

  const setDatasetSource = (value: DatasetSourceMode) => {
    setForm((current) => ({
      ...current,
      datasetSource: value,
      datasetId: value === "existing" ? current.datasetId : "",
      datasetPath: value === "path" ? current.datasetPath : "",
    }));
    if (value !== "upload") setDatasetFile(null);
  };

  const appendTraceBundlePath = (path: string) => {
    setForm((current) => ({
      ...current,
      traceBundlePaths: appendTraceBundlePathValue(current.traceBundlePaths, path),
    }));
  };

  const handleTraceExport = async () => {
    const sessionId = traceSessionId.trim();
    if (!sessionId) {
      toast.error("Session id is required");
      return;
    }
    try {
      const exported = await exportSessionTraces.mutateAsync({ sessionId });
      if (exported.trace_count === 0) {
        toast.warning("Trace export completed with no traces", {
          description: "Run a chat turn first or verify the session is linked to MLflow traces.",
        });
        return;
      }
      setTraceExport(exported);
      if (exported.distilled_bundle_path) {
        appendTraceBundlePath(exported.distilled_bundle_path);
      }
      toast.success("Trace bundle exported", {
        description: exported.distilled_bundle_path ?? exported.jsonl_path ?? exported.json_path,
      });
    } catch (error) {
      toast.error("Trace export failed", { description: errorMessage(error) });
    }
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      buildOptimizationRequest({ form, hasDatasetFile: Boolean(datasetFile) });
      let finalDatasetId = form.datasetId;
      if (form.datasetSource === "upload" && datasetFile) {
        const uploaded = await uploadDataset.mutateAsync({
          file: datasetFile,
          moduleSlug: form.targetMode === "module" ? form.moduleSlug : null,
        });
        finalDatasetId = uploaded.id;
        setDatasetFile(null);
        setForm((current) => ({
          ...current,
          datasetSource: "existing",
          datasetId: uploaded.id,
        }));
      }
      const request = buildOptimizationRequest({
        form: {
          ...form,
          datasetSource: "existing",
          datasetId: finalDatasetId,
        },
        datasetId: finalDatasetId,
        hasDatasetFile: false,
      });
      const created = await createRun.mutateAsync(request);
      toast.success("GEPA run started", { description: created.run_id });
      onSuccess?.();
    } catch (error) {
      toast.error("GEPA run not started", { description: errorMessage(error) });
    }
  };

  return (
    <SectionCard
      variant="subtle"
      className="border-border bg-card shadow-sm transition-all duration-200"
    >
      <SectionCardHeader className="border-b border-border-subtle bg-muted/10 px-6 py-5">
        <div className="flex items-center gap-2">
          <Settings2 className="size-5 text-primary" />
          <SectionCardTitle className="text-base font-semibold tracking-tight">
            New GEPA Run
          </SectionCardTitle>
        </div>
        <SectionCardDescription className="text-muted-foreground typo-body-sm mt-1">
          {moduleDatasetDescription(selectedModule)}
        </SectionCardDescription>
      </SectionCardHeader>
      <SectionCardContent className="p-6">
        <form className="flex flex-col gap-6" onSubmit={handleSubmit}>
          <div className="grid gap-6 lg:grid-cols-2">
            {/* Left Column: Target Metadata & Config */}
            <FieldGroup className="gap-5">
              <CompactField label="Target Mode" icon={<Sparkles className="size-4 text-primary" />}>
                <ToggleGroup
                  value={form.targetMode}
                  onValueChange={(value) => {
                    if (value) updateForm("targetMode", value as OptimizationTargetMode);
                  }}
                  variant="outline"
                  className="w-full flex"
                  disabled={isSubmitting}
                >
                  <ToggleGroupItem
                    value="module"
                    className="flex-1 font-medium transition-colors"
                    disabled={isSubmitting}
                  >
                    Registered module
                  </ToggleGroupItem>
                  <ToggleGroupItem
                    value="skill"
                    className="flex-1 font-medium transition-colors"
                    disabled={isSubmitting}
                  >
                    Skill file
                  </ToggleGroupItem>
                </ToggleGroup>
              </CompactField>

              {form.targetMode === "module" ? (
                <CompactField label="Registered Module">
                  <Select
                    value={form.moduleSlug}
                    onValueChange={(value) => value && updateForm("moduleSlug", value)}
                    disabled={isSubmitting || modulesQuery.isLoading || modules.length === 0}
                  >
                    <SelectTrigger className="h-10 w-full">
                      <SelectValue>
                        {selectedModule?.label ?? (form.moduleSlug || "Select module")}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectPositioner align="start">
                      <SelectContent className="border-border">
                        <SelectGroup>
                          {modules.map((module) => (
                            <SelectItem key={module.slug} value={module.slug}>
                              {module.label}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </SelectPositioner>
                  </Select>
                  {selectedModule ? (
                    <div className="rounded-lg border border-border-subtle bg-muted/10 px-4 py-3 text-xs text-muted-foreground leading-normal transition-all duration-200">
                      <div className="flex flex-wrap gap-1.5 mb-2">
                        <Badge variant="outline" className="border-border-subtle bg-background">
                          {selectedModule.optimization_target_kind ?? "custom"}
                        </Badge>
                        {selectedModule.signature_class_name ? (
                          <Badge variant="secondary" className="bg-muted text-muted-foreground">
                            {selectedModule.signature_class_name}
                          </Badge>
                        ) : null}
                        {selectedModule.runtime_module_name ? (
                          <Badge variant="secondary" className="bg-muted text-muted-foreground">
                            {selectedModule.runtime_module_name}
                          </Badge>
                        ) : null}
                      </div>
                      {selectedModule.description ? (
                        <div className="mt-1">{selectedModule.description}</div>
                      ) : null}
                    </div>
                  ) : null}
                </CompactField>
              ) : (
                <>
                  <CompactField label="Skill Target Sub-mode">
                    <ToggleGroup
                      value={form.skillTargetMode}
                      onValueChange={(value) => {
                        if (value) updateForm("skillTargetMode", value as SkillTargetMode);
                      }}
                      variant="outline"
                      className="w-full flex"
                      disabled={isSubmitting}
                    >
                      <ToggleGroupItem
                        value="name"
                        className="flex-1 font-medium transition-colors"
                        disabled={isSubmitting}
                      >
                        Bundled name
                      </ToggleGroupItem>
                      <ToggleGroupItem
                        value="path"
                        className="flex-1 font-medium transition-colors"
                        disabled={isSubmitting}
                      >
                        Skill path
                      </ToggleGroupItem>
                    </ToggleGroup>
                  </CompactField>
                  {form.skillTargetMode === "name" ? (
                    <CompactField label="Skill Name">
                      <Input
                        value={form.skillName}
                        onChange={(event) => updateForm("skillName", event.target.value)}
                        placeholder="optimization"
                        disabled={isSubmitting}
                        className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary"
                      />
                    </CompactField>
                  ) : (
                    <CompactField label="Skill Path">
                      <Input
                        value={form.skillPath}
                        onChange={(event) => updateForm("skillPath", event.target.value)}
                        placeholder="skills/custom/SKILL.md"
                        disabled={isSubmitting}
                        className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary"
                      />
                    </CompactField>
                  )}
                </>
              )}

              <Separator className="bg-border-subtle/50 my-1" />

              {/* Tuning Parameters */}
              <div className="grid gap-3 sm:grid-cols-4">
                <CompactField label="Auto">
                  <Select
                    value={form.auto}
                    onValueChange={(value) =>
                      updateForm("auto", value as OptimizationRunFormState["auto"])
                    }
                    disabled={isSubmitting}
                  >
                    <SelectTrigger className="h-10 w-full">
                      <SelectValue>{form.auto}</SelectValue>
                    </SelectTrigger>
                    <SelectPositioner align="start">
                      <SelectContent className="border-border">
                        <SelectGroup>
                          <SelectItem value="light">light</SelectItem>
                          <SelectItem value="medium">medium</SelectItem>
                          <SelectItem value="heavy">heavy</SelectItem>
                        </SelectGroup>
                      </SelectContent>
                    </SelectPositioner>
                  </Select>
                </CompactField>
                <CompactField label="Train Ratio">
                  <Input
                    value={form.trainRatio}
                    onChange={(event) => updateForm("trainRatio", event.target.value)}
                    inputMode="decimal"
                    disabled={isSubmitting}
                    className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary text-center font-mono"
                  />
                </CompactField>
                <CompactField label="Max Calls">
                  <Input
                    value={form.maxMetricCalls}
                    onChange={(event) => updateForm("maxMetricCalls", event.target.value)}
                    inputMode="numeric"
                    placeholder="auto"
                    disabled={isSubmitting}
                    className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary text-center font-mono"
                  />
                </CompactField>
                <CompactField label="Output Path">
                  <Input
                    value={form.outputPath}
                    onChange={(event) => updateForm("outputPath", event.target.value)}
                    placeholder="optional"
                    disabled={isSubmitting}
                    className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary font-mono"
                  />
                </CompactField>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <CompactField label="Reflection Profile">
                  <Select
                    value={form.reflectionProfileId || DEFAULT_SELECT_VALUE}
                    onValueChange={(value) =>
                      onReflectionProfileChange(
                        value === DEFAULT_SELECT_VALUE || !value ? "" : value,
                      )
                    }
                    disabled={isSubmitting || profilesQuery.isLoading}
                  >
                    <SelectTrigger className="h-10 w-full">
                      <SelectValue>
                        {profiles.find((profile) => profile.id === form.reflectionProfileId)
                          ?.name ?? "Default reflection model"}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectPositioner align="start">
                      <SelectContent className="border-border">
                        <SelectGroup>
                          <SelectItem value={DEFAULT_SELECT_VALUE}>
                            Default reflection model
                          </SelectItem>
                          {profiles.map((profile) => (
                            <SelectItem key={profile.id} value={profile.id}>
                              {profile.name}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </SelectPositioner>
                  </Select>
                </CompactField>
                <CompactField label="Reflection Model">
                  {modelsQuery.isPending && form.reflectionProfileId ? (
                    <Skeleton className="h-10 w-full rounded-md" />
                  ) : (
                    <Select
                      value={form.reflectionModelId}
                      onValueChange={(value) => value && updateForm("reflectionModelId", value)}
                      disabled={
                        isSubmitting || !form.reflectionProfileId || modelOptions.length === 0
                      }
                    >
                      <SelectTrigger className="h-10 w-full">
                        <SelectValue>
                          {modelOptions.find((model) => model.id === form.reflectionModelId)
                            ?.label ?? "Select model"}
                        </SelectValue>
                      </SelectTrigger>
                      <SelectPositioner align="start">
                        <SelectContent className="border-border">
                          <SelectGroup>
                            {modelOptions.map((model) => (
                              <SelectItem key={model.id} value={model.id}>
                                {model.label}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        </SelectContent>
                      </SelectPositioner>
                    </Select>
                  )}
                </CompactField>
              </div>
            </FieldGroup>

            {/* Right Column: Dataset Setup & Ingestion */}
            <FieldGroup className="gap-5">
              <CompactField
                label="Dataset Source"
                icon={<DatabaseZap className="size-4 text-primary" />}
              >
                <ToggleGroup
                  value={form.datasetSource}
                  onValueChange={(value) => {
                    if (value) setDatasetSource(value as DatasetSourceMode);
                  }}
                  variant="outline"
                  className="w-full flex"
                  disabled={isSubmitting}
                >
                  <ToggleGroupItem
                    value="existing"
                    className="flex-1 font-medium transition-colors"
                    disabled={isSubmitting}
                  >
                    Existing
                  </ToggleGroupItem>
                  <ToggleGroupItem
                    value="upload"
                    className="flex-1 font-medium transition-colors"
                    disabled={isSubmitting}
                  >
                    Upload file
                  </ToggleGroupItem>
                  <ToggleGroupItem
                    value="path"
                    className="flex-1 font-medium transition-colors"
                    disabled={isSubmitting}
                  >
                    Server path
                  </ToggleGroupItem>
                </ToggleGroup>
              </CompactField>

              {form.datasetSource === "existing" ? (
                <CompactField
                  label="Registered Dataset"
                  description={
                    selectedDataset
                      ? datasetLabel(selectedDataset)
                      : "Registered JSON/JSONL datasets"
                  }
                >
                  <Select
                    value={form.datasetId}
                    onValueChange={(value) => value && updateForm("datasetId", value)}
                    disabled={isSubmitting || datasetsQuery.isLoading || datasets.length === 0}
                  >
                    <SelectTrigger className="h-10 w-full">
                      <SelectValue>
                        {selectedDataset?.name ??
                          (datasetsQuery.isLoading ? "Loading datasets" : "Select dataset")}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectPositioner align="start">
                      <SelectContent className="border-border">
                        <SelectGroup>
                          {datasets.map((dataset) => (
                            <SelectItem
                              key={dataset.id}
                              value={dataset.id}
                              disabled={!isRunnableDataset(dataset)}
                            >
                              <div className="flex items-center gap-2">
                                <Database
                                  className="size-4 shrink-0 text-muted-foreground"
                                  data-icon="inline-start"
                                />
                                <span className="truncate">{datasetLabel(dataset)}</span>
                              </div>
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </SelectPositioner>
                  </Select>
                </CompactField>
              ) : null}

              {form.datasetSource === "upload" ? (
                <CompactField
                  label="Dataset File Upload"
                  description="Upload is registered before run creation."
                >
                  <div className="flex min-h-10 items-center gap-2 rounded-lg border border-input px-3 py-1 bg-background hover:border-border-subtle transition-colors duration-150">
                    <Upload
                      className="shrink-0 size-4 text-muted-foreground"
                      data-icon="inline-start"
                    />
                    <Input
                      key={datasetFile ? "loaded" : "empty"}
                      type="file"
                      accept=".json,.jsonl,application/json"
                      disabled={isSubmitting}
                      className="h-auto border-0 p-0 shadow-none file:mr-3 file:rounded-md file:border-0 file:bg-muted file:hover:bg-muted/80 file:px-2 file:py-1 file:text-xs file:font-medium file:cursor-pointer cursor-pointer text-xs"
                      onChange={(event) => setDatasetFile(event.target.files?.item(0) ?? null)}
                    />
                  </div>
                </CompactField>
              ) : null}

              {form.datasetSource === "path" ? (
                <CompactField label="Server Dataset Path">
                  <Input
                    value={form.datasetPath}
                    onChange={(event) => updateForm("datasetPath", event.target.value)}
                    placeholder="artifacts/optimization/dataset.jsonl"
                    disabled={isSubmitting}
                    className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary font-mono text-xs"
                  />
                </CompactField>
              ) : null}

              <CompactField
                label="Export Session Traces"
                description="Writes full MLflow traces and appends the distilled bundle path."
              >
                <div className="flex gap-2">
                  <Input
                    value={traceSessionId}
                    onChange={(event) => setTraceSessionId(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void handleTraceExport();
                      }
                    }}
                    placeholder="session id"
                    disabled={isSubmitting || exportSessionTraces.isPending}
                    className="h-10 border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary font-mono text-sm"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    disabled={isSubmitting || exportSessionTraces.isPending}
                    onClick={() => void handleTraceExport()}
                    className="h-10 px-4 font-medium transition-colors hover:bg-muted/40 shadow-none border-input"
                  >
                    <FileJson className="size-4 shrink-0" data-icon="inline-start" />
                    Export
                  </Button>
                </div>
              </CompactField>

              {traceExport ? (
                <Alert className="border-border bg-muted/20 rounded-lg">
                  <FileJson className="size-4 text-muted-foreground" />
                  <AlertTitle className="text-sm font-semibold text-foreground">
                    Trace bundle ready
                  </AlertTitle>
                  <AlertDescription className="text-xs text-muted-foreground mt-0.5">
                    {traceExport.trace_count} trace(s). {traceExport.distilled_bundle_path}
                  </AlertDescription>
                </Alert>
              ) : null}

              <CompactField label="Trace Bundle Paths">
                <Textarea
                  value={form.traceBundlePaths}
                  onChange={(event) => updateForm("traceBundlePaths", event.target.value)}
                  disabled={isSubmitting}
                  className="min-h-24 resize-y border-input bg-background shadow-none transition-colors hover:border-border-subtle focus-visible:ring-ring focus-visible:border-primary font-mono text-xs leading-normal p-3 rounded-lg"
                  placeholder="artifacts/traces/sessions/.../mlflow-traces.distilled.jsonl"
                />
              </CompactField>
            </FieldGroup>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-5 mt-2">
            <div className="min-w-0 text-xs text-muted-foreground leading-normal font-normal">
              {datasetFile ? (
                <span className="truncate block max-w-sm">Upload: {datasetFile.name}</span>
              ) : (
                <span>Optimizer: GEPA &middot; proposer: Daytona RLM</span>
              )}
            </div>
            <Button
              type="submit"
              disabled={isSubmitting}
              className="font-medium h-10 px-5 shadow-xs transition-colors"
            >
              {isSubmitting ? "Starting..." : "Start GEPA run"}
            </Button>
          </div>
        </form>
      </SectionCardContent>
    </SectionCard>
  );
}

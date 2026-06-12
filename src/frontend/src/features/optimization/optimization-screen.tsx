import { useEffect, useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Database, FileJson, FlaskConical, Upload } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { PageHeader } from "@/components/product/page-header";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { useLlmProfileModels, useLlmProfiles } from "@/features/settings/use-llm-profiles";
import type {
  DatasetResponse,
  GEPAModuleInfo,
  OptimizationPromotionDraftResponse,
  OptimizationRunResponse,
  SessionTraceExportResponse,
} from "@/lib/rlm-api";

import {
  appendTraceBundlePathValue,
  buildOptimizationRequest,
  DEFAULT_OPTIMIZATION_FORM,
  type DatasetSourceMode,
  type OptimizationRunFormState,
  type OptimizationTargetMode,
  type SkillTargetMode,
} from "./optimization-model";
import { errorMessage } from "./optimization-format";
import { RunDetailsSheet } from "./run-details-sheet";
import { RunHistory } from "./run-history";
import {
  useOptimizationDatasets,
  useOptimizationModules,
  useOptimizationMutations,
  useOptimizationRunDetails,
  useOptimizationRuns,
  useOptimizationStatus,
} from "./use-optimization";

type OptimizationTab = "new-run" | "history";

const DEFAULT_SELECT_VALUE = "__default__";
const EMPTY_MODULES: GEPAModuleInfo[] = [];
const EMPTY_DATASETS: DatasetResponse[] = [];
const EMPTY_RUNS: OptimizationRunResponse[] = [];

function CompactField({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Field className="gap-1.5">
      <FieldLabel>{label}</FieldLabel>
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
  return pieces.length ? pieces.join(" · ") : `Required keys: ${module.required_dataset_keys.join(", ")}`;
}

function isRunnableDataset(dataset: DatasetResponse): boolean {
  return dataset.row_count > 0;
}

export function OptimizationScreen() {
  const isMobile = useIsMobile();
  const statusQuery = useOptimizationStatus();
  const modulesQuery = useOptimizationModules();
  const [form, setForm] = useState<OptimizationRunFormState>(DEFAULT_OPTIMIZATION_FORM);
  const datasetsQuery = useOptimizationDatasets(
    form.targetMode === "module" ? form.moduleSlug : null,
  );
  const runsQuery = useOptimizationRuns();
  const profilesQuery = useLlmProfiles();
  const modelsQuery = useLlmProfileModels(form.reflectionProfileId || null);
  const { uploadDataset, createRun, createPromotionDraft, exportSessionTraces } =
    useOptimizationMutations();
  const [activeTab, setActiveTab] = useState<OptimizationTab>("new-run");
  const [datasetFile, setDatasetFile] = useState<File | null>(null);
  const [traceSessionId, setTraceSessionId] = useState("");
  const [traceExport, setTraceExport] = useState<SessionTraceExportResponse | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [promotionDraft, setPromotionDraft] = useState<OptimizationPromotionDraftResponse | null>(null);
  const runDetailsQuery = useOptimizationRunDetails(selectedRunId);

  const modules = modulesQuery.data ?? EMPTY_MODULES;
  const datasets = datasetsQuery.data?.items ?? EMPTY_DATASETS;
  const runnableDatasets = useMemo(() => datasets.filter(isRunnableDataset), [datasets]);
  const runs = runsQuery.data ?? EMPTY_RUNS;
  const profiles = profilesQuery.data ?? [];
  const modelOptions = useMemo(() => modelsQuery.data?.models ?? [], [modelsQuery.data?.models]);
  const selectedModule = useMemo(
    () => modules.find((module) => module.slug === form.moduleSlug),
    [form.moduleSlug, modules],
  );
  const selectedDataset = useMemo(
    () => datasets.find((dataset) => dataset.id === form.datasetId),
    [datasets, form.datasetId],
  );
  const isSubmitting = uploadDataset.isPending || createRun.isPending;

  useEffect(() => {
    if (form.moduleSlug || modules.length === 0) return;
    const firstModule = modules[0];
    if (!firstModule) return;
    setForm((current) => ({ ...current, moduleSlug: firstModule.slug }));
  }, [form.moduleSlug, modules]);

  useEffect(() => {
    if (form.datasetSource !== "existing" || form.datasetId || runnableDatasets.length === 0) return;
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

  const updateForm = <K extends keyof OptimizationRunFormState>(
    key: K,
    value: OptimizationRunFormState[K],
  ) => setForm((current) => ({ ...current, [key]: value }));

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
    setForm((current) => {
      return {
        ...current,
        traceBundlePaths: appendTraceBundlePathValue(current.traceBundlePaths, path),
      };
    });
  };

  const handleTraceExport = async () => {
    const sessionId = traceSessionId.trim();
    if (!sessionId) {
      toast.error("Session id is required");
      return;
    }
    try {
      const exported = await exportSessionTraces.mutateAsync({ sessionId });
      setTraceExport(exported);
      if (exported.distilled_bundle_path) appendTraceBundlePath(exported.distilled_bundle_path);
      if (exported.trace_count === 0) {
        toast.warning("Trace export completed with no traces", {
          description:
            "Run a chat turn first or verify the session is linked to MLflow traces.",
        });
        return;
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
      const uploaded =
        form.datasetSource === "upload" && datasetFile
          ? await uploadDataset.mutateAsync({
              file: datasetFile,
              moduleSlug: form.targetMode === "module" ? form.moduleSlug : null,
            })
          : null;
      const request = buildOptimizationRequest({
        form,
        datasetId: uploaded?.id,
        hasDatasetFile: false,
      });
      const created = await createRun.mutateAsync(request);
      toast.success("GEPA run started", { description: created.run_id });
      setDatasetFile(null);
      setActiveTab("history");
    } catch (error) {
      toast.error("GEPA run not started", { description: errorMessage(error) });
    }
  };

  const openRunDetails = (runId: string) => {
    setPromotionDraft(null);
    setSelectedRunId(runId);
  };

  const handleCreatePromotionDraft = async () => {
    if (!selectedRunId) return;
    try {
      const draft = await createPromotionDraft.mutateAsync(selectedRunId);
      setPromotionDraft(draft);
      toast.success("Promotion draft created", { description: draft.draft_path });
    } catch (error) {
      toast.error("Promotion draft not created", { description: errorMessage(error) });
    }
  };

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      <RunDetailsSheet
        runId={selectedRunId}
        open={Boolean(selectedRunId)}
        onOpenChange={(open) => {
          if (!open) setSelectedRunId(null);
        }}
        detail={runDetailsQuery.data}
        isLoading={runDetailsQuery.isLoading}
        error={runDetailsQuery.error}
        draft={promotionDraft}
        isDraftPending={createPromotionDraft.isPending}
        onCreateDraft={() => void handleCreatePromotionDraft()}
      />

      {!isMobile ? (
        <PageHeader
          isMobile={false}
          title="Optimization"
          description="Run offline RLM-GEPA prompt optimization from datasets and distilled traces."
          maxWidth="max-w-page"
        />
      ) : null}

      <ScrollArea className="min-h-0 flex-1">
        {isMobile ? (
          <PageHeader
            isMobile
            title="Optimization"
            description="Run offline RLM-GEPA prompt optimization from datasets and distilled traces."
          />
        ) : null}

        <div className="mx-auto flex w-full max-w-page flex-col gap-4 px-4 py-4 md:px-6">
          {statusQuery.data && !statusQuery.data.available ? (
            <Alert>
              <FlaskConical className="text-muted-foreground" />
              <AlertTitle>GEPA unavailable</AlertTitle>
              <AlertDescription>
                {statusQuery.data.guidance ?? "The optimizer is not available in this environment."}
              </AlertDescription>
            </Alert>
          ) : null}

          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as OptimizationTab)}
            className="min-h-0"
          >
            <div className="flex items-center justify-between gap-3">
              <TabsList>
                <TabsTrigger value="new-run">New Run</TabsTrigger>
                <TabsTrigger value="history">Run History</TabsTrigger>
              </TabsList>
              <Badge variant="outline">RLM-GEPA</Badge>
            </div>

            <TabsContent value="new-run" className="mt-2">
              <SectionCard variant="subtle">
                <SectionCardHeader>
                  <SectionCardTitle>New Run</SectionCardTitle>
                  <SectionCardDescription>
                    {moduleDatasetDescription(selectedModule)}
                  </SectionCardDescription>
                </SectionCardHeader>
                <SectionCardContent>
                  <form className="flex flex-col gap-5" onSubmit={handleSubmit}>
                    <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
                      <FieldGroup>
                        <CompactField label="Target">
                          <ToggleGroup
                            type="single"
                            value={form.targetMode}
                            onValueChange={(value) => {
                              if (value) updateForm("targetMode", value as OptimizationTargetMode);
                            }}
                            variant="outline"
                            className="w-full"
                          >
                            <ToggleGroupItem value="module">Registered module</ToggleGroupItem>
                            <ToggleGroupItem value="skill">Skill</ToggleGroupItem>
                          </ToggleGroup>
                        </CompactField>

                        {form.targetMode === "module" ? (
                          <CompactField label="Module">
                            <Select
                              value={form.moduleSlug}
                              onValueChange={(value) => {
                                if (typeof value === "string") updateForm("moduleSlug", value);
                              }}
                              disabled={modulesQuery.isLoading || modules.length === 0}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue>
                                  {selectedModule?.label ?? form.moduleSlug ?? "Select module"}
                                </SelectValue>
                              </SelectTrigger>
                              <SelectContent align="start">
                                <SelectGroup>
                                  {modules.map((module) => (
                                    <SelectItem key={module.slug} value={module.slug}>
                                      {module.label}
                                    </SelectItem>
                                  ))}
                                </SelectGroup>
                              </SelectContent>
                            </Select>
                            {selectedModule ? (
                              <div className="rounded-md border border-border-subtle bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                                <div className="flex flex-wrap gap-1.5">
                                  <Badge variant="outline">
                                    {selectedModule.optimization_target_kind ?? "custom"}
                                  </Badge>
                                  {selectedModule.signature_class_name ? (
                                    <Badge variant="secondary">{selectedModule.signature_class_name}</Badge>
                                  ) : null}
                                  {selectedModule.runtime_module_name ? (
                                    <Badge variant="secondary">{selectedModule.runtime_module_name}</Badge>
                                  ) : null}
                                </div>
                                {selectedModule.description ? (
                                  <div className="mt-2">{selectedModule.description}</div>
                                ) : null}
                              </div>
                            ) : null}
                          </CompactField>
                        ) : (
                          <>
                            <CompactField label="Skill target">
                              <ToggleGroup
                                type="single"
                                value={form.skillTargetMode}
                                onValueChange={(value) => {
                                  if (value) updateForm("skillTargetMode", value as SkillTargetMode);
                                }}
                                variant="outline"
                                className="w-full"
                              >
                                <ToggleGroupItem value="name">Bundled name</ToggleGroupItem>
                                <ToggleGroupItem value="path">Skill path</ToggleGroupItem>
                              </ToggleGroup>
                            </CompactField>
                            {form.skillTargetMode === "name" ? (
                              <CompactField label="Skill name">
                                <Input
                                  value={form.skillName}
                                  onChange={(event) => updateForm("skillName", event.target.value)}
                                  placeholder="optimization"
                                />
                              </CompactField>
                            ) : (
                              <CompactField label="Skill path">
                                <Input
                                  value={form.skillPath}
                                  onChange={(event) => updateForm("skillPath", event.target.value)}
                                  placeholder="skills/custom/SKILL.md"
                                />
                              </CompactField>
                            )}
                          </>
                        )}

                        <div className="grid gap-3 sm:grid-cols-4">
                          <CompactField label="Auto">
                            <Select
                              value={form.auto}
                              onValueChange={(value) =>
                                typeof value === "string"
                                  ? updateForm("auto", value as OptimizationRunFormState["auto"])
                                  : undefined
                              }
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue>{form.auto}</SelectValue>
                              </SelectTrigger>
                              <SelectContent align="start">
                                <SelectGroup>
                                  <SelectItem value="light">light</SelectItem>
                                  <SelectItem value="medium">medium</SelectItem>
                                  <SelectItem value="heavy">heavy</SelectItem>
                                </SelectGroup>
                              </SelectContent>
                            </Select>
                          </CompactField>
                          <CompactField label="Train ratio">
                            <Input
                              value={form.trainRatio}
                              onChange={(event) => updateForm("trainRatio", event.target.value)}
                              inputMode="decimal"
                            />
                          </CompactField>
                          <CompactField label="Max calls">
                            <Input
                              value={form.maxMetricCalls}
                              onChange={(event) => updateForm("maxMetricCalls", event.target.value)}
                              inputMode="numeric"
                              placeholder="auto"
                            />
                          </CompactField>
                          <CompactField label="Output path">
                            <Input
                              value={form.outputPath}
                              onChange={(event) => updateForm("outputPath", event.target.value)}
                              placeholder="optional"
                            />
                          </CompactField>
                        </div>

                        <div className="grid gap-3 sm:grid-cols-2">
                          <CompactField label="Reflection profile">
                            <Select
                              value={form.reflectionProfileId || DEFAULT_SELECT_VALUE}
                              onValueChange={(value) => {
                                const nextValue = value === DEFAULT_SELECT_VALUE ? "" : String(value);
                                setForm((current) => ({
                                  ...current,
                                  reflectionProfileId: nextValue,
                                  reflectionModelId: "",
                                }));
                              }}
                              disabled={profilesQuery.isLoading}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue>
                                  {profiles.find((profile) => profile.id === form.reflectionProfileId)
                                    ?.name ?? "Default reflection model"}
                                </SelectValue>
                              </SelectTrigger>
                              <SelectContent align="start">
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
                            </Select>
                          </CompactField>
                          <CompactField label="Reflection model">
                            {modelsQuery.isPending && form.reflectionProfileId ? (
                              <Skeleton className="h-9 w-full rounded-md" />
                            ) : (
                              <Select
                                value={form.reflectionModelId}
                                onValueChange={(value) => updateForm("reflectionModelId", String(value))}
                                disabled={!form.reflectionProfileId || modelOptions.length === 0}
                              >
                                <SelectTrigger className="w-full">
                                  <SelectValue>
                                    {modelOptions.find((model) => model.id === form.reflectionModelId)
                                      ?.label ?? "Select model"}
                                  </SelectValue>
                                </SelectTrigger>
                                <SelectContent align="start">
                                  <SelectGroup>
                                    {modelOptions.map((model) => (
                                      <SelectItem key={model.id} value={model.id}>
                                        {model.label}
                                      </SelectItem>
                                    ))}
                                  </SelectGroup>
                                </SelectContent>
                              </Select>
                            )}
                          </CompactField>
                        </div>
                      </FieldGroup>

                      <FieldGroup>
                        <CompactField label="Dataset source">
                          <ToggleGroup
                            type="single"
                            value={form.datasetSource}
                            onValueChange={(value) => {
                              if (value) setDatasetSource(value as DatasetSourceMode);
                            }}
                            variant="outline"
                            className="w-full"
                          >
                            <ToggleGroupItem value="existing">Existing</ToggleGroupItem>
                            <ToggleGroupItem value="upload">Upload</ToggleGroupItem>
                            <ToggleGroupItem value="path">Server path</ToggleGroupItem>
                          </ToggleGroup>
                        </CompactField>

                        {form.datasetSource === "existing" ? (
                          <CompactField
                            label="Dataset"
                            description={
                              selectedDataset ? datasetLabel(selectedDataset) : "Registered JSON/JSONL datasets"
                            }
                          >
                            <Select
                              value={form.datasetId}
                              onValueChange={(value) => updateForm("datasetId", String(value))}
                              disabled={datasetsQuery.isLoading || datasets.length === 0}
                            >
                              <SelectTrigger className="w-full">
                                <SelectValue>
                                  {selectedDataset?.name ??
                                    (datasetsQuery.isLoading ? "Loading datasets" : "Select dataset")}
                                </SelectValue>
                              </SelectTrigger>
                              <SelectContent align="start">
                                <SelectGroup>
                                  {datasets.map((dataset) => (
                                    <SelectItem
                                      key={dataset.id}
                                      value={dataset.id}
                                      disabled={!isRunnableDataset(dataset)}
                                    >
                                      <Database data-icon="inline-start" />
                                      {datasetLabel(dataset)}
                                    </SelectItem>
                                  ))}
                                </SelectGroup>
                              </SelectContent>
                            </Select>
                          </CompactField>
                        ) : null}

                        {form.datasetSource === "upload" ? (
                          <CompactField label="Dataset file" description="Upload is registered before run creation.">
                            <div className="flex min-h-9 items-center gap-2 rounded-md border border-input px-3 py-2">
                              <Upload className="shrink-0 text-muted-foreground" data-icon="inline-start" />
                              <Input
                                type="file"
                                accept=".json,.jsonl,application/json"
                                className="h-auto border-0 p-0 shadow-none file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-2 file:py-1 file:text-xs"
                                onChange={(event) =>
                                  setDatasetFile(event.target.files?.item(0) ?? null)
                                }
                              />
                            </div>
                          </CompactField>
                        ) : null}

                        {form.datasetSource === "path" ? (
                          <CompactField label="Server dataset path">
                            <Input
                              value={form.datasetPath}
                              onChange={(event) => updateForm("datasetPath", event.target.value)}
                              placeholder="artifacts/optimization/dataset.jsonl"
                            />
                          </CompactField>
                        ) : null}

                        <CompactField
                          label="Export session traces"
                          description="Writes full MLflow traces and appends the distilled bundle path."
                        >
                          <div className="flex gap-2">
                            <Input
                              value={traceSessionId}
                              onChange={(event) => setTraceSessionId(event.target.value)}
                              placeholder="session id"
                            />
                            <Button
                              type="button"
                              variant="outline"
                              disabled={exportSessionTraces.isPending}
                              onClick={() => void handleTraceExport()}
                            >
                              <FileJson data-icon="inline-start" />
                              Export
                            </Button>
                          </div>
                        </CompactField>

                        {traceExport ? (
                          <Alert>
                            <FileJson className="text-muted-foreground" />
                            <AlertTitle>Trace bundle ready</AlertTitle>
                            <AlertDescription>
                              {traceExport.trace_count} trace(s). {traceExport.distilled_bundle_path}
                            </AlertDescription>
                          </Alert>
                        ) : null}

                        <CompactField label="Trace bundle paths">
                          <Textarea
                            value={form.traceBundlePaths}
                            onChange={(event) => updateForm("traceBundlePaths", event.target.value)}
                            className="min-h-24 resize-y"
                            placeholder="artifacts/traces/sessions/.../mlflow-traces.distilled.jsonl"
                          />
                        </CompactField>
                      </FieldGroup>
                    </div>

                    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-4">
                      <div className="min-w-0 text-xs text-muted-foreground">
                        {datasetFile ? (
                          <span className="truncate">Upload: {datasetFile.name}</span>
                        ) : (
                          <span>Optimizer: GEPA · proposer: Daytona RLM</span>
                        )}
                      </div>
                      <Button type="submit" disabled={isSubmitting}>
                        {isSubmitting ? "Starting..." : "Start GEPA run"}
                      </Button>
                    </div>
                  </form>
                </SectionCardContent>
              </SectionCard>
            </TabsContent>

            <TabsContent value="history" className="mt-2">
              <RunHistory
                runs={runs}
                isLoading={runsQuery.isLoading}
                error={runsQuery.error}
                refetch={() => void runsQuery.refetch()}
                onSelectRun={openRunDetails}
              />
            </TabsContent>
          </Tabs>
        </div>
      </ScrollArea>
    </div>
  );
}

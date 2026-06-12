import { useEffect, useMemo, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { FlaskConical } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/product/page-header";
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
  isRunnableDataset,
  type DatasetSourceMode,
  type OptimizationRunFormState,
} from "./optimization-model";
import { errorMessage } from "./optimization-format";
import { OptimizationForm } from "./optimization-form";
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

const EMPTY_MODULES: GEPAModuleInfo[] = [];
const EMPTY_DATASETS: DatasetResponse[] = [];
const EMPTY_RUNS: OptimizationRunResponse[] = [];

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
  const [promotionDraft, setPromotionDraft] = useState<OptimizationPromotionDraftResponse | null>(
    null,
  );
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
      setTraceExport(exported);
      if (exported.distilled_bundle_path) appendTraceBundlePath(exported.distilled_bundle_path);
      if (exported.trace_count === 0) {
        toast.warning("Trace export completed with no traces", {
          description: "Run a chat turn first or verify the session is linked to MLflow traces.",
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
              <OptimizationForm
                form={form}
                updateForm={updateForm}
                onReflectionProfileChange={onReflectionProfileChange}
                setDatasetSource={setDatasetSource}
                modules={modules}
                modulesLoading={modulesQuery.isLoading}
                datasets={datasets}
                datasetsLoading={datasetsQuery.isLoading}
                selectedModule={selectedModule}
                selectedDataset={selectedDataset}
                profiles={profiles}
                profilesLoading={profilesQuery.isLoading}
                modelOptions={modelOptions}
                modelsPending={modelsQuery.isPending}
                datasetFile={datasetFile}
                onDatasetFileChange={setDatasetFile}
                traceSessionId={traceSessionId}
                onTraceSessionIdChange={setTraceSessionId}
                traceExport={traceExport}
                isSubmitting={isSubmitting}
                exportPending={exportSessionTraces.isPending}
                onSubmit={(event) => void handleSubmit(event)}
                onTraceExport={() => void handleTraceExport()}
              />
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

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { Separator } from "@/components/ui/separator";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
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
  appendTraceBundlePathValue,
  buildOptimizationRequest,
  DEFAULT_OPTIMIZATION_FORM,
  isRunnableDataset,
  type DatasetSourceMode,
  type OptimizationRunFormState,
} from "../optimization-model";
import { AdvancedSection } from "./advanced-section";
import { DatasetSection } from "./dataset-section";
import { ReflectionSection } from "./reflection-section";
import { TargetSection } from "./target-section";

const EMPTY_MODULES: GEPAModuleInfo[] = [];
const EMPTY_DATASETS: DatasetResponse[] = [];
const EMPTY_PROFILES: LlmProviderProfileResponse[] = [];
const EMPTY_MODELS: LlmModelCatalogEntry[] = [];

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
  const hasMounted = useRef(false);

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
    if (!hasMounted.current) {
      hasMounted.current = true;
      return;
    }
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
    <SectionCard variant="elevated">
      <SectionCardHeader className="border-b border-border-subtle bg-muted/10 px-6 py-5">
        <div className="flex items-center gap-2">
          <Settings2 className="size-5 text-primary" />
          <SectionCardTitle className="text-lg font-semibold tracking-tighter-custom">
            New GEPA Run
          </SectionCardTitle>
        </div>
        <SectionCardDescription className="text-muted-foreground typo-body-sm mt-1">
          {moduleDatasetDescription(selectedModule)}
        </SectionCardDescription>
      </SectionCardHeader>
      <SectionCardContent className="p-6">
        <form className="flex flex-col gap-6" onSubmit={handleSubmit}>
          <div className="flex flex-col gap-6">
            {/* Left Column: Target Metadata & Config */}
            <FieldGroup className="gap-5">
              <TargetSection
                form={form}
                updateForm={updateForm}
                modules={modules}
                modulesLoading={modulesQuery.isLoading}
                selectedModule={selectedModule}
                isSubmitting={isSubmitting}
              />

              <Separator className="bg-border-subtle/50 my-1" />

              <AdvancedSection form={form} updateForm={updateForm} isSubmitting={isSubmitting} />

              <ReflectionSection
                form={form}
                updateForm={updateForm}
                onReflectionProfileChange={onReflectionProfileChange}
                profiles={profiles}
                profilesLoading={profilesQuery.isLoading}
                modelOptions={modelOptions}
                modelsPending={modelsQuery.isPending}
                isSubmitting={isSubmitting}
              />
            </FieldGroup>

            {/* Right Column: Dataset Setup & Ingestion */}
            <FieldGroup className="gap-5">
              <DatasetSection
                form={form}
                updateForm={updateForm}
                setDatasetSource={setDatasetSource}
                datasets={datasets}
                datasetsLoading={datasetsQuery.isLoading}
                selectedDataset={selectedDataset}
                datasetFile={datasetFile}
                setDatasetFile={setDatasetFile}
                traceSessionId={traceSessionId}
                setTraceSessionId={setTraceSessionId}
                onTraceExport={() => void handleTraceExport()}
                exportPending={exportSessionTraces.isPending}
                traceExport={traceExport}
                isSubmitting={isSubmitting}
              />
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
            <Button type="submit" disabled={isSubmitting} className="font-medium px-5 shadow-xs">
              {isSubmitting ? "Starting..." : "Start GEPA run"}
            </Button>
          </div>
        </form>
      </SectionCardContent>
    </SectionCard>
  );
}

import type { FormEvent, ReactNode } from "react";
import { Database, FileJson, Upload } from "lucide-react";

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
import type {
  DatasetResponse,
  GEPAModuleInfo,
  SessionTraceExportResponse,
} from "@/lib/rlm-api";
import type { LlmModelCatalogEntry, LlmProviderProfileResponse } from "@/lib/rlm-api/llm-profiles";

import {
  isRunnableDataset,
  type DatasetSourceMode,
  type OptimizationRunFormState,
  type OptimizationTargetMode,
  type SkillTargetMode,
} from "./optimization-model";

const DEFAULT_SELECT_VALUE = "__default__";

function CompactField({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: ReactNode;
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
  return pieces.length
    ? pieces.join(" · ")
    : `Required keys: ${module.required_dataset_keys.join(", ")}`;
}

export type OptimizationFormProps = {
  form: OptimizationRunFormState;
  updateForm: <K extends keyof OptimizationRunFormState>(
    key: K,
    value: OptimizationRunFormState[K],
  ) => void;
  onReflectionProfileChange: (profileId: string) => void;
  setDatasetSource: (value: DatasetSourceMode) => void;
  modules: GEPAModuleInfo[];
  modulesLoading: boolean;
  datasets: DatasetResponse[];
  datasetsLoading: boolean;
  selectedModule: GEPAModuleInfo | undefined;
  selectedDataset: DatasetResponse | undefined;
  profiles: LlmProviderProfileResponse[];
  profilesLoading: boolean;
  modelOptions: LlmModelCatalogEntry[];
  modelsPending: boolean;
  datasetFile: File | null;
  onDatasetFileChange: (file: File | null) => void;
  traceSessionId: string;
  onTraceSessionIdChange: (value: string) => void;
  traceExport: SessionTraceExportResponse | null;
  isSubmitting: boolean;
  exportPending: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onTraceExport: () => void;
};

export function OptimizationForm({
  form,
  updateForm,
  onReflectionProfileChange,
  setDatasetSource,
  modules,
  modulesLoading,
  datasets,
  datasetsLoading,
  selectedModule,
  selectedDataset,
  profiles,
  profilesLoading,
  modelOptions,
  modelsPending,
  datasetFile,
  onDatasetFileChange,
  traceSessionId,
  onTraceSessionIdChange,
  traceExport,
  isSubmitting,
  exportPending,
  onSubmit,
  onTraceExport,
}: OptimizationFormProps) {
  return (
    <SectionCard variant="subtle">
      <SectionCardHeader>
        <SectionCardTitle>New Run</SectionCardTitle>
        <SectionCardDescription>{moduleDatasetDescription(selectedModule)}</SectionCardDescription>
      </SectionCardHeader>
      <SectionCardContent>
        <form className="flex flex-col gap-5" onSubmit={onSubmit}>
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
                    disabled={modulesLoading || modules.length === 0}
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
                      onReflectionProfileChange(nextValue);
                    }}
                    disabled={profilesLoading}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue>
                        {profiles.find((profile) => profile.id === form.reflectionProfileId)?.name ??
                          "Default reflection model"}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent align="start">
                      <SelectGroup>
                        <SelectItem value={DEFAULT_SELECT_VALUE}>Default reflection model</SelectItem>
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
                  {modelsPending && form.reflectionProfileId ? (
                    <Skeleton className="h-9 w-full rounded-md" />
                  ) : (
                    <Select
                      value={form.reflectionModelId}
                      onValueChange={(value) => updateForm("reflectionModelId", String(value))}
                      disabled={!form.reflectionProfileId || modelOptions.length === 0}
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue>
                          {modelOptions.find((model) => model.id === form.reflectionModelId)?.label ??
                            "Select model"}
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
                    disabled={datasetsLoading || datasets.length === 0}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue>
                        {selectedDataset?.name ??
                          (datasetsLoading ? "Loading datasets" : "Select dataset")}
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
                <CompactField
                  label="Dataset file"
                  description="Upload is registered before run creation."
                >
                  <div className="flex min-h-9 items-center gap-2 rounded-md border border-input px-3 py-2">
                    <Upload className="shrink-0 text-muted-foreground" data-icon="inline-start" />
                    <Input
                      type="file"
                      accept=".json,.jsonl,application/json"
                      className="h-auto border-0 p-0 shadow-none file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-2 file:py-1 file:text-xs"
                      onChange={(event) => onDatasetFileChange(event.target.files?.item(0) ?? null)}
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
                    onChange={(event) => onTraceSessionIdChange(event.target.value)}
                    placeholder="session id"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    disabled={exportPending}
                    onClick={onTraceExport}
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
  );
}

import type { Dispatch, SetStateAction } from "react";
import { Database, FileJson, Upload, DatabaseZap } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import type { DatasetResponse, SessionTraceExportResponse } from "@/lib/rlm-api";

import { CompactField } from "./form-field";
import { isRunnableDataset, type DatasetSourceMode, type OptimizationRunFormState } from "../optimization-model";

export function datasetLabel(dataset: DatasetResponse): string {
  const suffix = dataset.module_slug ? ` · ${dataset.module_slug}` : "";
  return `${dataset.name} · ${dataset.row_count} rows · ${dataset.format}${suffix}`;
}

export function DatasetSection({
  form,
  updateForm,
  setDatasetSource,
  datasets,
  datasetsLoading,
  selectedDataset,
  datasetFile,
  setDatasetFile,
  traceSessionId,
  setTraceSessionId,
  onTraceExport,
  exportPending,
  traceExport,
  isSubmitting,
}: {
  form: OptimizationRunFormState;
  updateForm: <K extends keyof OptimizationRunFormState>(
    key: K,
    value: OptimizationRunFormState[K],
  ) => void;
  setDatasetSource: (value: DatasetSourceMode) => void;
  datasets: DatasetResponse[];
  datasetsLoading: boolean;
  selectedDataset: DatasetResponse | undefined;
  datasetFile: File | null;
  setDatasetFile: Dispatch<SetStateAction<File | null>>;
  traceSessionId: string;
  setTraceSessionId: Dispatch<SetStateAction<string>>;
  onTraceExport: () => void;
  exportPending: boolean;
  traceExport: SessionTraceExportResponse | null;
  isSubmitting: boolean;
}) {
  return (
    <>
      <CompactField label="Dataset Source" icon={<DatabaseZap className="size-4 text-primary" />}>
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
            selectedDataset ? datasetLabel(selectedDataset) : "Registered JSON/JSONL datasets"
          }
        >
          <Select
            value={form.datasetId}
            onValueChange={(value) => value && updateForm("datasetId", value)}
            disabled={isSubmitting || datasetsLoading || datasets.length === 0}
          >
            <SelectTrigger className="w-full">
              <SelectValue>
                {selectedDataset?.name ?? (datasetsLoading ? "Loading datasets" : "Select dataset")}
              </SelectValue>
            </SelectTrigger>
            <SelectPositioner align="start">
              <SelectContent className="border-border">
                <SelectGroup>
                  {datasets.map((dataset) => (
                    <SelectItem key={dataset.id} value={dataset.id} disabled={!isRunnableDataset(dataset)}>
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
            <Upload className="shrink-0 size-4 text-muted-foreground" data-icon="inline-start" />
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
            className="font-mono text-xs"
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
                onTraceExport();
              }
            }}
            placeholder="session id"
            disabled={isSubmitting || exportPending}
            className="font-mono text-sm"
          />
          <Button
            type="button"
            variant="outline"
            disabled={isSubmitting || exportPending}
            onClick={onTraceExport}
            className="h-9 px-4 font-medium shadow-none"
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
          className="min-h-24 resize-y font-mono text-xs p-3"
          placeholder="artifacts/traces/sessions/.../mlflow-traces.distilled.jsonl"
        />
      </CompactField>
    </>
  );
}

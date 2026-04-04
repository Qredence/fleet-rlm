import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLegend,
  FieldSet,
  FieldTitle,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  optimizationEndpoints,
  type GEPAOptimizationRequest,
  type GEPAOptimizationResponse,
} from "@/lib/rlm-api/optimization";

const SETTINGS_FIELD_CLASSNAME = "gap-5 border-b border-border-subtle py-5 last:border-b-0";
const SETTINGS_SECTION_CLASSNAME = "max-w-[44rem] gap-4";

export const optimizationKeys = {
  all: ["optimization"] as const,
  status: () => [...optimizationKeys.all, "status"] as const,
};

export function OptimizationForm() {
  const [datasetPath, setDatasetPath] = useState("");
  const [programSpec, setProgramSpec] = useState("");
  const [outputPath, setOutputPath] = useState("");
  const [auto, setAuto] = useState<"light" | "medium" | "heavy">("light");
  const [trainRatio, setTrainRatio] = useState("0.8");
  const [lastResult, setLastResult] = useState<GEPAOptimizationResponse | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

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

  const runOptimization = useMutation({
    mutationFn: (input: GEPAOptimizationRequest) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      return optimizationEndpoints.run(input, controller.signal);
    },
    onSuccess: (result) => {
      if (!mountedRef.current) return;
      setLastResult(result);
      if (result.ok) {
        toast.success("GEPA optimization completed", {
          description:
            result.validation_score != null
              ? `Validation score: ${result.validation_score.toFixed(3)}`
              : `Trained on ${result.train_examples} examples`,
        });
      } else {
        toast.error("GEPA optimization failed", {
          description: result.error ?? "Unknown error",
        });
      }
    },
    onError: (error) => {
      if (!mountedRef.current) return;
      toast.error("Failed to run GEPA optimization", {
        description: error instanceof Error ? error.message : "Unexpected error",
      });
    },
  });

  const status = statusQuery.data;
  const available = status?.available ?? false;
  const ratio = Number.parseFloat(trainRatio);
  const validRatio = !Number.isNaN(ratio) && ratio > 0 && ratio < 1;
  const canRun = available && datasetPath.trim() !== "" && programSpec.trim() !== "" && validRatio;

  const handleRun = () => {
    if (!canRun) return;
    runOptimization.mutate({
      dataset_path: datasetPath.trim(),
      program_spec: programSpec.trim(),
      output_path: outputPath.trim() || null,
      auto,
      train_ratio: ratio,
    });
  };

  return (
    <div className="flex flex-col gap-10">
      <FieldSet className={SETTINGS_SECTION_CLASSNAME}>
        <div className="flex flex-col gap-1">
          <FieldLegend variant="label" className="mb-0 text-sm font-semibold">
            GEPA Prompt Optimization
          </FieldLegend>
          <FieldDescription>
            Evolve prompts using text feedback with GEPA (Generative Evolution of Prompts with
            Assessment). Requires MLflow and an exported trace dataset.
          </FieldDescription>
        </div>

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
                <Badge variant="secondary" className="bg-emerald-500/15 text-emerald-600">
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
              <FieldTitle>Dataset path</FieldTitle>
              <FieldDescription>
                Path to the exported MLflow trace dataset (JSON file).
              </FieldDescription>
            </FieldContent>
            <Input
              type="text"
              value={datasetPath}
              autoComplete="off"
              placeholder="traces.json"
              aria-label="Dataset path"
              onChange={(event) => setDatasetPath(event.target.value)}
              className="w-full"
            />
          </Field>

          <Field className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>Program spec</FieldTitle>
              <FieldDescription>
                DSPy program specification to optimize in <code>module:attr</code> form.
              </FieldDescription>
            </FieldContent>
            <Input
              type="text"
              value={programSpec}
              autoComplete="off"
              placeholder="package.module:build_program"
              aria-label="Program spec"
              onChange={(event) => setProgramSpec(event.target.value)}
              className="w-full"
            />
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
                Fraction of examples used for training (0.0–1.0). The remainder is used for
                validation.
              </FieldDescription>
            </FieldContent>
            <Input
              type="text"
              inputMode="decimal"
              value={trainRatio}
              autoComplete="off"
              aria-label="Train ratio"
              onChange={(event) => setTrainRatio(event.target.value)}
              className="w-full max-w-[8rem]"
            />
          </Field>

          {lastResult ? (
            <Field className={SETTINGS_FIELD_CLASSNAME}>
              <FieldContent>
                <FieldTitle>Last run result</FieldTitle>
                <FieldDescription>
                  <span className="block">Status: {lastResult.ok ? "✓ Success" : "✗ Failed"}</span>
                  <span className="block">
                    Program: {lastResult.program_spec} · Optimizer: {lastResult.optimizer}
                  </span>
                  <span className="block">
                    Train: {lastResult.train_examples} · Validation:{" "}
                    {lastResult.validation_examples}
                  </span>
                  {lastResult.validation_score != null ? (
                    <span className="block">
                      Validation score: {lastResult.validation_score.toFixed(4)}
                    </span>
                  ) : null}
                  {lastResult.output_path ? (
                    <span className="block">Saved to: {lastResult.output_path}</span>
                  ) : null}
                  {lastResult.error ? (
                    <span className="block text-destructive">Error: {lastResult.error}</span>
                  ) : null}
                </FieldDescription>
              </FieldContent>
            </Field>
          ) : null}

          <Field className="gap-5 py-5">
            <FieldContent>
              <FieldTitle>Run optimization</FieldTitle>
              <FieldDescription>
                Start a GEPA optimization run with the configured parameters. This may take several
                minutes depending on dataset size and intensity.
              </FieldDescription>
            </FieldContent>
            <Button
              variant="secondary"
              className="self-start rounded-lg"
              onClick={handleRun}
              disabled={!canRun || runOptimization.isPending}
            >
              {runOptimization.isPending ? "Running…" : "Run GEPA"}
            </Button>
          </Field>
        </FieldGroup>
      </FieldSet>
    </div>
  );
}

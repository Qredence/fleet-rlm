import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Play } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemFooter,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { Skeleton } from "@/components/ui/skeleton";
import {
  datasetEndpoints,
  optimizationEndpoints,
  optimizationKeys,
  type DatasetResponse,
  type GEPAModuleInfo,
} from "@/lib/rlm-api/optimization";
import type { OptimizationRunDraft } from "@/features/optimization/optimization-form";

/** Turn a kebab-case slug into a title-cased label (e.g. "reflect-and-revise" → "Reflect and Revise"). */
function humanizeSlug(slug: string): string {
  return slug
    .split(/[-_]/)
    .filter((w) => w.length > 0)
    .map((w) => w[0]!.toUpperCase() + w.slice(1))
    .join(" ");
}

function ModuleCard({
  mod,
  onPrepareRun,
  dataset,
}: {
  mod: GEPAModuleInfo;
  onPrepareRun: (mod: GEPAModuleInfo) => void;
  dataset: DatasetResponse | null;
}) {
  const displayDescription = mod.description || humanizeSlug(mod.slug);

  return (
    <Item variant="outline">
      <ItemContent>
        <ItemTitle className="text-sm">{mod.label}</ItemTitle>
        <ItemDescription className="text-xs">{displayDescription}</ItemDescription>
        <span className="mt-0.5 block font-mono typo-helper text-muted-foreground/60">
          {mod.program_spec}
        </span>
      </ItemContent>
      <ItemActions>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0 gap-1.5"
          disabled={dataset == null}
          onClick={() => onPrepareRun(mod)}
        >
          <Play className="size-3" />
          {dataset == null ? "Dataset required" : "Create Run"}
        </Button>
      </ItemActions>
      {mod.required_dataset_keys.length > 0 ? (
        <ItemFooter>
          <div className="flex flex-col gap-1.5">
            <span className="text-xs text-muted-foreground">Required keys</span>
            <div className="flex flex-wrap gap-1.5">
              {mod.required_dataset_keys.map((key) => (
                <Badge key={key} variant="secondary" className="font-mono text-xs">
                  {key}
                </Badge>
              ))}
            </div>
          </div>
        </ItemFooter>
      ) : null}
    </Item>
  );
}

export function ModulesTab({
  onPrepareRun,
}: {
  onPrepareRun?: (draft: OptimizationRunDraft) => void;
}) {
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

  const latestDatasetByModule = useMemo(() => {
    const datasets = datasetsQuery.data?.items ?? [];
    return datasets.reduce<Map<string, DatasetResponse>>((acc, dataset) => {
      if (!dataset.module_slug || acc.has(dataset.module_slug)) {
        return acc;
      }
      acc.set(dataset.module_slug, dataset);
      return acc;
    }, new Map());
  }, [datasetsQuery.data]);

  const handlePrepareRun = (mod: GEPAModuleInfo) => {
    const dataset = latestDatasetByModule.get(mod.slug);
    if (dataset == null) {
      toast.error("Create Run requires a dataset", {
        description: `Upload or create a ${mod.label} dataset first.`,
      });
      return;
    }
    onPrepareRun?.({
      datasetId: dataset.id,
      datasetName: dataset.name,
      moduleSlug: mod.slug,
      programSpec: mod.program_spec,
      auto: "light",
      trainRatio: 0.8,
    });
  };

  if (modulesQuery.isLoading) {
    return (
      <div className="grid gap-4 sm:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (modulesQuery.isError) {
    return (
      <Card className="border-destructive/30 bg-destructive/5">
        <CardContent className="py-4">
          <p className="text-sm text-destructive">
            Failed to load modules:{" "}
            {modulesQuery.error instanceof Error ? modulesQuery.error.message : "Unknown error"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const modules = modulesQuery.data ?? [];

  if (modules.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-12 text-center">
        <p className="text-sm text-muted-foreground">No optimization modules registered.</p>
        <p className="text-xs text-muted-foreground">Register DSPy modules to see them here.</p>
      </div>
    );
  }

  return (
    <ItemGroup className="grid sm:grid-cols-2">
      {modules.map((mod) => (
        <ModuleCard
          key={mod.slug}
          mod={mod}
          onPrepareRun={handlePrepareRun}
          dataset={latestDatasetByModule.get(mod.slug) ?? null}
        />
      ))}
    </ItemGroup>
  );
}

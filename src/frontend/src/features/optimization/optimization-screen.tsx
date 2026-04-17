import { useCallback, useState } from "react";
import { Layers, Database, Play, GitCompare, SlidersHorizontal } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { PageHeader } from "@/components/product/page-header";
import { ModulesTab } from "@/features/optimization/modules-tab";
import { DatasetsTab } from "@/features/optimization/datasets-tab";
import { RunsTab } from "@/features/optimization/runs-tab";
import { CompareTab } from "@/features/optimization/compare-tab";
import {
  OptimizationForm,
  type OptimizationRunDraft,
} from "@/features/optimization/optimization-form";
import { cn } from "@/lib/utils";

export function OptimizationScreen() {
  const isMobile = useIsMobile();
  const [activeTab, setActiveTab] = useState("modules");
  const [compareRunIds, setCompareRunIds] = useState<number[] | undefined>();
  const [runDraft, setRunDraft] = useState<OptimizationRunDraft | null>(null);
  const [draftVersion, setDraftVersion] = useState(0);

  const handleCompare = useCallback((runIds: number[]) => {
    setCompareRunIds(runIds);
    setActiveTab("compare");
  }, []);

  const handlePrepareRun = useCallback((draft: OptimizationRunDraft) => {
    setRunDraft(draft);
    setDraftVersion((current) => current + 1);
    setActiveTab("create");
  }, []);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {!isMobile ? (
        <PageHeader
          isMobile={false}
          title="Prompt Optimization"
          description="Evolve prompts using GEPA (Generative Evolution of Prompts with Assessment) powered by DSPy and MLflow."
        />
      ) : null}

      <ScrollArea className="min-h-0 flex-1">
        {isMobile ? (
          <PageHeader
            isMobile
            title="Prompt Optimization"
            description="Evolve prompts using GEPA powered by DSPy and MLflow."
          />
        ) : null}

        <div className={cn("mx-auto w-full max-w-200 py-4", isMobile ? "px-4" : "px-6")}>
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList variant="line" className="mb-2">
              <TabsTrigger value="modules" className="gap-1.5">
                <Layers className="size-3.5" />
                Modules
              </TabsTrigger>
              <TabsTrigger value="datasets" className="gap-1.5">
                <Database className="size-3.5" />
                Datasets
              </TabsTrigger>
              <TabsTrigger value="create" className="gap-1.5">
                <SlidersHorizontal className="size-3.5" />
                Create
              </TabsTrigger>
              <TabsTrigger value="runs" className="gap-1.5">
                <Play className="size-3.5" />
                Runs
              </TabsTrigger>
              <TabsTrigger value="compare" className="gap-1.5">
                <GitCompare className="size-3.5" />
                Compare
              </TabsTrigger>
            </TabsList>
            <TabsContent value="modules" className="pt-4">
              <ModulesTab onPrepareRun={handlePrepareRun} />
            </TabsContent>
            <TabsContent value="datasets" className="pt-4">
              <DatasetsTab onPrepareRun={handlePrepareRun} />
            </TabsContent>
            <TabsContent value="create" className="pt-4">
              <OptimizationForm
                initialDraft={runDraft}
                draftVersion={draftVersion}
                onRunCreated={() => setActiveTab("runs")}
              />
            </TabsContent>
            <TabsContent value="runs" className="pt-4">
              <RunsTab onCompare={handleCompare} />
            </TabsContent>
            <TabsContent value="compare" className="pt-4">
              <CompareTab initialRunIds={compareRunIds} />
            </TabsContent>
          </Tabs>
        </div>
      </ScrollArea>
    </div>
  );
}

import { useState } from "react";
import { toast } from "sonner";
import { FlaskConical } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PageHeader } from "@/components/product/page-header";
import { useIsMobile } from "@/hooks/use-is-mobile";
import type { OptimizationPromotionDraftResponse, OptimizationRunResponse } from "@/lib/rlm-api";

import { errorMessage } from "./optimization-format";
import { OptimizationForm } from "./optimization-form";
import { RunDetailsSheet } from "./run-details-sheet";
import { RunHistory } from "./run-history";
import {
  useOptimizationMutations,
  useOptimizationRunDetails,
  useOptimizationRuns,
  useOptimizationStatus,
} from "./use-optimization";

type OptimizationTab = "new-run" | "history";

const EMPTY_RUNS: OptimizationRunResponse[] = [];

export function OptimizationScreen() {
  const isMobile = useIsMobile();
  const statusQuery = useOptimizationStatus();
  const runsQuery = useOptimizationRuns();
  const { createPromotionDraft } = useOptimizationMutations();

  const [activeTab, setActiveTab] = useState<OptimizationTab>("new-run");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [promotionDraft, setPromotionDraft] = useState<OptimizationPromotionDraftResponse | null>(
    null,
  );
  const runDetailsQuery = useOptimizationRunDetails(selectedRunId);

  const runs = runsQuery.data ?? EMPTY_RUNS;

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
            <Alert className="border-border-subtle bg-muted/10 rounded-lg">
              <FlaskConical className="text-muted-foreground size-4" />
              <AlertTitle className="text-sm font-semibold text-foreground">
                GEPA unavailable
              </AlertTitle>
              <AlertDescription className="text-xs text-muted-foreground mt-0.5">
                {statusQuery.data.guidance ?? "The optimizer is not available in this environment."}
              </AlertDescription>
            </Alert>
          ) : null}

          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as OptimizationTab)}
            className="min-h-0"
          >
            <div className="flex items-center justify-between gap-3 border-b border-border-subtle pb-3">
              <TabsList className="bg-muted/40 p-1">
                <TabsTrigger
                  value="new-run"
                  className="font-medium text-xs py-1.5 px-3 transition-colors"
                >
                  New Run
                </TabsTrigger>
                <TabsTrigger
                  value="history"
                  className="font-medium text-xs py-1.5 px-3 transition-colors"
                >
                  Run History
                </TabsTrigger>
              </TabsList>
              <Badge
                variant="outline"
                className="border-border-subtle bg-muted/20 text-muted-foreground font-medium typo-helper"
              >
                RLM-GEPA
              </Badge>
            </div>

            <TabsContent value="new-run" className="mt-4 focus-visible:outline-none">
              <OptimizationForm onSuccess={() => setActiveTab("history")} />
            </TabsContent>

            <TabsContent value="history" className="mt-4 focus-visible:outline-none">
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

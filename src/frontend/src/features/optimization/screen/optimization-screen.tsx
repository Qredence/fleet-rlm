import { useState } from "react";
import { toast } from "sonner";
import { FlaskConical } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsList, TabsPanel, TabsTab } from "@/components/ui/tabs";
import type { OptimizationPromotionDraftResponse, OptimizationRunResponse } from "@/lib/rlm-api";

import { errorMessage } from "../optimization-format";
import { OptimizationForm } from "../form/optimization-form";
import { RunDetailsSheet } from "../run-details/run-details-sheet";
import { RunHistory } from "../run-history/run-history";
import {
  useOptimizationMutations,
  useOptimizationRunDetails,
  useOptimizationRunScorecard,
  useOptimizationRuns,
  useOptimizationStatus,
} from "../use-optimization";
import { optimizationEndpoints } from "@/lib/rlm-api";

type OptimizationTab = "new-run" | "history";

const EMPTY_RUNS: OptimizationRunResponse[] = [];

export function OptimizationScreen() {
  const statusQuery = useOptimizationStatus();
  const runsQuery = useOptimizationRuns();
  const { createPromotionDraft, approveArtifact, activateArtifact, resumeRun } =
    useOptimizationMutations();

  const [activeTab, setActiveTab] = useState<OptimizationTab>("new-run");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [promotionDraft, setPromotionDraft] = useState<OptimizationPromotionDraftResponse | null>(
    null,
  );
  const runDetailsQuery = useOptimizationRunDetails(selectedRunId);
  const scorecardQuery = useOptimizationRunScorecard(selectedRunId);

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

  const resolveRunArtifactId = async (): Promise<string | null> => {
    if (!selectedRunId) return null;
    try {
      const artifact = await optimizationEndpoints.runArtifact(selectedRunId);
      return artifact.id;
    } catch (error) {
      toast.error("Run artifact unavailable", { description: errorMessage(error) });
      return null;
    }
  };

  const handleApproveArtifact = async () => {
    const artifactId = await resolveRunArtifactId();
    if (!artifactId) return;
    try {
      const approved = await approveArtifact.mutateAsync(artifactId);
      toast.success("Artifact approved", { description: approved.id });
    } catch (error) {
      toast.error("Approve failed", { description: errorMessage(error) });
    }
  };

  const handleActivateArtifact = async () => {
    const artifactId = await resolveRunArtifactId();
    if (!artifactId) return;
    try {
      const activation = await activateArtifact.mutateAsync(artifactId);
      toast.success("Artifact activated", {
        description: `${activation.target_kind}:${activation.target_id}`,
      });
    } catch (error) {
      toast.error("Activate failed", { description: errorMessage(error) });
    }
  };

  const handleResumeRun = async () => {
    if (!selectedRunId) return;
    try {
      const resumed = await resumeRun.mutateAsync(selectedRunId);
      toast.success("Run resumed", { description: resumed.run_id });
    } catch (error) {
      toast.error("Resume failed", { description: errorMessage(error) });
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
        detail={runDetailsQuery.data ?? undefined}
        isLoading={runDetailsQuery.isLoading}
        error={runDetailsQuery.error}
        draft={promotionDraft}
        isDraftPending={createPromotionDraft.isPending}
        onCreateDraft={() => void handleCreatePromotionDraft()}
        scorecard={scorecardQuery.data ?? null}
        scorecardLoading={scorecardQuery.isLoading}
        scorecardError={scorecardQuery.error}
        onApproveArtifact={() => void handleApproveArtifact()}
        onActivateArtifact={() => void handleActivateArtifact()}
        onResumeRun={() => void handleResumeRun()}
        isArtifactActionPending={
          approveArtifact.isPending || activateArtifact.isPending || resumeRun.isPending
        }
      />

      <ScrollArea className="min-h-0 flex-1">
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
            <TabsList>
              <TabsTab value="new-run">New Run</TabsTab>
              <TabsTab value="history" className="gap-1.5">
                Run History
                {runs.length > 0 ? (
                  <Badge variant="secondary" className="h-5 px-1.5 text-xs tabular-nums">
                    {runs.length}
                  </Badge>
                ) : null}
              </TabsTab>
            </TabsList>

            <TabsPanel value="new-run" className="mt-4 focus-visible:outline-none">
              <OptimizationForm onSuccess={() => setActiveTab("history")} />
            </TabsPanel>

            <TabsPanel value="history" className="mt-4 focus-visible:outline-none">
              <RunHistory
                runs={runs}
                isLoading={runsQuery.isLoading}
                error={runsQuery.error}
                refetch={() => void runsQuery.refetch()}
                onSelectRun={openRunDetails}
              />
            </TabsPanel>
          </Tabs>
        </div>
      </ScrollArea>
    </div>
  );
}

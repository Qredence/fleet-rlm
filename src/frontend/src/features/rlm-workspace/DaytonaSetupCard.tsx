import { useState } from "react";
import { ChevronDown, TriangleAlert } from "lucide-react";

import type {
  DetectedDaytonaRepoContext,
  ResolvedDaytonaRepoContext,
} from "@/features/rlm-workspace/daytonaRepoContext";
import {
  buildDaytonaSourceStateLabel,
  parseDaytonaContextPaths,
} from "@/features/rlm-workspace/daytonaSourceContext";
import type { DaytonaContextSourceSummary } from "@/features/rlm-workspace/daytona-workbench/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils/cn";

function sourceBadge(
  resolvedRepoContext: ResolvedDaytonaRepoContext | null,
  hasContextPaths: boolean,
  hasInvalidManualOverride: boolean,
  hasActiveRunSources: boolean,
) {
  if (hasInvalidManualOverride) {
    return <Badge variant="warning">Repository URL needs to be valid</Badge>;
  }
  if (hasActiveRunSources) {
    return <Badge variant="secondary">Active run context</Badge>;
  }
  return (
    <Badge variant="outline">
      {buildDaytonaSourceStateLabel({
        hasRepo: Boolean(resolvedRepoContext?.repoUrl),
        hasContext: hasContextPaths,
      })}
    </Badge>
  );
}

interface DaytonaSetupCardProps {
  manualRepoUrl: string;
  onManualRepoUrlChange: (value: string) => void;
  contextPaths: string;
  onContextPathsChange: (value: string) => void;
  repoRef: string;
  onRepoRefChange: (value: string) => void;
  maxDepth: number;
  onMaxDepthChange: (value: number) => void;
  batchConcurrency: number;
  onBatchConcurrencyChange: (value: number) => void;
  detectedRepoContext: DetectedDaytonaRepoContext | null;
  resolvedRepoContext: ResolvedDaytonaRepoContext | null;
  hasInvalidManualOverride: boolean;
  activeRunRepoUrl?: string | null;
  activeRunContextSources?: DaytonaContextSourceSummary[];
  isActiveRunContextVisible?: boolean;
}

function DaytonaSetupCard({
  manualRepoUrl,
  onManualRepoUrlChange,
  contextPaths,
  onContextPathsChange,
  repoRef,
  onRepoRefChange,
  maxDepth,
  onMaxDepthChange,
  batchConcurrency,
  onBatchConcurrencyChange,
  detectedRepoContext,
  resolvedRepoContext,
  hasInvalidManualOverride,
  activeRunRepoUrl,
  activeRunContextSources = [],
  isActiveRunContextVisible = false,
}: DaytonaSetupCardProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const parsedContextPaths = parseDaytonaContextPaths(contextPaths);
  const hasManualOverride = manualRepoUrl.trim().length > 0;
  const hasResolvedRepo = Boolean(resolvedRepoContext?.repoUrl);
  const hasActiveRunSources =
    !hasManualOverride &&
    !hasResolvedRepo &&
    parsedContextPaths.length === 0 &&
    !hasInvalidManualOverride &&
    isActiveRunContextVisible &&
    (Boolean(activeRunRepoUrl) || activeRunContextSources.length > 0);
  const displayedRepoUrl =
    hasManualOverride
      ? manualRepoUrl
      : resolvedRepoContext?.repoUrl ?? activeRunRepoUrl ?? "";
  const detectedRepoDiffers =
    hasManualOverride &&
    detectedRepoContext != null &&
    detectedRepoContext.repoUrl !== resolvedRepoContext?.repoUrl;
  const effectiveHasRepo = Boolean(resolvedRepoContext?.repoUrl ?? activeRunRepoUrl);
  const effectiveContextCount =
    parsedContextPaths.length > 0
      ? parsedContextPaths.length
      : hasActiveRunSources
        ? activeRunContextSources.length
        : 0;

  const collapsedSummary = hasInvalidManualOverride
    ? "Manual repository override needs attention before the next run."
    : effectiveHasRepo || effectiveContextCount > 0
      ? "Daytona will use this source mix for the next run."
      : "No external sources are configured yet. Daytona will run in reasoning-only mode.";

  const handleExpandedChange = (nextValue: boolean) => {
    setIsExpanded(nextValue);
    if (!nextValue) {
      setAdvancedOpen(false);
    }
  };

  return (
    <Card>
      <CardHeader className="gap-2">
        <div className="flex flex-col gap-1">
          <CardTitle>Daytona source setup</CardTitle>
          <CardDescription>
            Review the source mix for the next Daytona run, then expand only when
            you want to edit repository, local context, or advanced settings.
          </CardDescription>
        </div>
        <CardAction className="flex flex-wrap items-center gap-2">
          {sourceBadge(
            resolvedRepoContext,
            parsedContextPaths.length > 0,
            hasInvalidManualOverride,
            hasActiveRunSources,
          )}
          {effectiveHasRepo ? (
            <Badge variant="secondary" className="max-w-full truncate">
              Repo ready
            </Badge>
          ) : null}
          {effectiveContextCount > 0 ? (
            <Badge variant="secondary">
              {effectiveContextCount} local {effectiveContextCount === 1 ? "path" : "paths"}
            </Badge>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => handleExpandedChange(!isExpanded)}
          >
            {isExpanded ? "Hide source setup" : "Edit source setup"}
          </Button>
        </CardAction>
      </CardHeader>

      {isExpanded ? (
        <>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="daytona-repo-url">Repository URL</Label>
              <div className="flex flex-col gap-2 md:flex-row md:items-center">
                <Input
                  id="daytona-repo-url"
                  aria-label="Daytona repository URL"
                  value={displayedRepoUrl}
                  onChange={(event) =>
                    onManualRepoUrlChange(event.currentTarget.value)
                  }
                  placeholder="https://github.com/qredence/fleet-rlm.git"
                />
                {hasManualOverride ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => onManualRepoUrlChange("")}
                  >
                    Clear repo
                  </Button>
                ) : null}
              </div>
              <p className="text-sm text-muted-foreground">
                Optional. GitHub, GitLab, and Bitbucket HTTPS repo URLs can still be
                auto-detected from the prompt.
              </p>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="daytona-context-paths">Context paths</Label>
              <Textarea
                id="daytona-context-paths"
                aria-label="Daytona context paths"
                value={contextPaths}
                onChange={(event) =>
                  onContextPathsChange(event.currentTarget.value)
                }
                placeholder={[
                  "/Users/zocho/Documents/spec.pdf",
                  "/Volumes/StorageBackup/_RLM/fleet-rlm-dspy/docs",
                ].join("\n")}
                className="min-h-24"
              />
              <p className="text-sm text-muted-foreground">
                Optional. Enter one readable host file or directory path per line.
                Daytona stages these directly into its workspace.
              </p>
            </div>

            {hasInvalidManualOverride ? (
              <Alert>
                <TriangleAlert />
                <AlertTitle>Manual repository URL is invalid</AlertTitle>
                <AlertDescription>
                  Enter a valid HTTPS repository URL or clear the override to keep the
                  run in local-context or reasoning-only mode.
                </AlertDescription>
              </Alert>
            ) : null}

            {!effectiveHasRepo && effectiveContextCount === 0 ? (
              <Alert>
                <TriangleAlert />
                <AlertTitle>Reasoning-only mode</AlertTitle>
                <AlertDescription>
                  No external sources are configured yet. Daytona will use the
                  recursive RLM runtime without cloning a repo or staging local
                  context.
                </AlertDescription>
              </Alert>
            ) : null}

            {resolvedRepoContext?.source !== "manual" && detectedRepoContext ? (
              <p className="text-sm text-muted-foreground">
                Using <span className="font-medium">{detectedRepoContext.repoUrl}</span>{" "}
                from the current prompt.
              </p>
            ) : null}

            {hasActiveRunSources ? (
              <div className="rounded-xl border border-border-subtle/80 bg-muted/20 p-3 text-sm text-muted-foreground">
                <p>
                  Active Daytona run is using the current source mix shown above.
                  Update the repo URL or context paths here to change the next run.
                </p>
              </div>
            ) : null}

            {detectedRepoDiffers && detectedRepoContext ? (
              <p className="text-sm text-muted-foreground">
                The current prompt also mentions{" "}
                <span className="font-medium">{detectedRepoContext.repoUrl}</span>,
                but the manual repo override will be used for this run.
              </p>
            ) : null}

            <Separator />

            <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
              <div className="flex items-center justify-between gap-3">
                <div className="flex flex-col gap-1">
                  <p className="text-sm font-medium text-foreground">
                    Advanced Daytona settings
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Repo ref applies only when a repository is configured.
                  </p>
                </div>
                <CollapsibleTrigger asChild>
                  <Button type="button" variant="ghost" size="sm">
                    {advancedOpen ? "Hide" : "Show"} advanced
                    <ChevronDown
                      data-icon="inline-end"
                      className={cn(
                        "transition-transform",
                        advancedOpen && "rotate-180",
                      )}
                    />
                  </Button>
                </CollapsibleTrigger>
              </div>

              <CollapsibleContent className="mt-4">
                <div className="grid gap-4 md:grid-cols-3">
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="daytona-repo-ref">Repository ref</Label>
                    <Input
                      id="daytona-repo-ref"
                      aria-label="Daytona repository ref"
                      value={repoRef}
                      disabled={!effectiveHasRepo}
                      onChange={(event) =>
                        onRepoRefChange(event.currentTarget.value)
                      }
                      placeholder="main"
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="daytona-max-depth">Max depth</Label>
                    <Input
                      id="daytona-max-depth"
                      aria-label="Daytona max depth"
                      type="number"
                      min={0}
                      value={String(maxDepth)}
                      onChange={(event) =>
                        onMaxDepthChange(
                          Math.max(0, Number(event.currentTarget.value) || 0),
                        )
                      }
                    />
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label htmlFor="daytona-batch-concurrency">
                      Batch concurrency
                    </Label>
                    <Input
                      id="daytona-batch-concurrency"
                      aria-label="Daytona batch concurrency"
                      type="number"
                      min={1}
                      value={String(batchConcurrency)}
                      onChange={(event) =>
                        onBatchConcurrencyChange(
                          Math.max(1, Number(event.currentTarget.value) || 1),
                        )
                      }
                    />
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          </CardContent>

          <CardFooter className="border-t">
            <p className="text-sm text-muted-foreground">
              Daytona runs use the recursive RLM runtime and stage local context
              directly inside the sandbox workspace.
            </p>
          </CardFooter>
        </>
      ) : (
        <CardContent className="pt-0">
          <div className="flex flex-col gap-3 rounded-xl border border-border-subtle/80 bg-muted/20 p-3">
            <p className="text-sm text-muted-foreground">{collapsedSummary}</p>

            {displayedRepoUrl ? (
              <p className="min-w-0 break-all text-sm text-foreground">
                <span className="font-medium">Repo:</span> {displayedRepoUrl}
              </p>
            ) : null}

            {effectiveContextCount > 0 ? (
              <p className="text-sm text-foreground">
                <span className="font-medium">Local context:</span> {" "}
                {effectiveContextCount} {effectiveContextCount === 1 ? "path" : "paths"}
              </p>
            ) : null}

            {resolvedRepoContext?.source !== "manual" && detectedRepoContext ? (
              <p className="text-sm text-muted-foreground">
                Prompt-detected repo will be used unless you add a manual override.
              </p>
            ) : null}

            {hasActiveRunSources ? (
              <p className="text-sm text-muted-foreground">
                Active Daytona run is using the current source mix shown above.
              </p>
            ) : null}

            {detectedRepoDiffers && detectedRepoContext ? (
              <p className="text-sm text-muted-foreground">
                The current prompt also mentions{" "}
                <span className="font-medium">{detectedRepoContext.repoUrl}</span>,
                but the manual repo override will be used for this run.
              </p>
            ) : null}
          </div>

          {hasInvalidManualOverride ? (
            <Alert className="mt-3">
              <TriangleAlert />
              <AlertTitle>Manual repository URL is invalid</AlertTitle>
              <AlertDescription>
                Expand source setup to fix the URL or clear the override.
              </AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      )}
    </Card>
  );
}

export { DaytonaSetupCard };

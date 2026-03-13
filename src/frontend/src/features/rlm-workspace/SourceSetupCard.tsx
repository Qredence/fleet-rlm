import { useState } from "react";
import { ChevronDown, TriangleAlert } from "lucide-react";

import type {
  DetectedRepoContext,
  ResolvedRepoContext,
} from "@/lib/utils/repoContext";
import {
  buildSourceStateLabel,
  parseContextPaths,
} from "@/lib/utils/sourceContext";
import type { ContextSourceSummary } from "@/features/rlm-workspace/run-workbench/types";
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
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils/cn";

function sourceBadge(
  resolvedRepoContext: ResolvedRepoContext | null,
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
      {buildSourceStateLabel({
        hasRepo: Boolean(resolvedRepoContext?.repoUrl),
        hasContext: hasContextPaths,
      })}
    </Badge>
  );
}

interface SourceSetupCardProps {
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
  detectedRepoContext: DetectedRepoContext | null;
  resolvedRepoContext: ResolvedRepoContext | null;
  hasInvalidManualOverride: boolean;
  activeRunRepoUrl?: string | null;
  activeRunContextSources?: ContextSourceSummary[];
  isActiveRunContextVisible?: boolean;
}

function SourceSetupCard({
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
}: SourceSetupCardProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const parsedContextPaths = parseContextPaths(contextPaths);
  const hasManualOverride = manualRepoUrl.trim().length > 0;
  const hasResolvedRepo = Boolean(resolvedRepoContext?.repoUrl);
  const hasActiveRunSources =
    !hasManualOverride &&
    !hasResolvedRepo &&
    parsedContextPaths.length === 0 &&
    !hasInvalidManualOverride &&
    isActiveRunContextVisible &&
    (Boolean(activeRunRepoUrl) || activeRunContextSources.length > 0);
  const visibleActiveRunRepoUrl =
    isActiveRunContextVisible && activeRunRepoUrl ? activeRunRepoUrl : "";
  const displayedRepoUrl = hasManualOverride
    ? manualRepoUrl
    : (resolvedRepoContext?.repoUrl ?? visibleActiveRunRepoUrl);
  const detectedRepoDiffers =
    hasManualOverride &&
    detectedRepoContext != null &&
    detectedRepoContext.repoUrl !== resolvedRepoContext?.repoUrl;
  const effectiveHasRepo = Boolean(
    resolvedRepoContext?.repoUrl ?? visibleActiveRunRepoUrl,
  );
  const effectiveContextCount =
    parsedContextPaths.length > 0
      ? parsedContextPaths.length
      : hasActiveRunSources
        ? activeRunContextSources.length
        : 0;

  const collapsedSummary = hasInvalidManualOverride
    ? "Manual repository override needs attention before the next run."
    : effectiveHasRepo || effectiveContextCount > 0
      ? "This source mix will be used for the next run."
      : "No external sources are configured yet. The runtime will use reasoning-only mode.";

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
          <CardTitle>Source setup</CardTitle>
          <CardDescription>
            Review the source mix for the next run, then expand only when
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
          <CardContent className="flex flex-col gap-6">
            <FieldGroup className="gap-4">
              <Field>
                <FieldLabel htmlFor="source-repo-url">Repository URL</FieldLabel>
                <InputGroup>
                  <InputGroupInput
                    id="source-repo-url"
                    aria-label="Repository URL"
                    value={displayedRepoUrl}
                    onChange={(event) =>
                      onManualRepoUrlChange(event.currentTarget.value)
                    }
                    placeholder="https://github.com/qredence/fleet-rlm.git"
                  />
                  {hasManualOverride ? (
                    <InputGroupAddon align="inline-end">
                      <InputGroupButton
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => onManualRepoUrlChange("")}
                      >
                        Clear repo
                      </InputGroupButton>
                    </InputGroupAddon>
                  ) : null}
                </InputGroup>
                <FieldDescription>
                  Optional. GitHub, GitLab, and Bitbucket HTTPS repo URLs can
                  still be auto-detected from the prompt.
                </FieldDescription>
              </Field>

              <Field>
                <FieldLabel htmlFor="source-context-paths">
                  Context paths
                </FieldLabel>
                <Textarea
                  id="source-context-paths"
                  aria-label="Context paths"
                  value={contextPaths}
                  onChange={(event) =>
                    onContextPathsChange(event.currentTarget.value)
                  }
                  placeholder={[
                    "/workspace/spec.pdf",
                    "/workspace/docs",
                  ].join("\n")}
                  className="min-h-24"
                />
                <FieldDescription>
                  Optional. Enter one readable host file or directory path per
                  line. Daytona stages these directly into its workspace.
                </FieldDescription>
              </Field>
            </FieldGroup>

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
                  The active run is using the current source mix shown above.
                  Update the repo URL or context paths to change the next run.
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
                    Advanced settings
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
                <FieldGroup className="gap-4 md:grid md:grid-cols-3">
                  <Field>
                    <FieldLabel htmlFor="source-repo-ref">
                      Repository ref
                    </FieldLabel>
                    <Input
                      id="source-repo-ref"
                      aria-label="Repository ref"
                      value={repoRef}
                      disabled={!effectiveHasRepo}
                      onChange={(event) =>
                        onRepoRefChange(event.currentTarget.value)
                      }
                      placeholder="main"
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="source-max-depth">Max depth</FieldLabel>
                    <Input
                      id="source-max-depth"
                      aria-label="Max depth"
                      type="number"
                      min={0}
                      value={String(maxDepth)}
                      onChange={(event) =>
                        onMaxDepthChange(
                          Math.max(0, Number(event.currentTarget.value) || 0),
                        )
                      }
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="source-batch-concurrency">
                      Batch concurrency
                    </FieldLabel>
                    <Input
                      id="source-batch-concurrency"
                      aria-label="Batch concurrency"
                      type="number"
                      min={1}
                      value={String(batchConcurrency)}
                      onChange={(event) =>
                        onBatchConcurrencyChange(
                          Math.max(1, Number(event.currentTarget.value) || 1),
                        )
                      }
                    />
                  </Field>
                </FieldGroup>
              </CollapsibleContent>
            </Collapsible>
          </CardContent>

          <CardFooter className="border-t">
            <p className="text-sm text-muted-foreground">
              Runs use the recursive RLM runtime and stage local context
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
                The active run is using the current source mix shown above.
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

export { SourceSetupCard };

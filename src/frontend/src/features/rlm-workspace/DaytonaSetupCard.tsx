import { useState } from "react";
import { ChevronDown, TriangleAlert } from "lucide-react";

import type {
  DetectedDaytonaRepoContext,
  ResolvedDaytonaRepoContext,
} from "@/features/rlm-workspace/daytonaRepoContext";
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
import { cn } from "@/lib/utils/cn";

function sourceBadge(
  resolvedRepoContext: ResolvedDaytonaRepoContext | null,
  hasManualOverride: boolean,
  hasInvalidManualOverride: boolean,
) {
  if (hasInvalidManualOverride) {
    return <Badge variant="warning">Manual override needs a valid repo URL</Badge>;
  }
  if (resolvedRepoContext?.source === "manual") {
    return <Badge variant="secondary">Manual override</Badge>;
  }
  if (resolvedRepoContext?.source === "prompt_mention") {
    return <Badge variant="success">Detected from @url mention</Badge>;
  }
  if (resolvedRepoContext?.source === "prompt_url") {
    return <Badge variant="success">Detected from prompt URL</Badge>;
  }
  if (hasManualOverride) {
    return <Badge variant="outline">Manual override</Badge>;
  }
  return <Badge variant="outline">Repo required</Badge>;
}

interface DaytonaSetupCardProps {
  manualRepoUrl: string;
  onManualRepoUrlChange: (value: string) => void;
  repoRef: string;
  onRepoRefChange: (value: string) => void;
  maxDepth: number;
  onMaxDepthChange: (value: number) => void;
  batchConcurrency: number;
  onBatchConcurrencyChange: (value: number) => void;
  detectedRepoContext: DetectedDaytonaRepoContext | null;
  resolvedRepoContext: ResolvedDaytonaRepoContext | null;
  hasInvalidManualOverride: boolean;
}

function DaytonaSetupCard({
  manualRepoUrl,
  onManualRepoUrlChange,
  repoRef,
  onRepoRefChange,
  maxDepth,
  onMaxDepthChange,
  batchConcurrency,
  onBatchConcurrencyChange,
  detectedRepoContext,
  resolvedRepoContext,
  hasInvalidManualOverride,
}: DaytonaSetupCardProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const hasManualOverride = manualRepoUrl.trim().length > 0;
  const displayedRepoUrl =
    hasManualOverride ? manualRepoUrl : detectedRepoContext?.repoUrl ?? "";
  const detectedRepoDiffers =
    hasManualOverride &&
    detectedRepoContext != null &&
    detectedRepoContext.repoUrl !== resolvedRepoContext?.repoUrl;

  return (
    <Card>
      <CardHeader className="gap-2">
        <div className="flex flex-col gap-1">
          <CardTitle>Daytona run setup</CardTitle>
          <CardDescription>
            Paste a repository URL into the task box or override it here. The
            task text is sent unchanged.
          </CardDescription>
        </div>
        <CardAction className="flex flex-wrap items-center gap-2">
          {sourceBadge(
            resolvedRepoContext,
            hasManualOverride,
            hasInvalidManualOverride,
          )}
          {resolvedRepoContext?.repoUrl ? (
            <Badge variant="outline" className="max-w-full truncate">
              {resolvedRepoContext.repoUrl}
            </Badge>
          ) : null}
        </CardAction>
      </CardHeader>

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
                Clear override
              </Button>
            ) : null}
          </div>
          <p className="text-sm text-muted-foreground">
            Supported auto-detection hosts: GitHub, GitLab, and Bitbucket over
            HTTPS.
          </p>
        </div>

        {hasInvalidManualOverride ? (
          <Alert>
            <TriangleAlert />
            <AlertTitle>Manual repo override is invalid</AlertTitle>
            <AlertDescription>
              Enter a valid HTTPS repository URL or clear the override to fall
              back to the repo detected in the prompt.
            </AlertDescription>
          </Alert>
        ) : null}

        {!resolvedRepoContext && !hasInvalidManualOverride ? (
          <Alert>
            <TriangleAlert />
            <AlertTitle>Repository required</AlertTitle>
            <AlertDescription>
              Paste a repo URL into the task box, use an <code>@https://...</code>{" "}
              prompt mention, or enter a manual override here before sending.
            </AlertDescription>
          </Alert>
        ) : null}

        {resolvedRepoContext?.source !== "manual" && detectedRepoContext ? (
          <p className="text-sm text-muted-foreground">
            Using <span className="font-medium">{detectedRepoContext.repoUrl}</span>{" "}
            from the current prompt.
          </p>
        ) : null}

        {detectedRepoDiffers && detectedRepoContext ? (
          <p className="text-sm text-muted-foreground">
            The current prompt also mentions{" "}
            <span className="font-medium">{detectedRepoContext.repoUrl}</span>,
            but the manual override will be used for this run.
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
                Ref, recursion depth, and concurrency for the experimental
                Daytona path.
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
          Daytona mode is still repo-clone-only and analysis-first in this
          release.
        </p>
      </CardFooter>
    </Card>
  );
}

export { DaytonaSetupCard };

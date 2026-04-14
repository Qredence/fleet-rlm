import { SearchSlash } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CodeBlock, CodeBlockCode } from "@/components/ui/code-block";
import { Reasoning, ReasoningTrigger, ReasoningContent } from "@/components/ai-elements/reasoning";
import { EmptyPanel } from "@/components/product/empty-panel";
import type {
  ArtifactSummary,
  ContextSourceSummary,
  IterationSummary,
} from "@/features/workspace/use-workspace";
import { statusBadgeVariant } from "./workbench-artifact-list";

function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function preferredArtifactText(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || null;
  }

  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  const record = value as Record<string, unknown>;
  for (const key of ["final_markdown", "summary", "text", "content", "message"]) {
    const candidate = record[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate;
    }
  }

  const nestedValue = record.value;
  if (nestedValue !== value) {
    return preferredArtifactText(nestedValue);
  }

  return null;
}

export function ArtifactPanel({ artifact }: { artifact?: ArtifactSummary | null }) {
  if (!artifact) {
    return (
      <EmptyPanel
        title="No final output yet"
        description="The final structured output for this run will appear here when execution completes."
        icon={SearchSlash}
      />
    );
  }

  const renderedArtifactText = preferredArtifactText(artifact.value);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        {artifact.kind ? <Badge variant="secondary">{artifact.kind}</Badge> : null}
        {artifact.finalizationMode ? (
          <Badge variant="secondary">{artifact.finalizationMode}</Badge>
        ) : null}
        {artifact.variableName ? (
          <Badge variant="secondary">var {artifact.variableName}</Badge>
        ) : null}
      </div>
      {artifact.textPreview ? (
        <Card className="border-border-subtle/80 bg-muted/15">
          <CardContent className="pt-4 text-sm text-foreground">{artifact.textPreview}</CardContent>
        </Card>
      ) : null}
      <CodeBlock className="border-border-subtle/80 bg-muted/15">
        <CodeBlockCode
          code={renderedArtifactText ?? stringifyValue(artifact.value)}
          language="json"
        />
      </CodeBlock>
    </div>
  );
}

export function IterationDetail({ iteration }: { iteration?: IterationSummary | null }) {
  if (!iteration) {
    return (
      <EmptyPanel
        title="No iteration selected"
        description="Select an iteration to inspect its prompt-response summary and execution output."
        icon={SearchSlash}
      />
    );
  }

  return (
    <Card className="border-border-subtle/80 bg-card/80">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm">Iteration {iteration.iteration}</CardTitle>
          <Badge variant={statusBadgeVariant(iteration.status)}>{iteration.status}</Badge>
          {iteration.phase ? <Badge variant="secondary">{iteration.phase}</Badge> : null}
        </div>
        <CardDescription>{iteration.summary}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {iteration.reasoningSummary ? (
          <Reasoning className="mb-0" defaultOpen>
            <ReasoningTrigger
              getThinkingMessage={() => (
                <span className="text-sm font-medium">Planner reasoning</span>
              )}
            />
            <ReasoningContent className="mt-2 text-sm text-muted-foreground whitespace-pre-wrap">
              {iteration.reasoningSummary}
            </ReasoningContent>
          </Reasoning>
        ) : null}
        {iteration.code ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Executed code
            </div>
            <CodeBlock className="border-border-subtle/80 bg-muted/15">
              <CodeBlockCode code={iteration.code} language="python" />
            </CodeBlock>
          </section>
        ) : null}
        {iteration.stdout ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              STDOUT
            </div>
            <CodeBlock className="border-border-subtle/80 bg-muted/15">
              <CodeBlockCode code={iteration.stdout} language="text" />
            </CodeBlock>
          </section>
        ) : null}
        {iteration.stderr ? (
          <section className="flex flex-col gap-2">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              STDERR
            </div>
            <CodeBlock className="border-border-subtle/80 bg-destructive/5 border-destructive/20">
              <CodeBlockCode code={iteration.stderr} language="text" />
            </CodeBlock>
          </section>
        ) : null}
        {iteration.error ? (
          <Alert variant="destructive">
            <AlertTitle>Iteration error</AlertTitle>
            <AlertDescription>{iteration.error}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function ContextSourceCard({ source }: { source: ContextSourceSummary }) {
  return (
    <Card className="border-border-subtle/80 bg-muted/15">
      <CardHeader className="gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm">{source.hostPath}</CardTitle>
          <Badge variant="secondary">{source.kind}</Badge>
          {source.sourceType ? <Badge variant="secondary">{source.sourceType}</Badge> : null}
        </div>
        <CardDescription>
          {source.stagedPath ? `Staged at ${source.stagedPath}` : "Pending staging"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm text-muted-foreground">
        <div className="flex flex-wrap gap-3">
          {source.fileCount != null ? <span>{source.fileCount} files</span> : null}
          {source.skippedCount != null ? <span>{source.skippedCount} skipped</span> : null}
          {source.extractionMethod ? <span>{source.extractionMethod}</span> : null}
        </div>
        {source.warnings?.length ? (
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {source.warnings.map((warning, index) => (
              <li key={`${source.hostPath}-warning-${index}`}>{warning}</li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

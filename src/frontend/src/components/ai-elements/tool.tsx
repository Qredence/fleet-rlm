import type { ReactNode } from "react";
import {
  ChevronDown,
  Wrench,
  LoaderCircle,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { typo } from "@/lib/config/typo";
import { cn } from "@/lib/utils/cn";

export type ToolState =
  | "input-streaming"
  | "running"
  | "output-available"
  | "output-error";
export type ToolDensity = "default" | "compact";
export type ToolDisclosure = "auto" | "always_open" | "always_collapsed";

function Tool({
  density = "default",
  disclosure = "auto",
  defaultOpen,
  className,
  ...props
}: React.ComponentProps<typeof Collapsible> & {
  density?: ToolDensity;
  disclosure?: ToolDisclosure;
}) {
  const resolvedDefaultOpen =
    disclosure === "always_open"
      ? true
      : disclosure === "always_collapsed"
        ? false
        : defaultOpen;
  return (
    <Collapsible
      defaultOpen={resolvedDefaultOpen}
      {...props}
      className={cn(
        density === "compact"
          ? "rounded-xl border-subtle/80 bg-card/70"
          : "rounded-xl border-subtle bg-card",
        className,
      )}
    />
  );
}

function ToolHeader({
  toolType,
  state,
  density = "default",
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  toolType: string;
  state: ToolState;
  density?: ToolDensity;
}) {
  const normalizedState = state.replace(/-/g, " ");

  const icon =
    state === "running" || state === "input-streaming" ? (
      <LoaderCircle
        className="size-3.5 animate-spin text-accent"
        aria-hidden="true"
      />
    ) : state === "output-error" ? (
      <AlertCircle className="size-3.5 text-destructive" aria-hidden="true" />
    ) : (
      <CheckCircle2
        className="size-3.5 text-muted-foreground"
        aria-hidden="true"
      />
    );

  return (
    <CollapsibleTrigger
      aria-label={`${toolType} tool (${normalizedState})`}
      className={cn(
        density === "compact"
          ? "group flex w-full items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-muted/20"
          : "group flex w-full items-center gap-2 px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      <Wrench className="size-3.5 text-muted-foreground" aria-hidden="true" />
      <span
        className={cn(
          "min-w-0 flex-1 font-medium text-foreground",
          density === "compact" ? "text-[13px]" : "text-sm",
        )}
      >
        {toolType}
      </span>
      {icon}
      <ChevronDown
        className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
        aria-hidden="true"
      />
    </CollapsibleTrigger>
  );
}

function ToolContent(props: React.ComponentProps<typeof CollapsibleContent>) {
  const { className, ...rest } = props;
  return (
    <CollapsibleContent
      {...rest}
      className={cn(
        "border-t border-border-subtle/80 px-2.5 py-2 space-y-1.5",
        className,
      )}
    />
  );
}

function ToolInput({
  input,
  className,
}: {
  input: unknown;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)} data-slot="tool-input">
      <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        Input
      </div>
      <pre
        className="overflow-x-auto rounded-md border-subtle/80 bg-muted/20 p-2 text-foreground"
        style={{ ...typo.base, fontFamily: "var(--font-family-mono)" }}
      >
        <code>
          {typeof input === "string" ? input : JSON.stringify(input, null, 2)}
        </code>
      </pre>
    </div>
  );
}

function ToolOutput({
  output,
  errorText,
  className,
}: {
  output?: ReactNode;
  errorText?: string;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)} data-slot="tool-output">
      <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        Output
      </div>
      {errorText ? (
        <div
          className="rounded-md border border-destructive/25 bg-destructive/5 p-2 text-destructive"
          style={typo.base}
        >
          {errorText}
        </div>
      ) : (
        <div
          className="rounded-md border-subtle/80 bg-muted/15 p-2 text-foreground"
          style={typo.base}
        >
          {output}
        </div>
      )}
    </div>
  );
}

export { Tool, ToolHeader, ToolContent, ToolInput, ToolOutput };

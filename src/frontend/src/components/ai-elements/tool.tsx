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
import { cn } from "@/components/ui/utils";

export type ToolState =
  | "input-streaming"
  | "running"
  | "output-available"
  | "output-error";

function Tool({
  className,
  ...props
}: React.ComponentProps<typeof Collapsible>) {
  return (
    <Collapsible
      {...props}
      className={cn(
        "rounded-lg border border-border-subtle bg-card",
        className,
      )}
    />
  );
}

function ToolHeader({
  toolType,
  state,
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  toolType: string;
  state: ToolState;
}) {
  const icon =
    state === "running" || state === "input-streaming" ? (
      <LoaderCircle className="size-4 animate-spin text-amber-500" />
    ) : state === "output-error" ? (
      <AlertCircle className="size-4 text-destructive" />
    ) : (
      <CheckCircle2 className="size-4 text-emerald-500" />
    );

  return (
    <CollapsibleTrigger
      className={cn(
        "group flex w-full items-center gap-2 px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      <Wrench className="size-4 text-muted-foreground" />
      <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
        {toolType}
      </span>
      {icon}
      <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

function ToolContent(props: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      {...props}
      className={cn(
        "border-t border-border-subtle px-3 py-2 space-y-2",
        props.className,
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
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
        Input
      </div>
      <pre className="rounded-md border border-border-subtle bg-muted/40 p-2 text-xs overflow-x-auto">
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
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
        Output
      </div>
      {errorText ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
          {errorText}
        </div>
      ) : (
        <div className="rounded-md border border-border-subtle bg-muted/20 p-2 text-xs text-foreground">
          {output}
        </div>
      )}
    </div>
  );
}

export { Tool, ToolHeader, ToolContent, ToolInput, ToolOutput };

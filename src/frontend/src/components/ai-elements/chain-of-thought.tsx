import type { ReactNode } from "react";
import {
  ChevronDown,
  Circle,
  LoaderCircle,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils/cn";

type StepStatus = "pending" | "active" | "complete" | "error";

function statusIcon(status: StepStatus) {
  if (status === "complete")
    return <CheckCircle2 className="size-4 text-emerald-500" />;
  if (status === "active")
    return <LoaderCircle className="size-4 animate-spin text-amber-500" />;
  if (status === "error")
    return <AlertCircle className="size-4 text-destructive" />;
  return <Circle className="size-3.5 text-muted-foreground" />;
}

function ChainOfThought({
  children,
  defaultOpen = false,
  className,
  ...props
}: React.ComponentProps<typeof Collapsible>) {
  return (
    <Collapsible
      defaultOpen={defaultOpen}
      className={cn(
        "rounded-lg border border-border-subtle bg-card",
        className,
      )}
      {...props}
    >
      {children}
    </Collapsible>
  );
}

function ChainOfThoughtHeader({
  className,
  children,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger>) {
  return (
    <CollapsibleTrigger
      className={cn(
        "group flex w-full items-center justify-between px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      <div className="text-xs font-medium text-muted-foreground">
        {children ?? "Execution trace"}
      </div>
      <ChevronDown className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180" />
    </CollapsibleTrigger>
  );
}

function ChainOfThoughtContent({
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      className={cn("border-t border-border-subtle", className)}
      {...props}
    />
  );
}

function ChainOfThoughtStep({
  label,
  status = "pending",
  icon: Icon,
  children,
  className,
}: {
  label: string;
  status?: StepStatus;
  icon?: React.ComponentType<{ className?: string }>;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn("px-3 py-2", className)}
      data-slot="cot-step"
      data-status={status}
    >
      <div className="flex items-start gap-2">
        <div className="mt-0.5 shrink-0">
          {Icon ? (
            <Icon className="size-4 text-muted-foreground" />
          ) : (
            statusIcon(status)
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm text-foreground">{label}</div>
          {children ? (
            <div className="mt-1 text-xs text-muted-foreground space-y-1">
              {children}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ChainOfThoughtSearchResults({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("mt-1 flex flex-wrap gap-1.5", className)} {...props} />
  );
}
function ChainOfThoughtSearchResult({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "rounded-full border border-border-subtle bg-muted/40 px-2 py-0.5 text-[11px]",
        className,
      )}
      {...props}
    />
  );
}
function ChainOfThoughtImage({
  caption,
  children,
  className,
}: {
  caption?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <figure className={cn("mt-2 space-y-1", className)}>
      {children}
      {caption ? (
        <figcaption className="text-[11px] text-muted-foreground">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

export {
  ChainOfThought,
  ChainOfThoughtHeader,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
  ChainOfThoughtSearchResults,
  ChainOfThoughtSearchResult,
  ChainOfThoughtImage,
};

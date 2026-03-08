import {
  CheckCircle2,
  CircleDot,
  LoaderCircle,
  AlertCircle,
  ChevronDown,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils/cn";

type TaskStatus = "pending" | "in_progress" | "completed" | "error";
export type TaskDensity = "default" | "compact";
export type TaskDisclosure = "auto" | "always_open" | "always_collapsed";

function iconForStatus(status: TaskStatus) {
  switch (status) {
    case "completed":
      return (
        <CheckCircle2
          className="size-3.5 text-muted-foreground"
          aria-hidden="true"
        />
      );
    case "in_progress":
      return (
        <LoaderCircle
          className="size-3.5 animate-spin text-accent"
          aria-hidden="true"
        />
      );
    case "error":
      return (
        <AlertCircle className="size-3.5 text-destructive" aria-hidden="true" />
      );
    default:
      return (
        <CircleDot
          className="size-3.5 text-muted-foreground"
          aria-hidden="true"
        />
      );
  }
}

function Task({
  density = "default",
  disclosure = "auto",
  defaultOpen,
  ...props
}: React.ComponentProps<typeof Collapsible> & {
  density?: TaskDensity;
  disclosure?: TaskDisclosure;
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
        props.className,
      )}
    />
  );
}

function TaskTrigger({
  title,
  status = "pending",
  density = "default",
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  title: string;
  status?: TaskStatus;
  density?: TaskDensity;
}) {
  const normalizedStatus = status.replace(/_/g, " ");

  return (
    <CollapsibleTrigger
      aria-label={`${title} (${normalizedStatus})`}
      className={cn(
        density === "compact"
          ? "group flex w-full items-center gap-2 px-2.5 py-2 text-left transition-colors hover:bg-muted/20"
          : "group flex w-full items-center gap-2 px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      {iconForStatus(status)}
      <span
        className={cn(
          "min-w-0 flex-1 font-medium text-foreground",
          density === "compact" ? "text-[13px]" : "text-sm",
        )}
      >
        {title}
      </span>
      <ChevronDown
        className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
        aria-hidden="true"
      />
    </CollapsibleTrigger>
  );
}

function TaskContent(props: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      {...props}
      className={cn(
        "border-t border-border-subtle/80 px-2.5 py-2",
        props.className,
      )}
    />
  );
}

function TaskItem({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("text-sm text-muted-foreground py-1", className)}
      {...props}
    />
  );
}

function TaskItemFile({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border-subtle bg-muted/40 px-1.5 py-0.5 text-xs",
        "text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export { Task, TaskTrigger, TaskContent, TaskItem, TaskItemFile };

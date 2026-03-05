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

function iconForStatus(status: TaskStatus) {
  switch (status) {
    case "completed":
      return (
        <CheckCircle2 className="size-4 text-emerald-500" aria-hidden="true" />
      );
    case "in_progress":
      return (
        <LoaderCircle
          className="size-4 animate-spin text-amber-500"
          aria-hidden="true"
        />
      );
    case "error":
      return (
        <AlertCircle className="size-4 text-destructive" aria-hidden="true" />
      );
    default:
      return (
        <CircleDot
          className="size-4 text-muted-foreground"
          aria-hidden="true"
        />
      );
  }
}

function Task(props: React.ComponentProps<typeof Collapsible>) {
  return (
    <Collapsible
      {...props}
      className={cn(
        "rounded-lg border border-border-subtle bg-card",
        props.className,
      )}
    />
  );
}

function TaskTrigger({
  title,
  status = "pending",
  className,
  ...props
}: React.ComponentProps<typeof CollapsibleTrigger> & {
  title: string;
  status?: TaskStatus;
}) {
  const normalizedStatus = status.replace(/_/g, " ");

  return (
    <CollapsibleTrigger
      aria-label={`${title} (${normalizedStatus})`}
      className={cn(
        "group flex w-full items-center gap-2 px-3 py-2 text-left",
        className,
      )}
      {...props}
    >
      {iconForStatus(status)}
      <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
        {title}
      </span>
      <ChevronDown
        className="size-4 text-muted-foreground transition-transform group-data-[state=open]:rotate-180"
        aria-hidden="true"
      />
    </CollapsibleTrigger>
  );
}

function TaskContent(props: React.ComponentProps<typeof CollapsibleContent>) {
  return (
    <CollapsibleContent
      {...props}
      className={cn("border-t border-border-subtle px-3 py-2", props.className)}
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
        "inline-flex items-center gap-1 rounded border border-border-subtle bg-muted/40 px-1.5 py-0.5 text-xs",
        className,
      )}
      {...props}
    />
  );
}

export { Task, TaskTrigger, TaskContent, TaskItem, TaskItemFile };

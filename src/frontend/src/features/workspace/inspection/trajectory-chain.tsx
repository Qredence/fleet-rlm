import type { ReactNode } from "react";
import { memo } from "react";
import {
  Activity,
  Brain,
  CheckIcon,
  ChevronDownIcon,
  CircleAlert,
  CircleDashed,
  Terminal,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Streamdown } from "@/components/ui/streamdown";
import { cn } from "@/lib/utils";

import { inspectorInsetClass, inspectorStyles } from "./inspector-styles";
import { renderBadges, statusTone } from "./inspector-ui";

type ChainStatus = "pending" | "running" | "completed" | "failed" | "unknown";
type ChainKind = "reasoning" | "tool" | "span";

function stepStatus(status: ChainStatus): "pending" | "active" | "complete" {
  if (status === "pending") return "pending";
  if (status === "running") return "active";
  return "complete";
}

function StepIcon({ kind, status }: { kind: ChainKind; status: ChainStatus }) {
  if (status === "failed") return CircleAlert;
  if (status === "pending" || status === "running") return CircleDashed;
  if (kind === "tool") return Terminal;
  if (kind === "span") return Activity;
  return Brain;
}

const stepStatusStyles = {
  active: "text-foreground",
  complete: "text-muted-foreground",
  pending: "text-muted-foreground/50",
};

export const TrajectoryChain = memo(function TrajectoryChain({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("not-prose w-full rounded-lg px-3 pt-3 shadow-sm", className)}>
      {children}
    </div>
  );
});

export function TrajectoryChainStep({
  title,
  description,
  status,
  kind = "reasoning",
  body,
  details,
  badges = [],
  children,
  defaultOpen = false,
  isLast = false,
  error = false,
}: {
  title: ReactNode;
  description?: ReactNode;
  status: ChainStatus;
  kind?: ChainKind;
  body?: string;
  details?: string[];
  badges?: string[];
  children?: ReactNode;
  defaultOpen?: boolean;
  isLast?: boolean;
  error?: boolean;
}) {
  const tone = statusTone(status === "unknown" ? "completed" : status);
  const Icon: LucideIcon = StepIcon({ kind, status });
  const hasBody = Boolean(body?.trim());
  const hasDetails = Boolean(details?.length);
  const hasChildren = Boolean(children);

  return (
    <Collapsible
      defaultOpen={defaultOpen}
      className={cn(
        "group/chain-step relative grid grid-cols-[1rem_minmax(0,1fr)] gap-3 text-sm",
        stepStatusStyles[stepStatus(status)],
        "fade-in-0 slide-in-from-top-2 animate-in",
        error && "text-destructive",
      )}
    >
      <div className="relative flex justify-center pt-1.5">
        <Icon className="relative z-10 size-4 bg-background" />
        {!isLast ? (
          <div className="absolute top-7 bottom-0 left-1/2 -mx-px w-px bg-border" />
        ) : null}
      </div>
      <div className="min-w-0 flex-1 space-y-2 overflow-hidden pb-4">
        <CollapsibleTrigger className="group/chain-trigger flex w-full min-w-0 items-center gap-2 text-left text-sm text-foreground transition-colors hover:text-foreground">
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="min-w-0 truncate font-medium text-foreground">{title}</span>
            <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
              {tone.label}
            </Badge>
          </span>
          <span className="relative size-4 shrink-0 text-muted-foreground">
            <ChevronDownIcon
              className={cn(
                "absolute inset-0 size-4 transition-all group-data-[state=open]/chain-trigger:rotate-180",
                "group-hover/chain-trigger:scale-0 group-data-[state=open]/chain-trigger:scale-100",
              )}
            />
            <CheckIcon className="absolute inset-0 size-4 scale-0 transition-transform group-hover/chain-trigger:scale-100 group-data-[state=open]/chain-trigger:scale-0" />
          </span>
        </CollapsibleTrigger>
        {description || badges.length > 0 ? (
          <div className="space-y-2">
            {description ? (
              <div className="text-muted-foreground typo-caption wrap-break-word">
                {description}
              </div>
            ) : null}
            {renderBadges(badges, "secondary")}
          </div>
        ) : null}
        {hasBody || hasDetails || hasChildren ? (
          <CollapsibleContent
            className={cn(
              "mt-2 space-y-3",
              "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 text-popover-foreground outline-none data-[state=closed]:animate-out data-[state=open]:animate-in",
            )}
          >
            {hasBody ? (
              <div
                className={cn(
                  "text-muted-foreground text-sm leading-6",
                  inspectorInsetClass(error ? "error" : "strong"),
                  "max-w-full overflow-hidden text-foreground",
                )}
              >
                <Streamdown content={body ?? ""} streaming={false} />
              </div>
            ) : null}
            {!hasBody && hasDetails ? (
              <div className={inspectorStyles.stack.compact}>
                {details?.map((detail, index) => (
                  <div
                    key={`${String(title)}-detail-${index}`}
                    className={cn(
                      "text-muted-foreground text-sm leading-6",
                      inspectorInsetClass(),
                      "text-foreground",
                    )}
                  >
                    {detail}
                  </div>
                ))}
              </div>
            ) : null}
            {children}
          </CollapsibleContent>
        ) : null}
      </div>
    </Collapsible>
  );
}

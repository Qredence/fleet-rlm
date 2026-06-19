import type { ReactNode } from "react";
import { Activity, Brain, CircleAlert, CircleDashed, Terminal } from "lucide-react";

import {
  ChainOfThought,
  ChainOfThoughtContent,
  ChainOfThoughtItem,
  ChainOfThoughtStep,
  ChainOfThoughtTrigger,
} from "@/components/ai-elements/chain-of-thought";
import { Badge } from "@/components/ui/badge";
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

export function TrajectoryChain({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <ChainOfThought
      className={cn(
        "rounded-lg border border-border-subtle/80 bg-card/70 px-3 pt-3 shadow-sm",
        className,
      )}
    >
      {children}
    </ChainOfThought>
  );
}

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
  const Icon = StepIcon({ kind, status });
  const hasBody = Boolean(body?.trim());
  const hasDetails = Boolean(details?.length);
  const hasChildren = Boolean(children);

  return (
    <ChainOfThoughtStep
      defaultOpen={defaultOpen}
      icon={Icon}
      isLast={isLast}
      status={stepStatus(status)}
      className={cn(error && "text-destructive")}
    >
      <ChainOfThoughtTrigger leftIcon={null}>
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="min-w-0 truncate font-medium text-foreground">{title}</span>
          <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
            {tone.label}
          </Badge>
        </span>
      </ChainOfThoughtTrigger>
      {description || badges.length > 0 ? (
        <div className="space-y-2">
          {description ? (
            <div className="text-muted-foreground typo-caption wrap-break-word">{description}</div>
          ) : null}
          {renderBadges(badges, "secondary")}
        </div>
      ) : null}
      {hasBody || hasDetails || hasChildren ? (
        <ChainOfThoughtContent>
          {hasBody ? (
            <ChainOfThoughtItem
              className={cn(
                inspectorInsetClass(error ? "error" : "strong"),
                "max-w-full overflow-hidden text-foreground",
              )}
            >
              <Streamdown content={body ?? ""} streaming={false} />
            </ChainOfThoughtItem>
          ) : null}
          {!hasBody && hasDetails ? (
            <div className={inspectorStyles.stack.compact}>
              {details?.map((detail, index) => (
                <ChainOfThoughtItem
                  key={`${String(title)}-detail-${index}`}
                  className={cn(inspectorInsetClass(), "text-foreground")}
                >
                  {detail}
                </ChainOfThoughtItem>
              ))}
            </div>
          ) : null}
          {children}
        </ChainOfThoughtContent>
      ) : null}
    </ChainOfThoughtStep>
  );
}

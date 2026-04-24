import { motion, useReducedMotion } from "motion/react";
import { CircleCheck, MessageSquare } from "lucide-react";
import type { ChatMessage } from "@/features/workspace/use-workspace";
import { Card, CardContent } from "@/components/ui/card";
import { OptionList } from "@/components/tool-ui/option-list";

interface Props {
  data: NonNullable<ChatMessage["clarificationData"]>;
  onResolve: (answer: string) => void;
}

export function ClarificationCard({ data, onResolve }: Props) {
  const prefersReduced = useReducedMotion();

  const resolvedOptionId = data.resolved
    ? (data.options.find((o) => o.label === data.resolvedAnswer)?.id ?? null)
    : undefined;

  const handleAction = async (actionId: string, selection: string[] | string | null) => {
    if (actionId !== "confirm") return;
    const selectedId = typeof selection === "string" ? selection : null;
    if (!selectedId) return;
    const option = data.options.find((o) => o.id === selectedId);
    if (option) onResolve(option.label);
  };

  // ── Resolved state ────────────────────────────────────────────
  if (data.resolved) {
    return (
      <motion.div
        initial={{ opacity: 0.8 }}
        animate={{ opacity: 1 }}
        transition={prefersReduced ? { duration: 0.01 } : { duration: 0.2 }}
      >
        <Card>
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="shrink-0 [&_svg]:h-4 [&_svg]:w-4" aria-hidden="true">
                  <CircleCheck className="text-chart-3" />
                </span>
                <span className="text-muted-foreground typo-helper">{data.stepLabel}</span>
              </div>
              <p className="text-muted-foreground typo-caption">{data.question}</p>
            </div>
            {resolvedOptionId != null ? (
              <OptionList
                id={data.stepLabel}
                options={data.options}
                selectionMode="single"
                choice={resolvedOptionId}
              />
            ) : (
              <p className="text-foreground typo-label">{data.resolvedAnswer}</p>
            )}
          </CardContent>
        </Card>
      </motion.div>
    );
  }

  // ── Active state ──────────────────────────────────────────────
  return (
    <motion.div
      initial={{ opacity: 0, y: prefersReduced ? 0 : 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        prefersReduced ? { duration: 0.01 } : { duration: 0.25, ease: [0.25, 0.1, 0.25, 1] }
      }
    >
      <Card className="border-border-subtle bg-card rounded-xl">
        <CardContent className="flex flex-col gap-4 p-4">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="shrink-0 [&_svg]:h-4 [&_svg]:w-4" aria-hidden="true">
                <MessageSquare className="text-muted-foreground" />
              </span>
              <span className="text-muted-foreground typo-helper">{data.stepLabel}</span>
            </div>
            <p className="text-foreground typo-label">{data.question}</p>
          </div>
          <OptionList
            id={data.stepLabel}
            options={data.options}
            selectionMode="single"
            actions={[{ id: "confirm", label: "Confirm", variant: "default" }]}
            onAction={handleAction}
          />
        </CardContent>
      </Card>
    </motion.div>
  );
}

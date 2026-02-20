import { motion, useReducedMotion } from "motion/react";
import { Check, MessageSquare } from "lucide-react";
import { typo } from "../../components/config/typo";
import type { ChatMessage } from "../../components/data/types";
import { Button } from "../../components/ui/button";
import { Card, CardContent } from "../../components/ui/card";
import { ResolvedChip } from "../../components/shared/ResolvedChip";
import { fadeUp, fadeUpReduced } from "./animation-presets";

interface HitlCardProps {
  data: NonNullable<ChatMessage["hitlData"]>;
  onResolve: (label: string) => void;
}

/**
 * HitlCard — Human-in-the-loop checkpoint card.
 *
 * Shows a question with action buttons; once resolved it displays a
 * confirmation chip instead.
 */
export function HitlCard({ data, onResolve }: HitlCardProps) {
  const prefersReduced = useReducedMotion();
  const preset = prefersReduced ? fadeUpReduced : fadeUp;

  return (
    <motion.div {...preset}>
      <Card className="border-border-subtle bg-card rounded-xl overflow-hidden shadow-sm">
        <CardContent className="p-5">
          <div className="flex items-center gap-3 mb-4">
            <div className="size-7 rounded-md bg-muted flex items-center justify-center">
              <MessageSquare
                className="w-4 h-4 text-muted-foreground"
                aria-hidden="true"
              />
            </div>
            <span className="text-foreground" style={typo.h4}>
              Checkpoint
            </span>
          </div>
          <p className="text-muted-foreground mb-4" style={typo.labelRegular}>
            {data.question}
          </p>

          {data.resolved ? (
            <ResolvedChip icon={<Check strokeWidth={3} />}>
              {data.resolvedLabel}
            </ResolvedChip>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              {data.actions.map((action) => (
                <Button
                  key={action.label}
                  variant={action.variant === "primary" ? "default" : "outline"}
                  size="sm"
                  onClick={() => onResolve(action.label)}
                >
                  {action.label}
                </Button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}

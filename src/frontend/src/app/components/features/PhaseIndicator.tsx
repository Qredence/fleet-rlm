import { typo } from "../config/typo";
import type { CreationPhase } from "../data/types";
import { Card, CardContent } from "../ui/card";
import { Check } from "lucide-react";

interface Props {
  phase: CreationPhase;
  className?: string;
}

export function PhaseIndicator({ phase, className }: Props) {
  const phases = [
    {
      keys: ["understanding"] as CreationPhase[],
      label: "Understanding",
      num: 1,
    },
    { keys: ["generating"] as CreationPhase[], label: "Generation", num: 2 },
    {
      keys: ["validating", "complete"] as CreationPhase[],
      label: "Validation",
      num: 3,
    },
  ];

  const order: CreationPhase[] = [
    "idle",
    "understanding",
    "generating",
    "validating",
    "complete",
  ];
  const idx = order.indexOf(phase);

  return (
    <Card className={className}>
      <CardContent className="p-3 md:p-4">
        <div className="flex items-center gap-2">
          {phases.map((p, i) => {
            const isActive = p.keys.includes(phase);
            const firstPhase = p.keys[0];
            const isDone =
              firstPhase != null && idx > order.indexOf(firstPhase);
            return (
              <div key={p.num} className="flex items-center gap-1 flex-1">
                <div className="flex items-center gap-2 flex-1">
                  <div
                    className="w-6 h-6 rounded-full flex items-center justify-center shrink-0 transition-colors"
                    style={{
                      backgroundColor:
                        isDone || isActive ? "var(--accent)" : "var(--muted)",
                    }}
                  >
                    {isDone ? (
                      <Check
                        className="w-3.5 h-3.5"
                        style={{ color: "var(--accent-foreground)" }}
                        aria-hidden="true"
                      />
                    ) : (
                      <span
                        style={{
                          ...typo.helper,
                          color: isActive
                            ? "var(--accent-foreground)"
                            : "var(--muted-foreground)",
                          fontWeight: "var(--font-weight-medium)",
                        }}
                      >
                        {p.num}
                      </span>
                    )}
                  </div>
                  <span
                    style={{
                      ...typo.helper,
                      color:
                        isActive || isDone
                          ? "var(--foreground)"
                          : "var(--muted-foreground)",
                      fontWeight: isActive
                        ? "var(--font-weight-medium)"
                        : "var(--font-weight-regular)",
                    }}
                  >
                    {p.label}
                  </span>
                </div>
                {i < phases.length - 1 && (
                  <div
                    className="h-px flex-1 mx-1 transition-colors"
                    style={{
                      backgroundColor: isDone
                        ? "var(--accent)"
                        : "var(--border)",
                    }}
                  />
                )}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

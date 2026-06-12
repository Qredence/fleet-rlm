import { Badge } from "@/components/ui/badge";
import {
  SectionCard,
  SectionCardContent,
  SectionCardDescription,
  SectionCardHeader,
  SectionCardTitle,
} from "@/components/product/section-layout";
import type { LlmRoleBindingResponse } from "@/lib/rlm-api/llm-profiles";

import { ROLE_ROWS } from "./constants";

interface ActiveBindingsSummaryProps {
  bindings: LlmRoleBindingResponse[];
}

export function ActiveBindingsSummary({ bindings }: ActiveBindingsSummaryProps) {
  const bindingByRole = Object.fromEntries(bindings.map((binding) => [binding.role, binding]));

  return (
    <SectionCard variant="subtle">
      <SectionCardHeader className="border-b border-border-subtle/70 pb-4">
        <SectionCardTitle>Active role bindings</SectionCardTitle>
        <SectionCardDescription>
          Current planner and delegate models resolved from saved profile assignments.
        </SectionCardDescription>
      </SectionCardHeader>
      <SectionCardContent className="flex flex-col gap-3 pt-4">
        {ROLE_ROWS.map((row) => {
          const binding = bindingByRole[row.role];
          const profileLabel = binding?.profile_name ?? "No profile";
          const modelLabel = binding?.model_id || "not set";
          const isConfigured = Boolean(binding?.profile_id && binding?.model_id);

          return (
            <div
              key={row.role}
              className="flex flex-col gap-1 rounded-lg border border-border-subtle/70 bg-background/60 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div>
                <p className="text-sm font-medium text-foreground">{row.title}</p>
                <p className="text-xs text-muted-foreground">{row.description}</p>
              </div>
              <div className="flex min-w-0 flex-col items-start gap-1 text-xs text-muted-foreground sm:items-end sm:text-right">
                <span>
                  {profileLabel}
                  {isConfigured ? null : (
                    <Badge variant="secondary" className="ml-2">
                      incomplete
                    </Badge>
                  )}
                </span>
                <span className="typo-body-xs font-mono text-foreground/80">{modelLabel}</span>
              </div>
            </div>
          );
        })}
      </SectionCardContent>
    </SectionCard>
  );
}

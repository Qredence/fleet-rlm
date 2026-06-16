import { Badge } from "@/components/ui/badge";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldTitle,
} from "@/components/ui/field";
import type { LlmRoleBindingResponse } from "@/lib/rlm-api/llm-profiles";

import { ROLE_ROWS, SETTINGS_FIELD_CLASSNAME } from "./constants";

interface ActiveBindingsSummaryProps {
  bindings: LlmRoleBindingResponse[];
}

export function ActiveBindingsSummary({ bindings }: ActiveBindingsSummaryProps) {
  const bindingByRole = Object.fromEntries(bindings.map((binding) => [binding.role, binding]));

  return (
    <FieldGroup className="gap-0">
      <Field orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Active role bindings</FieldTitle>
          <FieldDescription>
            Current planner and delegate models resolved from saved profile assignments.
          </FieldDescription>
        </FieldContent>
      </Field>
      {ROLE_ROWS.map((row) => {
        const binding = bindingByRole[row.role];
        const profileLabel = binding?.profile_name ?? "No profile";
        const modelLabel = binding?.model_id || "not set";
        const isConfigured = Boolean(binding?.profile_id && binding?.model_id);

        return (
          <Field key={row.role} orientation="responsive" className={SETTINGS_FIELD_CLASSNAME}>
            <FieldContent>
              <FieldTitle>{row.title}</FieldTitle>
              <FieldDescription>{row.description}</FieldDescription>
            </FieldContent>
            <div className="flex min-w-0 flex-1 flex-col items-start gap-1 text-sm text-muted-foreground sm:max-w-sm sm:items-end sm:text-right">
              <span className="max-w-full truncate">
                {profileLabel}
                {isConfigured ? null : (
                  <Badge variant="secondary" className="ml-2">
                    incomplete
                  </Badge>
                )}
              </span>
              <span className="typo-body-xs max-w-full truncate font-mono text-foreground/80">
                {modelLabel}
              </span>
            </div>
          </Field>
        );
      })}
    </FieldGroup>
  );
}

import type { ReactNode } from "react";

import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";

/**
 * CompactField — shared label/description wrapper used across optimization
 * form sections. Keeps spacing and label styling consistent.
 */
export function CompactField({
  label,
  description,
  children,
  icon,
}: {
  label: string;
  description?: string;
  children: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <Field className="gap-1.5 transition-all duration-200">
      <FieldLabel className="flex items-center gap-1.5 font-medium text-foreground">
        {icon}
        {label}
      </FieldLabel>
      {children}
      {description ? <FieldDescription>{description}</FieldDescription> : null}
    </Field>
  );
}

export const DEFAULT_SELECT_VALUE = "__default__";

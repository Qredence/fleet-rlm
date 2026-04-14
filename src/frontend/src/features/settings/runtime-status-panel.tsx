import { Badge } from "@/components/ui/badge";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldTitle,
} from "@/components/ui/field";
import type { RuntimeStatusResponse } from "@/lib/rlm-api";

export function formatCheckLabel(key: string): string {
  return key
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function shouldHydrateRuntimeForm(
  snapshot: { values?: Record<string, string> } | undefined,
  hasUnsavedRuntimeChanges: boolean,
): boolean {
  return Boolean(snapshot) && !hasUnsavedRuntimeChanges;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

const STATUS_FIELD_CLASSNAME = "border-b border-border-subtle py-4 last:border-b-0";

interface RuntimeStatusPanelProps {
  status: RuntimeStatusResponse | undefined;
}

export function RuntimeStatusPanel({ status }: RuntimeStatusPanelProps) {
  const activeModels = status?.active_models;

  return (
    <>
      <Field orientation="responsive" className={STATUS_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Runtime Status</FieldTitle>
          <FieldDescription>
            {status
              ? `Environment: ${status.app_env}. Runtime readiness is ${
                  status.ready ? "healthy" : "degraded"
                }.`
              : "Loading runtime status…"}
          </FieldDescription>
        </FieldContent>
        <Badge variant={status?.ready ? "default" : "secondary"}>
          {status?.ready ? "Ready" : "Needs Attention"}
        </Badge>
      </Field>

      <Field orientation="responsive" className={STATUS_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Active Models</FieldTitle>
          <FieldDescription>
            Resolved runtime model identifiers currently used for planner/delegate execution.
          </FieldDescription>
        </FieldContent>
        <div className="flex min-w-0 flex-col items-end gap-1 text-right text-xs text-muted-foreground">
          <div>Planner: {activeModels?.planner || "not set"}</div>
          <div>Delegate: {activeModels?.delegate || "not set"}</div>
          <div>Delegate small: {activeModels?.delegate_small || "not set"}</div>
        </div>
      </Field>

      {status?.write_enabled === false ? (
        <Field orientation="responsive" className="py-4">
          <FieldContent>
            <FieldTitle>Write Protection</FieldTitle>
            <FieldDescription>
              Runtime settings updates are disabled because APP_ENV is not local.
            </FieldDescription>
          </FieldContent>
          <Badge variant="destructive">Read-only</Badge>
        </Field>
      ) : null}
    </>
  );
}

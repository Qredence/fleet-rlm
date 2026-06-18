import { Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Field, FieldContent, FieldDescription, FieldTitle } from "@/components/ui/field";
import { useServiceInfo } from "@/hooks/runtime/use-service-info";

const PANEL_FIELD_CLASSNAME = "border-b border-border-subtle py-4 last:border-b-0";

export function ServiceInfoPanel() {
  const { data: info, isLoading, isError } = useServiceInfo();

  if (isLoading) {
    return (
      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>
            <Info className="mr-1.5 inline-block size-3.5 align-text-bottom" />
            About this instance
          </FieldTitle>
          <FieldDescription>Loading service metadata…</FieldDescription>
        </FieldContent>
      </Field>
    );
  }

  if (isError || !info) {
    return (
      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>About this instance</FieldTitle>
          <FieldDescription>Service information is unavailable.</FieldDescription>
        </FieldContent>
        <Badge variant="destructive">Unavailable</Badge>
      </Field>
    );
  }

  return (
    <>
      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Version</FieldTitle>
          <FieldDescription>Package version currently serving the API.</FieldDescription>
        </FieldContent>
        <Badge variant="secondary" className="font-mono text-xs">
          v{info.version}
        </Badge>
      </Field>

      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Environment</FieldTitle>
          <FieldDescription>Active deployment environment for this instance.</FieldDescription>
        </FieldContent>
        <Badge variant={info.app_env === "production" ? "default" : "secondary"}>
          {info.app_env}
        </Badge>
      </Field>

      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Authentication</FieldTitle>
          <FieldDescription>
            {info.auth_required ? "Authentication is enforced." : "Running in open-access mode."}
          </FieldDescription>
        </FieldContent>
        <div className="flex min-w-0 flex-col items-end gap-1 text-right text-xs text-muted-foreground">
          <Badge variant={info.auth_required ? "default" : "secondary"}>{info.auth_mode}</Badge>
          <span>{info.auth_required ? "Required" : "Optional"}</span>
        </div>
      </Field>

      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Sandbox Provider</FieldTitle>
          <FieldDescription>Active sandbox backend for runtime execution.</FieldDescription>
        </FieldContent>
        <Badge variant="secondary" className="font-mono text-xs">
          {info.sandbox_provider}
        </Badge>
      </Field>

      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Planner Model</FieldTitle>
          <FieldDescription>Primary LM identifier used for planning.</FieldDescription>
        </FieldContent>
        <span className="text-right text-xs text-muted-foreground">
          {info.agent_model ?? "not configured"}
        </span>
      </Field>

      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>RLM Limits</FieldTitle>
          <FieldDescription>
            Maximum recursive depth and ReAct iterations per top-level run.
          </FieldDescription>
        </FieldContent>
        <div className="flex min-w-0 flex-col items-end gap-1 text-right text-xs text-muted-foreground">
          <span>Depth: {info.rlm_max_depth}</span>
          <span>Iterations: {info.rlm_max_iterations}</span>
        </div>
      </Field>

      <Field orientation="responsive" className={PANEL_FIELD_CLASSNAME}>
        <FieldContent>
          <FieldTitle>Features</FieldTitle>
          <FieldDescription>Capabilities enabled on this instance.</FieldDescription>
        </FieldContent>
        <div className="flex min-w-0 flex-wrap justify-end gap-1">
          {info.database_enabled ? (
            <Badge variant="secondary" className="text-xs">
              Database
            </Badge>
          ) : null}
          {info.serve_ui ? (
            <Badge variant="secondary" className="text-xs">
              UI
            </Badge>
          ) : null}
          {info.expose_docs ? (
            <Badge variant="secondary" className="text-xs">
              API Docs
            </Badge>
          ) : null}
        </div>
      </Field>
    </>
  );
}

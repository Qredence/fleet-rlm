import { useCallback, useMemo, useRef, useState, type ChangeEvent } from "react";
import { Brain, Settings2, Sparkles, TriangleAlert, Wrench } from "lucide-react";
import { toast } from "sonner";

import {
  InputBar,
  type AttachedFile,
  type InputBarProps,
} from "@/components/agent-elements/input-bar";
import { ModelPicker, type ModelOption } from "@/components/agent-elements/input/model-picker";
import { ModeSelector } from "@/components/agent-elements/input/mode-selector";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ExecutionStatusBar } from "@/features/workspace/screen/execution-status-bar";
import {
  useLlmProfileModels,
  useLlmProfilesMutations,
  useLlmRoleBindings,
} from "@/features/settings/use-llm-profiles";
import { errorMessage } from "@/features/settings/runtime-status-panel";
import { useRuntimeSettings } from "@/features/settings/use-runtime-settings";
import { createLocalId } from "@/lib/id";
import type { WsExecutionMode } from "@/lib/rlm-api/ws-types";
import { cn } from "@/lib/utils";

const EXECUTION_MODE_OPTIONS = [
  { id: "auto", icon: Sparkles, label: "Auto" },
  { id: "rlm_only", icon: Brain, label: "RLM" },
  { id: "tools_only", icon: Wrench, label: "Tools" },
] as const;

interface WorkspaceAgentInputBarProps extends InputBarProps {
  executionMode: WsExecutionMode;
  onExecutionModeChange: (mode: WsExecutionMode) => void;
  activeModels?: {
    planner?: string | null;
    delegate?: string | null;
    delegate_small?: string | null;
  };
  onOpenModelSettings?: () => void;
  showStatusBar?: boolean;
  runtimeWarning?: {
    title: string;
    description: string;
    guidance: string[];
    onOpenSettings: () => void;
  };
}

function ExecutionModeToggle({
  value,
  onChange,
}: {
  value: WsExecutionMode;
  onChange: (mode: WsExecutionMode) => void;
}) {
  return (
    <ModeSelector
      modes={EXECUTION_MODE_OPTIONS}
      value={value}
      onChange={(mode) => onChange(mode as WsExecutionMode)}
    />
  );
}

function plannerModelOptions(
  models: Array<{ id: string; label: string }> | undefined,
  activePlannerModel: string | undefined,
): ModelOption[] {
  const catalog = models ?? [];
  if (catalog.length === 0) {
    return [
      {
        id: activePlannerModel ?? "planner",
        label: activePlannerModel || "Planner model",
        description: "Configure a planner profile in Settings",
        disabled: true,
      },
    ];
  }
  return catalog.map((model) => ({
    id: model.id,
    label: model.label,
    description: model.id === activePlannerModel ? "Active planner model" : undefined,
  }));
}

export function WorkspaceAgentInputBar({
  executionMode,
  onExecutionModeChange,
  activeModels,
  onOpenModelSettings,
  showStatusBar = true,
  runtimeWarning,
  className,
  rightActions,
  attachedFiles = [],
  onAttach,
  onRemoveFile,
  onSend,
  ...props
}: WorkspaceAgentInputBarProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [stagedDocuments, setStagedDocuments] = useState<AttachedFile[]>([]);
  const { statusQuery } = useRuntimeSettings();
  const rolesQuery = useLlmRoleBindings();
  const { saveRoleBindings } = useLlmProfilesMutations();

  const plannerBinding = rolesQuery.data?.bindings?.find((binding) => binding.role === "planner");
  const plannerProfileId = plannerBinding?.profile_id ?? null;
  const plannerModelId = plannerBinding?.model_id ?? "";
  const modelsQuery = useLlmProfileModels(plannerProfileId);
  const writeEnabled = statusQuery.data?.profile_write_enabled !== false;

  const pickerModels = useMemo(
    () => plannerModelOptions(modelsQuery.data?.models, activeModels?.planner ?? plannerModelId),
    [modelsQuery.data?.models, activeModels?.planner, plannerModelId],
  );

  const handlePlannerModelChange = useCallback(
    (modelId: string) => {
      if (!writeEnabled || !plannerProfileId) {
        toast.error("Planner model switching is unavailable for this runtime.");
        return;
      }
      saveRoleBindings.mutate(
        {
          planner: {
            profile_id: plannerProfileId,
            model_id: modelId,
          },
        },
        {
          onError: (error) => {
            toast.error("Failed to switch planner model", { description: errorMessage(error) });
          },
        },
      );
    },
    [plannerProfileId, saveRoleBindings, writeEnabled],
  );

  const handleAddDocument = useCallback(() => {
    onAttach?.();
    fileInputRef.current?.click();
  }, [onAttach]);

  const handleDocumentInputChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) return;
    setStagedDocuments((current) => [
      ...current,
      ...files.map((file) => ({
        id: createLocalId("document"),
        filename: file.name,
        size: file.size,
      })),
    ]);
    event.currentTarget.value = "";
  }, []);

  const handleRemoveFile = useCallback(
    (id: string) => {
      setStagedDocuments((current) => current.filter((file) => file.id !== id));
      onRemoveFile?.(id);
    },
    [onRemoveFile],
  );

  const handleSend: InputBarProps["onSend"] = useCallback(
    (message) => {
      setStagedDocuments([]);
      onSend(message);
    },
    [onSend],
  );

  const canSwitchPlanner =
    writeEnabled && Boolean(plannerProfileId) && pickerModels.some((model) => !model.disabled);

  return (
    <div className={cn("mx-auto flex w-full max-w-workbench-composer flex-col gap-2", className)}>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".csv,.doc,.docx,.json,.md,.pdf,.rtf,.txt,.yaml,.yml"
        className="hidden"
        onChange={handleDocumentInputChange}
      />
      {runtimeWarning ? (
        <Alert className="rounded-md border-warning/25 bg-warning/5 text-foreground">
          <TriangleAlert className="text-warning size-4" />
          <AlertTitle className="text-sm font-medium">{runtimeWarning.title}</AlertTitle>
          <AlertDescription>
            <div className="mt-1 flex flex-col gap-3">
              <div className="flex items-start justify-between gap-4">
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {runtimeWarning.description}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 shrink-0 gap-1.5 rounded-md text-xs"
                  onClick={runtimeWarning.onOpenSettings}
                >
                  <Settings2 className="size-3" />
                  Settings
                </Button>
              </div>
              {runtimeWarning.guidance.length > 0 ? (
                <ul className="list-disc space-y-1 pl-4 text-xs text-muted-foreground/80">
                  {runtimeWarning.guidance.map((msg) => (
                    <li key={msg}>{msg}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          </AlertDescription>
        </Alert>
      ) : null}
      {showStatusBar ? <ExecutionStatusBar /> : null}
      <InputBar
        {...props}
        className="px-0 pb-0"
        attachedFiles={[...attachedFiles, ...stagedDocuments]}
        onAttach={handleAddDocument}
        onRemoveFile={handleRemoveFile}
        onSend={handleSend}
        rightActions={
          <>
            <ExecutionModeToggle value={executionMode} onChange={onExecutionModeChange} />
            <ModelPicker
              models={pickerModels}
              value={plannerModelId || pickerModels[0]?.id}
              onChange={canSwitchPlanner ? handlePlannerModelChange : undefined}
              onConfigure={onOpenModelSettings}
              disabled={!canSwitchPlanner && !onOpenModelSettings}
              className="text-an-foreground-muted hover:text-an-foreground"
            />
            {rightActions}
          </>
        }
      />
    </div>
  );
}

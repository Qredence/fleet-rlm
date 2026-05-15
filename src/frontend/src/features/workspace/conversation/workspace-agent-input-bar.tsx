import { useCallback, useRef, useState, type ChangeEvent } from "react";
import { Brain, Settings2, Sparkles, TriangleAlert, Wrench } from "lucide-react";

import {
  InputBar,
  type AttachedFile,
  type InputBarProps,
} from "@/components/agent-elements/input-bar";
import { ModeSelector } from "@/components/agent-elements/input/mode-selector";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ExecutionStatusBar } from "@/features/workspace/screen/execution-status-bar";
import type { WsExecutionMode } from "@/lib/rlm-api/ws-types";
import { cn } from "@/lib/utils";

const EXECUTION_MODE_OPTIONS = [
  { id: "auto", icon: Sparkles, label: "Auto" },
  { id: "rlm_only", icon: Brain, label: "RLM" },
  { id: "tools_only", icon: Wrench, label: "Tools" },
] as const;

function createAttachmentId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

interface WorkspaceAgentInputBarProps extends InputBarProps {
  executionMode: WsExecutionMode;
  onExecutionModeChange: (mode: WsExecutionMode) => void;
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
      onChange={(nextValue) => onChange(nextValue as WsExecutionMode)}
      className="text-an-foreground-muted hover:text-an-foreground"
    />
  );
}

export function WorkspaceAgentInputBar({
  executionMode,
  onExecutionModeChange,
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
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [stagedDocuments, setStagedDocuments] = useState<AttachedFile[]>([]);

  const handleAddDocument = useCallback(() => {
    onAttach?.();
    fileInputRef.current?.click();
  }, [onAttach]);

  const handleDocumentInputChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.currentTarget.files ?? []);
    if (files.length > 0) {
      setStagedDocuments((current) => [
        ...current,
        ...files.map((file) => ({
          id: `document-${createAttachmentId()}`,
          filename: file.name,
          size: file.size,
        })),
      ]);
    }
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

  return (
    <div className={cn("mx-auto flex w-full max-w-175 flex-col gap-3", className)}>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".csv,.doc,.docx,.json,.md,.pdf,.rtf,.txt,.yaml,.yml"
        className="hidden"
        onChange={handleDocumentInputChange}
      />
      {runtimeWarning ? (
        <Alert className="border-amber-500/25 bg-amber-500/5 text-foreground rounded-lg">
          <TriangleAlert className="text-amber-500 size-4" />
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
                  className="h-7 shrink-0 gap-1.5 rounded-lg text-xs"
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
            {rightActions}
          </>
        }
      />
    </div>
  );
}

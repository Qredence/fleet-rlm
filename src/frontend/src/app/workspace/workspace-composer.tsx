import { useCallback, useEffect, useState } from "react";
import { ArrowUp, FileText, Square, X } from "lucide-react";
import { nanoid } from "nanoid";
import { toast } from "sonner";

import { AttachmentDropdown } from "@/app/workspace/composer/AttachmentDropdown";
import { ExecutionModeDropdown } from "@/app/workspace/composer/ExecutionModeDropdown";
import { RuntimeModeDropdown } from "@/app/workspace/composer/RuntimeModeDropdown";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ui/prompt-input";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/wsTypes";

interface AttachedFile {
  id: string;
  file: File;
  previewUrl?: string;
}

interface WorkspaceComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (attachments: AttachedFile[]) => void;
  onStop?: () => void;
  isLoading?: boolean;
  isReceiving?: boolean;
  attachmentsEnabled?: boolean;
  runtimeMode: WsRuntimeMode;
  onRuntimeModeChange: (mode: WsRuntimeMode) => void;
  executionMode: WsExecutionMode;
  onExecutionModeChange: (mode: WsExecutionMode) => void;
  canSubmit?: boolean;
  placeholder?: string;
  className?: string;
}

function createAttachmentId() {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `attachment-${nanoid()}`;
}

function revokeAttachmentPreview(attachment: AttachedFile) {
  if (attachment.previewUrl) {
    URL.revokeObjectURL(attachment.previewUrl);
  }
}

function WorkspaceComposer({
  value,
  onChange,
  onSend,
  onStop,
  isLoading = false,
  isReceiving = false,
  attachmentsEnabled = true,
  runtimeMode,
  onRuntimeModeChange,
  executionMode,
  onExecutionModeChange,
  canSubmit = true,
  placeholder = "Ask, search or make anything...",
  className,
}: WorkspaceComposerProps) {
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);
  const hasContent = value.trim().length > 0;
  const canSubmitMessage = hasContent && canSubmit;
  const isStreamingActive = isLoading && isReceiving;

  useEffect(
    () => () => {
      attachments.forEach(revokeAttachmentPreview);
    },
    [attachments],
  );

  const handleFilesSelected = useCallback((files: File[] | FileList) => {
    if (files.length > 0) {
      const newAttachments: AttachedFile[] = Array.from(files).map((file) => ({
        id: createAttachmentId(),
        file,
        previewUrl: file.type.startsWith("image/")
          ? URL.createObjectURL(file)
          : undefined,
      }));
      setAttachments((prev) => [...prev, ...newAttachments]);
    }
  }, []);

  const handleRemoveAttachment = useCallback((id: string) => {
    setAttachments((prev) => {
      const removed = prev.find((attachment) => attachment.id === id);
      if (removed) revokeAttachmentPreview(removed);
      return prev.filter((attachment) => attachment.id !== id);
    });
  }, []);

  const handleSubmit = useCallback(() => {
    if (!canSubmitMessage) return;
    const currentAttachments = [...attachments];
    currentAttachments.forEach(revokeAttachmentPreview);
    setAttachments([]);
    onSend(currentAttachments);
  }, [attachments, canSubmitMessage, onSend]);

  const handleUnsupportedAttachmentSelect = useCallback(() => {
    toast.info("File upload is not available yet", {
      description:
        "This backend currently does not accept binary upload payloads.",
    });
  }, []);

  const headerContent =
    attachments.length > 0 ? (
      <PromptInputHeader className="flex flex-col gap-3 px-1 pb-1 pt-1">
        <div className="flex flex-wrap gap-2">
          {attachments.map((attachment) => {
            const isImage = attachment.file.type.startsWith("image/");
            return (
              <div
                key={attachment.id}
                className="prompt-composer-attachment-chip group relative flex max-w-55 shrink-0 items-center gap-2 rounded-full px-2.5 py-1.5 text-xs"
              >
                {isImage && attachment.previewUrl ? (
                  <img
                    src={attachment.previewUrl}
                    alt={attachment.file.name}
                    className="h-5 w-5 shrink-0 rounded-full object-cover"
                  />
                ) : (
                  <FileText className="prompt-composer-attachment-chip-icon h-4 w-4 shrink-0" />
                )}

                <span className="truncate">{attachment.file.name}</span>

                <button
                  type="button"
                  onClick={() => handleRemoveAttachment(attachment.id)}
                  className="prompt-composer-attachment-chip-remove ml-auto flex h-5 w-5 shrink-0 items-center justify-center rounded-full"
                  aria-label={`Remove ${attachment.file.name}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            );
          })}
        </div>
      </PromptInputHeader>
    ) : null;

  return (
    <div className={className}>
      <PromptInput
        className="w-full"
        onSubmit={async () => {
          handleSubmit();
        }}
      >
        {headerContent}

        <PromptInputBody>
          <PromptInputTextarea
            aria-label="Message"
            className="min-h-16 px-4 pb-2 pt-4"
            disabled={isLoading}
            onChange={(event) => onChange(event.currentTarget.value)}
            placeholder={placeholder}
            value={value}
          />
        </PromptInputBody>

        <PromptInputFooter className="px-3 pb-3 pt-0">
          <PromptInputTools className="gap-1.5">
            <AttachmentDropdown
              uploadsEnabled={attachmentsEnabled}
              onFilesSelected={handleFilesSelected}
              onUnsupportedSelect={handleUnsupportedAttachmentSelect}
            />
            <ExecutionModeDropdown
              value={executionMode}
              onChange={onExecutionModeChange}
            />
            <RuntimeModeDropdown
              value={runtimeMode}
              onChange={onRuntimeModeChange}
            />
          </PromptInputTools>

          {isStreamingActive && onStop ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop generating"
              className={cn(
                "prompt-composer-submit-button flex aspect-square size-8 min-h-8 min-w-8 items-center justify-center rounded-full",
                "transition-[background-color,color,box-shadow,opacity]",
                "bg-foreground text-background hover:bg-foreground/80",
              )}
            >
              <Square className="size-3 fill-current" />
            </button>
          ) : (
            <PromptInputSubmit
              aria-label={isLoading ? "Sending message" : "Submit"}
              aria-busy={isReceiving}
              className={cn(
                "prompt-composer-submit-button aspect-square size-8 min-h-8 min-w-8 rounded-full first:rounded-full last:rounded-full",
                "transition-[background-color,color,box-shadow,opacity]",
              )}
              disabled={isLoading || !canSubmitMessage}
              size="icon-sm"
              variant="ghost"
            >
              {isLoading ? (
                <Spinner size="sm" />
              ) : (
                <ArrowUp className="size-4.5" />
              )}
            </PromptInputSubmit>
          )}
        </PromptInputFooter>
      </PromptInput>
    </div>
  );
}

export { WorkspaceComposer };
export type { AttachedFile };

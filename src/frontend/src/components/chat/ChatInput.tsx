import { useCallback, useEffect, useState } from "react";
import { nanoid } from "nanoid";
import { toast } from "sonner";

import { AttachmentChip, type AttachedFile } from "@/components/chat/input/AttachmentChip";
import { AttachmentDropdown } from "@/components/chat/input/AttachmentDropdown";
import { ExecutionModeDropdown } from "@/components/chat/input/ExecutionModeDropdown";
import { RuntimeModeDropdown } from "@/components/chat/input/RuntimeModeDropdown";
import { SendButton } from "@/components/chat/input/SendButton";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/prompt-kit/prompt-input";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/wsTypes";

interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (attachments: AttachedFile[]) => void;
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
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `attachment-${nanoid()}`;
}

function revokeAttachmentPreview(attachment: AttachedFile) {
  if (attachment.previewUrl) {
    URL.revokeObjectURL(attachment.previewUrl);
  }
}

function ChatInput({
  value,
  onChange,
  onSend,
  isLoading = false,
  isReceiving = false,
  attachmentsEnabled = true,
  runtimeMode,
  onRuntimeModeChange,
  executionMode,
  onExecutionModeChange,
  canSubmit = true,
  placeholder = "Ask anything…",
  className,
}: ChatInputProps) {
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);
  const hasContent = value.trim().length > 0;
  const canSubmitMessage = hasContent && canSubmit;

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
        previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
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
      description: "This backend currently does not accept binary upload payloads.",
    });
  }, []);

  const headerContent =
    attachments.length > 0 ? (
      <PromptInputHeader className="flex flex-col gap-3 px-1 pb-1 pt-1">
        <div className="flex flex-wrap gap-2">
          {attachments.map((attachment) => (
            <AttachmentChip
              key={attachment.id}
              attachment={attachment}
              onRemove={handleRemoveAttachment}
            />
          ))}
        </div>
      </PromptInputHeader>
    ) : null;

  return (
    <div className={className}>
      <PromptInput
        onSubmit={async () => {
          handleSubmit();
        }}
      >
        {headerContent}

        <PromptInputBody>
          <PromptInputTextarea
            aria-label="Message"
            className="min-h-10 px-2 pb-2 pt-3"
            disabled={isLoading}
            onChange={(event) => onChange(event.currentTarget.value)}
            placeholder={placeholder}
            value={value}
          />
        </PromptInputBody>

        <PromptInputFooter className="px-1 pb-1 pt-0">
          <PromptInputTools>
            <AttachmentDropdown
              uploadsEnabled={attachmentsEnabled}
              onFilesSelected={handleFilesSelected}
              onUnsupportedSelect={handleUnsupportedAttachmentSelect}
            />
            <ExecutionModeDropdown value={executionMode} onChange={onExecutionModeChange} />
            <RuntimeModeDropdown value={runtimeMode} onChange={onRuntimeModeChange} />
          </PromptInputTools>

          <SendButton
            disabled={isLoading || !canSubmitMessage}
            isLoading={isLoading}
            isReceiving={isReceiving}
          />
        </PromptInputFooter>
      </PromptInput>
    </div>
  );
}

export { ChatInput };
export type { AttachedFile };

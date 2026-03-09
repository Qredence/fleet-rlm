import { useCallback, useEffect, useState } from "react";
import { nanoid } from "nanoid";
import { toast } from "sonner";

import {
  AttachmentChip,
  type AttachedFile,
} from "@/components/chat/input/AttachmentChip";
import { AttachmentDropdown } from "@/components/chat/input/AttachmentDropdown";
import { ExecutionModeDropdown } from "@/components/chat/input/ExecutionModeDropdown";
import { SendButton } from "@/components/chat/input/SendButton";
import { SettingsDropdown } from "@/components/chat/input/SettingsDropdown";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input";
import type { WsExecutionMode } from "@/lib/rlm-api/wsTypes";

interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (attachments: AttachedFile[]) => void;
  isLoading?: boolean;
  isReceiving?: boolean;
  attachmentsEnabled?: boolean;
  executionMode: WsExecutionMode;
  onExecutionModeChange: (mode: WsExecutionMode) => void;
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

function ChatInput({
  value,
  onChange,
  onSend,
  isLoading = false,
  isReceiving = false,
  attachmentsEnabled = true,
  executionMode,
  onExecutionModeChange,
  placeholder = "Ask anything…",
  className,
}: ChatInputProps) {
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);
  const hasContent = value.trim().length > 0;

  useEffect(
    () => () => {
      attachments.forEach(revokeAttachmentPreview);
    },
    [attachments],
  );

  const handleFilesSelected = useCallback((files: File[]) => {
    const newAttachments: AttachedFile[] = files.map((file) => ({
      id: createAttachmentId(),
      file,
      previewUrl: file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined,
    }));

    setAttachments((prev) => [...prev, ...newAttachments]);
  }, []);

  const handleRemoveAttachment = useCallback((id: string) => {
    setAttachments((prev) => {
      const removed = prev.find((attachment) => attachment.id === id);
      if (removed) revokeAttachmentPreview(removed);
      return prev.filter((attachment) => attachment.id !== id);
    });
  }, []);

  const handleSubmit = useCallback(() => {
    const currentAttachments = [...attachments];
    currentAttachments.forEach(revokeAttachmentPreview);
    setAttachments([]);
    onSend(currentAttachments);
  }, [attachments, onSend]);

  const handleUnsupportedAttachmentSelect = useCallback(() => {
    toast.info("File upload is not available yet", {
      description:
        "This backend currently does not accept binary upload payloads.",
    });
  }, []);

  return (
    <PromptInput
      className={className}
      onSubmit={async () => {
        handleSubmit();
      }}
    >
      {attachments.length > 0 ? (
        <PromptInputHeader className="px-1 pb-1 pt-1">
          {attachments.map((attachment) => (
            <AttachmentChip
              key={attachment.id}
              attachment={attachment}
              onRemove={handleRemoveAttachment}
            />
          ))}
        </PromptInputHeader>
      ) : null}

      <PromptInputBody>
        <PromptInputTextarea
          aria-label="Message"
          className="min-h-12 px-2 pb-2.5 pt-3.5"
          disabled={isLoading}
          onChange={(event) => onChange(event.currentTarget.value)}
          placeholder={placeholder}
          value={value}
        />
      </PromptInputBody>

      <PromptInputFooter className="px-1 pb-1 pt-0">
        <PromptInputTools>
          <AttachmentDropdown
            onFilesSelected={handleFilesSelected}
            uploadsEnabled={attachmentsEnabled}
            onUnsupportedSelect={handleUnsupportedAttachmentSelect}
          />
          <SettingsDropdown />
        </PromptInputTools>

        <div className="flex items-center gap-1">
          <ExecutionModeDropdown
            value={executionMode}
            onChange={onExecutionModeChange}
          />

          <SendButton
            disabled={isLoading || !hasContent}
            isLoading={isLoading}
            isReceiving={isReceiving}
          />
        </div>
      </PromptInputFooter>
    </PromptInput>
  );
}

export { ChatInput };
export type { AttachedFile };

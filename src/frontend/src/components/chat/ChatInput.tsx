import { useCallback, useEffect, useState } from "react";

import { AgentDropdown } from "@/components/chat/input/AgentDropdown";
import {
  AttachmentChip,
  type AttachedFile,
} from "@/components/chat/input/AttachmentChip";
import { AttachmentDropdown } from "@/components/chat/input/AttachmentDropdown";
import { SendButton } from "@/components/chat/input/SendButton";
import { SettingsDropdown } from "@/components/chat/input/SettingsDropdown";
import { ThinkButton } from "@/components/chat/input/ThinkButton";
import {
  PromptInput,
  PromptInputActions,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/chat/prompt-input";

interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (attachments: AttachedFile[]) => void;
  isLoading?: boolean;
  selectedAgent: string;
  onAgentChange: (agentId: string) => void;
  thinkEnabled?: boolean;
  onThinkToggle?: () => void;
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
  return `attachment-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
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
  selectedAgent,
  onAgentChange,
  thinkEnabled = false,
  onThinkToggle,
  placeholder = "Ask anything…",
  className,
}: ChatInputProps) {
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);

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

  return (
    <PromptInput
      value={value}
      onChange={onChange}
      onSubmit={handleSubmit}
      isLoading={isLoading}
      className={className}
    >
      {attachments.length > 0 ? (
        <PromptInputHeader>
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
        <PromptInputTextarea placeholder={placeholder} />
      </PromptInputBody>

      <PromptInputFooter>
        <PromptInputTools>
          <AttachmentDropdown onFilesSelected={handleFilesSelected} />
          <SettingsDropdown />
        </PromptInputTools>

        <PromptInputActions>
          {onThinkToggle ? (
            <ThinkButton enabled={thinkEnabled} onToggle={onThinkToggle} />
          ) : null}

          <AgentDropdown
            selectedAgent={selectedAgent}
            onAgentChange={onAgentChange}
          />

          <SendButton />
        </PromptInputActions>
      </PromptInputFooter>
    </PromptInput>
  );
}

export { ChatInput };
export type { AttachedFile };

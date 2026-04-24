import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type ComponentType,
} from "react";
import {
  ArrowUp,
  AtSign,
  Brain,
  FileText,
  Paperclip,
  Sparkles,
  Square,
  Wrench,
  X,
} from "lucide-react";
import { nanoid } from "nanoid";
import { toast } from "sonner";

import {
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectGroup,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import type { WsExecutionMode } from "@/lib/rlm-api/ws-types";

const PROMPT_INPUT_ACTION_BUTTON_CLASSNAME =
  "prompt-composer-chip-button h-8 rounded-full border-transparent bg-transparent px-3 text-sm font-normal leading-5 tracking-tight-custom text-muted-foreground shadow-none hover:bg-foreground/6 hover:text-foreground active:bg-foreground/8 data-[popup-open]:bg-foreground/8 data-[popup-open]:text-foreground dark:bg-transparent dark:hover:bg-white/8 dark:active:bg-white/10 dark:data-[popup-open]:bg-white/10 focus-visible:ring-1 focus-visible:ring-ring/40";

const PROMPT_INPUT_ICON_BUTTON_CLASSNAME =
  "prompt-composer-icon-button size-8 min-h-8 min-w-8 rounded-full border-transparent p-0 text-muted-foreground shadow-none hover:bg-foreground/6 hover:text-foreground data-[state=open]:bg-foreground/8 data-[state=open]:text-foreground dark:bg-transparent dark:hover:bg-white/8 dark:data-[state=open]:bg-white/10";

const EXECUTION_MODE_OPTIONS = [
  { id: "auto", icon: Sparkles, label: "Auto" },
  { id: "rlm_only", icon: Brain, label: "RLM only" },
  { id: "tools_only", icon: Wrench, label: "Tools only" },
] as const satisfies ReadonlyArray<{
  id: WsExecutionMode;
  icon: ComponentType<{ className?: string }>;
  label: string;
}>;

export interface AttachedFile {
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
  executionMode: WsExecutionMode;
  onExecutionModeChange: (mode: WsExecutionMode) => void;
  canSubmit?: boolean;
  placeholder?: string;
  className?: string;
}

function PromptComposerAttachmentMenu({
  onAttachFiles,
  onUnsupportedSelect,
  uploadsEnabled = true,
}: {
  onAttachFiles?: () => void;
  onUnsupportedSelect?: () => void;
  uploadsEnabled?: boolean;
}) {
  return (
    <PromptInputActionMenu>
      <PromptInputActionMenuTrigger
        aria-label="Prompt features"
        className={PROMPT_INPUT_ICON_BUTTON_CLASSNAME}
        tooltip="Prompt features"
        variant="ghost"
      />
      <PromptInputActionMenuContent className="w-60">
        <PromptInputActionMenuItem
          onSelect={() => {
            if (!uploadsEnabled) {
              onUnsupportedSelect?.();
              return;
            }
            onAttachFiles?.();
          }}
        >
          <Paperclip />
          <span>
            {uploadsEnabled ? "Add images, PDFs or CSVs" : "Add images, PDFs or CSVs (coming soon)"}
          </span>
        </PromptInputActionMenuItem>
        <PromptInputActionMenuItem
          className="prompt-composer-menu-item-muted cursor-not-allowed opacity-70"
          disabled
        >
          <AtSign />
          <span>Add context (coming soon)</span>
        </PromptInputActionMenuItem>
      </PromptInputActionMenuContent>
    </PromptInputActionMenu>
  );
}

function ExecutionModeSelect({
  value,
  onChange,
}: {
  value: WsExecutionMode;
  onChange: (mode: WsExecutionMode) => void;
}) {
  const currentOption =
    EXECUTION_MODE_OPTIONS.find((option) => option.id === value) ?? EXECUTION_MODE_OPTIONS[0];

  return (
    <PromptInputSelect
      value={value}
      onValueChange={(nextValue) => onChange(nextValue as WsExecutionMode)}
    >
      <PromptInputSelectTrigger
        aria-label={`Execution mode: ${currentOption.label}`}
        className={cn(
          PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
          "w-auto min-w-0 justify-center gap-2 border-transparent shadow-none",
        )}
        size="sm"
      >
        <PromptInputSelectValue>{currentOption.label}</PromptInputSelectValue>
      </PromptInputSelectTrigger>
      <PromptInputSelectContent align="end" alignItemWithTrigger={false} className="w-44">
        <PromptInputSelectGroup>
          {EXECUTION_MODE_OPTIONS.map((option) => {
            const OptionIcon = option.icon;
            return (
              <PromptInputSelectItem key={option.id} value={option.id}>
                <OptionIcon className="shrink-0" />
                <span>{option.label}</span>
              </PromptInputSelectItem>
            );
          })}
        </PromptInputSelectGroup>
      </PromptInputSelectContent>
    </PromptInputSelect>
  );
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

function WorkspaceComposer({
  value,
  onChange,
  onSend,
  onStop,
  isLoading = false,
  isReceiving = false,
  attachmentsEnabled = true,
  executionMode,
  onExecutionModeChange,
  canSubmit = true,
  placeholder = "Ask, search or make anything...",
  className,
}: WorkspaceComposerProps) {
  const [attachments, setAttachments] = useState<AttachedFile[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
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
        previewUrl: file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined,
      }));
      setAttachments((prev) => [...prev, ...newAttachments]);
    }
  }, []);

  const handleAttachmentInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = event.currentTarget.files;
      if (files) {
        handleFilesSelected(files);
      }
      event.currentTarget.value = "";
    },
    [handleFilesSelected],
  );

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
                    className="size-5 shrink-0 rounded-full object-cover"
                  />
                ) : (
                  <FileText className="prompt-composer-attachment-chip-icon size-4 shrink-0" />
                )}

                <span className="truncate">{attachment.file.name}</span>

                <button
                  type="button"
                  onClick={() => handleRemoveAttachment(attachment.id)}
                  className="prompt-composer-attachment-chip-remove ml-auto flex size-5 shrink-0 items-center justify-center rounded-full"
                  aria-label={`Remove ${attachment.file.name}`}
                >
                  <X className="size-3" />
                </button>
              </div>
            );
          })}
        </div>
      </PromptInputHeader>
    ) : null;

  return (
    <div className={className}>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.pdf,.csv"
        className="hidden"
        onChange={handleAttachmentInputChange}
      />
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
            className="min-h-16 px-4 py-3"
            disabled={isLoading}
            onChange={(event) => onChange(event.currentTarget.value)}
            placeholder={placeholder}
            value={value}
          />
        </PromptInputBody>

        <PromptInputFooter className="px-3 pb-3 pt-0">
          <PromptInputTools className="gap-1.5">
            <PromptComposerAttachmentMenu
              onAttachFiles={() => fileInputRef.current?.click()}
              uploadsEnabled={attachmentsEnabled}
              onUnsupportedSelect={handleUnsupportedAttachmentSelect}
            />
            <ExecutionModeSelect value={executionMode} onChange={onExecutionModeChange} />
          </PromptInputTools>

          {isStreamingActive && onStop ? (
            <Button
              type="button"
              size="icon-sm"
              onClick={onStop}
              aria-label="Stop generating"
              className={cn(
                "prompt-composer-submit-button aspect-square size-8 rounded-full",
                "transition-[background-color,color,box-shadow,opacity]",
                "bg-foreground text-background hover:bg-foreground/80",
              )}
            >
              <Square className="size-3 fill-current" />
            </Button>
          ) : (
            <PromptInputSubmit
              aria-label={isLoading ? "Sending message" : "Submit"}
              aria-busy={isReceiving}
              className={cn(
                "prompt-composer-submit-button aspect-square size-8 rounded-full first:rounded-full last:rounded-full",
                "transition-[background-color,color,box-shadow,opacity]",
              )}
              disabled={isLoading || !canSubmitMessage}
              size="icon-sm"
              variant="ghost"
            >
              {isLoading ? <Spinner size="sm" /> : <ArrowUp className="size-4.5" />}
            </PromptInputSubmit>
          )}
        </PromptInputFooter>
      </PromptInput>
    </div>
  );
}

export { WorkspaceComposer };

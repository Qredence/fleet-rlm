import {
  type ChangeEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { nanoid } from "nanoid";
import {
  ArrowUp,
  Brain,
  ChevronDown,
  Code,
  FlaskConical,
  Globe,
  History,
  Laptop,
  Plus,
  Sparkles,
  Wand2,
  Wrench,
} from "lucide-react";
import { toast } from "sonner";

import {
  AttachmentChip,
  type AttachedFile,
} from "@/components/chat/input/AttachmentChip";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input";
import { Spinner } from "@/components/ui/spinner";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/wsTypes";
import { cn } from "@/lib/utils/cn";

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

const EXECUTION_MODE_LABELS: Record<WsExecutionMode, string> = {
  auto: "Auto",
  rlm_only: "RLM",
  tools_only: "ReAct",
};

const EXECUTION_MODE_ICONS: Record<WsExecutionMode, typeof Sparkles> = {
  auto: Sparkles,
  rlm_only: Brain,
  tools_only: Wrench,
};

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
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const hasContent = value.trim().length > 0;
  const canSubmitMessage = hasContent && canSubmit;

  useEffect(
    () => () => {
      attachments.forEach(revokeAttachmentPreview);
    },
    [attachments],
  );

  const handleFileChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      const newAttachments: AttachedFile[] = Array.from(files).map((file) => ({
        id: createAttachmentId(),
        file,
        previewUrl: file.type.startsWith("image/")
          ? URL.createObjectURL(file)
          : undefined,
      }));
      setAttachments((prev) => [...prev, ...newAttachments]);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
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

  const isDaytona = runtimeMode === "daytona_pilot";
  const runtimeLabel = isDaytona ? "Daytona" : "Modal";
  const CurrentExecIcon = EXECUTION_MODE_ICONS[executionMode];

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
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,.pdf,.csv"
        className="hidden"
        onChange={handleFileChange}
      />

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
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="prompt-composer-icon-button h-7 w-8 p-0 rounded-full border border-border"
                >
                  <Plus className="size-3" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="start"
                className="prompt-composer-menu max-w-xs rounded-xl p-1"
              >
                <DropdownMenuGroup className="space-y-1">
                  <DropdownMenuItem
                    className="prompt-composer-menu-item rounded-lg text-xs"
                    onSelect={() => {
                      if (attachmentsEnabled) {
                        fileInputRef.current?.click();
                      } else {
                        handleUnsupportedAttachmentSelect();
                      }
                    }}
                  >
                    <ArrowUp size={16} className="opacity-60" />
                    Attach Files
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="prompt-composer-menu-item rounded-lg text-xs"
                    disabled
                  >
                    <Code size={16} className="opacity-60" />
                    Add repository
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="prompt-composer-menu-item rounded-lg text-xs"
                    disabled
                  >
                    <Globe size={16} className="opacity-60" />
                    Web Search
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="prompt-composer-menu-item rounded-lg text-xs"
                    disabled
                  >
                    <History size={16} className="opacity-60" />
                    Chat History
                  </DropdownMenuItem>
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() =>
                onRuntimeModeChange(isDaytona ? "modal_chat" : "daytona_pilot")
              }
              className={cn(
                "h-7 px-2 rounded-full border border-border hover:bg-accent",
                isDaytona
                  ? "bg-foreground/8 text-foreground border-foreground/15"
                  : "text-muted-foreground",
              )}
            >
              <Wand2 className="size-3" />
              <span className="text-xs">{runtimeLabel}</span>
            </Button>
          </PromptInputTools>

          <div>
            <Button
              type="submit"
              disabled={isLoading || !canSubmitMessage}
              className="size-7 p-0 rounded-full bg-foreground/10 hover:bg-foreground/15 text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
              aria-label={isLoading ? "Sending message" : "Submit"}
              aria-busy={isReceiving}
            >
              {isLoading ? (
                <Spinner size="sm" />
              ) : (
                <ArrowUp className="size-3 text-foreground" />
              )}
            </Button>
          </div>
        </PromptInputFooter>
      </PromptInput>

      <div className="flex items-center gap-0 pt-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 rounded-full border border-transparent hover:bg-accent text-muted-foreground text-xs"
            >
              <Laptop className="size-3" />
              <span>{runtimeLabel}</span>
              <ChevronDown className="size-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="prompt-composer-menu max-w-xs rounded-2xl p-1.5"
          >
            <DropdownMenuGroup className="space-y-1">
              <DropdownMenuItem
                className={cn(
                  "prompt-composer-menu-item rounded-lg text-xs",
                  !isDaytona && "prompt-composer-menu-item-active",
                )}
                onSelect={() => onRuntimeModeChange("modal_chat")}
              >
                <Laptop size={16} className="opacity-60" />
                Modal
              </DropdownMenuItem>
              <DropdownMenuItem
                className={cn(
                  "prompt-composer-menu-item rounded-lg text-xs",
                  isDaytona && "prompt-composer-menu-item-active",
                )}
                onSelect={() => onRuntimeModeChange("daytona_pilot")}
              >
                <FlaskConical size={16} className="opacity-60" />
                Daytona
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 rounded-full border border-transparent hover:bg-accent text-muted-foreground text-xs"
            >
              <CurrentExecIcon className="size-3" />
              <span>{EXECUTION_MODE_LABELS[executionMode]}</span>
              <ChevronDown className="size-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="start"
            className="prompt-composer-menu max-w-xs rounded-2xl p-1.5"
          >
            <DropdownMenuGroup className="space-y-1">
              {(["auto", "rlm_only", "tools_only"] as const).map((mode) => {
                const Icon = EXECUTION_MODE_ICONS[mode];
                return (
                  <DropdownMenuItem
                    key={mode}
                    className={cn(
                      "prompt-composer-menu-item rounded-lg text-xs",
                      executionMode === mode &&
                        "prompt-composer-menu-item-active",
                    )}
                    onSelect={() => onExecutionModeChange(mode)}
                  >
                    <Icon size={16} className="opacity-60" />
                    {EXECUTION_MODE_LABELS[mode]}
                  </DropdownMenuItem>
                );
              })}
            </DropdownMenuGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        <div className="flex-1" />
      </div>
    </div>
  );
}

export { ChatInput };
export type { AttachedFile };

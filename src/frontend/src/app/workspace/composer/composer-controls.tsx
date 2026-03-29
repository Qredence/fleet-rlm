import {
  AtSign,
  Brain,
  Globe,
  MessagesSquare,
  Paperclip,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { ComponentType } from "react";

import {
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectGroup,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
} from "@/components/ai-elements/prompt-input";
import { cn } from "@/lib/utils";
import type { WsExecutionMode, WsRuntimeMode } from "@/lib/rlm-api/ws-types";

import {
  PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
  PROMPT_INPUT_ICON_BUTTON_CLASSNAME,
} from "./composer-action-styles";

interface ComposerSelectOption<T extends string> {
  id: T;
  icon: ComponentType<{ className?: string }>;
  label: string;
}

const EXECUTION_MODE_OPTIONS = [
  { id: "auto", icon: Sparkles, label: "Auto" },
  { id: "rlm_only", icon: Brain, label: "RLM only" },
  { id: "tools_only", icon: Wrench, label: "Tools only" },
] satisfies ComposerSelectOption<WsExecutionMode>[];

const RUNTIME_MODE_OPTIONS = [
  { id: "modal_chat", icon: MessagesSquare, label: "Modal chat" },
  { id: "daytona_pilot", icon: Globe, label: "Daytona" },
] satisfies ComposerSelectOption<WsRuntimeMode>[];

interface PromptComposerSelectProps<T extends string> {
  ariaLabel: string;
  contentClassName?: string;
  onChange: (value: T) => void;
  options: ComposerSelectOption<T>[];
  showCurrentIcon?: boolean;
  value: T;
}

function PromptComposerSelect<T extends string>({
  ariaLabel,
  contentClassName,
  onChange,
  options,
  showCurrentIcon = false,
  value,
}: PromptComposerSelectProps<T>) {
  const currentOption =
    options.find((option) => option.id === value) ?? options[0];

  if (!currentOption) {
    return null;
  }

  const CurrentIcon = currentOption.icon;

  return (
    <PromptInputSelect
      value={value}
      onValueChange={(nextValue) => onChange(nextValue as T)}
    >
      <PromptInputSelectTrigger
        aria-label={`${ariaLabel}: ${currentOption.label}`}
        className={cn(
          PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
          "w-auto min-w-0 justify-center gap-2 border-transparent shadow-none",
        )}
        size="sm"
      >
        {showCurrentIcon ? <CurrentIcon className="shrink-0" /> : null}
        <PromptInputSelectValue>{currentOption.label}</PromptInputSelectValue>
      </PromptInputSelectTrigger>
      <PromptInputSelectContent
        align="end"
        alignItemWithTrigger={false}
        className={contentClassName}
      >
        <PromptInputSelectGroup>
          {options.map((option) => {
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

interface PromptComposerAttachmentMenuProps {
  onAttachFiles?: () => void;
  onUnsupportedSelect?: () => void;
  uploadsEnabled?: boolean;
}

function PromptComposerAttachmentMenu({
  onAttachFiles,
  onUnsupportedSelect,
  uploadsEnabled = true,
}: PromptComposerAttachmentMenuProps) {
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
            {uploadsEnabled
              ? "Add images, PDFs or CSVs"
              : "Add images, PDFs or CSVs (coming soon)"}
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

interface ExecutionModeSelectProps {
  onChange: (mode: WsExecutionMode) => void;
  value: WsExecutionMode;
}

function ExecutionModeSelect({ value, onChange }: ExecutionModeSelectProps) {
  return (
    <PromptComposerSelect
      ariaLabel="Execution mode"
      contentClassName="w-44"
      onChange={onChange}
      options={EXECUTION_MODE_OPTIONS}
      value={value}
    />
  );
}

interface RuntimeModeSelectProps {
  onChange: (mode: WsRuntimeMode) => void;
  value: WsRuntimeMode;
}

function RuntimeModeSelect({ value, onChange }: RuntimeModeSelectProps) {
  return (
    <PromptComposerSelect
      ariaLabel="Runtime mode"
      contentClassName="w-48"
      onChange={onChange}
      options={RUNTIME_MODE_OPTIONS}
      showCurrentIcon
      value={value}
    />
  );
}

export {
  ExecutionModeSelect,
  PromptComposerAttachmentMenu,
  PromptComposerSelect,
  RuntimeModeSelect,
};

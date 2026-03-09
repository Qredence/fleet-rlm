/**
 * Chat Components
 *
 * Components for the chat interface including input controls and attachments.
 */

// Main input component
export { ChatInput } from "./ChatInput";

// Prompt input subdirectory
export {
  PromptInput,
  usePromptInput,
  PromptInputHeader,
  PromptInputBody,
  PromptInputTextarea,
  PromptInputFooter,
  PromptInputTools,
  PromptInputActions,
  PromptInputSubmit,
} from "./prompt-input";

// Input controls subdirectory
export { AgentDropdown } from "./input/AgentDropdown";
export { AttachmentChip, type AttachedFile } from "./input/AttachmentChip";
export { AttachmentDropdown } from "./input/AttachmentDropdown";
export { ExecutionModeDropdown } from "./input/ExecutionModeDropdown";
export { SendButton } from "./input/SendButton";
export { SettingsDropdown } from "./input/SettingsDropdown";
export { ThinkButton } from "./input/ThinkButton";
export {
  PROMPT_INPUT_ACTION_BUTTON_SIZE,
  PROMPT_INPUT_ACTION_BUTTON_CLASSNAME,
  PROMPT_INPUT_ICON_BUTTON_VARIANT,
  PROMPT_INPUT_ICON_BUTTON_CLASSNAME,
} from "./input/composerActionStyles";

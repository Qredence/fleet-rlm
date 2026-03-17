/**
 * Prompt kit components
 *
 * These components are local prompt-oriented wrappers around the Vercel AI SDK
 * element patterns. They preserve the repo's `data-slot` conventions and
 * compound component composition while exposing a project-specific namespace.
 *
 * Components use AI SDK types for seamless integration. Compatibility helpers
 * remain in place where the surrounding workspace UI expects stable APIs.
 */

// Message components
export {
  Message,
  MessageContent,
  MessageResponse,
  MessageActions,
  MessageAction,
  MessageBranch,
  MessageBranchContent,
  MessageBranchSelector,
  MessageBranchPrevious,
  MessageBranchNext,
  MessageBranchPage,
  MessageToolbar,
  type MessageProps,
} from "./message";

// Conversation components
export {
  Conversation,
  ConversationContent,
  ConversationDownload,
  ConversationEmptyState,
  ConversationScrollButton,
  type ConversationMessage,
  type ConversationProps,
} from "./conversation";

// Reasoning components
export {
  Reasoning,
  ReasoningTrigger,
  ReasoningContent,
  useReasoning,
  type ReasoningProps,
} from "./reasoning";

// Task components
export { Task, TaskTrigger, TaskContent, TaskItem, TaskItemFile, type TaskProps } from "./task";

// Tool components
export {
  Tool,
  ToolHeader,
  ToolContent,
  ToolInput,
  ToolOutput,
  getStatusBadge,
  type ToolPart,
  type ToolState,
  type ToolProps,
} from "./tool";

// Code block component
export { CodeBlock } from "./code-block";

// Shimmer components
export { Shimmer } from "./shimmer";

// Chain of Thought components
export {
  ChainOfThought,
  ChainOfThoughtHeader,
  ChainOfThoughtContent,
  ChainOfThoughtStep,
  ChainOfThoughtSearchResults,
  ChainOfThoughtSearchResult,
  ChainOfThoughtImage,
} from "./chain-of-thought";

// Attachments components
export {
  Attachments,
  Attachment,
  AttachmentPreview,
  AttachmentInfo,
  AttachmentRemove,
  AttachmentHoverCard,
  AttachmentHoverCardTrigger,
  AttachmentHoverCardContent,
  AttachmentEmpty,
} from "./attachments";

// Sources components
export { Sources, SourcesTrigger, SourcesContent, Source } from "./sources";

// Inline Citation components
export {
  InlineCitation,
  InlineCitationText,
  InlineCitationCard,
  InlineCitationCardTrigger,
  InlineCitationCardBody,
  InlineCitationCarousel,
  InlineCitationCarouselHeader,
  InlineCitationCarouselContent,
  InlineCitationCarouselItem,
  InlineCitationCarouselIndex,
  InlineCitationCarouselPrev,
  InlineCitationCarouselNext,
  InlineCitationSource,
  InlineCitationQuote,
} from "./inline-citation";

// Confirmation components
export {
  Confirmation,
  ConfirmationTitle,
  ConfirmationRequest,
  ConfirmationAccepted,
  ConfirmationRejected,
  ConfirmationActions,
  ConfirmationAction,
} from "./confirmation";

// Sandbox components
export {
  Sandbox,
  SandboxHeader,
  SandboxContent,
  SandboxTabs,
  SandboxTabsBar,
  SandboxTabsList,
  SandboxTabsTrigger,
  SandboxTabContent,
} from "./sandbox";

// Environment Variables components
export {
  EnvironmentVariables,
  EnvironmentVariablesHeader,
  EnvironmentVariablesTitle,
  EnvironmentVariablesToggle,
  EnvironmentVariablesContent,
  EnvironmentVariable,
  EnvironmentVariableGroup,
  EnvironmentVariableName,
  EnvironmentVariableValue,
  EnvironmentVariableCopyButton,
} from "./environment-variables";

// Suggestion components
export { Suggestions, Suggestion, type SuggestionsProps, type SuggestionProps } from "./suggestion";

// PromptInput components (re-exported from prompt-input module)
export {
  PromptInput,
  PromptInputProvider,
  PromptInputActionAddAttachments,
  PromptInputTextarea,
  PromptInputBody,
  PromptInputHeader,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputButton,
  PromptInputTools,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputSelect,
  PromptInputSelectContent,
  PromptInputSelectItem,
  PromptInputSelectTrigger,
  PromptInputSelectValue,
  PromptInputCommand,
  PromptInputCommandEmpty,
  PromptInputCommandGroup,
  PromptInputCommandInput,
  PromptInputCommandItem,
  PromptInputCommandList,
  PromptInputCommandSeparator,
  PromptInputHoverCard,
  PromptInputHoverCardContent,
  PromptInputHoverCardTrigger,
  PromptInputTab,
  PromptInputTabBody,
  PromptInputTabItem,
  PromptInputTabLabel,
  PromptInputTabsList,
  usePromptInputController,
  usePromptInputAttachments,
  usePromptInputReferencedSources,
  useProviderAttachments,
  LocalReferencedSourcesContext,
  type PromptInputProps,
  type PromptInputMessage,
  type PromptInputTextareaProps,
  type PromptInputProviderProps,
  type PromptInputControllerProps,
  type PromptInputActionAddAttachmentsProps,
} from "./prompt-input";

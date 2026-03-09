/**
 * AI Elements Components
 *
 * These components are the official Vercel ai-elements components installed
 * via `npx ai-elements add <component>`. They follow ai-elements patterns
 * with data-slot attributes and compound component composition.
 *
 * Note: Components use AI SDK types for seamless integration.
 * Custom wrapper components are provided for backward compatibility where needed.
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
export {
  Task,
  TaskTrigger,
  TaskContent,
  TaskItem,
  TaskItemFile,
  type TaskProps,
} from "./task";

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

// Code Block component (added by ai-elements)
export { CodeBlock } from "./code-block";

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

// Shimmer components
export { Shimmer } from "./shimmer";

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

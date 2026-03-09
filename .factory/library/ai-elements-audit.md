# AI Elements Component Audit

**Date:** 2026-03-08
**Auditor:** frontend-ai-elements-worker
**Milestone:** ai-elements-migration

## Executive Summary

All 13 local ai-elements components have official equivalents in the Vercel AI Elements registry. The audit found **0 truly custom components** — every component can be migrated to the official registry.

| Classification | Count |
|-----------------|-------|
| migrate-to-official | 13 |
| keep-custom | 0 |
| needs-refactor | 0 |

## Official Registry Components

The official Vercel AI Elements registry (`https://ai-sdk.dev/elements`) provides the following components organized by category:

### Chatbot (18 components)
- `attachments`
- `chain-of-thought`
- `checkpoint`
- `confirmation`
- `context`
- `conversation`
- `inline-citation`
- `message`
- `model-selector`
- `plan`
- `prompt-input`
- `queue`
- `reasoning`
- `shimmer`
- `sources`
- `suggestion`
- `task`
- `tool`

### Code (15 components)
- `agent`
- `artifact`
- `code-block`
- `commit`
- `environment-variables`
- `file-tree`
- `jsx-preview`
- `package-info`
- `sandbox`
- `schema-display`
- `snippet`
- `stack-trace`
- `terminal`
- `test-results`
- `web-preview`

### Voice (6 components)
- `audio-player`
- `mic-selector`
- `persona`
- `speech-input`
- `transcription`
- `voice-selector`

### Workflow (7 components)
- `canvas`
- `connection`
- `controls`
- `edge`
- `node`
- `panel`
- `toolbar`

### Utilities (2 components)
- `image`
- `open-in-chat`

## Local Components Audit

### 1. message.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `message` (Chatbot category)

**Local Implementation:**
- Exports: `Message`, `MessageContent`, `MessageResponse`, `MessageActions`, `MessageAction`
- Features: `from` prop (user/assistant/system), streaming support via `Streamdown`, data-slot attributes

**Official Features:**
- Exports: `Message`, `MessageContent`, `MessageResponse`, `MessageBranch`, `MessageBranchContent`, `MessageBranchNext`, `MessageBranchPage`, `MessageBranchPrevious`, `MessageBranchSelector`
- Full streaming integration with AI SDK hooks
- Branch/versioning support for message edits

**Migration Notes:**
- Replace local imports with official `npx ai-elements add message`
- Update consumers to use official component API
- `MessageResponse` in official version has better streaming integration
- Note: Official version includes `MessageBranch` for version history which local doesn't have

**Consumers:** `ChatMessageList.tsx`, `backendChatEventAdapter.ts`

---

### 2. conversation.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `conversation` (Chatbot category)

**Local Implementation:**
- Exports: `Conversation`, `ConversationContent`, `ConversationDownload`, `ConversationEmptyState`, `ConversationScrollButton`
- Features: Context for scroll state, download functionality, empty state with icon

**Official Features:**
- Exports: `Conversation`, `ConversationContent`, `ConversationScrollButton`
- Integrated scroll management with AI SDK
- Cleaner API surface

**Migration Notes:**
- `ConversationDownload` may need custom implementation (not in official)
- `ConversationEmptyState` - check if official has equivalent
- Official has simpler API, may need adaptation for download feature

**Consumers:** `ChatMessageList.tsx`

---

### 3. task.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `task` (Chatbot category)

**Local Implementation:**
- Exports: `Task`, `TaskTrigger`, `TaskContent`, `TaskItem`, `TaskItemFile`
- Features: Status icons (pending/in_progress/completed/error), density variants, disclosure control

**Official Features:**
- Same compound component pattern
- Status state management
- AI SDK integration

**Migration Notes:**
- Direct replacement possible
- API appears compatible
- `TaskItemFile` component is domain-specific - verify official has equivalent or keep custom

**Consumers:** Chat message rendering

---

### 4. tool.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `tool` (Chatbot category)

**Local Implementation:**
- Exports: `Tool`, `ToolHeader`, `ToolContent`, `ToolInput`, `ToolOutput`
- Features: Tool state management (input-streaming/running/output-available/output-error), density variants

**Official Features:**
- Same compound component structure
- Native AI SDK tool state integration
- Better type safety with SDK types

**Migration Notes:**
- Direct replacement possible
- Official version has better `ToolState` type integration with AI SDK
- Verify `density` prop compatibility

**Consumers:** `backendChatEventToolParts.ts`, `ChatMessageList.tsx`

---

### 5. reasoning.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `reasoning` (Chatbot category)

**Local Implementation:**
- Exports: `Reasoning`, `ReasoningTrigger`, `ReasoningContent`
- Features: Wraps `BaseReasoning` from `@/components/ui/reasoning`, `displayMode` (collapsible/inline_always), streaming support

**Official Features:**
- Exports: `Reasoning`, `ReasoningContent`, `ReasoningTrigger`
- Duration tracking, isThinking state
- Full AI SDK integration

**Migration Notes:**
- Local has custom `displayMode="inline_always"` - check if official supports this
- Local wraps a separate `ui/reasoning` component - migrate to official directly
- `density` prop should be compatible

**Consumers:** Chat message rendering

---

### 6. chain-of-thought.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `chain-of-thought` (Chatbot category)

**Local Implementation:**
- Exports: `ChainOfThought`, `ChainOfThoughtHeader`, `ChainOfThoughtContent`, `ChainOfThoughtStep`, `ChainOfThoughtSearchResults`, `ChainOfThoughtSearchResult`, `ChainOfThoughtImage`
- Features: Step status tracking, search result display, image embedding

**Official Features:**
- Step-based execution trace display
- Status indicators

**Migration Notes:**
- Verify all subcomponents exist in official version
- `ChainOfThoughtSearchResult`, `ChainOfThoughtImage` may be custom extensions
- May need to keep some custom components for search/image features

**Consumers:** Agent execution display

---

### 7. attachments.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `attachments` (Chatbot category)

**Local Implementation:**
- Exports: `Attachments`, `Attachment`, `AttachmentPreview`, `AttachmentInfo`, `AttachmentRemove`, `AttachmentHoverCard`, `AttachmentHoverCardTrigger`, `AttachmentHoverCardContent`, `AttachmentEmpty`
- Features: Grid/inline/list variants, media type detection, hover cards

**Official Features:**
- Rich media handling
- Preview capabilities
- AI SDK file attachment integration

**Migration Notes:**
- Extensive local implementation with many subcomponents
- Verify official covers all use cases
- `AttachmentHoverCard` pattern may need custom extension

**Consumers:** File upload/display in chat

---

### 8. confirmation.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `confirmation` (Chatbot category)

**Local Implementation:**
- Exports: `Confirmation`, `ConfirmationTitle`, `ConfirmationRequest`, `ConfirmationAccepted`, `ConfirmationRejected`, `ConfirmationActions`, `ConfirmationAction`
- Features: State-based rendering (approval-requested/approved/rejected), conditional display

**Official Features:**
- HITL (Human-in-the-loop) approval flows
- State management integrated with AI SDK

**Migration Notes:**
- API appears compatible
- `ConfirmationState` type matches official patterns
- Direct replacement possible

**Consumers:** HITL message cards

---

### 9. sources.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `sources` (Chatbot category)

**Local Implementation:**
- Exports: `Sources`, `SourcesTrigger`, `SourcesContent`, `Source`
- Features: URL sanitization, domain extraction, collapsible list

**Official Features:**
- Citation/source display
- Link management

**Migration Notes:**
- Direct replacement possible
- `safeHref` utility logic should be in official
- Simple API, likely 1:1 compatible

**Consumers:** Message source citations

---

### 10. inline-citation.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `inline-citation` (Chatbot category)

**Local Implementation:**
- Exports: `InlineCitation`, `InlineCitationText`, `InlineCitationCard`, `InlineCitationCardTrigger`, `InlineCitationCardBody`, `InlineCitationCarousel`, `InlineCitationCarouselHeader`, `InlineCitationCarouselContent`, `InlineCitationCarouselItem`, `InlineCitationCarouselIndex`, `InlineCitationCarouselPrev`, `InlineCitationCarouselNext`, `InlineCitationSource`, `InlineCitationQuote`
- Features: Popover-based citation cards, carousel navigation

**Official Features:**
- Inline reference display
- Card/popover patterns

**Migration Notes:**
- Extensive local implementation with carousel
- Carousel components (`InlineCitationCarousel*`) may be custom extensions
- Verify official has carousel support or keep custom

**Consumers:** Citation display in messages

---

### 11. shimmer.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `shimmer` (Chatbot category)

**Local Implementation:**
- Exports: `Shimmer`, `TextShimmerProps`
- Features: Motion-based text shimmer animation, configurable duration/spread

**Official Features:**
- Text shimmer/loading animation
- Motion integration

**Migration Notes:**
- Direct replacement possible
- Local uses `motion/react` - verify official uses same
- Props should be compatible

**Consumers:** Loading states

---

### 12. sandbox.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `sandbox` (Code category)

**Local Implementation:**
- Exports: `Sandbox`, `SandboxHeader`, `SandboxContent`, `SandboxTabs`, `SandboxTabsBar`, `SandboxTabsList`, `SandboxTabsTrigger`, `SandboxTabContent`
- Features: Code execution display, tabbed interface, density variants

**Official Features:**
- Code sandbox/execution environment display
- Terminal integration

**Migration Notes:**
- Local has extensive tab components (`SandboxTabs*`)
- Verify official includes tab functionality
- May need to combine with official `terminal` component

**Consumers:** Code execution display

---

### 13. environment-variables.tsx

**Classification:** `migrate-to-official`

**Official Equivalent:** `environment-variables` (Code category)

**Local Implementation:**
- Exports: `EnvironmentVariables`, `EnvironmentVariablesHeader`, `EnvironmentVariablesTitle`, `EnvironmentVariablesToggle`, `EnvironmentVariablesContent`, `EnvironmentVariable`, `EnvironmentVariableGroup`, `EnvironmentVariableName`, `EnvironmentVariableValue`, `EnvironmentVariableCopyButton`, `EnvironmentVariableRequired`
- Features: Value masking, copy functionality, show/hide toggle

**Official Features:**
- Environment variable display
- Masking and security features

**Migration Notes:**
- Comprehensive local implementation
- Verify official has all subcomponents
- `EnvironmentVariableCopyButton` may be custom extension

**Consumers:** Configuration display

---

## Migration Plan

### Phase 1: Simple Migrations (Low Risk)

These components have straightforward 1:1 mappings with official equivalents:

1. **shimmer.tsx** → `npx ai-elements add shimmer`
2. **sources.tsx** → `npx ai-elements add sources`
3. **confirmation.tsx** → `npx ai-elements add confirmation`

### Phase 2: Core Chat Components (Medium Risk)

These components are heavily used and require careful migration:

4. **message.tsx** → `npx ai-elements add message`
5. **conversation.tsx** → `npx ai-elements add conversation`
6. **reasoning.tsx** → `npx ai-elements add reasoning`

### Phase 3: Tool/Task Components (Medium Risk)

7. **task.tsx** → `npx ai-elements add task`
8. **tool.tsx** → `npx ai-elements add tool`

### Phase 4: Complex Components (Higher Risk)

These have many subcomponents and may need custom extensions:

9. **chain-of-thought.tsx** → `npx ai-elements add chain-of-thought`
10. **attachments.tsx** → `npx ai-elements add attachments`
11. **inline-citation.tsx** → `npx ai-elements add inline-citation`

### Phase 5: Code Components

12. **sandbox.tsx** → `npx ai-elements add sandbox`
13. **environment-variables.tsx** → `npx ai-elements add environment-variables`

## Import Path Updates

After migration, update imports from:

```typescript
// Before (local)
import { Message, MessageContent } from "@/components/ai-elements/message";

// After (official)
import { Message, MessageContent } from "@/components/ai-elements/message";
// (Same path - installed via CLI to same location)
```

The `npx ai-elements add <component>` command installs components to the path configured in `components.json`:
```json
{
  "aliases": {
    "components": "@/components"
  }
}
```

Components will be installed to `src/components/ai-elements/<component>.tsx`.

## Custom Extensions to Preserve

During migration, preserve these custom patterns if official doesn't support them:

1. **conversation.tsx**: `ConversationDownload` functionality
2. **chain-of-thought.tsx**: `ChainOfThoughtSearchResult`, `ChainOfThoughtImage`
3. **attachments.tsx**: `AttachmentHoverCard` pattern
4. **inline-citation.tsx**: Carousel navigation components
5. **reasoning.tsx**: `displayMode="inline_always"` variant

## Testing Strategy

After each migration:
1. Run `bun run type-check` - verify no TypeScript errors
2. Run `bun run lint` - verify no lint errors
3. Run `bun run test:unit` - verify unit tests pass
4. Manual testing of chat interface
5. Verify streaming behavior works correctly

## References

- Official Registry: https://ai-sdk.dev/elements
- Components.json Configuration: `src/frontend/components.json`
- Local Components Directory: `src/frontend/src/components/ai-elements/`

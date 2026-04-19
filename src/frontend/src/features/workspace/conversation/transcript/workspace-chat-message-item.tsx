import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/features/workspace/conversation/render-primitives";
import { Message, MessageContent, MessageResponse } from "@/components/ai-elements/message";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ai-elements/reasoning";
import { ClarificationCard } from "@/features/workspace/conversation/clarification-card";
import {
  ChatMessageLoadingState,
  WorkspaceLegacyStatusCard,
  WorkspaceTracePart,
} from "@/features/workspace/conversation/transcript/trace-part-renderers";
import type { ChatMessage } from "@/lib/workspace/workspace-types";
import { cn } from "@/lib/utils";
import { mapConfirmationState } from "@/lib/utils/prompt-kit-state";

interface WorkspaceChatMessageItemProps {
  message: ChatMessage;
  onResolveHitl: (msgId: string, label: string) => void;
  onResolveClarification: (msgId: string, answer: string) => void;
}

function hitlConfirmationState(
  message: ChatMessage,
): "approval-requested" | "approved" | "rejected" {
  const data = message.hitlData;
  if (!data?.resolved) return "approval-requested";
  const label = String(data.resolvedLabel ?? "").toLowerCase();
  if (/(reject|deny|decline|cancel|no)/.test(label)) return "rejected";
  return "approved";
}

export function WorkspaceChatMessageItem({
  message,
  onResolveHitl,
  onResolveClarification,
}: WorkspaceChatMessageItemProps) {
  const confirmationState = hitlConfirmationState(message);

  return (
    <>
      {message.type === "system" ? (
        <div className="flex items-center gap-4 py-4">
          <div className="h-[0.5px] flex-1 bg-border-strong opacity-20" />
          <span className="shrink-0 whitespace-pre-line text-xs font-medium uppercase tracking-wide text-muted-foreground/70">
            {message.content}
          </span>
          <div className="h-[0.5px] flex-1 bg-border-strong opacity-20" />
        </div>
      ) : null}

      {message.type === "user" ? (
        <Message from="user" className="mb-2.5">
          <MessageContent className="max-w-message rounded-xl border-subtle/80 bg-card/70 px-3.5 py-2.5 md:max-w-lg">
            <div className="whitespace-pre-wrap typo-body-sm leading-relaxed text-foreground">
              {message.content}
            </div>
          </MessageContent>
        </Message>
      ) : null}

      {message.type === "assistant" || message.type === "trace" || message.type === "reasoning" ? (
        <Message from="assistant" className="mb-2.5">
          <MessageContent className="w-full flex flex-col gap-2.5">
            {message.renderParts?.map((part, index) => (
              <WorkspaceTracePart
                key={`${message.id}-${part.kind}-${index}`}
                part={part}
                partKey={`${message.id}-${part.kind}-${index}`}
              />
            ))}
            {message.type === "assistant" && message.content ? (
              <div className="max-w-content rounded-bubble px-4 py-3.5 transition-colors md:px-5 md:py-4">
                <MessageResponse>{message.content}</MessageResponse>
              </div>
            ) : null}
            {message.type === "assistant" && message.streaming && !message.content ? (
              <div className="max-w-content rounded-bubble border border-border-subtle/60 bg-card/60 px-4 py-3.5 md:px-5 md:py-4">
                <ChatMessageLoadingState />
              </div>
            ) : null}
          </MessageContent>
        </Message>
      ) : null}

      {message.type === "hitl" && message.hitlData ? (
        <div className="mb-4">
          <Confirmation
            state={mapConfirmationState(confirmationState)}
            approval={{
              id: message.id,
              approved:
                confirmationState === "approved"
                  ? true
                  : confirmationState === "rejected"
                    ? false
                    : undefined,
            }}
          >
            <ConfirmationTitle className="text-sm font-medium">Checkpoint</ConfirmationTitle>
            <div className="mt-2 text-sm text-muted-foreground">{message.hitlData.question}</div>
            <ConfirmationRequest>
              <ConfirmationActions>
                {message.hitlData.actions.map((action, index) => (
                  <ConfirmationAction
                    key={`${message.id}-action-${index}`}
                    onClick={() => onResolveHitl(message.id, action.label)}
                    className={cn(
                      action.variant === "primary" &&
                        "border-primary bg-primary text-primary-foreground hover:bg-primary/90",
                    )}
                  >
                    {action.label}
                  </ConfirmationAction>
                ))}
              </ConfirmationActions>
            </ConfirmationRequest>
            <ConfirmationAccepted>
              <div className="mt-3 text-xs text-primary">
                Resolved: {message.hitlData.resolvedLabel ?? "Approved"}
              </div>
            </ConfirmationAccepted>
            <ConfirmationRejected>
              <div className="mt-3 text-xs text-destructive">
                Resolved: {message.hitlData.resolvedLabel ?? "Rejected"}
              </div>
            </ConfirmationRejected>
          </Confirmation>
        </div>
      ) : null}

      {message.type === "clarification" && message.clarificationData ? (
        <div className="mb-5">
          <ClarificationCard
            data={message.clarificationData}
            onResolve={(answer) => onResolveClarification(message.id, answer)}
          />
        </div>
      ) : null}

      {message.type === "reasoning" && message.reasoningData && !message.renderParts?.length ? (
        <div className="mb-2.5">
          <Reasoning
            isStreaming={message.reasoningData.isThinking}
            duration={message.reasoningData.duration}
          >
            <ReasoningTrigger />
            <ReasoningContent>
              {message.reasoningData.parts.map((part) => part.text).join("\n")}
            </ReasoningContent>
          </Reasoning>
        </div>
      ) : null}

      {message.type === "plan_update" ? (
        <WorkspaceLegacyStatusCard content={message.content} tone="accent" />
      ) : null}
      {message.type === "rlm_executing" ? (
        <WorkspaceLegacyStatusCard content={message.content} tone="primary" />
      ) : null}
      {message.type === "memory_update" ? (
        <WorkspaceLegacyStatusCard content={message.content} tone="success" />
      ) : null}
    </>
  );
}

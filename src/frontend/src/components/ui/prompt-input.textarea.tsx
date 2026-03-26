import type {
  ChangeEvent,
  ClipboardEventHandler,
  ComponentProps,
  KeyboardEventHandler,
  CompositionEvent,
} from "react";
import { useCallback, useState } from "react";

import {
  useOptionalPromptInputController,
  usePromptInputAttachments,
} from "@/components/ui/prompt-input.context";
import { InputGroupTextarea } from "@/components/ui/input-group";
import { cn } from "@/lib/utils";

export type PromptInputTextareaProps = ComponentProps<typeof InputGroupTextarea>;

export const PromptInputTextarea = ({
  onChange,
  onKeyDown,
  onPaste: userOnPaste,
  onCompositionEnd: userOnCompositionEnd,
  onCompositionStart: userOnCompositionStart,
  className,
  placeholder = "What would you like to know?",
  ...props
}: PromptInputTextareaProps) => {
  const controller = useOptionalPromptInputController();
  const attachments = usePromptInputAttachments();
  const [isComposing, setIsComposing] = useState(false);

  const handleKeyDown: KeyboardEventHandler<HTMLTextAreaElement> = useCallback(
    (event) => {
      onKeyDown?.(event);

      if (event.defaultPrevented) {
        return;
      }

      if (event.key === "Enter") {
        if (isComposing || event.nativeEvent.isComposing) {
          return;
        }
        if (event.shiftKey) {
          return;
        }
        event.preventDefault();

        const { form } = event.currentTarget;
        const submitButton = form?.querySelector(
          'button[type="submit"]',
        ) as HTMLButtonElement | null;
        if (submitButton?.disabled) {
          return;
        }

        form?.requestSubmit();
      }

      if (
        event.key === "Backspace" &&
        event.currentTarget.value === "" &&
        attachments.files.length > 0
      ) {
        event.preventDefault();
        const lastAttachment = attachments.files.at(-1);
        if (lastAttachment) {
          attachments.remove(lastAttachment.id);
        }
      }
    },
    [attachments, isComposing, onKeyDown],
  );

  const handlePaste: ClipboardEventHandler<HTMLTextAreaElement> = useCallback(
    (event) => {
      userOnPaste?.(event);

      if (event.defaultPrevented) {
        return;
      }

      const items = event.clipboardData?.items;

      if (!items) {
        return;
      }

      const files: File[] = [];

      for (const item of items) {
        if (item.kind === "file") {
          const file = item.getAsFile();
          if (file) {
            files.push(file);
          }
        }
      }

      if (files.length > 0) {
        event.preventDefault();
        attachments.add(files);
      }
    },
    [attachments, userOnPaste],
  );

  const handleCompositionEnd = useCallback(
    (event: CompositionEvent<HTMLTextAreaElement>) => {
      userOnCompositionEnd?.(event);
      setIsComposing(false);
    },
    [userOnCompositionEnd],
  );
  const handleCompositionStart = useCallback(
    (event: CompositionEvent<HTMLTextAreaElement>) => {
      userOnCompositionStart?.(event);
      setIsComposing(true);
    },
    [userOnCompositionStart],
  );

  const controlledProps = controller
    ? {
        onChange: (event: ChangeEvent<HTMLTextAreaElement>) => {
          controller.textInput.setInput(event.currentTarget.value);
          onChange?.(event);
        },
        value: controller.textInput.value,
      }
    : {
        onChange,
      };

  return (
    <InputGroupTextarea
      {...props}
      className={cn(
        "field-sizing-content min-h-12 w-full max-h-48 border-0 bg-transparent px-2 pb-2.5 pt-2",
        "prompt-composer-textarea outline-none ring-0 focus-visible:ring-0 focus-visible:outline-none",
        className,
      )}
      name="message"
      onCompositionEnd={handleCompositionEnd}
      onCompositionStart={handleCompositionStart}
      onKeyDown={handleKeyDown}
      onPaste={handlePaste}
      placeholder={placeholder}
      {...controlledProps}
    />
  );
};

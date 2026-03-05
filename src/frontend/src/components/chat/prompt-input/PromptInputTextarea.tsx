import { useEffect, type KeyboardEvent } from "react";

import { Textarea } from "@/components/ui/textarea";

import { usePromptInput } from "@/components/chat/prompt-input/PromptInput";

interface PromptInputTextareaProps {
  placeholder?: string;
}

function PromptInputTextarea({ placeholder }: PromptInputTextareaProps) {
  const { value, onChange, onSubmit, isLoading, textareaRef } =
    usePromptInput();

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, [value, textareaRef]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    e.preventDefault();
    if (!value.trim() || isLoading) return;
    onSubmit();
  };

  return (
    <Textarea
      ref={textareaRef}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={handleKeyDown}
      placeholder={placeholder}
      className="min-h-11 max-h-50 resize-none border-0 bg-transparent! dark:bg-transparent! disabled:bg-transparent! focus-visible:ring-0 focus-visible:ring-offset-0 py-3 px-4 text-foreground placeholder:text-muted-foreground"
      rows={1}
      disabled={isLoading}
      aria-label="Message input"
    />
  );
}

export { PromptInputTextarea };

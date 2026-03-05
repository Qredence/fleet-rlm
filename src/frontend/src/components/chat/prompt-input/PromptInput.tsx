import { useRef, type ReactNode } from "react";

import { cn } from "@/lib/utils/cn";
import { PromptInputContext } from "./PromptInputContext";

interface PromptInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  isLoading?: boolean;
  className?: string;
  children: ReactNode;
}

export function PromptInput({
  value,
  onChange,
  onSubmit,
  isLoading = false,
  className,
  children,
}: PromptInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  return (
    <PromptInputContext.Provider
      value={{ value, onChange, onSubmit, isLoading, textareaRef }}
    >
      <div className={cn("flex flex-col", className)}>{children}</div>
    </PromptInputContext.Provider>
  );
}

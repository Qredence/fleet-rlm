import {
  createContext,
  useContext,
  useRef,
  type ReactNode,
  type RefObject,
} from "react";

import { cn } from "@/components/ui/utils";

interface PromptInputContextValue {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  isLoading: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

const PromptInputContext = createContext<PromptInputContextValue | undefined>(
  undefined,
);

function usePromptInput(): PromptInputContextValue {
  const ctx = useContext(PromptInputContext);
  if (!ctx) {
    throw new Error("usePromptInput must be used within <PromptInput>");
  }
  return ctx;
}

interface PromptInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  isLoading?: boolean;
  className?: string;
  children: ReactNode;
}

function PromptInput({
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

export { PromptInput, usePromptInput };

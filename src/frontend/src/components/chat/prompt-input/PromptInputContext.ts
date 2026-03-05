import { createContext, type RefObject } from "react";

export interface PromptInputContextValue {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  isLoading: boolean;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}

export const PromptInputContext = createContext<
  PromptInputContextValue | undefined
>(undefined);

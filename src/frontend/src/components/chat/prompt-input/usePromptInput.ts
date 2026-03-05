import { useContext } from "react";

import {
  PromptInputContext,
  type PromptInputContextValue,
} from "./PromptInputContext";

export function usePromptInput(): PromptInputContextValue {
  const ctx = useContext(PromptInputContext);
  if (!ctx) {
    throw new Error("usePromptInput must be used within <PromptInput>");
  }
  return ctx;
}

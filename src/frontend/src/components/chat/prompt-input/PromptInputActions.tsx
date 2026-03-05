import type { ReactNode } from "react";

function PromptInputActions({ children }: { children: ReactNode }) {
  return <div className="flex items-center gap-2">{children}</div>;
}

export { PromptInputActions };

import type { ReactNode } from "react";

function PromptInputTools({ children }: { children: ReactNode }) {
  return <div className="flex items-center gap-1">{children}</div>;
}

export { PromptInputTools };

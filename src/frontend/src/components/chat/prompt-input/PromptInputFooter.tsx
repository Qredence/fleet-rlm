import type { ReactNode } from "react";

function PromptInputFooter({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center justify-between px-1 pb-0.5 pt-0.5">
      {children}
    </div>
  );
}

export { PromptInputFooter };

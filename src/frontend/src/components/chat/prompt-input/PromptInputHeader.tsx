import type { ReactNode } from "react";

function PromptInputHeader({ children }: { children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="flex items-center gap-2 px-3 pt-2.5 pb-0 overflow-x-auto">
      {children}
    </div>
  );
}

export { PromptInputHeader };

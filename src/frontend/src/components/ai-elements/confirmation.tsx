import {
  createContext,
  useContext,
  type ReactNode,
  type HTMLAttributes,
} from "react";
import { cn } from "@/lib/utils/cn";

export type ConfirmationState = "approval-requested" | "approved" | "rejected";

interface ConfirmationContextValue {
  state: ConfirmationState;
}

const ConfirmationContext = createContext<ConfirmationContextValue | null>(
  null,
);

interface ConfirmationProps extends HTMLAttributes<HTMLDivElement> {
  state: ConfirmationState;
  approval?: { id: string };
  children: ReactNode;
}

function Confirmation({
  state,
  children,
  className,
  ...props
}: ConfirmationProps) {
  return (
    <ConfirmationContext.Provider value={{ state }}>
      <div
        data-slot="confirmation"
        data-state={state}
        className={cn(
          "rounded-xl border-subtle bg-card p-4 shadow-sm",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    </ConfirmationContext.Provider>
  );
}

function ConfirmationTitle({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-slot="confirmation-title"
      className={cn("text-sm text-foreground", className)}
      {...props}
    />
  );
}

function ConditionalState({
  when,
  children,
}: {
  when: ConfirmationState;
  children: ReactNode;
}) {
  const ctx = useContext(ConfirmationContext);
  if (!ctx || ctx.state !== when) return null;
  return <>{children}</>;
}

function ConfirmationRequest({ children }: { children: ReactNode }) {
  return (
    <ConditionalState when="approval-requested">{children}</ConditionalState>
  );
}

function ConfirmationAccepted({ children }: { children: ReactNode }) {
  return <ConditionalState when="approved">{children}</ConditionalState>;
}

function ConfirmationRejected({ children }: { children: ReactNode }) {
  return <ConditionalState when="rejected">{children}</ConditionalState>;
}

function ConfirmationActions({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  const ctx = useContext(ConfirmationContext);
  if (!ctx || ctx.state !== "approval-requested") return null;
  return (
    <div
      data-slot="confirmation-actions"
      className={cn("mt-3 flex flex-wrap items-center gap-2", className)}
      {...props}
    />
  );
}

function ConfirmationAction({
  className,
  ...props
}: React.ComponentProps<"button">) {
  return (
    <button
      type="button"
      className={cn(
        "inline-flex items-center justify-center rounded-md border px-3 py-1.5 text-sm",
        "border-border-subtle bg-card hover:border-border-strong",
        className,
      )}
      {...props}
    />
  );
}

export {
  Confirmation,
  ConfirmationTitle,
  ConfirmationRequest,
  ConfirmationAccepted,
  ConfirmationRejected,
  ConfirmationActions,
  ConfirmationAction,
};

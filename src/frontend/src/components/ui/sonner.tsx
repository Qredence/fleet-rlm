import { Toaster as Sonner, type ToasterProps } from "sonner";

/**
 * Sonner toast provider.
 *
 * Detects light / dark from the Apps SDK `data-theme` attribute on `<html>`.
 * All visual tokens reference CSS variables so the user can
 * restyle toasts from the design system CSS alone.
 */
function Toaster(props: ToasterProps) {
  const isDark =
    typeof document !== "undefined" && document.documentElement.dataset.theme === "dark";

  return (
    <Sonner
      theme={isDark ? "dark" : "light"}
      className="toaster group [--normal-bg:var(--color-surface-elevated)] [--normal-border:var(--color-border)] [--normal-text:var(--color-text)] [--success-bg:var(--color-surface-elevated)] [--success-border:var(--color-border)] [--success-text:var(--color-text)] [--error-bg:var(--color-surface-elevated)] [--error-border:var(--color-border-danger-surface)] [--error-text:var(--color-text-danger)]"
      toastOptions={{
        classNames: {
          toast:
            "font-app text-[length:var(--font-text-sm-size)] font-medium leading-[var(--font-text-sm-line-height)] rounded-[var(--radius-lg)] [box-shadow:var(--shadow-300)]",
        },
      }}
      {...props}
    />
  );
}

export { Toaster };

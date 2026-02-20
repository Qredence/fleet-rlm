import type { CSSProperties } from "react";
import { Toaster as Sonner, type ToasterProps } from "sonner";

/**
 * Sonner toast provider.
 *
 * Detects light / dark from the `.dark` class on `<html>`.
 * All visual tokens reference CSS variables so the user can
 * restyle toasts from the design system CSS alone.
 */
function Toaster(props: ToasterProps) {
  const isDark =
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark");

  return (
    <Sonner
      theme={isDark ? "dark" : "light"}
      className="toaster group"
      toastOptions={{
        style: {
          fontFamily: "var(--font-family)",
          fontSize: "var(--text-label)",
          fontWeight: "var(--font-weight-medium)",
          lineHeight: "1.4",
          borderRadius: "var(--radius)",
          boxShadow: "var(--elevation-md)",
        },
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--success-bg": "var(--popover)",
          "--success-text": "var(--popover-foreground)",
          "--success-border": "var(--border)",
          "--error-bg": "var(--popover)",
          "--error-text": "var(--destructive)",
          "--error-border": "var(--border)",
        } as CSSProperties
      }
      {...props}
    />
  );
}

export { Toaster };

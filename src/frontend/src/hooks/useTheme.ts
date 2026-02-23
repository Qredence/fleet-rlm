import { useState, useEffect, useCallback } from "react";
import { telemetryClient } from "@/lib/telemetry/client";

export function useTheme() {
  const [isDark, setIsDark] = useState(() => {
    const saved = localStorage.getItem("theme");
    if (saved) return saved === "dark";
    return false;
  });

  useEffect(() => {
    const root = document.documentElement;
    if (isDark) {
      root.classList.add("dark");
      root.style.colorScheme = "dark";
    } else {
      root.classList.remove("dark");
      root.style.colorScheme = "light";
    }
  }, [isDark]);

  const toggle = useCallback(() => {
    document.documentElement.classList.add("theme-transition");
    setIsDark((prev) => {
      const next = !prev;
      localStorage.setItem("theme", next ? "dark" : "light");
      return next;
    });
    // Anonymous-only telemetry: capture theme toggle after state update.
    const wasDark = document.documentElement.classList.contains("dark");
    telemetryClient.capture("theme_toggled", {
      new_theme: wasDark ? "light" : "dark",
      previous_theme: wasDark ? "dark" : "light",
    });
    setTimeout(
      () => document.documentElement.classList.remove("theme-transition"),
      300,
    );
  }, []);

  return { isDark, toggle } as const;
}

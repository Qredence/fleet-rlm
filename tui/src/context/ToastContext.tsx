/**
 * Toast notification context for showing transient messages.
 */

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import type { Toast, ToastOptions, ToastType } from "../types/toast";

interface ToastContextValue {
  toasts: Toast[];
  showToast: (options: ToastOptions) => string;
  dismissToast: (id: string) => void;
  clearToasts: () => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return context;
}

interface ToastProviderProps {
  children: ReactNode;
  maxToasts?: number;
  defaultDuration?: number;
}

export function ToastProvider({
  children,
  maxToasts = 5,
  defaultDuration = 3000
}: ToastProviderProps) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const showToast = useCallback((options: ToastOptions): string => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const toast: Toast = {
      id,
      type: options.type,
      message: options.message,
      duration: options.duration ?? defaultDuration,
      timestamp: Date.now(),
    };

    setToasts(prev => {
      const updated = [...prev, toast];
      return updated.slice(-maxToasts);
    });

    return id;
  }, [maxToasts, defaultDuration]);

  const dismissToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const clearToasts = useCallback(() => {
    setToasts([]);
  }, []);

  return (
    <ToastContext.Provider value={{ toasts, showToast, dismissToast, clearToasts }}>
      {children}
    </ToastContext.Provider>
  );
}

export function useAutoDismissToast(toast: Toast, onDismiss: (id: string) => void) {
  useEffect(() => {
    if (toast.duration && toast.duration > 0) {
      const timer = setTimeout(() => {
        onDismiss(toast.id);
      }, toast.duration);

      return () => clearTimeout(timer);
    }
  }, [toast, onDismiss]);
}

export const toastIcons: Record<ToastType, string> = {
  success: "✓",
  error: "✗",
  warning: "⚠",
  info: "ℹ",
};

export const toastColors: Record<ToastType, string> = {
  success: "#73daca",
  error: "#f7768e",
  warning: "#e0af68",
  info: "#7aa2f7",
};

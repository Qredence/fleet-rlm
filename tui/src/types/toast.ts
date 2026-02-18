/**
 * Toast notification types and state.
 */

export type ToastType = "success" | "error" | "warning" | "info";

export interface Toast {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
  timestamp: number;
}

export interface ToastOptions {
  type: ToastType;
  message: string;
  duration?: number;
}

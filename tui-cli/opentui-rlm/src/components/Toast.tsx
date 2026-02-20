/**
 * Toast notification component for transient messages.
 */

import { memo } from "react";
import type { Toast as ToastType } from "../types/toast";
import { useAutoDismissToast, toastIcons, toastColors } from "../context/ToastContext";
import { bg, fg } from "../theme";

interface ToastItemProps {
  toast: ToastType;
  onDismiss: (id: string) => void;
}

function ToastItemInner({ toast, onDismiss }: ToastItemProps) {
  useAutoDismissToast(toast, onDismiss);

  const icon = toastIcons[toast.type];
  const color = toastColors[toast.type];

  return (
    <box
      paddingLeft={2}
      paddingRight={2}
      paddingTop={1}
      paddingBottom={1}
      backgroundColor={bg.elevated}
      border
      borderColor={color}
      flexDirection="row"
      gap={1}
    >
      <text fg={color}>{icon}</text>
      <text fg={fg.primary}>{toast.message}</text>
    </box>
  );
}

const ToastItem = memo(ToastItemInner);

interface ToastContainerProps {
  toasts: ToastType[];
  onDismiss: (id: string) => void;
}

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <box
      position="absolute"
      top={2}
      right={2}
      flexDirection="column"
      gap={1}
      zIndex={1000}
    >
      {toasts.map(toast => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </box>
  );
}

export default ToastContainer;

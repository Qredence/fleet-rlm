export function statusTone(status: "pending" | "running" | "completed" | "failed"): {
  label: string;
  variant: "secondary" | "warning" | "success" | "destructive-subtle";
} {
  switch (status) {
    case "pending":
      return { label: "Pending", variant: "secondary" };
    case "running":
      return { label: "Running", variant: "warning" };
    case "failed":
      return { label: "Failed", variant: "destructive-subtle" };
    default:
      return { label: "Completed", variant: "success" };
  }
}

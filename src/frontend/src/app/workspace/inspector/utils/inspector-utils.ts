export function statusTone(
  status: "pending" | "running" | "completed" | "failed",
): {
  label: string;
  variant: "secondary" | "default" | "destructive" | "outline";
} {
  switch (status) {
    case "pending":
      return { label: "Pending", variant: "secondary" };
    case "running":
      return { label: "Running", variant: "secondary" };
    case "failed":
      return { label: "Failed", variant: "destructive" };
    default:
      return { label: "Completed", variant: "default" };
  }
}

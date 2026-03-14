import type { RuntimeContext } from "@/lib/data/types";
import type {
  ExecutionSection,
  ToolSessionItem,
} from "@/features/rlm-workspace/assistant-content/types";

export function runtimeContextStrings(ctx?: RuntimeContext): string[] {
  if (!ctx) return [];
  const pills: string[] = [];
  if (ctx.depth > 0) pills.push(`depth ${ctx.depth}/${ctx.maxDepth}`);
  if (ctx.executionMode && ctx.executionMode !== "react") {
    pills.push(`mode ${ctx.executionMode}`);
  }
  if (ctx.sandboxActive) pills.push("sandbox");
  if (ctx.executionProfile !== "ROOT_INTERLOCUTOR") {
    pills.push(ctx.executionProfile.toLowerCase().replace(/_/g, " "));
  }
  if (ctx.volumeName) pills.push(ctx.volumeName);
  return pills;
}

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

export function stringifyValue(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function toolSessionItemState(
  item: ToolSessionItem,
): "pending" | "running" | "completed" | "failed" {
  if (item.part.kind === "tool" || item.part.kind === "sandbox") {
    if (item.part.errorText) return "failed";
    return item.part.state === "running" || item.part.state === "input-streaming"
      ? "running"
      : "completed";
  }
  if (item.part.kind === "status_note") {
    if (item.part.tone === "error") return "failed";
    if (item.part.tone === "warning") return "running";
  }
  return "completed";
}

export function executionSectionState(
  section: ExecutionSection,
): "pending" | "running" | "completed" | "failed" {
  if (section.kind === "tool_session") {
    const latest = section.session.items[section.session.items.length - 1];
    if (!latest) return "running";
    return toolSessionItemState(latest);
  }

  if (section.kind === "task") {
    if (section.part.status === "error") return "failed";
    if (section.part.status === "in_progress") return "running";
    if (section.part.status === "pending") return "pending";
    return "completed";
  }

  if (section.kind === "queue") {
    return section.part.items.every((item) => item.completed) ? "completed" : "running";
  }

  if (section.kind === "status_note") {
    if (section.part.tone === "error") return "failed";
    if (section.part.tone === "warning") return "running";
  }

  if ("errorText" in section.part && section.part.errorText) {
    return "failed";
  }
  if (
    "state" in section.part &&
    (section.part.state === "running" || section.part.state === "input-streaming")
  ) {
    return "running";
  }
  return "completed";
}

export function sectionGroups(sections: ExecutionSection[]) {
  return [
    {
      key: "tool-sessions",
      label: "Tool sessions",
      sections: sections.filter(
        (section) =>
          section.kind === "tool_session" ||
          section.kind === "tool" ||
          section.kind === "task" ||
          section.kind === "queue",
      ),
    },
    {
      key: "sandbox-runs",
      label: "Sandbox runs",
      sections: sections.filter((section) => section.kind === "sandbox"),
    },
    {
      key: "environment",
      label: "Environment",
      sections: sections.filter((section) => section.kind === "environment_variables"),
    },
    {
      key: "errors",
      label: "Errors / warnings",
      sections: sections.filter((section) => section.kind === "status_note"),
    },
  ].filter((group) => group.sections.length > 0);
}

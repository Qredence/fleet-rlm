import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vite-plus/test";
import {
  StatusBadge,
  StatusIndicator,
  StatusMessage,
  ExecutionProgress,
  STATUS_LABELS,
} from "@/components/product/execution-status";
import type { RunStatus } from "@/lib/workspace/workspace-types";

describe("StatusBadge", () => {
  const statuses: RunStatus[] = [
    "idle",
    "bootstrapping",
    "running",
    "completed",
    "error",
    "cancelled",
    "needs_human_review",
    "cancelling",
  ];

  it("renders all status variants with correct labels", () => {
    for (const status of statuses) {
      const html = renderToStaticMarkup(<StatusBadge status={status} />);
      expect(html).toContain(STATUS_LABELS[status]);
    }
  });

  it("renders with role=status for accessibility", () => {
    const html = renderToStaticMarkup(<StatusBadge status="running" />);
    expect(html).toContain('role="status"');
  });

  it("supports size variants", () => {
    const smHtml = renderToStaticMarkup(<StatusBadge status="idle" size="sm" />);
    const lgHtml = renderToStaticMarkup(<StatusBadge status="idle" size="lg" />);

    // Size sm uses smaller padding
    expect(smHtml).toContain("text-[11px]");
    // Size lg uses larger padding
    expect(lgHtml).toContain("text-sm");
  });

  it("can hide the icon", () => {
    const withIcon = renderToStaticMarkup(<StatusBadge status="running" showIcon />);
    const withoutIcon = renderToStaticMarkup(<StatusBadge status="running" showIcon={false} />);

    // SVG present when showIcon is true
    expect(withIcon).toContain("<svg");
    // No SVG when showIcon is false
    expect(withoutIcon).not.toContain("<svg");
  });

  it("applies animation to active states", () => {
    const runningHtml = renderToStaticMarkup(<StatusBadge status="running" />);
    const idleHtml = renderToStaticMarkup(<StatusBadge status="idle" />);

    expect(runningHtml).toContain("animate-spin");
    expect(idleHtml).not.toContain("animate-spin");
  });
});

describe("StatusIndicator", () => {
  it("renders as a small dot", () => {
    const html = renderToStaticMarkup(<StatusIndicator status="completed" />);
    expect(html).toContain("rounded-full");
    expect(html).toContain("size-2");
  });

  it("applies pulse animation to active states", () => {
    const runningHtml = renderToStaticMarkup(<StatusIndicator status="running" />);
    const completedHtml = renderToStaticMarkup(<StatusIndicator status="completed" />);

    expect(runningHtml).toContain("animate-pulse");
    expect(completedHtml).not.toContain("animate-pulse");
  });
});

describe("StatusMessage", () => {
  it("renders title and content", () => {
    const html = renderToStaticMarkup(
      <StatusMessage title="Test Title" variant="info">
        Test content here
      </StatusMessage>,
    );

    expect(html).toContain("Test Title");
    expect(html).toContain("Test content here");
  });

  it("applies variant-specific styling", () => {
    const successHtml = renderToStaticMarkup(
      <StatusMessage variant="success">Success</StatusMessage>,
    );
    const errorHtml = renderToStaticMarkup(<StatusMessage variant="error">Error</StatusMessage>);

    expect(successHtml).toContain("border-emerald-500");
    expect(errorHtml).toContain("border-destructive");
  });

  it("includes an icon by default", () => {
    const html = renderToStaticMarkup(
      <StatusMessage variant="warning">Warning message</StatusMessage>,
    );
    expect(html).toContain("<svg");
  });
});

describe("ExecutionProgress", () => {
  it("renders nothing for idle state", () => {
    const html = renderToStaticMarkup(<ExecutionProgress status="idle" />);
    expect(html).toBe("");
  });

  it("renders nothing for cancelled state", () => {
    const html = renderToStaticMarkup(<ExecutionProgress status="cancelled" />);
    expect(html).toBe("");
  });

  it("renders indeterminate animation for running state", () => {
    const html = renderToStaticMarkup(<ExecutionProgress status="running" />);
    expect(html).toContain("progressbar");
    expect(html).toContain("animate-progress-indeterminate");
  });

  it("renders full bar for completed state", () => {
    const html = renderToStaticMarkup(<ExecutionProgress status="completed" />);
    expect(html).toContain("w-full");
    expect(html).toContain("bg-emerald-500");
  });

  it("renders error styling for error state", () => {
    const html = renderToStaticMarkup(<ExecutionProgress status="error" />);
    expect(html).toContain("bg-destructive");
  });
});

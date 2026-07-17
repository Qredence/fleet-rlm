import { PassThrough } from "node:stream";

import { Box, Text, render, renderToString } from "ink";
import { describe, expect, it, vi } from "vitest";

import type { Message } from "./store.js";
import {
  initialTimelineScroll,
  reduceTimelineScroll,
  TimelineViewport,
} from "./timeline.js";
import { Code, Output, Reasoning } from "./views/index.js";

describe("timeline row scrolling", () => {
  it("bottom-anchors short and hydrated trajectories", () => {
    const short = reduceTimelineScroll(initialTimelineScroll(), {
      type: "metrics",
      contentHeight: 3,
      viewportHeight: 7,
    });
    expect(short).toEqual({ scrollTop: 0, maxScroll: 0, following: true });

    const hydrated = reduceTimelineScroll(
      reduceTimelineScroll(short, { type: "reset" }),
      { type: "metrics", contentHeight: 20, viewportHeight: 7 },
    );
    expect(hydrated).toEqual({ scrollTop: 13, maxScroll: 13, following: true });
  });

  it("freezes the viewed rows while scrolled and resumes following at the bottom", () => {
    let state = reduceTimelineScroll(initialTimelineScroll(), {
      type: "metrics",
      contentHeight: 20,
      viewportHeight: 7,
    });
    state = reduceTimelineScroll(state, { type: "page-up", viewportHeight: 7 });
    expect(state).toEqual({ scrollTop: 8, maxScroll: 13, following: false });

    state = reduceTimelineScroll(state, {
      type: "metrics",
      contentHeight: 25,
      viewportHeight: 7,
    });
    expect(state).toEqual({ scrollTop: 8, maxScroll: 18, following: false });

    state = reduceTimelineScroll(state, { type: "page-down", viewportHeight: 7 });
    expect(state).toEqual({ scrollTop: 13, maxScroll: 18, following: false });
    state = reduceTimelineScroll(state, { type: "end" });
    expect(state).toEqual({ scrollTop: 18, maxScroll: 18, following: true });
  });

  it("clamps after resize or collapse and exposes the whole trajectory by paging", () => {
    let state = reduceTimelineScroll(initialTimelineScroll(), {
      type: "metrics",
      contentHeight: 24,
      viewportHeight: 6,
    });
    expect(state).toMatchObject({ scrollTop: 18, maxScroll: 18 });

    while (state.scrollTop > 0) {
      state = reduceTimelineScroll(state, { type: "page-up", viewportHeight: 6 });
    }
    expect(state).toMatchObject({ scrollTop: 0, maxScroll: 18, following: false });

    state = reduceTimelineScroll(state, {
      type: "metrics",
      contentHeight: 5,
      viewportHeight: 6,
    });
    expect(state).toEqual({ scrollTop: 0, maxScroll: 0, following: true });
  });
});

describe("timeline viewport", () => {
  it("reports the viewport height after the surrounding layout shrinks it", async () => {
    const stdout = Object.assign(new PassThrough(), {
      columns: 60,
      rows: 8,
      isTTY: false,
    }) as unknown as NodeJS.WriteStream;
    const metrics: Array<[number, number]> = [];
    const instance = render(
      <Box flexDirection="column" height={8}>
        <TimelineViewport
          height={7}
          onMetrics={(contentHeight, viewportHeight) =>
            metrics.push([contentHeight, viewportHeight])
          }
        >
          <Text>{"one\ntwo\nthree\nfour\nfive\nsix\nseven"}</Text>
        </TimelineViewport>
        <Box height={3} flexShrink={0} />
      </Box>,
      { stdout, interactive: false, patchConsole: false },
    );

    try {
      await vi.waitFor(() => expect(metrics.at(-1)).toEqual([7, 5]));
    } finally {
      instance.unmount();
      await instance.waitUntilExit();
      instance.cleanup();
    }
  });

  it("can expose the oldest rows and the final answer from one complete trajectory", () => {
    const reasoning: Extract<Message, { kind: "reasoning" }> = {
      id: "reasoning-1",
      kind: "reasoning",
      runId: "run-1",
      step: 1,
      text: "Inspect the available evidence.",
      ts: 1,
    };
    const code: Extract<Message, { kind: "code" }> = {
      id: "code-1",
      kind: "code",
      runId: "run-1",
      step: 1,
      code: "value = inspect()\nprint(value)",
      ts: 2,
    };
    const output: Extract<Message, { kind: "output" }> = {
      id: "output-1",
      kind: "output",
      runId: "run-1",
      step: 1,
      output: "candidate evidence",
      ts: 3,
    };
    const trajectory = (
      <>
        <Reasoning message={reasoning} width={60} expanded />
        <Code message={code} width={60} expanded />
        <Output message={output} width={60} expanded />
        <Text>FINAL ANSWER</Text>
      </>
    );
    const complete = renderToString(
      <Box flexDirection="column" gap={1}>
        {trajectory}
      </Box>,
      { columns: 60 },
    );
    const viewportHeight = 5;
    const maxScroll = Math.max(0, complete.split("\n").length - viewportHeight);
    const oldest = renderToString(
      <TimelineViewport
        height={viewportHeight}
        scroll={{ scrollTop: 0, maxScroll, following: false }}
      >
        {trajectory}
      </TimelineViewport>,
      { columns: 60 },
    );
    const latest = renderToString(
      <TimelineViewport
        height={viewportHeight}
        scroll={{ scrollTop: maxScroll, maxScroll, following: true }}
      >
        {trajectory}
      </TimelineViewport>,
      { columns: 60 },
    );

    expect(complete.indexOf("REASONING")).toBeLessThan(complete.indexOf("CODE"));
    expect(complete.indexOf("CODE")).toBeLessThan(complete.indexOf("OUTPUT"));
    expect(complete.indexOf("OUTPUT")).toBeLessThan(complete.indexOf("FINAL ANSWER"));
    expect(oldest).toContain("REASONING");
    expect(oldest).not.toContain("FINAL ANSWER");
    expect(latest).not.toContain("REASONING");
    expect(latest).toContain("FINAL ANSWER");
  });

  it("anchors short timelines to the bottom with spacing between events", () => {
    const output = renderToString(
      <TimelineViewport height={7}>
        <Box><Text>first event</Text></Box>
        <Box><Text>second event</Text></Box>
      </TimelineViewport>,
      { columns: 60 },
    );
    const lines = output.split("\n");

    expect(lines.findIndex((line) => line.includes("first event"))).toBeGreaterThan(0);
    expect(lines.join("\n")).toContain("first event\n\nsecond event");
    expect(lines.at(-1)).toContain("second event");
  });
});

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vite-plus/test";
import { MessageResponse } from "@/components/ai-elements/message";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("MessageResponse", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("renders initial markdown content", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<MessageResponse>Hello world</MessageResponse>);
    });

    expect(container.textContent).toContain("Hello world");

    act(() => {
      root.unmount();
    });
  });

  it("accumulates streaming tokens without remounting (no key-based reset)", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    // Simulate token-by-token streaming: each render appends to the previous content.
    // MessageResponse must NOT remount between renders — that would destroy streaming state.
    act(() => {
      root.render(<MessageResponse>Hello</MessageResponse>);
    });

    const initialNode = container.firstChild;

    act(() => {
      root.render(<MessageResponse>Hello world</MessageResponse>);
    });

    // The component root node must be reused (not remounted) — no key-driven teardown.
    const updatedNode = container.firstChild;
    expect(updatedNode).toBe(initialNode);

    act(() => {
      root.unmount();
    });
  });
});

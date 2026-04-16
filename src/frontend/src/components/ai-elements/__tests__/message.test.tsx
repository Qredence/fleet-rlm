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

  it("updates rendered markdown immediately on rerender", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<MessageResponse>alpha</MessageResponse>);
    });

    expect(container.textContent).toContain("alpha");

    act(() => {
      root.render(<MessageResponse>omega</MessageResponse>);
    });

    expect(container.textContent).toContain("omega");
    expect(container.textContent).not.toContain("alpha");

    act(() => {
      root.unmount();
    });
  });
});

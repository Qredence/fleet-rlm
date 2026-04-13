import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import {
  ExecutionModeSelect,
  PromptComposerAttachmentMenu,
  RuntimeModeSelect,
} from "@/features/workspace/ui/composer/composer-controls";

let onValueChangeRef: ((value: string) => void) | undefined;
let currentValueRef: string | undefined;

const labelByValue: Record<string, string> = {
  auto: "Auto",
  daytona_pilot: "Daytona",
  rlm_only: "RLM only",
  tools_only: "Tools only",
};

vi.mock("@/components/ai-elements/prompt-input", () => ({
  PromptInputActionMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PromptInputActionMenuContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PromptInputActionMenuItem: ({
    children,
    onSelect,
  }: {
    children: ReactNode;
    onSelect?: () => void;
  }) => <button onClick={onSelect}>{children}</button>,
  PromptInputActionMenuTrigger: (props: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" {...props} />
  ),
  PromptInputSelect: ({
    children,
    onValueChange,
    value,
  }: {
    children: ReactNode;
    onValueChange?: (value: string) => void;
    value?: string;
  }) => {
    onValueChangeRef = onValueChange;
    currentValueRef = value;
    return <div>{children}</div>;
  },
  PromptInputSelectContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PromptInputSelectGroup: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  PromptInputSelectItem: ({ children, value }: { children: ReactNode; value: string }) => (
    <button type="button" role="menuitemradio" onClick={() => onValueChangeRef?.(value)}>
      {children}
    </button>
  ),
  PromptInputSelectTrigger: ({
    children,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
    children: ReactNode;
  }) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  PromptInputSelectValue: ({ children }: { children?: ReactNode }) => (
    <span>{children ?? (currentValueRef ? labelByValue[currentValueRef] : null)}</span>
  ),
}));

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

describe("composer-controls", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    onValueChangeRef = undefined;
    currentValueRef = undefined;
  });

  it("renders and updates execution mode selections", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onChange = vi.fn();

    act(() => {
      root.render(<ExecutionModeSelect value="auto" onChange={onChange} />);
    });

    expect(container.textContent).toContain("Auto");

    const toolsOnlyOption = Array.from(container.querySelectorAll('[role="menuitemradio"]')).find(
      (item) => item.textContent?.includes("Tools only") ?? false,
    );

    act(() => {
      toolsOnlyOption?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onChange).toHaveBeenCalledWith("tools_only");

    act(() => {
      root.unmount();
    });
  });

  it("renders and updates runtime mode selections", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onChange = vi.fn();

    act(() => {
      root.render(<RuntimeModeSelect value="daytona_pilot" onChange={onChange} />);
    });

    expect(container.textContent).toContain("Daytona");

    const daytonaOption = Array.from(container.querySelectorAll('[role="menuitemradio"]')).find(
      (item) => item.textContent?.includes("Daytona") ?? false,
    );

    act(() => {
      daytonaOption?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onChange).toHaveBeenCalledWith("daytona_pilot");

    act(() => {
      root.unmount();
    });
  });

  it("surfaces unsupported uploads immediately when binary upload is disabled", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onUnsupportedSelect = vi.fn();

    act(() => {
      root.render(
        <PromptComposerAttachmentMenu
          uploadsEnabled={false}
          onUnsupportedSelect={onUnsupportedSelect}
        />,
      );
    });

    const uploadButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent?.includes("Add images, PDFs or CSVs") ?? false,
    );

    expect(uploadButton?.textContent).toContain("(coming soon)");

    act(() => {
      uploadButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onUnsupportedSelect).toHaveBeenCalledOnce();

    act(() => {
      root.unmount();
    });
  });
});

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { PromptInputTextarea } from "@/components/ai-elements/prompt-input";
import {
  LocalAttachmentsContext,
  PromptInputController,
  type AttachmentsContext,
  type PromptInputControllerProps,
} from "@/components/ai-elements/prompt-input.context";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Root[] = [];

const createAttachmentsContext = (
  overrides: Partial<AttachmentsContext> = {},
): AttachmentsContext => ({
  add: vi.fn(),
  clear: vi.fn(),
  fileInputRef: { current: null },
  files: [],
  openFileDialog: vi.fn(),
  remove: vi.fn(),
  ...overrides,
});

const renderPromptInputTextarea = ({
  attachments = createAttachmentsContext(),
  controller,
  submitDisabled = false,
  onChange,
  value,
}: {
  attachments?: AttachmentsContext;
  controller?: PromptInputControllerProps;
  submitDisabled?: boolean;
  onChange?: (value: string) => void;
  value?: string;
} = {}) => {
  const container = document.createElement("div");
  document.body.appendChild(container);

  const root = createRoot(container);
  mountedRoots.push(root);

  const textarea = (
    <LocalAttachmentsContext.Provider value={attachments}>
      <form>
        <PromptInputTextarea
          aria-label="Message"
          onChange={(event) => onChange?.(event.currentTarget.value)}
          value={value}
        />
        <button disabled={submitDisabled} type="submit">
          Submit
        </button>
      </form>
    </LocalAttachmentsContext.Provider>
  );

  act(() => {
    root.render(
      controller ? (
        <PromptInputController.Provider value={controller}>
          {textarea}
        </PromptInputController.Provider>
      ) : (
        textarea
      ),
    );
  });

  const form = container.querySelector("form");
  const input = container.querySelector("textarea");

  if (!(form instanceof HTMLFormElement)) {
    throw new Error("Expected a form element.");
  }
  if (!(input instanceof HTMLTextAreaElement)) {
    throw new Error("Expected a textarea element.");
  }

  return { attachments, form, textarea: input };
};

afterEach(() => {
  while (mountedRoots.length > 0) {
    const root = mountedRoots.pop();
    if (root) {
      act(() => {
        root.unmount();
      });
    }
  }

  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("PromptInputTextarea", () => {
  it("re-exports the textarea through the public prompt-input barrel", () => {
    expect(PromptInputTextarea).toBeTypeOf("function");
  });

  it("requests submit on Enter when the submit button is enabled", () => {
    const { form, textarea } = renderPromptInputTextarea();
    const requestSubmit = vi.fn();

    Object.defineProperty(form, "requestSubmit", {
      configurable: true,
      value: requestSubmit,
    });

    act(() => {
      textarea.dispatchEvent(
        new KeyboardEvent("keydown", {
          bubbles: true,
          cancelable: true,
          key: "Enter",
        }),
      );
    });

    expect(requestSubmit).toHaveBeenCalledOnce();
  });

  it("does not submit on Enter while composing input", () => {
    const { form, textarea } = renderPromptInputTextarea();
    const requestSubmit = vi.fn();

    Object.defineProperty(form, "requestSubmit", {
      configurable: true,
      value: requestSubmit,
    });

    act(() => {
      textarea.dispatchEvent(new Event("compositionstart", { bubbles: true }));
    });

    act(() => {
      textarea.dispatchEvent(
        new KeyboardEvent("keydown", {
          bubbles: true,
          cancelable: true,
          key: "Enter",
        }),
      );
    });

    expect(requestSubmit).not.toHaveBeenCalled();
  });

  it("does not submit on Enter when the submit button is disabled", () => {
    const { form, textarea } = renderPromptInputTextarea({ submitDisabled: true });
    const requestSubmit = vi.fn();

    Object.defineProperty(form, "requestSubmit", {
      configurable: true,
      value: requestSubmit,
    });

    act(() => {
      textarea.dispatchEvent(
        new KeyboardEvent("keydown", {
          bubbles: true,
          cancelable: true,
          key: "Enter",
        }),
      );
    });

    expect(requestSubmit).not.toHaveBeenCalled();
  });

  it("removes the last attachment on Backspace when the textarea is empty", () => {
    const attachments = createAttachmentsContext({
      files: [
        {
          filename: "first.png",
          id: "file-1",
          mediaType: "image/png",
          type: "file",
          url: "blob:first",
        },
        {
          filename: "second.png",
          id: "file-2",
          mediaType: "image/png",
          type: "file",
          url: "blob:second",
        },
      ],
      remove: vi.fn(),
    });
    const { textarea } = renderPromptInputTextarea({ attachments });

    act(() => {
      textarea.dispatchEvent(
        new KeyboardEvent("keydown", {
          bubbles: true,
          cancelable: true,
          key: "Backspace",
        }),
      );
    });

    expect(attachments.remove).toHaveBeenCalledWith("file-2");
  });

  it("adds pasted files to the attachments context", () => {
    const file = new File(["image"], "diagram.png", { type: "image/png" });
    const attachments = createAttachmentsContext({ add: vi.fn() });
    const { textarea } = renderPromptInputTextarea({ attachments });
    const pasteEvent = new Event("paste", {
      bubbles: true,
      cancelable: true,
    }) as ClipboardEvent;

    Object.defineProperty(pasteEvent, "clipboardData", {
      configurable: true,
      value: {
        items: [
          {
            getAsFile: () => file,
            kind: "file",
          },
        ],
      },
    });

    act(() => {
      textarea.dispatchEvent(pasteEvent);
    });

    expect(attachments.add).toHaveBeenCalledWith([file]);
  });

  it("keeps the message field contract and syncs controller updates", () => {
    const attachments = createAttachmentsContext();
    const setInput = vi.fn();
    const onChange = vi.fn();
    const controller: PromptInputControllerProps = {
      __registerFileInput: vi.fn(),
      attachments,
      textInput: {
        clear: vi.fn(),
        setInput,
        value: "Provider controlled value",
      },
    };
    const { textarea } = renderPromptInputTextarea({
      attachments,
      controller,
      onChange,
    });

    expect(textarea.name).toBe("message");
    expect(textarea.value).toBe("Provider controlled value");

    act(() => {
      const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;

      setValue?.call(textarea, "Updated text");
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(setInput).toHaveBeenCalledWith("Updated text");
    expect(onChange).toHaveBeenCalledWith("Updated text");
  });
});

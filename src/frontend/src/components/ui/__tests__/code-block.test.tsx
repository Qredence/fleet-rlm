import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vite-plus/test";

import { CodeBlockCode } from "@/components/ui/code-block";

vi.mock("@/stores/theme-store", () => ({
  useIsDark: () => false,
}));

vi.mock("shiki", () => ({
  codeToHtml: vi.fn(),
}));

type Deferred<T> = {
  promise: Promise<T>;
  reject: (reason?: unknown) => void;
  resolve: (value: T | PromiseLike<T>) => void;
};

function createDeferred<T>(): Deferred<T> {
  let reject!: Deferred<T>["reject"];
  let resolve!: Deferred<T>["resolve"];
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, reject, resolve };
}

const mountedRoots = new Set<Root>();

function renderCode(code: string, language = "tsx") {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  mountedRoots.add(root);

  act(() => {
    root.render(<CodeBlockCode code={code} language={language} />);
  });

  return { container, root };
}

function unmount(root: Root) {
  act(() => root.unmount());
  mountedRoots.delete(root);
}

async function getCodeToHtmlMock() {
  const { codeToHtml } = await import("shiki");
  return vi.mocked(codeToHtml);
}

afterEach(() => {
  for (const root of mountedRoots) {
    act(() => root.unmount());
  }
  mountedRoots.clear();
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

describe("CodeBlockCode", () => {
  it("does not update React state when highlighting finishes after unmount", async () => {
    const deferred = createDeferred<string>();
    const codeToHtml = await getCodeToHtmlMock();
    codeToHtml.mockReturnValueOnce(deferred.promise);
    const { root } = renderCode("const value = 1;");

    await vi.waitFor(() => expect(codeToHtml).toHaveBeenCalledOnce());
    unmount(root);

    const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
    expect(windowDescriptor).toBeDefined();
    Reflect.deleteProperty(globalThis, "window");
    try {
      deferred.resolve('<pre data-highlight="late"><code>late</code></pre>');
      await deferred.promise;
      await Promise.resolve();
    } finally {
      Object.defineProperty(globalThis, "window", windowDescriptor!);
    }
  });

  it("ignores an older highlight that resolves after the latest code", async () => {
    const first = createDeferred<string>();
    const second = createDeferred<string>();
    const codeToHtml = await getCodeToHtmlMock();
    codeToHtml.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { container, root } = renderCode("const version = 'first';");

    await vi.waitFor(() => expect(codeToHtml).toHaveBeenCalledTimes(1));
    act(() => {
      root.render(<CodeBlockCode code="const version = 'second';" language="tsx" />);
    });
    await vi.waitFor(() => expect(codeToHtml).toHaveBeenCalledTimes(2));

    await act(async () => {
      second.resolve('<pre data-highlight="second"><code>second</code></pre>');
      await second.promise;
    });
    expect(container.querySelector('[data-highlight="second"]')).not.toBeNull();

    await act(async () => {
      first.resolve('<pre data-highlight="first"><code>first</code></pre>');
      await first.promise;
    });
    expect(container.querySelector('[data-highlight="second"]')).not.toBeNull();
    expect(container.querySelector('[data-highlight="first"]')).toBeNull();
  });

  it("keeps the plain-code fallback when Shiki rejects", async () => {
    const codeToHtml = await getCodeToHtmlMock();
    codeToHtml.mockRejectedValueOnce(new Error("Unsupported language"));
    const { container } = renderCode("plain fallback", "unsupported");

    await vi.waitFor(() => expect(codeToHtml).toHaveBeenCalledOnce());
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toBe("plain fallback");
    expect(container.querySelector("pre code")?.textContent).toBe("plain fallback");
  });

  it("replaces the plain-code fallback when Shiki resolves", async () => {
    const codeToHtml = await getCodeToHtmlMock();
    codeToHtml.mockResolvedValueOnce('<pre data-highlight="ready"><code>highlighted</code></pre>');
    const { container } = renderCode("const ready = true;");

    await vi.waitFor(() => {
      expect(container.querySelector('[data-highlight="ready"]')).not.toBeNull();
    });
    expect(container.textContent).toBe("highlighted");
  });
});

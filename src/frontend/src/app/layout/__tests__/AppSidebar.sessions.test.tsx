import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { AppSidebar } from "@/app/layout/AppSidebar";
import type { Conversation } from "@/stores/chatHistoryStore";
import { useChatHistoryStore } from "@/stores/chatHistoryStore";
import { useNavigationStore } from "@/stores/navigationStore";

(
  globalThis as typeof globalThis & {
    IS_REACT_ACT_ENVIRONMENT?: boolean;
  }
).IS_REACT_ACT_ENVIRONMENT = true;

const navigateToMock = vi.fn();
const navigateMock = vi.fn();
const locationState = {
  pathname: "/app/workspace",
};

vi.mock("lucide-react", () => {
  const Icon = () => <svg aria-hidden="true" />;
  return {
    Plus: Icon,
    Search: Icon,
    Settings: Icon,
    PanelLeftClose: Icon,
    PanelLeftOpen: Icon,
    Database: Icon,
    LogIn: Icon,
  };
});

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" className={className} {...props}>
      {children}
    </button>
  ),
}));

vi.mock("@/components/shared/QredenceLogo", () => ({
  QredenceLogo: () => <div>QredenceLogo</div>,
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateMock,
  useLocation: () => locationState,
}));

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({ navigateTo: navigateToMock }),
}));

function mountSidebar() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(<AppSidebar isCollapsed={false} onToggleCollapse={vi.fn()} />);
  });

  return { container, root };
}

function findButtonByText(container: HTMLElement, text: string) {
  return Array.from(container.querySelectorAll("button")).find((button) =>
    button.textContent?.includes(text),
  );
}

describe("AppSidebar session actions", () => {
  beforeEach(() => {
    navigateToMock.mockReset();
    navigateMock.mockReset();
    locationState.pathname = "/app/workspace";
    localStorage.clear();

    useChatHistoryStore.setState({ conversations: [] });
    useNavigationStore.setState({
      activeNav: "workspace",
      isCanvasOpen: false,
      selectedAssistantTurnId: null,
      activeInspectorTab: "trajectory",
      selectedFileNode: null,
      creationPhase: "idle",
      sessionId: 0,
      requestedConversationId: null,
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("starts a new workspace session from the sidebar", () => {
    const { container, root } = mountSidebar();

    const button = findButtonByText(container, "New Session");
    expect(button).toBeTruthy();

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(useNavigationStore.getState().sessionId).toBe(1);
    expect(useNavigationStore.getState().requestedConversationId).toBeNull();
    expect(navigateToMock).toHaveBeenCalledWith("workspace");

    act(() => {
      root.unmount();
    });
  });

  it("renders saved sessions and requests conversation loading when selected", () => {
    const conversation: Conversation = {
      id: "conv-1",
      title: "Saved conversation",
      messages: [
        {
          id: "assistant-1",
          type: "assistant",
          content: "Previously saved answer",
          streaming: false,
        },
      ],
      phase: "complete",
      createdAt: "2026-03-16T10:00:00.000Z",
      updatedAt: "2026-03-16T12:00:00.000Z",
    };

    useChatHistoryStore.setState({ conversations: [conversation] });

    const { container, root } = mountSidebar();

    expect(container.textContent).toContain("Saved conversation");

    const button = findButtonByText(container, "Saved conversation");
    expect(button).toBeTruthy();

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(useNavigationStore.getState().requestedConversationId).toBe("conv-1");
    expect(navigateToMock).toHaveBeenCalledWith("workspace");

    act(() => {
      root.unmount();
    });
  });
});

import { act } from "react";
import { createRoot } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vite-plus/test";

import { AppSidebar } from "@/screens/shell/app-sidebar";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import type { Conversation } from "@/screens/workspace/workspace-shell-contract";

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
let isMobile = false;
const workspaceShellState = {
  conversations: [] as Conversation[],
  newSession: vi.fn(),
  requestConversationLoad: vi.fn(),
  deleteConversation: vi.fn(),
  clearHistory: vi.fn(),
};

vi.mock("lucide-react", () => {
  const Icon = () => <svg aria-hidden="true" />;
  return {
    Plus: Icon,
    Search: Icon,
    Settings: Icon,
    Clock3: Icon,
    Trash2: Icon,
    PanelLeftIcon: Icon,
    XIcon: Icon,
    Database: Icon,
    LogIn: Icon,
    MessageCircle: Icon,
  };
});

vi.mock("@/components/ui/button", () => ({
  Button: ({
    children,
    className,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button type="button" className={className} {...props}>
      {children}
    </button>
  ),
}));

vi.mock("@/components/brand-mark", () => ({
  QredenceLogo: () => <div>QredenceLogo</div>,
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateMock,
  useLocation: () => locationState,
}));

vi.mock("@/hooks/useAppNavigate", () => ({
  useAppNavigate: () => ({ navigateTo: navigateToMock }),
}));

vi.mock("@/hooks/useIsMobile", () => ({
  useIsMobile: () => isMobile,
}));

vi.mock("@/screens/workspace/workspace-shell-contract", () => ({
  useWorkspaceShellHistory: () => workspaceShellState.conversations,
  useWorkspaceShellActions: () => ({
    newSession: workspaceShellState.newSession,
    requestConversationLoad: workspaceShellState.requestConversationLoad,
    deleteConversation: workspaceShellState.deleteConversation,
    clearHistory: workspaceShellState.clearHistory,
  }),
}));

function mountSidebar() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <SidebarProvider defaultOpen>
        <AppSidebar />
      </SidebarProvider>,
    );
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
    isMobile = false;
    locationState.pathname = "/app/workspace";
    workspaceShellState.conversations = [];
    workspaceShellState.newSession.mockReset();
    workspaceShellState.requestConversationLoad.mockReset();
    workspaceShellState.deleteConversation.mockReset();
    workspaceShellState.clearHistory.mockReset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("starts a new workspace session from the sidebar", () => {
    const { container, root } = mountSidebar();

    expect(findButtonByText(container, "Workbench")).toBeTruthy();

    const button = findButtonByText(container, "New session");
    expect(button).toBeTruthy();

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(workspaceShellState.newSession).toHaveBeenCalledOnce();
    expect(navigateToMock).toHaveBeenCalledWith("workspace");

    act(() => {
      root.unmount();
    });
  });

  it("renders an empty-state session hint in the left rail", () => {
    const { container, root } = mountSidebar();

    expect(container.textContent).toContain("Chats");
    expect(container.textContent).toContain("No chats yet.");
    expect(container.textContent).toContain(
      "Start a new session to populate this list.",
    );

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

    workspaceShellState.conversations = [conversation];

    const { container, root } = mountSidebar();

    expect(container.textContent).toContain("Saved conversation");

    const button = findButtonByText(container, "Saved conversation");
    expect(button).toBeTruthy();

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(workspaceShellState.requestConversationLoad).toHaveBeenCalledWith(
      "conv-1",
    );
    expect(navigateToMock).toHaveBeenCalledWith("workspace");

    act(() => {
      root.unmount();
    });
  });

  it("deletes a saved session from the consolidated sidebar", () => {
    const conversation: Conversation = {
      id: "conv-delete",
      title: "Sensitive conversation",
      messages: [],
      phase: "complete",
      createdAt: "2026-03-16T10:00:00.000Z",
      updatedAt: "2026-03-16T12:00:00.000Z",
    };

    workspaceShellState.conversations = [conversation];

    const { container, root } = mountSidebar();

    const deleteButton = Array.from(container.querySelectorAll("button")).find(
      (button) =>
        button
          .getAttribute("aria-label")
          ?.includes("Delete conversation: Sensitive conversation"),
    );

    expect(deleteButton).toBeTruthy();

    act(() => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(workspaceShellState.deleteConversation).toHaveBeenCalledWith(
      "conv-delete",
    );
    expect(workspaceShellState.requestConversationLoad).not.toHaveBeenCalled();

    act(() => {
      root.unmount();
    });
  });

  it("exposes saved sessions through the mobile sidebar trigger", () => {
    isMobile = true;
    workspaceShellState.conversations = [
      {
        id: "conv-mobile",
        title: "Mobile conversation",
        messages: [],
        phase: "complete",
        createdAt: "2020-03-16T10:00:00.000Z",
        updatedAt: "2020-03-16T12:00:00.000Z",
      },
    ];

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(
        <SidebarProvider defaultOpen>
          <SidebarTrigger />
          <AppSidebar />
        </SidebarProvider>,
      );
    });

    const trigger = container.querySelector("button");
    expect(trigger).toBeTruthy();

    act(() => {
      trigger?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(document.body.textContent).toContain("Mobile conversation");

    act(() => {
      root.unmount();
    });
  });
});

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vite-plus/test";

import { AppSidebar } from "@/features/layout/app-sidebar";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import type { Conversation } from "@/features/workspace";

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

vi.mock("@hugeicons/react", () => ({
  HugeiconsIcon: () => <svg aria-hidden="true" />,
}));

vi.mock("@hugeicons/core-free-icons", () => ({
  Cancel01Icon: [],
  ComputerIcon: [],
  Database01Icon: [],
  Delete01Icon: [],
  Login01Icon: [],
  PencilEdit02Icon: [],
  Search01Icon: [],
  Settings01Icon: [],
  SidebarLeft01Icon: [],
  SparklesIcon: [],
}));

vi.mock("lucide-react", () => {
  const Icon = () => <svg aria-hidden="true" />;
  return {
    Bell: Icon,
    Bot: Icon,
    Cpu: Icon,
    Database: Icon,
    FlaskConical: Icon,
    Info: Icon,
    Moon: Icon,
    Paintbrush: Icon,
    PanelLeftIcon: Icon,
    Plus: Icon,
    Search: Icon,
    Settings: Icon,
    Sparkles: Icon,
    Sun: Icon,
    Terminal: Icon,
    Trash2: Icon,
    XIcon: Icon,
    Zap: Icon,
    LogIn: Icon,
    MessageCircle: Icon,
    ChevronRightIcon: Icon,
  };
});

vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({ data: undefined, isLoading: false, isError: false }),
}));

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, className, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
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

vi.mock("@/hooks/use-app-navigate", () => ({
  useAppNavigate: () => ({ navigateTo: navigateToMock }),
}));

vi.mock("@/hooks/ui/use-is-mobile", () => ({
  useIsMobile: () => isMobile,
}));

const openCommandPaletteMock = vi.fn();

vi.mock("@/stores/navigation-store", () => ({
  useNavigationStore: () => ({
    openCommandPalette: openCommandPaletteMock,
  }),
}));

vi.mock("@/features/settings/screen/settings-content", () => {
  const Icon = () => <svg aria-hidden="true" />;
  const settingsSections = [
    { key: "appearance", label: "Appearance", icon: Icon },
    { key: "runtime", label: "Runtime", icon: Icon },
  ] as const;

  return {
    settingsSections,
    resolveSettingsSection: (section?: string) =>
      settingsSections.some((entry) => entry.key === section) ? section : undefined,
    getSettingsSectionTitle: (section?: string) =>
      settingsSections.find((entry) => entry.key === section)?.label ?? "Settings",
    getSettingsSectionDescription: () => "Settings description.",
    SettingsSectionContent: () => <div>Settings content</div>,
    SettingsSidebarNav: () => <nav>Settings sidebar nav</nav>,
  };
});

vi.mock("@/features/workspace", () => ({
  useWorkspaceLayoutHistory: () => workspaceShellState.conversations,
  useWorkspaceLayoutActions: () => ({
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
    openCommandPaletteMock.mockReset();
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

    const button = findButtonByText(container, "New Session");
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

  it("opens the optimization section from the sidebar", () => {
    const { container, root } = mountSidebar();

    const button = findButtonByText(container, "Optimization");
    expect(button).toBeTruthy();

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(navigateToMock).toHaveBeenCalledWith("optimization");

    act(() => {
      root.unmount();
    });
  });

  it("opens command palette search from the sidebar", () => {
    const { container, root } = mountSidebar();

    const button = findButtonByText(container, "Search sessions");
    expect(button).toBeTruthy();

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(openCommandPaletteMock).toHaveBeenCalledOnce();

    act(() => {
      root.unmount();
    });
  });

  it("renders supported navigation items without unsupported history", () => {
    const { container, root } = mountSidebar();

    expect(findButtonByText(container, "Workbench")).toBeTruthy();
    expect(findButtonByText(container, "Volumes")).toBeTruthy();
    expect(findButtonByText(container, "Optimization")).toBeTruthy();
    expect(findButtonByText(container, "History")).toBeUndefined();

    const volumesButton = findButtonByText(container, "Volumes");

    act(() => {
      volumesButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(navigateToMock).toHaveBeenCalledWith("volumes");

    act(() => {
      root.unmount();
    });
  });

  it("renders an empty-state session hint in the left rail", () => {
    const { container, root } = mountSidebar();

    expect(container.textContent).toContain("Sessions");
    expect(container.textContent).toContain("No chats yet.");
    expect(container.textContent).toContain("Start a new session to populate this list.");

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

    expect(workspaceShellState.requestConversationLoad).toHaveBeenCalledWith("conv-1");
    expect(navigateToMock).toHaveBeenCalledWith("workspace");

    act(() => {
      root.unmount();
    });
  });

  it("constrains long saved session titles to the sidebar width", () => {
    const conversation: Conversation = {
      id: "conv-long",
      title:
        "Review my recent code changes and suggest improvements analyze https://github.com/qredence/fleet-rlm with a very long title",
      messages: [],
      phase: "complete",
      createdAt: "2026-03-16T10:00:00.000Z",
      updatedAt: "2026-03-16T12:00:00.000Z",
    };

    workspaceShellState.conversations = [conversation];

    const { container, root } = mountSidebar();

    const button = findButtonByText(container, "Review my recent code changes");
    const label = button?.querySelector("span");

    expect(button?.className).toContain("overflow-hidden");
    expect(button?.className).toContain("max-w-full");
    expect(label?.className).toContain("truncate");

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

    const deleteButton = Array.from(container.querySelectorAll("button")).find((button) =>
      button.getAttribute("aria-label")?.includes("Delete conversation: Sensitive conversation"),
    );

    expect(deleteButton).toBeTruthy();

    act(() => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(workspaceShellState.deleteConversation).toHaveBeenCalledWith("conv-delete");
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

  it("opens the local settings dialog from the sidebar", () => {
    const { container, root } = mountSidebar();

    const button = findButtonByText(container, "Settings");
    expect(button).toBeTruthy();

    act(() => {
      button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(document.body.textContent).toContain("Settings content");

    act(() => {
      root.unmount();
    });
  });
});

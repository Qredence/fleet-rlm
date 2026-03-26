import { type MouseEvent, useMemo } from "react";
import {
  Clock3,
  Database,
  LogIn,
  MessageCircle,
  Plus,
  Search,
  Settings,
  Trash2,
} from "lucide-react";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { useNavigationStore } from "@/stores/navigationStore";

import { QredenceLogo } from "@/components/brand-mark";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { requestSettingsDialogOpen } from "@/screens/settings/settings-events";
import {
  type Conversation,
  useWorkspaceShellActions,
  useWorkspaceShellHistory,
} from "@/screens/workspace/workspace-shell-contract";

type TimeGroup = "Today" | "Yesterday" | "This Week" | "Older";

function relativeTime(isoDate: string): string {
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  const diff = now - then;

  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;

  const weeks = Math.floor(days / 7);
  if (weeks < 4) return `${weeks}w ago`;

  return new Date(isoDate).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function getTimeGroup(isoDate: string): TimeGroup {
  const now = new Date();
  const then = new Date(isoDate);
  const diffDays = Math.floor((now.getTime() - then.getTime()) / 86_400_000);

  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return "This Week";
  return "Older";
}

function groupConversations(
  conversations: Conversation[],
): Array<{ group: TimeGroup; items: Conversation[] }> {
  const order: TimeGroup[] = ["Today", "Yesterday", "This Week", "Older"];
  const grouped = new Map<TimeGroup, Conversation[]>();

  for (const conversation of conversations) {
    const group = getTimeGroup(conversation.updatedAt);
    if (!grouped.has(group)) {
      grouped.set(group, []);
    }
    grouped.get(group)?.push(conversation);
  }

  return order
    .filter((group) => grouped.has(group))
    .map((group) => ({ group, items: grouped.get(group) ?? [] }));
}

export function AppSidebar() {
  const conversations = useWorkspaceShellHistory();
  const { newSession, requestConversationLoad, deleteConversation, clearHistory } =
    useWorkspaceShellActions();
  const navigate = useNavigate();
  const { navigateTo } = useAppNavigate();
  const { openCommandPalette } = useNavigationStore();
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith("/app/workspace");
  const isVolumes = location.pathname.startsWith("/app/volumes");
  const groupedConversations = useMemo(() => groupConversations(conversations), [conversations]);

  const handleOpenSettings = (event: MouseEvent<HTMLButtonElement>) => {
    const wasHandledByDialog = requestSettingsDialogOpen({
      returnFocusTarget: event.currentTarget,
    });
    if (!wasHandledByDialog) {
      navigateTo("settings");
    }
  };

  const handleNewSession = () => {
    newSession();
    navigateTo("workspace");
  };

  const handleOpenConversation = (conversationId: string) => {
    requestConversationLoad(conversationId);
    navigateTo("workspace");
  };

  const handleDeleteConversation = (
    event: MouseEvent<HTMLButtonElement>,
    conversationId: string,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    deleteConversation(conversationId);
  };

  const handleClearHistory = () => {
    if (conversations.length === 0) return;
    if (!window.confirm("Delete all saved sessions?")) return;
    clearHistory();
  };

  const handleOpenLogin = (event: MouseEvent<HTMLButtonElement>) => {
    const openLoginEvent = new CustomEvent("open-login", {
      detail: { returnFocusTarget: event.currentTarget },
      cancelable: true,
    });
    const wasHandledByDialog = document.dispatchEvent(openLoginEvent) === false;
    if (!wasHandledByDialog) {
      navigate({ to: "/login" });
    }
  };

  return (
    <Sidebar variant="floating" collapsible="icon" className="border-0">
      <SidebarHeader className="gap-3 px-3 py-3">
        <div className="flex items-center gap-2 overflow-hidden px-2">
          <QredenceLogo className="size-4 shrink-0" />
          <div className="min-w-0 group-data-[collapsible=icon]:hidden">
            <span className="block truncate text-sm font-medium text-sidebar-foreground">
              Qredence
            </span>
            <span className="block truncate text-xs text-sidebar-foreground/65">
              Agentic workbench
            </span>
          </div>
        </div>
      </SidebarHeader>

      <SidebarContent className="overflow-hidden">
        <SidebarGroup className="pt-0">
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton onClick={handleNewSession} tooltip="New session">
                  <Plus />
                  <span>New Session</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton onClick={() => openCommandPalette()} tooltip="Search sessions">
                  <Search />
                  <span>Search sessions</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={isWorkspace}
                  onClick={() => navigateTo("workspace")}
                  tooltip="Workbench"
                >
                  <MessageCircle />
                  <span>Workbench</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={isVolumes}
                  onClick={() => navigateTo("volumes")}
                  tooltip="Volumes"
                >
                  <Database />
                  <span>Volumes</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator />

        <SidebarGroup className="min-h-0 flex-1 gap-2 overflow-hidden">
          <SidebarGroupLabel>Recent Sessions</SidebarGroupLabel>
          <SidebarGroupAction
            aria-label="Start a new session"
            title="Start a new session"
            onClick={handleNewSession}
          >
            <Plus />
          </SidebarGroupAction>
          <SidebarGroupContent className="relative min-h-0 flex-1 overflow-hidden">
            <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-4 bg-linear-to-b from-sidebar to-transparent group-data-[collapsible=icon]:hidden" />
            <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-5 bg-linear-to-t from-sidebar via-sidebar/95 to-transparent group-data-[collapsible=icon]:hidden" />
            <div className="no-scrollbar flex h-full min-h-0 flex-col overflow-y-auto overscroll-contain pr-1">
              <div className="flex items-start justify-between gap-3 px-2 pb-2 group-data-[collapsible=icon]:hidden">
                <div className="text-xs leading-5 text-sidebar-foreground/70">
                  Jump back into saved work from the same left rail you use for navigation.
                </div>
                {conversations.length > 0 ? (
                  <button
                    type="button"
                    onClick={handleClearHistory}
                    className="shrink-0 rounded-md px-2 py-1 text-[0.7rem] font-medium text-sidebar-foreground/65 transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
                  >
                    Clear all
                  </button>
                ) : null}
              </div>
              <SidebarMenu className="gap-1.5 pb-2">
                {conversations.length === 0 ? (
                  <SidebarMenuItem className="group-data-[collapsible=icon]:hidden">
                    <div className="rounded-xl border border-dashed border-sidebar-border/80 px-3 py-3 text-sm leading-5 text-sidebar-foreground/70">
                      <div className="font-medium text-sidebar-foreground">
                        No recent sessions yet
                      </div>
                      <div className="mt-1">
                        Start a new session and it will appear here for quick return.
                      </div>
                    </div>
                  </SidebarMenuItem>
                ) : (
                  groupedConversations.map(({ group, items }) => (
                    <li key={group} className="list-none">
                      <div className="px-2 pt-1 pb-1 text-[0.65rem] font-medium uppercase tracking-[0.14em] text-sidebar-foreground/55 group-data-[collapsible=icon]:hidden">
                        {group}
                      </div>
                      <SidebarMenu className="gap-1">
                        {items.map((session) => (
                          <SidebarMenuItem key={session.id}>
                            <SidebarMenuButton
                              onClick={() => handleOpenConversation(session.id)}
                              tooltip={session.title}
                              className="peer/menu-button h-auto min-h-12 items-start rounded-xl px-2.5 py-2.5 pr-10"
                            >
                              <Clock3 className="mt-0.5 size-4 shrink-0 text-sidebar-foreground/70" />
                              <div className="min-w-0 flex-1 group-data-[collapsible=icon]:hidden">
                                <div className="truncate text-sm font-medium text-sidebar-foreground">
                                  {session.title}
                                </div>
                                <div className="mt-1 truncate text-xs text-sidebar-foreground/60">
                                  {relativeTime(session.updatedAt)}
                                </div>
                              </div>
                            </SidebarMenuButton>
                            <SidebarMenuAction
                              aria-label={`Delete conversation: ${session.title}`}
                              title={`Delete conversation: ${session.title}`}
                              showOnHover
                              className="text-sidebar-foreground/60 hover:text-destructive"
                              onClick={(event) => handleDeleteConversation(event, session.id)}
                            >
                              <Trash2 />
                            </SidebarMenuAction>
                          </SidebarMenuItem>
                        ))}
                      </SidebarMenu>
                    </li>
                  ))
                )}
              </SidebarMenu>
            </div>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="px-3 py-3">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={handleOpenSettings} tooltip="Settings">
              <Settings />
              <span>Settings</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton onClick={handleOpenLogin} tooltip="Sign In">
              <LogIn />
              <span>Sign In</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  );
}

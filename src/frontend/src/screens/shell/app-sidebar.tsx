import { type MouseEvent } from "react";
import {
  Database,
  LogIn,
  MessageSquare,
  Plus,
  Search,
  Settings,
} from "lucide-react";
import { useLocation, useNavigate } from "@tanstack/react-router";

import { QredenceLogo } from "@/components/brand-mark";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import {
  useWorkspaceShellActions,
  useWorkspaceShellHistory,
} from "@/screens/workspace/workspace-shell-contract";

function formatSessionTimestamp(isoDate: string): string {
  const now = Date.now();
  const then = new Date(isoDate).getTime();
  const diffMinutes = Math.floor((now - then) / 60_000);

  if (diffMinutes < 1) return "Just now";
  if (diffMinutes < 60) return `${diffMinutes}m ago`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return `${diffDays}d ago`;

  return new Date(isoDate).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function AppSidebar() {
  const conversations = useWorkspaceShellHistory();
  const { newSession, requestConversationLoad } = useWorkspaceShellActions();
  const navigate = useNavigate();
  const { navigateTo } = useAppNavigate();
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith("/app/workspace");
  const isVolumes = location.pathname.startsWith("/app/volumes");

  const handleOpenSettings = () => {
    const openSettingsEvent = new CustomEvent("open-settings", {
      detail: { section: "general" },
      cancelable: true,
    });
    const wasHandledByDialog =
      document.dispatchEvent(openSettingsEvent) === false;
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
    <Sidebar collapsible="icon" className="border-r border-sidebar-border/80">
      <SidebarHeader className="gap-3 px-3 py-3">
        <div className="flex items-center gap-2 overflow-hidden px-2">
          <QredenceLogo className="h-5 w-auto shrink-0" />
          <span className="truncate text-sm font-medium text-sidebar-foreground">
            Qredence
          </span>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup className="pt-0">
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton
                  onClick={handleNewSession}
                  tooltip="New session"
                >
                  <Plus />
                  <span>New Session</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  isActive={isWorkspace}
                  onClick={() => navigateTo("workspace")}
                  tooltip="RLM Workspace"
                >
                  <MessageSquare />
                  <span>RLM Workspace</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton disabled tooltip="Search sessions">
                  <Search />
                  <span>Search sessions</span>
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

        <SidebarGroup className="min-h-0 flex-1">
          <SidebarGroupLabel>Sessions</SidebarGroupLabel>
          <SidebarGroupContent className="min-h-0">
            <SidebarMenu className="gap-1">
              {conversations.length === 0 ? (
                <div className="rounded-lg border border-dashed border-sidebar-border/80 px-3 py-3 text-xs text-sidebar-foreground/70 group-data-[collapsible=icon]:hidden">
                  Start a new session to build your recent history.
                </div>
              ) : (
                conversations.map((session) => (
                  <SidebarMenuItem key={session.id}>
                    <SidebarMenuButton
                      onClick={() => handleOpenConversation(session.id)}
                      tooltip={session.title}
                      className="h-auto items-start gap-2 py-2"
                    >
                      <MessageSquare />
                      <span className="flex min-w-0 flex-1 flex-col items-start">
                        <span className="truncate text-sm text-sidebar-foreground">
                          {session.title}
                        </span>
                        <span className="truncate text-xs text-sidebar-foreground/70 group-data-[collapsible=icon]:hidden">
                          {formatSessionTimestamp(session.updatedAt)}
                        </span>
                      </span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))
              )}
            </SidebarMenu>
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

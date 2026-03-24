import { type MouseEvent } from "react";
import {
  Database,
  LogIn,
  MessageCircle,
  Plus,
  Search,
  Settings,
} from "lucide-react";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { useNavigationStore } from "@/stores/navigationStore";

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
import { requestSettingsDialogOpen } from "@/screens/settings/settings-events";
import {
  useWorkspaceShellActions,
  useWorkspaceShellHistory,
} from "@/screens/workspace/workspace-shell-contract";

export function AppSidebar() {
  const conversations = useWorkspaceShellHistory();
  const { newSession, requestConversationLoad } = useWorkspaceShellActions();
  const navigate = useNavigate();
  const { navigateTo } = useAppNavigate();
  const { openCommandPalette } = useNavigationStore();
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith("/app/workspace");
  const isVolumes = location.pathname.startsWith("/app/volumes");

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
          <QredenceLogo className="size-4 shrink-0" />
          <span className="truncate text-sm font-medium text-sidebar-foreground">Qredence</span>
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
                      className="h-auto py-2"
                    >
                      <span className="truncate text-sm text-sidebar-foreground">
                        {session.title}
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

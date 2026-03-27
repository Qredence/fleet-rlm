import { type MouseEvent, useMemo } from "react";
import {
  Database,
  LogIn,
  MessageCircle,
  PanelLeftIcon,
  Plus,
  Search,
  Settings,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { useNavigationStore } from "@/stores/navigationStore";

import { QredenceLogo } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarSeparator,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import { requestSettingsDialogOpen } from "@/screens/settings/settings-events";
import {
  type Conversation,
  useWorkspaceShellActions,
  useWorkspaceShellHistory,
} from "@/screens/workspace/workspace-shell-contract";

const sidebarActionButtonClassName =
  "h-9 rounded-[10px] px-2.5 text-[14px] font-normal tracking-[-0.18px] text-sidebar-foreground/78 transition-colors hover:bg-sidebar-accent/75 hover:text-sidebar-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:font-normal data-[active=true]:text-sidebar-foreground [&>span]:truncate [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-sidebar-foreground/78";

const sessionButtonClassName =
  "peer/menu-button h-9 rounded-[10px] px-2.5 text-[14px] font-normal tracking-[-0.18px] text-sidebar-foreground/62 transition-colors hover:bg-sidebar-accent/70 hover:text-sidebar-foreground focus-visible:text-sidebar-foreground [&>span]:truncate";

function sortConversations(conversations: Conversation[]) {
  return [...conversations].sort(
    (left, right) =>
      new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
  );
}

function SidebarActionItem({
  label,
  icon: Icon,
  onClick,
  isActive = false,
}: {
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  isActive?: boolean;
}) {
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        tooltip={label}
        onClick={onClick}
        isActive={isActive}
        className={sidebarActionButtonClassName}
      >
        <Icon />
        <span>{label}</span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

export function AppSidebar() {
  const conversations = useWorkspaceShellHistory();
  const { toggleSidebar } = useSidebar();
  const { newSession, requestConversationLoad, deleteConversation } =
    useWorkspaceShellActions();
  const navigate = useNavigate();
  const { navigateTo } = useAppNavigate();
  const { openCommandPalette } = useNavigationStore();
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith("/app/workspace");
  const isVolumes = location.pathname.startsWith("/app/volumes");
  const sortedConversations = useMemo(
    () => sortConversations(conversations),
    [conversations],
  );

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
    <Sidebar
      variant="floating"
      collapsible="icon"
      className="border-0 !pr-0 [--sidebar-width:17.5rem] [--sidebar-width-icon:4rem] [&_[data-slot=sidebar-gap]]:w-[calc(var(--sidebar-width)-8px)] [&_[data-slot=sidebar-gap]]:group-data-[collapsible=icon]:w-[calc(var(--sidebar-width-icon)+1rem+2px)] [&_[data-slot=sidebar-inner]]:rounded-[16px] [&_[data-slot=sidebar-inner]]:border [&_[data-slot=sidebar-inner]]:border-sidebar-border/80 [&_[data-slot=sidebar-inner]]:bg-sidebar [&_[data-slot=sidebar-inner]]:ring-0 [&_[data-slot=sidebar-inner]]:shadow-none"
    >
      <SidebarHeader className="px-4 pt-4 pb-2">
        <div className="flex w-full items-center justify-between rounded-[10px]">
          <QredenceLogo className="ml-2.5 size-[18px] shrink-0 text-sidebar-foreground group-data-[collapsible=icon]:opacity-0" />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Toggle sidebar"
            onClick={toggleSidebar}
            className="pointer-events-none size-9 rounded-lg text-sidebar-foreground/75 opacity-0 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground group-data-[collapsible=icon]:pointer-events-auto group-data-[collapsible=icon]:opacity-100"
          >
            <PanelLeftIcon className="size-4" />
          </Button>
        </div>
      </SidebarHeader>

      <SidebarContent className="overflow-hidden px-2">
        <SidebarGroup className="pt-0">
          <SidebarGroupContent>
            <SidebarMenu className="gap-0.5">
              <SidebarActionItem
                label="New session"
                icon={Plus}
                onClick={handleNewSession}
              />
              <SidebarActionItem
                label="Search sessions"
                icon={Search}
                onClick={() => openCommandPalette()}
              />
              <SidebarActionItem
                label="Workbench"
                icon={MessageCircle}
                onClick={() => navigateTo("workspace")}
                isActive={isWorkspace}
              />
              <SidebarActionItem
                label="Volumes"
                icon={Database}
                onClick={() => navigateTo("volumes")}
                isActive={isVolumes}
              />
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator className="mx-2 mt-2 mb-1 bg-sidebar-border/70" />

        <SidebarGroup className="min-h-0 flex-1 gap-1 overflow-hidden pt-1 group-data-[collapsible=icon]:hidden">
          <SidebarGroupLabel className="h-auto px-2.5 pt-1 pb-2 text-[14px] font-normal normal-case tracking-[-0.18px] text-sidebar-foreground/58">
            Chats
          </SidebarGroupLabel>
          <SidebarGroupContent className="min-h-0 flex-1 overflow-hidden">
            <div className="no-scrollbar flex h-full min-h-0 flex-col overflow-y-auto overscroll-contain pr-1">
              <SidebarMenu className="gap-0.5 pb-2">
                {sortedConversations.length === 0 ? (
                  <SidebarMenuItem className="group-data-[collapsible=icon]:hidden">
                    <div className="px-2.5 py-2 text-sm leading-6 text-sidebar-foreground/45">
                      No chats yet. Start a new session to populate this list.
                    </div>
                  </SidebarMenuItem>
                ) : (
                  sortedConversations.map((session) => (
                    <SidebarMenuItem key={session.id}>
                      <SidebarMenuButton
                        onClick={() => handleOpenConversation(session.id)}
                        tooltip={session.title}
                        className={sessionButtonClassName}
                      >
                        <span>{session.title}</span>
                      </SidebarMenuButton>
                      <SidebarMenuAction
                        aria-label={`Delete conversation: ${session.title}`}
                        title={`Delete conversation: ${session.title}`}
                        showOnHover
                        className="right-2 text-sidebar-foreground/40 hover:bg-sidebar-accent/70 hover:text-destructive"
                        onClick={(event) =>
                          handleDeleteConversation(event, session.id)
                        }
                      >
                        <Trash2 />
                      </SidebarMenuAction>
                    </SidebarMenuItem>
                  ))
                )}
              </SidebarMenu>
            </div>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="px-2 pb-3 pt-2">
        <SidebarMenu className="gap-0.5">
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={handleOpenSettings}
              tooltip="Settings"
              className={sidebarActionButtonClassName}
            >
              <Settings />
              <span>Settings</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={handleOpenLogin}
              tooltip="Sign in"
              className={sidebarActionButtonClassName}
            >
              <LogIn />
              <span>Sign in</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}

import { type MouseEvent, useMemo } from "react";
import {
  Clock3,
  Database,
  LogIn,
  PanelLeftIcon,
  Plus,
  Search,
  Settings,
  Sparkles,
  Terminal,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { useNavigationStore } from "@/stores/navigation-store";

import { QredenceLogo } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
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
import { useAppNavigate } from "@/hooks/use-app-navigate";
import { requestSettingsDialogOpen } from "@/features/settings/settings-events";
import {
  type Conversation,
  useWorkspaceLayoutActions,
  useWorkspaceLayoutHistory,
} from "@/features/workspace/workspace-layout-contract";

const sidebarActionButtonClassName =
  "h-9 rounded-[10px] px-2.5 text-[14px] font-normal tracking-[-0.18px] text-sidebar-foreground/72 transition-[background-color,color,box-shadow] hover:bg-sidebar-accent/75 hover:text-sidebar-foreground data-[active=true]:bg-sidebar-accent/95 data-[active=true]:font-medium data-[active=true]:text-sidebar-foreground data-[active=true]:shadow-[inset_0_0_0_1px_color-mix(in_oklab,var(--sidebar-border)_78%,transparent)] group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:size-9! group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0! [&>span]:truncate [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-sidebar-foreground/72 data-[active=true]:[&>svg]:text-sidebar-foreground";

const sessionButtonClassName =
  "peer/menu-button h-9 rounded-[10px] px-2.5 text-[14px] font-normal tracking-[-0.18px] text-sidebar-foreground/62 transition-colors hover:bg-sidebar-accent/70 hover:text-sidebar-foreground focus-visible:text-sidebar-foreground group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:size-9! group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0! [&>span]:truncate";

function sortConversations(conversations: Conversation[]) {
  return [...conversations].sort(
    (left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime(),
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
  const conversations = useWorkspaceLayoutHistory();
  const { toggleSidebar, state: sidebarState } = useSidebar();
  const { newSession, requestConversationLoad, deleteConversation } = useWorkspaceLayoutActions();
  const navigate = useNavigate();
  const { navigateTo } = useAppNavigate();
  const { openCommandPalette } = useNavigationStore();
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith("/app/workspace");
  const isVolumes = location.pathname.startsWith("/app/volumes");
  const isOptimization = location.pathname.startsWith("/app/optimization");
  const isHistory = location.pathname.startsWith("/app/history");
  const isSettings = location.pathname.startsWith("/app/settings");
  const sortedConversations = useMemo(() => sortConversations(conversations), [conversations]);

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
      className="border-0 !pr-0 [&_[data-slot=sidebar-inner]]:rounded-[16px] [&_[data-slot=sidebar-inner]]:border [&_[data-slot=sidebar-inner]]:border-sidebar-border/80 [&_[data-slot=sidebar-inner]]:bg-sidebar [&_[data-slot=sidebar-inner]]:ring-0 [&_[data-slot=sidebar-inner]]:shadow-none"
    >
      <SidebarHeader className={cn("pt-4 pb-2", sidebarState === "collapsed" ? "px-2" : "px-4")}>
        <div
          className={cn(
            "flex w-full items-center rounded-[10px]",
            sidebarState === "collapsed" ? "justify-center" : "justify-between",
          )}
        >
          <QredenceLogo
            className={cn(
              "size-[18px] shrink-0 text-sidebar-foreground",
              sidebarState === "collapsed" ? "pointer-events-none absolute opacity-0" : "ml-2.5",
            )}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Toggle sidebar"
            onClick={toggleSidebar}
            className={cn(
              "size-9 rounded-lg text-sidebar-foreground/75 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground",
              sidebarState === "collapsed" ? "mx-auto" : "pointer-events-none opacity-0",
            )}
          >
            <PanelLeftIcon />
          </Button>
        </div>
      </SidebarHeader>

      <SidebarContent className="overflow-hidden px-1">
        <SidebarGroup className="px-1 pt-0">
          <SidebarGroupContent>
            <SidebarMenu className="gap-0.5">
              <SidebarActionItem label="New session" icon={Plus} onClick={handleNewSession} />
              <SidebarActionItem
                label="Search sessions"
                icon={Search}
                onClick={() => openCommandPalette()}
              />
              <SidebarActionItem
                label="Workbench"
                icon={Terminal}
                onClick={() => navigateTo("workspace")}
                isActive={isWorkspace}
              />
              <SidebarActionItem
                label="Volumes"
                icon={Database}
                onClick={() => navigateTo("volumes")}
                isActive={isVolumes}
              />
              <SidebarActionItem
                label="Optimization"
                icon={Sparkles}
                onClick={() => navigateTo("optimization")}
                isActive={isOptimization}
              />
              <SidebarActionItem
                label="History"
                icon={Clock3}
                onClick={() => navigateTo("history")}
                isActive={isHistory}
              />
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarSeparator className="mx-2 mt-2 mb-1 bg-sidebar-border/70" />

        <SidebarGroup className="min-h-0 flex-1 gap-1 overflow-hidden px-1 pt-1 group-data-[collapsible=icon]:hidden">
          <SidebarGroupLabel className="h-auto px-2.5 pt-1 pb-2 text-[14px] font-normal normal-case tracking-[-0.18px] text-sidebar-foreground/58">
            Chats
          </SidebarGroupLabel>
          <SidebarGroupContent className="min-h-0 flex-1 overflow-hidden">
            <div className="no-scrollbar flex h-full min-h-0 flex-col overflow-y-auto overscroll-contain pr-1">
              <SidebarMenu className="gap-0.5 pb-2">
                {sortedConversations.length === 0 ? (
                  <SidebarMenuItem className="group-data-[collapsible=icon]:hidden">
                    <div className="px-2 py-2 text-sm leading-6 text-sidebar-foreground/45">
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
                        onClick={(event) => handleDeleteConversation(event, session.id)}
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

      <SidebarFooter className="px-1 pb-3 pt-3">
        <div className={cn("rounded-[14px] p-1", sidebarState === "collapsed" && "hidden")}>
          <SidebarMenu className="gap-0.5">
            <SidebarMenuItem>
              <SidebarMenuButton
                tooltip="Sign in"
                onClick={handleOpenLogin}
                className={sidebarActionButtonClassName}
              >
                <LogIn />
                <span>Sign in</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton
                onClick={handleOpenSettings}
                tooltip="Settings"
                isActive={isSettings}
                className={sidebarActionButtonClassName}
              >
                <Settings />
                <span>Settings</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}

export { AppSidebar as LayoutSidebar };

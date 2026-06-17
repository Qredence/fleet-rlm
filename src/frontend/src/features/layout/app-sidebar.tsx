import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import {
  ComputerIcon,
  Database01Icon,
  Delete01Icon,
  Login01Icon,
  PencilEdit02Icon,
  Search01Icon,
  Settings01Icon,
  SidebarLeft01Icon,
  SparklesIcon,
} from "@hugeicons/core-free-icons";
import { type MouseEvent, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "@tanstack/react-router";

import { QredenceLogo } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  useSidebar,
} from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useAppNavigate } from "@/hooks/use-app-navigate";
import { cn } from "@/lib/utils";
import { useNavigationStore } from "@/stores/navigation-store";
import {
  type Conversation,
  useWorkspaceLayoutActions,
  useWorkspaceLayoutHistory,
} from "@/features/workspace/workspace-layout-contract";

import { SettingsDialog } from "./settings-dialog";

const sidebarActionButtonClassName =
  "group h-8 w-full justify-start rounded-lg px-1.5 text-sidebar-foreground/78 shadow-none transition-colors duration-0 hover:bg-sidebar-accent/80 hover:text-sidebar-foreground data-[active=true]:bg-sidebar-accent data-[active=true]:text-sidebar-foreground group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:size-8 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:[&>span]:hidden [&>span]:truncate";

function sortConversations(conversations: Conversation[]) {
  return [...conversations].sort((left, right) => {
    const leftTime = new Date(left.createdAt || left.updatedAt).getTime();
    const rightTime = new Date(right.createdAt || right.updatedAt).getTime();
    return rightTime - leftTime;
  });
}

function SidebarIcon({
  icon,
  className,
  size = 20,
}: {
  icon: IconSvgElement;
  className?: string;
  size?: number;
}) {
  return (
    <HugeiconsIcon
      icon={icon}
      size={size}
      strokeWidth={1.5}
      className={cn("min-w-5 text-sidebar-foreground/62", className)}
    />
  );
}

function SidebarActionItem({
  label,
  icon,
  onClick,
  isActive = false,
}: {
  label: string;
  icon: IconSvgElement;
  onClick: () => void;
  isActive?: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onClick}
          data-active={isActive}
          className={sidebarActionButtonClassName}
        >
          <SidebarIcon
            icon={icon}
            className="group-data-[active=true]/button:text-sidebar-foreground"
          />
          <span className="typo-label-regular tracking-tight-custom">{label}</span>
        </Button>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}

function SessionItem({
  session,
  onSelect,
  onDelete,
}: {
  session: Conversation;
  onSelect: (id: string) => void;
  onDelete: (event: MouseEvent<HTMLButtonElement>, session: Conversation) => void;
}) {
  const label = session.title || session.id;

  return (
    <div className="group/session relative w-full min-w-0 max-w-full overflow-hidden">
      <button
        type="button"
        onClick={() => onSelect(session.id)}
        className={cn(
          "inline-flex h-8 w-full min-w-0 max-w-full items-center justify-between overflow-hidden rounded-lg pl-1.5 pr-0.5 text-left text-sidebar-foreground transition-colors duration-0",
          "hover:bg-sidebar-accent/80 hover:text-sidebar-foreground",
        )}
      >
        <span className="min-w-0 flex-1 truncate typo-label-regular">{label}</span>
      </button>
      <button
        type="button"
        aria-label={`Delete conversation: ${label}`}
        title={`Delete conversation: ${label}`}
        onClick={(event) => onDelete(event, session)}
        className={cn(
          "absolute right-0.5 top-0.5 inline-flex size-7 items-center justify-center rounded-md text-sidebar-foreground/45 opacity-0 transition-opacity duration-0",
          "hover:bg-sidebar-accent hover:text-destructive group-hover/session:opacity-100 focus-visible:opacity-100",
        )}
      >
        <SidebarIcon icon={Delete01Icon} size={18} className="min-w-0" />
      </button>
    </div>
  );
}

function SidebarSessions({
  sessions,
  onSelect,
  onDelete,
}: {
  sessions: Conversation[];
  onSelect: (id: string) => void;
  onDelete: (event: MouseEvent<HTMLButtonElement>, session: Conversation) => void;
}) {
  return (
    <div className="flex h-full min-h-0 w-full min-w-0 max-w-full flex-1 flex-col overflow-hidden">
      <div className="ml-2 w-fit shrink-0 pl-1.5 typo-caption text-sidebar-foreground/58">
        Sessions
      </div>
      <div className="min-h-0 w-full min-w-0 max-w-full flex-1 overflow-hidden">
        <ScrollArea className="h-full w-full min-w-0 max-w-full [&_[data-radix-scroll-area-viewport]>div]:!block [&_[data-radix-scroll-area-viewport]>div]:!w-full [&_[data-radix-scroll-area-viewport]>div]:!min-w-0">
          <div className="flex w-full min-w-0 max-w-full flex-col gap-px overflow-hidden px-2 pb-2">
            {sessions.length === 0 ? (
              <div className="w-full min-w-0 max-w-full px-1.5 py-2 leading-6 text-sidebar-foreground/45 typo-caption">
                No chats yet. Start a new session to populate this list.
              </div>
            ) : (
              sessions.map((session) => (
                <SessionItem
                  key={session.id}
                  session={session}
                  onSelect={onSelect}
                  onDelete={onDelete}
                />
              ))
            )}
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}

export function AppSidebar() {
  const conversations = useWorkspaceLayoutHistory();
  const sortedConversations = useMemo(() => sortConversations(conversations), [conversations]);
  const { toggleSidebar, state: sidebarState } = useSidebar();
  const isCollapsed = sidebarState === "collapsed";
  const { newSession, requestConversationLoad, deleteConversation } = useWorkspaceLayoutActions();
  const navigate = useNavigate();
  const { navigateTo } = useAppNavigate();
  const { openCommandPalette } = useNavigationStore();
  const location = useLocation();
  const isWorkspace = location.pathname.startsWith("/app/workspace");
  const isVolumes = location.pathname.startsWith("/app/volumes");
  const isOptimization = location.pathname.startsWith("/app/optimization");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsReturnFocusRef = useRef<HTMLElement | null>(null);

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
    session: Conversation,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    deleteConversation(session.id);
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
    <>
      <Sidebar
        variant="sidebar"
        collapsible="icon"
        className="border-r border-transparent bg-sidebar text-sidebar-foreground"
      >
        <SidebarHeader className="flex h-12 shrink-0 justify-center gap-0 px-2 py-0">
          <div className="flex w-full items-center justify-between">
            {!isCollapsed ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleNewSession}
                className="min-w-0 flex-initial justify-start rounded-lg pl-1.5 text-sidebar-foreground hover:bg-sidebar-accent/80"
              >
                <QredenceLogo className="size-5 shrink-0 text-sidebar-foreground" />
                <span className="ml-2 truncate font-medium tracking-tight-custom">
                  Qredence Fleets
                </span>
              </Button>
            ) : null}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={toggleSidebar}
                  aria-label={isCollapsed ? "Open sidebar" : "Close sidebar"}
                  className={cn(
                    "size-8 rounded-lg text-sidebar-foreground/75 hover:bg-sidebar-accent/80 hover:text-sidebar-foreground",
                    isCollapsed && "mx-auto",
                  )}
                >
                  <SidebarIcon icon={SidebarLeft01Icon} className="min-w-0" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">
                {isCollapsed ? "Open Sidebar" : "Close Sidebar"}
              </TooltipContent>
            </Tooltip>
          </div>
        </SidebarHeader>

        <SidebarContent className="flex min-h-0 flex-1 overflow-hidden px-2">
          <div className="mb-4 flex shrink-0 flex-col gap-px">
            <SidebarActionItem
              label="New Session"
              icon={PencilEdit02Icon}
              onClick={handleNewSession}
            />
            <SidebarActionItem
              label="Search sessions"
              icon={Search01Icon}
              onClick={() => openCommandPalette()}
            />
            <SidebarActionItem
              label="Workbench"
              icon={ComputerIcon}
              onClick={() => navigateTo("workspace")}
              isActive={isWorkspace}
            />
            <SidebarActionItem
              label="Volumes"
              icon={Database01Icon}
              onClick={() => navigateTo("volumes")}
              isActive={isVolumes}
            />
            <SidebarActionItem
              label="Optimization"
              icon={SparklesIcon}
              onClick={() => navigateTo("optimization")}
              isActive={isOptimization}
            />
          </div>

          <div className="min-h-0 flex-1 overflow-hidden group-data-[collapsible=icon]:hidden">
            <SidebarSessions
              sessions={sortedConversations}
              onSelect={handleOpenConversation}
              onDelete={handleDeleteConversation}
            />
          </div>
        </SidebarContent>

        <SidebarFooter className="border-t border-transparent px-2 py-3">
          <div className="flex flex-col gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={handleOpenLogin}
                  title={isCollapsed ? "Sign in" : undefined}
                  className={sidebarActionButtonClassName}
                >
                  <SidebarIcon icon={Login01Icon} />
                  <span className="typo-label-regular tracking-tight-custom">Sign in</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">Sign in</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={(event) => {
                    settingsReturnFocusRef.current = event.currentTarget;
                    setSettingsOpen(true);
                  }}
                  title={isCollapsed ? "Settings" : undefined}
                  className={sidebarActionButtonClassName}
                >
                  <SidebarIcon icon={Settings01Icon} />
                  <span className="typo-label-regular tracking-tight-custom">Settings</span>
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">Settings</TooltipContent>
            </Tooltip>
          </div>
        </SidebarFooter>
      </Sidebar>

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        returnFocusRef={settingsReturnFocusRef}
      />
    </>
  );
}

export { AppSidebar as LayoutSidebar };

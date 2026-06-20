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
import { type MouseEvent, useEffect, useMemo, useRef, useState } from "react";
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
} from "@/features/workspace";
import { useAuth } from "@/lib/auth/auth-context";
import { isNeonAuthConfigured } from "@/lib/auth/neon";
import { UserButton } from "@neondatabase/auth-ui";
import { sessionsEndpoints } from "@/lib/rlm-api";
import { useChatHistoryStore } from "@/lib/workspace/chat-history-store";

import { SettingsDialog } from "./settings-dialog";
import { type SettingsSection } from "@/features/settings/screen/settings-content";

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
      <TooltipTrigger
        render={
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
        }
      />
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
        <ScrollArea className="h-full w-full min-w-0 max-w-full scroll-area-hover-reveal [&_[data-slot=scroll-area-viewport]>div]:!block [&_[data-slot=scroll-area-viewport]>div]:!w-full [&_[data-slot=scroll-area-viewport]>div]:!min-w-0">
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
  const { isAuthenticated, user } = useAuth();
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
  const [settingsSection, setSettingsSection] = useState<SettingsSection | undefined>(undefined);
  const settingsReturnFocusRef = useRef<HTMLElement | null>(null);

  // Synchronize past sessions from Neon if logged in
  useEffect(() => {
    if (!isAuthenticated) return;

    let active = true;
    const syncNeonSessions = async () => {
      try {
        const response = await sessionsEndpoints.list({ limit: 100 });
        if (!active) return;

        useChatHistoryStore.setState((state) => {
          const currentConversations = [...state.conversations];
          let updated = false;

          for (const item of response.items) {
            const externalId = item.external_session_id || item.id;
            const existingIndex = currentConversations.findIndex(
              (c) => c.id === externalId || c.durableSessionId === item.id
            );

            if (existingIndex >= 0) {
              const existing = currentConversations[existingIndex];
              if (existing && existing.durableSessionId !== item.id) {
                currentConversations[existingIndex] = {
                  ...existing,
                  durableSessionId: item.id,
                  title: existing.title || item.title,
                };
                updated = true;
              }
            } else {
              // Add compact record
              const newConv: Conversation = {
                id: externalId,
                title: item.title,
                messages: [],
                runtimeSessionId: externalId,
                durableSessionId: item.id,
                isCompactHistoryRecord: true,
                phase: "complete",
                createdAt: item.created_at,
                updatedAt: item.updated_at,
              };
              currentConversations.push(newConv);
              updated = true;
            }
          }

          if (updated) {
            return { conversations: currentConversations };
          }
          return {};
        });
      } catch (err) {
        console.error("Failed to sync Neon sessions on login:", err);
      }
    };

    syncNeonSessions();
    return () => {
      active = false;
    };
  }, [isAuthenticated]);

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
    if (isNeonAuthConfigured()) {
      navigate({ to: "/login" });
      return;
    }
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
              <TooltipTrigger
                render={
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
                }
              />
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
            {isAuthenticated && user && isNeonAuthConfigured() ? (
              <UserButton
                variant="ghost"
                size={isCollapsed ? "icon" : "default"}
                disableDefaultLinks={true}
                align="start"
                side="right"
                classNames={{
                  trigger: {
                    base: cn(
                      sidebarActionButtonClassName,
                      "h-10 px-1.5 py-1 text-sidebar-foreground hover:bg-sidebar-accent/80",
                      isCollapsed ? "justify-center" : "",
                    ),
                    user: {
                      title: "text-sidebar-foreground font-medium text-sm truncate max-w-[120px] text-left",
                      subtitle: "text-sidebar-foreground/58 text-xs truncate max-w-[120px] text-left",
                    },
                  },
                }}
                additionalLinks={[
                  <button
                    key="custom-settings"
                    type="button"
                    className="flex w-full items-center gap-2 cursor-pointer text-left text-sm text-foreground"
                    onClick={(event) => {
                      settingsReturnFocusRef.current = event.currentTarget;
                      setSettingsSection("appearance");
                      setSettingsOpen(true);
                    }}
                  >
                    <SidebarIcon icon={Settings01Icon} size={16} className="text-foreground/70" />
                    <span>Settings</span>
                  </button>
                ]}
              />
            ) : (
              <>
                {isAuthenticated && user ? (
                  <Tooltip>
                    <TooltipTrigger
                      render={
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={(event) => {
                            settingsReturnFocusRef.current = event.currentTarget;
                            setSettingsSection("account");
                            setSettingsOpen(true);
                          }}
                          title={isCollapsed ? user.name || user.email : undefined}
                          className={cn(
                            sidebarActionButtonClassName,
                            "hover:bg-sidebar-accent/80 hover:text-sidebar-foreground",
                          )}
                        >
                          <div className="flex size-5 shrink-0 items-center justify-center rounded-full bg-sidebar-primary text-sidebar-primary-foreground font-semibold text-[10px] uppercase">
                            {user.initials || "U"}
                          </div>
                          <span className="typo-label-regular tracking-tight-custom ml-2 truncate">
                            {user.name || user.email}
                          </span>
                        </Button>
                      }
                    />
                    <TooltipContent side="right">{user.name || user.email}</TooltipContent>
                  </Tooltip>
                ) : (
                  <Tooltip>
                    <TooltipTrigger
                      render={
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
                      }
                    />
                    <TooltipContent side="right">Sign in</TooltipContent>
                  </Tooltip>
                )}
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={(event) => {
                          settingsReturnFocusRef.current = event.currentTarget;
                          setSettingsSection("appearance");
                          setSettingsOpen(true);
                        }}
                        title={isCollapsed ? "Settings" : undefined}
                        className={sidebarActionButtonClassName}
                      >
                        <SidebarIcon icon={Settings01Icon} />
                        <span className="typo-label-regular tracking-tight-custom">Settings</span>
                      </Button>
                    }
                  />
                  <TooltipContent side="right">Settings</TooltipContent>
                </Tooltip>
              </>
            )}
          </div>
        </SidebarFooter>
      </Sidebar>

      <SettingsDialog
        open={settingsOpen}
        onOpenChange={(open) => {
          setSettingsOpen(open);
          if (!open) setSettingsSection(undefined);
        }}
        section={settingsSection}
        onSectionChange={setSettingsSection}
        returnFocusRef={settingsReturnFocusRef}
      />
    </>
  );
}

export { AppSidebar as LayoutSidebar };

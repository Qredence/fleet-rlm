import { type MouseEvent } from "react";
import {
  Plus,
  Search,
  Settings,
  PanelLeftClose,
  PanelLeftOpen,
  Database,
  LogIn,
  MessageSquare,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { QredenceLogo } from "@/components/shared/QredenceLogo";
import { useNavigate, useLocation } from "@tanstack/react-router";
import { useAppNavigate } from "@/hooks/useAppNavigate";
import {
  useWorkspaceShellActions,
  useWorkspaceShellHistory,
} from "@/screens/workspace/workspace-shell-contract";
import { cn } from "@/lib/utils/cn";

interface AppSidebarProps {
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

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

export function AppSidebar({ isCollapsed, onToggleCollapse }: AppSidebarProps) {
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

  const handleOpenWorkspace = () => {
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

  if (isCollapsed) {
    return (
      <div className="flex h-full w-15 flex-col items-center border-r border-border-subtle bg-surface py-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleCollapse}
          className="mb-4 rounded-md!"
        >
          <PanelLeftOpen
            className="size-5 text-muted-foreground"
            strokeWidth={1.5}
          />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleOpenWorkspace}
          className="mb-2 rounded-md!"
          aria-label="RLM Workspace"
        >
          <MessageSquare
            className="size-5 text-muted-foreground"
            strokeWidth={1.5}
          />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleNewSession}
          className="mb-4 rounded-md!"
          aria-label="New session"
        >
          <Plus className="size-5" strokeWidth={1.5} />
        </Button>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="icon"
          onClick={handleOpenSettings}
          className="mb-4 rounded-md!"
          aria-label="Open settings"
        >
          <Settings
            className="size-5 text-muted-foreground"
            strokeWidth={1.5}
          />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={handleOpenLogin}
          className="mb-4 rounded-md!"
          aria-label="Sign In"
        >
          <LogIn className="size-5 text-muted-foreground" strokeWidth={1.5} />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full w-65 flex-col overflow-hidden border-r border-border-subtle bg-surface">
      {/* Header */}
      <div className="flex h-12 items-center justify-between px-2">
        <div className="flex items-center gap-2 pl-1.5 text-foreground">
          <QredenceLogo className="h-5 w-auto" />
          <span className="typo-base font-medium">Qredence</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleCollapse}
          className="rounded-md!"
        >
          <PanelLeftClose
            className="size-5 text-muted-foreground"
            strokeWidth={1.5}
          />
        </Button>
      </div>

      {/* Primary Actions */}
      <div className="mb-4 flex flex-col gap-px px-2">
        <Button
          size="sm"
          variant={isWorkspace ? "secondary" : "ghost"}
          onClick={handleOpenWorkspace}
          className={cn(
            "group w-full justify-start rounded-md! pl-1.5 text-left transition-colors duration-0",
            !isWorkspace && "text-muted-foreground",
          )}
        >
          <MessageSquare className="min-w-5 size-5" strokeWidth={1.5} />
          <span className="typo-base overflow-hidden whitespace-nowrap font-medium">
            RLM Workspace
          </span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={handleNewSession}
          className={cn(
            "group w-full justify-start rounded-md! pl-1.5 text-left transition-colors duration-0",
            "text-muted-foreground",
          )}
        >
          <Plus className="min-w-5 size-5" strokeWidth={1.5} />
          <span className="typo-base overflow-hidden whitespace-nowrap font-medium">
            New Session
          </span>
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="group w-full justify-start rounded-md! pl-1.5 text-left text-muted-foreground transition-colors duration-0"
        >
          <Search className="min-w-5 size-5" strokeWidth={1.5} />
          <span className="typo-base overflow-hidden whitespace-nowrap font-medium">
            Search sessions
          </span>
        </Button>
        <Button
          size="sm"
          variant={isVolumes ? "secondary" : "ghost"}
          onClick={() => navigateTo("volumes")}
          className={cn(
            "group w-full justify-start rounded-md! pl-1.5 text-left transition-colors duration-0",
            !isVolumes && "text-muted-foreground",
          )}
        >
          <Database className="min-w-5 size-5" strokeWidth={1.5} />
          <span className="typo-base overflow-hidden whitespace-nowrap font-medium">
            Volumes
          </span>
        </Button>
      </div>

      {/* Session List */}
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        <div className="mb-1 ml-2 pl-1.5 py-1 text-xs font-medium text-muted-foreground">
          Sessions
        </div>
        <div className="flex flex-col gap-px px-2">
          {conversations.length === 0 ? (
            <div className="mx-2 rounded-lg border border-dashed border-border-subtle/80 px-3 py-3 text-xs text-muted-foreground">
              Start a new session to build your recent history.
            </div>
          ) : (
            conversations.map((session) => (
              <Button
                key={session.id}
                variant="ghost"
                size="sm"
                onClick={() => handleOpenConversation(session.id)}
                className={cn(
                  "group h-auto min-h-8 w-full justify-start rounded-md! pl-1.5 pr-1 py-1.5 text-left transition-colors duration-0",
                  "hover:bg-muted/70",
                )}
              >
                <div className="min-w-0 flex-1 text-left">
                  <div className="truncate text-sm font-[450] text-foreground">
                    {session.title}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {formatSessionTimestamp(session.updatedAt)}
                  </div>
                </div>
              </Button>
            ))
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="space-y-1 border-t border-border-subtle/50 px-2 py-3">
        <Button
          onClick={handleOpenSettings}
          size="sm"
          variant="ghost"
          className="group w-full justify-start rounded-md! pl-1.5 text-left text-muted-foreground transition-colors duration-0"
        >
          <Settings className="min-w-5 size-5" strokeWidth={1.5} />
          <span className="typo-base overflow-hidden whitespace-nowrap font-medium">
            Settings
          </span>
        </Button>
        <Button
          onClick={handleOpenLogin}
          size="sm"
          variant="ghost"
          className="group w-full justify-start rounded-md! pl-1.5 text-left text-muted-foreground transition-colors duration-0"
        >
          <LogIn className="min-w-5 size-5" strokeWidth={1.5} />
          <span className="typo-base overflow-hidden whitespace-nowrap font-medium">
            Sign In
          </span>
        </Button>
      </div>
    </div>
  );
}

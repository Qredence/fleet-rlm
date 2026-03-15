import { Plus, Search, Settings, PanelLeftClose, PanelLeftOpen, Database, LogIn } from "lucide-react";
import { Button } from "@/components/ui/button";
import { QredenceLogo } from "@/components/shared/QredenceLogo";
import { useNavigate, useLocation } from "@tanstack/react-router";
import { useConversations } from "@/stores/chatHistoryStore";
import { cn } from "@/lib/utils/cn";

interface AppSidebarProps {
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

export function AppSidebar({ isCollapsed, onToggleCollapse }: AppSidebarProps) {
  const conversations = useConversations();
  const navigate = useNavigate();
  const location = useLocation();

  const handleOpenSettings = () => {
    const openSettingsEvent = new CustomEvent("open-settings", {
      detail: { section: "general" },
      cancelable: true,
    });
    const wasHandledByDialog = document.dispatchEvent(openSettingsEvent) === false;
    if (!wasHandledByDialog) {
      navigate({ to: "/settings" });
    }
  };

  if (isCollapsed) {
    return (
      <div className="flex flex-col items-center py-4 w-[60px] border-r border-border-subtle bg-surface h-full">
        <Button variant="ghost" size="icon-sm" onClick={onToggleCollapse} className="mb-4">
          <PanelLeftOpen className="size-4 text-muted-foreground" />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => navigate({ to: "/" })} className="mb-4">
          <Plus className="size-4" />
        </Button>
        <div className="flex-1" />
        <Button variant="ghost" size="icon-sm" onClick={handleOpenSettings} className="mb-4">
          <Settings className="size-4 text-muted-foreground" />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={() => navigate({ to: "/login" })} className="mb-4">
          <LogIn className="size-4 text-muted-foreground" />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col w-[260px] border-r border-border-subtle bg-surface h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between p-4 pb-2">
        <div className="flex items-center gap-2 text-foreground">
          <QredenceLogo className="h-5 w-auto" />
          <span className="font-semibold text-sm tracking-wide">Qredence</span>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={onToggleCollapse}>
          <PanelLeftClose className="size-4 text-muted-foreground" />
        </Button>
      </div>

      {/* Primary Actions */}
      <div className="px-3 py-2 space-y-1">
        <Button
          variant={location.pathname === "/" ? "secondary" : "ghost"}
          onClick={() => navigate({ to: "/" })}
          className={cn("w-full justify-start h-9 px-2 gap-2 font-normal", location.pathname !== "/" && "text-muted-foreground")}
        >
          <Plus className="size-4" />
          <span>New Session</span>
        </Button>
        <Button variant="ghost" className="w-full justify-start h-9 px-2 gap-2 font-normal text-muted-foreground">
          <Search className="size-4" />
          <span>Search sessions</span>
        </Button>
        <Button
          variant={location.pathname === "/app/volumes" ? "secondary" : "ghost"}
          onClick={() => navigate({ to: "/app/volumes" })}
          className={cn("w-full justify-start h-9 px-2 gap-2 font-normal", location.pathname !== "/app/volumes" && "text-muted-foreground")}
        >
          <Database className="size-4" />
          <span>Volumes</span>
        </Button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <div className="text-xs font-medium text-muted-foreground px-2 py-1.5 mb-1">
          Sessions
        </div>
        <div className="space-y-0.5">
          {conversations.length === 0 ? (
            <div className="text-xs text-muted-foreground px-2 py-2">No previous sessions</div>
          ) : (
            conversations.map((session) => (
              <Button
                key={session.id}
                variant="ghost"
                className="w-full justify-start h-9 px-2 font-normal truncate"
              >
                <span className="truncate w-full text-left">{session.title}</span>
              </Button>
            ))
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-border-subtle/50 space-y-1">
        <Button onClick={handleOpenSettings} variant="ghost" className="w-full justify-start h-9 px-2 gap-2 font-normal text-muted-foreground">
          <Settings className="size-4" />
          <span>Settings</span>
        </Button>
        <Button onClick={() => navigate({ to: "/login" })} variant="ghost" className="w-full justify-start h-9 px-2 gap-2 font-normal text-muted-foreground">
          <LogIn className="size-4" />
          <span>Sign in</span>
        </Button>
      </div>
    </div>
  );
}

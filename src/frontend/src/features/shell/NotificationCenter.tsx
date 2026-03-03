/**
 * NotificationCenter — bell icon dropdown showing recent activity.
 *
 * Renders in TopHeader between the side-panel toggle and UserMenu.
 * Uses Popover on desktop and vaul Drawer on mobile for the list.
 *
 * All data is mock — demonstrates the pattern for a real notification
 * system backed by WebSockets or polling.
 */
import { useState, useCallback } from "react";
import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import {
  Bell,
  X,
  Check,
  Zap,
  ShieldCheck,
  Users,
  GitFork,
  AlertTriangle,
} from "lucide-react";
import { Drawer } from "vaul";
import { springs } from "@/lib/config/motion-config";
import { typo } from "@/lib/config/typo";
import { useIsMobile } from "@/components/ui/use-mobile";
import { IconButton } from "@/components/ui/icon-button";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/components/ui/utils";

// ── Types ───────────────────────────────────────────────────────────

interface Notification {
  id: string;
  type: "skill" | "validation" | "team" | "system";
  title: string;
  description: string;
  time: string;
  read: boolean;
  icon: typeof Zap;
  iconColor: string;
}

// ── Mock data ───────────────────────────────────────────────────────

const initialNotifications: Notification[] = [
  {
    id: "n-1",
    type: "skill",
    title: "Skill published",
    description: "Test Generation v1.4.2 was published to the registry.",
    time: "2 min ago",
    read: false,
    icon: Zap,
    iconColor: "var(--accent)",
  },
  {
    id: "n-2",
    type: "validation",
    title: "Validation passed",
    description: "Data Analysis scored 94/100 on quality checks.",
    time: "15 min ago",
    read: false,
    icon: ShieldCheck,
    iconColor: "var(--chart-3)",
  },
  {
    id: "n-3",
    type: "team",
    title: "Team member joined",
    description: "Sarah Kim accepted the invite and joined Qredence.",
    time: "1 hour ago",
    read: false,
    icon: Users,
    iconColor: "var(--chart-2)",
  },
  {
    id: "n-4",
    type: "skill",
    title: "Dependency updated",
    description: "code-analysis v2.1.0 is available. 3 skills depend on it.",
    time: "3 hours ago",
    read: true,
    icon: GitFork,
    iconColor: "var(--chart-4)",
  },
  {
    id: "n-5",
    type: "system",
    title: "Scheduled maintenance",
    description: "Platform maintenance window: Feb 15, 02:00-04:00 UTC.",
    time: "6 hours ago",
    read: true,
    icon: AlertTriangle,
    iconColor: "var(--chart-5)",
  },
  {
    id: "n-6",
    type: "validation",
    title: "Validation warning",
    description: "Knowledge Extraction quality score dropped below 80%.",
    time: "1 day ago",
    read: true,
    icon: AlertTriangle,
    iconColor: "var(--chart-5)",
  },
];

// ── Notification item ───────────────────────────────────────────────

function NotificationItem({
  notification,
  onMarkRead,
}: {
  notification: Notification;
  onMarkRead: (id: string) => void;
}) {
  const Icon = notification.icon;
  const prefersReduced = useReducedMotion();

  return (
    <motion.div
      initial={{ opacity: 0, y: prefersReduced ? 0 : 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={prefersReduced ? springs.instant : springs.default}
      className={cn(
        "flex items-start gap-3 px-4 py-3 transition-colors touch-target",
        !notification.read && "bg-accent/[0.03]",
        "hover:bg-muted/50",
      )}
    >
      {/* Icon */}
      <div
        className="flex items-center justify-center size-8 rounded-lg shrink-0 mt-0.5"
        style={{
          backgroundColor: `color-mix(in srgb, ${notification.iconColor} 12%, transparent)`,
        }}
      >
        <Icon className="size-4" style={{ color: notification.iconColor }} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-foreground truncate" style={typo.label}>
            {notification.title}
          </span>
          {!notification.read && (
            <div className="size-1.5 rounded-full bg-accent shrink-0" />
          )}
        </div>
        <p
          className="text-muted-foreground mt-0.5 line-clamp-2"
          style={typo.helper}
        >
          {notification.description}
        </p>
        <span
          className="text-muted-foreground/60 mt-1 block"
          style={typo.micro}
        >
          {notification.time}
        </span>
      </div>

      {/* Mark as read */}
      {!notification.read && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onMarkRead(notification.id);
          }}
          className="shrink-0 p-1 rounded-md hover:bg-muted transition-colors mt-0.5"
          aria-label={`Mark "${notification.title}" as read`}
        >
          <Check className="size-3.5 text-muted-foreground" />
        </button>
      )}
    </motion.div>
  );
}

// ── Notification list body ──────────────────────────────────────────

function NotificationList({
  notifications,
  onMarkRead,
  onMarkAllRead,
}: {
  notifications: Notification[];
  onMarkRead: (id: string) => void;
  onMarkAllRead: () => void;
}) {
  const unreadCount = notifications.filter((n) => !n.read).length;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border-subtle shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-foreground" style={typo.label}>
            Notifications
          </span>
          {unreadCount > 0 && (
            <Badge variant="accent" className="rounded-full">
              {unreadCount}
            </Badge>
          )}
        </div>
        {unreadCount > 0 && (
          <Button
            variant="ghost"
            className="h-auto px-2 py-1 text-muted-foreground hover:text-foreground"
            onClick={onMarkAllRead}
          >
            <span style={typo.helper}>Mark all read</span>
          </Button>
        )}
      </div>

      {/* List */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="divide-y divide-border-subtle">
          {notifications.map((n) => (
            <NotificationItem
              key={n.id}
              notification={n}
              onMarkRead={onMarkRead}
            />
          ))}
        </div>

        {notifications.length === 0 && (
          <div className="flex flex-col items-center justify-center py-12 text-center px-4">
            <Bell className="size-8 text-muted-foreground mb-3" />
            <p className="text-muted-foreground" style={typo.label}>
              No notifications
            </p>
            <p className="text-muted-foreground" style={typo.helper}>
              Activity from your skill fleet will appear here.
            </p>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

// ── Unread badge ────────────────────────────────────────────────────

function UnreadBadge({ count }: { count: number }) {
  const prefersReduced = useReducedMotion();

  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.span
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          exit={{ scale: 0 }}
          transition={prefersReduced ? springs.instant : springs.snappy}
          className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[16px] h-4 px-1 rounded-full bg-accent pointer-events-none"
        >
          <span
            style={{
              ...typo.micro,
              color: "var(--accent-foreground)",
              fontWeight: "var(--font-weight-medium)",
            }}
          >
            {count}
          </span>
        </motion.span>
      )}
    </AnimatePresence>
  );
}

// ── Main component ──────────────────────────────────────────────────

export function NotificationCenter() {
  const isMobile = useIsMobile();
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState(initialNotifications);

  const unreadCount = notifications.filter((n) => !n.read).length;
  const bellLabel = `Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`;

  const markRead = useCallback((id: string) => {
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n)),
    );
  }, []);

  const markAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
  }, []);

  // ── Mobile: Tooltip + Drawer ──────────────────────────────────
  if (isMobile) {
    return (
      <>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex relative">
              <IconButton
                onClick={() => setOpen(true)}
                aria-label={bellLabel}
                className="touch-target"
              >
                <Bell className="size-5" />
              </IconButton>
              <UnreadBadge count={unreadCount} />
            </span>
          </TooltipTrigger>
          <TooltipContent side="bottom">Notifications</TooltipContent>
        </Tooltip>

        <Drawer.Root open={open} onOpenChange={setOpen}>
          <Drawer.Portal>
            <Drawer.Overlay
              className="fixed inset-0 z-50"
              style={{ backgroundColor: "var(--glass-overlay)" }}
            />
            <Drawer.Content
              className="fixed inset-x-0 bottom-0 z-50 flex flex-col outline-none"
              style={{
                height: "80dvh",
                borderTopLeftRadius: "var(--radius-card)",
                borderTopRightRadius: "var(--radius-card)",
                backgroundColor: "var(--glass-sheet-bg)",
                backdropFilter: "blur(var(--glass-sheet-blur))",
                WebkitBackdropFilter: "blur(var(--glass-sheet-blur))",
                borderTop: "0.5px solid var(--glass-sheet-border)",
              }}
            >
              <div className="flex items-center justify-center py-2 shrink-0">
                <div
                  className="w-9 h-[5px] rounded-full"
                  style={{ backgroundColor: "var(--glass-sheet-handle)" }}
                  aria-hidden="true"
                />
              </div>
              <div className="flex items-center justify-between px-4 pb-2 shrink-0">
                <Drawer.Title>
                  <span className="text-foreground" style={typo.h3}>
                    Notifications
                  </span>
                </Drawer.Title>
                <IconButton
                  onClick={() => setOpen(false)}
                  aria-label="Close notifications"
                  className="touch-target"
                >
                  <X className="size-5 text-muted-foreground" />
                </IconButton>
              </div>
              <Drawer.Description className="sr-only">
                Recent notifications and activity feed
              </Drawer.Description>
              <NotificationList
                notifications={notifications}
                onMarkRead={markRead}
                onMarkAllRead={markAllRead}
              />
            </Drawer.Content>
          </Drawer.Portal>
        </Drawer.Root>
      </>
    );
  }

  // ── Desktop: Popover (no nested Tooltip — avoids asChild ref issue) ──
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <span className="inline-flex relative">
          <IconButton aria-label={bellLabel}>
            <Bell className="size-5" />
          </IconButton>
          <UnreadBadge count={unreadCount} />
        </span>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[380px] p-0 overflow-hidden border-border-subtle"
        style={{
          borderRadius: "var(--radius-card)",
          maxHeight: "480px",
          boxShadow: "var(--shadow-200-stronger)",
        }}
      >
        <NotificationList
          notifications={notifications}
          onMarkRead={markRead}
          onMarkAllRead={markAllRead}
        />
      </PopoverContent>
    </Popover>
  );
}

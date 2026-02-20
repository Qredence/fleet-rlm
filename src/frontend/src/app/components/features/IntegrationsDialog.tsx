/**
 * Mock application integrations dialog.
 *
 * Shows MCP-style integrations (GitHub, Slack, Jira, etc.) with
 * connect/disconnect toggles. Desktop: Dialog. Mobile: iOS 26 sheet.
 */
import { useState } from "react";
import {
  X,
  Github,
  MessageSquare,
  Figma,
  Blocks,
  PlugZap,
  Unplug,
  ExternalLink,
} from "lucide-react";
import { toast } from "sonner";
import { Drawer } from "vaul";
import { usePostHog } from "@posthog/react";
import { typo } from "../config/typo";
import { useIsMobile } from "../ui/use-mobile";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "../ui/dialog";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { IconButton } from "../ui/icon-button";
import { ScrollArea } from "../ui/scroll-area";
import { cn } from "../ui/utils";

// ── Integration data ────────────────────────────────────────────────

interface Integration {
  id: string;
  name: string;
  description: string;
  icon: typeof Github;
  category: "mcp" | "communication" | "design" | "devops";
  connected: boolean;
  status?: string;
}

const initialIntegrations: Integration[] = [
  {
    id: "github",
    name: "GitHub",
    description:
      "Sync skills with repositories, trigger CI/CD pipelines on skill publish.",
    icon: Github,
    category: "mcp",
    connected: true,
    status: "Connected as qredence-org",
  },
  {
    id: "slack",
    name: "Slack",
    description:
      "Receive skill creation notifications and HITL review requests in channels.",
    icon: MessageSquare,
    category: "communication",
    connected: true,
    status: "Connected to #skill-fleet",
  },
  {
    id: "jira",
    name: "Jira",
    description:
      "Create issues from validation failures and link skills to epics.",
    icon: Blocks,
    category: "devops",
    connected: false,
  },
  {
    id: "linear",
    name: "Linear",
    description:
      "Sync skill creation tasks and track progress in Linear projects.",
    icon: Blocks,
    category: "devops",
    connected: false,
  },
  {
    id: "notion",
    name: "Notion",
    description: "Export skill documentation to Notion pages and databases.",
    icon: Blocks,
    category: "mcp",
    connected: true,
    status: "Connected to Skills Wiki",
  },
  {
    id: "figma",
    name: "Figma",
    description:
      "Import design specifications and UI patterns as skill inputs.",
    icon: Figma,
    category: "design",
    connected: false,
  },
  {
    id: "confluence",
    name: "Confluence",
    description:
      "Publish validated skills as Confluence pages for team documentation.",
    icon: Blocks,
    category: "mcp",
    connected: false,
  },
  {
    id: "custom-mcp",
    name: "Custom MCP Server",
    description:
      "Connect any MCP-compatible server for custom tool integrations.",
    icon: PlugZap,
    category: "mcp",
    connected: false,
  },
];

const categoryLabels: Record<string, string> = {
  mcp: "MCP Integrations",
  communication: "Communication",
  design: "Design",
  devops: "DevOps & Project Management",
};

const categoryOrder = ["mcp", "devops", "communication", "design"];

// ── Integration card ────────────────────────────────────────────────

function IntegrationCard({
  integration,
  onToggle,
}: {
  integration: Integration;
  onToggle: () => void;
}) {
  const Icon = integration.icon;

  return (
    <div
      className={cn(
        "flex items-start gap-3 p-3 rounded-lg border transition-colors",
        integration.connected
          ? "border-accent/20 bg-accent/5"
          : "border-border-subtle bg-card hover:border-border-strong",
      )}
    >
      {/* Icon */}
      <div
        className={cn(
          "flex items-center justify-center w-9 h-9 rounded-lg shrink-0",
          integration.connected ? "bg-accent/10" : "bg-muted",
        )}
      >
        <Icon
          className={cn(
            "size-[18px]",
            integration.connected ? "text-accent" : "text-muted-foreground",
          )}
        />
      </div>

      {/* Text */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-foreground" style={typo.label}>
            {integration.name}
          </span>
          {integration.connected && (
            <Badge variant="success" className="shrink-0">
              Connected
            </Badge>
          )}
        </div>
        <p className="text-muted-foreground mt-0.5" style={typo.helper}>
          {integration.description}
        </p>
        {integration.status && (
          <p className="text-accent mt-1" style={typo.helper}>
            {integration.status}
          </p>
        )}
      </div>

      {/* Action */}
      <Button
        variant={integration.connected ? "outline" : "secondary"}
        className="shrink-0 gap-1.5"
        onClick={onToggle}
      >
        {integration.connected ? (
          <>
            <Unplug className="size-3.5" />
            <span style={typo.label}>Disconnect</span>
          </>
        ) : (
          <>
            <ExternalLink className="size-3.5" />
            <span style={typo.label}>Connect</span>
          </>
        )}
      </Button>
    </div>
  );
}

// ── Shared body ─────────────────────────────────────────────────────

function IntegrationsBody() {
  const [integrations, setIntegrations] = useState(initialIntegrations);
  const posthog = usePostHog();

  function handleToggle(id: string) {
    setIntegrations((prev) =>
      prev.map((intg) => {
        if (intg.id !== id) return intg;
        const next = !intg.connected;

        // PostHog: Capture integration connect/disconnect events
        if (next) {
          posthog?.capture("integration_connected", {
            integration_id: intg.id,
            integration_name: intg.name,
            integration_category: intg.category,
          });
          toast.success(`${intg.name} connected successfully`, {
            description: `Your ${intg.name} workspace is now linked. Data sync will begin shortly.`,
          });
        } else {
          posthog?.capture("integration_disconnected", {
            integration_id: intg.id,
            integration_name: intg.name,
            integration_category: intg.category,
          });
          toast(`${intg.name} disconnected`, {
            description: `The ${intg.name} integration has been removed. Existing synced data will be preserved.`,
          });
        }

        return {
          ...intg,
          connected: next,
          status: next ? `Connected just now` : undefined,
        };
      }),
    );
  }

  const connectedCount = integrations.filter((i) => i.connected).length;

  // Group by category
  const grouped = categoryOrder.map((cat) => ({
    key: cat,
    label: categoryLabels[cat],
    items: integrations.filter((i) => i.category === cat),
  }));

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground" style={typo.h3}>
          Integrations
        </h2>
        <p className="text-muted-foreground mt-1" style={typo.caption}>
          {connectedCount} of {integrations.length} integrations active
        </p>
      </div>

      {grouped.map((group) => (
        <div key={group.key}>
          <span className="text-muted-foreground" style={typo.label}>
            {group.label}
          </span>
          <div className="mt-2 space-y-2">
            {group.items.map((intg) => (
              <IntegrationCard
                key={intg.id}
                integration={intg}
                onToggle={() => handleToggle(intg.id)}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────

interface IntegrationsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function IntegrationsDialog({
  open,
  onOpenChange,
}: IntegrationsDialogProps) {
  const isMobile = useIsMobile();

  if (isMobile) {
    return (
      <Drawer.Root open={open} onOpenChange={onOpenChange}>
        <Drawer.Portal>
          <Drawer.Overlay
            className="fixed inset-0 z-50"
            style={{ backgroundColor: "var(--glass-overlay)" }}
          />
          <Drawer.Content
            className="fixed inset-x-0 bottom-0 z-50 flex flex-col outline-none"
            style={{
              height: "95dvh",
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
                  Integrations
                </span>
              </Drawer.Title>
              <IconButton
                onClick={() => onOpenChange(false)}
                aria-label="Close integrations"
                className="touch-target"
              >
                <X className="size-5 text-muted-foreground" />
              </IconButton>
            </div>
            <Drawer.Description className="sr-only">
              Manage application integrations and MCP connections
            </Drawer.Description>
            <ScrollArea className="flex-1 min-h-0">
              <div className="px-4 pb-6">
                <IntegrationsBody />
              </div>
            </ScrollArea>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[640px] p-6 rounded-card max-h-[85dvh] overflow-hidden flex flex-col">
        <DialogTitle className="sr-only">Integrations</DialogTitle>
        <DialogDescription className="sr-only">
          Manage application integrations and MCP connections
        </DialogDescription>
        <ScrollArea className="flex-1 min-h-0">
          <IntegrationsBody />
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}

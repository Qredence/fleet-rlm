/**
 * Mock pricing plan dialog.
 *
 * Displays three tiers (Free, Pro, Enterprise) with the user's current
 * plan highlighted. Desktop: Dialog. Mobile: iOS 26 Liquid Glass sheet.
 */
import { Check, X } from "lucide-react";
import { toast } from "sonner";
import { Drawer } from "vaul";
import { useTelemetry } from "@/lib/telemetry/useTelemetry";
import { typo } from "@/lib/config/typo";
import { useAuth, type PlanTier } from "@/hooks/useAuth";
import { useIsMobile } from "@/components/ui/use-mobile";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { IconButton } from "@/components/ui/icon-button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/components/ui/utils";

// ── Plan data ───────────────────────────────────────────────────────

interface PlanDef {
  key: PlanTier;
  name: string;
  price: string;
  period: string;
  description: string;
  features: string[];
  highlighted?: boolean;
}

const plans: PlanDef[] = [
  {
    key: "free",
    name: "Free",
    price: "$0",
    period: "/month",
    description: "For individuals exploring skill creation",
    features: [
      "5 skills per month",
      "Basic validation",
      "Community support",
      "Single user",
    ],
  },
  {
    key: "pro",
    name: "Pro",
    price: "$29",
    period: "/month",
    description: "For teams building production skill libraries",
    features: [
      "Unlimited skills",
      "Advanced validation & HITL",
      "Priority support",
      "Up to 10 team members",
      "Custom taxonomy",
      "API access",
      "Analytics dashboard",
    ],
    highlighted: true,
  },
  {
    key: "enterprise",
    name: "Enterprise",
    price: "Custom",
    period: "",
    description: "For organizations with advanced compliance needs",
    features: [
      "Everything in Pro",
      "Unlimited team members",
      "SSO / SAML",
      "Audit logging",
      "Dedicated account manager",
      "SLA guarantee",
      "On-premise deployment",
      "Custom integrations",
    ],
  },
];

// ── Plan card ───────────────────────────────────────────────────────

function PlanCard({
  plan,
  isCurrent,
  onSelect,
}: {
  plan: PlanDef;
  isCurrent: boolean;
  onSelect: () => void;
}) {
  return (
    <div
      className={cn(
        "flex flex-col rounded-card border p-5 transition-colors",
        isCurrent
          ? "border-accent bg-accent/5"
          : "border-border-subtle bg-card hover:border-border-strong",
      )}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-foreground" style={typo.h4}>
          {plan.name}
        </span>
        {isCurrent && <Badge variant="accent">Current</Badge>}
        {plan.highlighted && !isCurrent && (
          <Badge variant="secondary">Popular</Badge>
        )}
      </div>

      <div className="flex items-baseline gap-0.5 mb-2">
        <span className="text-foreground" style={typo.h2}>
          {plan.price}
        </span>
        {plan.period && (
          <span className="text-muted-foreground" style={typo.caption}>
            {plan.period}
          </span>
        )}
      </div>

      <p className="text-muted-foreground mb-4" style={typo.caption}>
        {plan.description}
      </p>

      <ul className="space-y-2 mb-5 flex-1">
        {plan.features.map((f) => (
          <li key={f} className="flex items-start gap-2">
            <Check
              className="size-4 text-accent shrink-0 mt-0.5"
              strokeWidth={2}
            />
            <span className="text-foreground" style={typo.caption}>
              {f}
            </span>
          </li>
        ))}
      </ul>

      <Button
        variant={
          isCurrent ? "outline" : plan.highlighted ? "default" : "secondary"
        }
        className="w-full"
        disabled={isCurrent}
        onClick={onSelect}
      >
        <span style={typo.label}>
          {isCurrent
            ? "Current Plan"
            : plan.key === "enterprise"
              ? "Contact Sales"
              : "Upgrade"}
        </span>
      </Button>
    </div>
  );
}

// ── Shared body ─────────────────────────────────────────────────────

function PricingBody({ onClose }: { onClose: () => void }) {
  const { user, setPlan } = useAuth();
  const telemetry = useTelemetry();
  const currentPlan = user?.plan ?? "free";

  function handleSelect(tier: PlanTier) {
    if (tier === "enterprise") {
      // PostHog: Capture enterprise inquiry event
      telemetry.capture("enterprise_inquiry_submitted", {
        current_plan: currentPlan,
      });
      toast("Enterprise inquiry submitted", {
        description:
          "Our sales team at sales@qredence.ai will reach out within 24 hours to discuss your requirements.",
      });
      return;
    }

    const previousPlan = currentPlan;
    const tierLabel = tier.charAt(0).toUpperCase() + tier.slice(1);
    const previousLabel =
      previousPlan.charAt(0).toUpperCase() + previousPlan.slice(1);
    const isUpgrade = previousPlan === "free" && tier === "pro";
    const isDowngrade = previousPlan === "pro" && tier === "free";

    setPlan(tier);

    // PostHog: Capture plan upgrade or downgrade events
    if (isUpgrade) {
      telemetry.capture("plan_upgraded", {
        previous_plan: previousPlan,
        new_plan: tier,
      });
      toast.success(`Upgraded to ${tierLabel}!`, {
        description: `Your workspace has been upgraded from ${previousLabel} to ${tierLabel}. All new features are now available.`,
      });
    } else if (isDowngrade) {
      telemetry.capture("plan_downgraded", {
        previous_plan: previousPlan,
        new_plan: tier,
      });
      toast.success(`Switched to ${tierLabel} plan`, {
        description: `Your plan has been changed from ${previousLabel} to ${tierLabel}. Changes take effect at the start of the next billing cycle.`,
      });
    } else {
      toast.success(`Switched to ${tierLabel} plan`, {
        description: `Your plan has been changed from ${previousLabel} to ${tierLabel}. Changes take effect at the start of the next billing cycle.`,
      });
    }

    onClose();
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h2 className="text-foreground" style={typo.h3}>
          Choose Your Plan
        </h2>
        <p className="text-muted-foreground mt-1" style={typo.caption}>
          Scale your skill management as your team grows
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {plans.map((plan) => (
          <PlanCard
            key={plan.key}
            plan={plan}
            isCurrent={currentPlan === plan.key}
            onSelect={() => handleSelect(plan.key)}
          />
        ))}
      </div>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────

interface PricingDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function PricingDialog({ open, onOpenChange }: PricingDialogProps) {
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
                  Pricing
                </span>
              </Drawer.Title>
              <IconButton
                onClick={() => onOpenChange(false)}
                aria-label="Close pricing"
                className="touch-target"
              >
                <X className="size-5 text-muted-foreground" />
              </IconButton>
            </div>
            <Drawer.Description className="sr-only">
              Choose a pricing plan for Skill Fleet
            </Drawer.Description>
            <ScrollArea className="flex-1 min-h-0">
              <div className="px-4 pb-6">
                <PricingBody onClose={() => onOpenChange(false)} />
              </div>
            </ScrollArea>
          </Drawer.Content>
        </Drawer.Portal>
      </Drawer.Root>
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[860px] p-6 rounded-card">
        <DialogTitle className="sr-only">Pricing Plans</DialogTitle>
        <DialogDescription className="sr-only">
          Choose a pricing plan for Skill Fleet
        </DialogDescription>
        <PricingBody onClose={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

/**
 * Billing settings pane — payment method and invoice history.
 */
import { useState } from "react";
import {
  CreditCard,
  Download,
  MoreHorizontal,
  Pencil,
  Trash2,
  Plus,
  CheckCircle2,
  Clock,
  AlertCircle,
} from "lucide-react";
import { toast } from "sonner";
import { usePostHog } from "@posthog/react";
import { typo } from "../../config/typo";
import { useAuth } from "../../hooks/useAuth";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../ui/dropdown-menu";
import { cn } from "../../ui/utils";
import { SettingsRow } from "../../shared/SettingsRow";
import { ListRow } from "../../shared/ListRow";

// ── Mock data ───────────────────────────────────────────────────────

interface PaymentMethod {
  id: string;
  type: "visa" | "mastercard" | "amex";
  last4: string;
  expiry: string;
  isDefault: boolean;
}

interface Invoice {
  id: string;
  date: string;
  amount: string;
  status: "paid" | "pending" | "failed";
  description: string;
}

const initialPaymentMethods: PaymentMethod[] = [
  {
    id: "pm_1",
    type: "visa",
    last4: "4242",
    expiry: "03/27",
    isDefault: true,
  },
  {
    id: "pm_2",
    type: "mastercard",
    last4: "8888",
    expiry: "11/26",
    isDefault: false,
  },
];

const mockInvoices: Invoice[] = [
  {
    id: "inv_006",
    date: "Feb 1, 2026",
    amount: "$29.00",
    status: "paid",
    description: "Pro Plan — February 2026",
  },
  {
    id: "inv_005",
    date: "Jan 1, 2026",
    amount: "$29.00",
    status: "paid",
    description: "Pro Plan — January 2026",
  },
  {
    id: "inv_004",
    date: "Dec 1, 2025",
    amount: "$29.00",
    status: "paid",
    description: "Pro Plan — December 2025",
  },
  {
    id: "inv_003",
    date: "Nov 1, 2025",
    amount: "$29.00",
    status: "paid",
    description: "Pro Plan — November 2025",
  },
  {
    id: "inv_002",
    date: "Oct 1, 2025",
    amount: "$0.00",
    status: "paid",
    description: "Free Plan — October 2025",
  },
  {
    id: "inv_001",
    date: "Sep 1, 2025",
    amount: "$0.00",
    status: "paid",
    description: "Free Plan — September 2025",
  },
];

const cardBrandLabels: Record<string, string> = {
  visa: "Visa",
  mastercard: "Mastercard",
  amex: "American Express",
};

// ── Status rendering ────────────────────────────────────────────────

function InvoiceStatusBadge({ status }: { status: Invoice["status"] }) {
  switch (status) {
    case "paid":
      return (
        <Badge variant="success" className="gap-1">
          <CheckCircle2 className="size-3" />
          Paid
        </Badge>
      );
    case "pending":
      return (
        <Badge variant="warning" className="gap-1">
          <Clock className="size-3" />
          Pending
        </Badge>
      );
    case "failed":
      return (
        <Badge variant="destructive-subtle" className="gap-1">
          <AlertCircle className="size-3" />
          Failed
        </Badge>
      );
  }
}

// ── Payment method card ─────────────────────────────────────────────

function PaymentMethodCard({
  method,
  onSetDefault,
  onRemove,
}: {
  method: PaymentMethod;
  onSetDefault: () => void;
  onRemove: () => void;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-3 p-3 rounded-lg border transition-colors",
        method.isDefault
          ? "border-accent/20 bg-accent/5"
          : "border-border-subtle bg-card",
      )}
    >
      {/* Card icon */}
      <div
        className={cn(
          "flex items-center justify-center w-10 h-7 rounded-md shrink-0",
          method.isDefault ? "bg-accent/10" : "bg-muted",
        )}
      >
        <CreditCard
          className={cn(
            "size-4",
            method.isDefault ? "text-accent" : "text-muted-foreground",
          )}
        />
      </div>

      {/* Card info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span data-slot="list-row-label" className="text-foreground">
            {cardBrandLabels[method.type]} ending in {method.last4}
          </span>
          {method.isDefault && <Badge variant="accent">Default</Badge>}
        </div>
        <span data-slot="list-row-subtitle" className="text-muted-foreground">
          Expires {method.expiry}
        </span>
      </div>

      {/* Actions */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="flex items-center justify-center p-1.5 rounded-lg text-muted-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50"
            aria-label="Payment method options"
          >
            <MoreHorizontal className="size-4" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-[160px]">
          <DropdownMenuItem
            onClick={() => toast.success("Edit card dialog opened (mock)")}
          >
            <Pencil className="size-4" />
            Edit
          </DropdownMenuItem>
          {!method.isDefault && (
            <DropdownMenuItem onClick={onSetDefault}>
              <CheckCircle2 className="size-4" />
              Set as default
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={onRemove}>
            <Trash2 className="size-4" />
            Remove
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

// ── Main pane ───────────────────────────────────────────────────────

export function BillingPane() {
  const { user } = useAuth();
  const posthog = usePostHog();
  const [methods, setMethods] = useState(initialPaymentMethods);

  if (!user) return null;

  const planLabel = user.plan.charAt(0).toUpperCase() + user.plan.slice(1);
  const planPrice =
    user.plan === "free" ? "$0" : user.plan === "pro" ? "$29" : "Custom";

  function handleSetDefault(id: string) {
    setMethods((prev) => prev.map((m) => ({ ...m, isDefault: m.id === id })));
    const card = methods.find((m) => m.id === id);
    toast.success("Default payment method updated", {
      description: `${cardBrandLabels[card?.type ?? "visa"]} ending in ${card?.last4} is now your default.`,
    });
  }

  function handleRemove(id: string) {
    const card = methods.find((m) => m.id === id);
    if (card?.isDefault) {
      toast.error("Cannot remove default payment method", {
        description: "Set another card as default first.",
      });
      return;
    }
    setMethods((prev) => prev.filter((m) => m.id !== id));
    toast.success("Payment method removed", {
      description: `${cardBrandLabels[card?.type ?? "visa"]} ending in ${card?.last4} has been removed.`,
    });
  }

  return (
    <div>
      {/* Current plan summary */}
      <SettingsRow
        label="Current Plan"
        description={`${planPrice}/month — billed monthly`}
      >
        <Badge variant="accent">{planLabel}</Badge>
      </SettingsRow>

      {/* Payment methods */}
      <div className="py-4 border-b border-border-subtle">
        <div className="flex items-center justify-between mb-3">
          <span data-slot="settings-row-label" className="text-foreground">
            Payment Methods
          </span>
          <Button
            variant="ghost"
            className="gap-1.5 h-auto py-1 px-2"
            onClick={() => {
              // PostHog: Capture payment method addition initiated
              posthog?.capture("payment_method_added", {
                current_plan: user.plan,
              });
              toast.success("Add payment method dialog opened (mock)", {
                description:
                  "You can add Visa, Mastercard, or American Express.",
              });
            }}
          >
            <Plus className="size-3.5" />
            <span style={typo.caption}>Add</span>
          </Button>
        </div>
        <div className="space-y-2">
          {methods.map((m) => (
            <PaymentMethodCard
              key={m.id}
              method={m}
              onSetDefault={() => handleSetDefault(m.id)}
              onRemove={() => handleRemove(m.id)}
            />
          ))}
          {methods.length === 0 && (
            <div className="py-6 text-center">
              <CreditCard className="size-8 text-muted-foreground mx-auto mb-2" />
              <p className="text-muted-foreground" style={typo.caption}>
                No payment methods on file
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Invoice history */}
      <div className="py-4">
        <span
          data-slot="settings-row-label"
          className="text-foreground block mb-3"
        >
          Invoice History
        </span>

        <div className="space-y-0">
          {/* Header row */}
          <div className="flex items-center gap-3 pb-2 border-b border-border-subtle">
            <span className="text-muted-foreground flex-1" style={typo.helper}>
              Description
            </span>
            <span
              className="text-muted-foreground w-[80px] text-right"
              style={typo.helper}
            >
              Amount
            </span>
            <span
              className="text-muted-foreground w-[72px] text-right"
              style={typo.helper}
            >
              Status
            </span>
            <span className="w-8" aria-hidden="true" />
          </div>

          {/* Invoice rows */}
          {mockInvoices.map((inv) => (
            <ListRow
              key={inv.id}
              label={
                <span className="truncate" style={typo.caption}>
                  {inv.description}
                </span>
              }
              subtitle={inv.date}
              trailing={
                <div className="flex items-center gap-3 shrink-0">
                  <span
                    className="text-foreground w-[80px] text-right"
                    style={typo.caption}
                  >
                    {inv.amount}
                  </span>
                  <span className="w-[72px] flex justify-end">
                    <InvoiceStatusBadge status={inv.status} />
                  </span>
                  <button
                    className="flex items-center justify-center p-1 rounded-lg text-muted-foreground hover:bg-muted transition-colors w-8 shrink-0 focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50"
                    aria-label={`Download invoice ${inv.id}`}
                    onClick={() => {
                      // PostHog: Capture invoice download
                      posthog?.capture("invoice_downloaded", {
                        invoice_id: inv.id,
                        invoice_amount: inv.amount,
                        invoice_status: inv.status,
                      });
                      toast.success("Invoice downloaded", {
                        description: `${inv.description} — ${inv.amount}`,
                      });
                    }}
                  >
                    <Download className="size-3.5" />
                  </button>
                </div>
              }
            />
          ))}
        </div>
      </div>
    </div>
  );
}

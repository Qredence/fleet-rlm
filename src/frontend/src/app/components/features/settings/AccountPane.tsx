/**
 * Account settings pane — profile management + team members.
 */
import { useState } from "react";
import { toast } from "sonner";
import {
  Crown,
  Shield,
  ShieldCheck,
  MoreHorizontal,
  UserPlus,
  Trash2,
} from "lucide-react";
import { typo } from "../../config/typo";
import { cn } from "../../ui/utils";
import { SettingsRow } from "../../shared/SettingsRow";
import { useAuth } from "../../hooks/useAuth";
import { Avatar, AvatarFallback } from "../../ui/avatar";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Input } from "../../ui/input";
import { Label } from "../../ui/label";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../ui/dropdown-menu";
import { ListRow } from "../../shared/ListRow";

// ── Mock team data ──────────────────────────────────────────────────

interface TeamMember {
  id: string;
  name: string;
  email: string;
  initials: string;
  role: "owner" | "admin" | "editor" | "viewer";
  status: "active" | "pending";
}

const roleConfig: Record<
  TeamMember["role"],
  {
    label: string;
    variant: "accent" | "secondary" | "outline";
    icon: typeof Crown;
  }
> = {
  owner: { label: "Owner", variant: "accent", icon: Crown },
  admin: { label: "Admin", variant: "accent", icon: ShieldCheck },
  editor: { label: "Editor", variant: "secondary", icon: Shield },
  viewer: { label: "Viewer", variant: "outline", icon: Shield },
};

const initialTeamMembers: TeamMember[] = [
  {
    id: "usr_01",
    name: "Alex Chen",
    email: "alex@qredence.ai",
    initials: "AC",
    role: "owner",
    status: "active",
  },
  {
    id: "usr_02",
    name: "Sarah Kim",
    email: "sarah@qredence.ai",
    initials: "SK",
    role: "admin",
    status: "active",
  },
  {
    id: "usr_03",
    name: "Marcus Rodriguez",
    email: "marcus@qredence.ai",
    initials: "MR",
    role: "editor",
    status: "active",
  },
  {
    id: "usr_04",
    name: "Priya Patel",
    email: "priya@qredence.ai",
    initials: "PP",
    role: "editor",
    status: "active",
  },
  {
    id: "usr_05",
    name: "Jordan Lee",
    email: "jordan@qredence.ai",
    initials: "JL",
    role: "viewer",
    status: "pending",
  },
];

// ── Member row ──────────────────────────────────────────────────────

function TeamMemberRow({
  member,
  isCurrentUser,
  onChangeRole,
  onRemove,
}: {
  member: TeamMember;
  isCurrentUser: boolean;
  onChangeRole: (role: TeamMember["role"]) => void;
  onRemove: () => void;
}) {
  const cfg = roleConfig[member.role];
  const RoleIcon = cfg.icon;

  return (
    <ListRow
      leading={
        <Avatar className="size-8">
          <AvatarFallback
            className={cn(
              member.status === "pending"
                ? "bg-muted text-muted-foreground"
                : "bg-accent/10 text-accent",
            )}
            style={{
              fontSize: "var(--text-helper)",
              fontWeight: "var(--font-weight-medium)",
              fontFamily: "var(--font-family)",
            }}
          >
            {member.initials}
          </AvatarFallback>
        </Avatar>
      }
      label={
        <span className="flex items-center gap-2">
          <span className="truncate">{member.name}</span>
          {isCurrentUser && (
            <span
              className="text-muted-foreground shrink-0"
              style={typo.helper}
            >
              (you)
            </span>
          )}
          {member.status === "pending" && (
            <Badge variant="warning" className="shrink-0">
              Pending
            </Badge>
          )}
        </span>
      }
      subtitle={member.email}
      trailing={
        <div className="flex items-center gap-2 shrink-0">
          {/* Role badge */}
          <Badge variant={cfg.variant} className="gap-1 shrink-0">
            <RoleIcon className="size-3" />
            {cfg.label}
          </Badge>

          {/* Actions (disabled for owner / self) */}
          {!isCurrentUser && member.role !== "owner" ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className="flex items-center justify-center p-1.5 rounded-lg text-muted-foreground hover:bg-muted transition-colors focus-visible:outline-none focus-visible:ring-[2px] focus-visible:ring-ring/50"
                  aria-label={`Options for ${member.name}`}
                >
                  <MoreHorizontal className="size-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-[160px]">
                <DropdownMenuItem
                  onClick={() => onChangeRole("admin")}
                  disabled={member.role === "admin"}
                >
                  <ShieldCheck className="size-4" />
                  Make Admin
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => onChangeRole("editor")}
                  disabled={member.role === "editor"}
                >
                  <Shield className="size-4" />
                  Make Editor
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => onChangeRole("viewer")}
                  disabled={member.role === "viewer"}
                >
                  <Shield className="size-4" />
                  Make Viewer
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive" onClick={onRemove}>
                  <Trash2 className="size-4" />
                  Remove
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            /* Spacer for alignment */
            <span className="w-8 shrink-0" aria-hidden="true" />
          )}
        </div>
      }
    />
  );
}

// ── Invite form ─────────────────────────────────────────────────────

function InviteForm({ onInvite }: { onInvite: (email: string) => void }) {
  const [email, setEmail] = useState("");

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    onInvite(email.trim());
    setEmail("");
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-end gap-2">
      <div className="flex-1 space-y-1.5">
        <Label htmlFor="invite-email">Invite by email</Label>
        <Input
          id="invite-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="colleague@company.com"
        />
      </div>
      <Button
        type="submit"
        variant="secondary"
        className="gap-1.5 shrink-0"
        disabled={!email.trim()}
      >
        <UserPlus className="size-3.5" />
        <span style={typo.label}>Invite</span>
      </Button>
    </form>
  );
}

// ── Main pane ───────────────────────────────────────────────────────

export function AccountPane() {
  const { user } = useAuth();
  const [members, setMembers] = useState(initialTeamMembers);

  if (!user) return null;

  const planLabel = user.plan.charAt(0).toUpperCase() + user.plan.slice(1);

  function handleChangeRole(memberId: string, newRole: TeamMember["role"]) {
    setMembers((prev) =>
      prev.map((m) => (m.id === memberId ? { ...m, role: newRole } : m)),
    );
    const member = members.find((m) => m.id === memberId);
    const roleLabel = roleConfig[newRole].label;
    toast.success(`Role updated for ${member?.name}`, {
      description: `${member?.name} is now ${roleLabel === "Admin" ? "an" : "a"} ${roleLabel}.`,
    });
  }

  function handleRemoveMember(memberId: string) {
    const member = members.find((m) => m.id === memberId);
    setMembers((prev) => prev.filter((m) => m.id !== memberId));
    toast.success(`${member?.name} removed from team`, {
      description: `${member?.email} will no longer have access to the workspace.`,
    });
  }

  function handleInvite(email: string) {
    const localPart = email.split("@")[0] ?? "";
    const initials = localPart
      .split(/[._-]/)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase() ?? "")
      .join("");

    const newMember: TeamMember = {
      id: `usr_${Date.now()}`,
      name: localPart || email,
      email,
      initials: initials || "NN",
      role: "viewer",
      status: "pending",
    };
    setMembers((prev) => [...prev, newMember]);
    toast.success("Invitation sent", {
      description: `An invite has been sent to ${email}. They'll appear as pending until they accept.`,
    });
  }

  return (
    <div>
      {/* Profile header */}
      <ListRow
        leading={
          <Avatar className="size-12">
            <AvatarFallback
              className="bg-accent/10 text-accent"
              style={typo.label}
            >
              {user.initials}
            </AvatarFallback>
          </Avatar>
        }
        label={user.name}
        subtitle={user.email}
        trailing={<Badge variant="accent">{planLabel}</Badge>}
        className="py-4"
      />

      {/* Name */}
      <div className="py-4 border-b border-border-subtle space-y-1.5">
        <Label htmlFor="settings-name">Display Name</Label>
        <Input
          id="settings-name"
          defaultValue={user.name}
          onBlur={() => toast.success("Display name updated (mock)")}
        />
      </div>

      {/* Email */}
      <div className="py-4 border-b border-border-subtle space-y-1.5">
        <Label htmlFor="settings-email">Email</Label>
        <Input
          id="settings-email"
          type="email"
          defaultValue={user.email}
          onBlur={() => toast.success("Email updated (mock)")}
        />
      </div>

      {/* Organization */}
      <SettingsRow label="Organization" description={user.org}>
        <Badge variant="secondary">{user.role}</Badge>
      </SettingsRow>

      {/* ── Team Members ─────────────────────────────────────────── */}
      <div className="py-4 border-b border-border-subtle">
        <div className="flex items-center justify-between mb-3">
          <div>
            <span
              data-slot="settings-row-label"
              className="text-foreground block"
            >
              Team Members
            </span>
            <span
              data-slot="list-row-subtitle"
              className="text-muted-foreground"
            >
              {members.length} member{members.length !== 1 ? "s" : ""} ·{" "}
              {members.filter((m) => m.status === "pending").length} pending
            </span>
          </div>
        </div>

        {/* Invite form */}
        <div className="mb-4">
          <InviteForm onInvite={handleInvite} />
        </div>

        {/* Member list */}
        <div>
          {members.map((member) => (
            <TeamMemberRow
              key={member.id}
              member={member}
              isCurrentUser={member.id === user.id}
              onChangeRole={(role) => handleChangeRole(member.id, role)}
              onRemove={() => handleRemoveMember(member.id)}
            />
          ))}
        </div>
      </div>

      {/* Change password */}
      <SettingsRow label="Password" description="Change your account password.">
        <Button
          variant="outline"
          className="rounded-lg shrink-0"
          onClick={() => toast.success("Password change dialog opened (mock)")}
        >
          <span style={typo.label}>Change Password</span>
        </Button>
      </SettingsRow>

      {/* Danger zone */}
      <SettingsRow label="Danger Zone" noBorder className="items-start">
        <Button
          variant="destructive-ghost"
          className="rounded-lg shrink-0"
          onClick={() =>
            toast.error("Account deletion requested (mock)", {
              description: "This action cannot be undone.",
            })
          }
        >
          <span style={typo.label}>Delete Account</span>
        </Button>
      </SettingsRow>
    </div>
  );
}

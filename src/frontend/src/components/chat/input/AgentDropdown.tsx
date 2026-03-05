import type { ReactNode } from "react";
import {
  Check,
  ChevronDown,
  Diamond,
  Orbit,
  Sparkles,
  Sun,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/components/ui/utils";

interface Agent {
  id: string;
  name: string;
  icon: ReactNode;
  beta?: boolean;
  supported?: boolean;
}

const AUTO_AGENT: Agent = {
  id: "auto",
  name: "Auto",
  icon: <Sparkles className="h-4 w-4" />,
  supported: true,
};

const routingAgents: Agent[] = [
  AUTO_AGENT,
  {
    id: "routing-sonnet-4.6",
    name: "Sonnet 4.6",
    icon: <Sun className="h-4 w-4" />,
    beta: true,
  },
  {
    id: "routing-opus-4.6",
    name: "Opus 4.6",
    icon: <Sun className="h-4 w-4" />,
    beta: true,
  },
  {
    id: "routing-gemini-3.1-pro",
    name: "Gemini 3.1 Pro",
    icon: <Diamond className="h-4 w-4" />,
    beta: true,
  },
  {
    id: "routing-gpt-5.2",
    name: "GPT-5.2",
    icon: <Orbit className="h-4 w-4" />,
    beta: true,
  },
];

const directAgents: Agent[] = [
  {
    id: "direct-sonnet-4.6",
    name: "Sonnet 4.6",
    icon: <Sun className="h-4 w-4" />,
    beta: true,
  },
  {
    id: "direct-opus-4.6",
    name: "Opus 4.6",
    icon: <Sun className="h-4 w-4" />,
    beta: true,
  },
  {
    id: "direct-gemini-2.5-flash",
    name: "Gemini 2.5 Flash",
    icon: <Diamond className="h-4 w-4" />,
  },
  {
    id: "direct-gemini-3.1-pro",
    name: "Gemini 3.1 Pro",
    icon: <Diamond className="h-4 w-4" />,
    beta: true,
  },
  {
    id: "direct-gpt-5.2",
    name: "GPT-5.2",
    icon: <Orbit className="h-4 w-4" />,
    beta: true,
  },
];

const allAgents = [...routingAgents, ...directAgents];

interface AgentDropdownProps {
  selectedAgent: string;
  onAgentChange: (agentId: string) => void;
}

function AgentDropdown({ selectedAgent, onAgentChange }: AgentDropdownProps) {
  const currentAgent =
    allAgents.find((agent) => agent.id === selectedAgent) ?? AUTO_AGENT;

  const handleSelect = (agent: Agent) => {
    if (agent.supported) {
      onAgentChange(agent.id);
      return;
    }

    toast.info("Model-specific routing isn’t available yet", {
      description:
        "This build currently uses backend runtime defaults. Select Auto for now.",
    });
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1 h-7 px-2.5 rounded-lg text-muted-foreground text-sm hover:text-foreground hover:bg-accent/50 transition-colors"
        >
          <span>{currentAgent.name}</span>
          <ChevronDown className="h-3 w-3 opacity-60" />
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        className="w-56 border-border bg-popover"
      >
        {routingAgents.map((agent) => (
          <DropdownMenuItem
            key={agent.id}
            onClick={() => handleSelect(agent)}
            className={cn(
              "flex items-center justify-between gap-1.5 py-2 px-2.5 rounded-md cursor-pointer text-xs",
              selectedAgent === agent.id && "bg-accent",
            )}
          >
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">{agent.icon}</span>
              <span className="text-xs font-medium">{agent.name}</span>
              {agent.beta ? (
                <Badge
                  variant="secondary"
                  className="h-4 px-1 text-[9px] font-medium"
                >
                  Beta
                </Badge>
              ) : null}
              {!agent.supported ? (
                <Badge
                  variant="secondary"
                  className="h-4 px-1 text-[9px] font-medium"
                >
                  Soon
                </Badge>
              ) : null}
            </div>
            {selectedAgent === agent.id ? (
              <Check className="h-3.5 w-3.5 text-primary" />
            ) : null}
          </DropdownMenuItem>
        ))}

        <DropdownMenuLabel className="px-2.5 py-1.5 text-[10px] font-medium text-muted-foreground">
          Chat directly with models
        </DropdownMenuLabel>

        {directAgents.map((agent) => (
          <DropdownMenuItem
            key={agent.id}
            onClick={() => handleSelect(agent)}
            className={cn(
              "flex items-center justify-between gap-1.5 py-2 px-2.5 rounded-md cursor-pointer text-xs",
              selectedAgent === agent.id && "bg-accent",
            )}
          >
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">{agent.icon}</span>
              <span className="text-xs font-medium">{agent.name}</span>
              {agent.beta ? (
                <Badge
                  variant="secondary"
                  className="h-4 px-1 text-[9px] font-medium"
                >
                  Beta
                </Badge>
              ) : null}
              <Badge
                variant="secondary"
                className="h-4 px-1 text-[9px] font-medium"
              >
                Soon
              </Badge>
            </div>
            {selectedAgent === agent.id ? (
              <Check className="h-3.5 w-3.5 text-primary" />
            ) : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export { AgentDropdown };

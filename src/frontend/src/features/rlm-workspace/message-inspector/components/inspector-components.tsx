import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Streamdown } from "@/components/ui/streamdown";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  ExecutionSection,
  ToolSessionItem,
} from "@/features/rlm-workspace/assistant-content/types";
import {
  inspectorStyles,
  inspectorInsetClass,
} from "@/features/rlm-workspace/shared/inspector-styles";
import {
  runtimeContextStrings,
  statusTone,
  stringifyValue,
  toolSessionItemState,
} from "../utils/inspector-utils";

export function renderBadges(
  values: string[],
  variant: "outline" | "secondary" | "warning" | "success" | "accent" = "outline",
) {
  if (values.length === 0) return null;
  return (
    <div className={inspectorStyles.badge.row}>
      {values.map((value) => (
        <Badge key={value} variant={variant} className={inspectorStyles.badge.meta}>
          {value}
        </Badge>
      ))}
    </div>
  );
}

export function ExternalAnchor({ href, children }: { href?: string; children: React.ReactNode }) {
  if (!href) {
    return <span className="text-foreground">{children}</span>;
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-foreground hover:text-accent"
    >
      <span>{children}</span>
      <ExternalLink className="size-3.5" />
    </a>
  );
}

export function DetailBlock({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value?: string;
  tone?: "default" | "error";
}) {
  if (!value) return null;
  return (
    <div className="space-y-1.5">
      <div className={inspectorStyles.heading.detail}>{label}</div>
      <div className={inspectorInsetClass(tone === "error" ? "error" : "strong")}>
        <Streamdown content={value} streaming={false} />
      </div>
    </div>
  );
}

export function ToolSessionDetails({ sessionItems }: { sessionItems: ToolSessionItem[] }) {
  return (
    <div className={inspectorStyles.stack.cards}>
      {sessionItems.map((item) => {
        const badges = runtimeContextStrings(item.runtimeContext);
        const state = toolSessionItemState(item);
        const tone = statusTone(state);
        const outputValue =
          item.part.kind === "tool"
            ? stringifyValue(item.part.output)
            : item.part.kind === "sandbox"
              ? item.part.output
              : "";
        const inputValue = item.part.kind === "tool" ? stringifyValue(item.part.input) : "";
        const codeValue = item.part.kind === "sandbox" ? item.part.code : "";

        return (
          <Card key={item.key} className={inspectorStyles.card.root}>
            <CardHeader className={inspectorStyles.card.header}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  <CardTitle className="text-sm font-medium text-foreground">
                    {item.part.kind === "status_note"
                      ? `Status: ${item.part.text}`
                      : `${item.eventKind.replace("_", " ")}: ${item.toolName ?? "tool"}`}
                  </CardTitle>
                </div>
                <Badge variant={tone.variant} className={inspectorStyles.badge.status}>
                  {tone.label}
                </Badge>
              </div>
              {renderBadges(badges)}
            </CardHeader>
            <CardContent className={inspectorStyles.card.contentStack}>
              <DetailBlock label="Input" value={inputValue} />
              <DetailBlock
                label="Output"
                value={outputValue}
                tone={
                  item.part.kind !== "sandbox"
                    ? "default"
                    : item.part.errorText
                      ? "error"
                      : "default"
                }
              />
              <DetailBlock
                label="Error"
                value={
                  item.part.kind === "tool" || item.part.kind === "sandbox"
                    ? item.part.errorText
                    : undefined
                }
                tone="error"
              />
              <DetailBlock label="Code" value={codeValue} />
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

export function renderExecutionSectionDetails(section: ExecutionSection) {
  switch (section.kind) {
    case "tool_session":
      return <ToolSessionDetails sessionItems={section.session.items} />;
    case "queue":
      return (
        <div className={inspectorStyles.stack.compact}>
          {section.part.items.map((item) => (
            <div key={item.id} className={inspectorInsetClass("strong")}>
              <div className="text-sm font-medium text-foreground">{item.label}</div>
              {item.description ? (
                <div className="mt-1 text-sm text-muted-foreground">{item.description}</div>
              ) : null}
            </div>
          ))}
        </div>
      );
    case "task":
      return (
        <div className={inspectorStyles.stack.compact}>
          {(section.part.items ?? []).map((item) => (
            <div key={item.id} className={inspectorInsetClass("strong")}>
              <div className="text-sm text-foreground">{item.text}</div>
            </div>
          ))}
        </div>
      );
    case "tool":
      return (
        <div className={inspectorStyles.stack.cards}>
          <DetailBlock label="Input" value={stringifyValue(section.part.input)} />
          <DetailBlock
            label="Output"
            value={stringifyValue(section.part.output)}
            tone={section.part.errorText ? "error" : "default"}
          />
          <DetailBlock label="Error" value={section.part.errorText} tone="error" />
          {renderBadges(runtimeContextStrings(section.part.runtimeContext))}
        </div>
      );
    case "sandbox":
      return (
        <div className={inspectorStyles.stack.cards}>
          <DetailBlock label="Code" value={section.part.code} />
          <DetailBlock
            label="Output"
            value={section.part.output}
            tone={section.part.errorText ? "error" : "default"}
          />
          <DetailBlock label="Error" value={section.part.errorText} tone="error" />
          {renderBadges(runtimeContextStrings(section.part.runtimeContext))}
        </div>
      );
    case "environment_variables":
      return (
        <div className={inspectorStyles.stack.compact}>
          {section.part.variables.map((variable) => (
            <div key={variable.name} className={inspectorInsetClass("strong")}>
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-foreground">{variable.name}</span>
                {variable.required ? (
                  <Badge variant="secondary" className={inspectorStyles.badge.meta}>
                    required
                  </Badge>
                ) : null}
              </div>
              <div className="mt-1 text-sm text-muted-foreground">{variable.value}</div>
            </div>
          ))}
        </div>
      );
    case "status_note":
      return (
        <div className={inspectorStyles.stack.cards}>
          <DetailBlock
            label="Status"
            value={section.part.text}
            tone={section.part.tone === "error" ? "error" : "default"}
          />
          {renderBadges(runtimeContextStrings(section.part.runtimeContext))}
        </div>
      );
  }
}

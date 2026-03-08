import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import { Copy, Eye, EyeOff } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils/cn";

type CopyFormat = "value" | "export";

interface EnvCtxValue {
  showValues: boolean;
  setShowValues: (next: boolean) => void;
}
const EnvCtx = createContext<EnvCtxValue | null>(null);

function EnvironmentVariables({
  showValues,
  defaultShowValues = false,
  onShowValuesChange,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  showValues?: boolean;
  defaultShowValues?: boolean;
  onShowValuesChange?: (show: boolean) => void;
}) {
  const [internalShow, setInternalShow] = useState(defaultShowValues);
  const resolvedShow = showValues ?? internalShow;
  const setShowValues = useCallback(
    (next: boolean) => {
      if (showValues == null) setInternalShow(next);
      onShowValuesChange?.(next);
    },
    [showValues, onShowValuesChange],
  );
  const value = useMemo(
    () => ({ showValues: resolvedShow, setShowValues }),
    [resolvedShow, setShowValues],
  );
  return (
    <EnvCtx.Provider value={value}>
      <div
        className={cn(
          "rounded-lg border-subtle bg-card",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    </EnvCtx.Provider>
  );
}

function EnvironmentVariablesHeader(
  props: React.HTMLAttributes<HTMLDivElement>,
) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 px-3 py-2 border-b border-border-subtle",
        props.className,
      )}
      {...props}
    />
  );
}
function EnvironmentVariablesTitle(
  props: React.HTMLAttributes<HTMLHeadingElement>,
) {
  return (
    <h3
      className={cn("text-sm font-medium text-foreground", props.className)}
      {...props}
    />
  );
}
function EnvironmentVariablesToggle(
  props: React.ComponentProps<typeof Switch>,
) {
  const ctx = useContext(EnvCtx);
  return (
    <label className="inline-flex items-center gap-2 text-xs text-muted-foreground">
      {ctx?.showValues ? (
        <Eye className="size-3.5" />
      ) : (
        <EyeOff className="size-3.5" />
      )}
      <span>Show values</span>
      <Switch
        checked={ctx?.showValues}
        onCheckedChange={(v) => ctx?.setShowValues(!!v)}
        {...props}
      />
    </label>
  );
}
function EnvironmentVariablesContent(
  props: React.HTMLAttributes<HTMLDivElement>,
) {
  return (
    <div
      className={cn("divide-y divide-border-subtle", props.className)}
      {...props}
    />
  );
}

interface VarRowCtx {
  name: string;
  value: string;
  required?: boolean;
}
const VarRowContext = createContext<VarRowCtx | null>(null);

function EnvironmentVariable({
  name,
  value,
  required,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  name: string;
  value: string;
  required?: boolean;
}) {
  return (
    <VarRowContext.Provider value={{ name, value, required }}>
      <div className={cn("px-3 py-2", className)} {...props}>
        {children}
      </div>
    </VarRowContext.Provider>
  );
}
function EnvironmentVariableGroup(props: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex items-start justify-between gap-3", props.className)}
      {...props}
    />
  );
}
function EnvironmentVariableName(props: React.HTMLAttributes<HTMLSpanElement>) {
  const row = useContext(VarRowContext);
  return (
    <span
      className={cn("font-mono text-xs text-foreground", props.className)}
      {...props}
    >
      {props.children ?? row?.name}
    </span>
  );
}
function EnvironmentVariableValue(
  props: React.HTMLAttributes<HTMLSpanElement>,
) {
  const row = useContext(VarRowContext);
  const env = useContext(EnvCtx);
  const raw = String(row?.value ?? "");
  const masked =
    raw.length <= 4 ? "••••" : `${raw.slice(0, 2)}••••${raw.slice(-2)}`;
  return (
    <span
      className={cn(
        "font-mono text-xs text-muted-foreground break-all",
        props.className,
      )}
      {...props}
    >
      {props.children ?? (env?.showValues ? raw : masked)}
    </span>
  );
}
function EnvironmentVariableCopyButton({
  copyFormat = "value",
  onCopy,
  onError,
  timeout: _timeout = 2000,
  className,
  ...props
}: React.ComponentProps<typeof Button> & {
  copyFormat?: CopyFormat;
  onCopy?: () => void;
  onError?: (error: Error) => void;
  timeout?: number;
}) {
  const row = useContext(VarRowContext);
  const copyText =
    copyFormat === "export"
      ? `export ${row?.name}="${row?.value ?? ""}"`
      : String(row?.value ?? "");
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className={cn("size-7", className)}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(copyText);
          onCopy?.();
        } catch (e) {
          onError?.(e as Error);
        }
      }}
      {...props}
    >
      <Copy className="size-3.5" />
    </Button>
  );
}
function EnvironmentVariableRequired(
  props: React.ComponentProps<typeof Badge>,
) {
  return (
    <Badge
      variant="secondary"
      className={cn("text-[10px]", props.className)}
      {...props}
    >
      {props.children ?? "Required"}
    </Badge>
  );
}

export {
  EnvironmentVariables,
  EnvironmentVariablesHeader,
  EnvironmentVariablesTitle,
  EnvironmentVariablesToggle,
  EnvironmentVariablesContent,
  EnvironmentVariable,
  EnvironmentVariableGroup,
  EnvironmentVariableName,
  EnvironmentVariableValue,
  EnvironmentVariableCopyButton,
  EnvironmentVariableRequired,
};

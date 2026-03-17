import * as React from "react";
import { Accordion as AccordionPrimitive } from "@base-ui/react/accordion";
import { ChevronDownIcon } from "lucide-react";

import { cn } from "@/lib/utils/cn";

function Accordion({
  type = "multiple",
  collapsible = false,
  value,
  defaultValue,
  onValueChange,
  ...props
}: Omit<
  React.ComponentProps<typeof AccordionPrimitive.Root>,
  "defaultValue" | "multiple" | "onValueChange" | "value"
> & {
  type?: "multiple" | "single";
  collapsible?: boolean;
  value?: string | string[];
  defaultValue?: string | string[];
  onValueChange?: (
    value: string | string[] | undefined,
    eventDetails: Parameters<
      NonNullable<React.ComponentProps<typeof AccordionPrimitive.Root>["onValueChange"]>
    >[1],
  ) => void;
}) {
  const controlledSingleValue = typeof value === "string" ? value : value?.[0];
  const defaultSingleValue =
    typeof defaultValue === "string" ? defaultValue : defaultValue?.[0];
  const firstItemValue = React.useMemo(() => {
    const items = React.Children.toArray(props.children);

    for (const item of items) {
      if (
        React.isValidElement<{ value?: string }>(item) &&
        typeof item.props.value === "string"
      ) {
        return item.props.value;
      }
    }

    return undefined;
  }, [props.children]);
  const [uncontrolledSingleValue, setUncontrolledSingleValue] = React.useState<
    string | undefined
  >(defaultSingleValue ?? (!collapsible ? firstItemValue : undefined));
  const singleValue =
    controlledSingleValue !== undefined
      ? controlledSingleValue
      : uncontrolledSingleValue ?? (!collapsible ? firstItemValue : undefined);

  if (type === "single") {
    return (
      <AccordionPrimitive.Root
        data-slot="accordion"
        defaultValue={defaultSingleValue ? [defaultSingleValue] : []}
        multiple={false}
        onValueChange={(nextValue, eventDetails) => {
          const nextSingleValue = nextValue[0];
          const resolvedValue =
            nextSingleValue ??
            (collapsible ? undefined : singleValue ?? firstItemValue);

          if (controlledSingleValue === undefined) {
            setUncontrolledSingleValue(resolvedValue);
          }

          onValueChange?.(resolvedValue, eventDetails);
        }}
        value={singleValue ? [singleValue] : []}
        {...props}
      />
    );
  }

  return (
    <AccordionPrimitive.Root
      data-slot="accordion"
      defaultValue={Array.isArray(defaultValue) ? defaultValue : []}
      multiple
      onValueChange={(nextValue, eventDetails) => {
        onValueChange?.(nextValue, eventDetails);
      }}
      value={Array.isArray(value) ? value : undefined}
      {...props}
    />
  );
}

function AccordionItem({
  className,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Item>) {
  return (
    <AccordionPrimitive.Item
      data-slot="accordion-item"
      className={cn("border-b border-border-subtle last:border-b-0", className)}
      {...props}
    />
  );
}

function AccordionTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Trigger>) {
  return (
    <AccordionPrimitive.Header className="flex">
      <AccordionPrimitive.Trigger
        data-slot="accordion-trigger"
        className={cn(
          "focus-visible:border-ring focus-visible:ring-ring/50 flex flex-1 items-start justify-between gap-4 rounded-md py-4 text-left transition-[color,box-shadow] outline-none hover:underline focus-visible:ring-[3px] disabled:pointer-events-none disabled:opacity-50 [&[data-state=open]>svg]:rotate-180",
          className,
        )}
        {...props}
      >
        {children}
        <ChevronDownIcon className="text-muted-foreground pointer-events-none size-4 shrink-0 translate-y-0.5 transition-transform duration-200" />
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  );
}

function AccordionContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Panel>) {
  return (
    <AccordionPrimitive.Panel
      data-slot="accordion-content"
      className="data-[state=closed]:animate-accordion-up data-[state=open]:animate-accordion-down overflow-hidden"
      {...props}
    >
      <div className={cn("pt-0 pb-4", className)}>{children}</div>
    </AccordionPrimitive.Panel>
  );
}

export { Accordion, AccordionItem, AccordionTrigger, AccordionContent };

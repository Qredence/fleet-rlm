import type * as React from "react";
import { Accordion as BaseAccordion } from "@base-ui/react";
import { ChevronDownIcon } from "lucide-react";

import { cn } from "@/lib/utils";

type AccordionRootProps = React.ComponentProps<typeof BaseAccordion.Root>;
type AccordionProps = Omit<
  AccordionRootProps,
  "type" | "defaultValue" | "value" | "onValueChange"
> & {
  type?: "single" | "multiple";
  collapsible?: boolean;
  defaultValue?: unknown;
  value?: unknown;
  onValueChange?: unknown;
};

function Accordion({ type, collapsible, ...props }: AccordionProps) {
  const accordionProps = { type, collapsible, ...props } as AccordionRootProps;

  return <BaseAccordion.Root {...accordionProps} />;
}

function AccordionItem({
  className,
  ...props
}: React.ComponentProps<typeof BaseAccordion.Item>) {
  return (
    <BaseAccordion.Item
      className={cn("border-b border-border-subtle last:border-b-0", className)}
      {...props}
    />
  );
}

function AccordionTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof BaseAccordion.Trigger>) {
  return (
    <BaseAccordion.Header className="flex">
      <BaseAccordion.Trigger
        className={cn(
          "focus-visible:border-ring focus-visible:ring-ring/50 flex flex-1 items-start justify-between gap-4 rounded-md py-4 text-left transition-[color,box-shadow] outline-none hover:underline focus-visible:ring-[3px] disabled:pointer-events-none disabled:opacity-50 [&[data-open]>svg]:rotate-180",
          className,
        )}
        {...props}
      >
        {children}
        <ChevronDownIcon
          className="text-muted-foreground pointer-events-none size-5 shrink-0 translate-y-0.5 transition-transform duration-200"
          strokeWidth={1.5}
        />
      </BaseAccordion.Trigger>
    </BaseAccordion.Header>
  );
}

function AccordionContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof BaseAccordion.Panel>) {
  return (
    <BaseAccordion.Panel
      className="data-ending-style:animate-accordion-up data-starting-style:animate-accordion-down overflow-hidden transition-all"
      {...props}
    >
      <div className={cn("pt-0 pb-4", className)}>{children}</div>
    </BaseAccordion.Panel>
  );
}

export { Accordion, AccordionItem, AccordionTrigger, AccordionContent };

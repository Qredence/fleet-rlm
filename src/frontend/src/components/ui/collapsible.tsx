import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible"

import { withAsChild } from "@/lib/base-ui/as-child"

function Collapsible(
  props: React.ComponentProps<typeof CollapsiblePrimitive.Root> & {
    asChild?: boolean
  }
) {
  const { children, props: rootProps, render } = withAsChild(props)
  return (
    <CollapsiblePrimitive.Root data-slot="collapsible" render={render} {...rootProps}>
      {children}
    </CollapsiblePrimitive.Root>
  )
}

function CollapsibleTrigger(
  props: React.ComponentProps<typeof CollapsiblePrimitive.Trigger> & {
    asChild?: boolean
  }
) {
  const { children, props: triggerProps, render } = withAsChild(props)
  return (
    <CollapsiblePrimitive.Trigger
      data-slot="collapsible-trigger"
      render={render}
      {...triggerProps}
    >
      {children}
    </CollapsiblePrimitive.Trigger>
  )
}

function CollapsibleContent(
  props: React.ComponentProps<typeof CollapsiblePrimitive.Panel> & {
    asChild?: boolean
  }
) {
  const { children, props: panelProps, render } = withAsChild(props)
  return (
    <CollapsiblePrimitive.Panel
      data-slot="collapsible-content"
      render={render}
      {...panelProps}
    >
      {children}
    </CollapsiblePrimitive.Panel>
  )
}

export { Collapsible, CollapsibleTrigger, CollapsibleContent }

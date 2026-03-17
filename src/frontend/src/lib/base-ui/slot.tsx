import * as React from "react";

import { useRender } from "@base-ui/react/use-render";

export const Slot = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement>>(
  function Slot({ children, ...props }, forwardedRef) {
    const child = React.Children.only(children);

    if (!React.isValidElement(child)) {
      return null;
    }

    return useRender({
      render: child,
      ref: forwardedRef,
      props,
    });
  },
);

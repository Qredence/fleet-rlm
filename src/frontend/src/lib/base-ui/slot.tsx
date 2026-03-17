import * as React from "react";

import { useRender } from "@base-ui/react/use-render";

export const Slot = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement>>(
  function Slot({ children, ...props }, forwardedRef) {
    const child = React.Children.only(children);
    const render = React.isValidElement(child) ? child : undefined;

    return useRender({
      enabled: Boolean(render),
      render,
      ref: forwardedRef,
      props,
    });
  },
);

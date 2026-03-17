import * as React from "react";

export function withAsChild<P extends { children?: React.ReactNode }>(
  props: P & { asChild?: boolean },
) {
  const { asChild = false, children, ...rest } = props;

  if (!asChild) {
    return { children, props: rest, render: undefined };
  }

  const child = React.Children.only(children);

  if (!React.isValidElement(child)) {
    return { children, props: rest, render: undefined };
  }

  return { children: undefined, props: rest, render: child };
}

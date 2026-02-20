import { Text as InkText, type TextProps as InkTextProps } from "ink";
import type React from "react";

export interface TextProps extends InkTextProps {
  children?: React.ReactNode;
}

/**
 * Wrapper around Ink's Text component with proper typing.
 * Handles React children safely.
 */
export function Text({ children, ...props }: TextProps): React.JSX.Element {
  return <InkText {...props}>{children}</InkText>;
}

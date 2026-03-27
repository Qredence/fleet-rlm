import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Manages controlled/uncontrolled state with an optional `onChange` callback.
 *
 * Replaces `@radix-ui/react-use-controllable-state` with a dependency-free
 * React 19 implementation.
 *
 * @param prop       - The controlled value (if `undefined`, state is uncontrolled).
 * @param defaultProp - The initial value for uncontrolled mode.
 * @param onChange   - Called whenever the value changes.
 */
export function useControllableState<T>({
  prop,
  defaultProp,
  onChange,
}: {
  prop?: T;
  defaultProp?: T;
  onChange?: (value: T) => void;
}): [T, (next: T | ((prev: T) => T)) => void] {
  const [uncontrolledValue, setUncontrolledValue] = useState<T>(
    defaultProp as T,
  );
  const isControlled = prop !== undefined;
  const value = isControlled ? (prop as T) : uncontrolledValue;

  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });

  const setValue = useCallback(
    (next: T | ((prev: T) => T)) => {
      const resolve = (prev: T): T =>
        typeof next === "function" ? (next as (prev: T) => T)(prev) : next;

      if (!isControlled) {
        setUncontrolledValue((prev) => {
          const resolved = resolve(prev);
          onChangeRef.current?.(resolved);
          return resolved;
        });
      } else {
        const resolved = resolve(prop as T);
        onChangeRef.current?.(resolved);
      }
    },
    [isControlled, prop],
  );

  return [value, setValue];
}

import * as React from "react";

interface UseControllableStateParams<T> {
  prop?: T | undefined;
  defaultProp?: T | undefined;
  onChange?: ((...args: any[]) => void) | undefined;
}

export function useControllableState<T>({
  prop,
  defaultProp,
  onChange,
}: UseControllableStateParams<T>): [
  T | undefined,
  React.Dispatch<React.SetStateAction<T | undefined>>,
] {
  const [uncontrolledState, setUncontrolledState] = React.useState<T | undefined>(
    defaultProp,
  );
  const isControlled = prop !== undefined;
  const value = isControlled ? prop : uncontrolledState;
  const onChangeRef = React.useRef(onChange);

  React.useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  const setValue = React.useCallback<
    React.Dispatch<React.SetStateAction<T | undefined>>
  >(
    (nextValue) => {
      const resolvedValue =
        nextValue instanceof Function ? nextValue(value) : nextValue;

      if (!isControlled) {
        setUncontrolledState(resolvedValue);
      }

      if (!Object.is(value, resolvedValue)) {
        onChangeRef.current?.(resolvedValue);
      }
    },
    [isControlled, value],
  );

  return [value, setValue];
}

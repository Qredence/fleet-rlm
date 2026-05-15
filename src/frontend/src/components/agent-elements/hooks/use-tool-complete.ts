import { useEffect, useRef } from "react";

export function useToolComplete(isAnimating: boolean, duration: number, onComplete: () => void) {
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    if (!isAnimating || duration <= 0) return;
    const t = setTimeout(() => onCompleteRef.current(), duration);
    return () => clearTimeout(t);
  }, [isAnimating, duration]);
}

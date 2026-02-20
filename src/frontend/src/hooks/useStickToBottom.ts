import { useRef, useEffect, useCallback, useState } from "react";

/**
 * Stick-to-bottom hook for chat-style scrolling.
 * Returns a `scrollRef` (for the scrollable viewport) and a `contentRef`
 * (for the content container). When the user is scrolled to the bottom,
 * new content additions auto-scroll to keep the view pinned.
 */
export function useStickToBottom() {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  // Internal ref for observers to read current value without stale closures
  const isAtBottomRef = useRef(true);
  // Reactive state for consumers to trigger re-renders
  const [isAtBottom, setIsAtBottom] = useState(true);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  // Track whether the user is near the bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const threshold = 60; // px tolerance

    function handleScroll() {
      if (!el) return;
      const { scrollTop, scrollHeight, clientHeight } = el;
      const atBottom = scrollHeight - scrollTop - clientHeight < threshold;
      // Update ref for observers
      isAtBottomRef.current = atBottom;
      // Update state to trigger re-renders for consumers
      setIsAtBottom(atBottom);
    }

    el.addEventListener("scroll", handleScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  // Observe content size changes and auto-scroll if stuck
  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;

    const observer = new ResizeObserver(() => {
      if (isAtBottomRef.current) {
        requestAnimationFrame(scrollToBottom);
      }
    });

    observer.observe(content);

    // Also observe child additions via MutationObserver
    const mutationObserver = new MutationObserver(() => {
      if (isAtBottomRef.current) {
        requestAnimationFrame(scrollToBottom);
      }
    });

    mutationObserver.observe(content, { childList: true, subtree: true });

    return () => {
      observer.disconnect();
      mutationObserver.disconnect();
    };
  }, [scrollToBottom]);

  return { scrollRef, contentRef, scrollToBottom, isAtBottom };
}

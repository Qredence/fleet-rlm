import { memo, useEffect, useRef } from "react";
import { Streamdown as StreamdownRenderer } from "streamdown";
import "streamdown/styles.css";
import { cn } from "@/lib/utils";

interface StreamdownProps {
  content: string;
  streaming?: boolean;
  speed?: number;
  interval?: number;
  onComplete?: () => void;
  className?: string;
}

/**
 * Compatibility wrapper around the `streamdown` package.
 *
 * We preserve the local prop contract used throughout the app (`content`,
 * `streaming`, optional `speed`/`interval`) while delegating markdown parsing
 * and streaming-safe rendering to the official Streamdown package.
 */
export const Streamdown = memo(function Streamdown({
  content,
  streaming = false,
  speed: _speed,
  interval: _interval,
  onComplete,
  className,
}: StreamdownProps) {
  const prevStreamingRef = useRef(streaming);

  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    prevStreamingRef.current = streaming;
    if (wasStreaming && !streaming) {
      onComplete?.();
    }
  }, [streaming, onComplete]);

  return (
    <div className={cn("streamdown-root", className)}>
      <StreamdownRenderer
        mode={streaming ? "streaming" : "static"}
        isAnimating={streaming}
        parseIncompleteMarkdown
        className={cn(
          "streamdown-content min-w-0 max-w-full space-y-4 whitespace-normal text-foreground wrap-break-word [&>*:first-child]:mt-0 [&>*:last-child]:mb-0",
          "typo-label leading-relaxed",
          "[&_p]:mb-3 [&_p]:max-w-full [&_p]:wrap-break-word [&_p]:typo-label [&_p]:leading-relaxed [&_p:last-child]:mb-0",
          "[&_li]:max-w-full [&_li]:wrap-break-word [&_li]:typo-label [&_li]:leading-relaxed",
          "[&_h1]:typo-h4 [&_h1]:leading-tight [&_h1]:font-semibold [&_h1]:mt-5 [&_h1]:mb-2",
          "[&_h2]:typo-body-sm [&_h2]:leading-snug [&_h2]:font-semibold [&_h2]:mt-4 [&_h2]:mb-1.5",
          "[&_h3]:typo-label [&_h3]:leading-snug [&_h3]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1",
          "[&_h4]:typo-body-sm [&_h4]:leading-snug [&_h4]:font-medium [&_h4]:mt-2 [&_h4]:mb-1",
          "[&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border-subtle [&_pre]:bg-muted/40 [&_pre]:p-3",
          "[&_pre_code]:block [&_pre_code]:min-w-max [&_pre_code]:whitespace-pre",
          "[&_:not(pre)>code]:whitespace-pre-wrap [&_:not(pre)>code]:wrap-break-word",
          "[&_code]:font-mono [&_code]:typo-body-sm [&_code]:leading-loose-custom",
          "[&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-6",
          "[&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-6",
          "[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
          "[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2",
        )}
      >
        {content}
      </StreamdownRenderer>
    </div>
  );
});

export type { StreamdownProps };

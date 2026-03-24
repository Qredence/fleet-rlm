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
          "text-[14px] leading-5.25 text-foreground",
          "[&_p]:text-[14px] [&_li]:text-[14px] [&_pre]:text-[14px]",
          "[&_h1]:text-[14px] [&_h1]:leading-5.25 [&_h1]:font-semibold",
          "[&_h2]:text-[14px] [&_h2]:leading-5.25 [&_h2]:font-semibold",
          "[&_h3]:text-[14px] [&_h3]:leading-5.25 [&_h3]:font-semibold",
          "[&_h4]:text-[14px] [&_h4]:leading-5.25 [&_h4]:font-semibold",
          "[&_p]:mb-3 [&_p:last-child]:mb-0",
          "[&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border-subtle [&_pre]:bg-muted/40 [&_pre]:p-3",
          "[&_code]:font-mono [&_code]:text-[14px] [&_code]:leading-5.25",
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

/**
 * StreamDown component for markdown streaming in OpenTUI
 * Renders markdown content with smooth streaming animation
 */

import { useEffect, useState } from "react";
import { z } from "zod";

// Zod schema for markdown stream props
export const StreamDownPropsSchema = z.object({
  content: z.string(),
  speed: z.number().positive().optional(),
  chunkSize: z.number().positive().optional(),
  onComplete: z.function().optional(),
  isStreaming: z.boolean().optional(),
});

export type StreamDownProps = z.infer<typeof StreamDownPropsSchema>;

// Simple markdown parsing for terminal display
function parseMarkdown(text: string): string {
  return (
    text
      // Headers
      .replace(/^### (.*$)/gim, "\x1b[1;36m$1\x1b[0m")
      .replace(/^## (.*$)/gim, "\x1b[1;35m$1\x1b[0m")
      .replace(/^# (.*$)/gim, "\x1b[1;33m$1\x1b[0m")
      // Bold
      .replace(/\*\*(.*?)\*\*/g, "\x1b[1m$1\x1b[0m")
      // Italic
      .replace(/\*(.*?)\*/g, "\x1b[3m$1\x1b[0m")
      // Code inline
      .replace(/`([^`]+)`/g, "\x1b[36m$1\x1b[0m")
      // Code blocks
      .replace(/```[\s\S]*?```/g, (match) => {
        return match
          .replace(/```(\w+)?\n?/, "")
          .replace(/```$/, "")
          .split("\n")
          .map((line) => `  \x1b[90m${line}\x1b[0m`)
          .join("\n");
      })
      // Links
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, "\x1b[34m$1\x1b[0m (\x1b[90m$2\x1b[0m)")
      // Lists
      .replace(/^\s*[-*+]\s+/gim, "  • ")
      .replace(/^\s*\d+\.\s+/gim, (match) => `  ${match.trim()} `)
      // Blockquotes
      .replace(/^> (.*$)/gim, "\x1b[3;90m  │ $1\x1b[0m")
      // Horizontal rules
      .replace(/^(---|\*\*\*|___)$/gim, "\x1b[90m─────────────────────\x1b[0m")
  );
}

export function StreamDown({
  content,
  speed = 20,
  chunkSize = 1,
  onComplete,
  isStreaming = true,
}: StreamDownProps) {
  const [displayedContent, setDisplayedContent] = useState("");
  const [isComplete, setIsComplete] = useState(false);
  
  useEffect(() => {
    if (!isStreaming) {
      setDisplayedContent(content);
      setIsComplete(true);
      return;
    }
    
    let currentIndex = 0;
    setDisplayedContent("");
    setIsComplete(false);
    
    const streamInterval = setInterval(() => {
      if (currentIndex >= content.length) {
        clearInterval(streamInterval);
        setIsComplete(true);
        onComplete?.();
        return;
      }
      
      const nextIndex = Math.min(currentIndex + chunkSize, content.length);
      setDisplayedContent(content.slice(0, nextIndex));
      currentIndex = nextIndex;
    }, 1000 / speed);
    
    return () => clearInterval(streamInterval);
  }, [content, speed, chunkSize, isStreaming, onComplete]);
  
  const parsedContent = parseMarkdown(displayedContent);
  
  return (
    <text>
      {parsedContent}
      {!isComplete && <span fg="#6a5acd">▊</span>}
    </text>
  );
}

// Pre-formatted stream for code blocks
export function CodeStream({
  content,
  language,
  speed = 50,
  isStreaming = true,
}: {
  content: string;
  language?: string;
  speed?: number;
  isStreaming?: boolean;
}) {
  const [displayedContent, setDisplayedContent] = useState("");
  
  useEffect(() => {
    if (!isStreaming) {
      setDisplayedContent(content);
      return;
    }
    
    let currentIndex = 0;
    setDisplayedContent("");
    
    const streamInterval = setInterval(() => {
      if (currentIndex >= content.length) {
        clearInterval(streamInterval);
        return;
      }
      
      const nextIndex = Math.min(currentIndex + 5, content.length);
      setDisplayedContent(content.slice(0, nextIndex));
      currentIndex = nextIndex;
    }, 1000 / speed);
    
    return () => clearInterval(streamInterval);
  }, [content, speed, isStreaming]);
  
  return (
    <box
      backgroundColor="#1e1e1e"
      padding={1}
      border
      borderStyle="rounded"
      borderColor="#333"
      title={language ? ` ${language} ` : " code "}
    >
      <text>{displayedContent}</text>
    </box>
  );
}

export default StreamDown;

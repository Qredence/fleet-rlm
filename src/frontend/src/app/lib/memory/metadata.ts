import {
  BookOpen,
  Brain,
  Compass,
  Lightbulb,
  MessageSquare,
} from "lucide-react";
import type { MemoryType } from "../../components/data/types";

export const TYPE_META: Record<
  MemoryType,
  { label: string; icon: typeof Brain; color: string }
> = {
  fact: {
    label: "Fact",
    icon: Lightbulb,
    color: "text-chart-1",
  },
  preference: {
    label: "Preference",
    icon: Compass,
    color: "text-chart-2",
  },
  session: {
    label: "Session",
    icon: MessageSquare,
    color: "text-chart-3",
  },
  knowledge: {
    label: "Knowledge",
    icon: BookOpen,
    color: "text-chart-4",
  },
  directive: {
    label: "Directive",
    icon: Brain,
    color: "text-chart-5",
  },
};

export const ALL_TYPES: MemoryType[] = [
  "fact",
  "preference",
  "session",
  "knowledge",
  "directive",
];

export const CREATABLE_TYPES: MemoryType[] = [
  "fact",
  "preference",
  "knowledge",
  "directive",
];

export function formatSize(entries: { content: string }[]): string {
  const chars = entries.reduce((a, e) => a + e.content.length, 0);
  if (chars < 1000) return `${chars} chars`;
  return `${(chars / 1000).toFixed(1)}K chars`;
}

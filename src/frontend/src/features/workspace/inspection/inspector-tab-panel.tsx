/**
 * InspectorTabPanel — shared scroll + padding wrapper for inspector tab content.
 *
 * All inspector tabs (message, execution, graph) use the same outer structure:
 *
 *   TabsContent (min-h-0 flex-1)
 *     ScrollArea (h-full)
 *       div (inspectorStyles.tab.content)
 *
 * This component encapsulates that repeated boilerplate so each tab only
 * renders its own content.
 */
import type { ReactNode } from "react";
import { TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { InspectorTab } from "@/lib/workspace/workspace-types";
import { inspectorStyles } from "./inspector-styles";

interface InspectorTabPanelProps {
  value: InspectorTab;
  children: ReactNode;
}

export function InspectorTabPanel({ value, children }: InspectorTabPanelProps) {
  return (
    <TabsContent value={value} className="min-h-0 flex-1">
      <ScrollArea className="h-full">
        <div className={inspectorStyles.tab.content}>{children}</div>
      </ScrollArea>
    </TabsContent>
  );
}

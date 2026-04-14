import { useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { PageHeader } from "@/components/product/page-header";
import { SessionList } from "./session-list";
import { SessionDetail } from "./session-detail";
import { cn } from "@/lib/utils";

export type HistorySelection =
  | { source: "api"; sessionId: number }
  | { source: "local"; conversationId: string };

export function HistoryScreen() {
  const isMobile = useIsMobile();
  const [selectedSession, setSelectedSession] = useState<HistorySelection | null>(null);

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {!isMobile ? (
        <PageHeader
          isMobile={false}
          title="Session History"
          description="Browse and search past session transcripts."
        />
      ) : null}

      <ScrollArea className="min-h-0 flex-1">
        {isMobile ? (
          <PageHeader
            isMobile
            title="Session History"
            description="Browse and search past session transcripts."
          />
        ) : null}

        <div className={cn("mx-auto w-full max-w-200 py-4", isMobile ? "px-4" : "px-6")}>
          <SessionList
            selectedSession={selectedSession}
            onSelect={setSelectedSession}
          />
        </div>
      </ScrollArea>

      {/* Detail drawer */}
      {selectedSession != null ? (
        <SessionDetail
          selectedSession={selectedSession}
          open={selectedSession != null}
          onOpenChange={(open) => {
            if (!open) setSelectedSession(null);
          }}
        />
      ) : null}
    </div>
  );
}

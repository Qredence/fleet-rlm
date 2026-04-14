import { useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { PageHeader } from "@/components/product/page-header";
import { OptimizationForm } from "@/features/optimization/optimization-form";
import { OptimizationRuns } from "@/features/optimization/optimization-runs";
import { cn } from "@/lib/utils";

export function OptimizationScreen() {
  const isMobile = useIsMobile();
  const [activeTab, setActiveTab] = useState("new-run");

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {!isMobile ? (
        <PageHeader
          isMobile={false}
          title="Prompt Optimization"
          description="Evolve prompts using GEPA (Generative Evolution of Prompts with Assessment) powered by DSPy and MLflow."
        />
      ) : null}

      <ScrollArea className="min-h-0 flex-1">
        {isMobile ? (
          <PageHeader
            isMobile
            title="Prompt Optimization"
            description="Evolve prompts using GEPA powered by DSPy and MLflow."
          />
        ) : null}

        <div className={cn("mx-auto w-full max-w-200 py-4", isMobile ? "px-4" : "px-6")}>
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList>
              <TabsTrigger value="new-run">New Run</TabsTrigger>
              <TabsTrigger value="history">Run History</TabsTrigger>
            </TabsList>
            <TabsContent value="new-run" className="pt-4">
              <OptimizationForm onRunCreated={() => setActiveTab("history")} />
            </TabsContent>
            <TabsContent value="history" className="pt-4">
              <OptimizationRuns />
            </TabsContent>
          </Tabs>
        </div>
      </ScrollArea>
    </div>
  );
}

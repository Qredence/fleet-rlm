import { ScrollArea } from "@/components/ui/scroll-area";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { PageHeader } from "@/components/product/page-header";
import { OptimizationForm } from "@/features/optimization/optimization-form";
import { cn } from "@/lib/utils";

export function OptimizationScreen() {
  const isMobile = useIsMobile();

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
          <OptimizationForm />
        </div>
      </ScrollArea>
    </div>
  );
}

import { ScrollArea } from "@/components/ui/scroll-area";
import { useIsMobile } from "@/hooks/use-is-mobile";
import { OptimizationForm } from "@/screens/settings/optimization-form";
import { cn } from "@/lib/utils";

export function OptimizationScreen() {
  const isMobile = useIsMobile();

  return (
    <div className="flex h-full w-full flex-col overflow-hidden bg-background">
      {!isMobile ? (
        <div className="mx-auto w-full max-w-200 shrink-0 border-b border-border-subtle px-6 pb-4 pt-4 md:pt-6">
          <h2 className="mb-1 text-balance text-foreground typo-h3">Prompt Optimization</h2>
          <p className="mb-0 text-muted-foreground typo-helper">
            Evolve prompts using GEPA (Generative Evolution of Prompts with Assessment) powered by
            DSPy and MLflow.
          </p>
        </div>
      ) : null}

      <ScrollArea className="min-h-0 flex-1">
        {isMobile ? (
          <div className="w-full px-4 pb-4 pt-2">
            <h2 className="mb-3 text-balance text-foreground typo-h2">Prompt Optimization</h2>
            <p className="mb-3 text-muted-foreground typo-helper">
              Evolve prompts using GEPA powered by DSPy and MLflow.
            </p>
          </div>
        ) : null}

        <div className={cn("mx-auto w-full max-w-200 py-4", isMobile ? "px-4" : "px-6")}>
          <OptimizationForm />
        </div>
      </ScrollArea>
    </div>
  );
}

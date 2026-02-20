import { motion } from "motion/react";
import { GitBranch, TerminalSquare, ListTree, FileCode2 } from "lucide-react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";
import { useArtifactStore } from "../../stores/artifactStore";
import { ArtifactGraph } from "./ArtifactGraph";
import { ArtifactREPL } from "./ArtifactREPL";
import { ArtifactTimeline } from "./ArtifactTimeline";
import { ArtifactPreview } from "./ArtifactPreview";

export function ArtifactCanvas() {
  const steps = useArtifactStore((state) => state.steps);
  const activeStepId = useArtifactStore((state) => state.activeStepId);
  const setActiveStepId = useArtifactStore((state) => state.setActiveStepId);

  return (
    <motion.div
      className="h-full flex flex-col p-3 md:p-4 gap-3"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
    >
      <Tabs defaultValue="graph" className="h-full flex flex-col gap-3">
        <TabsList className="w-full">
          <TabsTrigger value="graph">
            <GitBranch className="size-4" />
            Graph
          </TabsTrigger>
          <TabsTrigger value="repl">
            <TerminalSquare className="size-4" />
            REPL
          </TabsTrigger>
          <TabsTrigger value="timeline">
            <ListTree className="size-4" />
            Timeline
          </TabsTrigger>
          <TabsTrigger value="preview">
            <FileCode2 className="size-4" />
            Preview
          </TabsTrigger>
        </TabsList>

        <TabsContent value="graph" className="flex-1 min-h-0">
          <ArtifactGraph
            steps={steps}
            activeStepId={activeStepId}
            onSelectStep={setActiveStepId}
          />
        </TabsContent>

        <TabsContent value="repl" className="flex-1 min-h-0">
          <ArtifactREPL steps={steps} activeStepId={activeStepId} />
        </TabsContent>

        <TabsContent value="timeline" className="flex-1 min-h-0">
          <ArtifactTimeline
            steps={steps}
            activeStepId={activeStepId}
            onSelectStep={setActiveStepId}
          />
        </TabsContent>

        <TabsContent value="preview" className="flex-1 min-h-0">
          <ArtifactPreview steps={steps} activeStepId={activeStepId} />
        </TabsContent>
      </Tabs>
    </motion.div>
  );
}

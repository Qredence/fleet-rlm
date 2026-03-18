import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { PanelRight } from "lucide-react";

import { VolumeFileDetail } from "@/screens/volumes/components/volume-file-detail";
import { useVolumesSelectionStore } from "@/screens/volumes/model/volumes-selection-store";

export function VolumesCanvasPanel() {
  const selectedFileNode = useVolumesSelectionStore((state) => state.selectedFileNode);

  if (!selectedFileNode) {
    return (
      <Empty className="h-full rounded-none border-0 bg-transparent">
        <EmptyMedia variant="icon">
          <PanelRight />
        </EmptyMedia>
        <EmptyContent>
          <EmptyTitle>No file selected</EmptyTitle>
          <EmptyDescription>
            Open a file in Volumes to preview its contents here.
          </EmptyDescription>
        </EmptyContent>
      </Empty>
    );
  }

  return <VolumeFileDetail file={selectedFileNode} />;
}

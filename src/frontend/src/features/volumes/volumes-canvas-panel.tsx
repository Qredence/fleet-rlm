import { EmptyPanel } from "@/components/product/empty-panel";
import { useVolumesSelectionStore } from "@/features/volumes/use-volumes";
import { VolumeFileDetail } from "@/features/volumes/components/file-detail";

export function VolumesCanvasPanel() {
  const selectedFileNode = useVolumesSelectionStore((state) => state.selectedFileNode);

  if (!selectedFileNode) {
    return (
      <EmptyPanel
        title="No file selected"
        description="Open a file in Volumes to preview its contents here."
        className="h-full rounded-none border-0 bg-transparent"
      />
    );
  }

  return <VolumeFileDetail file={selectedFileNode} />;
}

export function VolumesCanvasUnavailablePanel() {
  return (
    <EmptyPanel
      title="Volumes unavailable"
      description="The Volumes surface requires a live FastAPI runtime. Disable VITE_MOCK_MODE to connect to the backend."
      className="h-full rounded-none border-0 bg-transparent"
    />
  );
}

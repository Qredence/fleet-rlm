import { useVolumesSelectionStore } from "@/screens/volumes/volumes-selection-store";

function useVolumesShellSelection() {
  const selectedFileNode = useVolumesSelectionStore((state) => state.selectedFileNode);
  const clearSelectedFile = useVolumesSelectionStore((state) => state.clearSelectedFile);

  return {
    selectedFileNode,
    clearSelectedFile,
  };
}

export { useVolumesShellSelection };

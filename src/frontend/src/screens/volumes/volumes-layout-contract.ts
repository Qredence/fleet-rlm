import { useVolumesSelectionStore } from "@/screens/volumes/volumes-selection-store";

function useVolumesLayoutSelection() {
  const selectedFileNode = useVolumesSelectionStore((state) => state.selectedFileNode);
  const clearSelectedFile = useVolumesSelectionStore((state) => state.clearSelectedFile);

  return {
    selectedFileNode,
    clearSelectedFile,
  };
}

const useVolumesShellSelection = useVolumesLayoutSelection;

export { useVolumesLayoutSelection, useVolumesShellSelection };

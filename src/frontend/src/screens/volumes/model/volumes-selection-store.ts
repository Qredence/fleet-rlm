import { create } from "zustand";
import type { FsNode } from "@/screens/volumes/model/volumes-types";

interface VolumesSelectionState {
  selectedFileNode: FsNode | null;
  selectFile: (node: FsNode | null) => void;
  clearSelectedFile: () => void;
}

export const useVolumesSelectionStore = create<VolumesSelectionState>((set) => ({
  selectedFileNode: null,
  selectFile: (selectedFileNode) => set({ selectedFileNode }),
  clearSelectedFile: () => set({ selectedFileNode: null }),
}));

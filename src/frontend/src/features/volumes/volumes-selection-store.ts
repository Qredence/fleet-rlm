import { create } from "zustand";

import type { FsNode } from "@/features/volumes/use-volumes";

export interface VolumesSelectionState {
  selectedFileNode: FsNode | null;
  selectFile: (node: FsNode | null) => void;
  clearSelectedFile: () => void;
}

export const useVolumesSelectionStore = create<VolumesSelectionState>((set) => ({
  selectedFileNode: null,
  selectFile: (selectedFileNode) => set({ selectedFileNode }),
  clearSelectedFile: () => set({ selectedFileNode: null }),
}));

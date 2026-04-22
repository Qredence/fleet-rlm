import { create } from "zustand";

import type { FsNode } from "@/features/volumes/use-volumes";

export interface VolumesSelectionState {
  selectedFileNode: FsNode | null;
  selectedVolumeName: string | null;
  selectFile: (node: FsNode | null) => void;
  selectVolume: (volumeName: string | null) => void;
  clearSelectedFile: () => void;
}

export const useVolumesSelectionStore = create<VolumesSelectionState>((set) => ({
  selectedFileNode: null,
  selectedVolumeName: null,
  selectFile: (selectedFileNode) => set({ selectedFileNode }),
  selectVolume: (selectedVolumeName) => set({ selectedVolumeName }),
  clearSelectedFile: () => set({ selectedFileNode: null }),
}));

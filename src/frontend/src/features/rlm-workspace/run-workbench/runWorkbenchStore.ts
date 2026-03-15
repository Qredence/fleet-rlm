import { create } from "zustand";

import type { WsServerMessage } from "@/lib/rlm-api";
import {
  applyFrameToRunWorkbenchState,
  createInitialRunWorkbenchState,
  failRunWorkbenchRun,
  shouldApplyRunFrame,
  startRunWorkbenchRun,
} from "@/features/rlm-workspace/run-workbench/runWorkbenchAdapter";
import type { DetailTab, RunWorkbenchState } from "@/features/rlm-workspace/run-workbench/types";

interface RunWorkbenchStore extends RunWorkbenchState {
  reset: () => void;
  beginRun: (input: {
    task: string;
    repoUrl?: string;
    repoRef?: string | null;
    contextPaths?: string[];
  }) => void;
  failRun: (errorMessage: string) => void;
  applyFrame: (frame: WsServerMessage) => void;
  selectIteration: (iterationId: string | null) => void;
  selectCallback: (callbackId: string | null) => void;
  selectTab: (tab: DetailTab) => void;
}

export const useRunWorkbenchStore = create<RunWorkbenchStore>((set, get) => ({
  ...createInitialRunWorkbenchState(),
  reset: () => set(createInitialRunWorkbenchState()),
  beginRun: (input) => set((state) => startRunWorkbenchRun(state, input)),
  failRun: (errorMessage) => set((state) => failRunWorkbenchRun(state, errorMessage)),
  applyFrame: (frame) =>
    set((state) => {
      if (!shouldApplyRunFrame(state, frame)) return state;
      return applyFrameToRunWorkbenchState(state, frame);
    }),
  selectIteration: (iterationId) => set({ selectedIterationId: iterationId }),
  selectCallback: (callbackId) => set({ selectedCallbackId: callbackId }),
  selectTab: (tab) => {
    if (get().selectedTab === tab) return;
    set({ selectedTab: tab });
  },
}));

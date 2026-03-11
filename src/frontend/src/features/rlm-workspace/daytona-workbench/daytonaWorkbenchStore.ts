import { create } from "zustand";

import type { WsServerMessage } from "@/lib/rlm-api";
import {
  applyDaytonaFrameToWorkbenchState,
  createInitialDaytonaWorkbenchState,
  shouldApplyDaytonaFrame,
  startDaytonaWorkbenchRun,
} from "@/features/rlm-workspace/daytona-workbench/daytonaWorkbenchAdapter";
import type {
  DaytonaDetailTab,
  DaytonaWorkbenchStateData,
} from "@/features/rlm-workspace/daytona-workbench/types";

interface DaytonaWorkbenchStore extends DaytonaWorkbenchStateData {
  reset: () => void;
  beginRun: (input: {
    task: string;
    repoUrl: string;
    repoRef?: string | null;
  }) => void;
  applyFrame: (frame: WsServerMessage) => void;
  selectNode: (nodeId: string | null) => void;
  selectTab: (tab: DaytonaDetailTab) => void;
}

export const useDaytonaWorkbenchStore = create<DaytonaWorkbenchStore>(
  (set, get) => ({
    ...createInitialDaytonaWorkbenchState(),
    reset: () => set(createInitialDaytonaWorkbenchState()),
    beginRun: (input) =>
      set((state) => startDaytonaWorkbenchRun(state, input)),
    applyFrame: (frame) =>
      set((state) => {
        if (!shouldApplyDaytonaFrame(state, frame)) return state;
        return applyDaytonaFrameToWorkbenchState(state, frame);
      }),
    selectNode: (nodeId) => set({ selectedNodeId: nodeId }),
    selectTab: (tab) => {
      if (get().selectedTab === tab) return;
      set({ selectedTab: tab });
    },
  }),
);

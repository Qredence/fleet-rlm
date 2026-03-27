import { create } from "zustand";

import type { WsServerMessage } from "@/lib/rlm-api";
import { telemetryClient } from "@/lib/telemetry/client";
import {
  applyFrameToRunWorkbenchState,
  createInitialRunWorkbenchState,
  failRunWorkbenchRun,
  shouldApplyRunFrame,
  startRunWorkbenchRun,
} from "@/lib/workspace/run-workbench-adapter";
import type {
  DetailTab,
  RunWorkbenchState,
} from "@/lib/workspace/workspace-types";

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
  failRun: (errorMessage) =>
    set((state) => failRunWorkbenchRun(state, errorMessage)),
  applyFrame: (frame) =>
    set((state) => {
      if (!shouldApplyRunFrame(state, frame)) return state;
      const next = applyFrameToRunWorkbenchState(state, frame);
      if (
        next.compatBackfillCount > state.compatBackfillCount &&
        next.lastCompatBackfill?.eventId
      ) {
        telemetryClient.capture("run_workbench_chat_final_backfill_used", {
          runtime_mode: next.lastCompatBackfill.runtimeMode ?? "unknown",
          used_summary: next.lastCompatBackfill.usedSummary,
          used_final_artifact: next.lastCompatBackfill.usedFinalArtifact,
        });
      }
      return next;
    }),
  selectIteration: (iterationId) => set({ selectedIterationId: iterationId }),
  selectCallback: (callbackId) => set({ selectedCallbackId: callbackId }),
  selectTab: (tab) => {
    if (get().selectedTab === tab) return;
    set({ selectedTab: tab });
  },
}));

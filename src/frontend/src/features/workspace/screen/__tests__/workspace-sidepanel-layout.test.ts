import { describe, expect, it } from "vite-plus/test";

import {
  WORKSPACE_CHAT_MIN_SIZE,
  WORKSPACE_SIDEPANEL_MAX_SIZE,
  WORKSPACE_SIDEPANEL_OPEN_SIZE,
} from "@/features/workspace/screen/workspace-screen";
import {
  WORKSPACE_VOLUME_TREE_DEFAULT_WIDTH,
  WORKSPACE_VOLUME_TREE_MAX_WIDTH,
  WORKSPACE_VOLUME_TREE_MIN_WIDTH,
  WORKSPACE_VOLUME_PREVIEW_MIN_WIDTH,
} from "@/features/workspace/screen/sidepanel/tabs/volume-tab";

describe("workspace sidepanel layout constants", () => {
  it("keeps chat primary while allowing the inspector to resize wider", () => {
    expect(WORKSPACE_CHAT_MIN_SIZE).toBe("25%");
    expect(WORKSPACE_SIDEPANEL_OPEN_SIZE).toBe("32%");
    expect(WORKSPACE_SIDEPANEL_MAX_SIZE).toBe("75%");
  });

  it("keeps the desktop volume tree resizable beside the preview", () => {
    expect(WORKSPACE_VOLUME_TREE_DEFAULT_WIDTH).toBe(220);
    expect(WORKSPACE_VOLUME_TREE_MIN_WIDTH).toBe(160);
    expect(WORKSPACE_VOLUME_TREE_MAX_WIDTH).toBe(520);
    expect(WORKSPACE_VOLUME_PREVIEW_MIN_WIDTH).toBe(160);
  });
});

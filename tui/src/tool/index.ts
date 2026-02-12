export { Tool, type ToolContext, type ToolResult, type ToolDefinition, truncateOutput } from "./tool";
export { GrepTool } from "./grep";
export { ReadTool } from "./read";
export { EditTool } from "./edit";
export { WriteTool } from "./write";
export { GlobTool } from "./glob";
export { BashTool } from "./bash";

import { Tool } from "./tool";
import { GrepTool } from "./grep";
import { ReadTool } from "./read";
import { EditTool } from "./edit";
import { WriteTool } from "./write";
import { GlobTool } from "./glob";
import { BashTool } from "./bash";

export function registerAllTools(): void {
  // Tools auto-register when imported, but we ensure they're loaded
  void GrepTool;
  void ReadTool;
  void EditTool;
  void WriteTool;
  void GlobTool;
  void BashTool;
}

export function getAllTools() {
  registerAllTools();
  return Tool.list();
}

import { z, type ZodTypeAny, type infer as ZodInfer } from "zod";

export interface ToolContext {
  abort: AbortSignal;
  metadata: Record<string, unknown>;
  setMetadata: (key: string, value: unknown) => void;
}

export interface ToolResult {
  title: string;
  metadata: Record<string, unknown>;
  output: string;
  attachments?: Array<{
    path?: string;
    content?: string;
    mimeType?: string;
  }>;
}

export interface ToolDefinition<T extends ZodTypeAny> {
  id: string;
  name: string;
  description: string;
  parameters: T;
  execute: (args: ZodInfer<T>, context: ToolContext) => Promise<ToolResult>;
}

interface RegisteredTool {
  id: string;
  definition: ToolDefinition<ZodTypeAny>;
}

const registry = new Map<string, RegisteredTool>();

export class Tool {
  static define<T extends ZodTypeAny>(
    id: string,
    config: {
      name: string;
      description: string;
      parameters: T;
      execute: (args: ZodInfer<T>, context: ToolContext) => Promise<ToolResult>;
    }
  ): ToolDefinition<T> {
    const definition: ToolDefinition<T> = {
      id,
      name: config.name,
      description: config.description,
      parameters: config.parameters,
      execute: config.execute,
    };

    registry.set(id, { id, definition: definition as unknown as ToolDefinition<ZodTypeAny> });
    return definition;
  }

  static get(id: string): ToolDefinition<ZodTypeAny> | undefined {
    return registry.get(id)?.definition;
  }

  static list(): ToolDefinition<ZodTypeAny>[] {
    return Array.from(registry.values()).map((r) => r.definition);
  }

  static async execute(
    id: string,
    args: Record<string, unknown>,
    context?: Partial<ToolContext>
  ): Promise<ToolResult> {
    const tool = registry.get(id);
    if (!tool) {
      throw new Error(`Tool not found: ${id}`);
    }

    const parsed = tool.definition.parameters.parse(args);
    const fullContext: ToolContext = {
      abort: context?.abort ?? new AbortController().signal,
      metadata: context?.metadata ?? {},
      setMetadata: (key, value) => {
        fullContext.metadata[key] = value;
      },
    };

    const result = await tool.definition.execute(parsed, fullContext);
    return this.truncateResult(result);
  }

  private static truncateResult(result: ToolResult, maxLength: number = 50000): ToolResult {
    if (result.output.length <= maxLength) return result;

    return {
      ...result,
      output: result.output.slice(0, maxLength) + "\n... (output truncated)",
      metadata: {
        ...result.metadata,
        truncated: true,
        originalLength: result.output.length,
      },
    };
  }
}

export function truncateOutput(output: string, maxLength: number = 50000): string {
  if (output.length <= maxLength) return output;
  return output.slice(0, maxLength) + "\n... (output truncated)";
}

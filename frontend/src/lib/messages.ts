import type { UIMessage } from 'ai'

export interface WorkspaceFileEntry {
  path: string
  kind: 'input' | 'artifact'
  sha256: string
  size_bytes: number
}

interface ChildMessage {
  id: string
  role: 'assistant'
  parts: FleetMessage['parts']
}

type FleetData = {
  skills: { skills: { name: string; score: number }[] }
  'file-tree': { files: WorkspaceFileEntry[] }
}

type FleetTools = {
  python_exec: { input: string; output: string | Record<string, unknown> }
  subagent: {
    input: { task: string; kind: 'subagent' | 'llm_query' }
    output: ChildMessage
  }
}

export type FleetMessage = UIMessage<unknown, FleetData, FleetTools>
export type FleetPart = FleetMessage['parts'][number]

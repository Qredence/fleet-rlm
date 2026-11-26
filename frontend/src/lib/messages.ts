import type { UIMessage } from 'ai'

export interface WorkspaceFileEntry {
  path: string
  kind: 'input' | 'artifact' | 'workspace'
  sha256?: string
  revision?: string
  size_bytes: number
}

interface ChildMessage {
  id: string
  role: 'assistant'
  parts: FleetMessage['parts']
}

export interface WebCall {
  id: string
  tool: 'web_search' | 'web_open'
  target: string
  outcome: 'running' | 'results' | 'no_results' | 'opened' | 'truncated' | 'failed' | 'exhausted' | 'interrupted'
  elapsed_ms: number | null
  used: number | null
  limit: number | null
}

export interface TurnDetails {
  runtime: 'dspy.RLM' | null
  root_actions: number
  child_rlms: number
  sub_lm_queries: number
  elapsed_ms: number | null
  completion: 'submit' | 'iteration_limit' | 'unknown' | 'failed' | 'timeout' | 'cancelled'
  trace_url: string | null
}

type FleetData = {
  skills: { skills: { name: string; score: number }[] }
  'file-tree': { files: WorkspaceFileEntry[] }
  'web-call': WebCall
  'turn-details': TurnDetails
  'python-submission': { tool_call_id: string }
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

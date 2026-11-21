import { useState, type ReactNode } from 'react'
import { GitBranch, MessageSquare } from 'lucide-react'
import {
  TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceIcon, TraceLine, TraceTitle,
} from '@/components/ui/trace'
import type { FleetPart } from '@/lib/messages'
import { StackTrace } from './PythonToolTrace'

type SubagentPart = Extract<FleetPart, { type: 'tool-subagent' }>

export function SubagentTrace({ part, live, children }: { part: SubagentPart; live: boolean; children: ReactNode }) {
  const label = part.input?.kind === 'llm_query' ? 'Model query' : 'Subagent'
  const incomplete = part.state === 'input-available' || (part.state === 'output-available' && part.preliminary === true)
  const running = live && incomplete
  const failed = part.state === 'output-error'
  const [open, setOpen] = useState(running || failed)
  const state = failed ? 'failed' : running ? 'running' : incomplete ? 'interrupted' : 'finished'

  return (
    <TraceCollapsible open={open} onOpenChange={setOpen}>
      <TraceCollapsibleTrigger>
        <TraceLine status={failed ? 'error' : running ? 'running' : undefined}>
          <TraceIcon>{part.input?.kind === 'llm_query' ? <MessageSquare /> : <GitBranch />}</TraceIcon>
          <TraceTitle>{label} {state}</TraceTitle>
        </TraceLine>
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent className="space-y-3">
        {part.input?.task && <p className="whitespace-pre-wrap text-sm">{part.input.task}</p>}
        {children}
        {failed && <StackTrace error={part.errorText} />}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

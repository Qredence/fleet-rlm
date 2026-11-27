import { Activity, Brain, FolderDown } from 'lucide-react'
import { useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Badge } from '@/components/ui/badge'
import {
  Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceIcon, TraceLine, TraceTitle,
} from '@/components/ui/trace'
import { followMarkdownFragment } from '@/lib/markdown-fragments'
import type { Memory } from '@/lib/api'
import type { FleetPart } from '@/lib/messages'
import { FileTree } from './FileTree'
import { PythonToolTrace } from './PythonToolTrace'
import { SubagentTrace } from './SubagentTrace'
import { TurnDetailsActivity, WebResearchActivity } from './TurnActivity'

type ActionPart = Extract<FleetPart, { type: 'data-web-call' | 'tool-subagent' | 'tool-python_exec' }>

export function MessageParts({ parts, live = false, preview, download, refine }: {
  parts: FleetPart[]
  live?: boolean
  preview: (path: string) => void
  download: (path: string) => void
  refine?: () => Promise<Memory | null>
}) {
  const [activityOpen, setActivityOpen] = useState<boolean>()
  const submissionMarkers = new Set(parts.flatMap((part) =>
    part.type === 'data-python-submission' ? [part.data.tool_call_id] : [],
  ))
  const submissions = new Map(parts.flatMap((part) =>
    part.type === 'tool-python_exec' && part.state === 'output-available'
      && part.preliminary !== true && submissionMarkers.has(part.toolCallId)
      ? [[part.toolCallId, part.output] as const] : [],
  ))
  const details = parts.find((part) => part.type === 'data-turn-details')
  const isAction = (part: FleetPart): part is ActionPart => part.type === 'data-web-call' || part.type === 'tool-subagent'
    || (part.type === 'tool-python_exec' && !submissions.has(part.toolCallId))
  const actions = parts.filter(isAction)
  const activity = parts.map((part, index) => ({ part, index })).filter(({ part, index }) => {
    if (part.type !== 'reasoning') return isAction(part)
    const next = parts[index + 1]
    return next?.type !== 'tool-python_exec' || submissions.has(next.toolCallId)
  })
  const counts = [
    [actions.filter((part) => part.type === 'tool-python_exec').length, 'Python action'],
    [actions.filter((part) => part.type === 'data-web-call').length, 'web request'],
    [actions.filter((part) => part.type === 'tool-subagent').length, 'delegated action'],
  ] as const
  const answered = parts.some((part) => part.type === 'text' && part.text.trim())
    && (!details || !['failed', 'timeout', 'cancelled'].includes(details.data.completion))
  const recovered = !live && answered ? actions.filter((part, index) => {
    const failed = part.type === 'data-web-call' ? part.data.outcome === 'failed' : part.state === 'output-error'
    return failed && actions.slice(index + 1).some((later) => {
      if (later.type !== part.type) return false
      return later.type === 'data-web-call'
        ? ['results', 'no_results', 'opened', 'truncated'].includes(later.data.outcome)
        : later.state === 'output-available' && later.preliminary !== true
    })
  }).length : 0
  const summary = [...counts, [recovered, 'recovered error'] as const]
    .flatMap(([count, label]) => count ? [`${count} ${label}${count === 1 ? '' : 's'}`] : [])
  const current = actions.findLast((part) => part.type === 'data-web-call'
    ? part.data.outcome === 'running'
    : part.state === 'input-streaming' || part.state === 'input-available'
      || (part.state === 'output-available' && part.preliminary === true))
  const liveLabel = current?.type === 'tool-python_exec' ? 'Running Python'
    : current?.type === 'data-web-call' ? 'Researching the web'
      : current?.type === 'tool-subagent' ? 'Delegating' : 'Working'

  function renderPart(part: FleetPart, index: number) {
    switch (part.type) {
      case 'data-skills':
        return part.data.skills.length > 0 && (
          <div key={part.id ?? index} className="flex flex-wrap gap-1.5" aria-label="Activated skills">
            {part.data.skills.map(({ name, score }) => (
              <Badge key={name} variant="secondary">
                @{name} <span title="Skill score">{Math.round(score * 100)}%</span>
              </Badge>
            ))}
          </div>
        )
      case 'data-web-call':
        return <WebResearchActivity key={part.id} calls={[part.data]} live={live} />
      case 'reasoning': {
        const streaming = live && part.state === 'streaming'
        return (
          <TraceCollapsible key={`${index}:${streaming}`} defaultOpen={streaming}>
            <TraceCollapsibleTrigger>
              <TraceLine status={streaming ? 'running' : undefined}>
                <TraceIcon><Brain /></TraceIcon>
                <TraceTitle>{streaming ? 'Reasoning' : 'Reasoning notes'}</TraceTitle>
              </TraceLine>
            </TraceCollapsibleTrigger>
            <TraceCollapsibleContent>
              <div className="markdown text-sm" onClick={followMarkdownFragment}><Markdown remarkPlugins={[remarkGfm]}>{part.text}</Markdown></div>
            </TraceCollapsibleContent>
          </TraceCollapsible>
        )
      }
      case 'tool-python_exec': {
        const previous = parts[index - 1]
        return <PythonToolTrace key={part.toolCallId} part={part} live={live}>
          {previous?.type === 'reasoning' && renderPart(previous, index - 1)}
        </PythonToolTrace>
      }
      case 'tool-subagent':
        return (
          <SubagentTrace key={part.toolCallId} part={part} live={live}>
            {part.state === 'output-available' && (
              <MessageParts parts={part.output.parts} live={live && part.preliminary === true} preview={preview} download={download} />
            )}
          </SubagentTrace>
        )
      case 'data-file-tree': {
        const artifacts = part.data.files.filter((file) => file.kind === 'artifact')
        return artifacts.length > 0 && (
          <TraceCollapsible key={part.id ?? index} defaultOpen>
            <TraceCollapsibleTrigger>
              <TraceLine><TraceIcon><FolderDown /></TraceIcon><TraceTitle>Artifacts</TraceTitle></TraceLine>
            </TraceCollapsibleTrigger>
            <TraceCollapsibleContent><FileTree files={artifacts} preview={preview} download={download} /></TraceCollapsibleContent>
          </TraceCollapsible>
        )
      }
      case 'text':
        return <div className="markdown min-w-0" key={index} onClick={followMarkdownFragment}><Markdown remarkPlugins={[remarkGfm]}>{part.text}</Markdown></div>
      default:
        return null
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {parts.map((part, index) => part.type === 'data-skills' && renderPart(part, index))}
      {activity.length > 0 && (
        <Trace>
          <TraceCollapsible open={activityOpen ?? live} onOpenChange={setActivityOpen}>
            <TraceCollapsibleTrigger>
              <TraceLine status={live ? 'running' : undefined}>
                <TraceIcon><Activity /></TraceIcon>
                <TraceTitle className="whitespace-normal">
                  {['Activity', ...(live ? [liveLabel] : summary)].join(' · ')}
                </TraceTitle>
              </TraceLine>
            </TraceCollapsibleTrigger>
            <TraceCollapsibleContent>
              <Trace>{activity.map(({ part, index }) => renderPart(part, index))}</Trace>
            </TraceCollapsibleContent>
          </TraceCollapsible>
        </Trace>
      )}
      {parts.map((part, index) => (part.type === 'text' || part.type === 'data-file-tree') && renderPart(part, index))}
      {(details || submissions.size > 0) && (
        <Trace>
          <TurnDetailsActivity details={details?.data} submissions={[...submissions.values()]} refine={live ? undefined : refine} />
        </Trace>
      )}
    </div>
  )
}

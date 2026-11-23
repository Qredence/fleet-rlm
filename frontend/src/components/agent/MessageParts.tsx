import { Brain, FolderDown } from 'lucide-react'
import type { ReactNode } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Badge } from '@/components/ui/badge'
import {
  Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceIcon, TraceLine, TraceTitle,
} from '@/components/ui/trace'
import { followMarkdownFragment } from '@/lib/markdown-fragments'
import type { FleetPart } from '@/lib/messages'
import { FileTree } from './FileTree'
import { PythonToolTrace } from './PythonToolTrace'
import { SubagentTrace } from './SubagentTrace'

export function MessageParts({ parts, live = false, preview, download }: {
  parts: FleetPart[]
  live?: boolean
  preview: (path: string) => void
  download: (path: string) => void
}) {
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
      case 'tool-python_exec':
        return <PythonToolTrace key={part.toolCallId} part={part} live={live} />
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

  const rows: ReactNode[] = []
  let traceRows: ReactNode[] = []
  let traceStart = 0
  function flushTrace() {
    if (!traceRows.length) return
    rows.push(<Trace key={`trace-${traceStart}`}>{traceRows}</Trace>)
    traceRows = []
  }

  parts.forEach((part, index) => {
    const row = renderPart(part, index)
    if (!row) return

    if (part.type === 'text' || part.type === 'data-skills') {
      flushTrace()
      rows.push(row)
    } else {
      if (!traceRows.length) traceStart = index
      traceRows.push(row)
    }
  })
  flushTrace()

  return <div className="flex min-w-0 flex-col gap-3">{rows}</div>
}

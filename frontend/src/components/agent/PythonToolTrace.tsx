import { CircleAlert, Terminal } from 'lucide-react'
import { useState } from 'react'
import { CodeBlock, CodeBlockActions, CodeBlockContent, CodeBlockCopyButton, CodeBlockHeader, CodeBlockLine, CodeBlockTitle } from '@/components/ui/code-block'
import {
  Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceDescription, TraceIcon, TraceLine, TraceTitle,
} from '@/components/ui/trace'
import { useHighlightedCode } from '@/hooks/use-highlighted-code'
import type { FleetPart } from '@/lib/messages'

type PythonPart = Extract<FleetPart, { type: 'tool-python_exec' }>

function PythonCode({ code }: { code: string }) {
  const lines = useHighlightedCode(code, 'python')
  return (
    <CodeBlock size="sm">
      <CodeBlockHeader>
        <CodeBlockTitle>Python</CodeBlockTitle>
        <CodeBlockActions><CodeBlockCopyButton value={code} /></CodeBlockActions>
      </CodeBlockHeader>
      <CodeBlockContent className="max-h-80" aria-label="Python source" data-language="python">
        {lines.map((line, index) => <CodeBlockLine key={index}>{line}</CodeBlockLine>)}
      </CodeBlockContent>
    </CodeBlock>
  )
}

export function StackTrace({ error }: { error: string }) {
  let title = error.split('\n')[0] || 'Error'
  let stack = error
  try {
    const parsed = JSON.parse(error)
    if (typeof parsed?.name === 'string' && typeof parsed.message === 'string' && typeof parsed.traceback === 'string') {
      title = [parsed.name, parsed.message].filter(Boolean).join(': ')
      stack = [title, parsed.traceback].filter(Boolean).join('\n')
    }
  } catch {
    // Plain tool errors already contain displayable text.
  }
  return (
    <Trace>
      <TraceCollapsible defaultOpen>
        <TraceCollapsibleTrigger>
          <TraceLine status="error">
            <TraceIcon><CircleAlert /></TraceIcon>
            <TraceTitle>{title}</TraceTitle>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <CodeBlock size="sm" variant="destructive">
            <CodeBlockContent wrap className="max-h-80" aria-label="Error traceback">{stack}</CodeBlockContent>
          </CodeBlock>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}

export function PythonToolTrace({ part, live }: { part: PythonPart; live: boolean }) {
  const pending = part.state === 'input-streaming' || part.state === 'input-available'
  const running = live && pending
  const failed = part.state === 'output-error'
  const [open, setOpen] = useState(running || failed)
  const raw = 'rawInput' in part ? part.rawInput : undefined
  const code = typeof part.input === 'string' ? part.input : typeof raw === 'string' ? raw : ''
  const output = part.state === 'output-available' ? part.output : undefined
  const stdout = typeof output === 'string' ? output : JSON.stringify(output, null, 2)
  const title = failed ? 'Python execution failed' : running ? 'Running Python' : pending ? 'Python execution interrupted' : 'Ran Python'

  const line = (
    <TraceLine status={failed ? 'error' : running ? 'running' : undefined}>
      <TraceIcon><Terminal /></TraceIcon>
      <TraceTitle>{title}</TraceTitle>
      {running && !code && <TraceDescription>Waiting for Python source…</TraceDescription>}
    </TraceLine>
  )
  if (!code && stdout === undefined && !failed) return line

  return (
    <TraceCollapsible open={open} onOpenChange={setOpen}>
      <TraceCollapsibleTrigger>{line}</TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <div className="flex min-w-0 flex-col gap-2">
          {code && <PythonCode code={code} />}
          {stdout !== undefined && (
            <CodeBlock size="sm">
              <CodeBlockHeader>
                <CodeBlockTitle>{typeof output === 'string' ? 'Stdout' : 'Result'}</CodeBlockTitle>
              </CodeBlockHeader>
              <CodeBlockContent className="max-h-80" wrap>{stdout || 'No output'}</CodeBlockContent>
            </CodeBlock>
          )}
          {failed && <StackTrace error={part.errorText} />}
        </div>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

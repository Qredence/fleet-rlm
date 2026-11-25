import { Globe, SquareTerminal } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { Button } from '@/components/ui/button'
import { errorMessage, type Memory } from '@/lib/api'
import type { TurnDetails, WebCall } from '@/lib/messages'
import {
  TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceIcon, TraceLine, TraceTitle,
} from '@/components/ui/trace'

const webOutcomes: Record<WebCall['outcome'], string> = {
  running: 'Running',
  results: 'Results found',
  no_results: 'No results',
  opened: 'Page opened',
  truncated: 'Page opened, extract truncated',
  failed: 'Failed',
  exhausted: 'Allowance exhausted',
  interrupted: 'Interrupted',
}

const completionLabels: Record<TurnDetails['completion'], string> = {
  submit: 'Submitted by the RLM',
  iteration_limit: 'Iteration limit reached, answer extracted from trajectory',
  unknown: 'Unavailable',
  failed: 'Failed',
  timeout: 'Timed out',
  cancelled: 'Cancelled',
}

function elapsed(ms: number) {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

export function WebResearchActivity({ calls, live }: { calls: WebCall[]; live: boolean }) {
  const usage = calls.reduce<WebCall | undefined>((latest, call) => {
    if (call.used === null || (latest?.used ?? -1) > call.used) return latest
    return call
  }, undefined)
  const running = live && calls.some((call) => call.outcome === 'running')

  return (
    <TraceCollapsible defaultOpen={running}>
      <TraceCollapsibleTrigger>
        <TraceLine status={running ? 'running' : undefined}>
          <TraceIcon><Globe /></TraceIcon>
          <TraceTitle>
            Web research{usage ? ` · ${usage.used} of ${usage.limit} requests used` : ''}
          </TraceTitle>
        </TraceLine>
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <ul className="divide-y text-sm">
          {calls.map((call) => (
            <li key={call.id} className="grid min-w-0 gap-1 py-2">
              <div className="flex flex-wrap gap-x-2">
                <span className="font-medium">{call.tool === 'web_search' ? 'Search' : 'Open page'}</span>
                <span className="text-muted-foreground">
                  {webOutcomes[!live && call.outcome === 'running' ? 'interrupted' : call.outcome]}
                  {call.elapsed_ms === null ? '' : ` · ${elapsed(call.elapsed_ms)}`}
                </span>
              </div>
              {call.target && <span className="wrap-anywhere">{call.target}</span>}
            </li>
          ))}
        </ul>
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

export function TurnDetailsActivity({ details, refine }: {
  details: TurnDetails
  refine?: () => Promise<Memory | null>
}) {
  const [lesson, setLesson] = useState<Memory | null>()
  const [refining, setRefining] = useState(false)
  const [error, setError] = useState('')
  const canRefine = refine && details.runtime === 'dspy.RLM'
    && ['submit', 'iteration_limit', 'unknown'].includes(details.completion)

  async function requestRefinement() {
    if (!refine || refining) return
    setError('')
    setRefining(true)
    try {
      setLesson(await refine())
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setRefining(false)
    }
  }

  return (
    <TraceCollapsible>
      <TraceCollapsibleTrigger>
        <TraceLine>
          <TraceIcon><SquareTerminal /></TraceIcon>
          <TraceTitle>Turn details</TraceTitle>
        </TraceLine>
      </TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Runtime</dt>
          <dd>{details.runtime ?? 'RLM did not start'}</dd>
          <dt className="text-muted-foreground">Root actions</dt><dd>{details.root_actions}</dd>
          <dt className="text-muted-foreground">Child RLMs started</dt><dd>{details.child_rlms}</dd>
          <dt className="text-muted-foreground">Sub-LM queries</dt><dd>{details.sub_lm_queries}</dd>
          {details.elapsed_ms !== null && <>
            <dt className="text-muted-foreground">RLM elapsed</dt><dd>{elapsed(details.elapsed_ms)}</dd>
          </>}
          <dt className="text-muted-foreground">Completion</dt><dd>{completionLabels[details.completion]}</dd>
        </dl>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {details.trace_url && /^https?:\/\//i.test(details.trace_url) && (
            <a className="text-sm underline" href={details.trace_url} target="_blank" rel="noreferrer">
              Open in MLflow
            </a>
          )}
          {canRefine && (lesson === undefined ? (
            <Button variant="outline" size="sm" disabled={refining} onClick={() => void requestRefinement()}>
              {refining ? 'Refining…' : 'Refine this turn'}
            </Button>
          ) : lesson ? (
            <Link className="text-sm underline" to={`/memories?session_id=${lesson.session_id}&memory_id=${lesson.id}`}>
              Review lesson
            </Link>
          ) : <span role="status" className="text-sm">No useful lesson found.</span>)}
        </div>
        {error && <p role="alert" className="mt-1 text-sm text-destructive">{error}</p>}
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

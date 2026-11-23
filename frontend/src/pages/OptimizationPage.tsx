import { Brain, Code2, Package } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { CodeBlock, CodeBlockActions, CodeBlockContent, CodeBlockCopyButton, CodeBlockHeader, CodeBlockTitle } from '@/components/ui/code-block'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage, type FleetApi, type WorkspaceSettingsView } from '@/lib/api'

const targets = [
  { title: 'RLM Compiled Program', field: 'optimized_program_path', target: 'rlm', icon: Code2, description: 'Optimize the root program with GEPA.' },
  { title: 'Skill Selector', field: 'skill_selector_path', target: 'skills', icon: Package, description: 'Calibrate skill selection.', dataset: 'skills.jsonl' },
  { title: 'Memory Gate', field: 'memory_gate_path', target: 'memory', icon: Brain, description: 'Calibrate when Fleet saves memories.', dataset: 'memory.jsonl' },
] as const

export function OptimizationPage({ api }: { api: FleetApi }) {
  const [settings, setSettings] = useState<WorkspaceSettingsView>()
  const [error, setError] = useState('')
  const [attempt, retry] = useState(0)
  useEffect(() => {
    const request = new AbortController()
    void api.getSettings(request.signal).then((result) => {
      if (!request.signal.aborted) setSettings(result)
    }).catch((failure) => {
      if (!request.signal.aborted) setError(errorMessage(failure))
    })
    return () => request.abort()
  }, [api, attempt])

  return (
    <>
      <PageHeader title="Optimization" />
      <ScrollArea className="min-h-0 flex-1">
        <section className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-4 sm:p-7" aria-label="Optimization overview">
          <div className="flex flex-col gap-3">
            <h2 className="text-base font-semibold">Deployment configuration</h2>
            {error && (
              <Alert variant="destructive">
                <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
                  {error}
                  <Button variant="outline" size="sm" onClick={() => { setError(''); retry((value) => value + 1) }}>Retry</Button>
                </AlertDescription>
              </Alert>
            )}
            <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,14rem),1fr))]">
              {targets.map(({ title, field, icon: Icon, description }) => (
                <Card key={field} size="sm" className="min-w-0">
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                      {title}
                    </CardTitle>
                    <CardDescription>{description}</CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-2">
                    {settings ? (
                      <>
                        <Badge className="w-fit" variant={settings.status[field] ? 'secondary' : 'outline'}>
                          {settings.status[field] ? 'Configured' : 'Not configured'}
                        </Badge>
                        {settings.status[field] && <p className="wrap-anywhere font-mono text-xs text-muted-foreground">{settings.status[field]}</p>}
                      </>
                    ) : error ? (
                      <span className="text-xs text-muted-foreground">Configuration unavailable</span>
                    ) : <Skeleton role="status" aria-label={`Loading ${title} status`} className="h-5 w-24 motion-reduce:animate-none" />}
                  </CardContent>
                </Card>
              ))}
            </div>
            {settings && (
              <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_minmax(0,1fr)]">
                <dt className="text-muted-foreground">Decision model</dt>
                <dd className="wrap-anywhere">{settings.effective.decision_model}</dd>
                {settings.status.mlflow_tracking_uri && (
                  <>
                    <dt className="text-muted-foreground">MLflow experiment</dt>
                    <dd className="wrap-anywhere">{settings.status.mlflow_experiment}</dd>
                  </>
                )}
              </dl>
            )}
          </div>

          <div className="flex min-w-0 flex-col gap-3">
            <h2 className="text-base font-semibold">Run offline</h2>
            <p className="text-sm text-muted-foreground">
              Run these commands from <code>backend</code> with <code>NEON_BRANCH_URL</code> set to an isolated Neon branch.
              Skill Selector and Memory Gate require labeled JSONL datasets.
            </p>
            {targets.map(({ title, target, ...details }) => {
              const dataset = 'dataset' in details ? ` --dataset ${details.dataset}` : ''
              const command = `uv run python -m fleet_rlm.optimize --target ${target} --database-url "$NEON_BRANCH_URL"${dataset}`
              return (
                <CodeBlock key={target} size="sm">
                  <CodeBlockHeader>
                    <CodeBlockTitle>{title}</CodeBlockTitle>
                    <CodeBlockActions><CodeBlockCopyButton value={command} aria-label={`Copy ${title} command`} /></CodeBlockActions>
                  </CodeBlockHeader>
                  <CodeBlockContent wrap>{command}</CodeBlockContent>
                </CodeBlock>
              )
            })}
            <p className="text-sm text-muted-foreground">
              For ReAnchor deployments, pin the exact TypeSafe Jev snapshot in <code>FLEET_DECISION_MODEL</code> and use that snapshot as the effective decision model.
            </p>
          </div>
        </section>
      </ScrollArea>
    </>
  )
}

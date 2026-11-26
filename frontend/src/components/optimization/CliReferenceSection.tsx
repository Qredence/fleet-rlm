import { Badge } from '@/components/ui/badge'
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from '@/components/ui/code-block'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { OptimizationCapabilities } from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

const CLI_SNIPPETS = [
  {
    target: 'skills',
    title: 'Skill Selector (ReAnchor)',
    command:
      'uv run python -m fleet_rlm.optimize --target skills --database-url "$NEON_BRANCH_URL" --dataset eval_datasets/skills.jsonl',
  },
  {
    target: 'memory',
    title: 'Memory Gate (ReAnchor)',
    command:
      'uv run python -m fleet_rlm.optimize --target memory --database-url "$NEON_BRANCH_URL" --dataset eval_datasets/memory.jsonl',
  },
  {
    target: 'rlm',
    title: 'Root RLM Program (GEPA)',
    command:
      'uv run python -m fleet_rlm.optimize --target rlm --database-url "$NEON_BRANCH_URL" --dataset eval_datasets/rlm.jsonl --max-metric-calls 80 --holdout-repeats 3',
  },
] as const

export function CliReferenceSection({
  capabilities,
}: {
  capabilities: OptimizationCapabilities | null
}) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(18rem,5fr)]">
      <div className="flex min-w-0 flex-col gap-3">
        <h2
          className={typeClass('title')}
          style={{ fontVariationSettings: fontWeights.semibold }}
        >
          Offline CLI workflow
        </h2>
        <p className={`${typeClass('body')} text-muted-foreground`}>
          Run these commands from <code>backend/</code> with <code>NEON_BRANCH_URL</code> pointed
          at an isolated Neon branch. Promoted ReAnchor artifacts pin their calibrated{' '}
          <code>(provider, decision_model)</code> per target automatically.
        </p>
        {CLI_SNIPPETS.map(({ target, title, command }) => (
          <CodeBlock key={target} size="sm">
            <CodeBlockHeader>
              <CodeBlockTitle>{title}</CodeBlockTitle>
              <CodeBlockActions>
                <Tooltip>
                  <TooltipTrigger render={<span className="inline-flex" />}>
                    <CodeBlockCopyButton value={command} aria-label={`Copy ${title} command`} />
                  </TooltipTrigger>
                  <TooltipContent side="top">Copy CLI command</TooltipContent>
                </Tooltip>
              </CodeBlockActions>
            </CodeBlockHeader>
            <CodeBlockContent wrap>{command}</CodeBlockContent>
          </CodeBlock>
        ))}
      </div>

      <div className="flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4">
        <h3
          className={typeClass('subtitle')}
          style={{ fontVariationSettings: fontWeights.semibold }}
        >
          Runtime environment &amp; security policy
        </h3>
        {capabilities ? (
          <dl className="grid gap-3">
            <div className="flex flex-col gap-0.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>Decision model</dt>
              <dd className={`${typeClass('body')} wrap-anywhere font-mono text-foreground`}>
                {capabilities.decision_model}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>Root &amp; sub LM</dt>
              <dd className={`${typeClass('body')} wrap-anywhere font-mono text-foreground`}>
                {capabilities.llm_model} · {capabilities.sub_llm_model}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5 border-t border-border/60 pt-2.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>
                Dataset storage backends
              </dt>
              <dd className="mt-1 flex flex-wrap gap-1.5">
                <Badge variant={capabilities.neon_storage_enabled === false ? 'outline' : 'secondary'}>
                  Neon S3: {capabilities.neon_storage_enabled === false ? 'Unavailable' : 'Ready'}
                </Badge>
                <Badge variant={capabilities.daytona_volume_enabled ? 'default' : 'outline'}>
                  Daytona Volume:{' '}
                  {capabilities.daytona_volume_enabled
                    ? capabilities.daytona_dataset_volume || 'fleet-eval-datasets'
                    : 'Not configured'}
                </Badge>
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>
                Provider credentials encryption
              </dt>
              <dd className={`${typeClass('body')} text-foreground`}>
                {capabilities.credentials_encryption_enabled
                  ? 'Configured (Fernet AEAD active)'
                  : 'Not configured — keyless local providers only'}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>
                Private / local LLM endpoints
              </dt>
              <dd className={`${typeClass('body')} text-foreground`}>
                {capabilities.allow_private_llm_endpoints
                  ? 'Allowed (localhost / RFC1918 enabled; IMDS still blocked)'
                  : 'Blocked by default (public HTTPS endpoints only)'}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>
                Local DSPy interpreter fallback
              </dt>
              <dd className={`${typeClass('body')} text-foreground`}>
                {capabilities.allow_local_interpreter
                  ? 'Enabled (FLEET_ALLOW_LOCAL_INTERPRETER=true)'
                  : 'Disabled (isolated Daytona sandboxes required)'}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5 border-t border-border/60 pt-2.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>
                Configured optimization branch
              </dt>
              <dd className={`${typeClass('body')} wrap-anywhere text-foreground`}>
                {capabilities.configured_branch_enabled
                  ? (capabilities.configured_branch_label ?? 'Configured')
                  : 'Not configured'}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>
                Auto ephemeral Neon branches
              </dt>
              <dd className={`${typeClass('body')} text-foreground`}>
                {capabilities.auto_neon_enabled ? 'Enabled' : 'Not configured'}
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className={`${typeClass('caption')} text-muted-foreground`}>MLflow tracking</dt>
              <dd className={`${typeClass('body')} wrap-anywhere text-foreground`}>
                {capabilities.mlflow_tracking_uri
                  ? `${capabilities.mlflow_experiment} (${capabilities.mlflow_tracking_uri})`
                  : 'Disabled'}
              </dd>
            </div>
          </dl>
        ) : (
          <p className={`${typeClass('caption')} text-muted-foreground`}>
            Environment status unavailable.
          </p>
        )}
      </div>
    </div>
  )
}

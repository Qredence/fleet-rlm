import {
  Activity,
  ChevronDown,
  Database,
  HelpCircle,
  KeyRound,
  Plus,
  RefreshCw,
  Terminal,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { ActiveRunBanner } from '@/components/optimization/ActiveRunBanner'
import { CliReferenceSection } from '@/components/optimization/CliReferenceSection'
import { DatasetsSection } from '@/components/optimization/DatasetsSection'
import {
  LaunchOptimizationDialog,
  type LaunchDefaults,
} from '@/components/optimization/LaunchOptimizationDialog'
import { ProvidersSection } from '@/components/optimization/ProvidersSection'
import { RunFullDiffSheet } from '@/components/optimization/RunFullDiffSheet'
import { RunHistorySection } from '@/components/optimization/RunHistorySection'
import { TargetSummaryGrid } from '@/components/optimization/TargetSummaryGrid'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Tabs, TabsItem, TabsPanel } from '@/components/ui/fluid-tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useOptimizations } from '@/hooks/use-optimizations'
import {
  errorMessage,
  type FleetApi,
  type LLMProviderView,
  type LMRoleSelection,
  type OptimizationJobDetail,
  type OptimizationJobSummary,
  type OptimizationTarget,
} from '@/lib/api'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

const TAB_INDEX = {
  runs: 0,
  datasets: 1,
  providers: 2,
  cli: 3,
} as const

function extractRoleBinding(
  config: Record<string, unknown> | undefined,
  key: string,
  fallbackProviderId?: string | null,
  fallbackModel?: string | null,
): LMRoleSelection | null {
  const models = config?.models as Record<string, unknown> | undefined
  const raw = models?.[key.replace(/_config$/, '')] ?? config?.[key]
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const rec = raw as Record<string, unknown>
    const providerId = typeof rec.provider_id === 'string' ? rec.provider_id : null
    const model = typeof rec.model === 'string' ? rec.model : null
    const effort = rec.reasoning_effort
    if (providerId || model) return {
      provider_id: providerId,
      model,
      reasoning_effort: effort === 'low' || effort === 'medium' || effort === 'high' || effort === 'xhigh'
        ? effort : undefined,
    }
  }
  if (fallbackProviderId || fallbackModel) {
    return { provider_id: fallbackProviderId ?? null, model: fallbackModel ?? null }
  }
  return null
}

function rerunDefaultsForJob(job: OptimizationJobSummary): LaunchDefaults {
  const cfg = job.config_json
  return {
    target: job.target,
    datasetId:
      job.dataset_source === 'custom' && job.dataset_id
        ? job.dataset_id
        : job.dataset_source === 'builtin'
          ? `builtin:${job.target}`
          : 'db',
    programKind: (job.program_kind === 'flex' ? 'flex' : 'rlm') as 'rlm' | 'flex',
    seed: typeof cfg?.seed === 'number' ? cfg.seed : 0,
    maxMetricCalls: typeof cfg?.max_metric_calls === 'number' ? cfg.max_metric_calls : 80,
    holdoutRepeats: typeof cfg?.holdout_repeats === 'number' ? cfg.holdout_repeats : 3,
    rootLm: extractRoleBinding(cfg, 'root_lm'),
    subLm: extractRoleBinding(cfg, 'sub_lm'),
    reflectionLm: extractRoleBinding(
      cfg,
      'reflection_lm_config',
      null,
      typeof cfg?.reflection_lm === 'string' ? cfg.reflection_lm : null,
    ),
    judgeLm: extractRoleBinding(
      cfg,
      'judge_lm_config',
      null,
      typeof cfg?.judge_lm === 'string' ? cfg.judge_lm : null,
    ),
    decisionLm: extractRoleBinding(
      cfg,
      'decision_lm',
      job.decision_provider_id ?? null,
      job.resolved_decision_model,
    ),
  }
}

export function OptimizationPage({ api }: { api: FleetApi }) {
  const {
    overview,
    datasets,
    jobs,
    selectedJobId,
    setSelectedJobId,
    selectedJob,
    liveJob,
    streamWarning,
    loading,
    loadingSelected,
    error,
    setError,
    announcement,
    setAnnouncement,
    targetFilter,
    setTargetFilter,
    statusFilter,
    setStatusFilter,
    refreshAll,
    trackStartedJob,
    dismissBanner,
  } = useOptimizations(api)

  const [activeTab, setActiveTab] = useState<number>(TAB_INDEX.runs)
  const [launchOpen, setLaunchOpen] = useState(false)
  const [launchDefaults, setLaunchDefaults] = useState<LaunchDefaults>({})
  const [diffJob, setDiffJob] = useState<OptimizationJobDetail | null>(null)
  const [guideOpen, setGuideOpen] = useState(false)

  const [providers, setProviders] = useState<LLMProviderView[] | null>(null)
  const [encryptionConfigured, setEncryptionConfigured] = useState(true)
  const [allowPrivateEndpoints, setAllowPrivateEndpoints] = useState(false)
  const [providersError, setProvidersError] = useState<string | null>(null)
  const [loadingProviders, setLoadingProviders] = useState(true)
  const providersControllerRef = useRef<AbortController | null>(null)

  const refreshProviders = useCallback(
    async (opts?: { silent?: boolean }) => {
      providersControllerRef.current?.abort()
      const controller = new AbortController()
      providersControllerRef.current = controller
      setProvidersError(null)
      if (!opts?.silent) setLoadingProviders(true)
      try {
        const res = await api.listOptimizationProviders(controller.signal)
        if (controller.signal.aborted) return
        setProviders(res.providers)
        setEncryptionConfigured(res.encryption_configured)
        setAllowPrivateEndpoints(res.allow_private_endpoints)
      } catch (failure) {
        if (!controller.signal.aborted) {
          setProvidersError(errorMessage(failure))
        }
      } finally {
        if (!controller.signal.aborted) setLoadingProviders(false)
      }
    },
    [api],
  )

  useEffect(() => {
    void refreshProviders()
    return () => providersControllerRef.current?.abort()
  }, [refreshProviders])

  const effectiveEncryptionConfigured =
    overview?.capabilities.credentials_encryption_enabled ?? encryptionConfigured
  const effectiveAllowPrivateEndpoints =
    overview?.capabilities.allow_private_llm_endpoints ?? allowPrivateEndpoints

  const activeOverviewJob = overview?.targets.find((item) => item.active_job)?.active_job ?? null
  const activeRunForBanner =
    liveJob ??
    (activeOverviewJob && selectedJob?.id === activeOverviewJob.id ? selectedJob : null)

  function openLaunch(defaults: LaunchDefaults = {}) {
    setLaunchDefaults(defaults)
    setLaunchOpen(true)
  }

  async function handleRefreshEverything() {
    await Promise.all([refreshAll(), refreshProviders()])
  }

  async function handleCancel(jobId: string) {
    try {
      const updated = await api.cancelOptimizationJob(jobId)
      trackStartedJob(updated)
    } catch (failure) {
      setError(errorMessage(failure))
    }
  }

  async function handlePromote(
    jobId: string,
    opts: { confirm_non_qualifying: boolean; pin_decision_model: boolean },
  ) {
    await api.promoteOptimizationJob(jobId, opts)
    setAnnouncement('Artifact promoted to live traffic.')
    await refreshAll({ silent: true })
  }

  async function handleRollback(target: OptimizationTarget, mode: 'previous' | 'env') {
    await api.rollbackOptimizationTarget(target, { mode })
    setAnnouncement(`Rolled back ${target} optimization target.`)
    await refreshAll({ silent: true })
  }

  return (
    <>
      <PageHeader title="Optimization">
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void handleRefreshEverything()}
            disabled={loading}
          >
            <RefreshCw
              className={`size-3.5 ${loading ? 'animate-spin motion-reduce:animate-none' : ''}`}
              aria-hidden="true"
            />
            Refresh
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={() => openLaunch({})}
            data-testid="new-optimization-run-button"
          >
            <Plus className="size-3.5" aria-hidden="true" />
            New Run
          </Button>
        </div>
      </PageHeader>

      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <section
          className="reveal-product mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-7"
          aria-label="Optimization control plane"
        >
          {error && (
            <Alert variant="destructive">
              <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
                <span>{error}</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleRefreshEverything()}
                >
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          )}

          {streamWarning && streamWarning !== error && (
            <Alert>
              <AlertDescription>{streamWarning}</AlertDescription>
            </Alert>
          )}

          <TargetSummaryGrid
            overview={overview}
            loading={loading}
            onLaunchTarget={(target) => openLaunch({ target })}
            onRollbackTarget={handleRollback}
          />

          <ActiveRunBanner
            job={activeRunForBanner}
            onCancel={handleCancel}
            onInspect={(jobId) => {
              setSelectedJobId(jobId)
              setActiveTab(TAB_INDEX.runs)
            }}
            onDismiss={dismissBanner}
          />

          <div className="flex min-w-0 flex-col gap-4">
            <Tabs
              selectedIndex={activeTab}
              onSelect={setActiveTab}
              idPrefix="optimization-tabs"
              className="workspace-fluid-tabs"
            >
              <TabsItem index={TAB_INDEX.runs} icon={Activity} label="Runs" />
              <TabsItem index={TAB_INDEX.datasets} icon={Database} label="Datasets" />
              <TabsItem index={TAB_INDEX.providers} icon={KeyRound} label="Providers" />
              <TabsItem index={TAB_INDEX.cli} icon={Terminal} label="CLI & Environment" />
            </Tabs>

            <TabsPanel index={TAB_INDEX.runs} selectedIndex={activeTab} idPrefix="optimization-tabs">
              <div className="flex flex-col gap-4">
                {/* Collapsible plain-English process guide */}
                <div className="rounded-xl border bg-card">
                  <button
                    type="button"
                    aria-expanded={guideOpen}
                    onClick={() => setGuideOpen((prev) => !prev)}
                    className="flex w-full cursor-pointer items-center justify-between gap-2 px-4 py-2.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                    data-testid="how-optimization-works-toggle"
                  >
                    <span className="flex items-center gap-2">
                      <HelpCircle className="size-4 text-muted-foreground" aria-hidden="true" />
                      <span
                        className={`${typeClass('body')} text-foreground`}
                        style={{ fontVariationSettings: fontWeights.semibold }}
                      >
                        How optimization works
                      </span>
                      <span className={`${typeClass('caption')} hidden text-muted-foreground sm:inline`}>
                        Prepare dataset → Run isolated eval → Review diff &amp; gate checks → Apply
                        live or roll back
                      </span>
                    </span>
                    <ChevronDown
                      className={`size-4 shrink-0 text-muted-foreground transition-transform duration-160 ${guideOpen ? 'rotate-180' : ''}`}
                      aria-hidden="true"
                    />
                  </button>

                  {guideOpen && (
                    <ol className="grid grid-cols-1 gap-3 border-t border-border/60 p-4 sm:grid-cols-2 lg:grid-cols-4">
                      <li className="flex flex-col gap-1">
                        <span
                          className={`${typeClass('caption')} text-foreground`}
                          style={{ fontVariationSettings: fontWeights.semibold }}
                        >
                          1. Prepare dataset
                        </span>
                        <span className={`${typeClass('caption')} text-muted-foreground`}>
                          Use a built-in eval set or upload a validated JSONL dataset to Neon Object
                          Storage or a managed Daytona Volume.
                        </span>
                      </li>
                      <li className="flex flex-col gap-1">
                        <span
                          className={`${typeClass('caption')} text-foreground`}
                          style={{ fontVariationSettings: fontWeights.semibold }}
                        >
                          2. Run isolated eval
                        </span>
                        <span className={`${typeClass('caption')} text-muted-foreground`}>
                          ReAnchor (Skills/Memory) or GEPA (Root RLM) executes against an isolated
                          Neon branch using your selected LLM providers.
                        </span>
                      </li>
                      <li className="flex flex-col gap-1">
                        <span
                          className={`${typeClass('caption')} text-foreground`}
                          style={{ fontVariationSettings: fontWeights.semibold }}
                        >
                          3. Review diff &amp; gate checks
                        </span>
                        <span className={`${typeClass('caption')} text-muted-foreground`}>
                          Inspect the Promotion Gate Checklist, unified prompt or parameter diffs,
                          and per-example holdout regressions before applying.
                        </span>
                      </li>
                      <li className="flex flex-col gap-1">
                        <span
                          className={`${typeClass('caption')} text-foreground`}
                          style={{ fontVariationSettings: fontWeights.semibold }}
                        >
                          4. Apply live or roll back
                        </span>
                        <span className={`${typeClass('caption')} text-muted-foreground`}>
                          Promoting activates the SHA-256 verified artifact immediately across all
                          workers; rollback reverts cleanly to a prior promotion or baseline.
                        </span>
                      </li>
                    </ol>
                  )}
                </div>

                <RunHistorySection
                  overview={overview}
                  jobs={jobs}
                  loading={loading}
                  selectedJobId={selectedJobId}
                  selectedJob={selectedJob}
                  loadingSelected={loadingSelected}
                  targetFilter={targetFilter}
                  statusFilter={statusFilter}
                  onTargetFilterChange={setTargetFilter}
                  onStatusFilterChange={setStatusFilter}
                  onSelectJob={setSelectedJobId}
                  onPromote={handlePromote}
                  onRerun={(job) => openLaunch(rerunDefaultsForJob(job))}
                  onOpenFullDiff={(job) => setDiffJob(job)}
                  onLaunchFirst={() => openLaunch({})}
                />
              </div>
            </TabsPanel>

            <TabsPanel
              index={TAB_INDEX.datasets}
              selectedIndex={activeTab}
              idPrefix="optimization-tabs"
            >
              <DatasetsSection
                api={api}
                datasets={datasets}
                capabilities={overview?.capabilities ?? null}
                loading={loading}
                onUploaded={(created) => {
                  setAnnouncement(`Uploaded dataset ${created.name}.`)
                  void refreshAll({ silent: true })
                }}
                onDeleted={() => {
                  setAnnouncement('Deleted custom dataset.')
                  void refreshAll({ silent: true })
                }}
                onRunWithDataset={(dataset) =>
                  openLaunch({ target: dataset.target, datasetId: dataset.id })
                }
              />
            </TabsPanel>

            <TabsPanel
              index={TAB_INDEX.providers}
              selectedIndex={activeTab}
              idPrefix="optimization-tabs"
            >
              <ProvidersSection
                api={api}
                providers={providers}
                encryptionConfigured={effectiveEncryptionConfigured}
                allowPrivateEndpoints={effectiveAllowPrivateEndpoints}
                loading={loadingProviders}
                loadError={providersError}
                onRetry={() => void refreshProviders()}
                onChanged={async (msg) => {
                  if (msg) setAnnouncement(msg)
                  await refreshProviders({ silent: true })
                }}
              />
            </TabsPanel>

            <TabsPanel index={TAB_INDEX.cli} selectedIndex={activeTab} idPrefix="optimization-tabs">
              <CliReferenceSection capabilities={overview?.capabilities ?? null} />
            </TabsPanel>
          </div>
        </section>
      </ScrollArea>

      <LaunchOptimizationDialog
        open={launchOpen}
        onOpenChange={setLaunchOpen}
        api={api}
        capabilities={overview?.capabilities ?? null}
        datasets={datasets ?? []}
        providers={providers ?? []}
        defaults={launchDefaults}
        onStarted={trackStartedJob}
        onManageProviders={() => setActiveTab(TAB_INDEX.providers)}
        onProviderRefreshed={(updated) => {
          setProviders((current) =>
            current ? current.map((item) => (item.id === updated.id ? updated : item)) : [updated],
          )
        }}
      />

      <RunFullDiffSheet
        job={diffJob}
        open={Boolean(diffJob)}
        onOpenChange={(open) => {
          if (!open) setDiffJob(null)
        }}
      />
    </>
  )
}

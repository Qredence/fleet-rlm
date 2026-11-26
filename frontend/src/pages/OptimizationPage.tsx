import { Activity, Database, Plus, RefreshCw, Terminal } from 'lucide-react'
import { useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { ActiveRunBanner } from '@/components/optimization/ActiveRunBanner'
import { CliReferenceSection } from '@/components/optimization/CliReferenceSection'
import { DatasetsSection } from '@/components/optimization/DatasetsSection'
import {
  LaunchOptimizationDialog,
  type LaunchDefaults,
} from '@/components/optimization/LaunchOptimizationDialog'
import { RunHistorySection } from '@/components/optimization/RunHistorySection'
import { TargetSummaryGrid } from '@/components/optimization/TargetSummaryGrid'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Tabs, TabsItem, TabsPanel } from '@/components/ui/fluid-tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useOptimizations } from '@/hooks/use-optimizations'
import { errorMessage, type FleetApi, type OptimizationTarget } from '@/lib/api'

const TAB_INDEX = {
  runs: 0,
  datasets: 1,
  cli: 2,
} as const

export function OptimizationPage({ api }: { api: FleetApi }) {
  const {
    overview,
    datasets,
    jobs,
    selectedJobId,
    setSelectedJobId,
    selectedJob,
    liveJob,
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

  const activeOverviewJob = overview?.targets.find((item) => item.active_job)?.active_job ?? null
  const activeRunForBanner =
    liveJob ??
    (activeOverviewJob && selectedJob?.id === activeOverviewJob.id ? selectedJob : null)

  function openLaunch(defaults: LaunchDefaults = {}) {
    setLaunchDefaults(defaults)
    setLaunchOpen(true)
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
    try {
      await api.promoteOptimizationJob(jobId, opts)
      setAnnouncement('Artifact promoted to live traffic.')
      await refreshAll({ silent: true })
    } catch (failure) {
      setError(errorMessage(failure))
      throw failure
    }
  }

  async function handleRollback(target: OptimizationTarget, mode: 'previous' | 'env') {
    try {
      await api.rollbackOptimizationTarget(target, { mode })
      setAnnouncement(`Rolled back ${target} optimization target.`)
      await refreshAll({ silent: true })
    } catch (failure) {
      setError(errorMessage(failure))
      throw failure
    }
  }

  return (
    <>
      <PageHeader title="Optimization">
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void refreshAll()}
            disabled={loading}
          >
            <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
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
                <Button variant="outline" size="sm" onClick={() => void refreshAll()}>
                  Retry
                </Button>
              </AlertDescription>
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
              <TabsItem index={TAB_INDEX.cli} icon={Terminal} label="CLI & Environment" />
            </Tabs>

            <TabsPanel index={TAB_INDEX.runs} selectedIndex={activeTab} idPrefix="optimization-tabs">
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
                onRerun={(job) =>
                  openLaunch({
                    target: job.target,
                    datasetId:
                      job.dataset_source === 'custom' && job.dataset_id
                        ? job.dataset_id
                        : job.dataset_source === 'builtin'
                          ? `builtin:${job.target}`
                          : 'db',
                    programKind: (job.program_kind === 'flex' ? 'flex' : 'rlm') as 'rlm' | 'flex',
                    seed: typeof job.config_json?.seed === 'number' ? job.config_json.seed : 0,
                    maxMetricCalls:
                      typeof job.config_json?.max_metric_calls === 'number'
                        ? job.config_json.max_metric_calls
                        : 80,
                    holdoutRepeats:
                      typeof job.config_json?.holdout_repeats === 'number'
                        ? job.config_json.holdout_repeats
                        : 3,
                  })
                }
                onLaunchFirst={() => openLaunch({})}
              />
            </TabsPanel>

            <TabsPanel
              index={TAB_INDEX.datasets}
              selectedIndex={activeTab}
              idPrefix="optimization-tabs"
            >
              <DatasetsSection
                api={api}
                datasets={datasets}
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
        defaults={launchDefaults}
        onStarted={trackStartedJob}
      />
    </>
  )
}

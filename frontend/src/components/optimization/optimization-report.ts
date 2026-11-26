import type { OptimizationJobDetail } from '@/lib/api'
import { formatScore } from './holdout-score'

export interface GateCheckRow {
  name: string
  label: string
  passed: boolean
  actual: unknown
  threshold: unknown
  reason: string
}

interface ProvenanceModel {
  role: string
  model: string
  providerName: string | null
  dialect: string | null
}

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function parseGateChecks(job: OptimizationJobDetail): GateCheckRow[] {
  const report = asRecord(job.report_json)
  const raw = report?.gate_checks
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.flatMap((item): GateCheckRow[] => {
      const rec = asRecord(item)
      if (!rec) return []
      return [
        {
          name: typeof rec.name === 'string' ? rec.name : 'gate',
          label: typeof rec.label === 'string' ? rec.label : String(rec.name ?? 'Gate check'),
          passed: rec.passed === true,
          actual: rec.actual,
          threshold: rec.threshold,
          reason: typeof rec.reason === 'string' ? rec.reason : '',
        },
      ]
    })
  }

  // Fallback for legacy runs created before explicit gate_checks telemetry.
  if (job.status !== 'completed') return []
  const fallback: GateCheckRow[] = []
  if (job.baseline_score !== null && job.optimized_score !== null) {
    const improved = job.optimized_score > job.baseline_score
    fallback.push({
      name: 'holdout_score_improved',
      label: 'Holdout score improved over baseline',
      passed: improved,
      actual: job.optimized_score,
      threshold: `> ${job.baseline_score.toFixed(4)}`,
      reason: improved
        ? `Holdout improved from ${formatScore(job.baseline_score, job.target)} to ${formatScore(job.optimized_score, job.target)}`
        : `Holdout ${formatScore(job.optimized_score, job.target)} did not beat baseline ${formatScore(job.baseline_score, job.target)}`,
    })
  }
  if (job.target !== 'rlm') {
    const fpBefore = asNumber(report?.fp_rate_before)
    const fpAfter = asNumber(report?.fp_rate_after)
    if (fpBefore !== null && fpAfter !== null) {
      const ok = fpAfter <= fpBefore
      fallback.push({
        name: 'fp_rate_non_regression',
        label: 'False-positive rate did not regress',
        passed: ok,
        actual: fpAfter,
        threshold: `<= ${fpBefore.toFixed(4)}`,
        reason: ok
          ? `FP rate ${formatScore(fpAfter, 'skills')} <= baseline ${formatScore(fpBefore, 'skills')}`
          : `FP rate regressed from ${formatScore(fpBefore, 'skills')} to ${formatScore(fpAfter, 'skills')}`,
      })
    }
  } else {
    const blockingCount = Array.isArray(report?.blocking_failures)
      ? report.blocking_failures.length
      : null
    const regCount = Array.isArray(report?.paired_regressions)
      ? report.paired_regressions.length
      : null
    if (blockingCount !== null) {
      fallback.push({
        name: 'zero_blocking_failures',
        label: 'Zero blocking case failures',
        passed: blockingCount === 0,
        actual: blockingCount,
        threshold: 0,
        reason:
          blockingCount === 0
            ? 'All blocking holdout cases passed'
            : `${blockingCount} blocking holdout failure(s)`,
      })
    }
    if (regCount !== null) {
      fallback.push({
        name: 'zero_paired_regressions',
        label: 'Zero paired case regressions',
        passed: regCount === 0,
        actual: regCount,
        threshold: 0,
        reason:
          regCount === 0
            ? 'No holdout case pass rate regressed'
            : `${regCount} paired regression(s) detected`,
      })
    }
  }
  return fallback
}

export function buildModelsProvenance(
  job: OptimizationJobDetail,
): ProvenanceModel[] {
  const cfg = asRecord(job.config_json)
  const configured = asRecord(cfg?.models)
  const reported = asRecord(asRecord(job.report_json)?.models)
  const roles: Array<[string, string]> = [
    ['root_lm', 'Root LM'],
    ['sub_lm', 'Sub LM'],
    ['reflection_lm', 'Reflection LM'],
    ['judge_lm', 'Judge LM'],
    ['decision_lm', 'Decision LM'],
  ]
  const rows: ProvenanceModel[] = []
  if (reported || configured) {
    for (const [key, label] of roles) {
      const entry = asRecord(reported?.[key]) ?? asRecord(configured?.[key])
      if (!entry || typeof entry.model !== 'string' || !entry.model) continue
      rows.push({
        role: label,
        model: entry.model,
        providerName: typeof entry.provider_name === 'string' ? entry.provider_name : null,
        dialect: typeof entry.dialect === 'string' ? entry.dialect : null,
      })
    }
  }
  if (rows.length === 0) {
    // Fallback to config_json provenance or resolved_decision_model.
    for (const [key, label] of [
      ['root_lm', 'Root LM'],
      ['reflection_lm_config', 'Reflection LM'],
      ['decision_lm', 'Decision LM'],
    ] as const) {
      const entry = asRecord(cfg?.[key])
      const model = typeof entry?.model === 'string' ? entry.model : null
      const providerName = typeof entry?.provider_name === 'string' ? entry.provider_name : null
      if (model || providerName) {
        rows.push({
          role: label,
          model: model ?? 'Provider default',
          providerName,
          dialect: typeof entry?.dialect === 'string' ? entry.dialect : null,
        })
      }
    }
  }
  if (rows.length === 0 && job.resolved_decision_model) {
    rows.push({
      role: 'Decision LM',
      model: job.resolved_decision_model,
      providerName: null,
      dialect: null,
    })
  }
  return rows
}

import type { WorkspaceSettingsFields } from './api'

export type NumericFieldKey =
  | 'rlm_max_iters'
  | 'rlm_max_llm_calls'
  | 'rlm_max_depth'
  | 'rlm_max_output_chars'
  | 'history_max_turns'
  | 'history_max_chars'
  | 'daytona_exec_timeout_seconds'

export interface SettingsDraft {
  llm_model: string
  sub_llm_model: string
  decision_model: string
  rlm_max_iters: string
  rlm_max_llm_calls: string
  rlm_max_depth: string
  rlm_max_output_chars: string
  history_max_turns: string
  history_max_chars: string
  daytona_exec_timeout_seconds: string
}

export interface NumericFieldSpec {
  readonly key: NumericFieldKey
  readonly label: string
  readonly min: number
  readonly max: number
}

export const NUMERIC_FIELDS: readonly NumericFieldSpec[] = [
  { key: 'rlm_max_iters', label: 'Max RLM iterations', min: 1, max: 100 },
  { key: 'rlm_max_llm_calls', label: 'Max LLM calls', min: 1, max: 500 },
  { key: 'rlm_max_depth', label: 'Max recursion depth', min: 1, max: 7 },
  { key: 'rlm_max_output_chars', label: 'Max output chars', min: 500, max: 200_000 },
  { key: 'history_max_turns', label: 'History max turns', min: 0, max: 100 },
  { key: 'history_max_chars', label: 'History max chars', min: 1_000, max: 200_000 },
  { key: 'daytona_exec_timeout_seconds', label: 'Execution timeout (seconds)', min: 5, max: 600 },
]

const NON_EMPTY_STRING_KEYS = ['llm_model', 'sub_llm_model', 'decision_model'] as const

export function toDraft(fields: WorkspaceSettingsFields): SettingsDraft {
  return {
    llm_model: fields.llm_model,
    sub_llm_model: fields.sub_llm_model,
    decision_model: fields.decision_model,
    rlm_max_iters: String(fields.rlm_max_iters),
    rlm_max_llm_calls: String(fields.rlm_max_llm_calls),
    rlm_max_depth: String(fields.rlm_max_depth),
    rlm_max_output_chars: String(fields.rlm_max_output_chars),
    history_max_turns: String(fields.history_max_turns),
    history_max_chars: String(fields.history_max_chars),
    daytona_exec_timeout_seconds: String(fields.daytona_exec_timeout_seconds),
  }
}

export function hasDraftChanges(effective: WorkspaceSettingsFields, draft: SettingsDraft): boolean {
  const baseline = toDraft(effective)
  return (Object.keys(baseline) as (keyof SettingsDraft)[]).some((key) => baseline[key] !== draft[key])
}

export function buildSettingsPatch(
  effective: WorkspaceSettingsFields,
  draft: SettingsDraft,
): { patch: Partial<WorkspaceSettingsFields>; validationError?: string } {
  const patch: Partial<WorkspaceSettingsFields> = {}

  for (const key of NON_EMPTY_STRING_KEYS) {
    const value = draft[key].trim()
    if (!value) return { patch: {}, validationError: `${key} cannot be empty.` }
    if (value !== effective[key]) patch[key] = value
  }

  for (const { key, label, min, max } of NUMERIC_FIELDS) {
    const raw = draft[key].trim()
    const parsed = raw ? Number(raw) : Number.NaN
    if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
      return {
        patch: {},
        validationError: `${label} must be an integer between ${min} and ${max}.`,
      }
    }
    const normalized = parsed === 0 ? 0 : parsed
    if (normalized !== effective[key]) patch[key] = normalized
  }

  return { patch }
}

import { describe, expect, it } from 'vitest'
import type { WorkspaceSettingsFields } from './api'
import {
  buildSettingsPatch,
  hasDraftChanges,
  NUMERIC_FIELDS,
  toDraft,
} from './settings-draft'

const BASE_EFFECTIVE: WorkspaceSettingsFields = {
  llm_model: 'openai/gpt-5-mini',
  sub_llm_model: 'openai/gpt-5-nano',
  decision_model: 'typesafe:jev-latest',
  rlm_max_iters: 20,
  rlm_max_llm_calls: 50,
  rlm_max_depth: 4,
  rlm_max_output_chars: 10_000,
  history_max_turns: 8,
  history_max_chars: 16_000,
  daytona_exec_timeout_seconds: 120,
}

describe('settings-draft', () => {
  it('matches exact backend numeric min/max bounds', () => {
    const bounds = Object.fromEntries(
      NUMERIC_FIELDS.map(({ key, min, max }) => [key, { min, max }]),
    )
    expect(bounds).toEqual({
      rlm_max_iters: { min: 1, max: 100 },
      rlm_max_llm_calls: { min: 1, max: 500 },
      rlm_max_depth: { min: 1, max: 7 },
      rlm_max_output_chars: { min: 500, max: 200_000 },
      history_max_turns: { min: 0, max: 100 },
      history_max_chars: { min: 1_000, max: 200_000 },
      daytona_exec_timeout_seconds: { min: 5, max: 600 },
    })
  })

  it('converts effective settings into a string draft and detects changes', () => {
    const draft = toDraft(BASE_EFFECTIVE)
    expect(draft.rlm_max_depth).toBe('4')
    expect(hasDraftChanges(BASE_EFFECTIVE, draft)).toBe(false)
    expect(
      hasDraftChanges(
        BASE_EFFECTIVE,
        { ...draft, rlm_max_depth: '7' },
      ),
    ).toBe(true)
  })

  it('produces a diff-only patch and trims whitespace', () => {
    const draft = toDraft(BASE_EFFECTIVE)
    expect(buildSettingsPatch(BASE_EFFECTIVE, draft)).toEqual({ patch: {} })

    const updated = {
      ...draft,
      llm_model: '  anthropic/claude-sonnet-4  ',
      rlm_max_depth: '7',
      history_max_turns: '0',
    }
    expect(buildSettingsPatch(BASE_EFFECTIVE, updated)).toEqual({
      patch: {
        llm_model: 'anthropic/claude-sonnet-4',
        rlm_max_depth: 7,
        history_max_turns: 0,
      },
    })
  })

  it('accepts exact min and max boundaries for every numeric field', () => {
    const baseDraft = toDraft(BASE_EFFECTIVE)
    for (const { key, min, max } of NUMERIC_FIELDS) {
      const minResult = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, [key]: String(min) })
      expect(minResult.validationError).toBeUndefined()
      if (min !== BASE_EFFECTIVE[key]) {
        expect(minResult.patch).toEqual({ [key]: min })
      }

      const maxResult = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, [key]: String(max) })
      expect(maxResult.validationError).toBeUndefined()
      if (max !== BASE_EFFECTIVE[key]) {
        expect(maxResult.patch).toEqual({ [key]: max })
      }
    }
  })

  it('rejects out-of-range, blank, and non-integer numeric inputs', () => {
    const baseDraft = toDraft(BASE_EFFECTIVE)

    // rlm_max_depth max is 7 (leaves room for depth-8 llm_query)
    const depthTooHigh = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, rlm_max_depth: '8' })
    expect(depthTooHigh.validationError).toContain('between 1 and 7')
    expect(depthTooHigh.patch).toEqual({})

    // history_max_turns allows 0, rejects negative and blank inputs (Number('') === 0 trap)
    const turnsZero = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, history_max_turns: '0' })
    expect(turnsZero.validationError).toBeUndefined()
    expect(turnsZero.patch).toEqual({ history_max_turns: 0 })

    const turnsBlank = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, history_max_turns: '   ' })
    expect(turnsBlank.validationError).toContain('between 0 and 100')

    const turnsNeg = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, history_max_turns: '-1' })
    expect(turnsNeg.validationError).toContain('between 0 and 100')

    for (const { key, min, max } of NUMERIC_FIELDS) {
      const below = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, [key]: String(min - 1) })
      expect(below.validationError).toBeDefined()
      expect(below.patch).toEqual({})

      const above = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, [key]: String(max + 1) })
      expect(above.validationError).toBeDefined()
      expect(above.patch).toEqual({})

      const decimal = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, [key]: `${min}.5` })
      expect(decimal.validationError).toBeDefined()
    }
  })

  it('rejects empty required string fields', () => {
    const baseDraft = toDraft(BASE_EFFECTIVE)
    for (const key of ['llm_model', 'sub_llm_model', 'decision_model'] as const) {
      const result = buildSettingsPatch(BASE_EFFECTIVE, { ...baseDraft, [key]: '   ' })
      expect(result.validationError).toBe(`${key} cannot be empty.`)
      expect(result.patch).toEqual({})
    }
  })
})

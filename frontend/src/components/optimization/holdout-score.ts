// DSPy reports Root RLM holdout scores on a 0–100 scale and gate scores on 0–1.
export function holdoutPoints(value: number, target: string): string {
  return target === 'rlm' ? value.toFixed(2) : (value * 100).toFixed(1)
}

export function formatScore(value: number | null | undefined, target: string): string {
  return value == null || !Number.isFinite(value) ? '—' : `${holdoutPoints(value, target)}%`
}

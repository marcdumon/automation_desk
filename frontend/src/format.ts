// CLAUDE> one place for how money, tokens and durations are written, so every page reads the same

// CLAUDE> always six decimals, so amounts line up digit for digit in right-aligned columns
export const usd = (value: number) => `$${value.toFixed(6)}`

export const count = (value: number) => value.toLocaleString('en-US')

export const ms = (value: number) => (value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`)

export const when = (iso: string) =>
  new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' })

// CLAUDE> '—' for a job with nothing to apply (a digest, an answer, a research): 'no' read as something left undone
export const applied = (job: { applied_at: string; apply_status: string; appliable?: boolean }) =>
  job.apply_status === 'error' ? 'failed' : job.applied_at ? 'yes' : job.appliable === false ? '—' : 'no'

import type { JobSummary } from './api'
import { count, ms, usd } from './format'

// CLAUDE> the cost line shown under every preview and every applied change
export default function JobCost({ job, onOpen }: { job: JobSummary; onOpen: (id: string) => void }) {
  return (
    <div className="job-cost">
      <dl>
        <div><dt>Cost</dt><dd>{usd(job.cost_usd)}</dd></div>
        <div><dt>Model</dt><dd>{job.models.join(', ') || 'none'}</dd></div>
        <div><dt>Model calls</dt><dd>{job.llm_calls}</dd></div>
        <div><dt>Tokens in / out</dt><dd>{count(job.prompt_tokens)} / {count(job.completion_tokens)}</dd></div>
        <div><dt>Google calls</dt><dd>{job.google_calls}</dd></div>
        {job.fetches > 0 && <div><dt>Pages read</dt><dd>{job.fetches}</dd></div>}
        <div><dt>Time</dt><dd>{ms(job.duration_ms)}</dd></div>
      </dl>
      <button type="button" className="link" onClick={() => onOpen(job.id)}>Everything this job did</button>
    </div>
  )
}

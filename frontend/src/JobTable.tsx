import type { JobSummary } from './api'
import { applied, count, ms, usd, when } from './format'

// CLAUDE> a list of jobs; the group pages pass only their own group's jobs
export default function JobTable({ jobs, showGroup, onOpen }: { jobs: JobSummary[]; showGroup: boolean; onOpen: (id: string) => void }) {
  if (jobs.length === 0) return <p className="muted">No jobs yet.</p>
  return (
    <div className="table-wrap">
      <table className="jobs">
        <thead>
          <tr>
            <th>When</th>
            {showGroup && <th>Group</th>}
            <th>Applied</th>
            <th>Sentence</th>
            <th>Outcome</th>
            <th>Model</th>
            <th className="num">Tokens in</th>
            <th className="num">Tokens out</th>
            <th className="num">Cost</th>
            <th className="num">Time</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map(j => (
            <tr key={j.id} className="clickable" onClick={() => onOpen(j.id)} tabIndex={0}
                onKeyDown={e => { if (e.key === 'Enter') onOpen(j.id) }}>
              <td className="nowrap">{when(j.started)}</td>
              {showGroup && <td>{j.group}</td>}
              <td className={j.apply_status === 'error' ? 'bad' : ''}>{applied(j)}</td>
              <td className="sentence">{j.sentence}</td>
              <td className={j.status === 'error' ? 'bad' : ''}>{j.status}</td>
              <td className="model">{j.models.length ? j.models.map(m => <span key={m} className="model-name"><span>{m.split('/')[0]}/</span><span>{m.split('/').slice(1).join('/')}</span></span>) : '—'}</td>
              <td className="num">{count(j.prompt_tokens)}</td>
              <td className="num">{count(j.completion_tokens)}</td>
              <td className="num">{usd(j.cost_usd)}</td>
              <td className="num">{ms(j.duration_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

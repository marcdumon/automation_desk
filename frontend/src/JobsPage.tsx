import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getJobs, type Usage } from './api'
import { count, usd } from './format'
import JobDetailPanel from './JobDetailPanel'
import JobTable from './JobTable'

// CLAUDE> the one page that spans all groups: what every job did and what it cost
export default function JobsPage() {
  const [group, setGroup] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const all = useQuery({ queryKey: ['jobs', 'all'], queryFn: () => getJobs() })
  if (all.isPending) return <p className="muted">Loading…</p>
  if (all.isError) return <p className="bad">{all.error.message}</p>
  const data = all.data
  const shown = group ? data.jobs.filter(j => j.group === group) : data.jobs
  const groups = [...new Set(data.jobs.map(j => j.group))]

  return (
    <section className="page">
      <header className="page-head"><h1>Jobs &amp; costs</h1></header>
      <p className="muted">Configured model: <strong>{data.configured_model}</strong>. Costs are what OpenRouter reported for each call.</p>

      <div className="totals">
        <div className="total-main"><span className="total-label">Spent in total</span><span className="total-value">{usd(data.total_cost_usd)}</span></div>
        <UsageTable title="Per group" rows={data.by_group} />
        <UsageTable title="Per model" rows={data.by_model} />
      </div>

      <div className="jobs-head">
        <h2>All jobs ({shown.length})</h2>
        <label>Group{' '}
          <select value={group} onChange={e => setGroup(e.target.value)}>
            <option value="">All</option>
            {groups.map(g => <option key={g} value={g}>{g}</option>)}
          </select>
        </label>
      </div>
      <JobTable jobs={shown} showGroup onOpen={setOpen} />
      {open && <JobDetailPanel id={open} onClose={() => setOpen(null)} />}
    </section>
  )
}

function UsageTable({ title, rows }: { title: string; rows: Record<string, Usage> }) {
  const entries = Object.entries(rows)
  return (
    <div className="usage">
      <h2>{title}</h2>
      {entries.length === 0 ? <p className="muted">Nothing yet.</p> : (
        <table>
          <thead><tr><th></th><th className="num">Calls</th><th className="num">Tokens in</th><th className="num">Tokens out</th><th className="num">Cost</th></tr></thead>
          <tbody>
            {entries.map(([name, u]) => (
              <tr key={name}>
                <td>{name}</td><td className="num">{u.calls}</td>
                <td className="num">{count(u.prompt_tokens)}</td><td className="num">{count(u.completion_tokens)}</td><td className="num">{usd(u.cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

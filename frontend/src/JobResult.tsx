import type { JobDetail } from './api'

// CLAUDE> what a job's preview showed and what its apply did, kept on the job so it can be looked at later
export default function JobResult({ job }: { job: JobDetail }) {
  const preview = job.preview
  const applied = new Set(job.applied_rows ?? [])
  const wasApplied = Boolean(job.summary.applied_at)
  if (!preview?.summary && job.results.length === 0) {
    return <p className="muted">This job was run before results were kept with it.</p>
  }
  const columns = preview.columns ?? []
  return (
    <div className="job-result">
      {preview.summary && <p className="result-summary">{preview.summary}</p>}
      {preview.answer && (
        <div className="answer">
          <p className="answer-text">{preview.answer}</p>
          {(preview.evidence ?? []).length > 0 && (
            <ul className="evidence">
              {preview.evidence!.map((e, i) => (
                <li key={i} className={e.verified ? '' : 'unverified'}>
                  <blockquote>{e.quote}</blockquote>
                  <span className="evidence-source">
                    {e.verified ? 'Found word for word in ' : 'NOT found word for word in '}
                    {e.link ? <a href={e.link} target="_blank" rel="noreferrer">{e.source}</a> : e.source}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {job.results.length > 0 && (
        <>
          <h4>What the apply did</h4>
          <ul className="results">{job.results.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </>
      )}
      {(preview.rows ?? []).length > 0 && (
        <>
          <h4>{preview.read_only ? 'Found' : wasApplied ? 'Previewed, with what was applied' : 'Previewed (not applied)'}</h4>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  {!preview.read_only && wasApplied && <th>Applied</th>}
                  {columns.map(c => <th key={c}>{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {preview.rows!.map(row => (
                  <tr key={row.id}>
                    {!preview.read_only && wasApplied && <td>{applied.has(row.id) ? 'yes' : '—'}</td>}
                    {columns.map((c, i) => (
                      <td key={c}>
                        {row.links?.[c]
                          ? <a href={row.links[c]} target="_blank" rel="noreferrer">{row.cells[c]}</a>
                          : /^https?:\/\//.test(row.cells[c] ?? '')
                            ? <a href={row.cells[c]} target="_blank" rel="noreferrer">{row.cells[c].replace(/^https?:\/\/(www\.)?/, '')}</a>
                            : row.cells[c]}
                        {i === 0 && row.note && <span className="note">{row.note}</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {(preview.notes ?? []).length > 0 && <ul className="sheet-notes">{preview.notes!.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </div>
  )
}

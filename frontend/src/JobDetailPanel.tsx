import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { getJob, type LLMCall } from './api'
import JobResult from './JobResult'
import { applied, count, ms, usd, when } from './format'

// CLAUDE> full record of one job: every model call with its prompt and reply, every Google call, every page read
export default function JobDetailPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const job = useQuery({ queryKey: ['job', id], queryFn: () => getJob(id) })
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])

  return (
    <dialog ref={dialog} className="job-detail" onClose={onClose} aria-labelledby="job-title">
      <div className="job-detail-head">
        <h2 id="job-title">Job {id}</h2>
        <button type="button" className="quiet" onClick={() => dialog.current?.close()}>Close</button>
      </div>
      {job.isPending && <p className="muted">Loading…</p>}
      {job.isError && <p className="bad">{job.error.message}</p>}
      {job.data && (() => {
        const { summary: s, llm_calls, google_calls, fetches } = job.data
        return (
          <>
            <dl className="facts">
              <div><dt>Started</dt><dd>{when(s.started)}</dd></div>
              <div><dt>Group</dt><dd>{s.group}</dd></div>
              <div><dt>Standard task</dt><dd>{s.task_name || '—'}</dd></div>
              <div><dt>Sentence</dt><dd>{s.sentence}</dd></div>
              <div><dt>Preview</dt><dd>{s.status}{s.message ? `: ${s.message}` : ''} ({ms(s.preview_ms)})</dd></div>
              <div><dt>Applied</dt><dd>
                {applied(s) === 'yes' ? `yes, at ${when(s.applied_at)} (${ms(s.apply_ms)})`
                  : applied(s) === 'failed' ? `failed at ${when(s.applied_at)}: ${s.apply_message}` : 'no'}
              </dd></div>
              <div><dt>Total cost</dt><dd>{usd(s.cost_usd)}</dd></div>
              <div><dt>Total time</dt><dd>{ms(s.duration_ms)}</dd></div>
            </dl>

            <h3>Result</h3>
            <JobResult job={job.data} />

            <h3>Model calls ({llm_calls.length})</h3>
            {llm_calls.length === 0 && <p className="muted">No model was used.</p>}
            {llm_calls.map((c, i) => (
              <section key={i} className="call">
                <p className="call-title"><span className={`step step-${c.stage}`}>{c.stage}</span>{c.purpose}{c.error && <span className="bad"> failed: {c.error}</span>}</p>
                <dl className="facts">
                  <div><dt>Model asked for</dt><dd>{c.model_requested}</dd></div>
                  <div><dt>Model that answered</dt><dd>{c.model_used || '—'}</dd></div>
                  <div><dt>Provider</dt><dd>{c.provider || '—'}</dd></div>
                  <div><dt>Tokens in / out</dt><dd>{count(c.prompt_tokens)} / {count(c.completion_tokens)}</dd></div>
                  <div><dt>Cost (OpenRouter)</dt><dd>{usd(c.cost_usd)}</dd></div>
                  <div><dt>Time</dt><dd>{ms(c.latency_ms)}</dd></div>
                  <div><dt>Finish reason</dt><dd>{c.finish_reason || '—'}</dd></div>
                  <div><dt>Generation id</dt><dd>{c.generation_id || '—'}</dd></div>
                </dl>
                <ExactCall call={c} />
              </section>
            ))}

            <h3>Google API calls ({google_calls.length})</h3>
            {google_calls.length === 0 && <p className="muted">No Google call was made.</p>}
            {google_calls.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Step</th><th>Method</th><th>Parameters</th><th>Time</th><th>Result</th></tr></thead>
                  <tbody>
                    {google_calls.map((c, i) => (
                      <tr key={i}>
                        <td><span className={`step step-${c.stage}`}>{c.stage}</span></td>
                        <td><code>{c.method}</code></td>
                        <td><pre className="params">{JSON.stringify(c.params, null, 1)}</pre></td>
                        <td className="nowrap">{ms(c.latency_ms)}</td>
                        <td className={c.error ? 'bad' : ''}>{c.error || 'ok'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {fetches.length > 0 && (
              <>
                <h3>Pages read ({fetches.length})</h3>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Step</th><th>Address</th><th>How</th><th>Status</th><th>Size</th><th>Time</th></tr></thead>
                    <tbody>
                      {fetches.map((f, i) => (
                        <tr key={i}><td><span className={`step step-${f.stage}`}>{f.stage}</span></td><td>{f.url}</td><td>{f.via}</td><td>{f.status || '—'}</td><td className="nowrap">{count(f.bytes)} bytes</td><td className="nowrap">{ms(f.latency_ms)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )
      })()}
    </dialog>
  )
}

const ROLE_LABELS: Record<string, string> = {
  system: 'Instructions (system message)',
  user: 'Text sent (user message)',
  assistant: "Model's earlier reply (sent back on retry)",
}

// CLAUDE> everything that went to OpenRouter for one call, as sent: messages, answer schema, settings, raw JSON
function ExactCall({ call }: { call: LLMCall }) {
  const request = call.request
  if (!request?.messages && !call.system && !call.user) {
    return <p className="muted">The prompt of this call was not recorded; only its model, tokens and cost are known.</p>
  }
  if (!request?.messages) {
    return (
      <>
        <p className="muted">Recorded before exact requests were kept: instructions and text only.</p>
        <h4>Instructions</h4><pre>{call.system}</pre>
        <h4>Text sent</h4><pre>{call.user}</pre>
        <h4>Model's reply</h4><pre>{call.reply}</pre>
      </>
    )
  }
  const { messages, response_format, ...settings } = request
  const schema = response_format?.json_schema?.schema
  return (
    <>
      {messages.map((m, i) => (
        <div key={i}>
          <h4>{i === messages.length - 1 && m.role === 'user' && i > 1 ? "Correction sent after an invalid reply (user message)" : ROLE_LABELS[m.role] ?? m.role}</h4>
          <pre>{m.content}</pre>
        </div>
      ))}
      {schema !== undefined && (
        <>
          <h4>Answer format the model had to follow ({response_format?.json_schema?.name}, strict JSON schema)</h4>
          <pre>{JSON.stringify(schema, null, 2)}</pre>
        </>
      )}
      <h4>Settings sent</h4>
      <pre>{JSON.stringify(settings, null, 2)}</pre>
      <h4>Model's reply</h4>
      <pre>{call.reply || '(empty)'}</pre>
      <details>
        <summary>Exact request body sent to OpenRouter (JSON)</summary>
        <CopyButton text={JSON.stringify(request, null, 2)} />
        <pre>{JSON.stringify(request, null, 2)}</pre>
      </details>
      <details>
        <summary>Exact response from OpenRouter (JSON)</summary>
        <CopyButton text={JSON.stringify(call.response, null, 2)} />
        <pre>{JSON.stringify(call.response, null, 2)}</pre>
      </details>
    </>
  )
}

function CopyButton({ text }: { text: string }) {
  return (
    <button type="button" className="quiet copy" onClick={() => navigator.clipboard.writeText(text)}>Copy</button>
  )
}

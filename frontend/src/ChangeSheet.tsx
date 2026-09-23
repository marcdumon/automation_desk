import { useState, type ReactNode } from 'react'

import type { Preview } from './api'

type Props = {
  taskName: string
  preview: Preview
  selected: Set<string>
  onToggle: (id: string) => void
  onToggleAll: (all: boolean) => void
  onConfirm: () => void
  onCancel: () => void
  busy: boolean
  cost: ReactNode
  onAdjust: (options: Record<string, string>) => void
  adjusting: boolean
}

// CLAUDE> the frozen plan as a table of changes; nothing reaches Google until "Apply" is pressed
export default function ChangeSheet({ taskName, preview, selected, onToggle, onToggleAll, onConfirm, onCancel, busy, cost, onAdjust, adjusting }: Props) {
  const selectable = preview.rows.filter(r => r.selectable)
  const allOn = selectable.length > 0 && selectable.every(r => selected.has(r.id))
  return (
    <div className="sheet" aria-label="Changes to apply">
      <div className="sheet-head">
        <div>
          <p className="sheet-task">{taskName}</p>
          <h2>{preview.summary}</h2>
        </div>
      </div>
      {preview.answer && (
        <div className="answer">
          <p className="answer-text">{preview.answer}</p>
          {preview.evidence.length > 0 && (
            <ul className="evidence">
              {preview.evidence.map((e, i) => (
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
      {preview.options.length > 0 && <Options preview={preview} onAdjust={onAdjust} adjusting={adjusting} />}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {!preview.read_only && (
                <th className="tick">
                  <input type="checkbox" aria-label="Select all" checked={allOn} onChange={e => onToggleAll(e.target.checked)} />
                </th>
              )}
              {preview.columns.map(c => <th key={c}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map(row => {
              const on = selected.has(row.id)
              return (
                <tr key={row.id} className={preview.read_only || on ? 'on' : 'off'}>
                  {!preview.read_only && (
                    <td className="tick">
                      <input type="checkbox" checked={on} disabled={!row.selectable} onChange={() => onToggle(row.id)}
                             aria-label={`Include ${Object.values(row.cells)[0] ?? row.id}`} />
                    </td>
                  )}
                  {preview.columns.map((c, i) => (
                    <td key={c}>
                      <span className="value">
                        {row.links?.[c]
                          ? <a href={row.links[c]} target="_blank" rel="noreferrer">{row.cells[c]}</a>
                          : /^https?:\/\//.test(row.cells[c] ?? '')
                          ? <a href={row.cells[c]} target="_blank" rel="noreferrer">{row.cells[c].replace(/^https?:\/\/(www\.)?/, '')}</a>
                          : row.cells[c]}
                      </span>
                      {i === 0 && row.note && <span className="note">{row.note}</span>}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {cost}
      {preview.notes.length > 0 && <ul className="sheet-notes">{preview.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      <div className="sheet-actions">
        {preview.read_only ? (
          <button type="button" className="primary" onClick={onCancel}>Close</button>
        ) : (
          <>
            <button type="button" className="quiet" onClick={onCancel} disabled={busy}>Cancel</button>
            <button type="button" className="primary" onClick={onConfirm} disabled={busy || selected.size === 0}>
              {busy ? 'Applying…' : `Apply ${selected.size} change${selected.size === 1 ? '' : 's'}`}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

// CLAUDE> settings the task offers for this preview; changing them recomposes it without reading the site again
function Options({ preview, onAdjust, adjusting }: { preview: Preview; onAdjust: Props['onAdjust']; adjusting: boolean }) {
  const initial = Object.fromEntries(preview.options.map(o => [o.name, o.value]))
  const [values, setValues] = useState(initial)
  const changed = preview.options.some(o => values[o.name] !== o.value)
  return (
    <form className="options" onSubmit={e => { e.preventDefault(); if (changed) onAdjust(values) }}>
      {preview.options.map(o => (
        <label key={o.name} className={o.multiline ? 'wide' : ''}>
          <span className="option-label">{o.label}</span>
          {o.multiline
            ? <textarea rows={8} value={values[o.name] ?? ''} onChange={e => setValues({ ...values, [o.name]: e.target.value })} />
            : <input value={values[o.name] ?? ''} onChange={e => setValues({ ...values, [o.name]: e.target.value })} />}
          {o.help && <span className="option-help">{o.help}</span>}
        </label>
      ))}
      <button type="submit" className="quiet" disabled={!changed || adjusting}>{adjusting ? 'Updating…' : 'Update preview'}</button>
    </form>
  )
}

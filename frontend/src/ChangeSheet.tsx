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
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(preview.options.map(o => [o.name, o.value])))
  const [cellValues, setCellValues] = useState<Record<string, string>>({})
  const changedCells = Object.entries(cellValues).filter(([key, value]) => {
    const [column, ...rest] = key.split(':')
    return preview.rows.find(r => r.id === rest.join(':'))?.inputs?.[column] !== value
  })
  const changed = preview.options.some(o => values[o.name] !== o.value) || changedCells.length > 0
  const editable = preview.options.length > 0 || preview.rows.some(r => Object.keys(r.inputs ?? {}).length > 0)
  const update = () => onAdjust({ ...values, ...Object.fromEntries(changedCells) })
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
      {editable && <Options preview={preview} values={values} setValues={setValues} changed={changed} onUpdate={update}
                            adjusting={adjusting} />}
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
                        {row.inputs?.[c] !== undefined
                          ? (row.inputs[c].length > 20 || row.inputs[c].includes('\n')
                            ? <textarea className={`cell-input col-${c.toLowerCase()}`} rows={1}
                                        aria-label={`${c} of ${Object.values(row.cells)[0] ?? row.id}`}
                                        value={cellValues[`${c}:${row.id}`] ?? row.inputs[c]}
                                        onChange={e => setCellValues({ ...cellValues, [`${c}:${row.id}`]: e.target.value })} />
                            : <input className={`cell-input col-${c.toLowerCase()}`}
                                     aria-label={`${c} of ${Object.values(row.cells)[0] ?? row.id}`}
                                     value={cellValues[`${c}:${row.id}`] ?? row.inputs[c]}
                                     onChange={e => setCellValues({ ...cellValues, [`${c}:${row.id}`]: e.target.value })}
                                     onKeyDown={e => { if (e.key === 'Enter' && changed) { e.preventDefault(); update() } }} />)
                          : row.links?.[c]
                          ? <a href={row.links[c]} target="_blank" rel="noreferrer">{row.cells[c]}</a>
                          : /^https?:\/\//.test(row.cells[c] ?? '')
                          ? <a href={row.cells[c]} target="_blank" rel="noreferrer">{row.cells[c].replace(/^https?:\/\/(www\.)?/, '')}</a>
                          : row.cells[c]}
                      </span>
                      {row.inputs?.[c] !== undefined && row.cells[c]?.endsWith('(assumed)') && <span className="note">assumed</span>}
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

// CLAUDE> settings the task offers for this preview, plus the per-row inputs; changing them recomposes the preview
// without reading the source again
function Options({ preview, values, setValues, changed, onUpdate, adjusting }: {
  preview: Preview; values: Record<string, string>; setValues: (v: Record<string, string>) => void; changed: boolean
  onUpdate: () => void; adjusting: boolean
}) {
  return (
    <form className="options" onSubmit={e => { e.preventDefault(); if (changed) onUpdate() }}>
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

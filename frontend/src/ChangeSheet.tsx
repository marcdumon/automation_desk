import { useState, type ReactNode } from 'react'

import type { Preview, PreviewGroup, Row } from './api'

type Props = {
  taskName: string
  preview: Preview
  selected: Set<string>
  onToggle: (id: string) => void
  onToggleAll: (all: boolean) => void
  onSetMany: (ids: string[], on: boolean) => void
  onAddSection: (name: string) => void
  addingSection: string | null
  // CLAUDE> why adding one site's events failed, shown at that site: the page-wide message sat 100 rows away from the button
  sectionError: { name: string; message: string } | null
  onConfirm: () => void
  onCancel: () => void
  busy: boolean
  cost: ReactNode
  onAdjust: (options: Record<string, string>) => void
  adjusting: boolean
}

// CLAUDE> the frozen plan as a table of changes; nothing reaches Google until "Apply" is pressed
export default function ChangeSheet({ taskName, preview, selected, onToggle, onToggleAll, onSetMany, onAddSection, addingSection,
  sectionError, onConfirm, onCancel, busy, cost, onAdjust, adjusting }: Props) {
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
  const declineAll = selected.size === 0 && selectable.length > 0 && (preview.groups?.length ?? 0) > 0
  const renderRow = (row: Row) => {
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
                ? <SourceLink url={row.cells[c]} />
                : row.cells[c]}
            </span>
            {row.inputs?.[c] !== undefined && row.cells[c]?.endsWith('(assumed)') && <span className="note">assumed</span>}
            {i === 0 && row.note && <span className="note">{row.note}</span>}
          </td>
        ))}
      </tr>
    )
  }
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
          {preview.groups && preview.groups.length > 0
            ? preview.groups.map(g => (
              <tbody key={g.name} className="group">
                <GroupHead group={g} rows={preview.rows.filter(r => r.group === g.name)} span={preview.columns.length}
                           tick={!preview.read_only} selected={selected} onSetMany={onSetMany}
                           onAdd={() => onAddSection(g.name)} adding={addingSection === g.name} busy={busy || addingSection !== null}
                           error={sectionError?.name === g.name ? sectionError.message : ''} />
                {preview.rows.filter(r => r.group === g.name).map(renderRow)}
              </tbody>
            ))
            : <tbody>{preview.rows.map(renderRow)}</tbody>}
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
            {/* CLAUDE> in a sectioned preview (watched sites) applying nothing declines the rest: they come back unticked */}
            <button type="button" className={declineAll ? 'quiet' : 'primary'} onClick={onConfirm}
                    disabled={busy || (selected.size === 0 && !declineAll)}>
              {busy ? 'Applying…' : declineAll ? 'Decline all' : `Apply ${selected.size} change${selected.size === 1 ? '' : 's'}`}
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
          {o.choices && o.choices.length > 0
            ? (
              <span className="choices" role="radiogroup" aria-label={o.label}>
                {o.choices.map(c => (
                  <button key={c} type="button" role="radio" aria-checked={values[o.name] === c}
                          className={values[o.name] === c ? 'choice chosen' : 'choice'}
                          onClick={() => setValues({ ...values, [o.name]: c })}>{c}</button>))}
              </span>
            )
            : o.multiline
              ? <textarea rows={8} value={values[o.name] ?? ''} onChange={e => setValues({ ...values, [o.name]: e.target.value })} />
              : <input value={values[o.name] ?? ''} onChange={e => setValues({ ...values, [o.name]: e.target.value })} />}
          {o.help && <span className="option-help">{o.help}</span>}
        </label>
      ))}
      <button type="submit" className="quiet" disabled={!changed || adjusting}>{adjusting ? 'Updating…' : 'Update preview'}</button>
    </form>
  )
}

// CLAUDE> the head of one source's section: its name, the calendar its events go to, what it gave, and a tick for all its rows
function GroupHead({ group, rows, span, tick, selected, onSetMany, onAdd, adding, busy, error }: {
  group: PreviewGroup; rows: Row[]; span: number; tick: boolean; selected: Set<string>; onSetMany: (ids: string[], on: boolean) => void
  onAdd: () => void; adding: boolean; busy: boolean; error: string
}) {
  const ids = rows.filter(r => r.selectable).map(r => r.id)
  const allOn = ids.length > 0 && ids.every(id => selected.has(id))
  const ticked = ids.filter(id => selected.has(id)).length
  const tone = /^\d+ new/.test(group.note) ? 'new' : /^added/.test(group.note) ? 'done'
    : /^(needs|could not)/.test(group.note) ? 'attention' : 'quiet'
  return (
    <tr className="group-head">
      {tick && (
        <td className="tick">
          {ids.length > 0 && <input type="checkbox" checked={allOn} aria-label={`Include every event of ${group.name}`}
                                    onChange={e => onSetMany(ids, e.target.checked)} />}
        </td>
      )}
      <td colSpan={span}>
        <div className="group-title">
          <h3>{group.name}</h3>
          {group.calendar && <span className="group-calendar">→ {group.calendar}</span>}
          <span className={`group-note ${tone}`}>{tone === 'done' && '✓ '}{group.note}</span>
          {tick && ids.length > 0 && (
            // CLAUDE> with nothing ticked the button declines the site's events: they come back unticked, never as new
            <button type="button" className={ticked ? 'primary group-add' : 'quiet group-add'} disabled={busy} onClick={onAdd}>
              {adding ? (ticked ? 'Adding…' : 'Declining…')
                : ticked ? `Add ${ticked} ${ticked === 1 ? 'event' : 'events'}` : `Decline ${ids.length} ${ids.length === 1 ? 'event' : 'events'}`}
            </button>
          )}
        </div>
        {error && <p className="cap-error group-error" role="alert">{error}</p>}
      </td>
    </tr>
  )
}

// CLAUDE> a page address as a clear button: the site and the start of the path, opening the page in a new tab
function SourceLink({ url }: { url: string }) {
  const parsed = new URL(url)
  const path = parsed.pathname.replace(/\/$/, '')
  const shown = parsed.hostname.replace(/^www\./, '') + (path.length > 28 ? `${path.slice(0, 27)}…` : path)
  return (
    <a className="source-link" href={url} target="_blank" rel="noreferrer" title={url}>
      <span>{shown}</span><span aria-hidden="true">↗</span>
    </a>
  )
}

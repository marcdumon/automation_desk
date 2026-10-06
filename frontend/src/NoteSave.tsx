import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { findNotes, noteHeadings, type NoteHeading, type SummaryTarget } from './api'

// CLAUDE> saving to Obsidian, shared by the Research page and the Chat page: a new note under a title, or a note the user
// has, with the place in it, the heading and its size

export type NoteSettings = { vault: string; subdir: string }

export const folderOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
export const nameOf = (path: string) => path.slice(path.lastIndexOf('/') + 1).replace(/\.md$/, '')
export const vaultName = (vault: string) => vault.split('/').filter(Boolean).pop() ?? ''

// CLAUDE> where a save goes: a new note under a title, or a note the user has (a place in it, the summary's heading and its
// size). The new summary form and the Obsidian card of a research's page use this one picker.
export type Spot = {
  where: 'new' | 'note'; note: { path: string; name: string } | null; place: 'top' | 'end' | 'after'; heading: NoteHeading | null
  level: 1 | 2; title: string
}
export const NEW_NOTE: Spot = { where: 'new', note: null, place: 'end', heading: null, level: 2, title: '' }
export const spotTarget = (spot: Spot): SummaryTarget => (spot.where === 'note' && spot.note
  ? { note: spot.note.path, place: spot.place, heading: spot.place === 'after' ? spot.heading : null, level: spot.level, title: spot.title.trim() }
  : { note: '', place: 'end', heading: null, level: 2, title: spot.title.trim() })
export const spotReady = (spot: Spot) => spot.where === 'new' || (spot.note !== null && (spot.place !== 'after' || spot.heading !== null))
export const spotOf = (target: Partial<SummaryTarget> | null | undefined, title = ''): Spot => (target?.note
  ? { where: 'note', note: { path: target.note, name: nameOf(target.note) }, place: target.place ?? (target.heading ? 'after' : 'end'),
      heading: target.heading ?? null, level: target.level ?? 2, title: target.title || title }
  : { ...NEW_NOTE, title: target?.title || title })
export const placeText = (spot: Spot) => (spot.place === 'after' && spot.heading ? `after “${spot.heading.text}”` : spot.place === 'top' ? 'at the top' : 'at the end')

export function SpotPicker({ settings, value, onChange, onlyNew = false, titleHint = '', skip }: {
  settings: NoteSettings; value: Spot; onChange: (spot: Spot) => void; onlyNew?: boolean; titleHint?: string; skip?: number | string
}) {
  const note = value.note
  const headings = useQuery({ queryKey: ['note-headings', note?.path, skip], queryFn: () => noteHeadings(note!.path, skip), enabled: note !== null })
  const list = headings.data?.headings ?? []
  const index = value.heading ? list.findIndex(h => h.level === value.heading!.level && h.text === value.heading!.text) : -1
  const selected = value.place === 'after' ? (index >= 0 ? String(index) : '') : value.place
  const pick = (choice: string) => onChange(choice === 'top' || choice === 'end'
    ? { ...value, place: choice, heading: null } : { ...value, place: 'after', heading: list[Number(choice)] ?? null })
  if (!settings.vault) return <p className="muted">Choose your Obsidian vault under Settings on the Research page first.</p>
  return (
    <div className="spot-picker">
      {!onlyNew && (
        <div className="research-switch" role="group" aria-label="Save to">
          <button type="button" aria-pressed={value.where === 'new'} onClick={() => onChange({ ...value, where: 'new' })}>A new note</button>
          <button type="button" aria-pressed={value.where === 'note'} onClick={() => onChange({ ...value, where: 'note' })}>A note I already have</button>
        </div>
      )}
      {value.where === 'new' ? (
        <label className="research-field">
          <span>Title of the new note</span>
          <input type="text" value={value.title} placeholder={titleHint} onChange={e => onChange({ ...value, title: e.target.value })} />
          <small className="muted">It goes to the folder {vaultName(settings.vault)}/{settings.subdir}.</small>
        </label>
      ) : (
        <>
          <div className="research-field">
            <span>Note</span>
            <NotePicker value={note} onChange={chosen => onChange({ ...value, note: chosen, place: 'end', heading: null })} />
          </div>
          {note && (
            <>
              <label className="research-field">
                <span>Where in the note</span>
                <select value={selected} onChange={e => pick(e.target.value)}>
                  {value.place === 'after' && index < 0 && <option value="">After “{value.heading?.text}” (no longer in the note)</option>}
                  <option value="top">At the top, under the note's title</option>
                  <option value="end">At the end</option>
                  {list.map((h, i) => (
                    <option key={`${i}-${h.text}`} value={String(i)}>{'  '.repeat(h.level - 1)}After “{h.text}”</option>
                  ))}
                </select>
              </label>
              <label className="research-field">
                <span>Heading of the summary in the note</span>
                <input type="text" value={value.title} placeholder={titleHint} onChange={e => onChange({ ...value, title: e.target.value })} />
              </label>
              <div className="research-field">
                <span>Heading size</span>
                <div className="country-chips" role="group" aria-label="Heading size">
                  <button type="button" className={`choice-chip${value.level === 2 ? ' chosen' : ''}`} aria-pressed={value.level === 2}
                          onClick={() => onChange({ ...value, level: 2 })}>## A section of the note</button>
                  <button type="button" className={`choice-chip${value.level === 1 ? ' chosen' : ''}`} aria-pressed={value.level === 1}
                          onClick={() => onChange({ ...value, level: 1 })}># A chapter, next to the note's title</button>
                </div>
              </div>
              {headings.isError && <p className="cap-error">{headings.error.message}</p>}
            </>
          )}
        </>
      )}
    </div>
  )
}

// CLAUDE> the notes of the vault set under Settings, newest first; type words of the name or the folder to find one
function NotePicker({ value, onChange }: { value: { path: string; name: string } | null; onChange: (note: { path: string; name: string } | null) => void }) {
  const [query, setQuery] = useState('')
  const found = useQuery({ queryKey: ['notes', query], queryFn: () => findNotes(query), placeholderData: keepPreviousData, enabled: value === null })
  if (value) {
    return (
      <div className="note-chosen">
        <span><strong>{value.name}</strong>{folderOf(value.path) && <span className="muted"> · {folderOf(value.path)}</span>}</span>
        <button type="button" className="quiet" onClick={() => onChange(null)}>Choose another note</button>
      </div>
    )
  }
  const notes = found.data?.notes ?? []
  return (
    <div className="note-picker">
      <input type="search" value={query} onChange={e => setQuery(e.target.value)} aria-label="Find a note"
             placeholder="Find a note by its name or folder" />
      {notes.length > 0 && (
        <ul className="note-results">
          {notes.map(n => (
            <li key={n.path}>
              <button type="button" onClick={() => onChange(n)}>
                <span className="note-name">{n.name}</span>
                {folderOf(n.path) && <span className="muted">{folderOf(n.path)}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {found.data && notes.length === 0 && <p className="muted">No note has these words in its name or folder.</p>}
      {found.isError && <p className="cap-error">{found.error.message}</p>}
    </div>
  )
}

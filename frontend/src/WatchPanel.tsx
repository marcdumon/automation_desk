import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import {
  checkWatch, getWatch, saveWatch, watchFromEvents, type Interpretation, type SiteProgress, type WatchedSite, type WatchState,
} from './api'
import { servePageRequests } from './capture'
import { extensionIsCurrent } from './openTab'
import StopButton from './StopButton'

// CLAUDE> the sites the user unticked stay unticked in this browser; a site added later starts ticked
const UNTICKED_KEY = 'watch-unticked'
const storedUnticked = (): number[] => {
  try { return JSON.parse(localStorage.getItem(UNTICKED_KEY) ?? '[]') } catch { return [] }
}
// CLAUDE> the site list folds away (closed unless the user opened it in this browser): open, it took the whole screen
const OPEN_KEY = 'watch-sites-open'
const storedOpen = (): boolean => {
  try { return localStorage.getItem(OPEN_KEY) === 'yes' } catch { return false }
}
const shortName = (label: string) => label.split('/')[0]

// CLAUDE> the agenda sites the user watches: checked only when they press the button, all of them or the ones ticked; the
// new events open in the preview
export default function WatchPanel({ onPreview }: { onPreview: (data: Interpretation) => void }) {
  const client = useQueryClient()
  const [unticked, setUnticked] = useState<number[]>(storedUnticked)
  const keepUnticked = (next: number[]) => {
    setUnticked(next)
    try { localStorage.setItem(UNTICKED_KEY, JSON.stringify(next)) } catch { /* CLAUDE> no storage: the choice lasts this visit */ }
  }
  const check = useMutation({
    mutationFn: ({ viaBrowser, only }: { viaBrowser: boolean; only: number[] | null }) => checkWatch(viaBrowser, only),
    onSuccess: data => {
      onPreview(data)
      client.invalidateQueries({ queryKey: ['watch'] })
      client.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
  // CLAUDE> while a check runs, ask every second what it is doing
  const state = useQuery({ queryKey: ['watch'], queryFn: getWatch, refetchInterval: check.isPending ? 1000 : false })
  // CLAUDE> a check through the browser needs this page to hand the sites over to the extension
  useEffect(() => (check.isPending ? servePageRequests(() => {}) : undefined), [check.isPending])
  if (!state.data) return null
  const data = state.data
  const needs = data.needs_browser
  const chosen = data.sites.filter(s => !unticked.includes(s.id))
  const all = chosen.length === data.sites.length
  return (
    <section className="watch-card">
      <div className="watch-head">
        <h2>Watched agenda sites</h2>
        <span className="watch-buttons">
          <StopButton action="watch-check" running={check.isPending} />
          <button type="button" className="primary" disabled={check.isPending || chosen.length === 0}
                  onClick={() => check.mutate({ viaBrowser: false, only: all ? null : chosen.map(s => s.id) })}>
            {check.isPending ? 'Checking…' : all ? 'Check for new events' : `Check ${chosen.length} of ${data.sites.length} sites`}
          </button>
        </span>
      </div>
      {data.sites.length > 0 && (
        <SiteChoices sites={data.sites} unticked={unticked} onChange={keepUnticked} disabled={check.isPending} />
      )}
      {needs.length > 0 && !extensionIsCurrent() && (
        <p className="news-notice">Reload the Automation desk reader extension once: vivaldi://extensions, then ↻ on its card, then
          this page. Read via browser needs it.</p>
      )}
      {needs.length > 0 && (
        <div className="watch-action">
          <span><strong>{needs.map(s => s.site.replace(/^https?:\/\/(www\.)?/, '')).join(', ')}</strong> {needs.length === 1 ? 'needs' : 'need'}
            {' '}your browser</span>
          <button type="button" className="primary" disabled={check.isPending}
                  onClick={() => check.mutate({ viaBrowser: true, only: needs.map(s => s.id) })}>Read via browser</button>
        </div>
      )}
      {check.isPending && <CheckProgress sites={data.progress?.sites ?? []} pause={data.progress?.pause ?? 0} />}
      {check.isError && <p className="cap-error">{check.error.message}</p>}
      <details className="watch-settings" open={data.lines.length === 0}>
        <summary>{data.lines.length} {data.lines.length === 1 ? 'site' : 'sites'} · default calendar {data.default_calendar}</summary>
        <WatchEditor key={`${data.default_calendar}\n${data.lines.join('\n')}`} data={data}
                     onSaved={() => client.invalidateQueries({ queryKey: ['watch'] })} />
        <FromEvents onDone={() => client.invalidateQueries({ queryKey: ['watch'] })} />
      </details>
    </section>
  )
}

// CLAUDE> a tick per site to choose which ones a check reads; each name opens its agenda page
function SiteChoices({ sites, unticked, onChange, disabled }: {
  sites: WatchedSite[]; unticked: number[]; onChange: (next: number[]) => void; disabled: boolean
}) {
  const [open, setOpen] = useState(storedOpen)
  const toggle = (next: boolean) => {
    setOpen(next)
    try { localStorage.setItem(OPEN_KEY, next ? 'yes' : 'no') } catch { /* CLAUDE> no storage: the choice lasts this visit */ }
  }
  const ticked = sites.filter(s => !unticked.includes(s.id)).length
  // CLAUDE> closed, the line still says what the last check found, e.g. "bozar.be: 9 new"
  const news = sites.filter(s => /^\d+ new/.test(s.last_result)).map(s => `${shortName(s.label)}: ${s.last_result}`)
  return (
    <details className="watch-sites" open={open} onToggle={e => toggle(e.currentTarget.open)}>
      <summary>
        <span className="watch-sites-title">Sites to check</span>
        <span className="muted">{ticked} of {sites.length} ticked</span>
        {news.length > 0 && <span className="watch-sites-news">{news.join(', ')}</span>}
      </summary>
      <div className="watch-sites-head">
        <button type="button" className="link-button" disabled={disabled} onClick={() => onChange([])}>All</button>
        <button type="button" className="link-button" disabled={disabled} onClick={() => onChange(sites.map(s => s.id))}>None</button>
      </div>
      <ul>
        {sites.map(s => (
          <li key={s.id} className="watch-site">
            <input type="checkbox" checked={!unticked.includes(s.id)} disabled={disabled} aria-label={`Check ${s.label}`}
                   onChange={e => onChange(e.target.checked ? unticked.filter(id => id !== s.id) : [...unticked, s.id])} />
            <a href={s.url} target="_blank" rel="noreferrer" className="watch-site-name">{s.label}</a>
            {s.calendar && <span className="muted">→ {s.calendar}</span>}
            {s.last_result && <span className="watch-site-result muted" title={s.last_result}>{s.last_result}</span>}
          </li>
        ))}
      </ul>
    </details>
  )
}

const MARK: Record<SiteProgress['state'], string> = { waiting: '', reading: '', done: '✓', browser: '!', failed: '×' }

// CLAUDE> the running check, site by site: which one is read now, how far it got, and what the others gave
function CheckProgress({ sites, pause }: { sites: SiteProgress[]; pause: number }) {
  const current = sites.findIndex(s => s.state === 'reading')
  const finished = sites.filter(s => !['waiting', 'reading'].includes(s.state)).length
  return (
    <div className="check-progress" aria-live="polite">
      <p className="check-step">
        {sites.length === 0 ? 'Starting…'
          : current >= 0 ? `Reading site ${current + 1} of ${sites.length}`
          : finished === sites.length ? 'Putting the new events together…' : 'Starting…'}
      </p>
      {pause > 0 && (
        <p className="check-pause">Pausing {pause} s: the language model takes at most 20 requests a minute on this account.</p>
      )}
      <ul>
        {sites.map(s => (
          <li key={s.site} className={`check-site ${s.state}`}>
            <span className="check-mark" aria-hidden="true">{MARK[s.state]}</span>
            <span className="check-name">{s.site}</span>
            <span className="check-detail">{s.state === 'reading' && !s.detail ? 'opening the page…' : s.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function WatchEditor({ data, onSaved }: { data: WatchState; onSaved: () => void }) {
  const [lines, setLines] = useState(data.lines.join('\n'))
  const [calendar, setCalendar] = useState(data.default_calendar)
  const save = useMutation({ mutationFn: () => saveWatch(lines.split('\n'), calendar), onSuccess: onSaved })
  const changed = lines !== data.lines.join('\n') || calendar !== data.default_calendar
  return (
    <div className="watch-editor">
      <label className="watch-default">Default calendar
        <input value={calendar} onChange={e => setCalendar(e.target.value)} />
      </label>
      <label className="watch-lines">One site per line; add <code>→ calendar</code> for another calendar than the default
        <textarea rows={10} value={lines} onChange={e => setLines(e.target.value)}
                  placeholder={'kmska.be/nl/agenda → Exhibitions\nmas.be/nl/agenda'} />
      </label>
      <button type="button" className="quiet" disabled={!changed || save.isPending} onClick={() => save.mutate()}>Save</button>
      {save.isError && <p className="cap-error">Not saved: {save.error.message}</p>}
    </div>
  )
}

// CLAUDE> the agenda pages the user imported events from before, found from those events; nothing to type
function FromEvents({ onDone }: { onDone: () => void }) {
  const find = useMutation({ mutationFn: watchFromEvents, onSuccess: onDone })
  return (
    <div className="watch-from-events">
      <button type="button" className="quiet" disabled={find.isPending} onClick={() => find.mutate()}>
        {find.isPending ? 'Looking through your calendars…' : 'Add sites from my calendar events'}</button>
      <StopButton action="watch-from-events" running={find.isPending} />
      {find.data && (
        <span className="muted">
          {find.data.added === 0 ? 'No new sites found.' : `Added ${find.data.added} ${find.data.added === 1 ? 'site' : 'sites'}.`}
          {find.data.unmatched > 0 && ` ${find.data.unmatched} import${find.data.unmatched === 1 ? '' : 's'} came from a PDF or typed text.`}
        </span>
      )}
      {find.isError && <span className="cap-error">{find.error.message}</span>}
    </div>
  )
}

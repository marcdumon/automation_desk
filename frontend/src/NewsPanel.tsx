import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import {
  answerSuggestion, continueNewsDigest, deleteNewsDigest, deleteNewsStory, deleteNewsSubject, getNewsDigest, getNewsOverview, makeNewsDigest, saveBlocked, saveNewsSites, saveSubjects, setNewsCap,
  type NewsDigest, type SuggestionAnswer,
} from './api'
import { servePageRequests } from './capture'
import { when } from './format'
import { extensionIsCurrent, openInBackground } from './openTab'
import StopButton from './StopButton'

// CLAUDE> a digest costs fractions of a cent: four decimals below a cent, two above
const cost = (dollars: number) => `$${dollars < 0.01 ? dollars.toFixed(4) : dollars.toFixed(2)}`

const shortWhen = (iso: string) =>
  new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })

// CLAUDE> the News group's own view: today's digest, suggestions, earlier digests, sites and subjects
export default function NewsPanel() {
  const client = useQueryClient()
  const overview = useQuery({ queryKey: ['news', 'overview'], queryFn: getNewsOverview,
                              refetchInterval: q => (q.state.data?.running ? 2000 : false) })
  const [chosen, setChosen] = useState<number | null>(null)
  const digestId = chosen ?? overview.data?.digests[0]?.id ?? null
  const digest = useQuery({ queryKey: ['news', 'digest', digestId], queryFn: () => getNewsDigest(digestId!), enabled: digestId !== null })
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['news'] })
    client.invalidateQueries({ queryKey: ['jobs'] })
  }
  const make = useMutation({ mutationFn: makeNewsDigest, onSuccess: refresh })
  const suggest = useMutation({ mutationFn: ({ name, answer }: { name: string; answer: SuggestionAnswer }) => answerSuggestion(name, answer),
                                onSuccess: refresh })
  // CLAUDE> while a digest runs, sites that refuse programs are read through this browser: this page must hand them over
  const running = Boolean(overview.data?.running)
  useEffect(() => (running ? servePageRequests(() => {}) : undefined), [running])
  // CLAUDE> when a run ends, reload the digest too: its stories and follow-ups changed
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !running) client.invalidateQueries({ queryKey: ['news'] })
    wasRunning.current = running
  }, [running, client])
  if (!overview.data) return null
  const data = overview.data
  return (
    <div className="news">
      <div className="news-toolbar">
        <button type="button" className="primary" disabled={data.running || make.isPending} onClick={() => make.mutate()}>
          {data.running ? 'Being made…' : 'Make digest now'}
        </button>
        <CapField key={data.cap_usd} value={data.cap_usd} onSaved={refresh} />
      </div>
      {!extensionIsCurrent() && (
        <p className="news-notice">Reload the Automation desk reader extension once: vivaldi://extensions, then ↻ on its card, then
          this page.</p>
      )}
      {data.running && data.progress?.step && (
        <section className="news-card digest-progress">
          <div className="digest-progress-head">
            <h3>{data.progress.step}</h3>
            <StopButton action="news-digest" running={data.running} />
          </div>
          <ul>{Object.entries(data.progress.sites ?? {}).map(([site, status]) => (
            <li key={site}><span>{site}</span> <span className="muted">{status}</span></li>))}</ul>
        </section>
      )}
      {data.failure && !data.running && (data.failure.startsWith('you stopped it')
        ? <p className="news-card news-quiet">You stopped the digest, so none was made. Nothing is lost: Make digest now takes the
            same new articles.</p>
        : <p className="news-card news-error">The last digest could not be made: {data.failure}. Press Make digest now to try again.</p>
      )}
      {data.nothing_new && !data.running && (
        <p className="news-card news-quiet">Nothing new since {shortWhen(data.nothing_new.since)}
          {' '}(checked {shortWhen(data.nothing_new.at)}).</p>
      )}
      {/* CLAUDE> after the last digest is deleted the page said nothing: what the button does */}
      {data.digests.length === 0 && !data.running && !data.failure && !data.nothing_new && (
        <p className="news-card news-quiet">No digest to show. Make digest now reads your {data.sources.length} sites and puts the
          new articles together by subject.</p>
      )}
      {data.suggestions.length > 0 && (
        <section className="news-card">
          <h3>Suggested subjects</h3>
          <ul className="suggestions">{data.suggestions.map(s => (
            <li key={s.name}>
              <span className="suggestion-name">{s.name}</span>
              <span className="muted suggestion-examples">{s.examples.length} · {s.examples.slice(0, 2).join(' · ')}</span>
              <span className="suggestion-buttons">
                <button type="button" className="quiet" title="Add as a subject" onClick={() => suggest.mutate({ name: s.name, answer: 'accept' })}>
                  Accept</button>
                <button type="button" className="quiet" title="Add to the blocked topics"
                        onClick={() => suggest.mutate({ name: s.name, answer: 'block' })}>Block</button>
                <button type="button" className="quiet" title="Do not suggest it again"
                        onClick={() => suggest.mutate({ name: s.name, answer: 'reject' })}>Reject</button>
              </span>
            </li>))}</ul>
        </section>
      )}
      <details className="news-card news-settings-box" open={data.subjects.length === 0 && data.blocked.length === 0}>
        <summary><span>Settings</span> <span className="muted">{data.sources.length} sites · {data.subjects.length} subjects
          {' · '}{data.blocked.length} blocked topics</span></summary>
        <SitesAndSubjects sources={data.sources} subjects={data.subjects} blocked={data.blocked} onChange={refresh} />
      </details>
      {data.digests.length > 1 && (
        <details className="news-card news-history">
          <summary><span>Earlier digests</span> <span className="muted">{data.digests.length - 1}</span></summary>
          <ul>{data.digests.map(d => (
            <li key={d.id}><button type="button" className="link" onClick={() => setChosen(d.id)}>
              {shortWhen(d.made_at)}</button> <span className="muted">{d.story_count} stories from {d.source_count} sites</span></li>))}</ul>
        </details>
      )}
      {digest.data && <DigestView digest={digest.data} onChange={refresh} onDeleted={() => { setChosen(null); refresh() }} />}
    </div>
  )
}

function DigestView({ digest, onChange, onDeleted }: { digest: NewsDigest; onChange: () => void; onDeleted: () => void }) {
  const remove = useMutation({ mutationFn: deleteNewsStory, onSuccess: onChange })
  const removeSubject = useMutation({ mutationFn: (subject: string) => deleteNewsSubject(digest.id, subject), onSuccess: onChange })
  const removeDigest = useMutation({ mutationFn: () => deleteNewsDigest(digest.id), onSuccess: onDeleted })
  const failed = remove.error ?? removeSubject.error ?? removeDigest.error
  return (
    <section className="digest">
      <header className="digest-card">
        <div className="digest-title">
          <h2>Digest · {shortWhen(digest.made_at)}</h2>
          <button type="button" className="quiet" disabled={removeDigest.isPending} onClick={() => removeDigest.mutate()}>Delete</button>
        </div>
        <p className="digest-meta">
          {digest.story_count} {digest.story_count === 1 ? 'story' : 'stories'} from {digest.source_count}
          {' '}{digest.source_count === 1 ? 'site' : 'sites'} · since {shortWhen(digest.covers_from)} · {cost(digest.cost_usd)}
        </p>
        <FollowUps digest={digest} onStarted={onChange} />
        {failed && <p className="cap-error">{failed.message}</p>}
        {digest.problems.length > 0 && <ul className="digest-problems">{digest.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
      </header>
      {digest.left_out.length > 0 && <LeftOutList articles={digest.left_out} />}
      {digest.subjects.map(group => (
        <details key={group.subject} className="digest-subject">
          <summary>
            <h3>{group.subject} <span className="muted">({group.stories.length})</span></h3>
            {/* CLAUDE> far right and red, away from the title the user clicks to fold; the click must not fold it */}
            <button type="button" className="story-delete subject-delete" aria-label={`Delete all ${group.subject} stories`} title="Delete this whole subject"
                    disabled={removeSubject.isPending}
                    onClick={e => {
                      e.preventDefault()
                      removeSubject.mutate(group.subject)
                    }}>✕</button>
          </summary>
          {group.stories.map(story => (
            <article key={story.id} className="story">
              {/* CLAUDE> delete sits right before the title: open and delete are one small move apart */}
              <div className="story-head">
                <button type="button" className="story-delete" aria-label={`Delete ${story.title}`} title="Delete this story"
                        disabled={remove.isPending} onClick={() => remove.mutate(story.id)}>✕</button>
                <a className="story-title" href={story.articles[0]?.link} target="_blank" rel="noreferrer"
                   onClick={openInBackground}>{story.title}</a>
              </div>
              {story.summary && <p className="story-summary">{story.summary}</p>}
              <p className="story-sources">
                {story.articles.map((a, i) => (
                  <span key={a.link}>{i > 0 && ' · '}
                    <a href={a.link} target="_blank" rel="noreferrer" onClick={openInBackground}>{a.source || 'source'}</a></span>
                ))}
              </p>
            </article>
          ))}
        </details>
      ))}
    </section>
  )
}

// CLAUDE> articles on a blocked topic: counted per topic, titles folded away so a wrongly filed one can still be found
function LeftOutList({ articles }: { articles: NewsDigest['left_out'] }) {
  const topics = new Map<string, number>()
  for (const a of articles) topics.set(a.topic, (topics.get(a.topic) ?? 0) + 1)
  return (
    <details className="left-out">
      <summary>{articles.length} article{articles.length === 1 ? '' : 's'} left out:
        {' '}{[...topics].map(([topic, n]) => `${topic} (${n})`).join(', ')}</summary>
      <ul>{articles.map(a => (
        <li key={a.link}><a href={a.link} target="_blank" rel="noreferrer" onClick={openInBackground}>{a.title}</a>
          <span className="muted"> {a.source} · {a.topic}</span></li>))}</ul>
    </details>
  )
}

// CLAUDE> what the digest could not do without the user: sites behind a human check, and articles past the daily cap
// CLAUDE> what the digest could not do without the user: sites that block programs, and headlines past the daily cap
function FollowUps({ digest, onStarted }: { digest: NewsDigest; onStarted: () => void }) {
  const go = useMutation({ mutationFn: (raiseCap: boolean) => continueNewsDigest(digest.id, raiseCap), onSuccess: onStarted })
  const blocked = digest.needs_check.length > 0
  const failed = digest.failed.length > 0
  const unsorted = digest.unsorted > 0 && digest.cap_to_sort !== null
  if (!blocked && !failed && !unsorted && !go.isError) return null
  return (
    <ul className="digest-actions">
      {blocked && (
        <li>
          <span><strong>{digest.needs_check.map(s => s.name).join(', ')}</strong> {digest.needs_check.length === 1 ? 'blocks' : 'block'}
            {' '}bots</span>
          <button type="button" className="primary" disabled={go.isPending} onClick={() => go.mutate(false)}>Read via browser</button>
        </li>
      )}
      {failed && (
        <li>
          <details>
            <summary><strong>{digest.failed.length} {digest.failed.length === 1 ? 'site' : 'sites'}</strong> didn't answer</summary>
            <p className="muted">{digest.failed.map(s => s.name).join(', ')}</p>
          </details>
          <button type="button" className="primary" disabled={go.isPending} onClick={() => go.mutate(false)}>Try again</button>
        </li>
      )}
      {unsorted && (
        <li>
          <span><strong>{digest.unsorted} {digest.unsorted === 1 ? 'headline' : 'headlines'}</strong> not sorted: cost cap reached</span>
          <button type="button" className="primary" disabled={go.isPending} onClick={() => go.mutate(true)}>
            {digest.cap_to_sort! > digest.cap_usd ? `Raise cap to $${digest.cap_to_sort!.toFixed(2)} and sort` : 'Sort'}
          </button>
        </li>
      )}
      {go.isError && <li className="cap-error">Not started: {go.error.message}</li>}
    </ul>
  )
}

function CapField({ value, onSaved }: { value: number; onSaved: () => void }) {
  const [text, setText] = useState(value.toFixed(2))
  // CLAUDE> '0,50' is how a Belgian keyboard writes it; an empty or unreadable field is never saved as $0
  const parsed = text.trim() === '' ? NaN : Number(text.trim().replace(',', '.'))
  const save = useMutation({ mutationFn: () => setNewsCap(parsed), onSuccess: onSaved })
  const invalid = Number.isNaN(parsed) || parsed < 0 || parsed > 10
  return (
    <label className="cap">Daily cost cap $
      <input value={text} aria-invalid={invalid} onChange={e => setText(e.target.value)}
             onBlur={() => !invalid && parsed !== value && save.mutate()} />
      {invalid && <span className="cap-error"> Type an amount from 0 to 10, e.g. 0.30.</span>}
      {save.isError && <span className="cap-error"> Not saved: {save.error.message}</span>}
    </label>
  )
}

function SitesAndSubjects({ sources, subjects, blocked, onChange }: {
  sources: { id: number; name: string; site: string; feed: string; last_result: string }[]
  subjects: string[]; blocked: string[]; onChange: () => void
}) {
  // CLAUDE> kept here, outside the keyed editor, so the problems stay visible after the list reloads
  const [siteProblems, setSiteProblems] = useState<string[]>([])
  return (
    <div className="news-settings">
      <div>
        <SiteList key={sources.map(s => s.site).join('\n')} sources={sources}
                  onSaved={problems => { setSiteProblems(problems); onChange() }} />
        {siteProblems.map(p => <p key={p} className="cap-error">{p}</p>)}
      </div>
      {/* CLAUDE> keyed so an accepted suggestion or saved list refills the editor */}
      <NameList key={`s:${subjects.join('\n')}`} title="Subjects" names={subjects} save={saveSubjects}
                label="Save" onSaved={onChange} />
      <NameList key={`b:${blocked.join('\n')}`} title="Blocked topics" names={blocked} save={saveBlocked}
                label="Save" onSaved={onChange} />
    </div>
  )
}

const shortSite = (site: string) => site.replace(/^https?:\/\//, '').replace(/\/$/, '')

// CLAUDE> the followed sites as an editable list: add or delete lines, then save; new sites get their feed looked up
function SiteList({ sources, onSaved }: {
  sources: { id: number; name: string; site: string; feed: string; last_result: string }[]; onSaved: (problems: string[]) => void
}) {
  const initial = sources.map(s => shortSite(s.site)).join('\n')
  const [text, setText] = useState(initial)
  const saved = useMutation({ mutationFn: () => saveNewsSites(text.split('\n')), onSuccess: answer => onSaved(answer.problems) })
  // CLAUDE> only sites whose last read went wrong; '31 new' and first reads are fine
  const troubled = sources.filter(s => s.last_result && !/^(\d+ new|first read)/.test(s.last_result))
  return (
    <div>
      <h3>Sites <span className="muted">one per line</span></h3>
      <textarea rows={12} value={text} onChange={e => setText(e.target.value)} />
      <button type="button" className="quiet" disabled={text === initial || saved.isPending} onClick={() => saved.mutate()}>
        {saved.isPending ? 'Saving… (looking up feeds)' : 'Save'}</button>
      <StopButton action="news-sites" running={saved.isPending} />
      {saved.isError && <p className="cap-error">Not saved: {saved.error.message}</p>}
      {troubled.length > 0 && (
        <details className="site-problems">
          <summary>{troubled.length} {troubled.length === 1 ? 'site has' : 'sites have'} a problem</summary>
          <ul>{troubled.map(s => <li key={s.id}><strong>{shortSite(s.site)}</strong> <span className="muted">{s.last_result.split('\n')[0]}</span></li>)}</ul>
        </details>
      )}
    </div>
  )
}

function NameList({ title, names, save, label, onSaved }: {
  title: string; names: string[]; save: (names: string[]) => Promise<unknown>; label: string; onSaved: () => void
}) {
  const [text, setText] = useState(names.join('\n'))
  const saved = useMutation({ mutationFn: () => save(text.split('\n')), onSuccess: onSaved })
  return (
    <div>
      <h3>{title}</h3>
      <textarea rows={12} value={text} onChange={e => setText(e.target.value)} />
      <button type="button" className="quiet" disabled={text === names.join('\n') || saved.isPending}
              onClick={() => saved.mutate()}>{label}</button>
    </div>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  answerSuggestion, deleteNewsDigest, deleteNewsStory, deleteNewsSubject, getNewsDigest, getNewsOverview, makeNewsDigest, saveBlocked, saveNewsSites, saveSubjects, setNewsCap,
  type NewsDigest, type SuggestionAnswer,
} from './api'
import { usd, when } from './format'
import { canOpenInBackground, openInBackground } from './openTab'

const shortWhen = (iso: string) =>
  new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })

// CLAUDE> the News group's own view: today's digest, suggestions, earlier digests, sites and subjects
export default function NewsPanel() {
  const client = useQueryClient()
  const overview = useQuery({ queryKey: ['news', 'overview'], queryFn: getNewsOverview,
                              refetchInterval: q => (q.state.data?.running ? 3000 : false) })
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
  if (!overview.data) return null
  const data = overview.data
  return (
    <div className="news">
      <div className="news-bar">
        <button type="button" className="primary" disabled={data.running || make.isPending} onClick={() => make.mutate()}>
          {data.running ? 'Being made…' : 'Make digest now'}
        </button>
        <CapField key={data.cap_usd} value={data.cap_usd} onSaved={refresh} />
      </div>
      {/* CLAUDE> settings and history sit above the digest: below it they were ten screens down */}
      <details className="news-settings-box" open={data.subjects.length === 0 && data.blocked.length === 0}>
        <summary>Settings: {data.sources.length} site{data.sources.length === 1 ? '' : 's'}, {data.subjects.length} subject
          {data.subjects.length === 1 ? '' : 's'}, {data.blocked.length} blocked topic{data.blocked.length === 1 ? '' : 's'}</summary>
        <SitesAndSubjects sources={data.sources} subjects={data.subjects} blocked={data.blocked} onChange={refresh} />
      </details>
      {data.digests.length > 1 && (
        <details className="news-history">
          <summary>Earlier digests ({data.digests.length - 1})</summary>
          <ul>{data.digests.map(d => (
            <li key={d.id}><button type="button" className="link" onClick={() => setChosen(d.id)}>
              {when(d.made_at)}: {d.story_count} stories from {d.source_count} sites</button></li>))}</ul>
        </details>
      )}
      {data.nothing_new && !data.running && (
        <div className="message">
          <p>Nothing new since {shortWhen(data.nothing_new.since)} (checked {shortWhen(data.nothing_new.at)}).</p>
          {data.nothing_new.problems.map(p => <p key={p} className="muted">{p}</p>)}
        </div>
      )}
      {data.failure && !data.running && (
        <div className="message error">
          <p>The last digest could not be made: {data.failure}. The morning run tries again an hour later, or press Make digest
            now. The failed job, with its details, is in the job list below.</p>
        </div>
      )}
      {data.suggestions.map(s => (
        <div key={s.name} className="message suggestion">
          <p>Suggested subject <strong>{s.name}</strong> ({s.examples.length} headline{s.examples.length === 1 ? '' : 's'}):
            {' '}{s.examples.slice(0, 3).join(' · ')}</p>
          <button type="button" className="quiet" title="Add as a subject" onClick={() => suggest.mutate({ name: s.name, answer: 'accept' })}>
            Accept</button>
          <button type="button" className="quiet" title="Add to the blocked topics: its articles are left out"
                  onClick={() => suggest.mutate({ name: s.name, answer: 'block' })}>Block</button>
          <button type="button" className="quiet" title="Not a subject; do not suggest it again"
                  onClick={() => suggest.mutate({ name: s.name, answer: 'reject' })}>Reject</button>
        </div>
      ))}
      {digest.data ? <DigestView digest={digest.data} onChange={refresh} onDeleted={() => { setChosen(null); refresh() }} /> : <p className="muted">No digest yet. Add sites, then make one.</p>}
    </div>
  )
}

function DigestView({ digest, onChange, onDeleted }: { digest: NewsDigest; onChange: () => void; onDeleted: () => void }) {
  const remove = useMutation({ mutationFn: deleteNewsStory, onSuccess: onChange })
  const removeSubject = useMutation({ mutationFn: (subject: string) => deleteNewsSubject(digest.id, subject), onSuccess: onChange })
  const removeDigest = useMutation({ mutationFn: () => deleteNewsDigest(digest.id), onSuccess: onDeleted })
  const failed = remove.error ?? removeSubject.error ?? removeDigest.error
  const reasons = new Map<string, number>()
  for (const a of digest.subjects.flatMap(g => g.stories.flatMap(s => s.articles)).filter(a => a.from_teaser)) {
    reasons.set(a.reason || 'no reason given', (reasons.get(a.reason || 'no reason given') ?? 0) + 1)
  }
  const teasers = [...reasons.values()].reduce((sum, n) => sum + n, 0)
  return (
    <section className="digest">
      <div className="digest-head">
        <h2>Digest of {when(digest.made_at)}</h2>
        <button type="button" className="quiet" disabled={removeDigest.isPending}
                onClick={() => removeDigest.mutate()}>Delete digest</button>
      </div>
      <p className="muted">Since {when(digest.covers_from)}: {digest.article_count} articles, {digest.story_count} stories,
        {' '}{digest.source_count} sites; cost {usd(digest.cost_usd)}.</p>
      {teasers > 0 && (
        <p className="muted">{teasers} article{teasers === 1 ? '' : 's'} summarised from the feed teaser:
          {' '}{[...reasons].map(([reason, n]) => `${reason} (${n})`).join(', ')}.</p>
      )}
      {!canOpenInBackground() && (
        <p className="muted open-hint">Links open in a tab in front of this page. To open them behind it instead, reload the
          Automation desk reader extension: go to vivaldi://extensions and press the reload arrow (↻) on its card, then reload
          this page.</p>
      )}
      {failed && <p className="cap-error">{failed.message}</p>}
      {digest.left_out.length > 0 && <LeftOutList articles={digest.left_out} />}
      {digest.problems.length > 0 && <ul className="sheet-notes">{digest.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
      {digest.subjects.map(group => (
        <details key={group.subject} className="digest-subject" open>
          <summary>
            {/* CLAUDE> a click on the ✕ must not also fold the section */}
            <button type="button" className="story-delete" aria-label={`Delete all ${group.subject} stories`} title="Delete this whole subject"
                    disabled={removeSubject.isPending}
                    onClick={e => {
                      e.preventDefault()
                      removeSubject.mutate(group.subject)
                    }}>✕</button>
            <h3>{group.subject} <span className="muted">({group.stories.length})</span></h3>
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
              <p className="story-summary">{story.summary}</p>
              <p className="story-sources">
                {story.articles.map((a, i) => (
                  <span key={a.link}>{i > 0 && ' · '}<a href={a.link} target="_blank" rel="noreferrer" onClick={openInBackground}>{a.source || 'source'}</a>
                    {a.published && <span className="muted"> {shortWhen(a.published)}</span>}
                    {a.from_teaser && <span className="muted"> (from teaser{a.reason ? `: ${a.reason}` : ''})</span>}</span>
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
      <NameList key={`s:${subjects.join('\n')}`} title="Subjects, one per line, in order" names={subjects} save={saveSubjects}
                label="Save subjects" onSaved={onChange} />
      <NameList key={`b:${blocked.join('\n')}`} title="Blocked topics, left out of the digest" names={blocked} save={saveBlocked}
                label="Save blocked topics" onSaved={onChange} />
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
  return (
    <div>
      <h3>Sites, one per line</h3>
      <textarea rows={Math.max(4, sources.length + 1)} value={text} onChange={e => setText(e.target.value)} />
      <button type="button" className="quiet" disabled={text === initial || saved.isPending} onClick={() => saved.mutate()}>
        {saved.isPending ? 'Saving… (looking up feeds)' : 'Save sites'}</button>
      {saved.isError && <p className="cap-error">Not saved: {saved.error.message}</p>}
      <ul className="site-status">{sources.map(s => (
        <li key={s.id}><strong>{s.name}</strong> <span className="muted">{s.feed ? 'feed' : 'front page'}
          {s.last_result ? ` · ${s.last_result.split('\n')[0]}` : ''}</span></li>))}</ul>
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
      <textarea rows={Math.max(4, names.length + 1)} value={text} onChange={e => setText(e.target.value)} />
      <button type="button" className="quiet" disabled={text === names.join('\n') || saved.isPending}
              onClick={() => saved.mutate()}>{label}</button>
    </div>
  )
}

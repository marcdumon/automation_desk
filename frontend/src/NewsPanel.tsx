import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import {
  answerSuggestion, getNewsDigest, getNewsOverview, makeNewsDigest, removeNewsSource, saveSubjects, setNewsCap, type NewsDigest,
} from './api'
import { when } from './format'

// CLAUDE> the News group's own view: today's digest, suggestions, earlier digests, sites and subjects
export default function NewsPanel() {
  const client = useQueryClient()
  const overview = useQuery({ queryKey: ['news', 'overview'], queryFn: getNewsOverview,
                              refetchInterval: q => (q.state.data?.running ? 3000 : false) })
  const [chosen, setChosen] = useState<number | null>(null)
  const digestId = chosen ?? overview.data?.digests[0]?.id ?? null
  const digest = useQuery({ queryKey: ['news', 'digest', digestId], queryFn: () => getNewsDigest(digestId!), enabled: digestId !== null })
  const refresh = () => client.invalidateQueries({ queryKey: ['news'] })
  const make = useMutation({ mutationFn: makeNewsDigest, onSuccess: refresh })
  const suggest = useMutation({ mutationFn: ({ name, accept }: { name: string; accept: boolean }) => answerSuggestion(name, accept),
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
      {data.suggestions.map(s => (
        <div key={s.name} className="message suggestion">
          <p>Suggested subject <strong>{s.name}</strong>: {s.examples.slice(0, 3).join(' · ')}</p>
          <button type="button" className="quiet" onClick={() => suggest.mutate({ name: s.name, accept: true })}>Accept</button>
          <button type="button" className="quiet" onClick={() => suggest.mutate({ name: s.name, accept: false })}>Reject</button>
        </div>
      ))}
      {digest.data ? <DigestView digest={digest.data} /> : <p className="muted">No digest yet. Add sites, then make one.</p>}
      {data.digests.length > 1 && (
        <details className="news-history">
          <summary>Earlier digests ({data.digests.length - 1})</summary>
          <ul>{data.digests.map(d => (
            <li key={d.id}><button type="button" className="link" onClick={() => setChosen(d.id)}>
              {when(d.made_at)}: {d.story_count} stories from {d.source_count} sites</button></li>))}</ul>
        </details>
      )}
      {/* CLAUDE> keyed so an accepted suggestion or saved list refills the editor */}
      <SitesAndSubjects key={data.subjects.join('\n')} sources={data.sources} subjects={data.subjects} onChange={refresh} />
    </div>
  )
}

function DigestView({ digest }: { digest: NewsDigest }) {
  return (
    <section className="digest">
      <h2>Digest of {when(digest.made_at)}</h2>
      <p className="muted">Since {when(digest.covers_from)}: {digest.article_count} articles, {digest.story_count} stories,
        {' '}{digest.source_count} sites.</p>
      {digest.problems.length > 0 && <ul className="sheet-notes">{digest.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
      {digest.subjects.map(group => (
        <div key={group.subject} className="digest-subject">
          <h3>{group.subject}</h3>
          {group.stories.map(story => (
            <article key={story.id} className="story">
              <a className="story-title" href={story.articles[0]?.link} target="_blank" rel="noreferrer">{story.title}</a>
              <p className="story-summary">{story.summary}</p>
              <p className="story-sources">
                {story.articles.map((a, i) => (
                  <span key={a.link}>{i > 0 && ' · '}<a href={a.link} target="_blank" rel="noreferrer">{a.source || 'source'}</a>
                    {a.from_teaser && <span className="muted"> (from teaser{a.reason ? `: ${a.reason}` : ''})</span>}</span>
                ))}
              </p>
            </article>
          ))}
        </div>
      ))}
    </section>
  )
}

function CapField({ value, onSaved }: { value: number; onSaved: () => void }) {
  const [text, setText] = useState(value.toFixed(2))
  const save = useMutation({ mutationFn: () => setNewsCap(Number(text)), onSuccess: onSaved })
  return (
    <label className="cap">Daily cost cap $
      <input value={text} onChange={e => setText(e.target.value)} onBlur={() => Number(text) !== value && save.mutate()} />
    </label>
  )
}

function SitesAndSubjects({ sources, subjects, onChange }: {
  sources: { id: number; name: string; site: string; feed: string; last_result: string }[]; subjects: string[]; onChange: () => void
}) {
  const [names, setNames] = useState(subjects.join('\n'))
  const saved = useMutation({ mutationFn: () => saveSubjects(names.split('\n')), onSuccess: onChange })
  return (
    <div className="news-settings">
      <div>
        <h3>Sites ({sources.length})</h3>
        <ul>{sources.map(s => (
          <li key={s.id}><strong>{s.name}</strong> <span className="muted">{s.feed ? 'feed' : 'front page'}
            {s.last_result ? `, last: ${s.last_result}` : ''}</span>
            <button type="button" aria-label={`Remove ${s.name}`} className="link"
                    onClick={() => removeNewsSource(s.id).then(onChange)}>×</button></li>))}</ul>
      </div>
      <div>
        <h3>Subjects, one per line, in order</h3>
        <textarea rows={Math.max(4, subjects.length + 1)} value={names} onChange={e => setNames(e.target.value)} />
        <button type="button" className="quiet" disabled={names === subjects.join('\n') || saved.isPending}
                onClick={() => saved.mutate()}>Save subjects</button>
      </div>
    </div>
  )
}

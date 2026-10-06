import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { useLocation, useRoute } from 'wouter'

import {
  addSummarySites, answerResearch, cancelRequirements, confirmRequirements, continueResearch, deleteResearch, exportResearch,
  getResearch, getResearchList, goOnResearch, replyResearch, renameResearch, rescoreResearch, saveResearchSettings,
  setResearchBudget, startResearch, startSummary, type RankedProduct, type Research, type ResearchList, type ResearchQuestion,
  type ResearchRequirement, type ResearchSettings, type SummaryTarget, type Verdict, type Weight,
} from './api'
import { NEW_NOTE, nameOf, placeText, spotOf, SpotPicker, spotReady, spotTarget, type Spot } from './NoteSave'
import StopButton from './StopButton'

// CLAUDE> two tools: a product or service to buy (questions, requirements, a scored comparison with advice), and a subject to
// summarise from websites into Obsidian. Both can go on, change and start again: research goes back and forth.
const COUNTRIES = ['BE', 'NL', 'DE', 'FR', 'LU', 'AT', 'IT', 'ES', 'UK', 'PL', 'US']
const STEP_NAMES: Record<string, string> = {
  requirements: 'Writing your requirements', classes: 'Finding the price classes', plan: 'Planning the searches',
  search: 'Searching', read: 'Reading shop pages', compare: 'Comparing', followups: 'Checking if a question is needed',
  score: 'Scoring the products', advise: 'Writing the advice',
}
const SUMMARY_STEP_NAMES: Record<string, string> = { search: 'Searching the sites', read: 'Reading the pages', compile: 'Writing the summary' }
// CLAUDE> a state that waits for the user says what to do next
const STATE_NAMES: Record<string, string> = {
  questions: 'Answer the questions', requirements: 'Check the requirements', budget: 'Choose a budget', running: 'Running',
  waiting: 'Answer one more question', done: 'Done', failed: 'Failed', stopped: 'Stopped',
}
const YOUR_TURN = new Set(['questions', 'requirements', 'budget', 'waiting'])
const KIND_NAMES: Record<string, string> = { product: 'Product', service: 'Service', summary: 'Summary' }
const WEIGHTS: { value: Weight; label: string }[] = [
  { value: 'must', label: 'Must' }, { value: 'important', label: 'Important' }, { value: 'nice', label: 'Nice to have' },
]
const MARKS: Record<Verdict, { sign: string; text: string }> = {
  yes: { sign: '✓', text: 'Yes' }, partly: { sign: '½', text: 'Partly' }, no: { sign: '✗', text: 'No' },
  unknown: { sign: '?', text: 'Its page does not say' },
}

// CLAUDE> € 0 is a free product (or a budget of nothing): said as 'free'
const euro = (value: number | null | undefined) => (value == null ? 'not found' : value === 0 ? 'free' : `€ ${value.toFixed(2)}`)
// CLAUDE> each offer in its own currency; an unknown currency is said, never shown as euro
const SIGNS: Record<string, string> = { EUR: '€', USD: '$', GBP: '£' }
const money = (value: number | null | undefined, currency: string) => {
  if (value == null) return 'not found'
  const sign = SIGNS[currency]
  return sign ? `${sign} ${value.toFixed(2)}` : `${value.toFixed(2)} (currency not known)`
}
// CLAUDE> a summary can cost a fraction of a cent: '$0.00' looked free
const usd = (value: number) => (value > 0 && value < 0.01 ? `$${value.toFixed(3)}` : `$${value.toFixed(2)}`)
const day = (created: string) => new Date(created).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
const cut = (text: string, length: number) => (text.length > length ? `${text.slice(0, length - 1).trimEnd()}…` : text)

// CLAUDE> what "Start again with changes" carries to the new form: the parts that cannot change on a research's page
type Draft = { tool: 'product' | 'summary'; request?: string; budget?: number | null; countries?: string[]; subject?: string; sites?: string }

export default function ResearchPage() {
  const client = useQueryClient()
  const [, navigate] = useLocation()
  const [match, params] = useRoute('/research/:id')
  const openId = match ? Number(params.id) : null
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [draft, setDraft] = useState<{ n: number; draft: Draft | null }>({ n: 0, draft: null })
  const list = useQuery({ queryKey: ['research-list'], queryFn: getResearchList, refetchInterval: 5000 })
  const refresh = () => client.invalidateQueries({ queryKey: ['research-list'] })
  if (list.isError) return <section className="page accent-research"><p className="cap-error">{list.error.message}</p></section>
  if (!list.data) return null
  const data = list.data
  return (
    <section className="page accent-research research-page">
      <header className="research-top">
        {openId === null
          ? <h1>Research</h1>
          : <button type="button" className="research-back" onClick={() => navigate('/research')}>← All researches</button>}
        {/* CLAUDE> the settings are the defaults for new research: on a research's own page they looked like its settings */}
        {openId === null && (
          <button type="button" className="quiet research-settings-button" onClick={() => setSettingsOpen(true)}
                  aria-haspopup="dialog">Settings</button>
        )}
      </header>
      {openId === null
        ? <Overview key={draft.n} data={data} draft={draft.draft}
                    onStarted={r => { client.setQueryData(['research', r.id], r); refresh(); setDraft(d => ({ n: d.n, draft: null })); navigate(`/research/${r.id}`) }} />
        : <Open key={openId} id={openId} data={data} onChange={refresh} onDeleted={() => { refresh(); navigate('/research') }}
                onAgain={d => { setDraft(old => ({ n: old.n + 1, draft: d })); navigate('/research'); window.scrollTo(0, 0) }}
                onSettings={() => setSettingsOpen(true)} />}
      {settingsOpen && <SettingsPanel data={data} onSaved={refresh} onClose={() => setSettingsOpen(false)} />}
    </section>
  )
}

function Overview({ data, draft, onStarted }: { data: ResearchList; draft: Draft | null; onStarted: (r: Research) => void }) {
  const [, navigate] = useLocation()
  return (
    <>
      <NewCard data={data} draft={draft} onStarted={onStarted} />
      <section className="research-overview" aria-label="Your researches">
        <h2>Your researches</h2>
        {data.researches.length === 0 ? <p className="muted">Your researches show here once you start one.</p> : (
          <ul className="research-cards">
            {data.researches.map(r => (
              <li key={r.id}>
                <button type="button" className="research-card" onClick={() => navigate(`/research/${r.id}`)}>
                  <span className="research-card-title">{r.title || cut(r.request, 70)}</span>
                  <span className="research-card-meta">
                    <span className="research-kind">{KIND_NAMES[r.kind] ?? r.kind}</span>
                    <span className={`state-chip state-${r.state}${YOUR_TURN.has(r.state) ? ' your-turn' : ''}`}>{STATE_NAMES[r.state] ?? r.state}</span>
                    <span>{day(r.created)}</span>
                    <span>{usd(r.cost_usd)}</span>
                  </span>
                  {r.kind === 'summary' ? (
                    r.summary && r.summary.points > 0 ? (
                      <span className="research-card-best">
                        <span className="research-card-figure">
                          <span className="research-card-score">{r.summary.points}</span>
                          <span className="research-card-unit">key points</span>
                        </span>
                        <span className="muted">
                          from {r.summary.sources} {r.summary.sources === 1 ? 'page' : 'pages'} on {r.summary.sites} {r.summary.sites === 1 ? 'site' : 'sites'}
                        </span>
                      </span>
                    ) : <span className="muted research-card-best">{r.state === 'done' || r.state === 'stopped' ? 'No summary' : 'Not finished'}</span>
                  ) : r.best ? (
                    <span className="research-card-best">
                      <span className="research-card-figure" aria-label={`Score ${r.best.score} of 100`}>
                        <span className="research-card-score">{r.best.score}</span>
                        <span className="research-card-unit">score</span>
                      </span>
                      <span>
                        <span className="research-card-product">{r.best.title}</span>
                        <span className="muted">{r.best.total != null ? euro(r.best.total) : 'no price on the website'}</span>
                      </span>
                    </span>
                  ) : <span className="muted research-card-best">{r.state === 'done' || r.state === 'stopped' ? 'No recommendation' : 'Not finished'}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  )
}

function CountryChips({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  return (
    <div className="country-chips" role="group" aria-label="Countries">
      {COUNTRIES.map(c => {
        const on = value.includes(c)
        return (
          <button key={c} type="button" className={`choice-chip${on ? ' chosen' : ''}`} aria-pressed={on}
                  onClick={() => onChange(on ? value.filter(x => x !== c) : [...value, c])}>{c}</button>
        )
      })}
    </div>
  )
}

function NewResearch({ settings, estimate, draft, onStarted }: {
  settings: ResearchSettings; estimate: number; draft: Draft | null; onStarted: (r: Research) => void
}) {
  const [request, setRequest] = useState(draft?.request ?? '')
  const [budget, setBudget] = useState(draft?.budget != null ? String(draft.budget) : '')
  const [countries, setCountries] = useState<string[] | null>(draft?.countries ?? null)
  const chosen = countries ?? settings.countries
  const amount = budget.trim() === '' ? null : Number(budget.replace(',', '.'))
  const badBudget = amount !== null && (!Number.isFinite(amount) || amount <= 0)
  const start = useMutation({ mutationFn: () => startResearch(request.trim(), amount, chosen), onSuccess: onStarted })
  return (
    <>
      <h2>What do you need?</h2>
      <textarea className="research-text" rows={4} value={request} onChange={e => setRequest(e.target.value)}
                aria-label="What do you need?" placeholder="For example: a pressure washer for a terrace and a car" />
      <label className="research-field">
        <span>Budget</span>
        <input type="text" inputMode="decimal" value={budget} onChange={e => setBudget(e.target.value)} aria-invalid={badBudget}
               placeholder="€, empty = I do not know" />
      </label>
      <div className="research-field">
        <span>Countries to look in</span>
        <CountryChips value={chosen} onChange={setCountries} />
      </div>
      <p className="muted">
        Costs at most ${estimate.toFixed(2)}: up to {settings.limits.searches} {settings.limits.searches === 1 ? 'search' : 'searches'} and{' '}
        {settings.limits.pages} shop {settings.limits.pages === 1 ? 'page' : 'pages'}. The limits are under Settings.
      </p>
      <div className="research-actions">
        <button type="button" className="primary" onClick={() => start.mutate()}
                disabled={start.isPending || !request.trim() || chosen.length === 0 || badBudget}>
          {start.isPending ? 'Preparing my questions…' : 'Ask my questions'}
        </button>
      </div>
      {start.isError && <p className="cap-error">{start.error.message}</p>}
    </>
  )
}

// CLAUDE> the tab chosen last stays chosen: a per-browser convenience, so storage may fail without harm
const TOOL_KEY = 'research-tool'
const storedTool = (): 'product' | 'summary' => {
  try { return localStorage.getItem(TOOL_KEY) === 'summary' ? 'summary' : 'product' } catch { return 'product' }
}

// CLAUDE> two tools under Research: a product or service to buy, or a subject to summarise from websites into Obsidian
function NewCard({ data, draft, onStarted }: { data: ResearchList; draft: Draft | null; onStarted: (r: Research) => void }) {
  const [tool, setTool] = useState<'product' | 'summary'>(draft?.tool ?? storedTool())
  const choose = (next: 'product' | 'summary') => {
    setTool(next)
    try { localStorage.setItem(TOOL_KEY, next) } catch { /* CLAUDE> no storage: the tab is not remembered */ }
  }
  return (
    <article className="habit-card research-new">
      <div className="research-switch" role="group" aria-label="What to do">
        <button type="button" aria-pressed={tool === 'product'} onClick={() => choose('product')}>Find a product or service</button>
        <button type="button" aria-pressed={tool === 'summary'} onClick={() => choose('summary')}>Summarise a subject</button>
      </div>
      {draft && <p className="research-note">The form holds the earlier request. Change what you want, then start.</p>}
      {tool === 'product'
        ? <NewResearch settings={data.settings} estimate={data.estimate} draft={draft?.tool === 'product' ? draft : null} onStarted={onStarted} />
        : <NewSummary data={data} draft={draft?.tool === 'summary' ? draft : null} onStarted={onStarted} />}
    </article>
  )
}

// CLAUDE> the most a summary of these websites costs: one search per site, up to 10 pages per site and each page named
// CLAUDE> without websites the web is searched: at most 3 searches the model writes
function summaryCost(lines: string[], data: ResearchList) {
  const limits = data.settings.limits
  const pages = lines.filter(line => /^https?:\/\/[^/]+\/./i.test(line.trim())).length
  const searches = Math.min(lines.length === 0 ? 3 : lines.length - pages, limits.searches)
  const read = Math.min(limits.pages, pages + 10 * searches)
  const cost = Math.min(searches * data.unit_costs.search + read * data.unit_costs.page + data.unit_costs.summary_steps, limits.cost)
  return { searches, read, cost }
}

const costParts = (cost: { searches: number; read: number }) => [
  cost.searches > 0 && `${cost.searches} ${cost.searches === 1 ? 'search' : 'searches'}`,
  `up to ${cost.read} ${cost.read === 1 ? 'page' : 'pages'}`,
].filter(Boolean).join(' and ')

function NewSummary({ data, draft, onStarted }: { data: ResearchList; draft: Draft | null; onStarted: (r: Research) => void }) {
  const settings = data.settings
  const [subject, setSubject] = useState(draft?.subject ?? '')
  const [sites, setSites] = useState(draft?.sites ?? '')
  const [spot, setSpot] = useState<Spot>(NEW_NOTE)
  const lines = sites.split('\n').filter(line => line.trim())
  const start = useMutation({ mutationFn: () => startSummary(subject.trim(), sites.split('\n'), spotTarget(spot)), onSuccess: onStarted })
  const cost = summaryCost(lines, data)
  const ready = subject.trim() !== '' && spotReady(spot)
  return (
    <>
      <h2>What do you want to know?</h2>
      <textarea className="research-text" rows={3} value={subject} onChange={e => setSubject(e.target.value)}
                aria-label="What do you want to know?" placeholder="For example: the rules for solar panels on a flat roof in Flanders" />
      <label className="research-field">
        <span>Websites, one per line (you may leave this empty)</span>
        <textarea className="research-text" rows={3} value={sites} onChange={e => setSites(e.target.value)} spellCheck={false}
                  placeholder={'Empty: the whole web is searched for your subject.\nOr a site to search, like vrt.be\nOr a page to read, like https://www.vrt.be/nl/nieuws/…'} />
        <small className="muted">
          {lines.length === 0 ? 'No websites: the whole web is searched for your subject.' : 'Only these websites are used.'}
        </small>
      </label>
      <div className="research-field">
        <span>Save to Obsidian</span>
        <SpotPicker settings={settings} value={spot} onChange={setSpot} titleHint="Empty: the summary's own title" />
      </div>
      <p className="muted">
        {`Costs at most ${usd(cost.cost)}: ${costParts(cost)}.`}
      </p>
      <div className="research-actions">
        <button type="button" className="primary" onClick={() => start.mutate()} disabled={start.isPending || !ready}>
          {start.isPending ? 'Starting…' : 'Make the summary'}
        </button>
      </div>
      {start.isError && <p className="cap-error">{start.error.message}</p>}
    </>
  )
}

function Open({ id, data, onChange, onDeleted, onAgain, onSettings }: {
  id: number; data: ResearchList; onChange: () => void; onDeleted: () => void; onAgain: (draft: Draft) => void; onSettings: () => void
}) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: ['research', id], queryFn: () => getResearch(id),
    refetchInterval: q => (q.state.data?.state === 'running' ? 1500 : false),
  })
  const keep = (r: Research) => { client.setQueryData(['research', id], r); onChange() }
  const remove = useMutation({ mutationFn: () => deleteResearch(id), onSuccess: onDeleted })
  if (query.isError) return <article className="habit-card"><p className="cap-error">{query.error.message}</p></article>
  if (!query.data) return null
  const r = query.data
  const finished = r.state === 'done' || r.state === 'stopped'
  const summary = r.kind === 'summary'
  const again = (): Draft => (summary
    ? { tool: 'summary', subject: r.request, sites: (r.plan ?? []).filter(item => item.kind !== 'web').map(item => item.site ?? item.url ?? '').join('\n') }
    : { tool: 'product', request: r.request, budget: r.budget, countries: r.countries })
  return (
    <>
      <header className="research-head">
        <div className="research-head-main">
          <TitleField r={r} onChange={keep} />
          {summary ? <SummaryFacts r={r} /> : (
            <dl className="research-facts-list">
              <div><dt>Kind</dt><dd>{KIND_NAMES[r.kind]}</dd></div>
              <div><dt>Budget</dt><dd>{r.budget != null ? euro(r.budget) : 'none set'}</dd></div>
              <div><dt>Countries</dt><dd>{r.countries.join(', ')}</dd></div>
              <div><dt>Started</dt><dd>{day(r.created)}</dd></div>
              <div><dt>Cost</dt><dd>{usd(r.cost_usd)}</dd></div>
            </dl>
          )}
          <details className="research-request">
            <summary>{summary ? 'Your subject' : 'Your request'}</summary>
            <p>{r.request}</p>
          </details>
        </div>
        <div className="research-head-actions">
          <div className="research-head-buttons">
            {r.state !== 'running' && (
              <button type="button" className="quiet" onClick={() => onAgain(again())}
                      title="A new form with this request, to change what cannot change here">Start again with changes</button>
            )}
            <button type="button" className="quiet danger" onClick={() => remove.mutate()} disabled={remove.isPending || r.state === 'running'}
                    title="Deletes it from this page. A note saved in Obsidian stays.">Delete</button>
          </div>
        </div>
      </header>
      {remove.isError && <p className="cap-error">{remove.error.message}</p>}
      {finished && <ObsidianCard r={r} settings={data.settings} onChange={keep} onSettings={onSettings} />}
      {r.state === 'questions' && <QuestionsCard r={r} onChange={keep} />}
      {r.state === 'requirements' && <RequirementsCard r={r} onChange={keep} />}
      {r.state === 'budget' && <BudgetCard r={r} onChange={keep} />}
      {r.state === 'running' && <RunningCard r={r} />}
      {r.state === 'waiting' && <FollowupsCard r={r} onChange={keep} />}
      {r.state === 'failed' && <FailedCard r={r} onChange={keep} />}
      {finished && <GoOnCard r={r} data={data} onChange={keep} />}
      {finished && (summary ? <SummaryView r={r} /> : <Advice r={r} onChange={keep} />)}
      {finished && summary && <AddSitesCard r={r} data={data} onChange={keep} />}
    </>
  )
}

function SummaryFacts({ r }: { r: Research }) {
  const plan = r.plan ?? []
  const sites = plan.filter(item => item.kind === 'site').length
  const pages = plan.filter(item => item.kind === 'page').length
  const web = plan.filter(item => item.kind === 'web').length
  const websites = [web && `web search (${web} ${web === 1 ? 'search' : 'searches'})`, sites && `${sites} ${sites === 1 ? 'site' : 'sites'}`,
                    pages && `${pages} ${pages === 1 ? 'page' : 'pages'}`].filter(Boolean).join(', ') || 'web search'
  return (
    <dl className="research-facts-list">
      <div><dt>Kind</dt><dd>Summary</dd></div>
      <div><dt>Websites</dt><dd title={plan.map(item => item.site ?? item.url ?? `Web search: ${item.query}`).join('\n')}>{websites}</dd></div>
      <div><dt>Started</dt><dd>{day(r.created)}</dd></div>
      <div><dt>Cost</dt><dd>{usd(r.cost_usd)}</dd></div>
    </dl>
  )
}

// CLAUDE> a research that stopped early can go on: the pages found but not read, the searches or sites not done yet
function GoOnCard({ r, data, onChange }: { r: Research; data: ResearchList; onChange: (r: Research) => void }) {
  const go = useMutation({ mutationFn: () => goOnResearch(r.id), onSuccess: onChange })
  const left = r.left ?? { pages: 0, searches: 0 }
  if (!r.stopped_text && !left.pages && !left.searches) return null
  const summary = r.kind === 'summary'
  const limits = data.settings.limits
  const parts = [
    left.pages > 0 && `${left.pages} ${summary ? '' : 'shop '}${left.pages === 1 ? 'page' : 'pages'} found ${left.pages === 1 ? 'is' : 'are'} not read yet`,
    left.searches > 0 && `${left.searches} ${summary ? 'site' : 'search'}${left.searches === 1 ? '' : summary ? 's' : 'es'} not done yet`,
  ].filter(Boolean)
  return (
    <article className="habit-card research-go-on" role="status">
      {r.stopped_text && <p>{r.stopped_text}</p>}
      {parts.length > 0 && (
        <>
          <p className="muted">{parts.join('; ')}.</p>
          <div className="research-actions">
            <button type="button" className="primary" onClick={() => go.mutate()} disabled={go.isPending}>
              {left.searches > 0 ? 'Go on' : `Read up to ${Math.min(left.pages, limits.pages)} more`}
            </button>
            <span className="muted">
              Costs at most {usd(r.go_on_estimate ?? 0)}.{' '}
              {summary ? 'Then the summary is written again.' : 'Then all products are scored again and the advice is written again.'}
            </span>
          </div>
        </>
      )}
      {go.isError && <p className="cap-error">{go.error.message}</p>}
    </article>
  )
}

// CLAUDE> more websites for a finished summary: only the new ones are searched or read, then the summary is written again
function AddSitesCard({ r, data, onChange }: { r: Research; data: ResearchList; onChange: (r: Research) => void }) {
  const [sites, setSites] = useState('')
  const lines = sites.split('\n').filter(line => line.trim())
  const add = useMutation({ mutationFn: () => addSummarySites(r.id, sites.split('\n')), onSuccess: moved => { setSites(''); onChange(moved) } })
  const cost = summaryCost(lines, data)
  return (
    <details className="habit-card research-add-sites">
      <summary>Add websites</summary>
      <textarea className="research-text" rows={3} value={sites} onChange={e => setSites(e.target.value)} spellCheck={false}
                aria-label="Websites to add, one per line"
                placeholder={'A site to search, like vrt.be\nOr a page to read, like https://www.vrt.be/nl/nieuws/…'} />
      <div className="research-actions">
        <button type="button" className="primary" onClick={() => add.mutate()} disabled={add.isPending || lines.length === 0}>
          {add.isPending ? 'Adding…' : 'Add and write the summary again'}
        </button>
        {lines.length > 0 && <span className="muted">Costs at most {usd(cost.cost)}. The pages read before are used again.</span>}
      </div>
      {add.isError && <p className="cap-error">{add.error.message}</p>}
    </details>
  )
}

// CLAUDE> the summary as the note has it: the text, the key points with numbered links to their pages, and the sources
function SummaryView({ r }: { r: Research }) {
  const result = r.result ?? {}
  const sources = result.sources ?? []
  const byNumber = new Map(sources.map(s => [s.n, s]))
  const paragraphs = result.paragraphs ?? []
  const groups = result.groups ?? []
  const unread = result.unread ?? []
  const off = result.off_subject ?? []
  const empty = result.pages_read || off.length ? 'None of the pages read is about the subject, so there is no summary.'
    : 'No page could be read, so there is no summary.'
  return (
    <>
      {result.note?.message && <p className="research-note" role="status">{result.note.message}</p>}
      <article className="habit-card summary-card">
        <h2>Summary</h2>
        {paragraphs.length > 0 ? paragraphs.map((p, i) => <p key={i}>{p}</p>) : <p className="muted">{empty}</p>}
      </article>
      {groups.length > 0 && (
        <article className="habit-card summary-card">
          <h2>Key points</h2>
          {groups.map((g, gi) => (
            <section key={gi} className="summary-group">
              <h3>{g.heading}</h3>
              <ul>
                {g.items.map((item, ii) => (
                  <li key={ii}>
                    {/* CLAUDE> the last word and the source numbers stay on one line: a number alone on a line looked lost */}
                    {item.text.slice(0, item.text.lastIndexOf(' ') + 1)}
                    <span className="summary-refs">
                      {item.text.slice(item.text.lastIndexOf(' ') + 1)}
                      {[...item.sources].sort((a, b) => a - b).map(n => byNumber.get(n) && (
                        <a key={n} className="summary-ref" href={byNumber.get(n)!.url} target="_blank" rel="noreferrer"
                           title={byNumber.get(n)!.title || byNumber.get(n)!.url} aria-label={`Source ${n}`}>{n}</a>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </article>
      )}
      {sources.length > 0 && (
        <article className="habit-card summary-card">
          <h2>Sources</h2>
          <ol className="summary-sources">
            {sources.map(s => (
              <li key={s.n} value={s.n}>
                <a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
                <span className="muted">{s.site}</span>
              </li>
            ))}
          </ol>
        </article>
      )}
      {unread.length + off.length > 0 && (
        <details className="habit-card">
          <summary>Pages not used ({unread.length + off.length})</summary>
          <ul className="research-list">
            {unread.map(u => <li key={u}><span className="muted">Not read: </span><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
            {off.map(u => <li key={u}><span className="muted">Not about the subject: </span><a href={u} target="_blank" rel="noreferrer">{u}</a></li>)}
          </ul>
        </details>
      )}
    </>
  )
}

// CLAUDE> the title is the research's name everywhere; click it to change it, Enter or leaving the field saves
function TitleField({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const shown = r.title || cut(r.request, 70)
  const [value, setValue] = useState(shown)
  const field = useRef<HTMLInputElement>(null)
  const save = useMutation({ mutationFn: () => renameResearch(r.id, value), onSuccess: onChange })
  return (
    <>
      <div className="research-title-row">
        <input ref={field} className="research-title" value={value} aria-label="Title of this research" title="Click to rename"
               onChange={e => setValue(e.target.value)}
               onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setValue(shown) }}
               onBlur={() => { if (value.trim() && value.trim() !== shown) save.mutate(); else setValue(shown) }} />
        <button type="button" className="research-rename" onClick={() => field.current?.select()} aria-label="Rename">✎</button>
      </div>
      {save.isError && <p className="cap-error">{save.error.message}</p>}
    </>
  )
}

// CLAUDE> a finished research is saved to Obsidian by itself when a vault is set; this says where, and saves again
// CLAUDE> saving to Obsidian in one place, chosen at each save: "Save again" writes the place saved last; "Save somewhere
// else" asks the place (a new note under a title, or a note the user has). A save never deletes what was saved before.
function ObsidianCard({ r, settings, onChange, onSettings }: {
  r: Research; settings: ResearchSettings; onChange: (r: Research) => void; onSettings: () => void
}) {
  const client = useQueryClient()
  const summary = r.kind === 'summary'
  const note = r.result?.note
  const saved = Boolean(note && note.path && !note.error)
  const last = spotOf(summary ? r.target : null, note?.title || r.title || cut(r.request, 70))
  const [open, setOpen] = useState(!saved)
  const [spot, setSpot] = useState<Spot>(last)
  const save = useMutation({
    mutationFn: (choice: { title?: string; target?: SummaryTarget }) => exportResearch(r.id, choice),
    onSuccess: () => {
      setOpen(false)
      return client.invalidateQueries({ queryKey: ['research', r.id] }).then(() => onChange(client.getQueryData(['research', r.id]) as Research))
    },
  })
  const ready = summary ? spotReady(spot) : spot.title.trim() !== ''
  const choice = summary ? { target: spotTarget(spot) } : { title: spot.title.trim() }
  const error = save.isError ? save.error.message : !save.isPending ? note?.error : ''
  return (
    <article className="habit-card obsidian-card">
      <h2>Save to Obsidian</h2>
      {saved && (
        <p>
          Saved in <a href={note!.obsidian_url} title={note!.path}>{nameOf(note!.path)}</a>
          {summary && last.note ? `, ${placeText(last)}` : ''}. Click the name to open it in Obsidian.
        </p>
      )}
      {note?.message && <p className="research-note" role="status">{note.message}</p>}
      {!open ? (
        <div className="research-actions">
          <button type="button" className="primary" onClick={() => save.mutate({})} disabled={save.isPending}>
            {save.isPending ? 'Saving…' : 'Save again'}
          </button>
          <button type="button" className="quiet" onClick={() => { setSpot(last); save.reset(); setOpen(true) }}>
            {summary ? 'Save somewhere else' : 'Save under another title'}
          </button>
        </div>
      ) : (
        <form className="obsidian-form" onSubmit={e => { e.preventDefault(); if (ready) save.mutate(choice) }}>
          <SpotPicker settings={settings} value={spot} onChange={setSpot} onlyNew={!summary} titleHint={r.title || ''} skip={r.id} />
          {saved && (
            <p className="muted">
              {summary ? 'What was saved before stays where it is. In a note that has this summary already, it goes to the place you choose.'
                : 'The note saved before stays. The same title writes that note again.'}
            </p>
          )}
          <div className="research-actions">
            <button type="submit" className="primary" disabled={save.isPending || !ready || !settings.vault}>{save.isPending ? 'Saving…' : 'Save'}</button>
            {saved && <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>}
          </div>
        </form>
      )}
      {error && <p className="cap-error">{error}</p>}
      {(!settings.vault || (error && /vault/i.test(error))) && <button type="button" className="link-button" onClick={onSettings}>Open Settings</button>}
    </article>
  )
}

// CLAUDE> more than one choice may fit ("Automatic float switch" or "Separate level sensor"): choices toggle, "Other" adds own
// text; a choice like "No preference" or "Not sure" stands alone, so it clears the others and the others clear it
const ALONE = /^(no preference|not sure|don.?t know|i do not know|any)\b/i
type Picks = { chosen: Record<string, string[]>; other: Record<string, string> }

function useAnswers() {
  const [picks, setPicks] = useState<Picks>({ chosen: {}, other: {} })
  const toggle = (id: string, choice: string) => setPicks(p => {
    const now = p.chosen[id] ?? []
    const next = now.includes(choice) ? now.filter(c => c !== choice)
      : ALONE.test(choice) ? [choice] : [...now.filter(c => !ALONE.test(c)), choice]
    return { ...p, chosen: { ...p.chosen, [id]: next } }
  })
  const write = (id: string, text: string) => setPicks(p => ({ ...p, other: { ...p.other, [id]: text } }))
  const given = (id: string) => [...(picks.chosen[id] ?? []), ...((picks.other[id] ?? '').trim() ? [picks.other[id].trim()] : [])]
  const payload = (questions: ResearchQuestion[]) => Object.fromEntries(questions.map(q => [q.id, given(q.id).join('; ')]))
  const complete = (questions: ResearchQuestion[]) => questions.every(q => given(q.id).length > 0)
  return { picks, toggle, write, payload, complete }
}

function Questions({ questions, answers }: { questions: ResearchQuestion[]; answers: ReturnType<typeof useAnswers> }) {
  return (
    <>
      <p className="muted">Choose every answer that fits; “Other” adds your own words.</p>
      <ol className="research-questions">
        {questions.map(q => {
          const chosen = answers.picks.chosen[q.id] ?? []
          const own = answers.picks.other[q.id] ?? ''
          return (
            <li key={q.id}>
              <p className="research-question">{q.text}</p>
              <div className="country-chips" role="group" aria-label={q.text}>
                {q.choices.map(c => (
                  <button key={c} type="button" className={`choice-chip${chosen.includes(c) ? ' chosen' : ''}`}
                          aria-pressed={chosen.includes(c)} onClick={() => answers.toggle(q.id, c)}>{c}</button>
                ))}
                <input type="text" className={`other-input${own.trim() ? ' chosen' : ''}`} placeholder="Other"
                       aria-label={`Other answer: ${q.text}`} value={own} onChange={e => answers.write(q.id, e.target.value)} />
              </div>
            </li>
          )
        })}
      </ol>
    </>
  )
}

function QuestionsCard({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const [kind, setKind] = useState<string>(r.kind)
  const answers = useAnswers()
  const questions = r.questions ?? []
  const send = useMutation({ mutationFn: () => answerResearch(r.id, answers.payload(questions), kind), onSuccess: onChange })
  const unknown = r.note.split('\n').filter(Boolean)
  return (
    <article className="habit-card">
      <h2>A few questions</h2>
      <div className="research-field">
        <span>You look for</span>
        <div className="country-chips" role="group" aria-label="You look for">
          <button type="button" className={`choice-chip${kind === 'product' ? ' chosen' : ''}`} aria-pressed={kind === 'product'}
                  onClick={() => setKind('product')}>A product to buy</button>
          <button type="button" className={`choice-chip${kind === 'service' ? ' chosen' : ''}`} aria-pressed={kind === 'service'}
                  onClick={() => setKind('service')}>A company that does the work</button>
        </div>
      </div>
      {unknown.length > 0 && (
        <div>
          <p className="muted">Not known yet:</p>
          <ul className="research-unknown">{unknown.map(u => <li key={u}>{u}</li>)}</ul>
        </div>
      )}
      <Questions questions={questions} answers={answers} />
      <div className="research-actions">
        <button type="button" className="primary" onClick={() => send.mutate()} disabled={send.isPending || !answers.complete(questions)}>
          {send.isPending ? 'Writing your requirements…' : 'Next: write my requirements'}
        </button>
      </div>
      {send.isError && <p className="cap-error">{send.error.message}</p>}
    </article>
  )
}

function BudgetCard({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const [amount, setAmount] = useState('')
  const send = useMutation({ mutationFn: (value: number) => setResearchBudget(r.id, value), onSuccess: onChange })
  const value = Number(amount.replace(',', '.'))
  const valid = amount.trim() !== '' && Number.isFinite(value) && value > 0
  return (
    <article className="habit-card">
      <h2>What is your budget?</h2>
      <p className="muted">Pick a price class, or type an amount.</p>
      <div className="research-classes">
        {(r.classes ?? []).map(c => (
          <button key={c.name} type="button" className="research-class" disabled={send.isPending} onClick={() => send.mutate(c.high)}>
            <strong>{c.name}</strong>
            <span>{euro(c.low)} to {euro(c.high)}</span>
            <span className="muted">{c.difference}</span>
          </button>
        ))}
      </div>
      <div className="research-actions">
        <input type="text" inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} placeholder="€ amount"
               aria-label="Budget amount" />
        <button type="button" className="primary" disabled={send.isPending || !valid} onClick={() => send.mutate(value)}>Use this budget</button>
      </div>
      {send.isError && <p className="cap-error">{send.error.message}</p>}
    </article>
  )
}

function RunningCard({ r }: { r: Research }) {
  const own = r.progress.id === r.id ? r.progress : {}
  const step = own.step ?? r.step
  const counted = (step === 'search' || step === 'read') && own.total ? ` (${own.done ?? 0} of ${own.total})` : ''
  return (
    <article className="habit-card" aria-live="polite">
      <h2>{`${(r.kind === 'summary' ? SUMMARY_STEP_NAMES : STEP_NAMES)[step] ?? 'Working'}${counted}…`}</h2>
      <p className="muted">Cost so far: ${(own.cost_usd ?? r.cost_usd).toFixed(2)}</p>
      {r.progress.id !== r.id && <p className="muted">Waiting for the research that runs now to finish.</p>}
      {/* CLAUDE> Stop works in the queue too: the research then stops as soon as its turn comes */}
      <div className="research-actions"><StopButton action={`research-${r.id}`} running /></div>
    </article>
  )
}

function FollowupsCard({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const answers = useAnswers()
  const questions = r.followups ?? []
  const send = useMutation({ mutationFn: () => replyResearch(r.id, answers.payload(questions)), onSuccess: onChange })
  return (
    <article className="habit-card">
      <h2>One more thing</h2>
      <Questions questions={questions} answers={answers} />
      <div className="research-actions">
        <button type="button" className="primary" onClick={() => send.mutate()} disabled={send.isPending || !answers.complete(questions)}>
          Continue the research
        </button>
      </div>
      {send.isError && <p className="cap-error">{send.error.message}</p>}
    </article>
  )
}

function FailedCard({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const send = useMutation({ mutationFn: () => continueResearch(r.id), onSuccess: onChange })
  return (
    <article className="habit-card">
      <p className="cap-error">{r.note || 'The research failed. Press Continue to try again.'}</p>
      <div className="research-actions">
        <button type="button" className="primary" onClick={() => send.mutate()} disabled={send.isPending}>Continue</button>
      </div>
      {send.isError && <p className="cap-error">{send.error.message}</p>}
    </article>
  )
}

const POINTS: Record<Weight, number> = { must: 3, important: 2, nice: 1 }

const pts = (value: number | undefined) => (value == null ? '—' : String(Math.round(value * 100) / 100))

// CLAUDE> the weighted requirements as an editable table: they hold for every product, so no product's answers here
function RequirementsTable({ items, onChange }: { items: ResearchRequirement[]; onChange: (items: ResearchRequirement[]) => void }) {
  const [added, setAdded] = useState('')
  const change = (index: number, update: Partial<ResearchRequirement>) =>
    onChange(items.map((item, n) => (n === index ? { ...item, ...update } : item)))
  const add = () => { if (added.trim()) { onChange([...items, { text: added.trim(), weight: 'important' }]); setAdded('') } }
  const total = items.reduce((sum, item) => sum + POINTS[item.weight], 0)
  return (
    <>
      <div className="habit-table-wrap">
        <table className="habit-table research-req-table">
          <thead>
            <tr><th>#</th><th>Requirement</th><th>Weight</th><th className="num">Points</th><th><span className="sr-only">Remove</span></th></tr>
          </thead>
          <tbody>
            {items.map((item, n) => (
              <tr key={item.id ?? `new-${n}`}>
                <td className="num">R{n + 1}</td>
                <td>
                  <textarea className="research-req-text" rows={1} value={item.text} aria-label={`Requirement R${n + 1}`}
                            onChange={e => change(n, { text: e.target.value.replace(/\n/g, ' ') })} />
                </td>
                <td>
                  <select value={item.weight} aria-label={`Weight of R${n + 1}`} onChange={e => change(n, { weight: e.target.value as Weight })}>
                    {WEIGHTS.map(w => <option key={w.value} value={w.value}>{w.label}</option>)}
                  </select>
                </td>
                <td className="num">{POINTS[item.weight]}</td>
                <td>
                  <button type="button" className="quiet" aria-label={`Remove R${n + 1}`}
                          onClick={() => onChange(items.filter((_, k) => k !== n))}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr><td /><td>Total</td><td /><td className="num">{total}</td><td /></tr></tfoot>
        </table>
      </div>
      <div className="research-actions">
        <input type="text" value={added} onChange={e => setAdded(e.target.value)} placeholder="Add a requirement"
               aria-label="New requirement" onKeyDown={e => { if (e.key === 'Enter') add() }} />
        <button type="button" className="quiet" onClick={add} disabled={!added.trim()}>Add</button>
      </div>
      <p className="muted research-points-help">
        Points per requirement: Must 3, Important 2, Nice to have 1. A product earns all of them when its page shows it meets
        the requirement, half when partly, a quarter when its page does not say, none when it does not meet it. Its score is
        the points earned out of {total}. A product that does not meet a Must is never recommended.
      </p>
    </>
  )
}

// CLAUDE> one product's answer on one requirement: the sign and the points earned
function Earned({ e, id }: { e: RankedProduct; id: string }) {
  const verdict = e.checks[id] ?? 'unknown'
  return (
    <span className={`check-${verdict}`} title={[MARKS[verdict].text, e.notes?.[id]].filter(Boolean).join(': ')}>
      <span className="check-sign" aria-hidden="true">{MARKS[verdict].sign}</span> {pts(e.points?.[id])}
    </span>
  )
}

function RequirementsCard({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const [items, setItems] = useState<ResearchRequirement[]>(r.requirements ?? [])
  const send = useMutation({ mutationFn: () => confirmRequirements(r.id, items), onSuccess: onChange })
  const cancel = useMutation({ mutationFn: () => cancelRequirements(r.id), onSuccess: onChange })
  const again = r.pages.some(p => p.status === 'read')
  // CLAUDE> a research scored before can go back to its result unchanged
  const scoredBefore = Boolean(r.result?.summary)
  return (
    <article className="habit-card">
      <h2>Your requirements</h2>
      <p className="muted">Every product found is scored against these. Change the text or the weight, remove or add requirements.</p>
      <RequirementsTable items={items} onChange={setItems} />
      <div className="research-actions">
        <button type="button" className="primary" onClick={() => send.mutate()}
                disabled={send.isPending || !items.some(i => i.text.trim())}>
          {again ? 'Score the products again' : 'Start the search'}
        </button>
        {scoredBefore && (
          <button type="button" className="quiet" onClick={() => cancel.mutate()} disabled={cancel.isPending}>Cancel</button>
        )}
        {again && <span className="muted">Uses the shop pages already read: no new search.</span>}
      </div>
      {send.isError && <p className="cap-error">{send.error.message}</p>}
      {cancel.isError && <p className="cap-error">{cancel.error.message}</p>}
    </article>
  )
}

function ResultRequirements({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const kept = r.result?.requirements ?? r.requirements ?? []
  const [items, setItems] = useState<ResearchRequirement[]>(kept)
  const changed = JSON.stringify(items.map(i => [i.text.trim(), i.weight])) !== JSON.stringify(kept.map(i => [i.text, i.weight]))
  const rescore = useMutation({ mutationFn: () => rescoreResearch(r.id, items), onSuccess: onChange })
  const read = r.pages.some(p => p.status === 'read')
  return (
    <article className="habit-card">
      <h2>Your requirements</h2>
      <RequirementsTable items={items} onChange={setItems} />
      {read && (
        <div className="research-actions">
          <button type="button" className="primary" onClick={() => rescore.mutate()}
                  disabled={!changed || rescore.isPending || !items.some(i => i.text.trim())}>Score again with these</button>
          {changed && <button type="button" className="quiet" onClick={() => setItems(kept)}>Undo my changes</button>}
          <span className="muted">Uses the shop pages already read: no new search, about $0.005.</span>
        </div>
      )}
      {rescore.isError && <p className="cap-error">{rescore.error.message}</p>}
    </article>
  )
}

// CLAUDE> what a person can tell a product by: its title with brand and model, linked to the cheapest shop
function ProductLink({ e }: { e: RankedProduct }) {
  return e.url ? <a href={e.url} target="_blank" rel="noreferrer">{e.title}</a> : <>{e.title}</>
}

function price(e: RankedProduct, kind: string) {
  if (e.total == null) return kind === 'service' ? 'not on the website' : 'not found'
  return euro(e.total)
}

function Recommendation({ r, best }: { r: Research; best: RankedProduct }) {
  const result = r.result ?? {}
  const reqs = result.requirements ?? []
  return (
    <article className="habit-card research-best">
      <div className="research-best-head">
        <div>
          <h2>Recommendation</h2>
          <p className="research-name"><ProductLink e={best} /></p>
          <p className="research-price">
            <strong>{price(best, r.kind)}</strong>
            {best.total != null && <span className="muted">{best.delivery_known ? 'with delivery' : 'delivery cost not shown'}</span>}
            {best.shop && <span className="muted">at {best.shop}{best.shops > 1 ? ` and ${best.shops - 1} other shop(s)` : ''}</span>}
          </p>
        </div>
        {/* CLAUDE> the one large element of the page: how well the recommendation meets the requirements */}
        <div className="research-score-big" aria-label={`Score ${best.score} of 100`}>
          <span className="research-score-number">{best.score}</span>
          <span className="research-score-of">of 100</span>
          <span className="research-score-points">{pts(best.points_total)} of {best.points_max ?? '—'} points</span>
        </div>
      </div>
      {!best.price_seen && <p className="muted">This price comes from the shop’s product data; the page itself did not show it. Check it on the shop page.</p>}
      {best.original && (
        <p className="muted">
          The shop shows {best.original.currency} {best.original.total.toFixed(2)}. This is that amount in euros at the European
          Central Bank rate of {day(best.original.date)}, plus 21% Belgian import VAT. Customs duties and fees are not included.
        </p>
      )}
      {r.kind === 'service' && (best.region || best.reviews || best.contact) && (
        <p className="muted">{[best.region, best.reviews && `reviews ${best.reviews}`, best.contact].filter(Boolean).join(' · ')}</p>
      )}
      {result.summary && <p>{result.summary}</p>}
      {result.reasons && <p>{result.reasons}</p>}
      {reqs.length > 0 && (
        <div className="habit-table-wrap">
          <table className="habit-table research-breakdown">
            <thead>
              <tr><th>#</th><th>Requirement</th><th>Weight</th><th>Does it meet it?</th><th>What its page shows</th><th className="num">Points</th></tr>
            </thead>
            <tbody>
              {reqs.map((q, n) => {
                const verdict = best.checks[q.id ?? ''] ?? 'unknown'
                return (
                  <tr key={q.id}>
                    <td className="num">R{n + 1}</td><td>{q.text}</td><td>{WEIGHTS.find(w => w.value === q.weight)?.label}</td>
                    <td className={`check-${verdict}`}><span className="check-sign" aria-hidden="true">{MARKS[verdict].sign}</span> {MARKS[verdict].text}</td>
                    <td className="muted">{best.notes?.[q.id ?? ''] || '—'}</td>
                    <td className="num">{pts(best.points?.[q.id ?? ''])} of {POINTS[q.weight]}</td>
                  </tr>
                )
              })}
            </tbody>
            <tfoot>
              <tr><td /><td colSpan={4}>Total: {pts(best.points_total)} of {best.points_max ?? '—'} points = score {best.score}/100</td><td /></tr>
            </tfoot>
          </table>
        </div>
      )}
      {(best.pros.length > 0 || best.cons.length > 0) && (
        <div className="research-proscons">
          <div><h3>Pros</h3><ul className="research-list">{best.pros.map(x => <li key={x}>{x}</li>)}</ul></div>
          <div><h3>Cons</h3><ul className="research-list">{best.cons.map(x => <li key={x}>{x}</li>)}</ul></div>
        </div>
      )}
    </article>
  )
}

function Ranking({ r, ranking }: { r: Research; ranking: RankedProduct[] }) {
  const service = r.kind === 'service'
  const reqs = r.result?.requirements ?? []
  return (
    <article className="habit-card">
      <h2>All {service ? 'companies' : 'products'} compared</h2>
      <p className="muted">
        Products that meet every Must first, then the highest score. R1, R2 … are your requirements above: each cell shows the
        answer (✓ yes, ½ partly, ? its page does not say, ✗ no) and the points earned. Point at a cell for the reason.
      </p>
      <div className="habit-table-wrap">
        <table className="habit-table research-rank">
          <thead>
            <tr>
              <th>{service ? 'Company' : 'Product'}</th>
              <th>Price</th>
              {reqs.map((q, n) => <th key={q.id} className="num" title={`${q.text} (${q.weight}, ${POINTS[q.weight]} points)`}>R{n + 1}</th>)}
              <th className="num">Score <small className="muted">points</small></th><th>Pros</th><th>Cons</th>
            </tr>
          </thead>
          <tbody>
            {ranking.map(e => (
              <tr key={e.n} className={e.tag === 'recommended' ? 'rank-best' : e.fails.length > 0 ? 'paused' : ''}>
                <td>
                  <ProductLink e={e} />
                  {e.tag && <span className={`rank-tag tag-${e.tag.replace(/ /g, '-')}`}>{e.tag}</span>}
                  {service && (e.region || e.reviews || e.contact) && (
                    <small className="muted">{[e.region, e.reviews && `reviews ${e.reviews}`, e.contact].filter(Boolean).join(' · ')}</small>
                  )}
                </td>
                <td className="num">
                  {price(e, r.kind)}
                  {e.total != null && !e.price_seen && <small className="muted">not seen on the page</small>}
                  {e.original && <small className="muted">from {e.original.currency} {e.original.total.toFixed(2)} + VAT</small>}
                </td>
                {reqs.map(q => <td key={q.id} className="num"><Earned e={e} id={q.id ?? ''} /></td>)}
                <td className="num"><strong>{e.score}</strong><small className="muted">{pts(e.points_total)} of {e.points_max ?? '—'}</small></td>
                <td><ul className="rank-points">{e.pros.map(x => <li key={x} className="pro">{x}</li>)}</ul></td>
                {/* CLAUDE> why it is not the recommendation is its first con, short ("Not confirmed: R3, R4"), not a line of its own */}
                <td>
                  <ul className="rank-points">
                    {e.tag !== 'recommended' && e.why_not.split('. ').filter(Boolean).map(x => <li key={`why-${x}`} className="con">{x}</li>)}
                    {e.cons.map(x => <li key={x} className="con">{x}</li>)}
                  </ul>
                </td>

              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  )
}

function Advice({ r, onChange }: { r: Research; onChange: (r: Research) => void }) {
  const result = r.result ?? {}
  const ranking = result.ranking
  const best = result.best && 'tag' in result.best ? result.best : null
  const noPrice = r.kind === 'product' ? result.comparison?.no_price ?? [] : []
  return (
    <>
      {!ranking && (
        <article className="habit-card">
          <p>This research was made before the scores existed. Add your requirements in the table below and press “Score again with these”.</p>
        </article>
      )}
      <ResultRequirements key={JSON.stringify(result.requirements ?? r.requirements)} r={r} onChange={onChange} />
      {ranking && (best ? <Recommendation r={r} best={best} /> : (
        <article className="habit-card"><p>{result.summary || 'No recommendation could be made from what was found.'}</p></article>
      ))}
      {ranking && ranking.length > 0 && <Ranking r={r} ranking={ranking} />}
      {noPrice.length > 0 && (
        <details className="habit-card">
          <summary>Products without a price ({noPrice.length})</summary>
          <p className="muted">The shop page showed no price, or only a price without VAT. They are not scored.</p>
          <ul className="research-list">
            {noPrice.map(p => (
              <li key={p.n}>
                {p.offers[0]?.url ? <a href={p.offers[0].url} target="_blank" rel="noreferrer">{p.title ?? p.name}</a> : p.title ?? p.name}
                {p.offers[0]?.shop && <span className="muted"> · {p.offers[0].shop}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {(result.risks?.length ?? 0) > 0 && (
        <article className="habit-card">
          <h2>Risks and points to check</h2>
          <ul className="research-list">{result.risks!.map(x => <li key={x}>{x}</li>)}</ul>
        </article>
      )}
      {(result.unread?.length ?? 0) > 0 && (
        <details className="habit-card">
          <summary>Shop pages that could not be read ({result.unread!.length})</summary>
          <ul className="research-list">{result.unread!.map(x => <li key={x}><a href={x} target="_blank" rel="noreferrer">{x}</a></li>)}</ul>
        </details>
      )}
    </>
  )
}

function SettingsPanel({ data, onSaved, onClose }: { data: ResearchList; onSaved: () => void; onClose: () => void }) {
  const settings: ResearchSettings = data.settings
  const [countries, setCountries] = useState(settings.countries)
  const [vault, setVault] = useState(settings.vault)
  const [subdir, setSubdir] = useState(settings.subdir)
  const [municipality, setMunicipality] = useState(settings.municipality)
  const [limits, setLimits] = useState({ searches: String(settings.limits.searches), pages: String(settings.limits.pages), cost: String(settings.limits.cost) })
  // CLAUDE> searches and pages are whole numbers; the server refuses fractions
  const parsed = {
    searches: Math.round(Number(limits.searches)), pages: Math.round(Number(limits.pages)), cost: Number(limits.cost.replace(',', '.')),
  }
  const valid = countries.length > 0 && Object.values(parsed).every(v => Number.isFinite(v) && v > 0)
  const save = useMutation({
    mutationFn: () => saveResearchSettings({ countries, municipality: municipality.trim(), limits: parsed, vault: vault.trim(), subdir: subdir.trim() }),
    onSuccess: () => { onSaved(); onClose() },
  })
  const field = (key: keyof typeof limits, label: string) => (
    <label className="research-field"><span>{label}</span>
      <input type="text" inputMode="decimal" value={limits[key]} onChange={e => setLimits(l => ({ ...l, [key]: e.target.value }))} />
    </label>
  )
  return (
    // CLAUDE> a panel from the right, over the page: settings are seldom changed, so they stay out of the way. They are the
    // defaults for new research, grouped by the tool they apply to: the summary's own place shows on the summary's page.
    <div className="research-drawer-backdrop" onClick={onClose}>
      <aside className="research-drawer" role="dialog" aria-modal="true" aria-labelledby="research-settings-title"
             onClick={e => e.stopPropagation()} onKeyDown={e => { if (e.key === 'Escape') onClose() }}>
        <header className="research-drawer-head">
          <h2 id="research-settings-title">Settings</h2>
          <button type="button" className="quiet" onClick={onClose} aria-label="Close the settings">Close</button>
        </header>
        <p className="muted">These apply to research and summaries you start from now on.</p>
        <section className="research-settings-group" aria-labelledby="settings-obsidian">
          <h3 id="settings-obsidian">Saving to Obsidian</h3>
          <div className="research-field"><span>Vault</span>
            {data.vaults.length > 0 && (
              <div className="country-chips" role="group" aria-label="Vaults Obsidian knows">
                {data.vaults.map(v => (
                  <button key={v.path} type="button" className={`choice-chip${vault === v.path ? ' chosen' : ''}`} aria-pressed={vault === v.path}
                          onClick={() => setVault(v.path)}>{v.name}</button>
                ))}
              </div>
            )}
            <input type="text" value={vault} onChange={e => setVault(e.target.value)} placeholder="/path/to/your/vault"
                   aria-label="Obsidian vault folder" />
          </div>
          <label className="research-field"><span>Folder for new notes</span>
            <input type="text" value={subdir} onChange={e => setSubdir(e.target.value)} placeholder="Research" />
            <small className="muted">Each research gets a note here, and each summary unless you choose a note for it.</small>
          </label>
        </section>
        <section className="research-settings-group" aria-labelledby="settings-limits">
          <h3 id="settings-limits">Limits for each run</h3>
          <div className="research-limits">{field('searches', 'Searches')}{field('pages', 'Pages read')}{field('cost', 'Cost ($)')}</div>
          <small className="muted">
            A run stops at the first limit it reaches; on its page you can go on. A summary does one search per site.
          </small>
        </section>
        <section className="research-settings-group" aria-labelledby="settings-products">
          <h3 id="settings-products">Products and services</h3>
          <div className="research-field"><span>Countries to look in</span><CountryChips value={countries} onChange={setCountries} /></div>
          <label className="research-field"><span>Your municipality</span>
            <input type="text" value={municipality} onChange={e => setMunicipality(e.target.value)} />
            <small className="muted">For a company that does the work nearby. Only the municipality goes to the search.</small>
          </label>
        </section>
        <div className="research-actions">
          <button type="button" className="primary" onClick={() => save.mutate()} disabled={save.isPending || !valid}>Save settings</button>
          <button type="button" className="quiet" onClick={onClose}>Cancel</button>
        </div>
        {save.isError && <p className="cap-error">{save.error.message}</p>}
      </aside>
    </div>
  )
}

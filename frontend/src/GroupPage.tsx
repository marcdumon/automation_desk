import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { ApiError, adjust, execute, getJobs, interpret, login, uploadFile, type Upload, type Group, type Interpretation, type JobSummary } from './api'
import { servePageRequests } from './capture'
import ChangeSheet from './ChangeSheet'
import ExtensionSetup from './ExtensionSetup'
import JobCost from './JobCost'
import JobDetailPanel from './JobDetailPanel'
import JobTable from './JobTable'
import NewsPanel from './NewsPanel'

export type LogEntry = { when: string; command: string; lines: string[]; job: JobSummary }

type Props = { group: Group; log: LogEntry[]; onLog: (entry: LogEntry) => void }

export default function GroupPage({ group, log, onLog }: Props) {
  const queryClient = useQueryClient()
  // CLAUDE> a sentence kept across 'Reload and try again' is picked up once, then forgotten
  const [text, setText] = useState(() => takeKeptSentence(group.id))
  const [taskId, setTaskId] = useState<string | null>(null)
  const [files, setFiles] = useState<Upload[]>([])
  const [uploading, setUploading] = useState('')
  const [uploadError, setUploadError] = useState('')
  const picker = useRef<HTMLInputElement>(null)
  const attach = async (chosen: FileList | null) => {
    for (const file of Array.from(chosen ?? [])) {
      setUploading(file.name)
      try {
        const up = await uploadFile(file)
        setFiles(current => [...current.filter(f => f.id !== up.id), up])
      } catch (err) {
        setUploadError((err as Error).message)
      }
    }
    setUploading('')
  }
  const [result, setResult] = useState<Interpretation | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [command, setCommand] = useState('')
  const box = useRef<HTMLTextAreaElement>(null)
  const [openJob, setOpenJob] = useState<string | null>(null)
  // CLAUDE> only this group's jobs are fetched for this page
  const groupJobs = useQuery({ queryKey: ['jobs', group.id], queryFn: () => getJobs(group.id) })
  const refreshJobs = () => queryClient.invalidateQueries({ queryKey: ['jobs'] })

  const ask = useMutation({
    mutationFn: () => interpret(group.id, text, taskId, files.map(f => f.id)),
    onSuccess: data => {
      setResult(data)
      setCommand(text)
      setSelected(new Set(data.preview?.rows.filter(r => r.selectable && r.selected).map(r => r.id) ?? []))
    },
    onError: () => setResult(null),
    onSettled: refreshJobs,
  })

  const tune = useMutation({
    mutationFn: (options: Record<string, string>) => adjust(group.id, result!.plan_id!, options),
    onSuccess: data => {
      setResult(current => ({ ...current!, ...data, arguments: current?.arguments ?? null }))
      // CLAUDE> keep the user's ticks; only rows that can no longer be applied drop out
      setSelected(current => new Set(data.preview?.rows.filter(r => r.selectable && current.has(r.id)).map(r => r.id) ?? []))
    },
    onSettled: refreshJobs,
  })

  const run = useMutation({
    mutationFn: () => execute(group.id, result!.plan_id!, [...selected]),
    onSuccess: data => {
      onLog({ when: new Date().toLocaleTimeString(), command, lines: data.results, job: data.job })
      setResult(null)
      setText('')
      setFiles([])
    },
    onSettled: refreshJobs,
  })

  // CLAUDE> only while a preview is being built can the app need a page read through this browser
  const [readingInBrowser, setReadingInBrowser] = useState(false)
  useEffect(() => {
    if (!ask.isPending) {
      setReadingInBrowser(false)
      return undefined
    }
    return servePageRequests(setReadingInBrowser)
  }, [ask.isPending])

  const signIn = useMutation({ mutationFn: login, onSuccess: () => queryClient.invalidateQueries({ queryKey: ['auth'] }) })

  const pick = (id: string, example: string) => {
    const next = taskId === id ? null : id
    setTaskId(next)
    if (next && !text.trim()) setText(example)
    box.current?.focus()
  }

  const submit = () => {
    if (!text.trim() || ask.isPending) return
    run.reset()
    ask.mutate()
  }

  const chosen = group.tasks.find(t => t.id === taskId)
  const error = (ask.error ?? tune.error ?? run.error) as ApiError | null

  return (
    <section className={`page accent-${group.id}`} aria-labelledby="page-title">
      <header className="page-head">
        <h1 id="page-title">{group.name}</h1>
      </header>

      {/* CLAUDE> News is run from its own page controls; other groups work through sentences */}
      {group.id === 'news' ? <NewsPanel /> : group.tasks.length === 0 ? (
        <p className="empty">{group.name} has no standard tasks yet. Describe the one you want and it can be added.</p>
      ) : (
        <>
          <form className="command" onSubmit={event => { event.preventDefault(); submit() }}
                onDragOver={event => { if (group.accepts_files) event.preventDefault() }}
                onDrop={event => { if (group.accepts_files) { event.preventDefault(); attach(event.dataTransfer.files) } }}>
            <label htmlFor="command-box" className="command-label">
              {chosen ? chosen.name : `Tell ${group.name} what to do`}
            </label>
            <textarea
              id="command-box"
              ref={box}
              rows={2}
              value={text}
              placeholder={chosen?.example ?? group.tasks[0].example}
              onChange={event => setText(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() }
              }}
            />
            {group.accepts_files && (files.length > 0 || uploading || uploadError) && (
              <div className="attachments">
                {files.map(f => (
                  <span key={f.id} className="chip">
                    {f.name} <span className="muted">({Math.max(1, Math.round(f.size / 1024))} KB)</span>
                    <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter(x => x.id !== f.id))}>×</button>
                  </span>
                ))}
                {uploading && <span className="muted">Attaching {uploading}…</span>}
                {uploadError && <span className="bad">{uploadError}</span>}
              </div>
            )}
            <div className="command-actions">
              {group.accepts_files && (
                <>
                  <input ref={picker} type="file" accept="application/pdf,.pdf" multiple hidden
                         onChange={event => { setUploadError(''); attach(event.target.files); event.target.value = '' }} />
                  <button type="button" className="quiet" onClick={() => picker.current?.click()}>Attach PDF</button>
                </>
              )}
              <span className="hint">
                {chosen ? 'Standard task selected. Click it again to let the app choose.' : 'The app picks the matching standard task.'}
              </span>
              <button type="submit" className="primary" disabled={!text.trim() || ask.isPending}>
                {ask.isPending ? 'Reading…' : 'Show changes'}
              </button>
            </div>
          </form>

          {ask.isPending && readingInBrowser && (
            <div className="message" role="status">
              <p>Working on it. If a tab opens with a site's "verify you are human" check, complete it there; the tab closes and the app continues by itself.</p>
            </div>
          )}

          {error?.setup === 'extension' && (
            <ExtensionSetup message={error.message} onReload={() => { keepSentence(group.id, text); window.location.reload() }} />
          )}

          {error && error.setup !== 'extension' && (
            <div className="message error" role="alert">
              <p>{error.message}</p>
              {error.needsLogin && (
                <button type="button" className="quiet" onClick={() => signIn.mutate()} disabled={signIn.isPending}>
                  {signIn.isPending ? 'Waiting for the browser…' : 'Log in to Google'}
                </button>
              )}
            </div>
          )}

          {result && result.status !== 'preview' && (
            <div className="message" role="status">
              <p>{result.status === 'clarify' ? result.message : `Not something ${group.name} can do here: ${result.message}`}</p>
              {result.job && <JobCost job={result.job} onOpen={setOpenJob} />}
            </div>
          )}

          {result?.status === 'preview' && result.preview && (
            <ChangeSheet
              key={JSON.stringify([result.preview.options, result.preview.rows.map(r => r.inputs)])}
              taskName={result.task_name ?? ''}
              preview={result.preview}
              selected={selected}
              onToggle={id => setSelected(current => {
                const next = new Set(current)
                if (next.has(id)) next.delete(id)
                else next.add(id)
                return next
              })}
              onToggleAll={all => setSelected(new Set(all ? result.preview!.rows.filter(r => r.selectable).map(r => r.id) : []))}
              onConfirm={() => run.mutate()}
              onCancel={() => setResult(null)}
              busy={run.isPending}
              cost={result.job && <JobCost job={result.job} onOpen={setOpenJob} />}
              onAdjust={options => tune.mutate(options)}
              adjusting={tune.isPending}
            />
          )}

          <div className="tasks">
            <h2>Standard tasks</h2>
            <ul>
              {group.tasks.map(task => (
                <li key={task.id}>
                  <button type="button" className={`task${task.id === taskId ? ' chosen' : ''}`}
                          aria-pressed={task.id === taskId} onClick={() => pick(task.id, task.example)}>
                    <span className="task-name">{task.name}</span>
                    <span className="task-desc">{task.description}</span>
                    <span className="task-example">“{task.example}”</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}

      {log.length > 0 && (
        <div className="log">
          <h2>Done in {group.name}</h2>
          <ol>
            {log.map((entry, index) => (
              <li key={index}>
                <div className="log-head"><time>{entry.when}</time><span>{entry.command}</span></div>
                <ul>{entry.lines.map((line, i) => <li key={i}>{line}</li>)}</ul>
                <JobCost job={entry.job} onOpen={setOpenJob} />
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="group-jobs">
        <h2>Jobs in {group.name}</h2>
        {groupJobs.data && <JobTable jobs={groupJobs.data.jobs} showGroup={false} onOpen={setOpenJob} />}
      </div>

      {openJob && <JobDetailPanel id={openJob} onClose={() => setOpenJob(null)} />}
    </section>
  )
}

const KEPT = 'automation-desk:kept-sentence:'

function keepSentence(group: string, sentence: string) {
  try { sessionStorage.setItem(KEPT + group, sentence) } catch { /* CLAUDE> storage blocked: the sentence is simply not kept */ }
}

function takeKeptSentence(group: string): string {
  try {
    const kept = sessionStorage.getItem(KEPT + group) ?? ''
    sessionStorage.removeItem(KEPT + group)
    return kept
  } catch {
    return ''
  }
}

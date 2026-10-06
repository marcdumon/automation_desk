import { keepPreviousData, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { memo, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useLocation, useRoute } from 'wouter'

import {
  anotherAnswer, askChat, changeChat, chooseAnswer, deleteChat, exportChat, findModels, followChat, getChat, getChats, startChat, stopChat,
  type Chat, type ChatList, type ChatMessage, type ChatModel,
} from './api'
import { nameOf, placeText, spotOf, SpotPicker, spotReady, spotTarget, type NoteSettings, type Spot } from './NoteSave'

// CLAUDE> a chat with an OpenRouter model of the user's choice: the answer shows word by word, another model can give a
// second opinion next to it, and the chat can be saved to Obsidian. Every answer shows what it cost.

const day = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
// CLAUDE> an answer can cost a hundredth of a cent: '$0.00' looked free
const usd = (value: number) => (value === 0 || value >= 0.01 ? `$${value.toFixed(2)}` : value >= 0.001 ? `$${value.toFixed(3)}`
  : `$${value.toFixed(4)}`)
const shortName = (id: string) => id.slice(id.indexOf('/') + 1)
// CLAUDE> what web search adds with this model: the search fee and the page text it reads; the fee alone for a model
// without a known price
const webExtra = (data: ChatList, model: ChatModel | undefined) => model?.web_extra ?? data.web_extra
const perAnswer = (m: ChatModel) => (m.per_answer == null ? 'price not known' : `${m.measured ? '' : '≈ '}${usd(m.per_answer)} an answer`)

// CLAUDE> the answer being written in each chat, kept outside the page: moving to another page and back neither stops nor
// loses it. Each chat has one reader at most.
type LiveAnswer = { messageId: number; text: string; wait: number }
const live = new Map<number, LiveAnswer>()
const listeners = new Set<() => void>()
const reading = new Set<number>()
let frame = 0

function tell() {
  // CLAUDE> pieces come many times a second: the page draws them once per screen refresh
  if (frame) return
  frame = requestAnimationFrame(() => { frame = 0; listeners.forEach(listener => listener()) })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function useLive(chatId: number) {
  return useSyncExternalStore(subscribe, () => live.get(chatId))
}

function putMessage(client: QueryClient, chatId: number, message: ChatMessage, writing: boolean) {
  client.setQueryData<Chat>(['chat', chatId], chat => chat && {
    ...chat, writing,
    messages: chat.messages.some(m => m.id === message.id)
      ? chat.messages.map(m => (m.id === message.id ? message : m)) : [...chat.messages, message],
  })
}

function follow(chatId: number, client: QueryClient) {
  if (reading.has(chatId)) return
  reading.add(chatId)
  followChat(chatId, event => {
    const now = live.get(chatId)
    if (event.type === 'start') live.set(chatId, { messageId: event.message.id, text: '', wait: 0 })
    else if (event.type === 'piece' && now) live.set(chatId, { ...now, text: now.text + event.text, wait: 0 })
    else if (event.type === 'wait' && now) live.set(chatId, { ...now, wait: event.seconds })
    else if (event.type === 'end') {
      putMessage(client, chatId, event.message, false)
      live.delete(chatId)
    }
    tell()
  }).finally(() => {
    reading.delete(chatId)
    live.delete(chatId)
    tell()
    client.invalidateQueries({ queryKey: ['chat', chatId] })
    client.invalidateQueries({ queryKey: ['chats'] })
  })
}

export default function ChatPage() {
  const [, navigate] = useLocation()
  const [match, params] = useRoute('/chat/:id')
  const openId = match ? Number(params.id) : null
  const list = useQuery({ queryKey: ['chats'], queryFn: getChats })
  if (list.isError) return <section className="page accent-chat"><p className="cap-error">{list.error.message}</p></section>
  if (!list.data) return null
  return (
    <section className="page accent-chat chat-page">
      <header className="research-top">
        {openId === null
          ? <h1>Chat</h1>
          : <button type="button" className="research-back" onClick={() => navigate('/chat')}>← All chats</button>}
      </header>
      {openId === null ? <ChatStart data={list.data} /> : <ChatView key={openId} id={openId} data={list.data} />}
    </section>
  )
}

function ChatStart({ data }: { data: ChatList }) {
  const client = useQueryClient()
  const [, navigate] = useLocation()
  const [model, setModel] = useState(data.new_model)
  const [info, setInfo] = useState<ChatModel | undefined>(data.new_model_info)
  const [web, setWeb] = useState(false)
  const start = useMutation({
    mutationFn: (text: string) => startChat(text, model, web),
    onSuccess: chat => {
      client.setQueryData(['chat', chat.id], chat)
      client.invalidateQueries({ queryKey: ['chats'] })
      navigate(`/chat/${chat.id}`)
    },
  })
  const remove = useMutation({ mutationFn: deleteChat, onSuccess: () => client.invalidateQueries({ queryKey: ['chats'] }) })
  return (
    <>
      <Composer label="Ask a question" placeholder="For example: propose a supplement regime for a 65 year old man" busy={start.isPending}
                web={web} onWeb={setWeb} webExtra={webExtra(data, info)} onSend={text => start.mutate(text)} autoFocus
                error={start.isError ? start.error.message : ''}>
        <ModelPicker value={model} models={data.models} known={info} onChange={(id, chosen) => { setModel(id); setInfo(chosen) }} />
      </Composer>
      <section className="chat-overview" aria-label="Your chats">
        <h2>Your chats</h2>
        {data.chats.length === 0 ? <p className="muted">Your chats show here once you ask a question.</p> : (
          <ul className="chat-list">
            {data.chats.map(c => (
              <li key={c.id}>
                <button type="button" className="chat-row" onClick={() => navigate(`/chat/${c.id}`)}>
                  <span className="chat-row-title">{c.title}</span>
                  <span className="chat-row-meta">
                    <span title={c.model}>{shortName(c.model)}</span>
                    <span>{day(c.updated)}</span>
                    <span>{c.questions} {c.questions === 1 ? 'question' : 'questions'}</span>
                    <span>{usd(c.cost_usd)}</span>
                  </span>
                </button>
                <button type="button" className="quiet chat-delete" onClick={() => remove.mutate(c.id)} disabled={remove.isPending}
                        aria-label={`Delete the chat “${c.title}”`}>Delete</button>
              </li>
            ))}
          </ul>
        )}
        {remove.isError && <p className="cap-error">{remove.error.message}</p>}
      </section>
    </>
  )
}

// CLAUDE> the question box: Enter sends, Shift+Enter is a new line; the web search switch says what it adds to the cost
function Composer({ label, placeholder, busy, web, onWeb, webExtra, onSend, onStop, error, autoFocus = false, children }: {
  label: string; placeholder: string; busy: boolean; web: boolean; onWeb: (web: boolean) => void; webExtra: number
  onSend: (text: string) => void; onStop?: () => void; error: string; autoFocus?: boolean; children?: React.ReactNode
}) {
  const [text, setText] = useState('')
  const send = () => {
    if (!text.trim() || busy) return
    onSend(text.trim())
    setText('')
  }
  return (
    <form className="command chat-composer" onSubmit={e => { e.preventDefault(); send() }}>
      <label className="command-label" htmlFor="chat-question">{label}</label>
      <textarea id="chat-question" value={text} placeholder={placeholder} rows={2} autoFocus={autoFocus}
                onChange={e => setText(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
      <div className="chat-composer-row">
        {children}
        <label className="chat-web">
          <input type="checkbox" checked={web} onChange={e => onWeb(e.target.checked)} />
          <span>Web search <span className="muted">+ about {usd(webExtra)} a question, with source links</span></span>
        </label>
        <span className="hint">Enter sends. Shift+Enter starts a new line.</span>
        {onStop
          ? <button type="button" className="primary" onClick={onStop}>Stop</button>
          : <button type="submit" className="primary" disabled={busy || !text.trim()}>Send</button>}
      </div>
      {error && <p className="cap-error">{error}</p>}
    </form>
  )
}

// CLAUDE> the short list with what an answer costs, the model in use when it is not on it (`known`: its name and price), and
// "Other model…": a search over every OpenRouter model
function ModelPicker({ value, models, known, onChange, label = 'Model' }: {
  value: string; models: ChatModel[]; known?: ChatModel; onChange: (id: string, model: ChatModel | undefined) => void; label?: string
}) {
  const [searching, setSearching] = useState(false)
  const [picked, setPicked] = useState<ChatModel | undefined>(undefined)
  const current = [picked, known].find(m => m?.id === value)
  const [query, setQuery] = useState('')
  const found = useQuery({ queryKey: ['chat-models', query], queryFn: () => findModels(query), enabled: searching,
                           placeholderData: keepPreviousData })
  const listed = models.some(m => m.id === value)
  if (searching) {
    const results = found.data?.models ?? []
    return (
      <div className="model-search">
        <span className="model-label">{label}</span>
        <div className="note-picker">
          <input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Find a model, for example mistral or llama"
                 aria-label="Find a model" autoFocus onKeyDown={e => { if (e.key === 'Escape') setSearching(false) }} />
          {results.length > 0 && (
            <ul className="note-results">
              {results.map(m => (
                <li key={m.id}>
                  <button type="button" onClick={() => { setPicked(m); onChange(m.id, m); setSearching(false); setQuery('') }}>
                    <span className="note-name">{m.name}</span>
                    <span className="muted">{perAnswer(m)}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {found.data && results.length === 0 && <p className="muted">No model has these words in its name.</p>}
          {found.isError && <p className="cap-error">{found.error.message}</p>}
        </div>
        <button type="button" className="link-button" onClick={() => setSearching(false)}>Back to the short list</button>
      </div>
    )
  }
  return (
    <label className="model-picker">
      <span className="model-label">{label}</span>
      <select value={value} onChange={e => (e.target.value === ''
        ? setSearching(true) : onChange(e.target.value, models.find(m => m.id === e.target.value)))}>
        {models.map(m => <option key={m.id} value={m.id}>{m.name} — {perAnswer(m)}</option>)}
        {!listed && <option value={value}>{current ? `${current.name} — ${perAnswer(current)}` : value}</option>}
        <option value="">Other model…</option>
      </select>
    </label>
  )
}

const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="chat-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}
                     components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" /> }}>
        {text}
      </ReactMarkdown>
    </div>
  )
})

function ChatView({ id, data }: { id: number; data: ChatList }) {
  const client = useQueryClient()
  const [, navigate] = useLocation()
  const [web, setWeb] = useState(false)
  // CLAUDE> a stopped or broken answer gets its cost from OpenRouter a few seconds after its end: asked again until then
  const query = useQuery({
    queryKey: ['chat', id], queryFn: () => getChat(id),
    refetchInterval: q => (q.state.data?.messages.some(m => m.role === 'assistant' && m.cost_usd === null && m.generation_id
      && m.state !== 'writing' && Date.now() - Date.parse(m.created) < 240000) ? 5000 : false),
  })
  const chat = query.data
  const now = useLive(id)
  useEffect(() => { if (chat?.writing) follow(id, client) }, [chat?.writing, id, client])
  const update = (next: Chat) => {
    client.setQueryData(['chat', id], next)
    client.invalidateQueries({ queryKey: ['chats'] })
  }
  const ask = useMutation({ mutationFn: (text: string) => askChat(id, text, web, ''), onSuccess: update })
  const stop = useMutation({ mutationFn: () => stopChat(id) })
  const model = useMutation({ mutationFn: (next: string) => changeChat(id, { model: next }), onSuccess: update })
  const remove = useMutation({ mutationFn: () => deleteChat(id),
                               onSuccess: () => { client.invalidateQueries({ queryKey: ['chats'] }); navigate('/chat') } })
  const bottom = useRef<HTMLDivElement>(null)
  const turns = chat ? [...new Set(chat.messages.map(m => m.turn))] : []
  // CLAUDE> a new question scrolls the question box into view; while the answer comes, the page follows it as long as the
  // reader is at the question box (a reader who scrolled up to read stays where they are)
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }) }, [turns.length])
  useEffect(() => {
    const end = bottom.current
    if (now && end && end.getBoundingClientRect().top < window.innerHeight + 240) end.scrollIntoView({ block: 'end' })
  }, [now])
  if (query.isError) return <p className="cap-error">{query.error.message}</p>
  if (!chat) return null
  const writing = chat.writing || now !== undefined
  const last = turns[turns.length - 1]
  const lastAnswers = chat.messages.filter(m => m.turn === last && m.role === 'assistant')
  const usable = chat.messages.some(m => m.role === 'assistant' && m.content.trim())
  return (
    <div className="chat-view">
      <header className="chat-head">
        <TitleField chat={chat} onChange={update} />
        <div className="chat-head-row">
          <ModelPicker label="Model of the next answer" value={chat.model} models={data.models} known={chat.model_info}
                       onChange={next => model.mutate(next)} />
          <dl className="research-facts-list">
            <div><dt>Cost</dt><dd>{usd(chat.cost_usd)}</dd></div>
            <div><dt>Started</dt><dd>{day(chat.created)}</dd></div>
          </dl>
          <button type="button" className="quiet" onClick={() => remove.mutate()} disabled={writing || remove.isPending}>Delete chat</button>
        </div>
        {(model.isError || remove.isError) && <p className="cap-error">{(model.error ?? remove.error)?.message}</p>}
      </header>
      <ol className="chat-turns">
        {turns.map(turn => (
          <Turn key={turn} chat={chat} turn={turn} now={now} writing={writing} onChange={update} />
        ))}
      </ol>
      {!writing && lastAnswers.length === 0 && <GetAnswer chat={chat} onChange={update} />}
      {!writing && lastAnswers.some(m => m.content.trim()) && <SecondOpinion chat={chat} data={data} onChange={update} />}
      <Composer label="Your next question" placeholder="Ask on about the answer" busy={ask.isPending || writing} web={web} onWeb={setWeb}
                webExtra={webExtra(data, chat.model_info)} onSend={text => ask.mutate(text)} onStop={writing ? () => stop.mutate() : undefined}
                error={ask.isError ? ask.error.message : stop.isError ? stop.error.message : ''} />
      <div ref={bottom} className="chat-end" />
      {usable && <SaveCard chat={chat} settings={data.settings} onSaved={() => client.invalidateQueries({ queryKey: ['chat', id] })} />}
    </div>
  )
}

function TitleField({ chat, onChange }: { chat: Chat; onChange: (chat: Chat) => void }) {
  const [title, setTitle] = useState(chat.title)
  const rename = useMutation({ mutationFn: (next: string) => changeChat(chat.id, { title: next }), onSuccess: onChange })
  const save = () => {
    const next = title.trim()
    if (!next) setTitle(chat.title)
    else if (next !== chat.title) rename.mutate(next)
  }
  return (
    <div className="research-head-main">
      <input className="research-title" value={title} aria-label="Title of the chat" title="Click to change the title"
             onChange={e => setTitle(e.target.value)} onBlur={save}
             onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') { setTitle(chat.title); e.currentTarget.blur() } }} />
      {rename.isError && <p className="cap-error">{rename.error.message}</p>}
    </div>
  )
}

function Turn({ chat, turn, now, writing, onChange }: {
  chat: Chat; turn: number; now: LiveAnswer | undefined; writing: boolean; onChange: (chat: Chat) => void
}) {
  const question = chat.messages.find(m => m.turn === turn && m.role === 'user')
  const answers = chat.messages.filter(m => m.turn === turn && m.role === 'assistant')
  const several = answers.length > 1
  return (
    <li className="chat-turn">
      {question && (
        <div className="chat-question">
          <p>{question.content}</p>
          {question.web && <span className="chat-tag">Web search</span>}
        </div>
      )}
      <div className={`chat-answers${several ? ' side-by-side' : ''}`}>
        {answers.map(a => (
          <Answer key={a.id} chat={chat} answer={a} now={now && now.messageId === a.id ? now : undefined} several={several}
                  writing={writing} onChange={onChange} />
        ))}
      </div>
    </li>
  )
}

function Answer({ chat, answer, now, several, writing, onChange }: {
  chat: Chat; answer: ChatMessage; now: LiveAnswer | undefined; several: boolean; writing: boolean; onChange: (chat: Chat) => void
}) {
  const again = useMutation({ mutationFn: () => anotherAnswer(chat.id, { replace: answer.id }), onSuccess: onChange })
  const choose = useMutation({ mutationFn: () => chooseAnswer(answer.id), onSuccess: onChange })
  const inProgress = answer.state === 'writing'
  const text = inProgress ? (now?.text || answer.content) : answer.content
  // CLAUDE> only the chosen answer is marked: 'Second opinion' on the first answer, once the other was chosen, was wrong
  const label = several && answer.chosen ? 'The chat goes on from this answer' : ''
  return (
    <article className={`chat-answer${several && answer.chosen ? ' chosen' : ''}`} aria-busy={inProgress}>
      <header className="chat-answer-head">
        <strong title={answer.model}>{shortName(answer.model)}</strong>
        {label && <span className="chat-tag chosen">{label}</span>}
      </header>
      {text ? <Markdown text={text} /> : inProgress ? <p className="muted">The model is thinking…</p> : null}
      {inProgress && now && now.wait > 0 && <p className="muted" role="status">Waiting for OpenRouter's limit: {now.wait} s</p>}
      {!inProgress && answer.state === 'stopped' && <p className="chat-ended">{answer.error || 'You stopped this answer.'}</p>}
      {!inProgress && answer.state === 'failed' && <p className="cap-error">{answer.error}</p>}
      {answer.sources.length > 0 && (
        <div className="chat-sources">
          <span className="muted">Sources</span>
          <ol>
            {answer.sources.map(s => <li key={s.url}><a href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a></li>)}
          </ol>
        </div>
      )}
      <footer className="chat-answer-foot">
        {!inProgress && (
          <span className="muted">
            {answer.cost_usd !== null ? usd(answer.cost_usd) : answer.generation_id ? 'cost being checked' : 'no cost'}
            {answer.seconds !== null && ` · ${Math.round(answer.seconds)} s`}
          </span>
        )}
        {several && !answer.chosen && !inProgress && answer.content.trim() && (
          <button type="button" className="quiet" onClick={() => choose.mutate()} disabled={writing || choose.isPending}>
            Continue with this answer
          </button>
        )}
        {!inProgress && (answer.state === 'failed' || answer.state === 'stopped') && (
          <button type="button" className="quiet" onClick={() => again.mutate()} disabled={writing || again.isPending}>Try again</button>
        )}
      </footer>
      {(again.isError || choose.isError) && <p className="cap-error">{(again.error ?? choose.error)?.message}</p>}
    </article>
  )
}

// CLAUDE> a question without any answer (the page closed before it began): ask for it with the chat's model
function GetAnswer({ chat, onChange }: { chat: Chat; onChange: (chat: Chat) => void }) {
  const get = useMutation({ mutationFn: () => anotherAnswer(chat.id, {}), onSuccess: onChange })
  return (
    <div className="research-actions">
      <button type="button" className="primary" onClick={() => get.mutate()} disabled={get.isPending}>Get the answer</button>
      {get.isError && <p className="cap-error">{get.error.message}</p>}
    </div>
  )
}

function SecondOpinion({ chat, data, onChange }: { chat: Chat; data: ChatList; onChange: (chat: Chat) => void }) {
  const [open, setOpen] = useState(false)
  const first = data.models.find(m => m.id !== chat.model)?.id ?? chat.model
  const [model, setModel] = useState(first)
  const ask = useMutation({ mutationFn: () => anotherAnswer(chat.id, { model }), onSuccess: next => { setOpen(false); onChange(next) } })
  if (!open) {
    return (
      <div className="research-actions">
        <button type="button" className="quiet" onClick={() => setOpen(true)}>Second opinion</button>
        <span className="muted">Another model answers the last question. Its answer shows next to this one.</span>
      </div>
    )
  }
  return (
    <form className="habit-card chat-second" onSubmit={e => { e.preventDefault(); ask.mutate() }}>
      <h2>Second opinion</h2>
      <ModelPicker label="Model of the second opinion" value={model} models={data.models} onChange={setModel} />
      <div className="research-actions">
        <button type="submit" className="primary" disabled={ask.isPending}>{ask.isPending ? 'Asking…' : 'Ask this model'}</button>
        <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {ask.isError && <p className="cap-error">{ask.error.message}</p>}
    </form>
  )
}

function SaveCard({ chat, settings, onSaved }: { chat: Chat; settings: NoteSettings; onSaved: () => void }) {
  const note = chat.note
  const saved = Boolean(note && note.path && !note.error)
  const last = spotOf(chat.target, note?.title || chat.title)
  const [open, setOpen] = useState(!saved)
  const [spot, setSpot] = useState<Spot>(last)
  const save = useMutation({
    mutationFn: (choice: { target?: ReturnType<typeof spotTarget> }) => exportChat(chat.id, choice.target),
    onSuccess: () => { setOpen(false); onSaved() },
  })
  return (
    <article className="habit-card obsidian-card">
      <h2>Save to Obsidian</h2>
      {saved && (
        <p>
          Saved in <a href={note!.obsidian_url} title={note!.path}>{nameOf(note!.path)}</a>
          {last.note ? `, ${placeText(last)}` : ''}. Click the name to open it in Obsidian.
        </p>
      )}
      {note?.message && <p className="research-note" role="status">{note.message}</p>}
      {!open ? (
        <div className="research-actions">
          <button type="button" className="primary" onClick={() => save.mutate({})} disabled={save.isPending}>
            {save.isPending ? 'Saving…' : 'Save again'}
          </button>
          <button type="button" className="quiet" onClick={() => { setSpot(last); save.reset(); setOpen(true) }}>Save somewhere else</button>
        </div>
      ) : (
        <form className="obsidian-form" onSubmit={e => { e.preventDefault(); if (spotReady(spot)) save.mutate({ target: spotTarget(spot) }) }}>
          <SpotPicker settings={settings} value={spot} onChange={setSpot} titleHint={chat.title} skip={`chat ${chat.id}`} />
          {saved && <p className="muted">What was saved before stays where it is. In a note that has this chat already, it goes to the place you choose.</p>}
          <div className="research-actions">
            <button type="submit" className="primary" disabled={save.isPending || !spotReady(spot) || !settings.vault}>
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
            {saved && <button type="button" className="quiet" onClick={() => setOpen(false)}>Cancel</button>}
          </div>
        </form>
      )}
      {save.isError && <p className="cap-error">{save.error.message}</p>}
    </article>
  )
}

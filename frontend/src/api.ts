// thin typed wrappers over the backend; every group uses the same generic routes

export type StandardTask = { id: string; name: string; description: string; example: string }
export type Group = { id: string; name: string; description: string; tasks: StandardTask[]; accepts_files: boolean }
export type Upload = { id: string; name: string; size: number }
export type Row = {
  id: string; cells: Record<string, string>; selectable: boolean; selected: boolean; note: string
  links: Record<string, string>; inputs: Record<string, string>; group?: string
}
export type PreviewGroup = { name: string; calendar: string; note: string }
export type PreviewOption = { name: string; label: string; value: string; help: string; multiline: boolean; choices?: string[] }
export type Evidence = { quote: string; source: string; link: string; verified: boolean }
export type Preview = {
  summary: string; columns: string[]; rows: Row[]; notes: string[]; options: PreviewOption[]; read_only: boolean
  answer: string; evidence: Evidence[]; groups?: PreviewGroup[]
}
export type JobSummary = {
  id: string
  group: string
  sentence: string
  task_id: string
  task_name: string
  started: string
  status: string
  message: string
  applied_at: string
  apply_status: '' | 'ok' | 'error'
  apply_message: string
  results: number
  appliable?: boolean
  preview_ms: number
  apply_ms: number
  duration_ms: number
  models: string[]
  llm_calls: number
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  google_calls: number
  fetches: number
}
export type LLMCall = {
  purpose: string
  model_requested: string
  model_used: string
  provider: string
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  latency_ms: number
  finish_reason: string
  generation_id: string
  system: string
  user: string
  reply: string
  error: string
  stage: 'preview' | 'apply'
  request?: { model?: string; temperature?: number; max_tokens?: number; messages?: { role: string; content: string }[];
    response_format?: { json_schema?: { name?: string; schema?: unknown } }; [key: string]: unknown }
  response?: Record<string, unknown>
}
export type GoogleCall = { method: string; params: Record<string, unknown>; latency_ms: number; error: string; stage: string }
export type Fetch = { url: string; status: number; bytes: number; latency_ms: number; via: string; stage: string }
export type JobDetail = {
  summary: JobSummary; llm_calls: LLMCall[]; google_calls: GoogleCall[]; fetches: Fetch[]; results: string[]
  preview: Partial<Preview>; applied_rows: string[]
}
export type Usage = { calls: number; prompt_tokens: number; completion_tokens: number; cost_usd: number }
export type JobList = {
  jobs: JobSummary[]
  total_cost_usd: number
  by_group: Record<string, Usage>
  by_model: Record<string, Usage>
  configured_model: string
}

export type Interpretation = {
  status: 'preview' | 'clarify' | 'unsupported'
  message: string
  task_id: string | null
  task_name: string | null
  plan_id: string | null
  arguments: Record<string, unknown> | null
  preview: Preview | null
  job: JobSummary | null
}

export class ApiError extends Error {
  constructor(message: string, readonly needsLogin: boolean, readonly setup: string = '') {
    super(message)
  }
}

// the browser's own words for an unreachable server ('Failed to fetch') tell the user nothing
export const APP_NOT_RUNNING = 'The Automation desk app is not running, so this could not be done. Start it again: '
  + 'type run_automation_desk in a terminal (or use your Automation desk launcher), then press the button again.'

const reach = (path: string, init?: RequestInit) => fetch(path, init).catch(() => { throw new ApiError(APP_NOT_RUNNING, false, '') })

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await reach(path, body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`
    throw new ApiError(detail, Boolean(data.auth), typeof data.setup === 'string' ? data.setup : '')
  }
  return data as T
}

export const getGroups = () => call<Group[]>('/api/groups')
export const getExtensionSetup = () => call<{ folder: string }>('/api/setup/extension')
export type Reminder = { id: string; title: string; steps: string[]; created: string; snoozed_until: string; due: boolean }
export const getReminders = () => call<Reminder[]>('/api/reminders')
export const snoozeReminder = (id: string, days: number) => call<{ ok: boolean }>(`/api/reminders/${id}/snooze`, { days })
export const reminderDone = (id: string) => call<{ ok: boolean }>(`/api/reminders/${id}/done`, {})
export const getVersion = () => call<{ build: string; restart_needed?: boolean }>('/api/version')
export const getAuth = () => call<{ ok: boolean; message: string }>('/api/auth')
export const login = () => call<{ ok: boolean; message: string }>('/api/auth/login', {})
export type SiteProgress = { site: string; state: 'waiting' | 'reading' | 'done' | 'browser' | 'failed'; detail: string }
export type WatchedSite = { id: number; label: string; url: string; calendar: string; last_result: string }
export type WatchState = {
  lines: string[]; default_calendar: string; needs_browser: { id: number; site: string }[]; sites: WatchedSite[]
  progress?: { sites?: SiteProgress[]; pause?: number }
}
export const getWatch = () => call<WatchState>('/api/calendar/watch')
export const saveWatch = (lines: string[], defaultCalendar: string) =>
  call<WatchState>('/api/calendar/watch', { lines, default_calendar: defaultCalendar })
// the widened PDF comes back as a file; errors come back as JSON like every other call
export async function pdfMargin(file: File, side: string, percent: number, name: string): Promise<Blob> {
  const query = new URLSearchParams({ side, percent: String(percent), name })
  const response = await reach(`/api/tools/pdf-margin?${query}`, { method: 'POST', body: file })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`, false, '')
  }
  return response.blob()
}
export type StatDay = {
  day: string; open: number | null; overdue: number | null; someday: number | null; this_week: number | null
  this_month: number | null; this_year: number | null; next_year: number | null; planned: number | null
  planned_done: number | null; completed: number; added: number; frog: boolean
}
export type TaskStats = {
  kpis: { open: { now: number | null; week_ago: number | null; first: number | null }; done_7d: number; added_7d: number
          frogs_month: number; days_month: number; plan_7d: { planned: number; done: number } }
  series: StatDay[]
  weekdays: { label: string; average: number | null; plan_pct: number | null }[]
  projects: { project: string; open: number; change: number }[]
  horizons: { label: string; open: number }[]
  cleanup: { project: string; open: number; per_day: number; empty_on: string | null } | null
  since: string | null
}
export const getTaskStats = (period: number) => call<TaskStats>(`/api/tasks/stats?period=${period}`)
export type HabitDay = { day: string; state: 'done' | 'missed' | 'open' | 'free' | 'future' }
export type Habit = {
  id: number; name: string; schedule: string; paused: boolean; streak: number; best: number; month_pct: number | null
  days: HabitDay[]
}
export type HabitsView = {
  day: string; today_date: string; today: { id: number; name: string; done: boolean; due: boolean; streak: number }[]; habits: Habit[]
  reminder: string
}
export const getHabits = (day?: string) => call<HabitsView>(`/api/habits${day ? `?day=${day}` : ''}`)
export const addHabit = (name: string, schedule: string) => call<HabitsView>('/api/habits', { name, schedule })
export const checkHabit = (id: number, day: string, done: boolean) => call<HabitsView>(`/api/habits/${id}/check`, { day, done })
export const reorderHabits = (ids: number[]) => call<HabitsView>('/api/habits/order', { ids })
export const setHabitReminder = (at: string) => call<HabitsView>('/api/habits/reminder', { at })
export async function changeHabit(id: number, fields: { name?: string; schedule?: string; paused?: boolean }): Promise<HabitsView> {
  const response = await reach(`/api/habits/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                                                       body: JSON.stringify(fields) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`, false, '')
  return data as HabitsView
}
export async function deleteHabit(id: number): Promise<HabitsView> {
  const response = await reach(`/api/habits/${id}`, { method: 'DELETE' })
  return response.json() as Promise<HabitsView>
}
export type ResearchQuestion = { id: string; text: string; choices: string[] }
export type ResearchOffer = {
  shop: string; url: string; country: string; price: number | null; currency: string; delivery: number | null
  total: number | null; ships_to_belgium: string; in_stock: boolean | null
  ex_vat?: boolean; contact?: string; region?: string; reviews?: string
}
export type ResearchProduct = {
  n: number; name: string; title?: string; brand: string; model: string; specs: Record<string, string>; offers: ResearchOffer[]
  best_total: number | null
}
export type Weight = 'must' | 'important' | 'nice'
export type Verdict = 'yes' | 'partly' | 'no' | 'unknown'
export type ResearchRequirement = { id?: string; text: string; weight: Weight }
// one scored product; tag 'recommended', 'cheapest good choice' or 'best above your budget'
export type RankedProduct = {
  n: number; name: string; title: string; brand: string; model: string; url: string; shop: string; shops: number
  total: number | null; price_seen: boolean; delivery_known?: boolean; score: number; checks: Record<string, Verdict>
  // a dollar or pound price turned into euros (day's ECB rate, plus 21% import VAT): what the shop showed
  original?: { price: number; total: number; currency: string; rate: number; date: string } | null
  notes?: Record<string, string>; points?: Record<string, number>; points_total?: number; points_max?: number
  pros: string[]; cons: string[]
  fails: string[]; unconfirmed: string[]; over_budget: boolean; tag: string; why_not: string
  contact: string; region: string; reviews: string
}
export type SummaryGroup = { heading: string; items: { text: string; sources: number[] }[] }
export type SummarySource = { n: number; url: string; title: string; site: string }
export type NoteHeading = { level: number; text: string }
// where a summary goes: no note = a new note; else a note of the vault, at the top, the end or after a heading, ## or #
export type SummaryTarget = {
  note: string; place: 'top' | 'end' | 'after'; heading: NoteHeading | null; level: 1 | 2; title: string; vault?: string
}
export type ResearchResult = {
  comparison?: { products: ResearchProduct[]; over_budget: ResearchProduct[]; no_price: ResearchProduct[] }
  ranking?: RankedProduct[]; best?: RankedProduct | null; requirements?: ResearchRequirement[]
  reasons?: string; risks?: string[]; summary?: string; stopped_by?: string; unread?: string[]
  paragraphs?: string[]; groups?: SummaryGroup[]; sources?: SummarySource[]; off_subject?: string[]; pages_read?: number
  note?: { path: string; obsidian_url: string; error: string; message?: string; title?: string }
}
export type ResearchProgress = { id?: number; step?: string; done?: number; total?: number; cost_usd?: number }
export type Research = {
  id: number; request: string; title: string | null; kind: 'product' | 'service' | 'summary'; budget: number | null; countries: string[]
  state: 'questions' | 'requirements' | 'budget' | 'running' | 'waiting' | 'done' | 'failed' | 'stopped'; step: string; note: string
  requirements: ResearchRequirement[] | null
  questions: ResearchQuestion[] | null; answers: Record<string, string> | null
  classes: { name: string; low: number; high: number; difference: string }[] | null
  followups: ResearchQuestion[] | null; result: ResearchResult | null; cost_usd: number; created: string
  pages: { url: string; status: string; country: string; error: string | null }[]; progress: ResearchProgress
  stopped_text?: string
  // a summary's plan is its sites and pages; a product research's plan is its search words
  plan?: { kind?: 'site' | 'page' | 'web'; site?: string; url?: string; query?: string }[] | null
  target?: Partial<SummaryTarget> | null
  // what a finished research has not done yet, and the most a run on costs
  left?: { pages: number; searches: number }
  go_on_estimate?: number
}
export type ResearchSettings = {
  countries: string[]; municipality: string; limits: { searches: number; pages: number; cost: number }; vault: string; subdir: string
}
export type ResearchList = {
  researches: {
    id: number; request: string; title: string | null; kind: string; state: string; created: string; cost_usd: number
    best: { title: string; score: number; total: number | null } | null
    summary: { points: number; sources: number; sites: number } | null
  }[]
  settings: ResearchSettings; vaults: { name: string; path: string }[]; estimate: number; progress: ResearchProgress
  unit_costs: { search: number; page: number; steps: number; summary_steps: number }
}
export const getResearchList = () => call<ResearchList>('/api/research')
export const getResearch = (id: number) => call<Research>(`/api/research/${id}`)
export const startResearch = (request: string, budget: number | null, countries: string[]) =>
  call<Research>('/api/research', { request, budget, countries })
export const answerResearch = (id: number, answers: Record<string, string>, kind: string) =>
  call<Research>(`/api/research/${id}/answers`, { answers, kind })
export const setResearchBudget = (id: number, amount: number) => call<Research>(`/api/research/${id}/budget`, { amount })
export const replyResearch = (id: number, answers: Record<string, string>) =>
  call<Research>(`/api/research/${id}/reply`, { answers })
export const continueResearch = (id: number) => call<Research>(`/api/research/${id}/continue`, {})
export const confirmRequirements = (id: number, requirements: ResearchRequirement[]) =>
  call<Research>(`/api/research/${id}/requirements`, { requirements })
export const rescoreResearch = (id: number, requirements: ResearchRequirement[]) =>
  call<Research>(`/api/research/${id}/rescore`, { requirements })
export const cancelRequirements = (id: number) => call<Research>(`/api/research/${id}/requirements/cancel`, {})
// one save to Obsidian: a research's note title, or a summary's place and title; empty = the place and title saved last
export const exportResearch = (id: number, choice: { title?: string; target?: SummaryTarget } = {}) =>
  call<{ path: string; obsidian_url: string; title: string }>(`/api/research/${id}/export`, choice)
export const startSummary = (subject: string, sites: string[], target: SummaryTarget) =>
  call<Research>('/api/research/summary', { subject, sites, target })
export const goOnResearch = (id: number) => call<Research>(`/api/research/${id}/more`, {})
export const addSummarySites = (id: number, sites: string[]) => call<Research>(`/api/research/${id}/sites`, { sites })
export const findNotes = (query: string) =>
  call<{ notes: { path: string; name: string }[] }>(`/api/research/notes?q=${encodeURIComponent(query)}`)
// `skip`: the summary (its number) or chat ('chat 3') being saved, whose own headings are no place to put it
export const noteHeadings = (path: string, skip?: number | string) =>
  call<{ headings: NoteHeading[] }>(
    `/api/research/notes/headings?path=${encodeURIComponent(path)}${skip ? `&skip=${encodeURIComponent(String(skip))}` : ''}`)
export async function renameResearch(id: number, title: string): Promise<Research> {
  const response = await reach(`/api/research/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                                                     body: JSON.stringify({ title }) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`, false)
  return data as Research
}
export const saveResearchSettings = (settings: Partial<ResearchSettings>) =>
  call<ResearchSettings>('/api/research/settings', settings)
export async function deleteResearch(id: number): Promise<{ deleted: boolean }> {
  const response = await reach(`/api/research/${id}`, { method: 'DELETE' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`, false)
  return data
}
export const stopAction = (action: string) => call<{ stopping: string }>(`/api/stop/${action}`, {})
export const watchFromEvents = () => call<WatchState & { added: number; unmatched: number }>('/api/calendar/watch/from-events', {})
export const checkWatch = (viaBrowser: boolean, only: number[] | null = null) =>
  call<Interpretation>('/api/calendar/watch/check', { via_browser: viaBrowser, only })
export const interpret = (group: string, text: string, taskId: string | null, uploadIds: string[] = []) =>
  call<Interpretation>(`/api/groups/${group}/interpret`, { text, task_id: taskId, upload_ids: uploadIds })

export async function uploadFile(file: File): Promise<Upload> {
  const response = await reach(`/api/uploads?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Upload failed (${response.status})`, false)
  return data as Upload
}
export const adjust = (group: string, planId: string, options: Record<string, string>) =>
  call<Interpretation>(`/api/groups/${group}/plans/${planId}/adjust`, { options })
export const execute = (group: string, planId: string, selected: string[], section = '') =>
  call<{ results: string[]; job: JobSummary }>(`/api/groups/${group}/execute`, { plan_id: planId, selected, section })
export const getJobs = (group?: string) => call<JobList>(group ? `/api/jobs?group=${group}` : '/api/jobs')
export const getJob = (id: string) => call<JobDetail>(`/api/jobs/${id}`)

export type NewsArticle = { link: string; title: string; source: string; published: string | null; from_teaser: boolean; reason: string }
export type NewsStory = { id: string; title: string; summary: string; articles: NewsArticle[] }
export type NewsDigestHead = {
  id: number; made_at: string; covers_from: string; trigger: string; job_id: string
  article_count: number; story_count: number; source_count: number; problems: string[]
}
export type NewsLeftOut = { link: string; title: string; source: string; topic: string }
export type NewsDigest = NewsDigestHead & {
  cost_usd: number; subjects: { subject: string; stories: NewsStory[] }[]; left_out: NewsLeftOut[]
  needs_check: { id: number; name: string; site: string }[]; failed: { id: number; name: string; site: string }[]; unsorted: number; cap_to_sort: number | null; cap_usd: number
}
export type NewsSource = { id: number; site: string; name: string; feed: string; kind: string; last_checked: string; last_result: string }
export type NewsOverview = {
  sources: NewsSource[]; subjects: string[]; suggestions: { name: string; examples: string[] }[]
  blocked: string[]; digests: NewsDigestHead[]; cap_usd: number; running: boolean; failure: string
  nothing_new: { at: string; since: string; problems: string[] } | null
  progress?: { step?: string; sites?: Record<string, string> }
}
export const getNewsOverview = () => call<NewsOverview>('/api/news/overview')
export const getNewsDigest = (id: number) => call<NewsDigest>(`/api/news/digests/${id}`)
export type SuggestionAnswer = 'accept' | 'block' | 'reject'
export const answerSuggestion = (name: string, answer: SuggestionAnswer) =>
  call<{ ok: boolean }>('/api/news/suggestions', { name, answer })
export const saveSubjects = (names: string[]) => call<{ subjects: string[] }>('/api/news/subjects', { names })
export const saveBlocked = (names: string[]) => call<{ blocked: string[] }>('/api/news/blocked', { names })
export const setNewsCap = (usd: number) => call<{ cap_usd: number }>('/api/news/cap', { usd })
export const makeNewsDigest = () => call<{ started: boolean }>('/api/news/make', {})
export async function deleteNewsStory(id: string): Promise<void> {
  const response = await reach(`/api/news/stories/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`The story could not be deleted (${response.status}). Reload the page.`)
}
export const continueNewsDigest = (id: number, raiseCap: boolean) =>
  call<{ started: boolean }>(`/api/news/digests/${id}/continue`, { raise_cap: raiseCap })
export async function deleteNewsDigest(id: number): Promise<void> {
  const response = await reach(`/api/news/digests/${id}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`The digest could not be deleted (${response.status}). Reload the page.`)
}
export const deleteNewsSubject = (digestId: number, subject: string) =>
  call<{ deleted: number }>(`/api/news/digests/${digestId}/delete-subject`, { subject })
export const saveNewsSites = (sites: string[]) => call<{ problems: string[]; sources: NewsSource[] }>('/api/news/sources', { sites })
export async function removeNewsSource(id: number): Promise<void> {
  await reach(`/api/news/sources/${id}`, { method: 'DELETE' })
}

// the Chat page: questions to an OpenRouter model, answers word by word, second opinions, saves to Obsidian
export type ChatModel = {
  id: string; name: string; in_per_m: number | null; out_per_m: number | null; per_answer: number | null; measured: boolean
  web_extra: number | null
}
export type ChatSource = { url: string; title: string }
export type ChatMessage = {
  id: number; chat_id: number; turn: number; role: 'user' | 'assistant'; content: string; model: string; web: boolean
  sources: ChatSource[]; chosen: boolean; state: 'writing' | 'done' | 'stopped' | 'failed'; error: string
  cost_usd: number | null; seconds: number | null; job_id: string; generation_id: string; created: string
}
export type ChatNote = { path: string; obsidian_url: string; title: string; message: string; error: string }
export type Chat = {
  id: number; title: string; model: string; created: string; updated: string; target: SummaryTarget | null
  note: ChatNote | null; messages: ChatMessage[]; cost_usd: number; writing: boolean; model_info: ChatModel
}
export type ChatSummary = { id: number; title: string; model: string; created: string; updated: string; cost_usd: number; questions: number }
export type ChatList = {
  chats: ChatSummary[]; models: ChatModel[]; new_model: string; new_model_info: ChatModel; web_extra: number
  settings: { vault: string; subdir: string }
}
export type ChatEvent = { type: 'start'; message: ChatMessage } | { type: 'wait'; seconds: number } | { type: 'piece'; text: string }
  | { type: 'end'; message: ChatMessage }

async function send<T>(path: string, method: 'PATCH' | 'PUT' | 'DELETE', body?: unknown): Promise<T> {
  const response = await reach(path, { method, headers: { 'Content-Type': 'application/json' },
                                       body: body === undefined ? undefined : JSON.stringify(body) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`, false)
  return data as T
}

export const getChats = () => call<ChatList>('/api/chat')
export const getChat = (id: number) => call<Chat>(`/api/chat/${id}`)
export const findModels = (query: string) => call<{ models: ChatModel[] }>(`/api/chat/models?q=${encodeURIComponent(query)}`)
export const startChat = (text: string, model: string, web: boolean) => call<Chat>('/api/chat', { text, model, web })
export const askChat = (id: number, text: string, web: boolean, model: string) =>
  call<Chat>(`/api/chat/${id}/questions`, { text, web, model })
export const anotherAnswer = (id: number, choice: { model?: string; replace?: number }) => call<Chat>(`/api/chat/${id}/answers`, choice)
export const stopChat = (id: number) => call<Chat>(`/api/chat/${id}/stop`, {})
export const chooseAnswer = (messageId: number) => call<Chat>(`/api/chat/messages/${messageId}/choose`, {})
export const exportChat = (id: number, target?: SummaryTarget) => call<ChatNote>(`/api/chat/${id}/export`, target ? { target } : {})
export const changeChat = (id: number, change: { title?: string; model?: string }) => send<Chat>(`/api/chat/${id}`, 'PATCH', change)
export const deleteChat = (id: number) => send<{ deleted: boolean }>(`/api/chat/${id}`, 'DELETE')

// the answer being written, as the server sends it (one JSON event per 'data:' line); ends with the answer, or
// at once when no answer is being written. A lost connection just ends it: the page then reloads the chat.
export async function followChat(id: number, onEvent: (event: ChatEvent) => void): Promise<void> {
  let response: Response
  try {
    response = await fetch(`/api/chat/${id}/follow`)
  } catch {
    return
  }
  if (!response.ok || !response.body) return
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return
      buffer += value
      let cut = buffer.indexOf('\n\n')
      while (cut >= 0) {
        for (const line of buffer.slice(0, cut).split('\n')) {
          if (line.startsWith('data: ')) onEvent(JSON.parse(line.slice(6)) as ChatEvent)
        }
        buffer = buffer.slice(cut + 2)
        cut = buffer.indexOf('\n\n')
      }
    }
  } catch {
    return
  }
}

// the Health page: the supplement regime for reference, the supplements with each unit taken at its time, the angina
// score, the Withings measurements
export type RegimeRow = {
  id: number; moment: string; moment_when: string; position: number; name: string; dose: string; how: string; why: string
  effects: string
}
export type HealthMoment = { name: string; when: string; supplements: RegimeRow[] }
// a supplement of the day: its unit, the units a day (null: no number a day), the units of the day in time order;
// listed false: removed from the list, shown for the units it has that day
export type HealthSupplement = {
  id: number; name: string; unit: string; per_day: number | null; listed: boolean; units: { id: number; taken_at: string }[]
}
export type WithingsValue = { value: number; measured_at: string }
export type HealthPressure = { systolic: number | null; diastolic: number | null; pulse: number | null; measured_at: string }
// segments: per kind (muscle_mass, fat_mass, fat_free_mass) the newest value of each body part
export type HealthLatest = {
  pressure: HealthPressure | null; values: Record<string, WithingsValue | undefined>
  segments: Record<string, Record<string, WithingsValue | undefined> | undefined>
}
export type WithingsStatus = {
  configured: boolean; connected: boolean; last_fetch: string | null; error: string; fetching: boolean; callback_url: string
}
// a Ketostix reading (level in mmol/L), and the six colours of the strip the server takes
export type KetoneReading = { id: number; taken_at: string; level: number }
export type KetostixLevel = { level: number; label: string; mg_dl: number }
export type HealthView = {
  day: string; today: string; now: string; moments: HealthMoment[]; supplements: HealthSupplement[]
  angina: { score: number | null; note: string; not_measured: boolean } | null
  ketones: KetoneReading[]; ketostix: KetostixLevel[]; latest: HealthLatest; withings: WithingsStatus; polar: PolarStatus
  sleep: SleepStatus
  // the tape measurements of the day by site, and the last day with any before it
  tape: { values: Record<string, number>; last: { day: string; values: Record<string, number> } | null }
}
// a day of the history: the fixed parts, and every scale value of that day by name ('weight', 'muscle_mass:left_arm')
export type HealthDayRow = {
  day: string; taken: number | null; planned: number; angina: number | null; angina_not_measured: boolean; ketones: number | null
  ketone_readings: number
  systolic: number | null; diastolic: number | null; pulse: number | null; readings: number
} & { [value: string]: string | number | null | undefined }

export const getHealth = (day?: string) => call<HealthView>(`/api/health${day ? `?day=${day}` : ''}`)
// one weigh-in (its groups of one minute) or one blood pressure reading; day is set for a removed one
export type HealthMeasurement = {
  kind: 'scale' | 'pressure'; measured_at: string; grpids: number[]; values: Record<string, number>; day?: string
}
// the Polar H10 values of one activity only; nothing for all sessions
const activityParam = (activity: number | null) => (activity === null ? '' : `&activity=${activity}`)
// a recording at its time, for the charts that show every recording of a day (a blood pressure reading, a measurement of
// the scale, a Ketostix reading, a Polar H10 session, an HRV or HR recovery test): its time (local, or UTC with its
// offset), its day and its values under their history keys
export type RecordingPoint = { at: string; day: string } & Record<string, number | string>
export const getHealthHistory = (days: number, activity: number | null = null) =>
  call<{ days: HealthDayRow[]; points: RecordingPoint[]; sleeps: (SleepTime & { day: string })[]; removed: HealthMeasurement[] }>(
    `/api/health/history?days=${days}${activityParam(activity)}`)
export const getDayMeasurements = (day: string) => call<{ measurements: HealthMeasurement[] }>(`/api/health/days/${day}/measurements`)
export const hideMeasurement = (grpids: number[]) => call<{ removed: HealthMeasurement[] }>('/api/health/measurements/hide', { grpids })
export const showMeasurement = (grpids: number[]) => call<{ removed: HealthMeasurement[] }>('/api/health/measurements/show', { grpids })
// the large view of a chart: one value with its rolling mean and band, numbers, histogram, outliers, the change from
// one day to the next, the last week against earlier weeks, years; and a second value against it by day
export type HealthAnalysis = {
  key: string; start: string; end: string; window: number
  points: { day: string; value: number; mean: number; std: number | null }[]
  stats: {
    count: number; mean: number; median: number; std: number | null; min: { day: string; value: number }
    max: { day: string; value: number }; trend_per_month: number | null
  } | null
  histogram: { edges: number[]; counts: number[] } | null
  outliers: { day: string; value: number; expected: number; z: number }[]
  steps: { count: number; median_size: number; p90_size: number; histogram: { edges: number[]; counts: number[] } } | null
  earlier: { ago: number; from: string; to: string; count: number; mean: number | null; diff: number | null }[]
  years: { year: number; months: (number | null)[] }[]
  // each recording of the value at its time (none for a value of the day: angina, supplements, the tape)
  readings: { at: string; day: string; value: number }[]
  // what the numbers count: the values of the days, or each session (a value of the Polar H10 sessions has no value of a
  // day, so no change from day to day and no correlation by day)
  per: 'day' | 'session'
}
export const getHealthAnalysis = (key: string, days: number | null, window: number, activity: number | null = null) =>
  call<HealthAnalysis>(`/api/health/analysis?key=${encodeURIComponent(key)}&window=${window}${days ? `&days=${days}` : ''}`
    + activityParam(activity))
// the second value on day D - lag against the first value on day D; by_lag: r and n of every delay from 0 to 30 days
export type HealthRelation = {
  key: string; other: string; lag: number; points: { day: string; x_day: string; x: number; y: number }[]; n: number
  r: number | null; slope: number | null; intercept: number | null; by_lag: { lag: number; r: number | null; n: number }[]
}
export const getHealthRelation = (key: string, other: string, days: number | null, lag = 0, activity: number | null = null) =>
  call<HealthRelation>(`/api/health/relation?key=${encodeURIComponent(key)}&other=${encodeURIComponent(other)}`
    + (days ? `&days=${days}` : '') + `&lag=${lag}` + activityParam(activity))
export const addUnit = (day: string, supplementId: number, takenAt: string) =>
  call<HealthView>('/api/health/units', { day, supplement_id: supplementId, taken_at: takenAt })
export const setUnitTime = (id: number, takenAt: string) => send<HealthView>(`/api/health/units/${id}`, 'PUT', { taken_at: takenAt })
export const removeUnit = (id: number) => send<HealthView>(`/api/health/units/${id}`, 'DELETE')
export type SupplementFields = { name: string; unit: string; per_day: number | null }
export const addSupplement = (day: string, fields: SupplementFields) => call<HealthView>('/api/health/supplements', { day, ...fields })
export const changeSupplement = (id: number, day: string, fields: SupplementFields) =>
  send<HealthView>(`/api/health/supplements/${id}`, 'PUT', { day, ...fields })
export const removeSupplement = (id: number, day: string) => send<HealthView>(`/api/health/supplements/${id}?day=${day}`, 'DELETE')
export const setAngina = (day: string, score: number | null, note: string, notMeasured = false) =>
  send<HealthView>('/api/health/angina', 'PUT', { day, score, note, not_measured: notMeasured })
export const setTape = (day: string, site: string, cm: number | null) =>
  send<HealthView>('/api/health/tape', 'PUT', { day, site, cm })
export const addKetoneReading = (day: string, level: number, takenAt: string) =>
  call<HealthView>('/api/health/ketones', { day, level, taken_at: takenAt })
export const setKetoneTime = (id: number, takenAt: string) =>
  send<HealthView>(`/api/health/ketones/${id}`, 'PUT', { taken_at: takenAt })
export const removeKetoneReading = (id: number) => send<HealthView>(`/api/health/ketones/${id}`, 'DELETE')
export const fetchWithings = (day: string) => call<HealthView>(`/api/health/withings/fetch?day=${day}`, {})
// one sleep of Sleep as Android: its bedtime and wake time (with their offset) and its sleep time
export type SleepTime = { start_at: string; end_at: string; sleep_min: number }
// the sleep of a day (the sleeps that end on it): its values under their history keys ('sleep:total', …), a value not
// measured left out, and its sleeps in time order
export type SleepDay = { values: Record<string, number>; sleeps: SleepTime[] }
// the Sleep as Android backup that Dropbox syncs to the PC: its place set, its time, the last import, a newer backup
// waiting, what to do (not set, not there, old); and the sleep of the page's day
export type SleepStatus = {
  configured: boolean; backup_at: string | null; imported_at: string | null; waiting: boolean; message: string
  day: SleepDay | null
}
// a tile of the overview row: its value as text ('' without data: then `empty` says what to do), its change against the
// 30-day mean, its status (within, above, below; falling, steady, rising; met, open) in words, a detail line, and 30 days
// of values for the sparkline
export type OverviewTile = {
  key: string; label: string; unit: string; value: number | null; text: string; change: { value: number; unit: string } | null
  status: string | null; status_text: string; detail: string; empty: string; spark: (number | null)[]
}
export const getHealthOverview = (day: string) => call<{ tiles: OverviewTile[] }>(`/api/health/overview?day=${day}`)
export const importSleep = (day: string) => call<HealthView>(`/api/health/sleep/import?day=${day}`, {})
// the sleep pattern of the last `days` days from the main sleep of each day (its longest): the standard deviation of the
// bedtime and the wake time in minutes (null below 3 nights), the weekdays and the weekend (by the wake day; clock times),
// the social jetlag in minutes, and the main sleep against the ln RMSSD of the same morning (r null below 10 mornings;
// slope: ln RMSSD per hour of sleep); null without a sleep
export type SleepGroup = { nights: number; sleep_min: number; bedtime: string; wake: string; mid: string }
export type SleepSummary = {
  nights: number; bedtime_sd: number | null; wake_sd: number | null; weekday: SleepGroup | null; weekend: SleepGroup | null
  jetlag_min: number | null; hrv: { pairs: number; r: number | null; slope: number | null }
}
export const getSleepSummary = (days: number) => call<SleepSummary | null>(`/api/health/sleep/summary?days=${days}`)
// each sleep of the last `days` days (null: from the first sleep on), for the large view of the sleep times
export const getSleepTimes = (days: number | null) =>
  call<{ first: string; last: string; sleeps: (SleepTime & { day: string })[] }>(`/api/health/sleep/times${days ? `?days=${days}` : ''}`)
// the Polar H10 import: set up (the two folders in automation_desk.toml), running with its progress in files (first the
// copy from the phone, then the reading), the last import, a failure, the number of sessions
// button: the name of the import button, where it imports from; phase: moving the files from the phone, or reading the
// files on the PC; last_note: where the last import read from, and what the move from the phone did; phone_error: why the
// last import did not read the phone, with what to do (no import error: the phone's FTP server is often off)
export type PolarStatus = {
  configured: boolean; button: string; running: boolean; phase: 'copy' | 'read'; done: number; total: number; saved: number
  last_import: string | null; last_note: string
  error: string; phone_error: string
  sessions: number
  // the newest session that is not an HRV test, up to the day of the page, the newest HRV test of the page's day and the 7
  // days before, and the newest HR recovery test and stairs test of the page's day and the 30 days before
  // last_exercise.energy: its energy expenditure in kcal, an estimate (null when person.toml lacks a value it needs)
  last_exercise: (PolarSession & { energy: number | null }) | null; last_hrv: PolarHrvCard | null
  last_recovery: PolarRecoveryCard | null
  last_stairs: PolarStairsCard | null
}
// one session, times in UTC: its activity and note, its time in movement and still with their mean HR. kind, rest_hr, rmssd
// and wrong_beats_pct are old fields that the import no longer fills (the HRV test has its own values)
export type PolarSession = {
  id: number; source: 'phone' | 'logger'; serial: string; start_at: string; end_at: string; day: string
  kind: 'exercise' | 'rest'; seconds: number; mean_hr: number | null; max_hr: number | null; rest_hr: number | null
  rmssd: number | null
  wrong_beats_pct: number | null; copy_of: number | null; copy_of_start: string | null; removed: boolean
  markers: PolarMarker[]; activity_id: number | null; activity: string | null; activity_test: string | null
  activity_no_split: boolean
  note: string | null
  moving_s: number | null; still_s: number | null; moving_hr: number | null; still_hr: number | null
}
export const importPolar = (day: string) => call<HealthView>(`/api/health/polar/import?day=${day}`, {})
export const getPolarSessions = (offset: number, limit: number) =>
  call<{ sessions: PolarSession[]; total: number; removed: PolarSession[] }>(`/api/health/polar/sessions?offset=${offset}&limit=${limit}`)
export const getPolarDay = (day: string) => call<{ sessions: PolarSession[] }>(`/api/health/polar/days/${day}`)
export const removePolarSession = (id: number) => call<{ ok: boolean }>(`/api/health/polar/sessions/${id}/remove`, {})
export const showPolarSession = (id: number) => call<{ ok: boolean }>(`/api/health/polar/sessions/${id}/show`, {})
export const removePolarSessions = (ids: number[]) => call<{ ok: boolean }>('/api/health/polar/sessions/remove', { ids })
export const showPolarSessions = (ids: number[]) => call<{ ok: boolean }>('/api/health/polar/sessions/show', { ids })
// the activities of the sessions, in list order, each with how many sessions have it; last_used: the activity of the
// last recording started from the card
// no_split: the chest stays still during the effort (weights on a bench), so its sessions have no split in movement and still
// last_at: the start of the newest session that is not removed (null without one)
export type PolarActivity = {
  id: number; name: string; test: string | null; no_split: boolean; sessions: number; last_at: string | null
}
export const getPolarActivities = () =>
  call<{ activities: PolarActivity[]; last_used: number | null }>('/api/health/polar/activities')
export const addPolarActivity = (name: string) => call<{ id: number }>('/api/health/polar/activities', { name })
export const renamePolarActivity = (id: number, name: string) =>
  call<{ ok: boolean }>(`/api/health/polar/activities/${id}`, { name })
export const removePolarActivity = (id: number) => call<{ ok: boolean }>(`/api/health/polar/activities/${id}/remove`, {})
export const setPolarNoSplit = (id: number, on: boolean) =>
  call<{ ok: boolean }>(`/api/health/polar/activities/${id}/no-split`, { on })
// only the fields given change
export const labelPolarSession = (id: number, fields: { activity_id?: number | null; note?: string }) =>
  call<{ ok: boolean }>(`/api/health/polar/sessions/${id}/label`, fields)
// the popup of one session: each second from start (epoch s) with its HR and whether it is still (null: no movement
// data), the zones (null without a birth year: below zone 1, then zone 1 to 5, in seconds), the parts between stops and
// the comparison with the last earlier sessions of the same activity
export type PolarPart = {
  kind: 'movement' | 'stop'; from: number; to: number; seconds: number; mean_hr: number | null; max_hr: number | null
}
export type PolarCompareValues = {
  seconds: number | null; moving_hr: number | null; max_hr: number | null; high_zone_min: number | null
}
export type PolarDetail = {
  session: PolarSession; start: number; hr: (number | null)[]; still: (0 | 1 | null)[] | null; birth_year: number | null
  zones: { max_hr: number; limits: number[]; seconds: number[] } | null
  parts: PolarPart[] | null
  compare: { count: number; this: PolarCompareValues; usual: PolarCompareValues } | null
  hrv: (PolarHrv & { beats: [number, number, number][] }) | null
  recovery: PolarRecovery | null
  stairs: PolarStairs | null
  // the energy expenditure in kcal, an estimate (null when person.toml lacks a value it needs: energy_missing says which);
  // energy_note: the correction factor of person.toml, or why it is not used ('' without one); trimp: the Edwards TRIMP
  // (null without a birth date)
  energy: number | null; energy_missing: string; energy_note: string; trimp: number | null
}
// the values of an HRV test (the 5 minutes after the first minute, the window in UTC ms), or the reason it has none
export type PolarHrv = {
  reason: string | null; rest_hr: number | null; rmssd: number | null; ln_rmssd: number | null; sdnn: number | null
  pnn50: number | null; lf: number | null; hf: number | null; lf_hf: number | null; stress: number | null
  wrong_pct: number | null; from_ms: number | null; to_ms: number | null
}
// the 7-day mean of ln RMSSD against the normal range of 60 days, and its 7-day coefficient of variation in % (null below
// 3 tests); without a range (fewer than 14 tests) missing says how many more tests are needed
export type PolarHrvStatus = {
  mean_7d: number | null; cv_7d: number | null; low: number | null; high: number | null
  status: 'below' | 'within' | 'above' | null; missing: number
}
// the values of an HR recovery test (the stop: the last Effort done marker, else the last second with the highest HR;
// stop_s: its second from the start of the session), or the reason it has none
export type PolarRecovery = {
  reason: string | null; hr_stop: number | null; hr_60: number | null; hr_120: number | null; drop_60: number | null
  drop_120: number | null; stop_s: number | null; stop_from: 'marker' | 'highest HR' | null
}
export type PolarRecoveryCard = PolarRecovery & { session_id: number; start_at: string }
// the values of a stairs test (the climb: from the first press of the phone marker to the last; each stop with a start
// after it is a pause; the seconds from the start of the session), or the reason it has none. The HR at the top and after
// it follow the HR recovery rules, with their own reason
export type PolarStairs = {
  reason: string | null; climb_s: number | null; pauses: number | null; pause_s: number | null; max_hr: number | null
  start_s: number | null; end_s: number | null; pause_list: { at_s: number; seconds: number }[] | null
  hr_top: number | null; hr_60: number | null; hr_120: number | null; drop_60: number | null; drop_120: number | null
  recovery_reason: string | null
}
export type PolarStairsCard = PolarStairs & { session_id: number; start_at: string }
// the newest session of a test with its values, for the result in its popup
export type PolarLastTest = (PolarHrv | PolarRecovery | PolarStairs) & { session_id: number; start_at: string }
export const getPolarLastTest = (test: 'hrv' | 'recovery' | 'stairs') =>
  call<PolarLastTest | null>(`/api/health/polar/tests/${test}/last`)
export type PolarHrvCard = PolarHrv & { session_id: number; start_at: string; status: PolarHrvStatus }
export const getPolarDetail = (id: number) => call<PolarDetail>(`/api/health/polar/sessions/${id}`)
// how many sessions in the folders on the PC the next import reads; recalc: because the calculations changed
export const getPolarWaiting = () => call<{ sessions: number; recalc: boolean }>('/api/health/polar/waiting')
// the minutes of the sessions per week (its Monday) by activity name, and the activity names in list order
export type PolarWeek = { week: string; minutes: Record<string, number> }
// the minutes per week in each of the 5 HR zones (zone 1 first) and the lower limits of the zones this year; both null
// without a birth date in person.toml
export const getPolarZones = (days: number) =>
  call<{ limits: number[] | null; weeks: { week: string; minutes: number[] }[] | null }>(`/api/health/polar/zones?days=${days}`)
export const getPolarWeeks = (days: number, activity: number | null) =>
  call<{ weeks: PolarWeek[]; activities: string[] }>(`/api/health/polar/weeks?days=${days}${activityParam(activity)}`)
// the energy expenditure in kcal per week (its Monday) by activity name, of the sessions that are not HRV tests; the activity
// names in list order; what person.toml must give when a value is missing ('' when none is)
// the minutes of moderate and of vigorous activity per week (its Monday), the WHO minimum a week (a vigorous minute counts
// double), and what person.toml must give when it is missing ('' when nothing is)
export const getPolarActivity = (days: number) =>
  call<{ weeks: { week: string; moderate: number; vigorous: number }[]; target: number; missing: string }>(
    `/api/health/polar/activity?days=${days}`)
export const getPolarEnergy = (days: number) =>
  call<{ weeks: { week: string; kcal: Record<string, number> }[]; activities: string[]; missing: string }>(
    `/api/health/polar/energy?days=${days}`)
// a marker of a logger session: its time (UTC), its label and the HR of its second
export type PolarMarker = { at: string; label: string; hr: number | null }
// the recording with the user's own logger now: its state, the live values, the last marker (Effort done of the HR
// recovery test), the last error and the activity chosen at Start
export type PolarLive = {
  configured: boolean
  state: 'off' | 'connecting' | 'recording' | 'connection lost' | 'stopping' | 'importing'
  seconds: number | null; hr: number | null; battery: number | null
  last_marker: { label: string; at: string } | null
  error: string
  // the activity chosen at Start recording (null for a recording started elsewhere)
  activity: number | null
}
export const getPolarLive = () => call<PolarLive>('/api/health/polar/live')
export const startPolarRecording = (activityId: number | null) =>
  call<PolarLive>('/api/health/polar/live/start', { activity_id: activityId })
export const stopPolarRecording = () => call<PolarLive>('/api/health/polar/live/stop', {})
export const markPolar = (label: string) => call<PolarLive>('/api/health/polar/live/markers', { label })

// CLAUDE> thin typed wrappers over the backend; every group uses the same generic routes

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

// CLAUDE> the browser's own words for an unreachable server ('Failed to fetch') tell the user nothing
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
export type WatchState = {
  lines: string[]; default_calendar: string; needs_browser: { id: number; site: string }[]
  progress?: { sites?: SiteProgress[]; pause?: number }
}
export const getWatch = () => call<WatchState>('/api/calendar/watch')
export const saveWatch = (lines: string[], defaultCalendar: string) =>
  call<WatchState>('/api/calendar/watch', { lines, default_calendar: defaultCalendar })
// CLAUDE> the widened PDF comes back as a file; errors come back as JSON like every other call
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
  weekdays: { label: string; average: number; plan_pct: number | null }[]
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
  day: string; today_date: string; today: { id: number; name: string; done: boolean; streak: number }[]; habits: Habit[]
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

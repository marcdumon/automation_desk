// CLAUDE> thin typed wrappers over the backend; every group uses the same generic routes

export type StandardTask = { id: string; name: string; description: string; example: string }
export type Group = { id: string; name: string; description: string; tasks: StandardTask[]; accepts_files: boolean }
export type Upload = { id: string; name: string; size: number }
export type Row = {
  id: string; cells: Record<string, string>; selectable: boolean; selected: boolean; note: string
  links: Record<string, string>; inputs: Record<string, string>
}
export type PreviewOption = { name: string; label: string; value: string; help: string; multiline: boolean }
export type Evidence = { quote: string; source: string; link: string; verified: boolean }
export type Preview = {
  summary: string; columns: string[]; rows: Row[]; notes: string[]; options: PreviewOption[]; read_only: boolean
  answer: string; evidence: Evidence[]
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

async function call<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, body === undefined ? undefined : {
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
export const getVersion = () => call<{ build: string }>('/api/version')
export const getAuth = () => call<{ ok: boolean; message: string }>('/api/auth')
export const login = () => call<{ ok: boolean; message: string }>('/api/auth/login', {})
export const interpret = (group: string, text: string, taskId: string | null, uploadIds: string[] = []) =>
  call<Interpretation>(`/api/groups/${group}/interpret`, { text, task_id: taskId, upload_ids: uploadIds })

export async function uploadFile(file: File): Promise<Upload> {
  const response = await fetch(`/api/uploads?name=${encodeURIComponent(file.name)}`, { method: 'POST', body: file })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : `Upload failed (${response.status})`, false)
  return data as Upload
}
export const adjust = (group: string, planId: string, options: Record<string, string>) =>
  call<Interpretation>(`/api/groups/${group}/plans/${planId}/adjust`, { options })
export const execute = (group: string, planId: string, selected: string[]) =>
  call<{ results: string[]; job: JobSummary }>(`/api/groups/${group}/execute`, { plan_id: planId, selected })
export const getJobs = (group?: string) => call<JobList>(group ? `/api/jobs?group=${group}` : '/api/jobs')
export const getJob = (id: string) => call<JobDetail>(`/api/jobs/${id}`)

export type NewsArticle = { link: string; title: string; source: string; published: string | null; from_teaser: boolean; reason: string }
export type NewsStory = { id: string; title: string; summary: string; articles: NewsArticle[] }
export type NewsDigestHead = {
  id: number; made_at: string; covers_from: string; trigger: string; job_id: string
  article_count: number; story_count: number; source_count: number; problems: string[]
}
export type NewsDigest = NewsDigestHead & { cost_usd: number; subjects: { subject: string; stories: NewsStory[] }[] }
export type NewsSource = { id: number; site: string; name: string; feed: string; kind: string; last_checked: string; last_result: string }
export type NewsOverview = {
  sources: NewsSource[]; subjects: string[]; suggestions: { name: string; examples: string[] }[]
  digests: NewsDigestHead[]; cap_usd: number; running: boolean; failure: string
}
export const getNewsOverview = () => call<NewsOverview>('/api/news/overview')
export const getNewsDigest = (id: number) => call<NewsDigest>(`/api/news/digests/${id}`)
export const answerSuggestion = (name: string, accept: boolean) =>
  call<{ ok: boolean }>(`/api/news/suggestions/${encodeURIComponent(name)}`, { accept })
export const saveSubjects = (names: string[]) => call<{ subjects: string[] }>('/api/news/subjects', { names })
export const setNewsCap = (usd: number) => call<{ cap_usd: number }>('/api/news/cap', { usd })
export const makeNewsDigest = () => call<{ started: boolean }>('/api/news/make', {})
export async function removeNewsSource(id: number): Promise<void> {
  await fetch(`/api/news/sources/${id}`, { method: 'DELETE' })
}

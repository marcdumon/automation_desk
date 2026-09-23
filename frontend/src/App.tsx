import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { Link, Redirect, Route, Switch, useRoute } from 'wouter'

import { getAuth, getGroups, getVersion, login, type Group } from './api'
import GroupPage, { type LogEntry } from './GroupPage'
import JobsPage from './JobsPage'
import Reminders from './Reminders'

export default function App() {
  const groups = useQuery({ queryKey: ['groups'], queryFn: getGroups })
  // CLAUDE> one log per group, kept here so switching pages does not lose it; a page only ever receives its own
  const [logs, setLogs] = useState<Record<string, LogEntry[]>>({})
  const append = useCallback((group: string, entry: LogEntry) =>
    setLogs(current => ({ ...current, [group]: [entry, ...(current[group] ?? [])] })), [])

  // CLAUDE> the build this page was loaded with; a newer one on the server means this page runs old code
  const version = useQuery({ queryKey: ['version'], queryFn: getVersion, refetchInterval: 15000, refetchOnWindowFocus: true })
  const loadedBuild = useRef<string | null>(null)
  if (version.data && loadedBuild.current === null) loadedBuild.current = version.data.build
  const outdated = Boolean(version.data && loadedBuild.current !== null && version.data.build !== loadedBuild.current)

  if (groups.isPending) return <div className="boot">Loading…</div>
  if (groups.isError) return <div className="boot">The app server is not reachable: {groups.error.message}</div>

  return (
    <div className="shell">
      <Sidebar groups={groups.data} />
      <main className="main">
        {outdated && (
          <div className="message outdated" role="alert">
            <p>The app was updated. Reload this page to use the new version.</p>
            <button type="button" className="primary" onClick={() => window.location.reload()}>Reload</button>
          </div>
        )}
        <Switch>
          {groups.data.map(group => (
            <Route key={group.id} path={`/${group.id}`}>
              <GroupPage key={group.id} group={group} log={logs[group.id] ?? []} onLog={entry => append(group.id, entry)} />
            </Route>
          ))}
          <Route path="/jobs"><JobsPage /></Route>
          <Route><Redirect to={`/${groups.data[0]?.id ?? ''}`} /></Route>
        </Switch>
      </main>
    </div>
  )
}

function Sidebar({ groups }: { groups: Group[] }) {
  return (
    <nav className="sidebar" aria-label="Task groups">
      <div className="brand">Automation desk</div>
      <ul className="group-list">
        {groups.map(group => <GroupLink key={group.id} group={group} />)}
      </ul>
      <JobsLink />
      <Reminders />
      <GoogleStatus />
    </nav>
  )
}

function GroupLink({ group }: { group: Group }) {
  const [active] = useRoute(`/${group.id}`)
  return (
    <li>
      <Link href={`/${group.id}`} className={`group-link accent-${group.id}${active ? ' active' : ''}`}
            aria-current={active ? 'page' : undefined}>
        <span className="swatch" aria-hidden="true" />
        <span>{group.name}</span>
      </Link>
    </li>
  )
}

function JobsLink() {
  const [active] = useRoute('/jobs')
  return (
    <Link href="/jobs" className={`group-link jobs-link${active ? ' active' : ''}`} aria-current={active ? 'page' : undefined}>
      Jobs &amp; costs
    </Link>
  )
}

function GoogleStatus() {
  const queryClient = useQueryClient()
  const auth = useQuery({ queryKey: ['auth'], queryFn: getAuth })
  const signIn = useMutation({ mutationFn: login, onSettled: () => queryClient.invalidateQueries({ queryKey: ['auth'] }) })
  if (!auth.data) return null
  return (
    <div className="google-status">
      <span className={auth.data.ok ? 'ok' : 'bad'}>{auth.data.ok ? 'Google connected' : auth.data.message}</span>
      {!auth.data.ok && (
        <button type="button" className="quiet" onClick={() => signIn.mutate()} disabled={signIn.isPending}>
          {signIn.isPending ? 'Waiting for the browser…' : 'Log in to Google'}
        </button>
      )}
      {signIn.isError && <span className="bad">{signIn.error.message}</span>}
    </div>
  )
}

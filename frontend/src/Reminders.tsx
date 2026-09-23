import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { getReminders, reminderDone, snoozeReminder, type Reminder } from './api'
import { when } from './format'

// CLAUDE> due reminders pop up once per page load; the sidebar entry opens them any time

function Linked({ text }: { text: string }) {
  return (
    <>
      {text.split(/(https?:\/\/\S+)/).map((part, i) => (
        /^https?:\/\//.test(part) ? <a key={i} href={part} target="_blank" rel="noreferrer">{part}</a> : <span key={i}>{part}</span>
      ))}
    </>
  )
}

function ReminderDialog({ reminders, onClose }: { reminders: Reminder[]; onClose: () => void }) {
  const queryClient = useQueryClient()
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal() }, [])
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['reminders'] })
  const snooze = useMutation({ mutationFn: ({ id, days }: { id: string; days: number }) => snoozeReminder(id, days), onSuccess: refresh })
  const finish = useMutation({ mutationFn: (id: string) => reminderDone(id), onSuccess: refresh })
  useEffect(() => { if (reminders.length === 0) dialog.current?.close() }, [reminders.length])

  return (
    <dialog ref={dialog} className="reminder-dialog" onClose={onClose} aria-labelledby="reminder-title">
      <h2 id="reminder-title">{reminders.length === 1 ? 'Reminder' : `${reminders.length} reminders`}</h2>
      {reminders.map(r => (
        <section key={r.id} className="reminder">
          <h3>{r.title}</h3>
          <ol>{r.steps.map((step, i) => <li key={i}><Linked text={step} /></li>)}</ol>
          <p className="muted">Added {when(r.created)}{r.snoozed_until && !r.due ? `, snoozed until ${when(r.snoozed_until)}` : ''}.</p>
          <div className="reminder-actions">
            <button type="button" className="quiet" onClick={() => snooze.mutate({ id: r.id, days: 1 })}>Remind me tomorrow</button>
            <button type="button" className="quiet" onClick={() => snooze.mutate({ id: r.id, days: 2 })}>Remind me in 2 days</button>
            <button type="button" className="quiet" onClick={() => finish.mutate(r.id)}>I did it</button>
          </div>
        </section>
      ))}
      <div className="reminder-actions"><button type="button" className="primary" onClick={() => dialog.current?.close()}>Close</button></div>
    </dialog>
  )
}

export default function Reminders() {
  const reminders = useQuery({ queryKey: ['reminders'], queryFn: getReminders, refetchInterval: 60000 })
  const [open, setOpen] = useState(false)
  const [poppedUp, setPoppedUp] = useState(false)
  const all = reminders.data ?? []
  const due = all.filter(r => r.due)
  useEffect(() => {
    if (!poppedUp && due.length > 0) { setOpen(true); setPoppedUp(true) }
  }, [due.length, poppedUp])
  if (all.length === 0) return null
  return (
    <>
      <button type="button" className="reminders-link" onClick={() => setOpen(true)}>
        Reminders ({all.length})
      </button>
      {open && <ReminderDialog reminders={all} onClose={() => setOpen(false)} />}
    </>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { addHabit, changeHabit, checkHabit, deleteHabit, getHabits, setHabitReminder, type Habit, type HabitsView } from './api'

// CLAUDE> the user's own habit tracker, outside Todoist: one check-in (made for the evening), streaks and a month grid
const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
const DAY_NAMES: Record<string, string> = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' }

const longDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
const shift = (day: string, days: number) => {
  const d = new Date(`${day}T12:00:00`)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

export function rhythm(schedule: string): string {
  if (schedule === 'daily') return 'Every day'
  if (schedule === 'weekdays') return 'Weekdays'
  if (schedule.startsWith('weekly:')) return `${schedule.slice(7)}× a week`
  return schedule.slice(5).split(',').map(d => DAY_NAMES[d] ?? d).join(', ')
}

export default function HabitsPage() {
  const client = useQueryClient()
  const [day, setDay] = useState<string | null>(null)
  const view = useQuery({ queryKey: ['habits', day], queryFn: () => getHabits(day ?? undefined), placeholderData: previous => previous })
  const keep = (data: HabitsView) => client.setQueryData(['habits', day], data)
  const tick = useMutation({ mutationFn: ({ id, done }: { id: number; done: boolean }) => checkHabit(id, view.data!.day, done),
                             onSuccess: keep })
  if (view.isError) return <section className="page accent-habits"><p className="cap-error">{view.error.message}</p></section>
  if (!view.data) return null
  const data = view.data
  const isToday = data.day === data.today_date
  const done = data.today.filter(h => h.done).length
  return (
    <section className="page accent-habits">
      <header className="page-head"><h1>Habits</h1></header>
      <div className="habits">
        <article className="habit-card checkin">
          <div className="checkin-head">
            <button type="button" className="quiet" aria-label="Previous day" onClick={() => setDay(shift(data.day, -1))}>‹</button>
            <div>
              <h2>{isToday ? 'Today' : longDay(data.day)}</h2>
              <p className="muted">{isToday ? longDay(data.day) : 'catching up a day you missed'} · {done} of {data.today.length} done</p>
            </div>
            <button type="button" className="quiet" aria-label="Next day" disabled={isToday}
                    onClick={() => setDay(shift(data.day, 1) === data.today_date ? null : shift(data.day, 1))}>›</button>
          </div>
          {data.today.length === 0
            ? <p className="habits-empty">{data.habits.length ? 'Nothing due this day.' : 'Add your first habit below.'}</p>
            : (
              <ul className="checkin-list">
                {data.today.map(h => (
                  <li key={h.id}>
                    <button type="button" className={`habit-tick${h.done ? ' done' : ''}`} aria-pressed={h.done}
                            disabled={tick.isPending} onClick={() => tick.mutate({ id: h.id, done: !h.done })}>
                      <span className="tick-mark" aria-hidden="true">{h.done ? '✓' : ''}</span>
                      <span className="habit-name">{h.name}</span>
                      {h.streak > 0 && <span className="habit-streak">🔥 {h.streak}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          {tick.isError && <p className="cap-error">{tick.error.message}</p>}
        </article>
        {data.habits.length > 0 && <Overview habits={data.habits} />}
        <Manage data={data} onChange={keep} />
      </div>
    </section>
  )
}

const STATE_NAMES = { done: 'done', missed: 'missed', open: 'still open', free: 'not due', future: 'to come' }

function Overview({ habits }: { habits: Habit[] }) {
  const month = new Date(`${habits[0].days[0].day}T12:00:00`).toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })
  return (
    <article className="habit-card">
      <h2>{month}</h2>
      <div className="habit-table-wrap">
        <table className="habit-table">
          <thead>
            <tr><th>Habit</th><th>Streak</th><th>Best</th><th>This month</th><th className="grid-head">Days</th></tr>
          </thead>
          <tbody>
            {habits.map(h => (
              <tr key={h.id} className={h.paused ? 'paused' : ''}>
                <td><span className="habit-name">{h.name}</span><small className="muted">{h.paused ? 'paused' : rhythm(h.schedule)}</small></td>
                <td className="num">{h.streak ? `🔥 ${h.streak}` : '—'}</td>
                <td className="num">{h.best || '—'}</td>
                <td className="num">{h.month_pct === null ? '—' : `${h.month_pct}%`}</td>
                <td>
                  <span className="habit-grid">
                    {h.days.map(d => (
                      <i key={d.day} className={`cell ${d.state}`}
                         title={`${new Date(`${d.day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric' })}: ${STATE_NAMES[d.state]}`} />
                    ))}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="habit-legend">
        {(['done', 'missed', 'open', 'free'] as const).map(s => <li key={s}><i className={`cell ${s}`} />{STATE_NAMES[s]}</li>)}
      </ul>
    </article>
  )
}

function RhythmPicker({ value, onChange }: { value: string; onChange: (schedule: string) => void }) {
  const kind = value.startsWith('days:') ? 'days' : value.startsWith('weekly:') ? 'weekly' : value
  const days = value.startsWith('days:') ? value.slice(5).split(',') : []
  const times = value.startsWith('weekly:') ? value.slice(7) : '3'
  return (
    <span className="rhythm">
      <select value={kind} aria-label="Rhythm" onChange={e => {
        const k = e.target.value
        onChange(k === 'days' ? 'days:mon,wed,fri' : k === 'weekly' ? `weekly:${times}` : k)
      }}>
        <option value="daily">Every day</option>
        <option value="weekdays">Weekdays</option>
        <option value="days">Set days</option>
        <option value="weekly">Times a week</option>
      </select>
      {kind === 'days' && (
        <span className="choices" role="group" aria-label="Days">
          {WEEKDAYS.map(d => {
            const on = days.includes(d)
            const next = on ? days.filter(x => x !== d) : WEEKDAYS.filter(x => x === d || days.includes(x))
            return (
              <button key={d} type="button" className={on ? 'choice chosen' : 'choice'} aria-pressed={on}
                      disabled={on && days.length === 1} onClick={() => onChange(`days:${next.join(',')}`)}>{DAY_NAMES[d]}</button>
            )
          })}
        </span>
      )}
      {kind === 'weekly' && (
        <select value={times} aria-label="Times a week" onChange={e => onChange(`weekly:${e.target.value}`)}>
          {[1, 2, 3, 4, 5, 6].map(n => <option key={n} value={n}>{n}× a week</option>)}
        </select>
      )}
    </span>
  )
}

function Manage({ data, onChange }: { data: HabitsView; onChange: (data: HabitsView) => void }) {
  const [name, setName] = useState('')
  const [schedule, setSchedule] = useState('daily')
  const [reminder, setReminder] = useState(data.reminder)
  const add = useMutation({ mutationFn: () => addHabit(name, schedule), onSuccess: d => { onChange(d); setName('') } })
  const change = useMutation({ mutationFn: ({ id, fields }: { id: number; fields: Parameters<typeof changeHabit>[1] }) =>
    changeHabit(id, fields), onSuccess: onChange })
  const remove = useMutation({ mutationFn: deleteHabit, onSuccess: onChange })
  const saveReminder = useMutation({ mutationFn: () => setHabitReminder(reminder), onSuccess: onChange })
  const failed = add.error ?? change.error ?? remove.error ?? saveReminder.error
  return (
    <details className="habit-card manage" open={data.habits.length === 0}>
      <summary><h2>Edit habits</h2></summary>
      <ul className="manage-list">
        {data.habits.map(h => (
          <li key={h.id}>
            <input defaultValue={h.name} aria-label="Habit name"
                   onBlur={e => e.target.value.trim() && e.target.value !== h.name && change.mutate({ id: h.id, fields: { name: e.target.value } })} />
            <RhythmPicker value={h.schedule} onChange={s => change.mutate({ id: h.id, fields: { schedule: s } })} />
            <button type="button" className="quiet" onClick={() => change.mutate({ id: h.id, fields: { paused: !h.paused } })}>
              {h.paused ? 'Resume' : 'Pause'}</button>
            <button type="button" className="quiet danger" aria-label={`Delete ${h.name}`} onClick={() => remove.mutate(h.id)}>Delete</button>
          </li>
        ))}
      </ul>
      <form className="manage-add" onSubmit={e => { e.preventDefault(); if (name.trim()) add.mutate() }}>
        <input value={name} placeholder="New habit, e.g. Read: 20 pages" aria-label="New habit" onChange={e => setName(e.target.value)} />
        <RhythmPicker value={schedule} onChange={setSchedule} />
        <button type="submit" className="primary" disabled={!name.trim() || add.isPending}>Add</button>
      </form>
      <label className="reminder">Daily reminder
        <input value={reminder} placeholder="21:00" aria-label="Reminder time" onChange={e => setReminder(e.target.value)}
               onBlur={() => reminder !== data.reminder && saveReminder.mutate()} />
        <span className="muted">{reminder ? 'a desktop notice at this time while habits are open' : 'empty: no reminder'}</span>
      </label>
      {failed && <p className="cap-error">{failed.message}</p>}
    </details>
  )
}

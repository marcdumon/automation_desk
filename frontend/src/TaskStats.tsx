import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'

import { getTaskStats, type StatDay, type TaskStats as Stats } from './api'

// CLAUDE> the Tasks page's statistics: a count of open tasks every morning (from 07:00), done and added per day from
// Todoist; charts are plain SVG in the app's two validated series colours (--series-1 blue, --series-2 orange)
const PERIODS: [number, string][] = [[30, '30 days'], [90, '90 days'], [0, 'All']]
const HORIZON_NAMES: Record<string, string> = {
  this_week: 'This week', this_month: 'This month', this_year: 'This year', next_year: 'Next year', someday: 'Someday',
}

const shortDay = (day: string) => new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
const longDay = (day: string) =>
  new Date(`${day}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })

const OPEN_KEY = 'task-stats-open'

// CLAUDE> folded by default so the Tasks page's commands stay in reach; the choice is remembered in this browser
function rememberedOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === '1'
  } catch {
    return false
  }
}

export default function TaskStats() {
  const [open, setOpen] = useState(rememberedOpen)
  const toggle = (next: boolean) => {
    setOpen(next)
    try {
      localStorage.setItem(OPEN_KEY, next ? '1' : '0')
    } catch {
      // CLAUDE> private windows refuse storage; the section then simply starts folded
    }
  }
  const [period, setPeriod] = useState(30)
  const stats = useQuery({ queryKey: ['task-stats', period], queryFn: () => getTaskStats(period), placeholderData: previous => previous })
  if (stats.isError) return <p className="cap-error">Statistics not available: {stats.error.message}</p>
  if (!stats.data) return null
  const data = stats.data
  const k = data.kpis
  return (
    <details className={`stats${stats.isFetching ? ' refreshing' : ''}`} open={open}
             onToggle={e => toggle((e.currentTarget as HTMLDetailsElement).open)}>
      <summary className="stats-summary">
        <h2>Your tasks over time</h2>
        <span className="stats-glance">
          {k.open.now ?? '—'} open · {k.done_7d} done this week · {k.frogs_month} {k.frogs_month === 1 ? 'frog' : 'frogs'} this month
        </span>
      </summary>
      <div className="stats-head">
        <span className="choices" role="radiogroup" aria-label="Period">
          {PERIODS.map(([days, label]) => (
            <button key={days} type="button" role="radio" aria-checked={period === days}
                    className={period === days ? 'choice chosen' : 'choice'} onClick={() => setPeriod(days)}>{label}</button>
          ))}
        </span>
      </div>
      {!data.since && (
        <p className="stats-note">The first count of your open tasks is made the first time the app runs after 07:00. The
          trend of open tasks starts from there; done and added per day are read back from Todoist.</p>
      )}
      <Kpis data={data} />
      <div className="stats-grid">
        <Card title="Open tasks each morning" wide>
          <OpenChart series={data.series} />
        </Card>
        <Card title="Done and added per day" wide>
          <DoneAddedChart series={data.series} />
        </Card>
        <Card title="Planned tasks done each day" wide>
          <PlanChart series={data.series} />
        </Card>
        <Card title="Done per weekday, on average">
          <BarTable rows={data.weekdays.map(w => ({ label: w.label, value: w.average ?? 0,
                                                    note: w.average === null ? 'no data yet' : undefined }))} />
        </Card>
        <Card title="Plan finished per weekday">
          <BarTable percent rows={data.weekdays.map(w => ({ label: w.label, value: w.plan_pct ?? 0,
                                                            note: w.plan_pct === null ? 'no plan yet' : undefined }))} />
        </Card>
        <Card title="Open per project">
          <BarTable rows={data.projects.map(p => ({ label: p.project, value: p.open, note: change(p.change) }))} />
        </Card>
        <Card title="Open per horizon">
          <BarTable rows={data.horizons.map(h => ({ label: HORIZON_NAMES[h.label] ?? h.label, value: h.open }))} />
        </Card>
      </div>
    </details>
  )
}

const change = (value: number) => (value === 0 ? '' : value > 0 ? `+${value}` : `${value}`)

function Kpis({ data }: { data: Stats }) {
  const open = data.kpis.open
  const vsWeek = open.now !== null && open.week_ago !== null ? open.now - open.week_ago : null
  const cleanup = data.cleanup
  const plan = data.kpis.plan_7d
  return (
    <div className="kpis">
      <Tile label="Open now" value={open.now ?? '—'}
            delta={vsWeek === null ? undefined : `${vsWeek > 0 ? '+' : ''}${vsWeek} vs a week ago`}
            good={vsWeek === null ? undefined : vsWeek <= 0} />
      <Tile label="Done, last 7 days" value={data.kpis.done_7d} />
      <Tile label="Added, last 7 days" value={data.kpis.added_7d}
            delta={`${data.kpis.done_7d - data.kpis.added_7d >= 0 ? 'more done than added' : 'more added than done'}`}
            good={data.kpis.done_7d >= data.kpis.added_7d} />
      <Tile label="Plan done, last 7 days"
            value={plan.planned ? `${Math.round((100 * plan.done) / plan.planned)}%` : '—'}
            delta={plan.planned ? `${plan.done} of ${plan.planned} planned tasks` : 'counted from tomorrow morning'} />
      <Tile label="Frog days this month" value={`${data.kpis.frogs_month} / ${data.kpis.days_month}`} />
      {cleanup && (
        <Tile label={`${cleanup.project} empty`}
              value={cleanup.empty_on ? shortDay(cleanup.empty_on) : '—'}
              delta={cleanup.empty_on ? `${cleanup.open} left, ${cleanup.per_day} a day` : `${cleanup.open} left, no pace yet`} />
      )}
    </div>
  )
}

function Tile({ label, value, delta, good }: { label: string; value: ReactNode; delta?: string; good?: boolean }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      {delta && <span className={`tile-delta${good === undefined ? '' : good ? ' good' : ' bad'}`}>{delta}</span>}
    </div>
  )
}

function Card({ title, wide, children }: { title: string; wide?: boolean; children: ReactNode }) {
  return <figure className={`stats-card${wide ? ' wide' : ''}`}><figcaption>{title}</figcaption>{children}</figure>
}

// CLAUDE> the chart's drawing width follows its box, so text never stretches
function useWidth(): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(600)
  useEffect(() => {
    if (!ref.current) return
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.round(entry.contentRect.width))))
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])
  return [ref, width]
}

const PAD = { left: 36, right: 16, top: 12, bottom: 28 }
const HEIGHT = 220

function niceMax(value: number): number {
  if (value <= 5) return 5
  const step = 10 ** Math.floor(Math.log10(value))
  return Math.ceil(value / step) * step
}

function Axes({ width, max, days, labelsOnly }: { width: number; max: number; days: string[]; labelsOnly?: boolean }) {
  const ticks = labelsOnly ? [] : [0, max / 2, max]
  const plotW = width - PAD.left - PAD.right
  const every = Math.max(1, Math.ceil(days.length / Math.floor(plotW / 70)))
  return (
    <g className="axes">
      {ticks.map(t => {
        const y = PAD.top + (HEIGHT - PAD.top - PAD.bottom) * (1 - t / max)
        return (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={y} y2={y} className="grid" />
            <text x={PAD.left - 6} y={y + 4} textAnchor="end">{Math.round(t)}</text>
          </g>
        )
      })}
      {days.map((day, i) => i % every === 0 && (
        <text key={day} x={PAD.left + (plotW * (i + 0.5)) / days.length} y={HEIGHT - 8} textAnchor="middle">{shortDay(day)}</text>
      ))}
    </g>
  )
}

type Tip = { x: number; y: number; title: string; rows: { key: string; value: string; kind: string }[] }

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }} role="status">
      <strong>{tip.title}</strong>
      {tip.rows.map(r => <span key={r.key}><i className={`key ${r.kind}`} /><b>{r.value}</b> {r.key}</span>)}
    </div>
  )
}

function Legend({ items }: { items: { label: string; kind: string; line?: boolean }[] }) {
  return (
    <ul className="chart-legend">
      {items.map(i => <li key={i.label}><i className={`key ${i.kind}${i.line ? ' line' : ''}`} />{i.label}</li>)}
    </ul>
  )
}

function OpenChart({ series }: { series: StatDay[] }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  if (!series.some(s => s.open !== null)) {
    return <p className="stats-empty">No morning counts yet: the first one is made after 07:00.</p>
  }
  const max = niceMax(Math.max(...series.map(s => s.open ?? 0)))
  const plotW = width - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (plotW * (i + 0.5)) / series.length
  const y = (v: number) => PAD.top + plotH * (1 - v / max)
  // CLAUDE> a morning without a count leaves a gap rather than a made-up line
  const path = (key: 'open' | 'overdue') => series.reduce((d, s, i) => {
    const v = s[key]
    if (v === null) return d
    const previous = i > 0 ? series[i - 1][key] : null
    return `${d}${previous === null ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
  }, '')
  const last = [...series].reverse().find(s => s.open !== null)!
  const lastIndex = series.lastIndexOf(last)
  const pick = (clientX: number, box: DOMRect) => {
    const i = Math.round(((clientX - box.left - PAD.left) / plotW) * series.length - 0.5)
    setHover(Math.min(series.length - 1, Math.max(0, i)))
  }
  const h = hover === null ? null : series[hover]
  return (
    <div className="chart" ref={ref}>
      <Legend items={[{ label: 'Open', kind: 's1', line: true }, { label: 'Overdue', kind: 's2', line: true }]} />
      <svg width={width} height={HEIGHT} role="img" aria-label="Open and overdue tasks each morning"
           onPointerMove={e => pick(e.clientX, e.currentTarget.getBoundingClientRect())} onPointerLeave={() => setHover(null)}>
        <Axes width={width} max={max} days={series.map(s => s.day)} />
        {hover !== null && <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} />}
        <path d={path('overdue')} className="line s2" />
        <path d={path('open')} className="line s1" />
        <circle cx={x(lastIndex)} cy={y(last.open!)} r={4} className="dot s1" />
        <text x={x(lastIndex) - 8} y={y(last.open!) - 10} textAnchor="end" className="end-label">{last.open}</text>
      </svg>
      <Tooltip tip={h && h.open !== null ? {
        x: Math.min(x(hover!) + 12, width - 150), y: 24, title: longDay(h.day),
        rows: [{ key: 'open', value: String(h.open), kind: 's1' }, { key: 'overdue', value: String(h.overdue ?? 0), kind: 's2' }],
      } : null} />
    </div>
  )
}

function DoneAddedChart({ series }: { series: StatDay[] }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  const max = niceMax(Math.max(1, ...series.flatMap(s => [s.completed, s.added])))
  const plotW = width - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const band = plotW / series.length
  const bar = Math.max(2, Math.min(12, (band - 4) / 2))
  const y = (v: number) => PAD.top + plotH * (1 - v / max)
  const column = (cx: number, v: number, kind: string) => {
    if (v <= 0) return null
    const top = y(v)
    const h = PAD.top + plotH - top
    const r = Math.min(4, bar / 2, h)
    // CLAUDE> rounded at the data end, square at the baseline
    return <path className={`bar ${kind}`} d={`M${cx},${PAD.top + plotH}V${top + r}q0,${-r} ${r},${-r}h${bar - 2 * r}q${r},0 ${r},${r}V${PAD.top + plotH}Z`} />
  }
  const h = hover === null ? null : series[hover]
  return (
    <div className="chart" ref={ref}>
      <Legend items={[{ label: 'Done', kind: 's1' }, { label: 'Added', kind: 's2' }]} />
      <svg width={width} height={HEIGHT} role="img" aria-label="Tasks done and added per day" onPointerLeave={() => setHover(null)}>
        <Axes width={width} max={max} days={series.map(s => s.day)} />
        {series.map((s, i) => {
          const left = PAD.left + band * i + (band - (2 * bar + 2)) / 2
          return (
            <g key={s.day} className={hover === i ? 'hovered' : ''} onPointerEnter={() => setHover(i)}>
              <rect className="hit" x={PAD.left + band * i} y={PAD.top} width={band} height={plotH} />
              {column(left, s.completed, 's1')}
              {column(left + bar + 2, s.added, 's2')}
              {s.frog && <text x={left + bar} y={y(Math.max(s.completed, s.added)) - 6} textAnchor="middle" className="frog">🐸</text>}
            </g>
          )
        })}
      </svg>
      <Tooltip tip={h ? {
        x: Math.min(PAD.left + band * (hover! + 1) + 8, width - 150), y: 24, title: longDay(h.day),
        rows: [{ key: 'done', value: String(h.completed), kind: 's1' }, { key: 'added', value: String(h.added), kind: 's2' },
               ...(h.frog ? [{ key: 'frog eaten', value: '🐸', kind: 'none' }] : [])],
      } : null} />
    </div>
  )
}

function BarTable({ rows, percent }: { rows: { label: string; value: number; note?: string }[]; percent?: boolean }) {
  if (rows.length === 0) return <p className="stats-empty">Shown from the first morning count on.</p>
  const max = percent ? 100 : Math.max(...rows.map(r => r.value), 1)
  return (
    <ul className="bar-table">
      {rows.map(r => (
        <li key={r.label}>
          <span className="bar-label" title={r.label}>{r.label}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${(100 * r.value) / max}%` }} /></span>
          <span className="bar-value">{r.note && !r.value ? <small>{r.note}</small> : <>{r.value}{percent && '%'}
            {r.note && <small> {r.note}</small>}</>}</span>
        </li>
      ))}
    </ul>
  )
}

function PlanChart({ series }: { series: StatDay[] }) {
  const [ref, width] = useWidth()
  const [hover, setHover] = useState<number | null>(null)
  if (!series.some(s => s.planned)) {
    return <p className="stats-empty">Counted from tomorrow morning: the tasks dated that day, and how many of them you finish.</p>
  }
  const plotW = width - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const band = plotW / series.length
  const bar = Math.max(3, Math.min(16, band - 4))
  const pct = (s: StatDay) => (s.planned ? Math.round((100 * (s.planned_done ?? 0)) / s.planned) : null)
  const y = (v: number) => PAD.top + plotH * (1 - v / 100)
  const h = hover === null ? null : series[hover]
  return (
    <div className="chart" ref={ref}>
      <svg width={width} height={HEIGHT} role="img" aria-label="Share of each day's planned tasks that got done"
           onPointerLeave={() => setHover(null)}>
        <g className="axes">
          {[0, 50, 100].map(t => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} className="grid" />
              <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end">{t}%</text>
            </g>
          ))}
        </g>
        <Axes width={width} max={100} days={series.map(s => s.day)} labelsOnly />
        {series.map((s, i) => {
          const value = pct(s)
          const left = PAD.left + band * i + (band - bar) / 2
          const top = value === null ? 0 : y(Math.max(value, 1))
          const r = Math.min(4, bar / 2)
          return (
            <g key={s.day} className={hover === i ? 'hovered' : ''} onPointerEnter={() => setHover(i)}>
              <rect className="hit" x={PAD.left + band * i} y={PAD.top} width={band} height={plotH} />
              {value !== null && (
                <path className="bar s1" d={`M${left},${PAD.top + plotH}V${top + r}q0,${-r} ${r},${-r}h${bar - 2 * r}q${r},0 ${r},${r}V${PAD.top + plotH}Z`} />
              )}
            </g>
          )
        })}
      </svg>
      <Tooltip tip={h ? {
        x: Math.min(PAD.left + band * (hover! + 1) + 8, width - 170), y: 24, title: longDay(h.day),
        rows: h.planned ? [{ key: `done of ${h.planned} planned`, value: `${h.planned_done ?? 0}`, kind: 's1' },
                           { key: 'of the plan', value: `${pct(h)}%`, kind: 'none' }]
          : [{ key: h.planned === null ? 'no morning count' : 'nothing planned', value: '—', kind: 'none' }],
      } : null} />
    </div>
  )
}

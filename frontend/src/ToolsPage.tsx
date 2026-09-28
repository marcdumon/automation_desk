import { useMutation } from '@tanstack/react-query'
import { useRef, useState, type DragEvent } from 'react'

import { pdfMargin } from './api'

// CLAUDE> small one-step tasks: no model, no Google account; each tool is a card
export default function ToolsPage() {
  return (
    <section className="page accent-tools">
      <header className="page-head"><h1>Tools</h1></header>
      <div className="tools">
        <PdfMargin />
      </div>
    </section>
  )
}

const SIDES = [['left', 'Left'], ['right', 'Right'], ['both', 'Both']] as const
const stem = (name: string) => name.replace(/\.pdf$/i, '')

function PdfMargin() {
  const [file, setFile] = useState<File | null>(null)
  const [side, setSide] = useState('right')
  const [percent, setPercent] = useState('33')
  const [name, setName] = useState('')
  const [dragging, setDragging] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const share = Number(percent.replace(',', '.'))
  const validShare = percent.trim() !== '' && share >= 1 && share <= 300
  const make = useMutation({
    mutationFn: () => pdfMargin(file!, side, share, name.trim() || stem(file!.name)),
    onSuccess: blob => {
      // CLAUDE> the browser saves it like any download, under the chosen name
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = `${stem(name.trim() || stem(file!.name))}.pdf`
      link.click()
      setTimeout(() => URL.revokeObjectURL(link.href), 10000)
    },
  })
  const choose = (chosen: File | undefined) => {
    if (!chosen) return
    setFile(chosen)
    setName(stem(chosen.name))
    make.reset()
  }
  const drop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    choose(e.dataTransfer.files[0])
  }
  return (
    <article className="tool-card">
      <h2>Add white space to a PDF</h2>
      <p className="muted">Room for notes beside every page; the pages themselves stay as they are.</p>
      <div className="tool-body">
        <button type="button" className={`drop-zone${dragging ? ' dragging' : ''}${file ? ' chosen' : ''}`}
                onClick={() => input.current?.click()} onDragOver={e => { e.preventDefault(); setDragging(true) }}
                onDragLeave={() => setDragging(false)} onDrop={drop}>
          {file ? <><strong>{file.name}</strong><span className="muted">Click to choose another</span></>
            : <><strong>Choose a PDF</strong><span className="muted">or drop it here</span></>}
        </button>
        <input ref={input} type="file" accept="application/pdf,.pdf" hidden onChange={e => choose(e.target.files?.[0])} />
        <div className="tool-settings">
          <div className="tool-fields">
            <label>
              <span className="option-label">Side</span>
              <span className="choices" role="radiogroup" aria-label="Side">
                {SIDES.map(([value, label]) => (
                  <button key={value} type="button" role="radio" aria-checked={side === value}
                          className={side === value ? 'choice chosen' : 'choice'} onClick={() => setSide(value)}>{label}</button>
                ))}
              </span>
            </label>
            <label>
              <span className="option-label">White space</span>
              <span className="percent-field">
                <input value={percent} inputMode="decimal" aria-invalid={!validShare} onChange={e => setPercent(e.target.value)} />
                <span>% of the page width{side === 'both' ? ', each side' : ''}</span>
              </span>
            </label>
            <label>
              <span className="option-label">Save as</span>
              <span className="name-field">
                <input value={name} placeholder="file name" onChange={e => setName(e.target.value)} disabled={!file} />
                <span>.pdf</span>
              </span>
            </label>
          </div>
          {!validShare && <p className="cap-error">Type a number from 1 to 300.</p>}
          <div className="tool-actions">
            <button type="button" className="primary" disabled={!file || !validShare || make.isPending} onClick={() => make.mutate()}>
              {make.isPending ? 'Making…' : 'Make PDF'}</button>
            {make.isSuccess && <span className="tool-done">✓ Downloaded as {stem(name.trim() || stem(file!.name))}.pdf</span>}
            {make.isError && <span className="cap-error">{make.error.message}</span>}
          </div>
        </div>
      </div>
    </article>
  )
}

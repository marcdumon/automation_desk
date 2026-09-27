import { useEffect, useState } from 'react'

import { stopAction } from './api'

// CLAUDE> shown next to a long action while it runs; the action stops after the step it is on and keeps what it has done
export default function StopButton({ action, running }: { action: string; running: boolean }) {
  const [asked, setAsked] = useState(false)
  useEffect(() => { if (!running) setAsked(false) }, [running])
  if (!running) return null
  return (
    <button type="button" className="quiet stop-button" disabled={asked}
            onClick={() => { setAsked(true); stopAction(action).catch(() => setAsked(false)) }}>
      {asked ? 'Stopping…' : 'Stop'}
    </button>
  )
}

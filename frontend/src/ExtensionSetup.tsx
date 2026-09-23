import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getExtensionSetup } from './api'

// CLAUDE> shown when a page must be read through this browser and the reader extension is missing: every step, no hunting

function CopyField({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <span className="copy-field">
      <code>{value}</code>
      <button type="button" className="quiet" aria-label={`Copy ${label}`} onClick={() => {
        navigator.clipboard.writeText(value).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })
      }}>{copied ? 'Copied' : 'Copy'}</button>
    </span>
  )
}

export default function ExtensionSetup({ message, onReload }: { message: string; onReload: () => void }) {
  const setup = useQuery({ queryKey: ['extension-setup'], queryFn: getExtensionSetup })
  return (
    <div className="setup" role="alert">
      <h2>One-time setup: let this browser read pages for the app</h2>
      <p>{message} This takes about a minute and is needed only once.</p>
      <ol>
        <li>Open a new tab, paste this address and press Enter: <CopyField value="vivaldi://extensions" label="extensions address" /></li>
        <li>Switch on <strong>Developer mode</strong> (top right of that page).</li>
        <li>Click <strong>Load unpacked</strong> and choose this folder:{' '}
          {setup.data ? <CopyField value={setup.data.folder} label="folder" /> : <span className="muted">loading…</span>}
          <br /><span className="muted">In the folder picker you can paste the path (Ctrl+L), then click Select.</span>
        </li>
        <li>Come back to this tab and click <strong>Reload and try again</strong>. Your sentence is kept.</li>
      </ol>
      <button type="button" className="primary" onClick={onReload}>Reload and try again</button>
    </div>
  )
}

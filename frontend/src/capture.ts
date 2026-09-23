// CLAUDE> while a job runs, hand the app's page requests to the Automation desk extension in this browser

type PageAnswer = { url?: string; html?: string; asked_you?: boolean; error?: string; missing_extension?: boolean }

function readThroughExtension(requestId: string, url: string): Promise<PageAnswer> {
  if (document.documentElement.dataset.automationBridge !== 'ready') return Promise.resolve({ missing_extension: true })
  return new Promise(resolve => {
    const listener = (event: MessageEvent) => {
      if (event.source !== window || event.data?.type !== 'automation-desk:page' || event.data.requestId !== requestId) return
      window.removeEventListener('message', listener)
      const { url: finalUrl, html, asked_you, error } = event.data as PageAnswer
      resolve({ url: finalUrl, html, asked_you, error })
    }
    window.addEventListener('message', listener)
    window.postMessage({ type: 'automation-desk:read', requestId, url }, window.location.origin)
  })
}

async function serveOnce(onReading: (reading: boolean) => void): Promise<void> {
  const response = await fetch('/api/capture/pending')
  if (!response.ok) return
  const requests: { id: string; url: string }[] = await response.json()
  if (requests.length === 0) return
  onReading(true)
  await Promise.all(requests.map(async ({ id, url }) => {
    const answer = await readThroughExtension(id, url)
    await fetch(`/api/capture/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(answer),
    })
  }))
  onReading(false)
}

// CLAUDE> returns a stop function; polling is cheap, it only talks to the local app. `onReading` says when a page is
// being read through this browser, so the page can explain the tab that opens.
export function servePageRequests(onReading: (reading: boolean) => void): () => void {
  let stopped = false
  const loop = async () => {
    while (!stopped) {
      await serveOnce(onReading).catch(() => onReading(false))
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
  }
  loop()
  return () => { stopped = true }
}

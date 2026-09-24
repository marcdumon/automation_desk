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

// CLAUDE> each request is read on its own: while one site loads in the browser, new ones are taken at once (the app gives up
// on a request nobody takes within 15 s)
async function takeRequests(inFlight: Set<string>, onReading: (reading: boolean) => void): Promise<void> {
  const response = await fetch('/api/capture/pending')
  if (!response.ok) return
  const requests: { id: string; url: string }[] = await response.json()
  for (const { id, url } of requests) {
    inFlight.add(id)
    onReading(true)
    readThroughExtension(id, url)
      .then(answer => fetch(`/api/capture/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(answer),
      }))
      .catch(() => undefined)
      .finally(() => {
        inFlight.delete(id)
        if (inFlight.size === 0) onReading(false)
      })
  }
}

// CLAUDE> returns a stop function; polling is cheap, it only talks to the local app. `onReading` says when a page is
// being read through this browser, so the page can explain the tab that opens.
export function servePageRequests(onReading: (reading: boolean) => void): () => void {
  let stopped = false
  const inFlight = new Set<string>()
  const loop = async () => {
    while (!stopped) {
      await takeRequests(inFlight, onReading).catch(() => undefined)
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
  }
  loop()
  return () => { stopped = true }
}

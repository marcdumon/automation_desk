// CLAUDE> runs only on the Automation desk page: passes its page requests to the background script and the answers back
document.documentElement.dataset.automationBridge = 'ready'

window.addEventListener('message', event => {
  if (event.source !== window || event.data?.type !== 'automation-desk:read') return
  const { requestId, url } = event.data
  chrome.runtime.sendMessage({ type: 'read', url }, answer => {
    const result = answer ?? { error: chrome.runtime.lastError?.message ?? 'The extension did not answer.' }
    window.postMessage({ type: 'automation-desk:page', requestId, ...result }, window.location.origin)
  })
})

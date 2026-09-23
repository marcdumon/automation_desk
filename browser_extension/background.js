// CLAUDE> opens the page in a background tab of this browser, waits for it, returns its HTML and closes the tab.
// If the site keeps showing a human check, the tab is brought to the front for the user; this script never clicks it.

const CHALLENGE_TITLES = ['just a moment', 'attention required', 'one moment', 'checking your browser', 'performing security verification']
const QUIET_WAIT_MS = 8000
const HUMAN_WAIT_MS = 180000
const LOAD_WAIT_MS = 45000
const SETTLE_MS = 2500

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

async function waitLoaded(tabId) {
  const deadline = Date.now() + LOAD_WAIT_MS
  while ((await chrome.tabs.get(tabId)).status !== 'complete') {
    if (Date.now() > deadline) throw new Error('The page did not finish loading within 45 s.')
    await sleep(500)
  }
}

async function challenged(tabId) {
  const title = ((await chrome.tabs.get(tabId)).title || '').toLowerCase()
  return CHALLENGE_TITLES.some(t => title.includes(t))
}

async function read(url, guiTab) {
  const tab = await chrome.tabs.create({ url, active: false, windowId: guiTab.windowId, index: guiTab.index + 1 })
  let askedYou = false
  try {
    await waitLoaded(tab.id)
    const started = Date.now()
    while (await challenged(tab.id)) {
      if (!askedYou && Date.now() - started > QUIET_WAIT_MS) {
        askedYou = true
        await chrome.tabs.update(tab.id, { active: true })
      }
      if (Date.now() - started > HUMAN_WAIT_MS) throw new Error(`${url} asked to verify that a person is visiting, and the check was not completed within 3 minutes.`)
      await sleep(1000)
    }
    await waitLoaded(tab.id)
    // CLAUDE> agenda widgets often fill in after the first render
    await sleep(SETTLE_MS)
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => [location.href, document.documentElement.outerHTML],
    })
    return { url: result[0], html: result[1], asked_you: askedYou }
  } finally {
    await chrome.tabs.remove(tab.id).catch(() => {})
    if (askedYou) await chrome.tabs.update(guiTab.id, { active: true }).catch(() => {})
  }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message?.type !== 'read' || !sender.tab) return false
  read(message.url, sender.tab).then(reply, error => reply({ error: String(error.message || error) }))
  return true
})

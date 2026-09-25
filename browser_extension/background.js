// CLAUDE> opens the page in a background tab of this browser, waits for it, returns its HTML and closes the tab.
// If the site keeps showing a human check, the tab is brought to the front for the user; this script never clicks it.

const CHALLENGE_TITLES = ['just a moment', 'attention required', 'one moment', 'checking your browser', 'performing security verification',
  'are you a robot', 'access denied']
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

// CLAUDE> services whose check pages ask for a person; DataDome's page has no telling title (the Economist's is 'economist.com').
// A real page may load one of them too (a form's reCaptcha), but is large.
const CHECK_SERVICES = ['captcha-delivery.com', 'challenges.cloudflare.com', 'hcaptcha.com', 'recaptcha', 'px-captcha', 'perimeterx']
const CHECK_PAGE_MAX = 60000

async function challenged(tabId) {
  const title = ((await chrome.tabs.get(tabId)).title || '').toLowerCase()
  if (CHALLENGE_TITLES.some(t => title.includes(t))) return true
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId }, func: () => document.documentElement.outerHTML })
    const html = (result || '').toLowerCase()
    return html.length < CHECK_PAGE_MAX && CHECK_SERVICES.some(service => html.includes(service))
  } catch {
    return false
  }
}

// CLAUDE> `mayAsk` false (news): the tab never comes forward; a human check that does not pass by itself is reported instead
async function read(url, guiTab, mayAsk) {
  const tab = await chrome.tabs.create({ url, active: false, windowId: guiTab.windowId, index: guiTab.index + 1 })
  let askedYou = false
  try {
    await waitLoaded(tab.id)
    const started = Date.now()
    while (await challenged(tab.id)) {
      if (!mayAsk && Date.now() - started > QUIET_WAIT_MS) {
        return { error: `${url} shows a human check. Open it once in this browser and pass it; then continue the digest.`, needs_person: true }
      }
      if (mayAsk && !askedYou && Date.now() - started > QUIET_WAIT_MS) {
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
  // CLAUDE> a link from the digest: open it behind the Automation desk tab, which keeps the focus
  if (message?.type === 'open' && sender.tab) {
    if (/^https?:\/\//.test(message.url)) {
      chrome.tabs.create({ url: message.url, active: false, windowId: sender.tab.windowId, openerTabId: sender.tab.id })
    }
    return false
  }
  if (message?.type !== 'read' || !sender.tab) return false
  read(message.url, sender.tab, message.mayAsk !== false).then(reply, error => reply({ error: String(error.message || error) }))
  return true
})

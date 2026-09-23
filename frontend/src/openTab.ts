import type { MouseEvent } from 'react'

// CLAUDE> links opened through the Automation desk reader extension land in a background tab, so this page keeps the focus;
// a page cannot do that by itself. Without the extension (or with an older one) a link opens the normal way.
export const canOpenInBackground = () => document.documentElement.dataset.automationOpen === '1'

export function openInBackground(event: MouseEvent<HTMLAnchorElement>) {
  if (!canOpenInBackground() || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  window.postMessage({ type: 'automation-desk:open', url: event.currentTarget.href }, window.location.origin)
}

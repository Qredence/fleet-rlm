import type { MouseEvent } from 'react'

export function followMarkdownFragment(event: MouseEvent<HTMLElement>) {
  if (event.defaultPrevented || !(event.target instanceof Element)) return
  const href = event.target.closest('a[href^="#"]')?.getAttribute('href')
  if (!href) return

  // The URL fragment belongs to HashRouter. Resolve anchors within this Markdown block.
  event.preventDefault()
  let id = href.slice(1)
  try {
    id = decodeURIComponent(id)
  } catch {
    // Keep literal percent characters in malformed escape sequences.
  }
  if (!id) return

  const target = event.currentTarget.querySelector<HTMLElement>(`#${CSS.escape(id)}`)
  if (!target) return
  if (target.tabIndex < 0 && !target.hasAttribute('tabindex')) target.tabIndex = -1
  target.focus({ preventScroll: true })
  target.scrollIntoView({ block: 'nearest' })
}

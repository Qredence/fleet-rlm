import Markdown from 'react-markdown'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { chatMarkdownProps } from './chat-markdown'

function render(markdown: string) {
  return renderToStaticMarkup(<Markdown {...chatMarkdownProps}>{markdown}</Markdown>)
}

describe('chatMarkdownProps', () => {
  it('does not render remote images', () => {
    const html = render('See ![tracking pixel](https://example.com/p.png) here.')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('example.com')
  })

  it('does not render raw HTML images', () => {
    expect(render('<img src="https://example.com/p.png">')).not.toContain('<img')
  })

  it('opens external links in a new tab', () => {
    expect(render('[Source](https://example.com/a)')).toBe(
      '<p><a href="https://example.com/a" target="_blank" rel="noopener noreferrer">Source</a></p>',
    )
    expect(render('[Source](http://example.com/a)')).toContain('target="_blank"')
    expect(render('[Source](//example.com/a)')).toContain('target="_blank"')
    expect(render('<https://example.com/a>')).toContain('rel="noopener noreferrer"')
  })

  it('keeps fragment links in the current tab', () => {
    expect(render('[Details](#details)')).toBe('<p><a href="#details">Details</a></p>')
  })
})

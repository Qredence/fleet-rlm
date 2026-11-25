import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { FleetPart, TurnDetails, WebCall } from '@/lib/messages'
import { MessageParts } from './MessageParts'

const turnDetails: TurnDetails = {
  runtime: 'dspy.RLM',
  root_actions: 3,
  child_rlms: 0,
  sub_lm_queries: 1,
  elapsed_ms: 1500,
  completion: 'submit',
  trace_url: null,
}

function webCall(id: string, changes: Partial<WebCall> = {}): FleetPart {
  return {
    type: 'data-web-call',
    id,
    data: { id, tool: 'web_search', target: `query ${id}`, outcome: 'results', elapsed_ms: 200, used: 1, limit: 5, ...changes },
  }
}

function renderParts(parts: FleetPart[], props: { live?: boolean; refine?: () => Promise<null> } = {}) {
  const preview = vi.fn()
  const download = vi.fn()
  render(<MessageParts parts={parts} preview={preview} download={download} {...props} />)
  return { preview, download }
}

describe('MessageParts', () => {
  it('renders markdown text and activated skill scores', () => {
    renderParts([
      { type: 'data-skills', id: 'skills', data: { skills: [{ name: 'pdf-reader', score: 0.874 }] } },
      { type: 'text', text: 'Hello **world**' },
    ])

    expect(within(screen.getByLabelText('Activated skills')).getByText('87%')).toBeTruthy()
    expect(screen.getByText('@pdf-reader', { exact: false })).toBeTruthy()
    expect(screen.getByText('world').tagName).toBe('STRONG')
  })

  it('opens streaming reasoning while the turn is live', () => {
    renderParts([{ type: 'reasoning', text: 'Thinking about it', state: 'streaming' }], { live: true })

    expect(screen.getByText('Reasoning')).toBeTruthy()
    expect(screen.getByText('Thinking about it')).toBeTruthy()
  })

  it('collapses reasoning notes after the turn completes', async () => {
    const user = userEvent.setup()
    renderParts([{ type: 'reasoning', text: 'Thinking about it', state: 'streaming' }])

    expect(screen.queryByText('Thinking about it')).toBeNull()
    await user.click(screen.getByText('Reasoning notes'))
    expect(screen.getByText('Thinking about it')).toBeTruthy()
  })

  it('groups all web calls into one activity row', async () => {
    const user = userEvent.setup()
    renderParts([webCall('a'), { type: 'text', text: 'between' }, webCall('b', { used: 2 })])

    const rows = screen.getAllByText(/^Web research/)
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toBe('Web research · 2 of 5 requests used')

    await user.click(rows[0])
    expect(screen.getByText('query a')).toBeTruthy()
    expect(screen.getByText('query b')).toBeTruthy()
  })

  it('marks running web calls as interrupted on a completed turn', async () => {
    const user = userEvent.setup()
    renderParts([webCall('a', { outcome: 'running', elapsed_ms: null })])

    await user.click(screen.getByText(/^Web research/))
    expect(screen.getByText('Interrupted')).toBeTruthy()
  })

  it('offers turn refinement only on completed turns', async () => {
    const user = userEvent.setup()
    const refine = vi.fn(async () => null)
    const parts: FleetPart[] = [{ type: 'data-turn-details', id: 'details', data: turnDetails }]

    const { unmount } = render(<MessageParts parts={parts} live preview={vi.fn()} download={vi.fn()} refine={refine} />)
    await user.click(screen.getByText('Turn details'))
    expect(screen.queryByRole('button', { name: 'Refine this turn' })).toBeNull()
    unmount()

    renderParts(parts, { refine })
    await user.click(screen.getByText('Turn details'))
    await user.click(screen.getByRole('button', { name: 'Refine this turn' }))
    expect(refine).toHaveBeenCalledOnce()
    expect(await screen.findByText('No useful lesson found.')).toBeTruthy()
  })

  it('lists only artifacts from a file tree and forwards preview and download', async () => {
    const user = userEvent.setup()
    const { preview, download } = renderParts([{
      type: 'data-file-tree',
      id: 'tree',
      data: {
        files: [
          { path: 'files/input.csv', kind: 'input', size_bytes: 10 },
          { path: 'artifacts/report.md', kind: 'artifact', size_bytes: 20 },
        ],
      },
    }])

    expect(screen.getByText('Artifacts')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Preview files/input.csv' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Preview artifacts/report.md' }))
    await user.click(screen.getByRole('button', { name: 'Download artifacts/report.md' }))
    expect(preview).toHaveBeenCalledWith('artifacts/report.md')
    expect(download).toHaveBeenCalledWith('artifacts/report.md')
  })

  it('hides a file tree that has no artifacts and empty skill lists', () => {
    const { container } = render(
      <MessageParts
        parts={[
          { type: 'data-file-tree', id: 'tree', data: { files: [{ path: 'files/a.txt', kind: 'input', size_bytes: 1 }] } },
          { type: 'data-skills', id: 'skills', data: { skills: [] } },
        ]}
        preview={vi.fn()} download={vi.fn()}
      />,
    )

    expect(container.firstElementChild?.childElementCount).toBe(0)
  })
})

import { renderToStaticMarkup } from 'react-dom/server'
import { expect, it } from 'vitest'
import type { FleetPart } from '@/lib/messages'
import { MessageParts } from './MessageParts'

const parts: FleetPart[] = [
  { type: 'reasoning', text: 'Check precision', state: 'done' },
  { type: 'tool-python_exec', toolCallId: 'failed', state: 'output-error', input: 'calculate()', errorText: 'ValueError: integer conversion limit' },
  { type: 'tool-python_exec', toolCallId: 'retry', state: 'output-available', input: 'retry()', output: { answer: 'ordinary tool data' } },
  { type: 'tool-python_exec', toolCallId: 'submit', state: 'output-available', input: 'SUBMIT()', output: { answer: 'The digit is 5.' } },
  { type: 'data-python-submission', data: { tool_call_id: 'submit' } },
  { type: 'text', text: 'The digit is 5.' },
]

function render(items: FleetPart[], live = false) {
  return renderToStaticMarkup(<MessageParts parts={items} live={live} preview={() => {}} download={() => {}} />)
}

it('summarizes a recovered Python error while showing the submitted answer once', () => {
  const html = render(parts)
  expect(html).toContain('Activity · 2 Python actions · 1 recovered error')
  expect(html).toContain('Turn details')
  expect(html.match(/The digit is 5\./g)).toHaveLength(1)
})

it('preserves unmarked Python results as actions and distinguishes a running call', () => {
  expect(render(parts.filter((part) => part.type !== 'data-python-submission')))
    .toContain('Activity · 3 Python actions · 1 recovered error')
  expect(render([{ type: 'tool-python_exec', toolCallId: 'running', state: 'input-available', input: 'compute()' }], true))
    .toContain('Activity · Running Python')
  const progress: FleetPart = {
    type: 'tool-python_exec', toolCallId: 'running', state: 'output-available',
    input: 'compute()', output: 'Working on the result', preliminary: true,
  }
  const marker: FleetPart = { type: 'data-python-submission', data: { tool_call_id: 'running' } }
  const live = render([progress, marker], true)
  expect(live).toContain('Activity · Running Python')
  expect(live).toContain('Working on the result')
  expect(live).not.toContain('Turn details')
  expect(render([progress, marker])).toContain('Activity · 1 Python action')
})

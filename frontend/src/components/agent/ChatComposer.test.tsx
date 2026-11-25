import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { Skill } from '@/lib/api'
import { ChatComposer } from './ChatComposer'

const skills: Skill[] = [
  { name: 'pdf-reader', description: 'Read PDF files' },
  { name: 'pdf-writer', description: 'Write PDF files' },
  { name: 'web_search', description: 'Search the web' },
]

function Harness({ onSubmit, onStop = vi.fn(), onUpload, loading = false }: {
  onSubmit: (value: string) => Promise<void>
  onStop?: () => void
  onUpload?: (files: File[]) => void
  loading?: boolean
}) {
  const [value, setValue] = useState('')
  const [pinned, setPinned] = useState<string[]>([])
  return (
    <>
      <ChatComposer
        skills={skills} pinned={pinned} setPinned={setPinned} value={value} onValueChange={setValue}
        onSubmit={onSubmit} onStop={onStop} loading={loading} attachments={null} onUpload={onUpload}
        submitDisabled={false} modelSelector={null}
      />
      <output aria-label="Pinned skills">{pinned.join(',')}</output>
    </>
  )
}

function setup(props: Partial<Parameters<typeof Harness>[0]> = {}) {
  const user = userEvent.setup()
  const onSubmit = vi.fn(async () => {})
  render(<Harness onSubmit={onSubmit} {...props} />)
  const textarea = screen.getByRole<HTMLTextAreaElement>('textbox', { name: 'Message Fleet' })
  return { user, onSubmit, textarea }
}

function activeOption() {
  const id = screen.getByRole('textbox', { name: 'Message Fleet' }).getAttribute('aria-activedescendant')
  return id ? document.getElementById(id) : null
}

describe('ChatComposer', () => {
  it('filters skills after @ and moves the active option with the arrow keys', async () => {
    const { user, textarea } = setup()

    await user.type(textarea, 'Use @pdf')
    const options = await screen.findAllByRole('option')
    expect(options.map((option) => option.getAttribute('aria-label'))).toEqual(['Pdf reader', 'Pdf writer'])
    expect(activeOption()?.getAttribute('aria-label')).toBe('Pdf reader')

    await user.keyboard('{ArrowDown}')
    expect(activeOption()?.getAttribute('aria-label')).toBe('Pdf writer')
    await user.keyboard('{ArrowDown}')
    expect(activeOption()?.getAttribute('aria-label')).toBe('Pdf reader')
    await user.keyboard('{ArrowUp}')
    expect(activeOption()?.getAttribute('aria-label')).toBe('Pdf writer')
  })

  it('completes the active skill on Enter and pins it without sending', async () => {
    const { user, onSubmit, textarea } = setup()

    await user.type(textarea, 'Use @pdf')
    await screen.findByRole('listbox')
    await user.keyboard('{ArrowDown}{Enter}')

    expect(textarea.value).toBe('Use @pdf-writer ')
    expect(textarea.selectionStart).toBe(textarea.value.length)
    expect(screen.getByLabelText('Pinned skills').textContent).toBe('pdf-writer')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('completes a skill on click', async () => {
    const { user, textarea } = setup()

    await user.type(textarea, '@web')
    await user.click(await screen.findByRole('option', { name: 'Web search' }))

    expect(textarea.value).toBe('@web_search ')
    expect(screen.getByLabelText('Pinned skills').textContent).toBe('web_search')
  })

  it('closes the skill list on Escape and then sends on Enter', async () => {
    const { user, onSubmit, textarea } = setup()

    await user.type(textarea, 'Hi @pdf')
    await screen.findByRole('listbox')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()

    await user.keyboard('{Enter}')
    expect(onSubmit).toHaveBeenCalledWith('Hi @pdf')
  })

  it('shows a status when no skill matches', async () => {
    const { user, textarea } = setup()

    await user.type(textarea, '@zzz')
    expect((await screen.findByText('No matching skills')).getAttribute('role')).toBe('status')
    expect(screen.queryAllByRole('option')).toHaveLength(0)
  })

  it('ignores Enter while an IME composition is active', async () => {
    const { user, onSubmit, textarea } = setup()

    await user.type(textarea, '@pdf')
    await screen.findByRole('listbox')
    fireEvent.keyDown(textarea, { key: 'Enter', isComposing: true })

    expect(textarea.value).toBe('@pdf')
    expect(screen.getByRole('listbox')).toBeTruthy()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('opens the skill list only when the IME composition ends', async () => {
    const { textarea } = setup()

    textarea.focus()
    fireEvent.compositionStart(textarea)
    fireEvent.input(textarea, { target: { value: '@pdf' }, isComposing: true })
    expect(textarea.value).toBe('@pdf')
    expect(screen.queryByRole('listbox')).toBeNull()

    fireEvent.compositionEnd(textarea)
    expect(await screen.findByRole('listbox')).toBeTruthy()
  })

  it('sends trimmed text on Enter and keeps Shift+Enter as a new line', async () => {
    const { user, onSubmit, textarea } = setup()

    await user.type(textarea, '  hello{Shift>}{Enter}{/Shift}')
    expect(onSubmit).not.toHaveBeenCalled()

    await user.keyboard('{Enter}')
    expect(onSubmit).toHaveBeenCalledWith('hello')
  })

  it('turns the send button into a stop button while loading', async () => {
    const onStop = vi.fn()
    const { user, onSubmit } = setup({ loading: true, onStop })

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).toHaveBeenCalledOnce()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('passes picked files to the upload handler', async () => {
    const onUpload = vi.fn()
    const { user } = setup({ onUpload })
    const file = new File(['a,b'], 'data.csv', { type: 'text/csv' })

    await user.upload(screen.getByLabelText<HTMLInputElement>('Attach workspace files'), file)
    expect(onUpload).toHaveBeenCalledWith([file])
  })
})

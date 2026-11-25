import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FleetApi, Skill } from '@/lib/api'
import { FilePreview } from './FilePreview'

const skillSource = '---\nname: demo\ndescription: Demo skill\n---\nSay hi.\n'
const skillDigest = 'cdd77c8dd9c2456b89c492f0006a38077a442824343f0e4ffd0341118f5d8d24'

function renderPreview(path: string, response: Response | Error, overrides: Partial<FleetApi> = {}) {
  const getFile = vi.fn(async () => {
    if (response instanceof Error) throw response
    return response
  })
  const installSkill = vi.fn(async (): Promise<Skill> => ({ name: 'demo', description: 'Demo skill' }))
  const api = { getFile, installSkill, ...overrides } as unknown as FleetApi
  const onDownload = vi.fn(async () => {})
  const onSkillInstalled = vi.fn()
  const view = render(
    <FilePreview api={api} sessionId="s1" path={path} onDownload={onDownload} onSkillInstalled={onSkillInstalled} />,
  )
  return { ...view, api, getFile, installSkill, onDownload, onSkillInstalled }
}

describe('FilePreview', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows a loading state, then the text contents', async () => {
    renderPreview('files/notes.txt', new Response('line one\nline two'))

    expect(screen.getByRole('status', { name: 'Loading preview' })).toBeTruthy()
    expect((await screen.findByLabelText('File contents')).textContent).toBe('line one\nline two')
    expect(screen.queryByText('Showing the first 128 KiB.')).toBeNull()
  })

  it('shows only the first 128 KiB of a large text file', async () => {
    renderPreview('files/big.log', new Response('a'.repeat(128 * 1024 + 10)))

    expect(await screen.findByText('Showing the first 128 KiB.')).toBeTruthy()
    expect(screen.getByLabelText('File contents').textContent).toHaveLength(128 * 1024)
  })

  it('does not fetch file types that it cannot preview', async () => {
    const { getFile } = renderPreview('files/archive.zip', new Response(''))

    expect(await screen.findByText('Download this file to open it.')).toBeTruthy()
    expect(getFile).not.toHaveBeenCalled()
  })

  it('rejects media files above the size limit without reading them', async () => {
    const response = new Response('png', { headers: { 'content-length': String(21 * 1024 * 1024) } })
    const cancel = vi.spyOn(response.body as ReadableStream, 'cancel')
    renderPreview('artifacts/chart.png', response)

    expect(await screen.findByText('Files larger than 20 MB are available to download.')).toBeTruthy()
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('shows an image and revokes its object URL on unmount', async () => {
    const createObjectURL = vi.fn(() => 'blob:chart')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL }))
    const { unmount } = renderPreview('artifacts/chart.png', new Response('png'))

    expect((await screen.findByRole('img', { name: 'chart.png' })).getAttribute('src')).toBe('blob:chart')
    unmount()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:chart')
  })

  it('reports a failed fetch', async () => {
    renderPreview('files/notes.txt', new Error('File not found'))

    expect(await screen.findByText('File not found')).toBeTruthy()
  })

  it('renders markdown files as formatted text', async () => {
    renderPreview('files/readme.md', new Response('# Title'))

    expect((await screen.findByRole('heading', { name: 'Title' })).tagName).toBe('H1')
  })

  it('installs a SKILL.md artifact with the SHA-256 of the previewed bytes', async () => {
    const user = userEvent.setup()
    const { installSkill, onSkillInstalled } = renderPreview('artifacts/demo/SKILL.md', new Response(skillSource))

    expect((await screen.findByLabelText('File contents')).tagName).toBe('PRE')
    await user.click(await screen.findByRole('button', { name: 'Install skill' }))

    expect(installSkill).toHaveBeenCalledWith('s1', 'artifacts/demo/SKILL.md', skillDigest)
    expect(onSkillInstalled).toHaveBeenCalledWith({ name: 'demo', description: 'Demo skill' })
    expect((await screen.findByRole('status')).textContent).toBe('Installed @demo. It is available in Skills for all sessions.')
    expect(screen.queryByRole('button', { name: 'Install skill' })).toBeNull()
  })

  it('shows an install failure and keeps the install action', async () => {
    const user = userEvent.setup()
    const installSkill = vi.fn(async () => {
      throw new Error('Digest mismatch')
    })
    const { onSkillInstalled } = renderPreview('artifacts/demo/SKILL.md', new Response(skillSource), { installSkill })

    await user.click(await screen.findByRole('button', { name: 'Install skill' }))

    expect(await screen.findByText('Digest mismatch')).toBeTruthy()
    expect(onSkillInstalled).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Install skill' })).toBeTruthy()
  })

  it.each([
    ['a truncated SKILL.md', 'artifacts/demo/SKILL.md', 'a'.repeat(128 * 1024 + 1)],
    ['a SKILL.md above 64 KiB', 'artifacts/demo/SKILL.md', 'a'.repeat(64 * 1024 + 1)],
    ['a SKILL.md outside artifacts', 'files/demo/SKILL.md', skillSource],
  ])('does not offer install for %s', async (_, path, body) => {
    renderPreview(path, new Response(body))

    await screen.findByLabelText('File contents')
    expect(screen.queryByRole('button', { name: 'Install skill' })).toBeNull()
  })

  it('does not offer install without Web Crypto', async () => {
    vi.stubGlobal('crypto', {})
    renderPreview('artifacts/demo/SKILL.md', new Response(skillSource))

    await screen.findByLabelText('File contents')
    expect(screen.queryByRole('button', { name: 'Install skill' })).toBeNull()
  })

  it('shows a download failure', async () => {
    const user = userEvent.setup()
    const { onDownload } = renderPreview('files/notes.txt', new Response('hi'))
    onDownload.mockRejectedValueOnce(new Error('Download failed'))

    await user.click(screen.getByRole('button', { name: 'Download file' }))

    expect(await screen.findByText('Download failed')).toBeTruthy()
  })
})

import { describe, expect, it } from 'vitest'
import { filesInWorkspaceTab, initialWorkspaceSelection, workspaceSelection } from './workspace-browser'
import type { WorkspaceFileEntry } from './messages'

const input = { path: 'files/report.md', kind: 'input', sha256: 'input', size_bytes: 90 } satisfies WorkspaceFileEntry
const artifact = { path: 'artifacts/charts/sales.svg', kind: 'artifact', sha256: 'output', size_bytes: 410 } satisfies WorkspaceFileEntry

describe('workspace browser selection', () => {
  it('filters artifacts without changing their paths', () => {
    expect(filesInWorkspaceTab([input, artifact], 'workspace')).toEqual([input, artifact])
    expect(filesInWorkspaceTab([input, artifact], 'artifacts')).toEqual([artifact])
  })

  it('reveals transcript files in the correct tab', () => {
    const output = workspaceSelection(initialWorkspaceSelection, {
      type: 'open', path: artifact.path, fromChat: true,
    })
    expect(output).toEqual({ tab: 'artifacts', path: artifact.path, reveal: { path: artifact.path } })
    const upload = workspaceSelection(output, { type: 'open', path: input.path, fromChat: true })
    expect(upload).toEqual({ tab: 'workspace', path: input.path, reveal: { path: input.path } })
  })

  it('keeps visible selections and clears an upload hidden by the artifact filter', () => {
    const current = workspaceSelection(initialWorkspaceSelection, { type: 'open', path: artifact.path })
    expect(workspaceSelection(current, { type: 'tab', tab: 'artifacts' }).path).toBe(artifact.path)
    const upload = workspaceSelection(initialWorkspaceSelection, { type: 'open', path: input.path })
    expect(workspaceSelection(upload, { type: 'tab', tab: 'artifacts' }).path).toBeUndefined()
  })

  it('returns focus to the previous file and starts each fresh session unselected', () => {
    const opened = workspaceSelection(initialWorkspaceSelection, { type: 'open', path: artifact.path, fromChat: true })
    expect(workspaceSelection(opened, { type: 'close', restoreFocus: true })).toEqual({
      tab: 'artifacts', focus: artifact.path,
    })
    expect(initialWorkspaceSelection).toEqual({ tab: 'workspace' })
  })
})

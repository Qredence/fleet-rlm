import { describe, expect, it } from 'vitest'
import { buildFileTree, formatFileSize } from './file-tree'
import type { WorkspaceFileEntry } from './messages'

function fileEntry(path: string, size_bytes: number, kind: 'input' | 'artifact' = 'input'): WorkspaceFileEntry {
  return { path, size_bytes, kind, sha256: 'deadbeef' }
}

describe('formatFileSize', () => {
  it('handles zero, negative, and non-finite inputs safely', () => {
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(-10)).toBe('0 B')
    expect(formatFileSize(Number.NaN)).toBe('0 B')
    expect(formatFileSize(Number.POSITIVE_INFINITY)).toBe('0 B')
  })

  it('formats byte, kilobyte, megabyte, and gigabyte values cleanly', () => {
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(1023)).toBe('1023 B')
    expect(formatFileSize(1024)).toBe('1.0 KB')
    expect(formatFileSize(1536)).toBe('1.5 KB')
    expect(formatFileSize(10 * 1024)).toBe('10 KB')
    expect(formatFileSize(1024 * 1024)).toBe('1.0 MB')
    expect(formatFileSize(12.4 * 1024 * 1024)).toBe('12 MB')
    expect(formatFileSize(2.5 * 1024 ** 3)).toBe('2.5 GB')
    expect(formatFileSize(20 * 1024 ** 3)).toBe('20 GB')
  })
})

describe('buildFileTree', () => {
  it('returns an empty map for an empty file list', () => {
    expect(buildFileTree([]).size).toBe(0)
  })

  it('builds nested folders and shares common ancestor directories', () => {
    const tree = buildFileTree([
      fileEntry('README.md', 420),
      fileEntry('inputs/sales.csv', 2048),
      fileEntry('inputs/notes.txt', 128),
      fileEntry('artifacts/charts/q1/summary.png', 15_360, 'artifact'),
    ])

    expect([...tree.keys()]).toEqual(['README.md', 'inputs', 'artifacts'])

    const readme = tree.get('README.md')
    expect(readme).toEqual({
      type: 'file',
      name: 'README.md',
      path: 'README.md',
      sizeBytes: 420,
    })

    const inputs = tree.get('inputs')
    expect(inputs?.type).toBe('folder')
    if (inputs?.type === 'folder') {
      expect([...inputs.children.keys()]).toEqual(['sales.csv', 'notes.txt'])
      expect(inputs.children.get('sales.csv')).toEqual({
        type: 'file',
        name: 'sales.csv',
        path: 'inputs/sales.csv',
        sizeBytes: 2048,
      })
    }

    const artifacts = tree.get('artifacts')
    expect(artifacts?.type).toBe('folder')
    if (artifacts?.type === 'folder') {
      const charts = artifacts.children.get('charts')
      expect(charts?.type).toBe('folder')
      if (charts?.type === 'folder') {
        const q1 = charts.children.get('q1')
        expect(q1?.type).toBe('folder')
        if (q1?.type === 'folder') {
          expect(q1.children.get('summary.png')).toEqual({
            type: 'file',
            name: 'summary.png',
            path: 'artifacts/charts/q1/summary.png',
            sizeBytes: 15_360,
          })
        }
      }
    }
  })

  it('updates duplicate file paths and ignores empty path segments', () => {
    const tree = buildFileTree([
      fileEntry('/artifacts//report.md', 100, 'artifact'),
      fileEntry('artifacts/report.md', 250, 'artifact'),
      fileEntry('', 10),
    ])

    expect([...tree.keys()]).toEqual(['artifacts'])
    const artifacts = tree.get('artifacts')
    expect(artifacts?.type).toBe('folder')
    if (artifacts?.type === 'folder') {
      expect(artifacts.children.get('report.md')).toEqual({
        type: 'file',
        name: 'report.md',
        path: 'artifacts/report.md',
        sizeBytes: 250,
      })
    }
  })
})

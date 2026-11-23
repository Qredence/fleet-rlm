import type { WorkspaceFileEntry } from './messages'

export type WorkspaceTab = 'workspace' | 'artifacts'

type WorkspaceSelection = {
  tab: WorkspaceTab
  path?: string
  reveal?: { path: string }
  focus?: string
}

type WorkspaceAction =
  | { type: 'tab'; tab: WorkspaceTab }
  | { type: 'open'; path: string; fromChat?: boolean }
  | { type: 'close'; restoreFocus?: boolean }

export const initialWorkspaceSelection: WorkspaceSelection = { tab: 'workspace' }

function tabForFile(path: string): WorkspaceTab {
  return path.startsWith('artifacts/') ? 'artifacts' : 'workspace'
}

export function workspaceSelection(state: WorkspaceSelection, action: WorkspaceAction): WorkspaceSelection {
  switch (action.type) {
    case 'tab':
      if (state.tab === action.tab) return state
      return {
        tab: action.tab,
        path: action.tab === 'artifacts' && state.path && tabForFile(state.path) !== 'artifacts'
          ? undefined : state.path,
      }
    case 'open':
      return {
        tab: action.fromChat ? tabForFile(action.path) : state.tab,
        path: action.path,
        reveal: action.fromChat ? { path: action.path } : undefined,
      }
    case 'close':
      return { tab: state.tab, focus: action.restoreFocus ? state.path : undefined }
  }
}

export function filesInWorkspaceTab<T extends WorkspaceFileEntry>(files: T[], tab: WorkspaceTab): T[] {
  return tab === 'artifacts' ? files.filter(({ kind }) => kind === 'artifact') : files
}

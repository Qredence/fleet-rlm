import { FileText, RotateCw, X } from 'lucide-react'
import {
  Attachment, AttachmentAction, AttachmentActions, AttachmentContent, AttachmentDescription,
  AttachmentGroup, AttachmentMedia, AttachmentTitle,
} from '@/components/ui/attachment'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { WorkspaceFiles } from '@/hooks/use-workspace-files'

const attachmentLabels = {
  idle: 'Queued',
  uploading: 'Uploading…',
  done: 'In workspace',
  error: 'Upload failed',
}

export function WorkspaceAttachments({ attachments, canUpload, onRetry, onDismiss }: {
  attachments: WorkspaceFiles['attachments']
  canUpload: boolean
  onRetry: (id: number) => void
  onDismiss: (id: number) => void
}) {
  if (!attachments.length) return null
  return (
    <AttachmentGroup aria-label="Workspace attachments">
      {attachments.map(({ id, file, state, error }) => (
        <Attachment key={id} size="sm" state={state} className="w-64 flex-nowrap">
          <AttachmentMedia><FileText aria-hidden="true" /></AttachmentMedia>
          <AttachmentContent>
            <AttachmentTitle title={file.name} className="motion-reduce:shimmer-none">
              {file.name}
            </AttachmentTitle>
            <AttachmentDescription role="status" className={error ? 'whitespace-normal wrap-anywhere' : undefined}>
              {error ? `Upload failed. ${error}` : attachmentLabels[state]}
            </AttachmentDescription>
          </AttachmentContent>
          <AttachmentActions>
            {state === 'error' && (
              <Tooltip>
                <TooltipTrigger render={<AttachmentAction
                  type="button" aria-label={`Retry upload of ${file.name}`}
                  disabled={!canUpload} onClick={() => onRetry(id)}
                />}>
                  <RotateCw />
                </TooltipTrigger>
                <TooltipContent>Retry upload</TooltipContent>
              </Tooltip>
            )}
            {(state === 'done' || state === 'error') && (
              <Tooltip>
                <TooltipTrigger render={<AttachmentAction
                  type="button" aria-label={`Dismiss ${file.name}`} onClick={() => onDismiss(id)}
                />}>
                  <X />
                </TooltipTrigger>
                <TooltipContent>Dismiss</TooltipContent>
              </Tooltip>
            )}
          </AttachmentActions>
        </Attachment>
      ))}
    </AttachmentGroup>
  )
}

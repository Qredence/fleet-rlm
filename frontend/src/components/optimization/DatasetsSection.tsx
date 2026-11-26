import { Eye, Play, Trash2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  CodeBlock,
  CodeBlockActions,
  CodeBlockContent,
  CodeBlockCopyButton,
  CodeBlockHeader,
  CodeBlockTitle,
} from '@/components/ui/code-block'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import {
  errorMessage,
  type FleetApi,
  type OptimizationDatasetPreview,
  type OptimizationDatasetSummary,
  type OptimizationTarget,
} from '@/lib/api'
import { Elevated } from '@/lib/elevated'
import { fontWeights } from '@/lib/font-weight'
import { typeClass } from '@/lib/type-scale'

const TARGET_LABEL: Record<OptimizationTarget, string> = {
  skills: 'Skill Selector',
  memory: 'Memory Gate',
  rlm: 'Root RLM',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

export function DatasetsSection({
  api,
  datasets,
  loading,
  onUploaded,
  onDeleted,
  onRunWithDataset,
}: {
  api: FleetApi
  datasets: OptimizationDatasetSummary[] | null
  loading: boolean
  onUploaded: (created: OptimizationDatasetSummary) => void
  onDeleted: (datasetId: string) => void
  onRunWithDataset: (dataset: OptimizationDatasetSummary) => void
}) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadTarget, setUploadTarget] = useState<OptimizationTarget>('skills')
  const [uploadName, setUploadName] = useState('')
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')

  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const [previewData, setPreviewData] = useState<OptimizationDatasetPreview | null>(null)

  const [deleteTarget, setDeleteTarget] = useState<OptimizationDatasetSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  async function openPreview(dataset: OptimizationDatasetSummary) {
    setPreviewOpen(true)
    setPreviewLoading(true)
    setPreviewError('')
    setPreviewData(null)
    try {
      const result = await api.previewOptimizationDataset(dataset.id, 25)
      setPreviewData(result)
    } catch (failure) {
      setPreviewError(errorMessage(failure))
    } finally {
      setPreviewLoading(false)
    }
  }

  async function handleUpload(event: React.FormEvent) {
    event.preventDefault()
    if (!selectedFile) {
      setUploadError('Choose a UTF-8 .jsonl file first.')
      return
    }
    setUploadError('')
    setUploading(true)
    try {
      const body = new FormData()
      body.append('target', uploadTarget)
      if (uploadName.trim()) body.append('name', uploadName.trim())
      body.append('file', selectedFile)
      const created = await api.uploadOptimizationDataset(body)
      setSelectedFile(null)
      setUploadName('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      onUploaded(created)
    } catch (failure) {
      setUploadError(errorMessage(failure))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Upload custom dataset panel */}
      <form
        onSubmit={handleUpload}
        className="flex flex-col gap-4 rounded-xl border bg-card p-4"
        aria-label="Upload custom JSONL dataset"
      >
        <div className="flex flex-col gap-1">
          <h2 className={typeClass('subtitle')} style={{ fontVariationSettings: fontWeights.semibold }}>
            Upload evaluation dataset
          </h2>
          <p className={`${typeClass('caption')} text-muted-foreground`}>
            Validated JSONL files are stored in Neon Object Storage (max 2,000 rows, no raw API keys
            or database URLs).
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <Field>
            <FieldLabel>Target</FieldLabel>
            <Select
              value={uploadTarget}
              onValueChange={(value) => setUploadTarget((value || 'skills') as OptimizationTarget)}
            >
              <SelectTrigger aria-label="Dataset target" placeholder="Select target" />
              <SelectContent>
                <SelectItem index={0} value="skills">Skill Selector</SelectItem>
                <SelectItem index={1} value="memory">Memory Gate</SelectItem>
                <SelectItem index={2} value="rlm">Root RLM</SelectItem>
              </SelectContent>
            </Select>
          </Field>

          <Field>
            <FieldLabel htmlFor="opt-dataset-name">Display name (optional)</FieldLabel>
            <Input
              id="opt-dataset-name"
              value={uploadName}
              onChange={(event) => setUploadName(event.target.value)}
              placeholder="e.g. customer-skills-v2.jsonl"
            />
          </Field>

          <Field>
            <FieldLabel htmlFor="opt-dataset-file">JSONL file</FieldLabel>
            <div
              onDragOver={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault()
                setDragging(false)
                const dropped = event.dataTransfer.files[0]
                if (dropped) {
                  setSelectedFile(dropped)
                  setUploadError('')
                }
              }}
              className={`rounded-lg border border-dashed p-1.5 transition-colors ${dragging ? 'border-foreground bg-hover' : 'border-border'}`}
            >
              <Input
                ref={fileInputRef}
                id="opt-dataset-file"
                type="file"
                accept=".jsonl,application/jsonl,text/plain"
                onChange={(event) => {
                  setSelectedFile(event.target.files?.[0] ?? null)
                  setUploadError('')
                }}
              />
            </div>
            <FieldDescription>
              {selectedFile
                ? `${selectedFile.name} (${formatBytes(selectedFile.size)})`
                : 'Drop a .jsonl file or browse.'}
            </FieldDescription>
          </Field>
        </div>

        {uploadError && (
          <Alert variant="destructive">
            <AlertDescription>{uploadError}</AlertDescription>
          </Alert>
        )}

        <div className="flex justify-end">
          <Button type="submit" size="sm" disabled={uploading || !selectedFile}>
            <Upload className="size-3.5" aria-hidden="true" />
            {uploading ? 'Validating & uploading…' : 'Upload dataset'}
          </Button>
        </div>
      </form>

      {/* Dataset subgrid catalog */}
      {loading && !datasets ? (
        <div className="opt-subgrid-cards" role="status" aria-label="Loading datasets">
          {[0, 1, 2].map((idx) => (
            <Elevated
              key={idx}
              offset={1}
              shadowLevel={2}
              className="opt-subgrid-card min-w-0 rounded-xl p-4 ring-1 ring-foreground/10"
            >
              <Skeleton className="h-5 w-40 bg-hover motion-reduce:animate-none" />
              <Skeleton className="h-8 w-full bg-hover motion-reduce:animate-none" />
              <Skeleton className="h-10 w-full bg-hover motion-reduce:animate-none" />
              <Skeleton className="h-8 w-32 bg-hover motion-reduce:animate-none" />
            </Elevated>
          ))}
        </div>
      ) : (
        <div className="opt-subgrid-cards" role="region" aria-label="Available datasets">
          {(datasets ?? []).map((dataset) => {
            const splits = dataset.summary_json?.splits ?? {}
            return (
              <Elevated
                key={dataset.id}
                offset={1}
                shadowLevel={2}
                data-testid={`dataset-card-${dataset.id}`}
                className="opt-subgrid-card min-w-0 rounded-xl p-4 ring-1 ring-foreground/10"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3
                      className={`${typeClass('subtitle')} truncate text-foreground`}
                      style={{ fontVariationSettings: fontWeights.semibold }}
                      title={dataset.name}
                    >
                      {dataset.name}
                    </h3>
                    <p className={`${typeClass('caption')} text-muted-foreground`}>
                      {TARGET_LABEL[dataset.target]}
                    </p>
                  </div>
                  <Badge variant={dataset.source === 'builtin' ? 'secondary' : 'outline'}>
                    {dataset.source === 'builtin' ? 'Built-in' : 'Custom'}
                  </Badge>
                </div>

                <dl className="grid grid-cols-2 gap-2 border-t border-border/60 pt-2.5">
                  <div>
                    <dt className={`${typeClass('caption')} text-muted-foreground`}>Rows</dt>
                    <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                      {dataset.row_count}
                    </dd>
                  </div>
                  <div>
                    <dt className={`${typeClass('caption')} text-muted-foreground`}>Lineages</dt>
                    <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                      {dataset.summary_json?.lineage_count ?? 0}
                    </dd>
                  </div>
                </dl>

                <p className={`${typeClass('caption')} border-t border-border/60 pt-2.5 tabular-nums text-muted-foreground`}>
                  Splits: {splits.train ?? 0} train · {splits.val ?? 0} val · {splits.holdout ?? 0}{' '}
                  holdout ({formatBytes(dataset.size_bytes)})
                </p>

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5">
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => void openPreview(dataset)}
                      data-testid={`preview-dataset-${dataset.id}`}
                    >
                      <Eye className="size-3.5" aria-hidden="true" />
                      Preview
                    </Button>
                    <Button size="sm" onClick={() => onRunWithDataset(dataset)}>
                      <Play className="size-3.5" aria-hidden="true" />
                      Run
                    </Button>
                  </div>

                  {dataset.source === 'custom' && (
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      aria-label={`Delete dataset ${dataset.name}`}
                      onClick={() => setDeleteTarget(dataset)}
                    >
                      <Trash2 className="size-3.5" aria-hidden="true" />
                    </Button>
                  )}
                </div>
              </Elevated>
            )
          })}
        </div>
      )}

      {/* Read-only preview Sheet */}
      <Sheet open={previewOpen} onOpenChange={setPreviewOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto data-[side=right]:sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>{previewData?.dataset.name ?? 'Dataset preview'}</SheetTitle>
            <SheetDescription>
              Read-only sample (first 25 rows) with class and split counts.
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-6">
            {previewLoading ? (
              <div className="flex flex-col gap-2" role="status" aria-label="Loading dataset preview">
                <Skeleton className="h-16 w-full bg-hover motion-reduce:animate-none" />
                <Skeleton className="h-64 w-full bg-hover motion-reduce:animate-none" />
              </div>
            ) : previewError ? (
              <Alert variant="destructive">
                <AlertDescription>{previewError}</AlertDescription>
              </Alert>
            ) : (
              previewData && (
                <>
                  <dl className="grid grid-cols-3 gap-2 rounded-lg border p-3">
                    <div>
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Total rows</dt>
                      <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                        {previewData.dataset.row_count}
                      </dd>
                    </div>
                    <div>
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Lineages</dt>
                      <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                        {previewData.dataset.summary_json?.lineage_count ?? 0}
                      </dd>
                    </div>
                    <div>
                      <dt className={`${typeClass('caption')} text-muted-foreground`}>Balance</dt>
                      <dd className={`${typeClass('body')} tabular-nums text-foreground`}>
                        {previewData.dataset.target === 'rlm'
                          ? `${previewData.dataset.summary_json?.blocking_count ?? 0} blocking`
                          : `${previewData.dataset.summary_json?.positive_count ?? 0}+ / ${previewData.dataset.summary_json?.negative_count ?? 0}-`}
                      </dd>
                    </div>
                  </dl>
                  <div className="flex flex-col gap-2">
                    {previewData.rows.map((row, index) => (
                      <CodeBlock key={index} size="sm">
                        <CodeBlockHeader>
                          <CodeBlockTitle>Row {index + 1}</CodeBlockTitle>
                          <CodeBlockActions>
                            <CodeBlockCopyButton
                              value={JSON.stringify(row, null, 2)}
                              aria-label={`Copy row ${index + 1}`}
                            />
                          </CodeBlockActions>
                        </CodeBlockHeader>
                        <CodeBlockContent wrap>
                          {JSON.stringify(row, null, 2)}
                        </CodeBlockContent>
                      </CodeBlock>
                    ))}
                  </div>
                </>
              )
            )}
          </div>
        </SheetContent>
      </Sheet>

      <AlertDialog open={Boolean(deleteTarget)} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete custom dataset?</AlertDialogTitle>
            <AlertDialogDescription>
              Deletes <code>{deleteTarget?.name}</code> from Neon Postgres and Object Storage.
              Existing job reports remain intact.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={deleting}
              onClick={async () => {
                if (!deleteTarget) return
                setDeleting(true)
                try {
                  await api.deleteOptimizationDataset(deleteTarget.id)
                  const deletedId = deleteTarget.id
                  setDeleteTarget(null)
                  onDeleted(deletedId)
                } finally {
                  setDeleting(false)
                }
              }}
            >
              {deleting ? 'Deleting…' : 'Delete dataset'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

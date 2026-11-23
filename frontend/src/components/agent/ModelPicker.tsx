import { useEffect, useState, type ReactElement, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import {
  Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList, ComboboxTrigger,
} from '@/components/ui/combobox'
import type { ModelCatalog } from '@/lib/api'
import type { ModelSelectorProps } from './ModelSelector'

interface ModelOption {
  value: string
  label: string
  detail?: string
}

export function ModelPicker({ api, model, disabled, saving, onChange, label, trigger, children }: ModelSelectorProps & {
  label: string
  trigger: ReactElement
  children: ReactNode
}) {
  const [open, setOpen] = useState(!disabled)
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!open) return
    const request = new AbortController()
    void api.listModels(request.signal).then((result) => {
      if (!request.signal.aborted) setCatalog(result)
    }).catch(() => {
      if (!request.signal.aborted) setCatalog({ workspace_default: '', catalog_status: 'unavailable', models: [] })
    })
    return () => request.abort()
  }, [api, attempt, open])

  const workspace: ModelOption = {
    value: '', label: 'Workspace default', detail: catalog?.workspace_default,
  }
  const providerModels: ModelOption[] = (catalog?.models ?? []).map(({ id, label }) => ({ value: id, label }))
  const selection = model ? providerModels.find(({ value }) => value === model)
    ?? { value: model, label } : workspace
  const options = [workspace, ...providerModels]
  if (model && !providerModels.includes(selection)) options.splice(1, 0, selection)

  return (
    <Combobox<ModelOption>
      items={options}
      value={selection}
      open={open}
      disabled={disabled}
      itemToStringLabel={(item) => item.label}
      itemToStringValue={(item) => item.value}
      isItemEqualToValue={(item, value) => item.value === value.value}
      onOpenChange={(next) => {
        if (next && saving) return
        if (next) setCatalog(null)
        setOpen(next)
      }}
      onValueChange={(item) => {
        if (item) onChange(item.value || null)
      }}
    >
      <ComboboxTrigger render={trigger}>
        {children}
      </ComboboxTrigger>
      <ComboboxContent side="top" align="end" className="flex w-80 min-w-0 flex-col">
        <ComboboxInput aria-label="Search models" placeholder="Search models…" showTrigger={false} className="shrink-0" />
        <ComboboxEmpty>No matching models</ComboboxEmpty>
        <ComboboxList className="min-h-0 max-h-64">
          {(item: ModelOption) => (
            <ComboboxItem key={item.value} value={item} className="min-h-9 px-2 pr-8" title={item.label}>
              <span className="flex min-w-0 flex-col">
                <span className="truncate">{item.label}</span>
                {item.detail && <span className="truncate text-xs text-muted-foreground">{item.detail}</span>}
              </span>
            </ComboboxItem>
          )}
        </ComboboxList>
        {catalog?.catalog_status !== 'ready' && (
          <div className="flex shrink-0 items-center justify-between gap-2 border-t px-2.5 py-1.5 text-xs text-muted-foreground">
            <span role="status">
              {catalog === null && <span className="shimmer motion-reduce:shimmer-none">Loading models…</span>}
              {catalog?.catalog_status === 'unavailable' && 'Gateway model catalog unavailable.'}
              {catalog?.catalog_status === 'unconfigured' && 'No model gateway configured.'}
            </span>
            {catalog?.catalog_status === 'unavailable' && (
              <Button type="button" variant="ghost" size="xs" onClick={() => {
                setCatalog(null)
                setAttempt((value) => value + 1)
              }}>Retry</Button>
            )}
          </div>
        )}
      </ComboboxContent>
    </Combobox>
  )
}

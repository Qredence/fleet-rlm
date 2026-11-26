import { ChevronDown } from 'lucide-react'
import { useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import type { FleetApi } from '@/lib/api'
import type { ModelPicker } from './ModelPicker'

export interface ModelSelectorProps {
  api: FleetApi
  model: string | null
  disabled: boolean
  saving: boolean
  onChange: (model: string | null) => void
}

export function ModelSelector(props: ModelSelectorProps) {
  const { model, disabled, saving } = props
  const [Picker, setPicker] = useState<typeof ModelPicker>()
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const loadingRef = useRef(false)
  const modelName = model?.replace(/^provider\/[^/]+\//, '').replace(/^openai\//, '')
  const label = modelName?.replace(/^gpt-/i, 'GPT-') || 'Workspace default'

  async function openPicker() {
    if (disabled || loadingRef.current) return
    loadingRef.current = true
    setLoading(true)
    setLoadError(false)
    try {
      const { ModelPicker } = await import('./ModelPicker')
      setPicker(() => ModelPicker)
    } catch {
      setLoadError(true)
    } finally {
      loadingRef.current = false
      setLoading(false)
    }
  }

  const triggerProps = {
    type: 'button',
    variant: 'ghost',
    size: 'sm',
    className: 'h-8 min-w-0 max-w-60 shrink rounded-(--prompt-inner-radius)',
    'aria-label': loadError ? 'Retry opening model selector' : `Model: ${label}`,
    'aria-busy': saving || loading || undefined,
    'aria-disabled': saving || undefined,
    title: loadError ? 'Model selector could not load. Try again.' : `Model: ${label}`,
  } as const

  const content = <span className="truncate">{loadError ? 'Retry models' : label}</span>

  if (Picker) return <Picker {...props} label={label} trigger={<Button {...triggerProps} />}>{content}</Picker>

  return (
    <Button
      {...triggerProps}
      role="combobox"
      aria-haspopup="listbox"
      aria-expanded={false}
      disabled={disabled}
      onClick={() => void openPicker()}
      onKeyDown={(event) => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
        event.preventDefault()
        void openPicker()
      }}
    >
      {content}
      <ChevronDown className="pointer-events-none size-4 text-muted-foreground" />
    </Button>
  )
}

import { AtSign } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { Skill } from '@/lib/api'

export function SkillPicker({ skills, selected, onChange }: {
  skills: Skill[]
  selected: string[]
  onChange: (names: string[]) => void
}) {
  if (!skills.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5" aria-label="Pinned skills">
      <AtSign className="mr-0.5 size-4 text-muted-foreground" aria-hidden="true" />
      {skills.map(({ name, description }) => {
        const pinned = selected.includes(name)
        return (
          <Badge
            key={name}
            variant={pinned ? 'default' : 'outline'}
            render={<button type="button" />}
            aria-pressed={pinned}
            title={description}
            className="cursor-pointer font-mono"
            onClick={() => onChange(pinned ? selected.filter((item) => item !== name) : [...selected, name])}
          >
            @{name}
          </Badge>
        )
      })}
    </div>
  )
}

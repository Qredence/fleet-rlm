import { ArrowLeft, ChevronRight, Package, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import Markdown from 'react-markdown'
import { Link, NavLink, useParams } from 'react-router'
import remarkGfm from 'remark-gfm'
import { cn } from 'cn'
import { PageHeader } from '@/components/PageHeader'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button, buttonVariants } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { InputGroup, InputGroupAddon, InputGroupInput } from '@/components/ui/input-group'
import { Item, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { errorMessage, type FleetApi, type Skill, type SkillDetail } from '@/lib/api'
import { followMarkdownFragment } from '@/lib/markdown-fragments'

export function CapabilitiesPage({ api, skills, loading, error, onRetry }: {
  api: FleetApi
  skills: Skill[]
  loading: boolean
  error: string
  onRetry: () => void
}) {
  const { name } = useParams()
  const [query, setQuery] = useState('')
  const normalized = query.trim().toLowerCase().replace(/^@/, '')
  const filtered = skills.filter((skill) => `${skill.name} ${skill.description}`.toLowerCase().includes(normalized))

  return (
    <>
      <PageHeader title="Capabilities" />
      <div className="grid min-h-0 min-w-0 flex-1 lg:grid-cols-[minmax(16rem,22rem)_minmax(0,1fr)]">
        <aside aria-label="Skill catalog" className={cn('min-h-0 min-w-0 flex-col lg:border-r', name ? 'hidden lg:flex' : 'flex')}>
          <div className="shrink-0 px-4 pt-4 pb-3">
            <InputGroup>
              <InputGroupInput
                type="search" aria-label="Filter skills" placeholder="Search skills…"
                value={query} onChange={(event) => setQuery(event.target.value)}
              />
              <InputGroupAddon><Search aria-hidden="true" /></InputGroupAddon>
            </InputGroup>
          </div>
          <ScrollArea className="min-h-0 flex-1">
            <div className="flex flex-col gap-3 px-4 pb-4">
              {error && (
                <Alert variant="destructive">
                  <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
                    {error} <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>
                  </AlertDescription>
                </Alert>
              )}
              {loading ? (
                <div role="status" aria-label="Loading skills" className="grid gap-2">
                  <Skeleton className="h-14 w-full motion-reduce:animate-none" />
                  <Skeleton className="h-14 w-full motion-reduce:animate-none" />
                  <Skeleton className="h-14 w-full motion-reduce:animate-none" />
                </div>
              ) : filtered.length ? (
                <ItemGroup aria-label="Skills">
                  {filtered.map((skill) => (
                    <div key={skill.name} role="listitem">
                      <Item
                        size="sm" variant={skill.name === name ? 'muted' : 'default'}
                        render={<NavLink to={`/capabilities/${encodeURIComponent(skill.name)}`} />}
                      >
                        <ItemMedia variant="icon"><Package aria-hidden="true" /></ItemMedia>
                        <ItemContent className="min-w-0">
                          <ItemTitle className="block wrap-anywhere">@{skill.name}</ItemTitle>
                          <ItemDescription>{skill.description}</ItemDescription>
                        </ItemContent>
                        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                      </Item>
                    </div>
                  ))}
                </ItemGroup>
              ) : !error && (
                <Empty>
                  <EmptyHeader><EmptyTitle>{normalized ? 'No matching skills' : 'No skills available'}</EmptyTitle></EmptyHeader>
                </Empty>
              )}
            </div>
          </ScrollArea>
        </aside>
        {name ? <SkillPreview key={name} name={name} api={api} /> : (
          <Empty className="hidden lg:flex">
            <EmptyHeader>
              <EmptyMedia variant="icon"><Package /></EmptyMedia>
              <EmptyTitle>Select a skill</EmptyTitle>
              <EmptyDescription>Open a skill to read its instructions.</EmptyDescription>
            </EmptyHeader>
          </Empty>
        )}
      </div>
    </>
  )
}

function SkillPreview({ api, name }: { api: FleetApi; name: string }) {
  const [skill, setSkill] = useState<SkillDetail>()
  const [error, setError] = useState('')
  const [attempt, retry] = useState(0)
  useEffect(() => {
    const request = new AbortController()
    void api.getSkill(name, request.signal).then((result) => {
      if (!request.signal.aborted) setSkill(result)
    }).catch((failure) => {
      if (!request.signal.aborted) setError(errorMessage(failure))
    })
    return () => request.abort()
  }, [api, name, attempt])

  return (
    <ScrollArea className="min-h-0 min-w-0">
      <article className="mx-auto flex w-full max-w-3xl flex-col gap-5 p-4 sm:p-7" aria-label="Skill preview">
        <Link to="/capabilities" className={buttonVariants({ variant: 'ghost', size: 'sm', className: 'self-start lg:hidden' })}>
          <ArrowLeft data-icon="inline-start" /> All skills
        </Link>
        {error ? (
          <Alert variant="destructive">
            <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
              {error}
              <Button size="sm" variant="outline" onClick={() => { setError(''); retry((value) => value + 1) }}>Retry</Button>
            </AlertDescription>
          </Alert>
        ) : skill ? (
          <>
            <div className="flex flex-col gap-2 border-b pb-4">
              <h2 className="wrap-anywhere text-xl font-semibold">@{skill.name}</h2>
              <p className="text-sm text-muted-foreground">{skill.description}</p>
            </div>
            <div className="markdown" onClick={followMarkdownFragment}>
              <Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']} unwrapDisallowed>
                {skill.body || 'This skill has no instructions.'}
              </Markdown>
            </div>
          </>
        ) : (
          <div role="status" aria-label="Loading skill preview" className="grid gap-3">
            <Skeleton className="h-7 w-1/2 motion-reduce:animate-none" />
            <Skeleton className="h-20 w-full motion-reduce:animate-none" />
          </div>
        )}
      </article>
    </ScrollArea>
  )
}

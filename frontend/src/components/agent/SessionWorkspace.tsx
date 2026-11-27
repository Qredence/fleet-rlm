import { Brain, PanelRight, Terminal } from 'lucide-react'
import { lazy, useEffect, useReducer, useRef, useState, type RefObject } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { Deferred } from '@/components/Deferred'
import { PageHeader } from '@/components/PageHeader'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { Bubble, BubbleContent } from '@/components/ui/bubble'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Message, MessageContent, MessageHeader } from '@/components/ui/message'
import {
  MessageScroller, MessageScrollerButton, MessageScrollerContent, MessageScrollerItem,
  MessageScrollerProvider, MessageScrollerViewport,
} from '@/components/ui/message-scroller'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { DeferredSheet } from '@/components/ui/deferred-sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useMediaQuery } from '@/hooks/use-media-query'
import { useSessionChat } from '@/hooks/use-session-chat'
import { useWorkspaceFiles } from '@/hooks/use-workspace-files'
import { errorMessage, type ChatModel, type FleetApi, type ReasoningEffort, type Session, type SessionUpdate, type Skill } from '@/lib/api'
import type { FleetMessage } from '@/lib/messages'
import { initialWorkspaceSelection, workspaceSelection } from '@/lib/workspace-browser'
import { ChatComposer } from './ChatComposer'
import { ChatPlaceholder } from './ChatPlaceholder'
import { ModelSelector } from './ModelSelector'
import { WorkspaceAttachments } from './WorkspaceAttachments'
import { WorkspaceSidePanel } from './WorkspaceSidePanel'

const FilePreview = lazy(() => import('./FilePreview').then(({ FilePreview }) => ({ default: FilePreview })))
const MessageParts = lazy(() => import('./MessageParts').then(({ MessageParts }) => ({ default: MessageParts })))
const paneLayout = 'flex w-full min-h-0 min-w-0 flex-col'
const dockedPaneClassName = `${paneLayout} my-2 h-[calc(100%-1rem)] overflow-hidden bg-surface-2`

interface SessionWorkspaceProps {
  api: FleetApi
  token: string
  session: Session
  skills: Skill[]
  skillsError: string
  visible: boolean
  recentStops: RefObject<Map<string, number>>
  filesDocked: boolean
  setFilesDocked: (value: boolean) => void
  onActivity: (id: string | null) => void
  onTurnAccepted: () => void
  onTurnComplete: () => void
  onFilesUploaded: () => void
  onSkillInstalled: (skill: Skill) => void
  settingsOpen: boolean
  onChatSettingsChange: (changes: SessionUpdate) => Promise<Session>
}

const effortLabels: Record<ReasoningEffort | 'default', string> = {
  default: 'Default', none: 'None', minimal: 'Minimal', low: 'Low', medium: 'Medium',
  high: 'High', xhigh: 'Extra high', max: 'Max',
}

export function SessionWorkspace({
  api, token, session, skills, skillsError, visible, recentStops, filesDocked, setFilesDocked,
  onActivity, onTurnAccepted, onTurnComplete, onFilesUploaded, onSkillInstalled, settingsOpen, onChatSettingsChange,
}: SessionWorkspaceProps) {
  const [initialMessages, setInitialMessages] = useState<FleetMessage[] | null>(null)
  const [transcriptError, setTranscriptError] = useState('')
  const [attempt, retry] = useState(0)
  const [prompt, setPrompt] = useState('')
  const [skillDraft, setSkillDraft] = useState<string | null>(null)
  const [receivedDraftKey, setReceivedDraftKey] = useState<string | null>(null)
  const [pinned, setPinned] = useState<string[]>([])
  const [modelSaving, setModelSaving] = useState(false)
  const [modelError, setModelError] = useState('')
  const [modelInfo, setModelInfo] = useState<ChatModel>()
  const [modelReload, reloadModel] = useState(0)
  const [modelInfoError, setModelInfoError] = useState(false)
  const modelSavePending = useRef(false)
  const [filesDrawerOpen, setFilesDrawerOpen] = useState(false)
  const [selection, selectFile] = useReducer(workspaceSelection, initialWorkspaceSelection)
  const filesTrigger = useRef<HTMLButtonElement>(null)
  const restoreFilesFocus = useRef(false)
  const wide = useMediaQuery('(min-width: 80rem)')
  const files = useWorkspaceFiles(api, session.id, onFilesUploaded)
  const { refresh } = files
  const location = useLocation()
  const navigate = useNavigate()
  const incomingDraft: unknown = location.state?.skillDraft
  if (visible && typeof incomingDraft === 'string' && receivedDraftKey !== location.key) {
    setReceivedDraftKey(location.key)
    setSkillDraft(incomingDraft)
  }

  useEffect(() => {
    if (!visible || typeof incomingDraft !== 'string') return
    const { pathname, search, hash } = location
    void navigate({ pathname, search, hash }, { replace: true, state: null })
  }, [incomingDraft, location, navigate, visible])

  useEffect(() => {
    const request = new AbortController()
    void api.getMessages(session.id, request.signal).then((items) => {
      if (!request.signal.aborted) setInitialMessages(items)
    }).catch((failure) => {
      if (!request.signal.aborted) setTranscriptError(errorMessage(failure))
    })
    return () => request.abort()
  }, [api, session.id, attempt])

  useEffect(() => {
    if (!visible || settingsOpen) return
    let request: AbortController
    function refreshModel() {
      request?.abort()
      const next = new AbortController()
      request = next
      void api.getChatModel(session.id, next.signal).then((model) => {
        if (next.signal.aborted) return
        setModelInfo(model)
        setModelInfoError(false)
      }).catch(() => {
        if (next.signal.aborted) return
        setModelInfo(undefined)
        setModelInfoError(true)
      })
    }
    refreshModel()
    window.addEventListener('focus', refreshModel)
    return () => {
      window.removeEventListener('focus', refreshModel)
      request?.abort()
    }
  }, [api, session.id, session.model_override, session.reasoning_override?.effort, visible, settingsOpen, modelReload])

  const chat = useSessionChat({
    id: session.id, token, initialMessages, recentStops,
    onAccepted: onTurnAccepted,
    onComplete: () => {
      refresh()
      onTurnComplete()
    },
  })
  const { messages, status, error, preparing, waitingForStop, sendError, active, stream } = chat
  const observer = stream && <stream.Observer chat={stream.chat} onChange={chat.setSnapshot} />
  const latestPromptIndex = messages.findLastIndex((message) => message.role === 'user')

  useEffect(() => {
    if (!active) return
    onActivity(session.id)
    return () => onActivity(null)
  }, [active, onActivity, session.id])

  if ((!visible || wide) && filesDrawerOpen) setFilesDrawerOpen(false)
  if (!visible && selection.path) selectFile({ type: 'close' })

  async function submit(text: string) {
    if (!text || active || modelSavePending.current || files.isUploading() || files.hasFailed) return
    await chat.send(text, pinned, () => {
      if (skillDraft === null) setPrompt((current) => current.trim() === text ? '' : current)
      else setSkillDraft((current) => current?.trim() === text ? null : current)
      files.clearCompleted()
    })
  }

  async function saveChatSettings(changes: SessionUpdate) {
    if (modelSavePending.current || chat.isBusy()) return
    modelSavePending.current = true
    setModelSaving(true)
    setModelError('')
    try {
      await onChatSettingsChange(changes)
      if ('model_override' in changes) setModelInfo(undefined)
    } catch (failure) {
      setModelError(errorMessage(failure))
    } finally {
      reloadModel((previous) => previous + 1)
      modelSavePending.current = false
      setModelSaving(false)
    }
  }

  const dockFiles = wide && filesDocked
  useEffect(() => {
    if (!visible || !restoreFilesFocus.current) return
    restoreFilesFocus.current = false
    filesTrigger.current?.focus()
  }, [dockFiles, visible])

  if (!visible) return <>{observer}</>

  const canUpload = !active && !files.uploading
  const upload = (batch: File[]) => {
    if (canUpload && !chat.isBusy()) files.add(batch)
  }
  const retryUpload = (id: number) => {
    if (canUpload && !chat.isBusy()) files.retry(id)
  }
  const attachments = (
    <WorkspaceAttachments attachments={files.attachments} canUpload={canUpload} onRetry={retryUpload} onDismiss={files.dismiss} />
  )
  const filesOpen = wide ? dockFiles : filesDrawerOpen

  function openFromChat(path: string) {
    selectFile({ type: 'open', path, fromChat: true })
    if (wide) setFilesDocked(true)
    else setFilesDrawerOpen(true)
  }

  function closePreview() {
    selectFile({ type: 'close', restoreFocus: true })
  }

  const previewPath = selection.path
  const previewFile = files.files?.find(({ path }) => path === previewPath)
  const filesToggle = (
    <Tooltip>
      <TooltipTrigger render={<Button
        ref={filesTrigger} variant={filesOpen ? 'secondary' : 'ghost'} size="icon-sm"
        aria-label="Toggle Files panel" aria-expanded={filesOpen} aria-haspopup={wide ? undefined : 'dialog'}
        onClick={() => {
          if (wide) {
            restoreFilesFocus.current = true
            setFilesDocked(!dockFiles)
          } else {
            setFilesDrawerOpen(!filesDrawerOpen)
          }
        }}
      />}>
        <PanelRight aria-hidden="true" />
      </TooltipTrigger>
      <TooltipContent>Toggle Files panel</TooltipContent>
    </Tooltip>
  )
  const inspector = (
    <WorkspaceSidePanel
      session={session} files={files.files} error={files.error} canUpload={canUpload}
      attachments={wide ? undefined : attachments}
      wide={wide} tab={selection.tab} selectedPath={previewPath}
      reveal={selection.reveal} focusPath={selection.focus}
      onTabChange={(tab) => selectFile({ type: 'tab', tab })}
      onBack={closePreview}
      filesToggle={dockFiles ? filesToggle : undefined}
      onUpload={upload} onPreview={(path) => selectFile({ type: 'open', path })} onDownload={files.download}
      filePreview={previewPath && (
        <Deferred fallback={<Skeleton role="status" aria-label="Loading preview" className="m-3 h-32 motion-reduce:animate-none" />}>
          <FilePreview
            key={`${previewPath}:${previewFile?.revision ?? previewFile?.sha256 ?? ''}`} api={api} sessionId={session.id} path={previewPath}
            onClose={wide ? closePreview : undefined}
            onDownload={() => api.downloadFile(session.id, previewPath)}
            onSkillInstalled={onSkillInstalled}
          />
        </Deferred>
      )}
    />
  )

  return (
    <>
      {observer}
      <ResizablePanelGroup orientation="horizontal" className="min-h-0 flex-1">
        <ResizablePanel id="chat" minSize={dockFiles ? "22rem" : undefined}>
          <section className={dockFiles ? `${dockedPaneClassName} rounded-l-xl` : `${paneLayout} h-full`} aria-label="Chat">
            <PageHeader title={session.title}>
              {!dockFiles && filesToggle}
            </PageHeader>
            {initialMessages === null ? (
              <ChatPlaceholder showHeader={false} loading={!transcriptError} error={transcriptError} actionLabel="Retry" onAction={() => {
                setTranscriptError('')
                retry((current) => current + 1)
              }} />
            ) : <Deferred fallback={(
              <div role="status" aria-label="Loading transcript" className="min-h-0 flex-1 p-4">
                <Skeleton className="h-24 w-full motion-reduce:animate-none" />
              </div>
            )}>
              <MessageScrollerProvider autoScroll defaultScrollPosition="last-anchor">
                <MessageScroller className="flex-1">
                  <MessageScrollerViewport>
                    <MessageScrollerContent className="mx-auto w-full max-w-3xl px-3 py-5 sm:px-6 sm:py-7" aria-label="Conversation">
                      {messages.length ? messages.map((message, index) => {
                        const user = message.role === 'user'
                        return (
                          <MessageScrollerItem
                            key={message.id}
                            messageId={message.id}
                            scrollAnchor={user}
                            // Measure the latest turn at its real height before positioning the saved transcript.
                            className={latestPromptIndex >= 0 && index >= latestPromptIndex ? '[content-visibility:visible]' : undefined}
                          >
                            <Message align={user ? 'end' : 'start'} role="article" aria-label={user ? 'You' : 'Fleet'}>
                              <MessageContent>
                                {user ? (
                                  <Bubble variant="muted" align="end" className="max-w-[90%] sm:max-w-[80%]">
                                    <BubbleContent>
                                      <MessageParts parts={message.parts} preview={openFromChat} download={files.download} />
                                    </BubbleContent>
                                  </Bubble>
                                ) : (
                                  <>
                                    <MessageHeader className="gap-2 px-0">
                                      <Terminal className="size-4" aria-hidden="true" /> Fleet
                                    </MessageHeader>
                                    <MessageParts
                                      parts={message.parts} live={active && index === messages.length - 1}
                                      preview={openFromChat} download={files.download}
                                      refine={() => api.refineTurn(session.id, message.id)}
                                    />
                                  </>
                                )}
                              </MessageContent>
                            </Message>
                          </MessageScrollerItem>
                        )
                      }) : (
                        <MessageScrollerItem className="flex flex-1 items-center">
                          <Empty>
                            <EmptyHeader>
                              <EmptyMedia variant="icon"><Terminal /></EmptyMedia>
                              <EmptyTitle>Start a conversation</EmptyTitle>
                              <EmptyDescription>
                                Ask Fleet a question or attach files from your device.
                                {skills.length > 0 && ' Type @ to pin a skill.'}
                              </EmptyDescription>
                            </EmptyHeader>
                          </Empty>
                        </MessageScrollerItem>
                      )}
                    </MessageScrollerContent>
                  </MessageScrollerViewport>
                  <MessageScrollerButton aria-label="Jump to latest message" />
                </MessageScroller>
              </MessageScrollerProvider>
            </Deferred>}
            <div className="mx-auto flex w-full max-w-3xl shrink-0 flex-col gap-2 px-3 pb-3 sm:px-6 sm:pb-5">
              {(modelError || sendError || error || skillsError) && (
                <Alert variant="destructive"><AlertDescription>{modelError || sendError || error?.message || skillsError}
                  {(sendError || error?.message)?.includes('Choose another model and retry') && latestPromptIndex >= 0 && (
                    <Button variant="outline" size="sm" disabled={active || modelSaving} onClick={() => {
                      const text = messages[latestPromptIndex].parts.flatMap(part => part.type === 'text' ? [part.text] : []).join(' ')
                      void submit(text)
                    }}>Retry last request with selected model</Button>
                  )}</AlertDescription></Alert>
              )}
              {active && (
                <p role="status" className="px-1 text-xs text-muted-foreground">
                  <span className={preparing || status === 'submitted' ? 'shimmer motion-reduce:shimmer-none' : undefined}>
                    {preparing ? 'Loading chat client…' : waitingForStop ? 'Stopping previous run…' : status === 'submitted' ? 'Starting run…' : 'Run in progress…'}
                  </span>
                </p>
              )}
              {skillDraft !== null && (
                <Button className="self-end" variant="ghost" size="sm" onClick={() => setSkillDraft(null)}>
                  Cancel skill draft
                </Button>
              )}
              <ChatComposer
                skills={skills} pinned={pinned} setPinned={setPinned} value={skillDraft ?? prompt}
                onValueChange={skillDraft === null ? setPrompt : setSkillDraft}
                onSubmit={submit} onStop={chat.stop} loading={active}
                submitDisabled={initialMessages === null || modelSaving || files.uploading || files.hasFailed}
                attachments={attachments} onUpload={canUpload ? upload : undefined}
                modelSelector={<div className="flex min-w-0 items-center gap-1">
                  {modelInfo && !modelInfoError && modelInfo.efforts.length > 0 && (
                    <Select value={modelInfo.effort ?? 'default'} disabled={active || modelSaving} onValueChange={(value) => {
                      const effort = modelInfo.efforts.find((item) => item === value)
                      if (value === 'default') void saveChatSettings({ reasoning_override: null })
                      else if (effort) void saveChatSettings({ reasoning_override: { model: modelInfo.id, effort } })
                    }}>
                      <SelectTrigger
                        icon={Brain}
                        variant="borderless"
                        size="compact"
                        aria-label={`Thinking effort: ${effortLabels[modelInfo.effort ?? 'default']}`}
                        title={`Thinking effort: ${effortLabels[modelInfo.effort ?? 'default']}`}
                        className="h-8 min-w-0 max-w-28 text-xs"
                      />
                      <SelectContent>
                        <SelectItem index={0} value="default">Default</SelectItem>
                        {modelInfo.efforts.map((effort, index) => (
                          <SelectItem key={effort} index={index + 1} value={effort}>{effortLabels[effort]}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  <ModelSelector
                    api={api} model={session.model_override} disabled={active || modelSaving} saving={modelSaving}
                    resolvedLabel={modelInfo?.label ?? (session.model_override ? undefined : modelInfoError ? 'Model unavailable' : 'Loading model…')}
                    onChange={(model) => {
                      if (model !== session.model_override) void saveChatSettings({ model_override: model })
                    }}
                  />
                </div>}
              />
            </div>
          </section>
        </ResizablePanel>
        {dockFiles && (
          <>
            <ResizableHandle withHandle aria-label="Resize Files panel" className="my-2 bg-foreground/20" />
            <ResizablePanel id="files" defaultSize="48%" minSize="32rem" maxSize="64%">
              <aside aria-label="Workspace files" className={`${dockedPaneClassName} rounded-r-xl`}>
                {inspector}
              </aside>
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
      {!wide && (
        <DeferredSheet
          open={filesDrawerOpen} onOpenChange={setFilesDrawerOpen} title="Workspace files"
          headerClassName="sr-only"
          content={{ side: 'right', finalFocus: filesTrigger, className: 'w-[min(100%,32rem)] gap-0 data-[side=right]:w-[min(100%,32rem)] data-[side=right]:sm:max-w-none [&>[data-slot=sheet-close]]:top-2' }}
        >
          {inspector}
        </DeferredSheet>
      )}
    </>
  )
}

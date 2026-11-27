import { useState } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field, FieldLabel } from '@/components/ui/field'
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger } from '@/components/ui/select'
import { errorMessage, type ChatGPTPlanStatus, type FleetApi } from '@/lib/api'

const usageUrl = 'https://chatgpt.com/settings/usage'

export function ChatGPTPlanSettings({ api, status, onChange, modelsOnly }: {
  api: FleetApi
  status: ChatGPTPlanStatus
  onChange: () => Promise<void>
  modelsOnly?: boolean
}) {
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const active = status.profiles.find((profile) => profile.id === status.active)

  async function perform(run: () => Promise<unknown>, reloadStatus = true) {
    setPending(true)
    setMessage('')
    setError('')
    try {
      await run()
      if (reloadStatus) await onChange()
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setPending(false)
    }
  }

  function connect(profileId?: string) {
    const consent = window.open('about:blank', '_blank')
    if (!consent) {
      setError('Allow Fleet to open a new tab, then try again.')
      return
    }
    consent.opener = null
    void perform(() => api.startChatGPT(profileId).then(({ url }) => {
      consent.location.replace(url)
    }).catch((failure) => {
      consent.close()
      throw failure
    }), false)
  }

  return (
    <section aria-label={modelsOnly ? 'Local ChatGPT plan models' : 'ChatGPT plan'} className="grid gap-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{modelsOnly ? 'Local ChatGPT Plan Models' : 'ChatGPT Plan'}</h3>
        {active?.status === 'connected' && !modelsOnly && <Badge variant="secondary">Connected</Badge>}
      </div>
      {modelsOnly ? (
        <>
          <p className="text-xs text-muted-foreground">These choices apply to chat on this computer and save immediately.</p>
          {(['primary', 'sub'] as const).map((role) => {
            const current = status.defaults[role]
            const unavailable = current && !status.models.some((model) => model.id === current)
            const label = role === 'primary' ? 'ChatGPT plan primary model' : 'ChatGPT plan sub-model'
            return (
              <Field key={role}>
                <FieldLabel htmlFor={`chatgpt-default-${role}`}>{label}</FieldLabel>
                <Select value={current ?? 'workspace'} disabled={pending} onValueChange={(value) => {
                  if (value) void perform(() => api.updateChatGPTDefaults({ [role]: value === 'workspace' ? null : value }))
                }}>
                  <SelectTrigger id={`chatgpt-default-${role}`} aria-label={label} />
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value="workspace" index={0}>Use workspace default</SelectItem>
                      {unavailable && (
                        <SelectItem value={current} index={1} disabled>Selected ChatGPT model unavailable</SelectItem>
                      )}
                      {status.models.map((model, index) => (
                        <SelectItem key={model.id} value={model.id} index={index + (unavailable ? 2 : 1)}>{model.label}</SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </Field>
            )
          })}
        </>
      ) : (
        <>
          {status.profiles.length > 0 && (
            <Field>
              <FieldLabel htmlFor="chatgpt-account">ChatGPT account</FieldLabel>
              <Select value={status.active ?? undefined} disabled={pending} onValueChange={(id) => {
                if (id) void perform(() => api.selectChatGPT(id))
              }}>
                <SelectTrigger id="chatgpt-account" aria-label="ChatGPT account" />
                <SelectContent>
                  <SelectGroup>
                    {status.profiles.map((profile, index) => (
                      <SelectItem key={profile.id} value={profile.id} index={index}>{profile.label}</SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
          )}
          {active && active.status !== 'connected' && (
            <p className="text-xs text-muted-foreground">
              {active.status === 'consent_required' ? 'Reconnect and approve ChatGPT plan usage.'
                : active.status === 'signed_out' ? 'This account is signed out.' : 'Reconnect this account to use its plan.'}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" data-oai-siwc-button="" disabled={pending} onClick={() => connect()}>
              {status.profiles.length ? 'Add ChatGPT account' : 'Continue with ChatGPT'}
            </Button>
            {active && (
              <Button type="button" size="sm" variant="outline" data-oai-siwc-button="" disabled={pending}
                onClick={() => connect(active.id)}>
                Reconnect
              </Button>
            )}
            {active?.status === 'connected' && (
              <Button type="button" size="sm" variant="outline" disabled={pending}
                onClick={() => void perform(() => api.refreshChatGPTModels(active.id))}>Refresh models</Button>
            )}
            {active && active.status !== 'signed_out' && (
              <Button type="button" size="sm" variant="ghost" disabled={pending}
                onClick={() => void perform(async () => {
                  const result = await api.signOutChatGPT(active.id)
                  if (!result.revocation_confirmed) {
                    setMessage('Signed out locally. Remote revocation could not be confirmed. Disconnect Fleet in ChatGPT settings.')
                  }
                })}>Sign out</Button>
            )}
            <a href={usageUrl} target="_blank" rel="noreferrer" className="text-sm underline underline-offset-4">Manage usage</a>
          </div>
        </>
      )}
      {message && <p role="status" className="text-xs text-muted-foreground">{message}</p>}
      {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}
    </section>
  )
}

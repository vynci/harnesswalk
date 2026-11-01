// The bring-your-own-key button of the composer. It works where the site offers it (private
// mode): the key lives in this tab's memory only. In public mode it stays visible and disabled,
// with the reason as its tooltip (useFeature('byo_key_ui')): keys arrive with accounts.
import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { InputGroupButton } from '@/components/ui/input-group'
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from '@/components/ui/popover'
import { useFeature } from '@/state/hooks'
import { useApp } from '@/state/store'

/**
 * Bring your own key: a password field in a popover. The key stays in this tab's memory, goes
 * out as the provider key header with each turn, and is gone on reload or Clear.
 */
export function ByoKeyButton() {
  const feature = useFeature('byo_key_ui')
  if (!feature.enabled) {
    return (
      <Hint label={feature.reason} side="top">
        <InputGroupButton
          size="xs"
          className="text-muted-foreground aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
          aria-disabled="true"
          aria-label={`Bring your own key (${feature.reason})`}
          onClick={(e) => e.preventDefault()}
          data-testid="byo-key-button"
          data-set="false"
          data-disabled="true"
        >
          <KeyRound aria-hidden />
          Key
        </InputGroupButton>
      </Hint>
    )
  }
  return <ByoKeyPopover />
}

function ByoKeyPopover() {
  const key = useApp((s) => s.byoKey)
  const setByoKey = useApp((s) => s.setByoKey)
  const bypass = useApp((s) => s.capabilities?.public?.byo_key_bypasses_budget !== false)
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState(false)
  const label = key ? 'Your key is set for this tab' : 'Bring your own key'
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Hint label={label} side="top">
        <PopoverTrigger asChild>
          <InputGroupButton size="xs" className="text-muted-foreground" aria-label={label} data-testid="byo-key-button" data-set={key ? 'true' : 'false'}>
            <KeyRound aria-hidden className={key ? 'text-brand' : undefined} />
            {key ? 'Your key' : 'Key'}
          </InputGroupButton>
        </PopoverTrigger>
      </Hint>
      <PopoverContent side="top" align="start" className="w-80" data-testid="byo-key-popover">
        <PopoverHeader>
          <PopoverTitle>Your Anthropic API key</PopoverTitle>
          <PopoverDescription data-testid="byo-key-note">Held in this tab only, sent with each request, never stored.</PopoverDescription>
        </PopoverHeader>
        {key ? (
          <div className="flex items-center gap-2 text-sm">
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground" data-testid="byo-key-masked">
              key ending {key.slice(-4)}
            </span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                setByoKey(null)
                setDraft('')
              }}
              data-testid="byo-key-clear"
            >
              Clear
            </Button>
          </div>
        ) : (
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (!draft.trim()) return
              setByoKey(draft)
              setDraft('')
              setOpen(false)
            }}
          >
            <Input
              type="password"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="sk-ant-…"
              autoComplete="off"
              spellCheck={false}
              aria-label="Your Anthropic API key"
              className="h-8 font-mono text-xs md:text-xs"
              data-testid="byo-key-input"
            />
            <Button type="submit" size="sm" disabled={!draft.trim()} data-testid="byo-key-save">
              Use
            </Button>
          </form>
        )}
        <p className="text-xs text-muted-foreground">
          {bypass ? 'With your key the Claude models are available even when the shared budget is spent, and your turns do not count against it.' : 'With your key the Claude models use your account.'}
        </p>
      </PopoverContent>
    </Popover>
  )
}

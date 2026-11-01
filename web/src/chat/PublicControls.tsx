// Public mode in the composer (phase-6 CONTRACT section 6): the Model Selector over
// `capabilities.public.models`, with the models it cannot offer disabled and their reason as a
// tooltip, and the bring-your-own-key popover. The key lives in this tab's memory only.
import { Fragment, useState } from 'react'
import { ChevronDown, KeyRound } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { InputGroupButton } from '@/components/ui/input-group'
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from '@/components/ui/popover'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { effectiveModel, modelReasonShort, resetTime, type PickerModel } from '@/state/features'
import { usePickerModels } from '@/state/hooks'
import { useApp } from '@/state/store'

const PROVIDER_LABEL: Record<string, string> = { anthropic: 'Claude', fake: 'Scripted' }

function byProvider(models: PickerModel[]): [string, PickerModel[]][] {
  const out = new Map<string, PickerModel[]>()
  for (const m of models) out.set(m.provider, [...(out.get(m.provider) ?? []), m])
  return [...out]
}

/** A model the reader cannot pick: the item is disabled, so the tooltip hangs on a wrapping span. */
function UnavailableModel({ model }: { model: PickerModel }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="block" data-testid="model-option" data-model={model.alias} data-available="false" data-reason={model.reason ?? ''}>
          <DropdownMenuRadioItem value={model.alias} disabled className="items-start">
            <span className="flex flex-col gap-0.5">
              <span>{model.label}</span>
              <span className="text-xs text-muted-foreground" aria-hidden>
                {modelReasonShort(model.reason)}
              </span>
              <span className="sr-only">{model.reasonText}</span>
            </span>
          </DropdownMenuRadioItem>
        </span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-64" data-testid="model-reason">
        {model.reasonText}
      </TooltipContent>
    </Tooltip>
  )
}

/** The Model Selector for a public turn: the default preselected, unavailable models disabled. */
export function PublicModelPicker() {
  const models = usePickerModels()
  const chosen = useApp((s) => s.publicModel)
  const setPublicModel = useApp((s) => s.setPublicModel)
  const budget = useApp((s) => s.capabilities?.public?.budget)
  if (models.length === 0) return null
  const current = effectiveModel(models, chosen)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <InputGroupButton size="xs" className="text-muted-foreground" aria-label={`Model: ${current?.label ?? 'none available'}`} data-testid="model-picker" data-model={current?.alias ?? ''}>
          {current?.label ?? 'No model available'}
          <ChevronDown aria-hidden />
        </InputGroupButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-64" data-testid="model-menu">
        <DropdownMenuRadioGroup value={current?.alias ?? ''} onValueChange={setPublicModel}>
          {byProvider(models).map(([provider, list], i) => (
            <Fragment key={provider}>
              {i > 0 && <DropdownMenuSeparator />}
              <DropdownMenuLabel>{PROVIDER_LABEL[provider] ?? provider}</DropdownMenuLabel>
              {list.map((m) =>
                m.available ? (
                  <DropdownMenuRadioItem key={m.alias} value={m.alias} data-testid="model-option" data-model={m.alias} data-available="true">
                    {m.label}
                  </DropdownMenuRadioItem>
                ) : (
                  <UnavailableModel key={m.alias} model={m} />
                ),
              )}
            </Fragment>
          ))}
        </DropdownMenuRadioGroup>
        {budget && (
          <>
            <DropdownMenuSeparator />
            <p className="px-2 py-1.5 text-xs text-muted-foreground tabular-nums" data-testid="budget-line" data-status={budget.status}>
              {budget.status === 'exhausted'
                ? `Today's shared Claude budget is spent. It resets at ${resetTime(budget.resets_at)}.`
                : `Shared Claude budget today: ${Math.round((budget.spent_fraction ?? 0) * 100)}% used.`}
            </p>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * Bring your own key: a password field in a popover. The key stays in this tab's memory, goes
 * out as the provider key header with each turn, and is gone on reload or Clear.
 */
export function ByoKeyButton() {
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

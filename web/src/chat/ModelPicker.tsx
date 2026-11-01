// The composer's model controls, one set for every mode (picker fixes): the Model Selector with
// Auto first (the router decides, so the turn names no model) and the concrete models after it,
// the Fixture select beside it while the next turn runs on the scripted model, and the banner
// above the composer while today's shared Claude budget is used up.
import { Fragment } from 'react'
import { Select as SelectPrimitive } from 'radix-ui'
import { CheckIcon, ChevronDown, Clock, FlaskConical, X } from 'lucide-react'
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
import { InputGroupButton } from '@/components/ui/input-group'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { autoModel, effectiveModel, fixtureCatalog, fixtureGroups, fixtureVisible, resetPhrase, selectedModel, type PickerModel } from '@/state/features'
import { usePickerModels } from '@/state/hooks'
import { selectBudgetBanner, useApp } from '@/state/store'

const AUTO = '__auto__'
const SERVER_DEFAULT = '__default__'

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: 'Claude',
  fake: 'Scripted',
  ollama: 'Local (Ollama)',
  openai_compatible: 'OpenAI-compatible',
}

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
              <span className="text-xs text-muted-foreground" aria-hidden data-testid="model-reason-short">
                {model.reasonShort}
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

/**
 * The Model Selector. Auto is first and the default: the turn names no model and the router
 * picks, and the hint under it says what that is right now (the alias the API flags `default`,
 * which moves to the fake model when the shared budget is spent). The concrete models follow,
 * grouped by provider, the ones that cannot answer disabled with their reason.
 */
export function ModelPicker() {
  const models = usePickerModels()
  const picked = useApp((s) => s.pickedModel)
  const pickModel = useApp((s) => s.pickModel)
  const budget = useApp((s) => (s.capabilities?.mode === 'public' ? s.capabilities.public?.budget : undefined))
  if (models.length === 0) return null
  const selected = selectedModel(models, picked)
  const auto = autoModel(models)
  const current = selected ?? auto
  const autoHint = auto ? `Currently ${auto.label}` : 'No model can answer right now'
  const label = selected ? selected.label : 'Auto'
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <InputGroupButton
          size="xs"
          className="text-muted-foreground"
          aria-label={`Model: ${selected ? selected.label : `Auto (router decides), ${autoHint.toLowerCase()}`}`}
          data-testid="model-picker"
          data-model={current?.alias ?? ''}
          data-selected={selected ? selected.alias : 'auto'}
        >
          {label}
          <ChevronDown aria-hidden />
        </InputGroupButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-64" data-testid="model-menu">
        <DropdownMenuRadioGroup value={selected?.alias ?? AUTO} onValueChange={(v) => pickModel(v === AUTO ? null : v)}>
          <DropdownMenuRadioItem value={AUTO} className="items-start" data-testid="model-option" data-model="auto" data-available="true">
            <span className="flex flex-col gap-0.5">
              <span>Auto (router decides)</span>
              <span className="text-xs text-muted-foreground" data-testid="auto-hint" data-model={auto?.alias ?? ''}>
                {autoHint}
              </span>
            </span>
          </DropdownMenuRadioItem>
          {byProvider(models).map(([provider, list]) => (
            <Fragment key={provider}>
              <DropdownMenuSeparator />
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
                ? `Today's shared Claude budget is used up. Back at ${resetPhrase(budget.resets_at)}.`
                : `Shared Claude budget today: ${Math.round((budget.spent_fraction ?? 0) * 100)}% used.`}
            </p>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** One fixture in the select: its name (what the trigger shows) and, under it, its description. */
function FixtureItem({ name, description }: { name: string; description: string | null }) {
  return (
    <SelectPrimitive.Item
      value={name}
      textValue={name}
      data-slot="select-item"
      className="relative flex min-h-7 w-full cursor-default flex-col items-start gap-0.5 rounded-md py-1.5 pr-8 pl-2 text-sm outline-hidden select-none focus:bg-accent focus:text-accent-foreground data-disabled:pointer-events-none data-disabled:opacity-50"
    >
      <span className="pointer-events-none absolute top-2 right-2 flex size-4 items-center justify-center">
        <SelectPrimitive.ItemIndicator>
          <CheckIcon className="pointer-events-none size-4" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{name}</SelectPrimitive.ItemText>
      {description && (
        <span className="line-clamp-2 text-xs text-muted-foreground" aria-hidden data-testid="fixture-description">
          {description}
        </span>
      )}
    </SelectPrimitive.Item>
  )
}

/**
 * The Fixture select, beside the Model Selector while the next turn runs on the scripted model
 * (picked, or what Auto resolves to): which canned script the fake model plays. Its first entry
 * is the server's own (public_demo on the public site, the start-up fixture in dev). It sits in
 * the header, left of the pack picker, while the next turn runs on the fake model.
 */
export function FixturePicker() {
  const capabilities = useApp((s) => s.capabilities)
  const packs = useApp((s) => s.packs)
  const pack = useApp((s) => s.pack)
  const fixture = useApp((s) => s.fixture)
  const setFixture = useApp((s) => s.setFixture)
  const provider = useApp((s) => s.provider)
  const picked = useApp((s) => s.pickedModel)
  const mock = useApp((s) => s.mock)
  const models = usePickerModels()
  if (!fixtureVisible(capabilities, effectiveModel(models, picked), provider)) return null
  const catalog = fixtureCatalog(capabilities)
  // The session pack's own fixtures first, then the shared ones; other packs' are hidden.
  const groups = fixtureGroups(catalog, packs, pack)
  const value = fixture && groups.all.includes(fixture) ? fixture : SERVER_DEFAULT
  const serverDefault = catalog.defaultName ? `${catalog.defaultName} (default)` : mock ? 'by message' : 'default'
  // The trigger names the script only; the menu says which entry is the server's default. No
  // tooltip on it: Escape would close the tooltip before the open list.
  const shown = value === SERVER_DEFAULT ? (catalog.defaultName ?? serverDefault) : value
  const item = (name: string) => <FixtureItem key={name} name={name} description={catalog.descriptions[name] ?? null} />
  return (
    <Select value={value} onValueChange={(v) => setFixture(v === SERVER_DEFAULT ? null : v)}>
      <SelectPrimitive.Trigger asChild>
        <Button size="sm" variant="outline" className="max-w-56 text-muted-foreground" aria-label={`Fixture: ${shown} (the scripted answer of the fake model)`} data-testid="fixture-select" data-fixture={fixture ?? ''}>
          <FlaskConical aria-hidden />
          <span className="truncate">
            <SelectPrimitive.Value>{shown}</SelectPrimitive.Value>
          </span>
          <ChevronDown aria-hidden />
        </Button>
      </SelectPrimitive.Trigger>
      <SelectContent position="popper" side="bottom" align="end" className="max-h-80 w-72" data-testid="fixture-menu">
        <SelectItem value={SERVER_DEFAULT}>{serverDefault}</SelectItem>
        {groups.own.length > 0 && (
          <SelectGroup data-testid="fixture-group-pack">
            <SelectLabel>This pack</SelectLabel>
            {groups.own.map(item)}
          </SelectGroup>
        )}
        {groups.own.length > 0 && groups.shared.length > 0 && <SelectSeparator />}
        {groups.shared.length > 0 && (
          <SelectGroup data-testid="fixture-group-shared">
            {groups.own.length > 0 && <SelectLabel>Shared</SelectLabel>}
            {groups.shared.map(item)}
          </SelectGroup>
        )}
      </SelectContent>
    </Select>
  )
}

/**
 * Public mode: a slim banner above the composer while today's shared Claude budget is used up
 * (or right after a turn fell through to the fake model), with the reset time. Closing it holds
 * for this budget day in this tab's session.
 */
export function BudgetBanner() {
  const resetsAt = useApp(selectBudgetBanner)
  const dismiss = useApp((s) => s.dismissBudgetBanner)
  if (resetsAt === null) return null
  const text = `Today's shared Claude budget is used up. You're on the scripted fake model until ${resetPhrase(resetsAt || null)}.`
  return (
    <div role="status" className="flex items-center gap-2 rounded-lg border bg-muted/60 py-1 pr-1 pl-3 text-xs text-muted-foreground" data-testid="budget-banner">
      <Clock className="size-3.5 shrink-0" aria-hidden />
      <span className="min-w-0 flex-1">{text}</span>
      <Button type="button" size="icon-xs" variant="ghost" aria-label="Dismiss the budget notice" onClick={dismiss} data-testid="budget-banner-dismiss">
        <X />
      </Button>
    </div>
  )
}

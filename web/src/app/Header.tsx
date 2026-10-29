import { Monitor, Moon, RotateCcw, Sun } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { useApp, type Theme } from '@/state/store'

const NEXT_THEME: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
const AUTO = '__auto__'

function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md border p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            'rounded px-2 py-0.5 text-xs focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
            value === o.value ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
          data-testid={`view-${o.value}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Header() {
  const capabilities = useApp((s) => s.capabilities)
  const mock = useApp((s) => s.mock)
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const stepMode = useApp((s) => s.stepMode)
  const setStepMode = useApp((s) => s.setStepMode)
  const theme = useApp((s) => s.theme)
  const setTheme = useApp((s) => s.setTheme)
  const provider = useApp((s) => s.provider)
  const setProvider = useApp((s) => s.setProvider)
  const fixture = useApp((s) => s.fixture)
  const setFixture = useApp((s) => s.setFixture)
  const newSession = useApp((s) => s.newSession)
  const invalidEvents = useApp((s) => s.invalidEvents)
  const disabled = useApp((s) => s.disabled)
  const dev = capabilities?.mode === 'dev'
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
      <div className="flex items-baseline gap-2">
        <h1 className="text-base font-semibold tracking-tight">harnesswalk</h1>
        <span className="hidden text-xs text-muted-foreground lg:inline">walk through an AI harness, one node at a time</span>
      </div>
      {capabilities && (
        <Badge variant="outline" className="font-mono" data-testid="mode-badge" title={`API ${capabilities.version}`}>
          {capabilities.mode}
          {mock ? ' · mock' : ''}
        </Badge>
      )}
      {invalidEvents > 0 && (
        <Badge variant="destructive" title="Events that failed schema validation (see the console)">
          {invalidEvents} invalid events
        </Badge>
      )}
      <div className="ml-auto flex flex-wrap items-center gap-3">
        {dev && capabilities && (
          <>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Provider
              <Select value={provider ?? AUTO} onValueChange={(v) => setProvider(v === AUTO ? null : v)}>
                <SelectTrigger size="sm" className="h-7 w-32 text-xs" aria-label="Provider">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={AUTO}>default</SelectItem>
                  {capabilities.providers.map((p) => (
                    <SelectItem key={p.name} value={p.name} disabled={!p.configured}>
                      {p.name}
                      {p.configured ? '' : ' (not configured)'}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            {capabilities.fixtures.length > 0 && (
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                Fixture
                <Select value={fixture ?? AUTO} onValueChange={(v) => setFixture(v === AUTO ? null : v)}>
                  <SelectTrigger size="sm" className="h-7 w-36 text-xs" aria-label="Fixture" data-testid="fixture-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={AUTO}>{mock ? 'by message' : 'default'}</SelectItem>
                    {capabilities.fixtures.map((f) => (
                      <SelectItem key={f} value={f}>
                        {f}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
            )}
          </>
        )}
        {disabled.length > 0 && (
          <Badge variant="outline" className="text-state-error" title={`Disabled on the next turn: ${disabled.join(', ')}`}>
            {disabled.length} off next turn
          </Badge>
        )}
        {capabilities?.features.step_mode !== false && (
          <label className="flex items-center gap-1.5 text-xs">
            <Switch checked={stepMode} onCheckedChange={setStepMode} aria-label="Step mode: pause at every node" data-testid="step-toggle" />
            Step mode
          </label>
        )}
        <Segmented
          label="Diagram view"
          value={view}
          onChange={setView}
          options={[
            { value: 'simple', label: 'Simple' },
            { value: 'expert', label: 'Expert' },
          ]}
        />
        <Button size="icon" variant="ghost" className="size-8" onClick={newSession} aria-label="Start a new session" title="New session">
          <RotateCcw />
        </Button>
        <Button
          size="icon"
          variant="ghost"
          className="size-8"
          onClick={() => setTheme(NEXT_THEME[theme])}
          aria-label={`Theme: ${theme}. Switch to ${NEXT_THEME[theme]}`}
          title={`Theme: ${theme}`}
          data-testid="theme-toggle"
        >
          <ThemeIcon />
        </Button>
      </div>
    </header>
  )
}

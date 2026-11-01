import { Copy, Ellipsis, Moon, RotateCcw, Sun } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { LessonsButton } from '@/lessons/LessonsButton'
import { fixtureChoices, packTitle } from '@/state/packs'
import { useApp } from '@/state/store'
import { PackPicker } from './PackPicker'
import { useDarkTheme } from './useTheme'

const AUTO = '__auto__'

/** One ghost icon button: Sun in dark mode (press for light), Moon in light mode (press for dark). */
function ThemeButton() {
  const dark = useDarkTheme()
  const setTheme = useApp((s) => s.setTheme)
  const label = dark ? 'Switch to light theme' : 'Switch to dark theme'
  return (
    <Hint label={label}>
      <Button size="icon-sm" variant="ghost" aria-label={label} onClick={() => setTheme(dark ? 'light' : 'dark')} data-testid="theme-toggle">
        {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
      </Button>
    </Hint>
  )
}

function SessionMenu() {
  const sessionId = useApp((s) => s.sessionId)
  const turns = useApp((s) => s.order.length)
  const newSession = useApp((s) => s.newSession)
  const notify = useApp((s) => s.notify)
  const packName = useApp((s) => (s.packs ? packTitle(s.packs, s.pack) : null))
  const copy = () => {
    if (!sessionId) return
    navigator.clipboard?.writeText(sessionId).then(
      () => notify('info', 'Session id copied.'),
      () => notify('error', 'Could not copy the session id.'),
    )
  }
  return (
    <DropdownMenu>
      <Hint label="Session">
        <DropdownMenuTrigger asChild>
          <Button size="icon" variant="ghost" aria-label="Session actions" data-testid="session-menu">
            <Ellipsis />
          </Button>
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span>Session</span>
          <span className="truncate font-mono text-xs font-normal text-muted-foreground">
            {sessionId ?? 'starts with your first message'}
            {turns ? ` · ${turns} turn${turns === 1 ? '' : 's'}` : ''}
          </span>
          {packName && (
            <span className="truncate text-xs font-normal text-muted-foreground" data-testid="session-pack">
              on {packName}
            </span>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={newSession}>
          <RotateCcw /> New session
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={copy} disabled={!sessionId}>
          <Copy /> Copy session id
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function Header() {
  const capabilities = useApp((s) => s.capabilities)
  const mock = useApp((s) => s.mock)
  const provider = useApp((s) => s.provider)
  const setProvider = useApp((s) => s.setProvider)
  const fixture = useApp((s) => s.fixture)
  const setFixture = useApp((s) => s.setFixture)
  const invalidEvents = useApp((s) => s.invalidEvents)
  const disabled = useApp((s) => s.disabled)
  const packs = useApp((s) => s.packs)
  const pack = useApp((s) => s.pack)
  const dev = capabilities?.mode === 'dev'
  // The session pack's own fixtures first, then the shared ones; other packs' are hidden.
  const fixtures = fixtureChoices(packs, capabilities?.fixtures ?? [], pack)

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
      <div className="flex items-baseline gap-2">
        <h1 className="text-base font-medium tracking-tight">harnesswalk</h1>
        <span className="hidden text-xs text-muted-foreground md:inline">walk through an AI harness, one node at a time</span>
      </div>
      {capabilities && (
        <Hint label={`API ${capabilities.version}${mock ? ', in-browser mock' : ''}`}>
          <Badge variant="outline" className="font-mono" data-testid="mode-badge">
            {capabilities.mode}
            {mock ? ' · mock' : ''}
          </Badge>
        </Hint>
      )}
      {invalidEvents > 0 && (
        <Hint label="Events that failed schema validation (see the console)">
          <Badge variant="destructive">{invalidEvents} invalid events</Badge>
        </Hint>
      )}
      <div className="ml-auto flex flex-wrap items-center gap-3">
        {dev && capabilities && (
          <>
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Provider
              <Select value={provider ?? AUTO} onValueChange={(v) => setProvider(v === AUTO ? null : v)}>
                <SelectTrigger size="sm" className="w-32" aria-label="Provider">
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
            {fixtures.all.length > 0 && (
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                Fixture
                <Select value={fixture ?? AUTO} onValueChange={(v) => setFixture(v === AUTO ? null : v)}>
                  <SelectTrigger size="sm" className="w-36" aria-label="Fixture" data-testid="fixture-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={AUTO}>{mock ? 'by message' : 'default'}</SelectItem>
                    {fixtures.own.length > 0 && (
                      <SelectGroup data-testid="fixture-group-pack">
                        <SelectLabel>This pack</SelectLabel>
                        {fixtures.own.map((f) => (
                          <SelectItem key={f} value={f}>
                            {f}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    {fixtures.own.length > 0 && fixtures.shared.length > 0 && <SelectSeparator />}
                    {fixtures.shared.length > 0 && (
                      <SelectGroup data-testid="fixture-group-shared">
                        {fixtures.own.length > 0 && <SelectLabel>Shared</SelectLabel>}
                        {fixtures.shared.map((f) => (
                          <SelectItem key={f} value={f}>
                            {f}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                  </SelectContent>
                </Select>
              </label>
            )}
          </>
        )}
        {disabled.length > 0 && (
          <Hint label={`Disabled on the next turn: ${disabled.join(', ')}`}>
            <Badge variant="outline" className="text-state-error">
              {disabled.length} off next turn
            </Badge>
          </Hint>
        )}
        {(dev || disabled.length > 0) && <Separator orientation="vertical" className="my-1.5" />}
        <PackPicker />
        <div className="flex items-center gap-1">
          <LessonsButton />
          <ThemeButton />
          <SessionMenu />
        </div>
      </div>
    </header>
  )
}

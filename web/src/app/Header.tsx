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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useApp } from '@/state/store'
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
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const provider = useApp((s) => s.provider)
  const setProvider = useApp((s) => s.setProvider)
  const fixture = useApp((s) => s.fixture)
  const setFixture = useApp((s) => s.setFixture)
  const invalidEvents = useApp((s) => s.invalidEvents)
  const disabled = useApp((s) => s.disabled)
  const dev = capabilities?.mode === 'dev'

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
      <div className="flex items-baseline gap-2">
        <h1 className="text-base font-medium tracking-tight">harnesswalk</h1>
        <span className="hidden text-xs text-muted-foreground lg:inline">walk through an AI harness, one node at a time</span>
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
            {capabilities.fixtures.length > 0 && (
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                Fixture
                <Select value={fixture ?? AUTO} onValueChange={(v) => setFixture(v === AUTO ? null : v)}>
                  <SelectTrigger size="sm" className="w-36" aria-label="Fixture" data-testid="fixture-select">
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
          <Hint label={`Disabled on the next turn: ${disabled.join(', ')}`}>
            <Badge variant="outline" className="text-state-error">
              {disabled.length} off next turn
            </Badge>
          </Hint>
        )}
        <Hint label="Expand each group into its parts and draw every edge.">
          <label className="flex items-center gap-1.5 text-xs">
            <Switch checked={view === 'expert'} onCheckedChange={(on) => setView(on ? 'expert' : 'simple')} aria-label="Expert view" data-testid="view-toggle" />
            Expert view
          </label>
        </Hint>
        <ThemeButton />
        <SessionMenu />
      </div>
    </header>
  )
}

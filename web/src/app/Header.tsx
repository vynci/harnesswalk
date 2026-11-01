import { Moon, Sun } from 'lucide-react'
import { Logo } from '@/components/logo'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { GuideButton } from '@/lessons/GuideButton'
import { useApp } from '@/state/store'
import { PackPicker } from './PackPicker'
import { SessionChip } from './SessionChip'
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

export function Header() {
  const capabilities = useApp((s) => s.capabilities)
  const provider = useApp((s) => s.provider)
  const setProvider = useApp((s) => s.setProvider)
  const invalidEvents = useApp((s) => s.invalidEvents)
  const disabled = useApp((s) => s.disabled)
  const dev = capabilities?.mode === 'dev'

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-2">
      <div className="flex items-center gap-2">
        <Logo className="h-5 w-auto shrink-0 text-brand" data-testid="brand-mark" />
        <h1 className="text-base font-medium tracking-tight">Harnesswalk</h1>
        <span className="hidden text-xs text-muted-foreground md:inline">An interactive way to understand AI agent harnesses</span>
      </div>
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
        <div className="flex min-w-0 items-center gap-2">
          <PackPicker />
          <div className="flex shrink-0 items-center gap-1">
            <GuideButton />
            <ThemeButton />
            <SessionChip />
          </div>
        </div>
      </div>
    </header>
  )
}

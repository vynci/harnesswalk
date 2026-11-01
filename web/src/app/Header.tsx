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

const REPO_URL = 'https://github.com/vynci/harnesswalk'

/** The repository link: the right-most icon button in the header, after the session chip and a divider. */
function GitHubButton() {
  return (
    <Hint label="Source on GitHub">
      <Button asChild size="icon-sm" variant="ghost" aria-label="Harnesswalk on GitHub" data-testid="github-link">
        <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
          <svg viewBox="0 0 24 24" aria-hidden className="size-4 fill-current">
            <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.56v-2.17c-3.2.7-3.87-1.37-3.87-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.19 1.76 1.19 1.03 1.76 2.69 1.25 3.35.96.1-.75.4-1.25.73-1.54-2.55-.29-5.24-1.28-5.24-5.69 0-1.26.45-2.29 1.19-3.09-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11.1 11.1 0 0 1 5.79 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.12 3.05.74.8 1.18 1.83 1.18 3.09 0 4.42-2.69 5.39-5.26 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5z" />
          </svg>
        </a>
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
            <Separator orientation="vertical" className="mx-0.5 my-1.5" />
            <GitHubButton />
          </div>
        </div>
      </div>
    </header>
  )
}

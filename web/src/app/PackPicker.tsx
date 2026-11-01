// The pack picker in the header (P5): the dataset packs of GET /v1/packs as a Model Selector
// style menu (a DropdownMenu with one radio group), each with its title and the feature it shows
// off, the session's pack checked. Picking another one starts a new session on it.
import { ChevronDown, Database } from 'lucide-react'
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
import { Spinner } from '@/components/ui/spinner'
import { packTitle } from '@/state/packs'
import { selectTurnRunning, useApp } from '@/state/store'

export function PackPicker() {
  const packs = useApp((s) => s.packs)
  const pack = useApp((s) => s.pack)
  const switching = useApp((s) => s.switchingPack)
  const running = useApp(selectTurnRunning)
  const choosePack = useApp((s) => s.choosePack)
  if (!packs || packs.length === 0) return null
  const current = packs.find((p) => p.id === pack)
  const title = packTitle(packs, pack)
  return (
    <DropdownMenu>
      <Hint label={running ? 'A turn is running: pick a pack when it ends' : `Dataset pack: ${title}${current?.feature ? `, ${current.feature.toLowerCase()}` : ''}. Picking another starts a new session.`}>
        <DropdownMenuTrigger asChild>
          <Button
            size="sm"
            variant="outline"
            className="max-w-56 gap-1.5"
            aria-label={`Dataset pack: ${title}`}
            aria-disabled={running || switching ? true : undefined}
            onClick={(e) => {
              if (running || switching) e.preventDefault()
            }}
            data-testid="pack-picker"
            data-pack={pack ?? ''}
          >
            {switching ? <Spinner role={undefined} aria-hidden className="motion-reduce:animate-none" /> : <Database aria-hidden />}
            <span className="min-w-0 truncate">{title}</span>
            <ChevronDown className="text-muted-foreground" aria-hidden />
          </Button>
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align="end" className="w-80" data-testid="pack-menu">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span>Dataset pack</span>
          <span className="text-xs font-normal text-muted-foreground">Each pack shows off one feature. Picking one starts a new session on it.</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup
          value={pack ?? ''}
          onValueChange={(id) => {
            if (id && id !== pack && !running) void choosePack(id)
          }}
        >
          {packs.map((p) => (
            <DropdownMenuRadioItem key={p.id} value={p.id} className="items-start" data-testid="pack-option" data-pack={p.id} disabled={running}>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="truncate">
                  {p.title || p.id}
                  {p.default && <span className="text-muted-foreground"> · default</span>}
                </span>
                <span className="text-xs text-muted-foreground">
                  {p.feature ?? p.domain ?? 'a dataset pack'}
                  {p.domain && p.feature ? ` · ${p.domain}` : ''}
                </span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

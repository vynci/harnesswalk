// The pack picker in the header (P5): the dataset packs of GET /v1/packs as a Model Selector
// style menu (a DropdownMenu with one radio group), each with its title and the feature it shows
// off, the session's pack checked. Picking another one starts a new session on it. The trigger
// fits the pack's name (up to 24 rem, cut with an ellipsis only past that, the tooltip naming it
// in full); on a phone it shows the first word of the name.
import { ChevronDown, Database } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
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

/** The menu's order (owner's choice): the starter shop first, then the domain packs in a fixed walk; unknown packs follow by title. */
const PACK_ORDER = ['_starter', 'healthcare', 'logistics', 'ecommerce', 'sports', 'finance']
function orderPacks<T extends { id: string; title?: string }>(packs: T[]): T[] {
  const rank = (p: T) => {
    const i = PACK_ORDER.indexOf(p.id)
    return i === -1 ? PACK_ORDER.length : i
  }
  return [...packs].sort((a, b) => rank(a) - rank(b) || (a.title ?? a.id).localeCompare(b.title ?? b.id))
}

export function PackPicker() {
  const packs = useApp((s) => s.packs)
  const pack = useApp((s) => s.pack)
  const switching = useApp((s) => s.switchingPack)
  const running = useApp(selectTurnRunning)
  const choosePack = useApp((s) => s.choosePack)
  if (!packs || packs.length === 0) return null
  const current = packs.find((p) => p.id === pack)
  const title = packTitle(packs, pack)
  const short = title.split(/\s+/)[0] || title
  return (
    <DropdownMenu>
      <Hint label={running ? `${title}. A turn is running: pick a pack when it ends` : `Dataset pack: ${title}${current?.feature ? `, ${current.feature.toLowerCase()}` : ''}. Picking another starts a new session.`}>
        <DropdownMenuTrigger asChild>
          <Button
            size="sm"
            variant="outline"
            className="w-auto max-w-[min(24rem,40vw)] min-w-0 shrink gap-1.5 max-sm:max-w-40"
            aria-label={`Dataset pack: ${title}`}
            aria-disabled={running || switching ? true : undefined}
            onClick={(e) => {
              if (running || switching) e.preventDefault()
            }}
            data-testid="pack-picker"
            data-pack={pack ?? ''}
          >
            {switching ? <Spinner role={undefined} aria-hidden className="motion-reduce:animate-none" /> : <Database aria-hidden />}
            <span className="min-w-0 truncate max-sm:hidden" data-testid="pack-picker-title">
              {title}
            </span>
            <span className="min-w-0 truncate sm:hidden">{short}</span>
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
          {orderPacks(packs).map((p) => (
            <DropdownMenuRadioItem key={p.id} value={p.id} className="items-start" data-testid="pack-option" data-pack={p.id} disabled={running}>
              <span className="flex min-w-0 flex-col gap-1">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate font-medium">{p.title || p.id}</span>
                  {p.domain && (
                    <Badge variant="outline" className="shrink-0 font-normal text-muted-foreground capitalize" data-testid="pack-domain">
                      {p.domain}
                    </Badge>
                  )}
                  {p.default && <span className="shrink-0 text-xs text-muted-foreground">default</span>}
                </span>
                <span className="text-xs text-muted-foreground">{p.feature ?? 'a dataset pack'}</span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

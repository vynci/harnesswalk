// The pack picker in the header (P5): the dataset packs of GET /v1/packs as a Model Selector
// style menu (a DropdownMenu with one radio group), each with its title and the feature it shows
// off, the session's pack checked. Picking another one starts a new session on it. The trigger
// fits the pack's name (up to 24 rem, cut with an ellipsis only past that, the tooltip naming it
// in full); on a phone it shows the first word of the name. The menu ends with "Add your own
// dataset", disabled until packs of one's own arrive (useFeature('pack_uploads')), with a link to
// the guide that does it by hand (docs/your-own-data.md).
import { DropdownMenu as DropdownMenuPrimitive } from 'radix-ui'
import { ChevronDown, Database, Plus } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Spinner } from '@/components/ui/spinner'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { repoBlobUrl } from '@/lessons/links'
import { useFeature } from '@/state/hooks'
import { orderPacks, packTitle } from '@/state/packs'
import { selectTurnRunning, useApp } from '@/state/store'

/** The developer guide to a pack of one's own data, in the repository the API names. */
export const OWN_DATA_GUIDE = 'docs/your-own-data.md'

/**
 * The menu's last entry: "Use your own dataset", disabled with its reason as the tooltip (on the
 * wrapping element, since a disabled item takes no pointer events), so it never closes the menu
 * or picks a pack. Under it, a muted line links to the guide; the link is its own menu item, so
 * it stays clickable and reachable with the arrow keys while the entry is disabled.
 */
function AddOwnDataset({ tid, inline }: { tid: string; inline: boolean }) {
  const feature = useFeature('pack_uploads')
  const base = repoBlobUrl(useApp((s) => s.capabilities?.repo_url))
  const guide = base ? `${base}${OWN_DATA_GUIDE}` : null
  const reason = feature.reason ?? 'Feature coming soon.'
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div data-testid={`${tid}-manage`} data-disabled={feature.enabled ? 'false' : 'true'}>
          <DropdownMenuItem disabled={!feature.enabled} onSelect={(e) => e.preventDefault()}>
            <Plus aria-hidden />
            <span>Use your own dataset</span>
            <span className="sr-only">{reason}</span>
          </DropdownMenuItem>
          <p className="px-2 pb-1.5 pl-8 text-xs text-muted-foreground">
            Today: a folder under datasets/ ·{' '}
            {guide ? (
              <DropdownMenuPrimitive.Item asChild>
                <a
                  href={guide}
                  target="_blank"
                  rel="noreferrer"
                  className="cursor-pointer rounded-xs font-medium text-foreground underline underline-offset-2 outline-hidden hover:text-brand focus:bg-accent data-highlighted:bg-accent"
                  data-testid={`${tid}-manage-link`}
                >
                  Guide
                </a>
              </DropdownMenuPrimitive.Item>
            ) : (
              <span>{OWN_DATA_GUIDE}</span>
            )}
          </p>
        </div>
      </TooltipTrigger>
      <TooltipContent side={inline ? 'right' : 'left'} className="max-w-64" data-testid={`${tid}-manage-reason`}>
        {reason}
      </TooltipContent>
    </Tooltip>
  )
}

/**
 * `variant="inline"` is the empty chat's copy: an outline button at full width of its row with
 * the whole title, the menu opening below it, its own test ids (the header's stay unique).
 */
export function PackPicker({ variant = 'header' }: { variant?: 'header' | 'inline' } = {}) {
  const inline = variant === 'inline'
  const tid = inline ? 'empty-pack' : 'pack'
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
            size={inline ? 'default' : 'sm'}
            variant={inline ? 'outline' : 'default'}
            className={inline ? 'w-auto max-w-sm min-w-0 gap-2 font-medium' : 'w-auto max-w-[min(24rem,40vw)] min-w-0 shrink gap-1.5 font-medium shadow-xs max-sm:max-w-40'}
            aria-label={`Dataset pack: ${title}`}
            aria-disabled={running || switching ? true : undefined}
            onClick={(e) => {
              if (running || switching) e.preventDefault()
            }}
            data-testid={`${tid}-picker`}
            data-pack={pack ?? ''}
          >
            {switching ? <Spinner role={undefined} aria-hidden className="motion-reduce:animate-none" /> : <Database aria-hidden />}
            <span className={inline ? 'min-w-0 truncate' : 'min-w-0 truncate max-sm:hidden'} data-testid={`${tid}-picker-title`}>
              {title}
            </span>
            {!inline && <span className="min-w-0 truncate sm:hidden">{short}</span>}
            <ChevronDown className={inline ? 'opacity-60' : 'opacity-80'} aria-hidden />
          </Button>
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align={inline ? 'start' : 'end'} className="w-80" data-testid={`${tid}-menu`}>
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
            <DropdownMenuRadioItem key={p.id} value={p.id} className="items-start" data-testid={`${tid}-option`} data-pack={p.id} data-selected={p.id === pack ? 'true' : 'false'} disabled={running}>
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
                <span className="text-xs text-muted-foreground">
                  {p.feature ?? 'a dataset pack'}
                  {p.profile && (
                    <span data-testid="pack-profile" data-profile={p.profile}>
                      {' · '}
                      {p.profile} profile
                    </span>
                  )}
                </span>
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <AddOwnDataset tid={tid} inline={inline} />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

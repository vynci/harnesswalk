// The empty chat's pack chooser: the dataset packs as a grid of cards, in the header picker's
// order, the session's pack marked (a brand ring and a check; keyboard focus is an outline set
// off the card, so the two never look alike). Picking another starts a new session on it
// (choosePack, as the header picker does), so the suggested prompts under the composer follow
// it. Two columns once the chat is wide enough, one below that.
import { useId, useState } from 'react'
import { CircleCheck } from 'lucide-react'
import type { PackSummary } from '@/api/types'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'
import { orderPacks } from '@/state/packs'
import { selectTurnRunning, useApp } from '@/state/store'

export function PackChooser({ packs }: { packs: PackSummary[] }) {
  const pack = useApp((s) => s.pack)
  const switching = useApp((s) => s.switchingPack)
  const running = useApp(selectTurnRunning)
  const sending = useApp((s) => s.sending)
  const choosePack = useApp((s) => s.choosePack)
  const [picked, setPicked] = useState<string | null>(null)
  const lead = useId()
  const blocked = running || switching || sending

  const pick = (id: string) => {
    if (blocked || id === pack) return
    setPicked(id)
    void choosePack(id).finally(() => setPicked(null))
  }

  return (
    <div className="flex w-full flex-col gap-3 @container">
      <p id={lead} className="text-sm text-muted-foreground">
        Pick a dataset to walk through:
      </p>
      <div role="group" aria-labelledby={lead} className="grid w-full grid-cols-1 gap-2 @min-[22rem]:grid-cols-2" data-testid="empty-pack-grid">
        {orderPacks(packs).map((p) => {
          const selected = p.id === pack
          const title = p.title || p.id
          const label = running
            ? `${title}. A turn is running: pick a pack when it ends`
            : selected
              ? `${title} is this session's pack.`
              : `Picking ${title} starts a new session on it.`
          return (
            <Hint key={p.id} label={label} side="top">
              <button
                type="button"
                className="flex min-w-0 flex-col items-start gap-1.5 rounded-lg border bg-card p-3 text-left transition-colors outline-none hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-solid focus-visible:outline-ring aria-disabled:cursor-not-allowed aria-disabled:opacity-60 aria-disabled:hover:bg-card data-[selected=true]:border-brand data-[selected=true]:bg-brand/5 data-[selected=true]:ring-1 data-[selected=true]:ring-brand data-[selected=true]:hover:bg-brand/5"
                aria-current={selected ? 'true' : undefined}
                aria-disabled={blocked && !selected ? true : undefined}
                onClick={() => pick(p.id)}
                data-testid="empty-pack-card"
                data-pack={p.id}
                data-selected={selected ? 'true' : 'false'}
              >
                <span className="flex w-full min-w-0 items-start gap-2">
                  <span className="min-w-0 flex-1 text-sm font-medium">{title}</span>
                  {picked === p.id && switching ? (
                    <Spinner role={undefined} aria-hidden className="mt-0.5 shrink-0 text-muted-foreground motion-reduce:animate-none" />
                  ) : selected ? (
                    <CircleCheck className="mt-0.5 size-4 shrink-0 text-brand" aria-label="Current pack" data-testid="empty-pack-current" />
                  ) : null}
                </span>
                {p.domain && (
                  <Badge variant="outline" className="font-normal text-muted-foreground capitalize">
                    {p.domain}
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">{p.feature ?? 'a dataset pack'}</span>
              </button>
            </Hint>
          )
        })}
      </div>
    </div>
  )
}
